"""``SpectrumSoakRecorder`` — record the spectrometer, continuously, and nothing else.

This is ``XcorrSpectrumRecorder`` with the scan taken out. There are no stages to move,
so there is no motion gate and no per-spectrum context: every spectrum that survives the
period filter is filed with its timestamp and that is the whole schema. What it is *for*
is answering "is the stabilization loop actually holding?" — you record a few minutes with
the loop off, a few minutes with it on, and compare the fringe drift between the two files.
That question needs the raw frames, not the loop's own opinion of itself, which is why
this records the stream rather than the phase readouts the panel already prints.

Two constraints carry over unchanged from XCORR, because they are properties of the
spectrum stream rather than of XCORR:

**Acks are on the critical path of an unrelated subsystem.** ``SlotCoordinator`` promotes
the next spectrum only once *every* registered consumer has acked, and the handler runs
synchronously on the EventBus publisher's thread (the IPC reader thread). So
:meth:`_on_spectrum` copies out of shared memory, pushes onto a bounded queue, and acks —
nothing else. When the queue is full the spectrum is dropped and still acked. A recorder
that cannot keep up must degrade its own data, never the phase loop's. This matters more
here than in XCORR: the point of the exercise is to watch the loop, so the measurement
must not perturb it.

**Free-running means the period is a filter, not a trigger.** The spectrometer integrates
at its own rate (~1 / (exposure * average), ~4 Hz stock) and nothing here can ask it for a
frame. ``period_s`` therefore *decimates*: the first spectrum whose timestamp is at least
``period_s`` past the last kept one is kept. Actual spacing lands between ``period_s`` and
``period_s + 1/rate``, and the recorded ``timestamp_ns`` is the truth about when each frame
happened — never reconstruct the time axis from the index and the requested period.
``period_s=0`` keeps everything the device delivers.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import h5py
import numpy as np

from base_core.framework.events.event_bus import EventBus
from base_core.framework.serialization.h5_utils import ensure_group, now_utc_iso

from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable

log = logging.getLogger(__name__)

FORMAT_NAME = "milnerlab-spectrum-soak"
FORMAT_VERSION = 1

#: Spectra buffered between the IPC thread and the writer thread. At ~4 Hz this is a
#: minute of slack -- far more than any plausible disk stall -- so hitting it means
#: something is genuinely wrong, and dropping is then the right answer.
_QUEUE_MAX = 256

#: Rows appended per dataset resize.
_CHUNK_ROWS = 32

#: How long ``close()`` waits for the writer thread to drain.
_DRAIN_TIMEOUT_S = 5.0


def default_soak_path(out_dir: Path, when: datetime | None = None, tag: str = "") -> Path:
    """``<out_dir>/SOAK_<tag>_YYYYmmdd_HHMMSS.h5``. Timestamp last, so runs sort."""
    when = when or datetime.now()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in tag.strip()).strip("_")
    stem = f"SOAK_{safe}_{when:%Y%m%d_%H%M%S}" if safe else f"SOAK_{when:%Y%m%d_%H%M%S}"
    return Path(out_dir) / f"{stem}.h5"


class SoakH5Writer:
    """One file, one flat table.

    Layout::

        SOAK_20260902_143022.h5
        ├─ (root attrs) format_name, format_version, created_utc, completed_utc,
        │               n_pixels, n_dropped, requested_period_s, requested_duration_s,
        │               plus whatever provenance the caller passed
        ├─ wavelength_nm  float64[n_pixels]   written once, from the first spectrum
        ├─ counts         float32[N, n_pixels]
        └─ timestamp_ns   int64[N]            time.time_ns() at END of integration

    ``counts`` is float32 and row-chunked: readers pull whole spectra, and a chunk
    spanning rows would make a single-spectrum read decompress its neighbours.
    """

    def __init__(self, path: Path, attrs: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._f: h5py.File | None = h5py.File(self.path, "w")
        self._f.attrs["format_name"] = FORMAT_NAME
        self._f.attrs["format_version"] = FORMAT_VERSION
        self._f.attrs["created_utc"] = now_utc_iso()
        for k, v in (attrs or {}).items():
            self._f.attrs[k] = str(v) if isinstance(v, Path) else v
        self._n_pixels = 0
        self._opened = False
        self._since_flush = 0
        self.n_written = 0

    @property
    def file(self) -> h5py.File:
        if self._f is None:
            raise RuntimeError("soak writer is closed")
        return self._f

    def open_axis(self, wavelengths: Sequence[float]) -> None:
        """Pin the wavelength axis and create the tables. Idempotent.

        Deferred until the first spectrum arrives: ``n_pixels`` comes from the device,
        and a run that saw nothing should have no tables rather than empty ones claiming
        a pixel count nobody measured.
        """
        with self._lock:
            if self._opened or self._f is None:
                return
            wl = np.asarray(wavelengths, dtype=np.float64)
            if wl.size == 0:
                raise ValueError("open_axis: empty wavelength axis")
            self._n_pixels = int(wl.size)
            f = self._f
            f.attrs["n_pixels"] = self._n_pixels
            f.create_dataset("wavelength_nm", data=wl, chunks=True,
                             compression="lzf", shuffle=True)
            f.create_dataset("counts", shape=(0, self._n_pixels),
                             maxshape=(None, self._n_pixels), dtype=np.float32,
                             chunks=(1, self._n_pixels), compression="lzf", shuffle=True)
            f.create_dataset("timestamp_ns", shape=(0,), maxshape=(None,), dtype=np.int64,
                             chunks=(_CHUNK_ROWS,), compression="lzf", shuffle=True)
            self._opened = True
            f.flush()
            log.info("soak file opened: %s (%d pixels)", self.path, self._n_pixels)

    def append(self, counts: Sequence[np.ndarray], stamps: Sequence[int]) -> int:
        """Append a batch. Rows whose pixel count disagrees with the axis are skipped.

        Skipped rather than fatal: a mid-run spectrometer reconfiguration must not cost
        the frames recorded either side of it.
        """
        with self._lock:
            if not self._opened or self._f is None or not counts:
                return 0
            keep = [(c, t) for c, t in zip(counts, stamps)
                    if np.asarray(c).size == self._n_pixels]
            if len(keep) != len(counts):
                log.warning("soak: skipped %d spectrum/spectra with unexpected pixel count "
                            "(axis is %d wide)", len(counts) - len(keep), self._n_pixels)
            if not keep:
                return 0
            f = self._f
            start = f["counts"].shape[0]
            end = start + len(keep)
            f["counts"].resize(end, axis=0)
            f["counts"][start:end, :] = np.asarray(
                [np.asarray(c, dtype=np.float32) for c, _ in keep], dtype=np.float32)
            f["timestamp_ns"].resize(end, axis=0)
            f["timestamp_ns"][start:end] = np.asarray([t for _, t in keep], dtype=np.int64)
            self.n_written = end
            self._since_flush += len(keep)
            if self._since_flush >= _CHUNK_ROWS:
                self._since_flush = 0
                f.flush()
            return len(keep)

    def close(self, *, n_dropped: int = 0) -> None:
        with self._lock:
            if self._f is None:
                return
            self._f.attrs["n_dropped"] = int(n_dropped)
            self._f.attrs["completed_utc"] = now_utc_iso()
            self._f.flush()
            self._f.close()
            self._f = None
            log.info("soak file closed: %s (%d spectra, %d dropped)",
                     self.path, self.n_written, n_dropped)


class SpectrumSoakRecorder:
    """Record every ``period_s`` for ``duration_s``, then stop on its own.

    Qt-free and container-free by design: it takes the bus and the spectrometer handle,
    so it runs identically under the app, under a panel, and under ``tools/record_spectra.py``.

    Stopping is *self-timed* against the recorded timestamps rather than left to the
    caller's sleep: the caller's clock and the spectrometer's are the same wall clock, but
    the caller may be blocked, and a soak that overruns because the driving loop stalled
    would put the extra frames in the file with no way to tell.
    """

    CONSUMER_ID = "spectrum_soak"

    def __init__(
        self,
        bus: EventBus,
        handle: Any,                     # SpectrometerWorkerHandle
        writer: SoakH5Writer,
        *,
        period_s: float = 0.0,
        duration_s: float = 60.0,
        on_progress: Callable[[int, int, float], None] | None = None,
    ) -> None:
        self._bus = bus
        self._handle = handle
        self._writer = writer
        self._period_ns = max(0, int(float(period_s) * 1e9))
        self._duration_ns = max(0, int(float(duration_s) * 1e9))
        #: Called on the WRITER thread after each batch reaches the file, with
        #: (n_kept, n_seen, elapsed_s). Deliberately not the IPC thread: a panel that
        #: repaints must never sit between a spectrum and its ack.
        self._on_progress = on_progress

        self._queue: "queue.Queue[tuple[np.ndarray, int]]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self._unsub = None

        # Touched only on the IPC reader thread once started, but read by wait()/close()
        # on the caller's, so they are guarded together with the counters.
        self._lock = threading.Lock()
        self._first_ns: int | None = None
        self._last_kept_ns: int | None = None
        self._wavelengths: np.ndarray | None = None
        self.n_seen = 0
        self.n_kept = 0
        self.n_dropped = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._drain, name="spectrum-soak-writer",
                                        daemon=True)
        self._thread.start()
        # Subscribe before registering: registering is what makes the coordinator wait on
        # our ack, so there must be no window where we are pending but not listening.
        self._unsub = self._bus.subscribe(SpectrumAvailable, self._on_spectrum)
        self._handle.register_consumer(self.CONSUMER_ID)
        log.info("soak recording started: %.1f s at %.3f s period -> %s",
                 self._duration_ns / 1e9, self._period_ns / 1e9, self._writer.path)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the duration has elapsed. Returns False on timeout.

        The clock starts at the FIRST spectrum, not at :meth:`start`, so a slow
        spectrometer start-up does not eat into the requested duration.
        """
        return self._done.wait(timeout)

    def close(self) -> None:
        """Unregister, drain what is queued, close the file.

        Ordering matters: unregister *first* so the coordinator stops waiting on our ack,
        then unsubscribe, then let the writer finish. The other way round leaves a promoted
        slot pending on a consumer that no longer listens, stalling the phase loop.
        """
        self._handle.unregister_consumer(self.CONSUMER_ID)
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._done.set()
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=_DRAIN_TIMEOUT_S)
            if thread.is_alive():
                log.warning("soak: writer thread did not drain in %.0f s", _DRAIN_TIMEOUT_S)
        self._writer.close(n_dropped=self.n_dropped)
        log.info("soak recording stopped: %d seen, %d recorded, %d dropped",
                 self.n_seen, self.n_kept, self.n_dropped)

    @property
    def path(self):
        """Where this recording is being written."""
        return self._writer.path

    @property
    def elapsed_s(self) -> float:
        with self._lock:
            if self._first_ns is None:
                return 0.0
            return (time.time_ns() - self._first_ns) / 1e9

    # -- consumer (IPC reader thread -- must stay short) -------------------

    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            ts = int(event.timestamp_ns)
            with self._lock:
                self.n_seen += 1
                if self._first_ns is None:
                    self._first_ns = ts
                over = self._duration_ns and (ts - self._first_ns) >= self._duration_ns
                due = (self._last_kept_ns is None
                       or ts - self._last_kept_ns >= self._period_ns)
                if due and not self._done.is_set():
                    self._last_kept_ns = ts
                else:
                    due = False
            if due:
                buf = self._handle.buffer
                # Copy out of shared memory: the slot is reused the moment we ack.
                counts = np.array(buf.intensities(event.slot), dtype=np.float64)
                with self._lock:
                    if self._wavelengths is None:
                        self._wavelengths = np.array(buf.wavelengths(event.slot),
                                                     dtype=np.float64)
                try:
                    self._queue.put_nowait((counts, ts))
                    with self._lock:
                        self.n_kept += 1
                except queue.Full:
                    with self._lock:
                        self.n_dropped += 1
            if over:
                # The last spectrum of the run is kept before the flag is raised, so a
                # duration that is an exact multiple of the period is inclusive.
                self._done.set()
        except Exception:
            # Never let a read error escape: this runs on the IPC reader thread, and an
            # exception here would skip the ack below and stall the whole stream.
            log.exception("soak: read failed for slot %d", event.slot)
        finally:
            self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id,
                                          consumer_id=self.CONSUMER_ID))

    # -- writer thread ----------------------------------------------------

    def _drain(self) -> None:
        while True:
            batch = self._take_batch()
            if batch:
                try:
                    with self._lock:
                        wl = self._wavelengths
                    if wl is not None:
                        self._writer.open_axis(wl)
                    self._writer.append([c for c, _ in batch], [t for _, t in batch])
                    if self._on_progress is not None:
                        self._on_progress(self.n_kept, self.n_seen, self.elapsed_s)
                except Exception:
                    # Recording is additive: a storage failure loses spectra, never the
                    # run. Keep draining so the queue cannot back up into drops.
                    log.exception("soak: write failed, dropping %d", len(batch))
                    with self._lock:
                        self.n_dropped += len(batch)
            elif self._stop.is_set():
                return

    def _take_batch(self) -> list[tuple[np.ndarray, int]]:
        try:
            first = self._queue.get(timeout=0.1)
        except queue.Empty:
            return []
        batch = [first]
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                return batch

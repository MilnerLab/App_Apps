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
        roi: tuple[float, float] | None = None,
        on_progress: Callable[[int, int, float], None] | None = None,
        on_data: Callable[[np.ndarray, np.ndarray], None] | None = None,
    ) -> None:
        self._bus = bus
        self._handle = handle
        self._writer = writer
        self._period_ns = max(0, int(float(period_s) * 1e9))
        self._duration_ns = max(0, int(float(duration_s) * 1e9))
        #: Wavelength band to keep, or None for the whole detector. Applied as a column
        #: slice on the way in, so the file holds exactly what was recorded and needs no
        #: second opinion about which region was under study when it is read back.
        self._roi = None if roi is None else (float(roi[0]), float(roi[1]))
        #: Column slice derived from the first spectrum's axis. None until then.
        self._cut: slice | None = None
        #: Called on the WRITER thread after each batch reaches the file, with
        #: (n_kept, n_seen, elapsed_s). Deliberately not the IPC thread: a panel that
        #: repaints must never sit between a spectrum and its ack.
        self._on_progress = on_progress
        #: Called on the WRITER thread with (wavelength_nm[px], block[n, px]) for each
        #: batch that reached the file -- what the panel draws. It sees exactly the rows
        #: that were stored, ROI crop included, so the picture cannot drift from the file.
        self._on_data = on_data

        self._queue: "queue.Queue[tuple[np.ndarray, int]]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self._unsub = None
        # Paused time does not count against the duration: "record for 5 minutes" means
        # five minutes of data. A pause that ate into the clock would silently shorten
        # the run, and the two arms of a comparison would no longer be the same length.
        self._paused = threading.Event()
        self._paused_ns = 0
        self._paused_at: int | None = None

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
        log.info("soak recording started: %.1f s at %.3f s period%s -> %s",
                 self._duration_ns / 1e9, self._period_ns / 1e9,
                 "" if self._roi is None else " over %.2f-%.2f nm" % self._roi,
                 self._writer.path)

    def pause(self) -> None:
        """Stop keeping spectra. The stream, the registration and the acks continue --
        only the filing stops, so a pause costs the phase loop nothing."""
        with self._lock:
            if self._paused_at is None:
                self._paused_at = time.time_ns()
        self._paused.set()

    def resume(self) -> None:
        with self._lock:
            if self._paused_at is not None:
                self._paused_ns += time.time_ns() - self._paused_at
                self._paused_at = None
        self._paused.clear()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

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
    def wavelengths(self) -> np.ndarray | None:
        """The axis actually being recorded (ROI-cropped), or None before the first
        spectrum has arrived."""
        with self._lock:
            return self._wavelengths

    @property
    def path(self):
        """Where this recording is being written."""
        return self._writer.path

    @property
    def elapsed_s(self) -> float:
        """Recording time so far, with paused spans taken out -- the same clock the
        duration is measured against, so the panel's progress cannot disagree with it."""
        with self._lock:
            if self._first_ns is None:
                return 0.0
            return self._recorded_ns(time.time_ns()) / 1e9

    def _recorded_ns(self, now_ns: int) -> int:
        """Caller holds the lock."""
        if self._first_ns is None:
            return 0
        paused = self._paused_ns
        if self._paused_at is not None:
            paused += now_ns - self._paused_at
        return max(0, now_ns - self._first_ns - paused)

    # -- consumer (IPC reader thread -- must stay short) -------------------

    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            ts = int(event.timestamp_ns)
            paused = self._paused.is_set()
            with self._lock:
                self.n_seen += 1
                if self._first_ns is None and not paused:
                    self._first_ns = ts
                over = self._duration_ns and self._recorded_ns(ts) >= self._duration_ns
                due = (not paused
                       and (self._last_kept_ns is None
                            or ts - self._last_kept_ns >= self._period_ns))
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
                        wl = np.array(buf.wavelengths(event.slot), dtype=np.float64)
                        self._cut = self._slice_for(wl)
                        self._wavelengths = wl[self._cut]
                    cut = self._cut
                if cut is not None:
                    counts = counts[cut]
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

    def _slice_for(self, wl: np.ndarray) -> slice:
        """Columns to keep. A contiguous slice rather than a boolean mask because the
        axis is monotonic and a slice keeps the HDF5 rows contiguous too.

        An ROI that selects nothing -- a stale region, a regrating, a typo -- falls back
        to the whole detector with a warning. Recording the wrong span is recoverable;
        recording an empty file and finding out an hour later is not.
        """
        if self._roi is None:
            return slice(None)
        lo, hi = self._roi
        idx = np.nonzero((wl >= lo) & (wl <= hi))[0]
        if idx.size < 2:
            log.warning("soak: ROI %.2f-%.2f nm selects %d of %d pixels; recording the "
                        "whole detector instead", lo, hi, idx.size, wl.size)
            self._roi = None
            return slice(None)
        return slice(int(idx[0]), int(idx[-1]) + 1)

    # -- writer thread ----------------------------------------------------

    def _drain(self) -> None:
        while True:
            self._check_deadline()
            batch = self._take_batch()
            if batch:
                try:
                    with self._lock:
                        wl = self._wavelengths
                    if wl is not None:
                        self._writer.open_axis(wl)
                    self._writer.append([c for c, _ in batch], [t for _, t in batch])
                    if self._on_data is not None and wl is not None:
                        self._on_data(wl, np.asarray([c for c, _ in batch],
                                                     dtype=np.float32))
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

    def _check_deadline(self) -> None:
        """End the run on the clock, not only on the next spectrum.

        The duration used to be tested inside the consumer, which meant a stream that
        stopped delivering -- a spectrometer that died, a worker that hung -- left the run
        recording forever, the panel saying "recording", and the file never closed. The
        writer thread already wakes every 100 ms for its queue, so it is the natural place
        to notice that the time is up regardless of what the device is doing.
        """
        if self._done.is_set() or not self._duration_ns:
            return
        with self._lock:
            if self._first_ns is None:
                return
            over = self._recorded_ns(time.time_ns()) >= self._duration_ns
        if over:
            self._done.set()

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

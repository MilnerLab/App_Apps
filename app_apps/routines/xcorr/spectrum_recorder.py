"""``XcorrSpectrumRecorder`` — record the free-running spectrometer during an XCORR scan.

The spectrometer is not driven by the scan. It free-runs in its own 32-bit subprocess
at roughly ``1 / (exposure_time * average)`` (~4 Hz with the stock config), pushing each
spectrum into a two-slot shared-memory buffer. This class registers as one more consumer
of that stream and files every spectrum into the run's HDF5 file, stamped with the stage
positions in force when it was integrated — which is what makes "what did the
spectrometer see at this grating and delay?" answerable after the fact.

Two constraints shape the design.

**Acks are on the critical path of an unrelated subsystem.** ``SlotCoordinator`` promotes
the next spectrum only once *every* registered consumer has acked, so a consumer that
blocks on disk I/O throttles ``phase_control_vm``, ``phase_tracking`` and ``envelope``
alike. Worse, the handler runs synchronously on the EventBus publisher's thread — the IPC
reader thread. So :meth:`_on_spectrum` does exactly three things: copy out of shared
memory, push onto a bounded queue, ack. Everything else happens on :attr:`_thread`. When
the queue is full the spectrum is *dropped* and still acked; a recorder that cannot keep
up must degrade its own data, never the phase loop's.

**A spectrum integrated across a stage move belongs to no position.** Hence the motion
gate. :meth:`gate_close` is called on entry to every move and :meth:`gate_open` once the
stages are stationary and the context has been set. ``SpectrumAvailable.timestamp_ns`` is
``time.time_ns()`` sampled *after* ``PHO_Acquire`` returns (``spm_002/spectrometer.py``),
so it is a wall clock directly comparable to this process's and it marks the *end* of the
integration. The start is therefore ``timestamp_ns - integration_span_ns``, and a spectrum
is kept only when that whole interval sits inside an open window. Anything straddling a
move is discarded.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from base_core.framework.events.event_bus import EventBus

from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable
from app_apps.routines.xcorr.storage import SpectrumRecord, XcorrH5Writer

if TYPE_CHECKING:
    from spm_002.config import SpectrometerConfig
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle

log = logging.getLogger(__name__)

#: Spectra buffered between the IPC thread and the writer thread. At ~4 Hz this is a
#: minute of slack — far more than any plausible disk stall — so hitting it means
#: something is genuinely wrong, and dropping is then the right answer.
_QUEUE_MAX = 256

#: Extra margin added to the nominal integration span, as a fraction. The device's own
#: readout and the DLL call add time beyond ``exposure * average``, and the cost of
#: over-estimating is a few discarded spectra whereas under-estimating admits one that
#: saw a moving stage.
_SPAN_MARGIN = 0.25

#: How long ``close()`` waits for the writer thread to drain.
_DRAIN_TIMEOUT_S = 5.0


@dataclass
class _Context:
    """Stage positions stamped onto every spectrum accepted in the current window."""

    setpoint_index: int = -1
    probe_index: int = -1
    grating_mm: float = float("nan")
    delay_mm: float = float("nan")
    probe_mm: float = float("nan")


def integration_span_ns(config: "SpectrometerConfig") -> int:
    """Conservative estimate of how long one spectrum takes to integrate, in ns.

    ``exposure_time`` is a ``Time`` quantity; ``average`` is the number of hardware
    averages folded into a single returned spectrum, so the two multiply.
    """
    from base_core.quantities.models import Prefix

    exposure_ms = float(config.exposure_time.value(Prefix.MILLI))
    n_avg = max(1, int(config.average))
    return int(exposure_ms * 1e6 * n_avg * (1.0 + _SPAN_MARGIN))


class XcorrSpectrumRecorder:
    """Consume the spectrum stream for the duration of one XCORR run.

    Not a ``PanelViewModel`` and deliberately free of Qt: the routine half of XCORR
    must stay importable from the headless harness, which never builds a window.
    """

    CONSUMER_ID = "xcorr_recorder"

    def __init__(
        self,
        bus: EventBus,
        handle: "SpectrometerWorkerHandle",
        writer: XcorrH5Writer,
        *,
        span_ns: int,
        provenance: dict[str, object] | None = None,
    ) -> None:
        self._bus = bus
        self._handle = handle
        self._writer = writer
        self._span_ns = int(span_ns)
        self._provenance = dict(provenance or {})

        self._queue: "queue.Queue[SpectrumRecord]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._unsub = None

        # Guards the gate and the context together — they are read as a pair by the
        # IPC thread and written as a pair by the routine thread.
        self._gate_lock = threading.Lock()
        self._gate_open_ns: int | None = None   # None = never opened
        self._gate_closed_ns: int | None = None  # None = currently open
        self._ctx = _Context()

        #: Wavelength axis from the first spectrum seen, handed to the writer once.
        #: Written on the IPC thread, read on the writer thread; a plain attribute is
        #: enough because it only ever goes None -> array and the writer tolerates None.
        self._wavelengths: np.ndarray | None = None

        # Counters are touched from both the IPC thread and the writer thread, and
        # n_dropped ends up in the file, so they get a lock rather than a prayer.
        self._counts_lock = threading.Lock()
        self.n_dropped = 0
        self.n_gated_out = 0
        self.n_accepted = 0

    def _bump(self, field: str, by: int = 1) -> None:
        with self._counts_lock:
            setattr(self, field, getattr(self, field) + by)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Register as a consumer and start draining. The gate starts closed."""
        self._thread = threading.Thread(
            target=self._drain, name="xcorr-spectrum-writer", daemon=True
        )
        self._thread.start()
        # Subscribe before registering: registering is what makes the coordinator wait
        # on our ack, so there must be no window where we are pending but not listening.
        self._unsub = self._bus.subscribe(SpectrumAvailable, self._on_spectrum)
        self._handle.register_consumer(self.CONSUMER_ID)
        log.info("XCORR spectrum recorder started (integration span %.0f ms)", self._span_ns / 1e6)

    def close(self) -> None:
        """Unregister, drain what is queued, and stamp the drop count.

        Ordering matters: unregister *first* so the coordinator stops waiting on our
        ack, then unsubscribe, then let the writer thread finish. Doing it the other
        way round would leave a promoted slot pending on a consumer that no longer
        listens, stalling the phase loop until the handle was torn down.
        """
        self._handle.unregister_consumer(self.CONSUMER_ID)
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self.gate_close()

        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=_DRAIN_TIMEOUT_S)
            if thread.is_alive():
                log.warning("XCORR spectrum recorder: writer thread did not drain in %.0f s",
                            _DRAIN_TIMEOUT_S)
        self._writer.close_spectra(n_dropped=self.n_dropped)
        log.info(
            "XCORR spectrum recorder stopped: %d recorded, %d gated out (motion), %d dropped",
            self.n_accepted, self.n_gated_out, self.n_dropped,
        )

    # -- the motion gate (routine thread) ---------------------------------

    def gate_close(self) -> None:
        """Stop accepting. Called on entry to every stage move.

        The context is left untouched, so a spectrum that finished integrating inside
        the window just closed is still filed against the right positions when its
        event arrives late.
        """
        with self._gate_lock:
            if self._gate_closed_ns is None:
                self._gate_closed_ns = time.time_ns()

    def gate_open(self, ctx_setpoint: int, grating_mm: float, delay_mm: float,
                  probe_index: int, probe_mm: float) -> None:
        """Publish the current stage positions and start accepting from now on."""
        with self._gate_lock:
            self._ctx = _Context(
                setpoint_index=ctx_setpoint,
                probe_index=probe_index,
                grating_mm=grating_mm,
                delay_mm=delay_mm,
                probe_mm=probe_mm,
            )
            self._gate_open_ns = time.time_ns()
            self._gate_closed_ns = None

    def _admit(self, ts_ns: int) -> _Context | None:
        """Return the context to stamp, or ``None`` if the spectrum saw motion."""
        with self._gate_lock:
            if self._gate_open_ns is None:
                return None
            if ts_ns - self._span_ns < self._gate_open_ns:
                return None  # integration began before the stages settled
            if self._gate_closed_ns is not None and ts_ns > self._gate_closed_ns:
                return None  # integration ran past the start of the next move
            return self._ctx

    # -- consumer (IPC reader thread — must stay short) --------------------

    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            ctx = self._admit(event.timestamp_ns)
            if ctx is None:
                self._bump("n_gated_out")
                return
            buf = self._handle.buffer
            # Copy out of shared memory: the slot is reused the moment we ack.
            counts = np.array(buf.intensities(event.slot), dtype=np.float64)
            wavelengths = np.array(buf.wavelengths(event.slot), dtype=np.float64)
            record = SpectrumRecord(
                counts=counts,
                timestamp_ns=event.timestamp_ns,
                setpoint_index=ctx.setpoint_index,
                probe_index=ctx.probe_index,
                grating_mm=ctx.grating_mm,
                delay_mm=ctx.delay_mm,
                probe_mm=ctx.probe_mm,
            )
            if self._wavelengths is None:
                self._wavelengths = wavelengths
            try:
                self._queue.put_nowait(record)
                self._bump("n_accepted")
            except queue.Full:
                self._bump("n_dropped")
        except Exception:
            # Never let a read error escape: this runs on the IPC reader thread, and an
            # exception here would skip the ack below and stall the whole stream.
            log.exception("XCORR spectrum recorder: read failed for slot %d", event.slot)
        finally:
            self._bus.publish(SpectrumAck(
                slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID
            ))

    # -- writer thread ----------------------------------------------------

    def _drain(self) -> None:
        """Batch whatever is queued into the HDF5 file until stopped and empty."""
        while True:
            batch = self._take_batch()
            if batch:
                try:
                    self._write(batch)
                except Exception:
                    # Recording is additive: a storage failure loses spectra, never the
                    # scan. Keep draining so the queue cannot back up into drops.
                    log.exception("XCORR spectrum recorder: write failed, dropping %d", len(batch))
                    self._bump("n_dropped", len(batch))
            elif self._stop.is_set():
                return

    def _take_batch(self) -> list[SpectrumRecord]:
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

    def _write(self, batch: list[SpectrumRecord]) -> None:
        wl = self._wavelengths
        if wl is not None:
            attrs = dict(self._provenance)
            attrs["integration_span_ns"] = self._span_ns
            attrs["gate_rule"] = (
                "kept only when [timestamp_ns - integration_span_ns, timestamp_ns] lies "
                "wholly within a stationary-stage window"
            )
            self._writer.open_spectra(wl, attrs)
        self._writer.append_spectra(batch)

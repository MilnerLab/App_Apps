"""``SpectrumRecorder`` — file the free-running spectrometer stream into an HDF5 group.

Knows nothing about any routine. It writes into whatever ``h5py.Group`` it is handed: the
root of its own file (``SpectrumRecordingService``) or a subgroup of a bigger file owned
by someone else, e.g. ``scan_file["spectrometer"]`` inside a scan. Layout under that group::

    <group>/
    ├─ info              JSON  SpectrumRecordingInfo
    ├─ wavelength_nm     float64[n_pixels]  from the first spectrum
    ├─ metadata/<ts>     JSON  SpectrumRecordingMetadata, one per rig state change
    └─ traces/<ts>       float32[n_pixels], one per spectrum

``<ts>`` is ``time.time_ns()`` zero-padded to 19 digits, so name order is time order. A
trace belongs to the latest metadata entry whose timestamp is not after its own. Traces
are keyed by ``SpectrumAvailable.timestamp_ns`` (sampled in the spectrometer subprocess
after the acquisition returns); metadata by this process's clock at the event. Same
machine, same wall clock.

A metadata entry is written at start and whenever the rig state it records changes: a
grating or delay stage reports a new position (workers report once, *after* the move
completes, so a spectrum taken mid-move still sits under the previous entry), phase
stabilization starts or stops running, a stabilization setting changes (e.g. the target
phase ``set_phase``), or the spectrometer settings are applied.

**Acks are on the critical path of other subsystems.** ``SlotCoordinator`` promotes the
next spectrum only once every consumer has acked, and the handler runs on the IPC reader
thread. So :meth:`_on_spectrum` copies out of shared memory, queues, and acks in
``finally``; a full queue drops the spectrum (counted) rather than blocking. Metadata
snapshots go onto the same queue, so the single writer thread keeps their order relative
to the traces.
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import h5py
import numpy as np

from base_core.framework.events.event_bus import EventBus
from base_core.framework.serialization.h5_utils import ensure_group, now_utc_iso, write_primitive
from base_core.framework.serialization.serialization import from_primitive, to_primitive
from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length

from app_apps.analysis.phase_control.events import (
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)
from app_apps.io.control_readout.mfa_cc.events import NewMfaccPosition
from app_apps.io.control_readout.uts150cc.events import NewUts150ccPosition
from app_apps.io.spectrometer.events import SpectrometerConfigChanged, SpectrumAck, SpectrumAvailable
from app_apps.recording.models import (
    FORMAT_NAME,
    FORMAT_VERSION,
    SpectrumRecordingInfo,
    SpectrumRecordingMetadata,
)

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
    from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle

log = logging.getLogger(__name__)

SOURCE = "spm002"

#: Queued items between the IPC thread and the writer thread. At ~4 Hz this is minutes of
#: slack, so hitting it means the disk is genuinely stuck and dropping is the right answer.
_QUEUE_MAX = 1024
#: Flush the file after this many traces (and after every metadata entry).
_FLUSH_EVERY = 32
#: How long ``stop()`` waits for the writer thread to drain.
_DRAIN_TIMEOUT_S = 5.0

_consumer_ids = itertools.count()


def _stabilization_settings(primitive: dict) -> dict:
    """A stabilization config's primitive form without ``params``, the live fit state the
    worker rewrites on every accepted frame."""
    return {k: v for k, v in primitive.items() if k != "params"}


def timestamp_key(timestamp_ns: int) -> str:
    """Dataset name for a timestamp: zero-padded so lexicographic order is time order."""
    return f"{int(timestamp_ns):019d}"


@dataclass(frozen=True)
class _Trace:
    timestamp_ns: int
    counts: np.ndarray
    wavelengths: np.ndarray


@dataclass(frozen=True)
class _Metadata:
    timestamp_ns: int
    entry: SpectrumRecordingMetadata


class SpectrumRecorder:
    """Record the spectrometer stream plus the rig state it was taken under.

    Free of Qt and of any routine, so routines, the headless harness and a future scan
    recorder can all drive it. One instance records one run: ``start`` once, ``stop`` once.
    """

    def __init__(
        self,
        bus: EventBus,
        spectrometer: "SpectrometerWorkerHandle",
        stabilization: "PhaseStabilizationHandle",
        grating: "Uts150ccHandle",
        delay: "MfaccHandle",
    ) -> None:
        self._bus = bus
        self._spectrometer = spectrometer
        self._stabilization = stabilization
        self._grating = grating
        self._delay = delay
        # Unique per instance: two recorders may run at once (a standalone recording and
        # a scan), and the coordinator tracks acks by consumer id.
        self.consumer_id = f"spectrum_recorder_{next(_consumer_ids)}"

        self._group: h5py.Group | None = None
        self._lock: threading.RLock | None = None
        self._info: SpectrumRecordingInfo | None = None
        self._queue: "queue.Queue[_Trace | _Metadata]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._unsubs: list = []

        # Read on the IPC thread, written from whichever thread the event arrives on.
        self._state_lock = threading.Lock()
        self._grating_mm: float | None = None
        self._delay_mm: float | None = None
        self._stabilization_running = False
        #: Stabilization settings (config minus live fit state) in the last entry queued.
        self._stabilization_settings: dict | None = None

        self._counts_lock = threading.Lock()
        self.n_traces = 0
        self.n_dropped = 0
        self.n_metadata = 0
        self._traces_since_flush = 0
        self._wavelengths_written = False

    def _bump(self, field: str, by: int = 1) -> None:
        with self._counts_lock:
            setattr(self, field, getattr(self, field) + by)

    @property
    def is_recording(self) -> bool:
        return self._thread is not None

    # -- lifecycle --------------------------------------------------------

    def start(self, group: h5py.Group, lock: threading.RLock) -> None:
        """Begin recording into ``group``.

        ``lock`` guards every access to the file the group lives in. ``h5py`` is not
        thread-safe, so when the group is part of a bigger file, its owner must pass the
        same lock it holds for its own writes.
        """
        if self._thread is not None:
            raise RuntimeError("SpectrumRecorder is already recording")
        self._group = group
        self._lock = lock
        self._info = SpectrumRecordingInfo(
            format_name=FORMAT_NAME,
            format_version=FORMAT_VERSION,
            source=SOURCE,
            started_utc=now_utc_iso(),
        )
        with lock:
            ensure_group(group, "metadata")
            ensure_group(group, "traces")
            write_primitive(group, "info", self._info)
            group.file.flush()

        with self._state_lock:
            self._grating_mm = self._grating.last_position
            self._delay_mm = self._delay.last_position
            self._stabilization_running = self._stabilization.state == WorkerStatus.RUNNING

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._drain, name=f"{self.consumer_id}-writer", daemon=True
        )
        self._thread.start()
        self._enqueue_metadata("start")

        self._unsubs = [
            self._bus.subscribe(NewUts150ccPosition, self._on_grating),
            self._bus.subscribe(NewMfaccPosition, self._on_delay),
            self._bus.subscribe(PhaseTrackingStateChanged, self._on_stabilization_state),
            self._bus.subscribe(SpectrometerConfigChanged, self._on_spectrometer_config),
            self._bus.subscribe(StabilizationConfigChanged, self._on_stabilization_config),
            # Subscribe before registering: registering is what makes the coordinator
            # wait on our ack, so we must already be listening.
            self._bus.subscribe(SpectrumAvailable, self._on_spectrum),
        ]
        self._spectrometer.register_consumer(self.consumer_id)

        # A fresh reading, in case the stage moved while nothing was listening. The reply
        # arrives as a position event and becomes its own metadata entry.
        for stage in (self._grating, self._delay):
            if stage.state == WorkerStatus.RUNNING:
                stage.get_position()
        log.info("Spectrum recorder %s started", self.consumer_id)

    def stop(self) -> None:
        """Stop listening, drain the queue, and stamp the final counts.

        Unregister *first* so the coordinator stops waiting on our ack, then unsubscribe.
        The other way round leaves a promoted slot pending on a consumer that no longer
        listens, stalling every other spectrum consumer.
        """
        if self._thread is None:
            return
        self._spectrometer.unregister_consumer(self.consumer_id)
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

        self._stop.set()
        thread, self._thread = self._thread, None
        thread.join(timeout=_DRAIN_TIMEOUT_S)
        if thread.is_alive():
            log.warning("Spectrum recorder %s: writer did not drain in %.0f s",
                        self.consumer_id, _DRAIN_TIMEOUT_S)

        assert self._group is not None and self._lock is not None and self._info is not None
        self._info.stopped_utc = now_utc_iso()
        self._info.n_traces = self.n_traces
        self._info.n_dropped = self.n_dropped
        with self._lock:
            write_primitive(self._group, "info", self._info)
            self._group.file.flush()
        log.info("Spectrum recorder %s stopped: %d traces, %d metadata entries, %d dropped",
                 self.consumer_id, self.n_traces, self.n_metadata, self.n_dropped)

    # -- rig state (any thread) -------------------------------------------

    def _on_grating(self, event: NewUts150ccPosition) -> None:
        with self._state_lock:
            self._grating_mm = event.position
        self._enqueue_metadata("grating")

    def _on_delay(self, event: NewMfaccPosition) -> None:
        with self._state_lock:
            self._delay_mm = event.position
        self._enqueue_metadata("delay")

    def _on_stabilization_state(self, _: PhaseTrackingStateChanged) -> None:
        state = self._stabilization.state
        if state == WorkerStatus.BUSY:
            return  # a request in flight; the status restores on reply
        running = state == WorkerStatus.RUNNING
        with self._state_lock:
            if running == self._stabilization_running:
                return
            self._stabilization_running = running
        self._enqueue_metadata("stabilization")

    def _on_spectrometer_config(self, _: SpectrometerConfigChanged) -> None:
        self._enqueue_metadata("spectrometer_config")

    def _on_stabilization_config(self, _: StabilizationConfigChanged) -> None:
        """New entry only when a stabilization *setting* changed -- the target phase
        (``set_phase``, via Apply or Capture target), a threshold, the window.

        The event itself fires on every accepted frame: the worker writes the measured phase
        into ``params`` (``c0``, ``phase_ref``) and syncs. ``params`` is live fit state, not a
        setting, so it is left out of the comparison -- otherwise this would write an entry
        several times a second. It is still stored in full in every entry.
        """
        settings = _stabilization_settings(to_primitive(self._stabilization.config))
        with self._state_lock:
            if settings == self._stabilization_settings:
                return
        self._enqueue_metadata("stabilization_config")

    def _enqueue_metadata(self, reason: str) -> None:
        with self._state_lock:
            grating_mm, delay_mm = self._grating_mm, self._delay_mm
            running = self._stabilization_running
        # Snapshot the shared config instances through their primitive form: the panels
        # edit those objects in place, and a queued entry must keep the values it was
        # taken with.
        stabilization = to_primitive(self._stabilization.config)
        with self._state_lock:
            self._stabilization_settings = _stabilization_settings(stabilization)
        entry = SpectrumRecordingMetadata(
            reason=reason,
            # UTS150CC and MFA-CC both count in mm.
            grating_position=None if grating_mm is None else Length(grating_mm, Prefix.MILLI),
            delay_position=None if delay_mm is None else Length(delay_mm, Prefix.MILLI),
            stabilization_running=running,
            spectrometer=from_primitive(
                type(self._spectrometer.config), to_primitive(self._spectrometer.config)),
            stabilization=from_primitive(type(self._stabilization.config), stabilization),
        )
        try:
            self._queue.put_nowait(_Metadata(time.time_ns(), entry))
        except queue.Full:
            log.error("Spectrum recorder %s: queue full, metadata entry %r lost",
                      self.consumer_id, reason)

    # -- consumer (IPC reader thread — must stay short) --------------------

    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            buf = self._spectrometer.buffer
            # Copy out of shared memory: the slot is reused the moment we ack.
            trace = _Trace(
                timestamp_ns=event.timestamp_ns,
                counts=np.array(buf.intensities(event.slot), dtype=np.float32),
                wavelengths=np.array(buf.wavelengths(event.slot), dtype=np.float64),
            )
            try:
                self._queue.put_nowait(trace)
            except queue.Full:
                self._bump("n_dropped")
        except Exception:
            # Never let a read error escape: it would skip the ack and stall the stream.
            self._bump("n_dropped")
            log.exception("Spectrum recorder %s: read failed for slot %d",
                          self.consumer_id, event.slot)
        finally:
            self._bus.publish(SpectrumAck(
                slot=event.slot, item_id=event.item_id, consumer_id=self.consumer_id
            ))

    # -- writer thread ----------------------------------------------------

    def _drain(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                self._write(item)
            except Exception:
                # Recording is additive: a storage failure loses this item, never the run.
                log.exception("Spectrum recorder %s: write failed", self.consumer_id)
                if isinstance(item, _Trace):
                    self._bump("n_dropped")

    def _write(self, item: "_Trace | _Metadata") -> None:
        assert self._group is not None and self._lock is not None
        key = timestamp_key(item.timestamp_ns)
        with self._lock:
            if isinstance(item, _Metadata):
                write_primitive(self._group["metadata"], key, item.entry)
                self._bump("n_metadata")
                self._group.file.flush()
                return

            if not self._wavelengths_written:
                self._group.create_dataset("wavelength_nm", data=item.wavelengths)
                self._wavelengths_written = True
            traces = self._group["traces"]
            if key in traces:
                # Two spectra cannot share a nanosecond; a repeat is the same frame.
                return
            traces.create_dataset(key, data=item.counts)
            self._bump("n_traces")
            self._traces_since_flush += 1
            if self._traces_since_flush >= _FLUSH_EVERY:
                self._traces_since_flush = 0
                self._group.file.flush()

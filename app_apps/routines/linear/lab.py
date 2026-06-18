"""The `lab` facade: the blocking verb surface a linear routine calls.

A routine receives a `Lab` instance and drives the experiment through it — every device
verb blocks until its action completes, so the routine reads as a plain top-to-bottom
script. The facade wires the bridge primitives (`bridge.await_event`) to the existing device
handles and the scope/spectrometer shared-memory consumer handshake. It imports the device
*event* and *buffer* types (all import-clean) but only duck-types the handles/services, so a
routine can be tested with fakes (no subprocess).

Completion signals (verified against the workers):
  * ESP301 move      -> MoveComplete(axis)        (poll thread; OKReply != settled)
  * HWP rotate       -> HwpAngleUpdate            (worker emits then replies OK = settled)
  * picomotor step   -> StepsMoved(axis)          (synchronous worker)
  * servo block/open -> ArmStateChanged(arm)      (synchronous worker)
  * scope capture    -> TraceAvailable -> read -> TraceAck
  * spectrum read    -> SpectrumAvailable -> read -> SpectrumAck

See docs/routine_authoring_plan.md and docs/experiment_physics.md (§2.7 verb grammar).
"""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

import numpy as np

from app_apps.analysis.spectrum_info.fit import fit_spectrum
from app_apps.analysis.spectrum_info.model import SpectrumInfo
from app_apps.io.oscilloscope.events import TraceAck, TraceAvailable
from spm_002.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable
from app_apps.routines.linear.bridge import await_event, cancellable_sleep
from app_apps.routines.linear.cancel import CancelToken, RoutineError
from app_apps.routines.linear.config import LabConfig
from base_core.framework.events.event_bus import EventBus
from base_core.math.models import Angle
from control_readout.esp_301.messages import MoveComplete, PositionUpdate
from control_readout.picomotor.messages import StepsMoved
from control_readout.rgv100bl.messages import HwpAngleUpdate
from control_readout.servo_shutter.messages import ArmStateChanged

log = logging.getLogger(__name__)


class LabUnavailable(RoutineError):
    """Raised when a routine uses a `lab` verb whose device was not wired into this run."""


@dataclass(frozen=True)
class SpectrumReading:
    """A spectrum from the SPM-002: wavelengths (nm) + intensities, both 1-D arrays."""

    wavelengths: np.ndarray
    intensities: np.ndarray


class _Unavailable:
    """Placeholder for an un-wired device; any use raises a clear LabUnavailable."""

    def __init__(self, name: str, needs: str) -> None:
        self._name = name
        self._needs = needs

    def __getattr__(self, item: str) -> Any:
        raise LabUnavailable(
            f"lab.{self._name} is not available in this run (needs {self._needs})"
        )


# ----------------------------------------------------------------------------------------
# Device sub-facades
# ----------------------------------------------------------------------------------------


class StageFacade:
    """One ESP301 axis (probe / delay / truncation). Blocks until motion completes."""

    def __init__(
        self,
        bus: EventBus,
        cancel: CancelToken,
        esp: Any,
        axis: int,
        config: LabConfig,
    ) -> None:
        self._bus = bus
        self._cancel = cancel
        self._esp = esp
        self._axis = axis
        self._config = config
        self._last_position: Optional[float] = None
        self._unsub = bus.subscribe(PositionUpdate, self._on_position)

    def _on_position(self, event: PositionUpdate) -> None:
        if event.axis == self._axis:
            self._last_position = event.position

    def move_to(self, position: float) -> float:
        return self._await_move(lambda: self._esp.move_to(self._axis, position))

    def move_by(self, delta: float) -> float:
        return self._await_move(lambda: self._esp.move_relative(self._axis, delta))

    def _await_move(self, emit: Callable[[], None]) -> float:
        event = await_event(
            self._bus,
            MoveComplete,
            emit=emit,
            match=lambda e: e.axis == self._axis,
            timeout=self._config.move_timeout_s,
            cancel=self._cancel,
            poll=self._config.poll_s,
        )
        if self._config.settle_s:
            cancellable_sleep(self._config.settle_s, cancel=self._cancel)
        self._last_position = event.position
        return event.position

    @property
    def position(self) -> Optional[float]:
        """Last polled position (non-blocking); None until the first PositionUpdate."""
        return self._last_position

    def close(self) -> None:
        self._unsub()


class RotatorFacade:
    """The HWP rotator (RGV100BL). Blocks until the move is acknowledged."""

    def __init__(self, bus: EventBus, cancel: CancelToken, rgv: Any, config: LabConfig) -> None:
        self._bus = bus
        self._cancel = cancel
        self._rgv = rgv
        self._config = config

    def rotate_to(self, angle: Angle) -> None:
        self._await(lambda: self._rgv.rotate_to(angle))

    def home(self) -> None:
        self._await(lambda: self._rgv.home())

    def _await(self, emit: Callable[[], None]) -> None:
        await_event(
            self._bus,
            HwpAngleUpdate,
            emit=emit,
            timeout=self._config.rotate_timeout_s,
            cancel=self._cancel,
            poll=self._config.poll_s,
        )
        if self._config.settle_s:
            cancellable_sleep(self._config.settle_s, cancel=self._cancel)

    def close(self) -> None:  # symmetry with the other facades
        pass


class PicomotorFacade:
    """Mirror picomotors (open-loop). Blocks until the steps are issued."""

    def __init__(self, bus: EventBus, cancel: CancelToken, pico: Any, config: LabConfig) -> None:
        self._bus = bus
        self._cancel = cancel
        self._pico = pico
        self._config = config

    def step(self, axis: int, steps: int) -> int:
        event = await_event(
            self._bus,
            StepsMoved,
            emit=lambda: self._pico.step(axis, steps),
            match=lambda e: e.axis == axis,
            timeout=self._config.step_timeout_s,
            cancel=self._cancel,
            poll=self._config.poll_s,
        )
        return event.total_steps

    def close(self) -> None:
        pass


class ShutterFacade:
    """Servo shutters. `close(arm)` blocks the arm; `open(arm)` unblocks it."""

    def __init__(self, bus: EventBus, cancel: CancelToken, servo: Any, config: LabConfig) -> None:
        self._bus = bus
        self._cancel = cancel
        self._servo = servo
        self._config = config

    def close(self, arm: int) -> None:  # block the arm
        self._await(arm, blocked=True, emit=lambda: self._servo.block(arm))

    def open(self, arm: int) -> None:  # unblock the arm
        self._await(arm, blocked=False, emit=lambda: self._servo.unblock(arm))

    def _await(self, arm: int, *, blocked: bool, emit: Callable[[], None]) -> None:
        await_event(
            self._bus,
            ArmStateChanged,
            emit=emit,
            match=lambda e: e.arm == arm and e.blocked == blocked,
            timeout=self._config.shutter_timeout_s,
            cancel=self._cancel,
            poll=self._config.poll_s,
        )

    def shutdown(self) -> None:  # not named close(); close(arm) is a verb here
        pass


class ScopeFacade:
    """The oscilloscope (CH1 photodiode → XCORR). On-demand single-trace capture.

    Registers as a consumer only for the duration of a capture, so produced frames between
    captures are not held pending (unregister auto-acks any straggler slots).
    """

    def __init__(
        self,
        bus: EventBus,
        cancel: CancelToken,
        service: Any,
        spec: ScopeMemorySpec,
        consumer_id: str,
        config: LabConfig,
    ) -> None:
        self._bus = bus
        self._cancel = cancel
        self._service = service
        self._spec = spec
        self._consumer_id = consumer_id
        self._config = config
        self._buffer: Optional[ScopeBuffer] = None

    def _ensure_buffer(self) -> ScopeBuffer:
        if self._buffer is None:
            try:
                self._buffer = ScopeBuffer.attach(self._spec)
            except FileNotFoundError as exc:
                raise RoutineError(
                    "scope shared memory not found — is the oscilloscope service running?"
                ) from exc
        return self._buffer

    def capture(self, channel: Optional[int] = 0) -> np.ndarray:
        """Block for the next trace; return channel `channel` (None = all channels)."""
        buffer = self._ensure_buffer()
        self._service.register_consumer(self._consumer_id)
        try:
            event = await_event(
                self._bus,
                TraceAvailable,
                timeout=self._config.capture_timeout_s,
                cancel=self._cancel,
                poll=self._config.poll_s,
            )
            trace = buffer.read_trace(event.slot)  # copy; safe to ack after
            self._bus.publish(
                TraceAck(
                    slot=event.slot,
                    item_id=event.item_id,
                    consumer_id=self._consumer_id,
                )
            )
            return trace if channel is None else trace[channel].copy()
        finally:
            # auto-acks any other slots produced while we held the consumer slot
            self._service.unregister_consumer(self._consumer_id)

    def xcorr_point(self, channel: int = 0, n_top: Optional[int] = None) -> float:
        """One XCORR scalar: mean of the N highest samples of a fresh trace."""
        n = self._config.xcorr_top_n if n_top is None else n_top
        trace = self.capture(channel=channel)
        top = np.sort(trace)[-n:]
        return float(np.mean(top))

    def shutdown(self) -> None:
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None


class SpectrometerFacade:
    """The SPM-002 spectrometer. `read()` blocks for the next spectrum."""

    def __init__(
        self,
        bus: EventBus,
        cancel: CancelToken,
        service: Any,
        spec: SpectrumMemorySpec,
        consumer_id: str,
        config: LabConfig,
    ) -> None:
        self._bus = bus
        self._cancel = cancel
        self._service = service
        self._spec = spec
        self._consumer_id = consumer_id
        self._config = config
        self._buffer: Optional[SpectrumBuffer] = None

    def _ensure_buffer(self) -> SpectrumBuffer:
        if self._buffer is None:
            try:
                self._buffer = SpectrumBuffer.attach(self._spec)
            except FileNotFoundError as exc:
                raise RoutineError(
                    "spectrometer shared memory not found — is the spectrometer service running?"
                ) from exc
        return self._buffer

    def read(self) -> SpectrumReading:
        buffer = self._ensure_buffer()
        self._service.register_consumer(self._consumer_id)
        try:
            event = await_event(
                self._bus,
                SpectrumAvailable,
                timeout=self._config.spectrum_timeout_s,
                cancel=self._cancel,
                poll=self._config.poll_s,
            )
            wavelengths = buffer.wavelengths(event.slot)
            intensities = buffer.intensities(event.slot)
            self._bus.publish(
                SpectrumAck(
                    slot=event.slot,
                    item_id=event.item_id,
                    consumer_id=self._consumer_id,
                )
            )
            return SpectrumReading(wavelengths=wavelengths, intensities=intensities)
        finally:
            self._service.unregister_consumer(self._consumer_id)

    def shutdown(self) -> None:
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None


# ----------------------------------------------------------------------------------------
# The Lab aggregator
# ----------------------------------------------------------------------------------------


class Lab:
    """The object injected as the first argument of every linear routine.

    Construct with whatever device handles/services are available; un-wired devices raise a
    clear LabUnavailable when used. The runner (R.4) builds this from DI and calls `close()`
    on teardown.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        cancel: CancelToken,
        esp: Any = None,
        rgv: Any = None,
        picomotor: Any = None,
        servo: Any = None,
        scope_handle: Any = None,
        scope_spec: Optional[ScopeMemorySpec] = None,
        spectrum_handle: Any = None,
        spectrum_spec: Optional[SpectrumMemorySpec] = None,
        probe_axis: int = 1,
        delay_axis: int = 2,
        truncation_axis: int = 3,
        consumer_id: str = "linear_routine",
        config: LabConfig = LabConfig(),
        params: Optional[dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._bus = bus
        self._cancel = cancel
        self._config = config
        self.params: dict[str, Any] = dict(params or {})
        self._log_fn = log_fn or log.info
        self._records: list[dict[str, Any]] = []
        self._closeables: list[Callable[[], None]] = []

        # Motion stages (ESP301 axes)
        if esp is not None:
            self.probe = StageFacade(bus, cancel, esp, probe_axis, config)
            self.delay = StageFacade(bus, cancel, esp, delay_axis, config)
            self.truncation = StageFacade(bus, cancel, esp, truncation_axis, config)
            self._closeables += [self.probe.close, self.delay.close, self.truncation.close]
        else:
            self.probe = self.delay = self.truncation = _Unavailable("probe/delay/truncation", "EspHandle")  # type: ignore[assignment]

        self.hwp = RotatorFacade(bus, cancel, rgv, config) if rgv is not None \
            else _Unavailable("hwp", "RgvHandle")
        # QWP reuses the collaborator's ELL14 rotator; wiring deferred to M4.7.
        self.qwp = _Unavailable("qwp", "ELL14 rotator — deferred to M4.7")

        self.picomotor = PicomotorFacade(bus, cancel, picomotor, config) if picomotor is not None \
            else _Unavailable("picomotor", "PicomotorHandle")

        self.shutter = ShutterFacade(bus, cancel, servo, config) if servo is not None \
            else _Unavailable("shutter", "ServoShutterHandle")

        if scope_handle is not None and scope_spec is not None:
            self.scope = ScopeFacade(bus, cancel, scope_handle, scope_spec, consumer_id, config)
            self._closeables.append(self.scope.shutdown)
        else:
            self.scope = _Unavailable("scope", "OscilloscopeWorkerHandle + ScopeMemorySpec")  # type: ignore[assignment]

        if spectrum_handle is not None and spectrum_spec is not None:
            self.spectrometer = SpectrometerFacade(
                bus, cancel, spectrum_handle, spectrum_spec, consumer_id, config
            )
            self._closeables.append(self.spectrometer.shutdown)
        else:
            self.spectrometer = _Unavailable(  # type: ignore[assignment]
                "spectrometer", "SpectrometerWorkerHandle + SpectrumMemorySpec"
            )

    # ---- analysis ---------------------------------------------------------------------

    def fit_spectrum(self, reading: SpectrumReading) -> SpectrumInfo:
        """Fit a spectrometer reading to the envelope-bounded chirped-sinusoid model."""
        return fit_spectrum(reading.wavelengths, reading.intensities)

    def xcorr_point(self, channel: int = 0, n_top: Optional[int] = None) -> float:
        """Convenience: one XCORR scalar from the scope (mean of top-N samples)."""
        return self.scope.xcorr_point(channel=channel, n_top=n_top)

    # ---- flow / data helpers ----------------------------------------------------------

    def sleep(self, seconds: float) -> None:
        """Cancellable sleep."""
        cancellable_sleep(seconds, cancel=self._cancel, poll=self._config.poll_s)

    def checkpoint(self) -> None:
        """A cancellation point for long pure-CPU loops."""
        self._cancel.raise_if_cancelled()

    def frange(self, start: float, stop: float, step: float) -> Iterator[float]:
        """Inclusive float range; checks cancellation each step."""
        if step == 0:
            raise ValueError("frange step must be non-zero")
        n = int(math.floor((stop - start) / step + 1e-9)) + 1
        for i in range(max(n, 0)):
            self.checkpoint()
            yield start + i * step

    def log(self, message: str) -> None:
        self._log_fn(message)

    def record(self, **fields: Any) -> None:
        """Append one row of results."""
        self._records.append(dict(fields))

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    def save(self, path: str) -> str:
        """Write recorded rows to a CSV (human-accessible). Returns the path."""
        columns: list[str] = []
        for row in self._records:
            for key in row:
                if key not in columns:
                    columns.append(key)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(self._records)
        return path

    def plot(self, x: str, y: str, *, save_path: Optional[str] = None) -> Optional[str]:
        """Plot recorded `y` vs `x`. Saves a PNG if `save_path` is given, else shows."""
        try:
            import matplotlib
            if save_path:
                matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise RoutineError("matplotlib is not installed; cannot plot") from exc

        xs = [row[x] for row in self._records]
        ys = [row[y] for row in self._records]
        fig, ax = plt.subplots()
        ax.plot(xs, ys, marker="o")
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if save_path:
            fig.savefig(save_path)
            plt.close(fig)
            return save_path
        plt.show()
        return None

    # ---- lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        """Release subscriptions and attached buffers. Idempotent."""
        for closer in self._closeables:
            try:
                closer()
            except Exception:
                log.exception("Lab.close: a teardown step failed")
        self._closeables = []

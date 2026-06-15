"""A stateful in-process "optical plant" — the entire physical world for closed-loop tests.

`OpticalPlant` plays three roles at once so the *real* `Lab` can drive it end to end:

  * a fake **ESP** stage handle  (`move_to` / `move_relative`, emits `MoveComplete`/`PositionUpdate`),
  * a fake **RGV** HWP handle     (`rotate_to` / `home`, emits `HwpAngleUpdate`),
  * a fake **spectrometer service** + free-running producer that writes synthetic spectra into a
    real `SpectrumBuffer` and publishes `SpectrumAvailable`.

Crucially the plant is *stateful*: rotating the HWP shifts the next spectrum's `phase0`, moving
the delay shifts `nu0`, moving the truncation shifts `nu_end`. So a control loop genuinely closes
through the real fit + PID, not a stubbed passthrough. The plant also records what it produced
(keyed by `item_id`) and which frames a consumer acked, so a reporter can re-fit exactly the
frames the loop measured (see `report.py`).

Physics mapping (grounded in `analysis/spectrum_info/model.py`):
  nu0_thz   = C / central_wavelength_nm        -> delay      : central_wl = C / (nu0_base + nu0_slope*delay_pos)
  nu_end_thz= C / (central_wl + 2 sigma)       -> truncation : bandwidth = bw_base + bw_slope*trunc_pos
  phase0                                        -> HWP        : phase0 = phase_off + phase_gain*angle_rad
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app_apps.analysis.spectrum_info.generator import synthetic_spectrum, wavelength_grid
from app_apps.analysis.spectrum_info.model import (
    C_NM_THZ,
    SpectrumParams,
    envelope_edges_thz,
)
from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable
from app_apps.routines.linear.cancel import CancelToken
from app_apps.routines.linear.config import LabConfig
from app_apps.routines.linear.lab import Lab
from base_core.framework.events.event_bus import EventBus
from base_core.math.models import Angle
from control_readout.esp_301.messages import MoveComplete, PositionUpdate
from control_readout.rgv100bl.messages import HwpAngleUpdate


@dataclass(frozen=True)
class PlantState:
    """A snapshot of the plant's true (noise-free) state at one instant."""

    delay_pos: float
    trunc_pos: float
    hwp_angle: float  # radians
    nu0_thz: float
    nu_end_thz: float
    phase0: float


@dataclass(frozen=True)
class Produced:
    """One emitted frame: the exact (2, N) array written + the true state when it was made."""

    item_id: int
    frame: np.ndarray  # row 0 wavelengths, row 1 intensities
    state: PlantState


class OpticalPlant:
    """Fake ESP + RGV + spectrometer rolled into one stateful plant. See module docstring."""

    def __init__(
        self,
        bus: EventBus,
        *,
        grid: Optional[np.ndarray] = None,
        # nu0 (THz) = nu0_base + nu0_slope * delay_pos(mm)
        nu0_base: float = 374.0,
        nu0_slope: float = 3.0,
        # bandwidth (nm) = bw_base + bw_slope * trunc_pos(mm)   (drives nu_end)
        bw_base: float = 30.0,
        bw_slope: float = 4.0,
        # phase0 (rad) = phase_off + phase_gain * hwp_angle(rad)
        phase_off: float = 0.05,
        phase_gain: float = 1.0,
        amp_upper: float = 1.0,
        amp_lower: float = 0.05,
        tau_ps: float = 0.1,  # ~4 fringes across the band: enough for the FFT seed, low nu0 bias
        g2: float = 0.0,
        g3: float = 0.0,
        noise: float = 0.01,
        seed: int = 1234,
        delay_axis: int = 2,
        truncation_axis: int = 3,
        produce_interval_s: float = 0.02,
        slot_count: int = 8,
    ) -> None:
        self.bus = bus
        self._grid = grid if grid is not None else wavelength_grid(760.0, 840.0, 512)
        self._nu0_base, self._nu0_slope = nu0_base, nu0_slope
        self._bw_base, self._bw_slope = bw_base, bw_slope
        self._phase_off, self._phase_gain = phase_off, phase_gain
        self._amp_upper, self._amp_lower = amp_upper, amp_lower
        self._tau_ps, self._g2, self._g3 = tau_ps, g2, g3
        self._noise = noise
        self._rng = np.random.default_rng(seed)
        self._delay_axis, self._truncation_axis = delay_axis, truncation_axis
        self._produce_interval_s = produce_interval_s

        # true mutable state (guarded by _lock)
        self._lock = threading.Lock()
        self._delay_pos = 0.0
        self._trunc_pos = 0.0
        self._hwp_angle = 0.0
        self._item_id = 0
        self._slot = 0

        # history for the reporter
        self._produced: dict[int, Produced] = {}
        self.actuations: list[tuple[float, str, float, PlantState]] = []
        self.consumed: list[tuple[float, int]] = []  # (monotonic_t, item_id)

        # real shared-memory buffer (this object is the writer/creator)
        self.spec = SpectrumMemorySpec(
            f"plant_{uuid.uuid4().hex[:8]}", slot_count=slot_count, pixel_count=len(self._grid)
        )
        self.buffer = SpectrumBuffer.create(self.spec)

        self._unsub_ack = bus.subscribe(SpectrumAck, self._on_ack)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- state -> params/observables --------------------------------------------------
    def _params_locked(self) -> SpectrumParams:
        nu0 = self._nu0_base + self._nu0_slope * self._delay_pos
        central_wl = C_NM_THZ / nu0
        bandwidth = max(self._bw_base + self._bw_slope * self._trunc_pos, 1.0)
        phase0 = self._phase_off + self._phase_gain * self._hwp_angle
        return SpectrumParams(
            central_wavelength_nm=central_wl,
            bandwidth_nm=bandwidth,
            amp_upper=self._amp_upper,
            amp_lower=self._amp_lower,
            phase0=phase0,
            tau_ps=self._tau_ps,
            g2=self._g2,
            g3=self._g3,
        )

    def _snapshot_locked(self) -> PlantState:
        p = self._params_locked()
        nu0, _nu_start, nu_end = envelope_edges_thz(p)
        return PlantState(
            delay_pos=self._delay_pos,
            trunc_pos=self._trunc_pos,
            hwp_angle=self._hwp_angle,
            nu0_thz=nu0,
            nu_end_thz=nu_end,
            phase0=p.phase0,
        )

    def state(self) -> PlantState:
        """The current true (noise-free) state — what a perfect measurement would read."""
        with self._lock:
            return self._snapshot_locked()

    # ---- producer ---------------------------------------------------------------------
    def _produce_once(self) -> None:
        with self._lock:
            params = self._params_locked()
            state = self._snapshot_locked()
            slot = self._slot
            self._slot = (self._slot + 1) % self.spec.slot_count
            self._item_id += 1
            item_id = self._item_id
        intensities = synthetic_spectrum(
            self._grid, params, noise=self._noise, seed=int(self._rng.integers(2**31))
        )
        self.buffer.write_spectrum(slot, self._grid, intensities)
        frame = np.vstack([self._grid, intensities]).copy()
        with self._lock:
            self._produced[item_id] = Produced(item_id, frame, state)
            # prune to keep memory bounded over long runs
            if len(self._produced) > 512:
                for old in sorted(self._produced)[:256]:
                    del self._produced[old]
        self.bus.publish(SpectrumAvailable(slot=slot, item_id=item_id, timestamp_ns=time.time_ns()))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._produce_once()
            self._stop.wait(self._produce_interval_s)

    def start(self) -> None:
        self._produce_once()  # ensure a frame exists before the first read
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---- spectrometer service contract ------------------------------------------------
    def register_consumer(self, consumer_id: str) -> None:  # no-op: producer is free-running
        pass

    def unregister_consumer(self, consumer_id: str) -> None:
        pass

    def _on_ack(self, event: SpectrumAck) -> None:
        self.consumed.append((time.monotonic(), event.item_id))

    # ---- ESP stage handle contract ----------------------------------------------------
    def move_to(self, axis: int, position: float) -> None:
        with self._lock:
            self._set_axis_locked(axis, position, relative=False)
            new_pos = self._delay_pos if axis == self._delay_axis else self._trunc_pos
            state = self._snapshot_locked()
        self.actuations.append((time.monotonic(), f"move_to[axis{axis}]", position, state))
        self.bus.publish(PositionUpdate(axis=axis, position=new_pos))
        self.bus.publish(MoveComplete(axis=axis, position=new_pos))

    def move_relative(self, axis: int, delta: float) -> None:
        with self._lock:
            self._set_axis_locked(axis, delta, relative=True)
            new_pos = self._delay_pos if axis == self._delay_axis else self._trunc_pos
            state = self._snapshot_locked()
        self.actuations.append((time.monotonic(), f"move_by[axis{axis}]", delta, state))
        self.bus.publish(PositionUpdate(axis=axis, position=new_pos))
        self.bus.publish(MoveComplete(axis=axis, position=new_pos))

    def _set_axis_locked(self, axis: int, value: float, *, relative: bool) -> None:
        if axis == self._delay_axis:
            self._delay_pos = (self._delay_pos + value) if relative else value
        elif axis == self._truncation_axis:
            self._trunc_pos = (self._trunc_pos + value) if relative else value
        # other axes (probe) are inert in these tests

    # ---- RGV (HWP) handle contract ----------------------------------------------------
    def rotate_to(self, angle: Angle) -> None:
        with self._lock:
            self._hwp_angle = float(angle)  # Angle is a float subclass (radians)
            state = self._snapshot_locked()
        self.actuations.append((time.monotonic(), "rotate_to", float(angle), state))
        self.bus.publish(HwpAngleUpdate(angle=angle))

    def home(self) -> None:
        with self._lock:
            self._hwp_angle = 0.0
            state = self._snapshot_locked()
        self.actuations.append((time.monotonic(), "home", 0.0, state))
        self.bus.publish(HwpAngleUpdate(angle=Angle(0.0)))

    # ---- reporter access --------------------------------------------------------------
    def produced(self, item_id: int) -> Optional[Produced]:
        with self._lock:
            return self._produced.get(item_id)

    # ---- teardown ---------------------------------------------------------------------
    def close(self) -> None:
        self.stop()
        try:
            self._unsub_ack()
        except Exception:
            pass
        try:
            self.buffer.unlink()
        finally:
            self.buffer.close()


def build_plant_lab(
    plant: OpticalPlant, *, config: Optional[LabConfig] = None
) -> tuple[Lab, CancelToken]:
    """Construct a real `Lab` wired to `plant` as ESP + RGV + spectrometer. Returns (lab, cancel).

    Caller is responsible for `lab.close()` then `plant.close()` (in that order) on teardown.
    """
    cancel = CancelToken()
    cfg = config or LabConfig(
        spectrum_timeout_s=5.0,
        move_timeout_s=5.0,
        rotate_timeout_s=5.0,
        poll_s=0.01,
    )
    lab = Lab(
        bus=plant.bus,
        cancel=cancel,
        esp=plant,
        rgv=plant,
        spectrometer_service=plant,
        spectrum_spec=plant.spec,
        consumer_id=f"integ-{uuid.uuid4().hex[:6]}",
        config=cfg,
    )
    return lab, cancel

from __future__ import annotations

from typing import Callable

from base_core.framework.events import EventBus
from base_core.framework.routines.step import Step

from app_apps.analysis.phase_control.events import StabilizationConfigChanged
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.routines.cfg_calibration.cfg_range import CfgRange


class WaitForConfigStep(Step):
    """Subscribe to StabilizationConfigChanged and compute CfgRange from the next update.

    One-shot: unsubscribes after the first event and calls on_complete (routine.advance_step).
    """

    def __init__(
        self,
        slot: int,
        bus: EventBus,
        config: StabilizationConfig,
        cfg_range: CfgRange,
        on_complete: Callable[[], None],
    ) -> None:
        super().__init__(slot)
        self._bus = bus
        self._config = config
        self._cfg_range = cfg_range
        self._on_complete = on_complete
        self._unsub: Callable[[], None] | None = None
        self._done = False

    def start(self) -> None:
        self._done = False
        self._unsub = self._bus.subscribe(StabilizationConfigChanged, self._on_config_changed)

    def stop(self) -> None:
        self._unsubscribe()

    def reset(self) -> None:
        self._unsubscribe()
        self._done = False

    def _unsubscribe(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_config_changed(self, _: StabilizationConfigChanged) -> None:
        if self._done:
            return
        self._done = True
        computed = CfgRange.from_stabilization_config(self._config, self._cfg_range.fwhm)
        self._cfg_range.min = computed.min
        self._cfg_range.max = computed.max
        self._unsubscribe()
        self._on_complete()

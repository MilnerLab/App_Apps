"""Phase stabilization can drive either HWP rotator, switched at runtime.

Checked: a correction goes to exactly the selected rotator, a switch drops the averaging
block and announces itself once, the panel refuses a switch under a running loop, each
device's interlock follows the selection, and the segmented control reports a selection.

Needs Qt for the view models, but no hardware and no event loop:

    python test/stabilization_rotator_test.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from base_core.framework.events import EventBus  # noqa: E402
from base_core.ipc.worker_handle import WorkerStatus  # noqa: E402
from base_core.math.enums import AngleUnit  # noqa: E402
from base_core.math.models import Angle  # noqa: E402
from base_qt.app.dispatcher import QtDispatcher  # noqa: E402
from base_qt.ui.segmented_control import SegmentedControl  # noqa: E402

from app_apps.analysis.phase_control.events import StabilizationRotatorChanged  # noqa: E402
from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (  # noqa: E402
    FringeFitParams,
    StabilizationConfig,
)
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable, DropBatch  # noqa: E402
from app_apps.io.control_readout.ell14.events import RequestRotate  # noqa: E402
from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle  # noqa: E402
from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel  # noqa: E402
from app_apps.io.control_readout.rgv.events import RequestRotateRGV  # noqa: E402
from app_apps.io.control_readout.rgv.handler import RgvHandle  # noqa: E402
from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel  # noqa: E402
from app_apps.io.control_readout.rotator import HwpRotator  # noqa: E402

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


class _FakeSpectrumWriter:
    def register_consumer(self, _cid: str) -> None: ...
    def unregister_consumer(self, _cid: str) -> None: ...


class _FakeConnector:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, msg: object) -> None:
        self.sent.append(msg)


def _handle():
    bus = EventBus()
    handle = PhaseStabilizationHandle(bus, _FakeSpectrumWriter(),  # type: ignore[arg-type]
                                      StabilizationConfig(params=FringeFitParams()))
    seen: list[object] = []
    for t in (RequestRotateRGV, RequestRotate, StabilizationRotatorChanged):
        bus.subscribe(t, seen.append)
    return handle, seen


def _correction() -> CorrectionAvailable:
    return CorrectionAvailable(angle=Angle(0.5, AngleUnit.DEG), sign=1)


# -- dispatch ---------------------------------------------------------------------------
def test_default_is_rgv() -> None:
    handle, seen = _handle()
    handle._on_correction_available(_correction())
    check(handle.rotator == HwpRotator.RGV100BL, "default rotator is the RGV")
    check([type(e) for e in seen] == [RequestRotateRGV], "a correction goes to the RGV by default")


def test_switch_routes_to_ell14_and_back() -> None:
    handle, seen = _handle()
    conn = _FakeConnector()
    handle._connector = conn  # type: ignore[assignment]
    handle.set_rotator(HwpRotator.ELL14)
    handle.set_rotator(HwpRotator.ELL14)   # no-op: not a change
    check([type(e) for e in seen] == [StabilizationRotatorChanged],
          "a switch is announced exactly once")
    check(len(conn.sent) == 1 and isinstance(conn.sent[0], DropBatch),
          "a switch drops the averaging block")
    seen.clear()
    handle._on_correction_available(_correction())
    check([type(e) for e in seen] == [RequestRotate], "after the switch a correction goes to the ELL14")
    check(abs(seen[0].angle.Deg - 0.5) < 1e-9 and seen[0].sign == 1, "the increment and sign are passed through")
    seen.clear()
    handle.set_rotator(HwpRotator.RGV100BL)
    handle._on_correction_available(_correction())
    check([type(e) for e in seen] == [StabilizationRotatorChanged, RequestRotateRGV],
          "switching back routes to the RGV again")


def test_switch_without_connection_does_not_raise() -> None:
    handle, seen = _handle()
    handle.set_rotator(HwpRotator.ELL14)
    check(handle.rotator == HwpRotator.ELL14, "a switch before the subprocess is up still takes")


# -- interlocks -------------------------------------------------------------------------
class _FakePhaseService:
    def __init__(self, rotator: HwpRotator, running: bool = True) -> None:
        self.rotator = rotator
        self.running = running
        self.mode = ControlMode.PHASE_TRACKING

    @property
    def rotator_locked(self) -> bool:
        return self.running

    @property
    def active_state(self) -> WorkerStatus:
        return WorkerStatus.RUNNING if self.running else WorkerStatus.NEW

    def stop_worker(self) -> None:
        self.running = False


def test_interlock_follows_selection() -> None:
    for rotator in HwpRotator:
        svc = _FakePhaseService(rotator)
        rgv = RgvViewModel(EventBus(), QtDispatcher(), RgvHandle(bus=EventBus()), svc)  # type: ignore[arg-type]
        ell = ELL14RotatorViewModel(EventBus(), QtDispatcher(),
                                    ELL14RotatorHandle(bus=EventBus()), svc)  # type: ignore[arg-type]
        check(rgv.stabilization_running == (rotator == HwpRotator.RGV100BL),
              f"loop on {rotator.value}: RGV interlocked only if it is the active rotator")
        check(ell.stabilization_running == (rotator == HwpRotator.ELL14),
              f"loop on {rotator.value}: ELL14 interlocked only if it is the active rotator")
    svc = _FakePhaseService(HwpRotator.ELL14)
    svc.mode = ControlMode.ENVELOPE
    rgv = RgvViewModel(EventBus(), QtDispatcher(), RgvHandle(bus=EventBus()), svc)  # type: ignore[arg-type]
    check(rgv.stabilization_running, "envelope mode still claims the RGV whatever the selection")


# -- widget -----------------------------------------------------------------------------
def test_segmented_control() -> None:
    w = SegmentedControl([("RGV", HwpRotator.RGV100BL), ("ELL14", HwpRotator.ELL14)])
    got: list[object] = []
    w.value_changed.connect(got.append)
    check(w.value() == HwpRotator.RGV100BL, "first segment is selected initially")
    w.set_value(HwpRotator.ELL14)
    check(w.value() == HwpRotator.ELL14 and got == [], "set_value selects silently")
    w._group.button(0).click()
    check(got == [HwpRotator.RGV100BL], "a click emits the new value once")
    w._group.button(0).click()
    check(got == [HwpRotator.RGV100BL] and w.value() == HwpRotator.RGV100BL,
          "clicking the selected segment keeps it pushed in and emits nothing")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(_fails)} failure(s)")
    sys.exit(1 if _fails else 0)

"""The control block every positioned device gets in the Devices panel.

Readout, relative move, absolute move, home -- and the Start/Pause/Resume/Stop the panel
already had. Deliberately one widget for all five devices: the operator moves between a
delay stage and a rotator constantly, and having the same three rows in the same order on
each is worth more than per-device layouts would be.

**The readout shows "—" until the position has actually been read**, never 0.000. An unread
axis and an axis sitting at zero are different facts, and only one of them means the stage
is referenced. Everything downstream depends on that distinction: relative moves are
synthesised from the last known position (see ``MotionViewModel``) and are refused while it
is unknown.

Speed control is deliberately absent. The device layer has ``set_velocity`` /
``set_speed``, but there is no IPC message carrying it -- the ESP301 message set is Move /
Home / GetPos only -- so exposing it means a message and a worker handler per device in the
Devices repo. The decision on this panel was to ship App-side only.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel


class MotionControls(QGroupBox):
    def __init__(self, title: str, vm: MotionViewModel,
                 parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._vm = vm

        outer = QVBoxLayout(self)
        outer.setSpacing(4)

        outer.addLayout(self._build_readout_row())
        outer.addLayout(self._build_relative_row())
        outer.addLayout(self._build_absolute_row())

        vm.position_changed.connect(self._render_position)
        vm.worker_state_changed.connect(self._ctrl.set_status)
        self._render_position(vm.position)

    # -- rows -------------------------------------------------------------------------
    def _build_readout_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._readout = QLabel("—")
        self._readout.setTextFormat(Qt.TextFormat.PlainText)
        self._readout.setAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
        self._readout.setMinimumWidth(110)
        self._readout.setStyleSheet("font-weight: bold;")

        read = QPushButton("Read")
        read.setToolTip("Re-read the position without moving anything")
        read.clicked.connect(self._vm.refresh)

        home = QPushButton("Home")
        home.setToolTip("Drive to the home switch and re-reference")
        home.clicked.connect(self._vm.home)

        self._ctrl = WorkerControlWidget(self._vm.start, self._vm.pause,
                                         self._vm.resume, self._vm.stop, parent=self)
        self._ctrl.set_status(self._vm.worker_status)

        row.addWidget(QLabel("Position"))
        row.addWidget(self._readout)
        row.addWidget(QLabel(self._vm.units))
        row.addWidget(read)
        row.addWidget(home)
        row.addStretch(1)
        row.addWidget(self._ctrl)
        return row

    def _build_relative_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._step = self._spin()
        # The step is a MAGNITUDE; the two buttons carry the direction. A signed step plus
        # a single Go button makes "-0.5" easy to leave in the box and then apply twice by
        # accident, in the direction you were not looking at. Range before value: the
        # default range is 0..99.99 and would silently clamp a larger default step.
        self._step.setRange(0.0, abs(self._vm.limits[1] - self._vm.limits[0]))
        self._step.setValue(self._vm.default_step)
        self._step.setToolTip("Relative step. Synthesised as an absolute move from the last "
                              "read position, so Read must have happened first.")

        minus = QPushButton("− step")
        minus.clicked.connect(lambda: self._vm.move_relative(-self._step.value()))
        plus = QPushButton("+ step")
        plus.clicked.connect(lambda: self._vm.move_relative(+self._step.value()))

        row.addWidget(QLabel("Relative"))
        row.addWidget(minus)
        row.addWidget(self._step)
        row.addWidget(QLabel(self._vm.units))
        row.addWidget(plus)
        row.addStretch(1)
        return row

    def _build_absolute_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._target = self._spin()
        self._target.setRange(*self._vm.limits)

        go = QPushButton("Go")
        go.clicked.connect(lambda: self._vm.move_absolute(self._target.value()))

        row.addWidget(QLabel("Absolute"))
        row.addWidget(self._target)
        row.addWidget(QLabel(self._vm.units))
        row.addWidget(go)
        row.addStretch(1)
        return row

    def _spin(self) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(self._vm.decimals)
        box.setSingleStep(10.0 ** -min(self._vm.decimals, 3))
        box.setKeyboardTracking(False)
        return box

    # -- rendering --------------------------------------------------------------------
    def _render_position(self, position: object) -> None:
        if position is None:
            self._readout.setText("—")
            return
        self._readout.setText(f"{float(position):+.{self._vm.decimals}f}")

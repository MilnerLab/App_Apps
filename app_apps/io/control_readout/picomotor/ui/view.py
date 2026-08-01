"""Mirror-control panel for the New Focus 8742 picomotors.

One block per mirror: an arrow pad driving that mirror's pitch/yaw axes by the
current increment, an editable increment with the operator's 1/10/50 presets, and a
per-axis step readout with a Zero button.

**Everything here says "steps (open-loop)", never a position in physical units.**
The 8742 has no encoder; the number is the controller's own step count, and a step
is nominally ~30 nm but varies with load, direction and temperature and is
asymmetric between directions. The absolute box is a convenience on that same
counter, deliberately smaller and to one side, because relative stepping is the
primary control and presenting the two as equals would imply a calibration that does
not exist.

The critical axis (motor 3, yaw) is called out in its label. A mislabelled axis
during an alignment session wastes the session.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from base_qt.ui.panel_view import PanelView
from base_qt.ui.worker_control_widget import WorkerControlWidget
from control_readout.picomotor.config import MirrorAxes

from app_apps.io.control_readout.picomotor.ui.view_model import (
    INCREMENT_PRESETS,
    PicomotorViewModel,
)


class PicomotorView(PanelView):
    def __init__(self, vm: PicomotorViewModel, parent: QWidget) -> None:
        super().__init__("Mirror picomotors (8742)", parent, vm=vm)
        self._vm = vm
        #: Per-axis readout labels, keyed by axis number.
        self._readouts: dict[int, QLabel] = {}

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.header_layout.addWidget(ctrl)
        self.header_widget.setVisible(True)

        self.body_layout.addWidget(self._build_increment_row())
        for mirror in vm.mirrors:
            self.body_layout.addWidget(self._build_mirror(mirror))

        note = QLabel("Counts are open-loop controller steps — not a calibrated "
                      "position. Use Zero to re-reference.")
        note.setWordWrap(True)
        note.setEnabled(False)
        self.body_layout.addWidget(note)

        vm.steps_changed.connect(self._render_steps)
        # Read the counters once on open, so the panel shows where the axes are
        # without the operator having to move one to find out.
        vm.refresh()
        self._render_steps()

    # -- construction -----------------------------------------------------

    def _build_increment_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)

        self._increment = QSpinBox()
        self._increment.setRange(1, 10_000)
        self._increment.setValue(self._vm.increment)
        self._increment.setToolTip("Steps per arrow press (open-loop controller steps)")
        self._increment.valueChanged.connect(self._vm.set_increment)

        presets = QComboBox()
        presets.setToolTip(
            "Operator's workflow: 50 oversteps but brackets the optimum in a few "
            "clicks, then ~10 to converge")
        for value in INCREMENT_PRESETS:
            presets.addItem(str(value), value)
        presets.setCurrentIndex(INCREMENT_PRESETS.index(self._vm.increment)
                                if self._vm.increment in INCREMENT_PRESETS else 0)
        presets.activated.connect(
            lambda i: self._increment.setValue(int(presets.itemData(i))))

        refresh = QPushButton("Read counters")
        refresh.setToolTip("Re-read the step counters without moving anything")
        refresh.clicked.connect(self._vm.refresh)

        lay.addWidget(QLabel("Increment"))
        lay.addWidget(self._increment)
        lay.addWidget(QLabel("presets"))
        lay.addWidget(presets)
        lay.addStretch(1)
        lay.addWidget(refresh)
        return row

    def _build_mirror(self, mirror: MirrorAxes) -> QWidget:
        box = QGroupBox(mirror.name)
        outer = QHBoxLayout(box)

        outer.addWidget(self._build_arrow_pad(mirror))
        outer.addSpacing(12)

        axes = QVBoxLayout()
        # Yaw first, and marked when critical — the operator reads top-down and the
        # critical axis is the one that must not be confused for its neighbour.
        axes.addWidget(self._build_axis_row(
            mirror.yaw_axis, "Yaw", critical=mirror.critical))
        axes.addWidget(self._build_axis_row(mirror.pitch_axis, "Pitch"))
        axes.addStretch(1)
        outer.addLayout(axes, stretch=1)
        return box

    def _build_arrow_pad(self, mirror: MirrorAxes) -> QWidget:
        pad = QWidget()
        grid = QGridLayout(pad)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        # (text, row, col, axis, sign). Up/down drive pitch, left/right drive yaw.
        for text, r, c, axis, sign in (
            ("▲", 0, 1, mirror.pitch_axis, +1),
            ("◀", 1, 0, mirror.yaw_axis, -1),
            ("▶", 1, 2, mirror.yaw_axis, +1),
            ("▼", 2, 1, mirror.pitch_axis, -1),
        ):
            btn = QPushButton(text)
            btn.setFixedSize(34, 30)
            btn.setAutoRepeat(False)   # one press, one increment — no runaway on hold
            btn.setToolTip(f"axis {axis} by {'+' if sign > 0 else '-'}increment")
            btn.clicked.connect(
                lambda _=False, a=axis, s=sign: self._vm.nudge(a, s))
            grid.addWidget(btn, r, c)
        return pad

    def _build_axis_row(self, axis: int, role: str, critical: bool = False) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)

        name = QLabel(f"{role} — motor {axis}" + ("  ⚠ CRITICAL" if critical else ""))
        name.setMinimumWidth(150)
        if critical:
            name.setStyleSheet("font-weight: bold;")
            name.setToolTip("The yaw axis behind the stage walk-off — check twice "
                            "before moving it")

        readout = QLabel("—")
        readout.setMinimumWidth(110)
        readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        readout.setTextFormat(Qt.PlainText)
        self._readouts[axis] = readout

        target = QSpinBox()
        target.setRange(-1_000_000, 1_000_000)
        target.setToolTip("Absolute move on the open-loop counter — a convenience, "
                          "NOT a calibrated position")
        go = QPushButton("Go")
        go.setFixedWidth(34)
        go.clicked.connect(lambda _=False, a=axis, t=target: self._vm.step_to(a, t.value()))

        zero = QPushButton("Zero")
        zero.setToolTip("Set this axis counter to 0. Moves nothing.")
        zero.clicked.connect(lambda _=False, a=axis: self._vm.zero(a))

        lay.addWidget(name)
        lay.addWidget(readout)
        lay.addWidget(QLabel("steps (open-loop)"))
        lay.addStretch(1)
        lay.addWidget(target)
        lay.addWidget(go)
        lay.addWidget(zero)
        return row

    # -- rendering --------------------------------------------------------

    def _render_steps(self, *_: object) -> None:
        for axis, label in self._readouts.items():
            steps = self._vm.steps_for(axis)
            # "—" not "0": an unread counter and a counter reading zero are different
            # facts, and only one of them means the axis is referenced.
            label.setText("—" if steps is None else f"{steps:+d}")

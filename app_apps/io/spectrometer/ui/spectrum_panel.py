from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QWidget

from base_qt.ui.panel import Panel
from .spectrum_vm import SpectrumVM


class SpectrumPanel(Panel):
    """
    Live spectrum display.

    Shows intensity vs wavelength, updated on every SpectrumAvailable event.
    Integration time can be set via the spin box at the bottom.
    """

    def __init__(self, vm: SpectrumVM) -> None:
        super().__init__("Spectrum", vm)

    @property
    def vm(self) -> SpectrumVM:
        return self.__dict__["vm"]  # type: ignore[return-value]

    @vm.setter
    def vm(self, value: SpectrumVM) -> None:
        self.__dict__["vm"] = value

    def setup(self) -> None:
        # Plot widget
        self._plot = pg.PlotWidget()
        self._plot.setBackground(None)
        self._plot.setLabel("left", "Intensity", units="counts")
        self._plot.setLabel("bottom", "Wavelength", units="nm")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._curve = self._plot.plot(pen=pg.mkPen("#4ea6ff", width=1.5))
        self.body_layout.addWidget(self._plot, stretch=1)

        # Integration time control
        controls = QWidget()
        form = QFormLayout(controls)
        form.setContentsMargins(0, 4, 0, 0)
        self._int_time = QDoubleSpinBox()
        self._int_time.setRange(1.0, 10_000.0)
        self._int_time.setValue(100.0)
        self._int_time.setSuffix(" ms")
        self._int_time.setSingleStep(10.0)
        self._int_time.editingFinished.connect(self._on_integration_time_changed)
        form.addRow("Integration time", self._int_time)
        self.body_layout.addWidget(controls)

        self._connect(self.vm.spectrum_updated, self._on_spectrum)

    def _on_spectrum(self, wavelengths, intensities) -> None:
        self._curve.setData(wavelengths, intensities)

    def _on_integration_time_changed(self) -> None:
        self.vm.set_integration_time(self._int_time.value())

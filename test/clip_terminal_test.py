"""Where the f_cfg readout quotes its short-wavelength terminal.

The band is the fitted envelope's FWHM, ``mu -/+ FWHM/2``. But f_cfg is the phase
DERIVATIVE, and on a trace clipped at the short-wavelength end the envelope below the cut
is the cubic's extrapolation rather than a measurement -- so quoting there states a
frequency for light that was never recorded. The terminal is therefore
``max(mu - FWHM/2, cut_left)``: whichever wavelength is HIGHER, which is always the one
the data reaches.

The operator can override the cut by dragging the left marker. What is pinned here is that
the override wins, that clearing it restores the detected value, and -- the one that
matters most -- that a frame with no clip and no drag reads EXACTLY as it did before any
of this existed.

    python test/clip_terminal_test.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_apps.analysis.phase_control.subprocess.domain.fringe_core as fc  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (  # noqa: E402
    FringeFitParams,
    StabilizationConfig,
)

_fails: list[str] = []

PU = [1.0, 802.0, 4.0, 0.0]             # envelope: mu = 802 nm, sigma = 4 nm
CSIG = (0.0, 2.0 * math.pi, 0.0, 0.0)   # 1 cycle/nm, no chirp
L0 = 802.0


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


def _lo(cut=None) -> float:
    """The low-wavelength terminal cfg_range reports."""
    return float(fc.cfg_range(CSIG, L0, PU, cut_left=cut)[2])


# -- the rule -----------------------------------------------------------------------------
def test_an_unclipped_frame_is_unchanged() -> None:
    """The whole default path. If this moves, every existing reading moved with it."""
    band_lo, _band_hi = fc.fwhm_band_nm(PU)
    check(abs(_lo(None) - band_lo) < 1e-9,
          f"with no cut the terminal is the FWHM edge, {band_lo:.3f} nm (got {_lo(None):.3f})")
    check(fc.cfg_range(CSIG, L0, PU) == fc.cfg_range(CSIG, L0, PU, cut_left=None),
          "and omitting the argument entirely is the same call")


def test_a_clip_inside_the_band_raises_the_terminal() -> None:
    band_lo, _ = fc.fwhm_band_nm(PU)
    check(band_lo < 799.0, "(the test cut sits above the FWHM edge)")
    check(abs(_lo(799.0) - 799.0) < 1e-9,
          f"a cut at 799 nm becomes the terminal (got {_lo(799.0):.3f})")


def test_a_clip_outside_the_band_is_ignored() -> None:
    """Only ever RAISES the terminal: a cut below the FWHM edge removed nothing the readout
    was quoting, and one past the far edge would invert the band."""
    band_lo, band_hi = fc.fwhm_band_nm(PU)
    check(abs(_lo(780.0) - band_lo) < 1e-9,
          f"a cut below the band leaves the terminal alone (got {_lo(780.0):.3f})")
    check(abs(_lo(band_hi + 10.0) - band_lo) < 1e-9,
          "and a cut past the far edge does not invert the band")
    check(abs(_lo(float("nan")) - band_lo) < 1e-9, "a non-finite cut is ignored")


def test_the_terminal_is_the_higher_wavelength_of_the_two() -> None:
    """Stated as the operator stated it, over the whole range."""
    band_lo, band_hi = fc.fwhm_band_nm(PU)
    for cut in (790.0, 795.0, band_lo - 1e-6, band_lo + 1e-6, 799.0, 803.0, band_hi - 1e-6):
        want = max(band_lo, cut) if band_lo < cut < band_hi else band_lo
        if abs(_lo(cut) - want) > 1e-6:
            check(False, f"cut {cut}: expected {want:.4f}, got {_lo(cut):.4f}")
            return
    check(True, "the terminal is max(FWHM edge, cut) at every cut tested")


# -- the override -------------------------------------------------------------------------
class _Handle:
    def __init__(self) -> None:
        self.pushes = 0

    def set_config(self, _cfg) -> None:
        self.pushes += 1


class _Signal:
    """Stands in for the Qt signal.

    The view model is built with ``__new__`` so no QObject is initialised, and a real
    Signal raises "Signal source has been deleted" the moment it is emitted. Recording the
    emissions is more useful here anyway -- what Auto's enabled state depends on IS the
    second argument.
    """

    def __init__(self) -> None:
        self.emissions: list[tuple] = []

    def emit(self, *args) -> None:
        self.emissions.append(args)


def _vm():
    """The view model with Qt stubbed out -- only the cut logic is under test here."""
    from app_apps.analysis.phase_control.ui import stabilization_control_view_model as m
    vm = m.StabilizationControlViewModel.__new__(m.StabilizationControlViewModel)
    vm._config = StabilizationConfig(params=FringeFitParams())
    vm._handle = _Handle()
    vm.cut_left_changed = _Signal()
    vm._plot_frequency = False
    vm._knife_lines = []
    vm._fwhm_lines = []
    vm._rf_label = None
    vm._show_knife_edges = True
    return vm


def test_the_detected_cut_is_used_when_there_is_no_override() -> None:
    vm = _vm()
    vm._config.params.cut_left = 796.5
    check(vm.effective_cut_left == 796.5, "the fit's own cut is the terminal by default")
    check(vm.cut_left_is_manual is False, "and it is not reported as manual")


def test_a_drag_overrides_the_detected_cut() -> None:
    vm = _vm()
    vm._config.params.cut_left = 796.5
    vm._config.manual_cut_left = 799.25
    check(vm.effective_cut_left == 799.25, "the dragged value wins")
    check(vm.cut_left_is_manual is True, "and is reported as manual, so Auto can enable")


def test_a_committed_fit_does_not_wipe_the_override() -> None:
    """The reason the override lives on the config and not on params: params is REPLACED
    wholesale by every committed frame."""
    vm = _vm()
    vm._config.manual_cut_left = 799.25
    vm._config.params = FringeFitParams()      # as a fresh commit does
    vm._config.params.cut_left = 794.0
    check(vm.effective_cut_left == 799.25,
          f"the drag survives the next committed fit (got {vm.effective_cut_left})")


def test_clearing_restores_the_detected_cut() -> None:
    vm = _vm()
    vm._config.params.cut_left = 796.5
    vm._config.manual_cut_left = 799.25
    type(vm).clear_manual_cut_left(vm)
    check(vm.effective_cut_left == 796.5, "Auto hands the terminal back to the fit")
    check(vm._handle.pushes == 1, "and the cleared config is pushed to the subprocess once")
    check(vm.cut_left_changed.emissions == [(796.5, False)],
          f"and it announces manual=False, which is what disables Auto "
          f"(got {vm.cut_left_changed.emissions})")

    # Idempotent: pressing Auto again must not push another config or re-announce.
    type(vm).clear_manual_cut_left(vm)
    check(vm._handle.pushes == 1, "pressing Auto twice is a no-op, not a second push")


def test_no_cut_anywhere_reads_as_none() -> None:
    vm = _vm()
    check(vm.effective_cut_left is None,
          "an unclipped frame with no drag has no terminal override at all")


# -- the plot-x round trip ----------------------------------------------------------------
def test_a_drag_in_frequency_mode_lands_on_the_same_nm() -> None:
    """The marker is drawn through _to_plot_x; a drag comes back through _from_plot_x. If
    those two disagree the edge walks every time the operator touches it."""
    vm = _vm()
    vm._plot_frequency = True
    for nm in (793.0, 799.0, 802.0, 810.0):
        back = type(vm)._from_plot_x(vm, type(vm)._to_plot_x(vm, nm))
        if abs(back - nm) > 1e-9:
            check(False, f"{nm} nm round-tripped to {back}")
            return
    check(True, "nm -> plot x -> nm is exact in frequency mode")
    vm._plot_frequency = False
    check(abs(type(vm)._from_plot_x(vm, type(vm)._to_plot_x(vm, 799.0)) - 799.0) < 1e-12,
          "and in wavelength mode")


TESTS = [
    test_an_unclipped_frame_is_unchanged,
    test_a_clip_inside_the_band_raises_the_terminal,
    test_a_clip_outside_the_band_is_ignored,
    test_the_terminal_is_the_higher_wavelength_of_the_two,
    test_the_detected_cut_is_used_when_there_is_no_override,
    test_a_drag_overrides_the_detected_cut,
    test_a_committed_fit_does_not_wipe_the_override,
    test_clearing_restores_the_detected_cut,
    test_no_cut_anywhere_reads_as_none,
    test_a_drag_in_frequency_mode_lands_on_the_same_nm,
]

if __name__ == "__main__":
    for t in TESTS:
        print(f"\n--- {t.__name__}")
        t()
    print()
    if _fails:
        print(f"FAILED ({len(_fails)}):")
        for f in _fails:
            print("  -", f)
        sys.exit(1)
    print("all clip-terminal checks passed")

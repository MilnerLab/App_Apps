"""Frozen-template phase tracking: the properties the control loop depends on.

Every check here is one the loop would fail silently without. Nothing in this file needs
the app framework or a spectrometer, so it runs anywhere:

    python test/phase_template_test.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.phase_template import (  # noqa: E402
    PhaseTemplate,
    align_sign,
    fit_phase,
    instantaneous_frequency,
    shape_mismatch,
)
from app_apps.analysis.phase_control.subprocess.domain.template_tracker import (  # noqa: E402
    PhaseAverager,
)

X = np.linspace(792.0, 812.0, 700)
L0 = 802.0
CSIG = (0.3, 6.65, 0.12, 0.004)     # c0..c3, a realistic carrier + chirp
PU = (300.0, 802.0, 4.0, 155.0)     # upper envelope Gaussian
PLN = (280.0, 802.0, 4.2, 0.0)      # envelope gap
NOISE = 4.0

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


def make_template(csig=CSIG, pU=PU, pLn=PLN) -> PhaseTemplate:
    t = PhaseTemplate(l0=L0, csig=list(csig), pU=list(pU), pLn=list(pLn),
                      x_ref=[float(v) for v in X])
    y = trace(0.0, csig=csig, pU=pU, pLn=pLn, noise=0.0)
    t.f_ref = [float(v) for v in instantaneous_frequency(X, y, t)]
    t.amp_ref = fit_phase(X, y, t).amplitude
    return t


def trace(delta: float, csig=CSIG, pU=PU, pLn=PLN, noise=NOISE, seed=0,
          scale=1.0, offset=0.0) -> np.ndarray:
    Ud = fc.gauss(X, *pU)
    Ld = Ud - fc.gauss(X, *pLn)
    mid, half = 0.5 * (Ud + Ld), 0.5 * (Ud - Ld)
    phi = fc.phase_poly(np.asarray(csig, float), X - L0) + delta
    y = scale * (mid + half * np.cos(phi)) + offset
    if noise:
        y = y + np.random.default_rng(seed).normal(0, noise, X.size)
    return y


def test_closed_form_matches_brute_force() -> None:
    """The 1-parameter problem is analytically solvable, so the closed form must BE the
    least-squares minimiser -- not merely close to it."""
    tpl = make_template()
    mid, half = tpl.envelopes(X)
    phi = tpl.phi(X)
    worst_bf = worst_truth = 0.0
    for truth in (-3.0, -1.5, -0.3, 0.0, 0.4, 1.5, 3.0):
        y = trace(truth, seed=int(100 * truth) % 97)
        got = fit_phase(X, y, tpl).delta
        grid = np.linspace(-np.pi, np.pi, 200_001)
        sse = [float(np.sum((y - (mid + half * np.cos(phi + d))) ** 2))
               for d in grid[::200]]
        coarse = grid[::200][int(np.argmin(sse))]
        fine = np.linspace(coarse - 1e-2, coarse + 1e-2, 20_001)
        best = fine[int(np.argmin([float(np.sum((y - (mid + half * np.cos(phi + d))) ** 2))
                                   for d in fine]))]
        worst_bf = max(worst_bf, abs(np.angle(np.exp(1j * (got - best)))))
        worst_truth = max(worst_truth, abs(np.angle(np.exp(1j * (got - truth)))))
    check(worst_bf < 1e-4, f"closed form == brute-force minimiser to {worst_bf*1e3:.3f} mrad")
    check(worst_truth < 0.02, f"accuracy vs truth {worst_truth*1e3:.1f} mrad over -3..+3 rad")


def test_cost() -> None:
    tpl = make_template()
    y = trace(0.7)
    fit_phase(X, y, tpl)
    t0 = time.perf_counter()
    for _ in range(200):
        fit_phase(X, y, tpl)
    us = (time.perf_counter() - t0) / 200 * 1e6
    check(us < 2000.0, f"closed-form fit costs {us:.0f} us/frame (cold fit is ~260 000)")


def test_rigid_freeze_survives_intensity_drift() -> None:
    """Phase enters only through the correlation term, so brightness and baseline drift
    cannot bias it. This is why the envelope is frozen rather than allowed to float."""
    tpl = make_template()
    base = fit_phase(X, trace(0.8, noise=0.0), tpl).delta
    worst = 0.0
    for scale in (0.3, 1.0, 2.0):
        for offset in (-50.0, 0.0, 50.0):
            got = fit_phase(X, trace(0.8, noise=0.0, scale=scale, offset=offset), tpl).delta
            worst = max(worst, abs(np.angle(np.exp(1j * (got - base)))))
    # The residual is not zero: scaling leaves ((s-1)*mid + offset)*half in the correlation,
    # which is oscillatory and nearly -- but not exactly -- orthogonal to cos(Phi). What
    # matters is the size: ~10 mrad is 0.6 deg, against a PHASE_TOLERANCE of 10 deg.
    check(worst < 0.02,
          f"phase shifts {worst*1e3:.2f} mrad under 0.3x-2x intensity and +-50 ct baseline "
          f"(tolerance is 10 deg = 175 mrad)")


def test_amplitude_collapses_without_fringes() -> None:
    tpl = make_template()
    strong = fit_phase(X, trace(0.0, noise=0.0), tpl).amplitude
    washed = fit_phase(X, fc.gauss(X, *PU), tpl).amplitude   # bright bump, zero modulation
    ratio = strong / max(washed, 1e-9)
    check(ratio > 20.0,
          f"fringe amplitude drops {ratio:.0f}x when the fringes vanish (in-loop gate)")


def test_shape_mismatch_is_phase_invariant() -> None:
    """Instantaneous frequency is the DERIVATIVE of phase, so a constant offset cancels --
    which is what lets one metric see shape while being blind to the thing that changes
    every frame."""
    tpl = make_template()
    same = [shape_mismatch(X, trace(d, noise=0.0), tpl)
            for d in (0.0, 1.5, -3.0, np.pi)]
    check(max(same) < 0.008,
          f"same shape, phase 0..pi -> mismatch {min(same):.4f}-{max(same):.4f} (below gate)")

    moved = {
        "delay c1 +5%":  shape_mismatch(X, trace(0.0, noise=0.0, csig=(0.3, 6.65 * 1.05, 0.12, 0.004)), tpl),
        "delay c1 +25%": shape_mismatch(X, trace(0.0, noise=0.0, csig=(0.3, 6.65 * 1.25, 0.12, 0.004)), tpl),
        "grating c2 x2": shape_mismatch(X, trace(0.0, noise=0.0, csig=(0.3, 6.65, 0.24, 0.004)), tpl),
        "grating c2 ->0": shape_mismatch(X, trace(0.0, noise=0.0, csig=(0.3, 6.65, 0.0, 0.004)), tpl),
    }
    for name, mm in moved.items():
        check(mm > 0.009, f"{name} -> mismatch {mm:.4f} (above gate, re-captures)")


def test_sign_flip_is_invisible_to_the_hilbert_check() -> None:
    """smooth_absf takes |f|, so a sign-flipped template is IDENTICAL to it. This is why
    the sign hazard needs its own guard rather than relying on trigger 2."""
    tpl = make_template()
    flipped = make_template(csig=tuple(-c for c in CSIG))
    y = trace(0.7, noise=0.0)
    mm = abs(shape_mismatch(X, y, tpl) - shape_mismatch(X, y, flipped))
    check(mm < 1e-6, f"sign-flipped template is indistinguishable to the shape check ({mm:.2e})")

    d_ok = fit_phase(X, y, tpl).delta
    d_flip = fit_phase(X, y, flipped).delta
    check(abs(d_ok + d_flip) < 1e-6,
          f"...and it inverts the measured phase: {d_ok:+.4f} vs {d_flip:+.4f}")


def test_align_sign_enforces_continuity() -> None:
    old = make_template()
    new = make_template(csig=tuple(-c for c in CSIG))
    fixed = align_sign(new, old)
    check(fixed.csig[1] * old.csig[1] > 0,
          "align_sign flips a re-captured template whose carrier disagrees with the previous")
    y = trace(0.7, noise=0.0)
    check(abs(fit_phase(X, y, fixed).delta - fit_phase(X, y, old).delta) < 1e-6,
          "...so the phase it measures is continuous across the re-capture")
    same = align_sign(make_template(), old)
    check(same.csig[1] > 0, "an agreeing template is left alone")


def test_absolute_phase_is_continuous_across_recapture() -> None:
    """Two templates fitted at different moments carry different c0, but describe the SAME
    physical phase at lambda_ref. Track bare delta instead and every re-capture silently
    redefines zero: the next window reads the jump as real error and the loop commands a
    large spurious correction -- at every setpoint of every scan."""
    lam_ref = 802.0
    drift = 0.55
    a = make_template()
    # The re-capture: the same shape, fitted while the physical phase sat `drift` higher, so
    # the new template absorbs that into its own c0. This is what a real re-capture does.
    b = make_template(csig=(CSIG[0] + drift,) + CSIG[1:])

    y = trace(drift, noise=0.0)          # a trace at the phase b was captured at
    da = fit_phase(X, y, a).delta
    db = fit_phase(X, y, b).delta
    check(abs(np.angle(np.exp(1j * (da - db - drift)))) < 1e-6,
          f"bare delta jumps by the re-capture's own offset: {da:+.4f} -> {db:+.4f} "
          f"({abs(da - db):.3f} rad of phantom error)")

    pa = a.absolute_phase(da, lam_ref)
    pb = b.absolute_phase(db, lam_ref)
    check(abs(np.angle(np.exp(1j * (pa - pb)))) < 1e-6,
          f"...while the absolute phase at lambda_ref is continuous ({pa:.6f} vs {pb:.6f})")

    # And it survives a shift of the phase ORIGIN too, which a re-capture also moves.
    du = 800.5 - L0
    c0, c1, c2, c3 = CSIG
    shifted = PhaseTemplate(
        l0=800.5,
        csig=[c0 + c1 * du + c2 * du ** 2 + c3 * du ** 3,
              c1 + 2 * c2 * du + 3 * c3 * du ** 2,
              c2 + 3 * c3 * du,
              c3],
        pU=list(PU), pLn=list(PLN), x_ref=[float(v) for v in X])
    ps = shifted.absolute_phase(fit_phase(X, y, shifted).delta, lam_ref)
    check(abs(np.angle(np.exp(1j * (pa - ps)))) < 1e-6,
          f"...and across a moved phase origin l0 ({pa:.6f} vs {ps:.6f})")


def test_phase_averager_is_circular() -> None:
    """The arithmetic mean of 0.01 and 6.27 rad is pi -- the opposite of both inputs, and a
    confident instruction to drive the plate half a turn the wrong way."""
    a = PhaseAverager()
    check(a.value() is None, "an empty averager has no value (and issues no correction)")
    for p in (0.01, 6.27, 0.02, 6.28):
        a.add(p, 0.5)
    got = abs(np.angle(np.exp(1j * a.value())))
    check(got < 0.3, f"phases straddling the 2pi wrap average to {got:.3f} rad, not pi")

    b = PhaseAverager()
    for _ in range(200):
        b.add(1.234, 0.05)
    check(abs(b.value() - 1.234) < 1e-6, "a constant phase averages to itself")
    b.reset()
    check(b.value() is None and b.count == 0, "reset flushes")


TESTS = [
    test_closed_form_matches_brute_force,
    test_cost,
    test_rigid_freeze_survives_intensity_drift,
    test_amplitude_collapses_without_fringes,
    test_shape_mismatch_is_phase_invariant,
    test_sign_flip_is_invisible_to_the_hilbert_check,
    test_align_sign_enforces_continuity,
    test_absolute_phase_is_continuous_across_recapture,
    test_phase_averager_is_circular,
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
    print("all frozen-template checks passed")

"""Unit tests for the XCORR delay-domain frequency analysis (task C18, spec §8c.0).

The fit is a pure function of one array pair, so this is the second place in the
routine where unit tests are decisive rather than mock theatre (AGENTS.md §7): a
synthetic trace has a *known* f₀ and chirp, so the test can assert on recovered
physics rather than on plumbing.

What this does NOT establish: behaviour on real traces. Every trace here is an exact
Gaussian envelope carrying a polynomial-phase fringe — the model the fit assumes, by
construction. Real sweeps run much nearer Nyquist and are not clean cubics. That gap
is covered from the other side by ``test_xcorr_fringe_parity.py`` (task C21), which
scores this pipeline against ``fringe_core`` on real CSVs, and it must be re-checked
against the first real grid (spec §8c.5).

No pytest: nothing in this repo declares it, and a test dependency is not something
to install ad hoc (AGENTS.md §5). Run it directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_frequency.py

Exit code 0 means every case passed; failures print a traceback and exit 1.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_apps.analysis.xcorr import fringe_fit as ff  # noqa: E402
from app_apps.analysis.xcorr.frequency import (  # noqa: E402
    C_MM_PER_PS, DEFAULT_FWHM_PS, delta_t_ps, fit_sweep, probe_mm_to_ps, separation_mm,
)

# --- tiny harness -------------------------------------------------------------

_FAILURES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  ok   {name}")
    except Exception:
        _FAILURES.append(name)
        print(f"  FAIL {name}")
        traceback.print_exc()


def assert_close(actual: float, expected: float, tol: float, what: str) -> None:
    if not np.isfinite(actual) or abs(actual - expected) > tol:
        raise AssertionError(f"{what}: got {actual!r}, expected {expected} +- {tol}")


def assert_true(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)


# --- synthetic traces ---------------------------------------------------------
#
# Built in the SAME form the fit assumes, so the recovered numbers are checkable:
#
#     y = env(t) * (1 + cos Phi(t)),   env = A exp(-(t-mu)^2 / 2 sigma^2)
#
# whose upper envelope is 2*env and lower envelope 0, giving mid = half = env and a
# normalised fringe of exactly cos(Phi). Phase is written in the frequency domain --
#
#     f(t) = f0 + k*(t-mu) + q*(t-mu)^2          [cycles/ps]
#     Phi  = 2*pi * integral f dt
#
# -- because f0 and the chirp k are what the readout reports, and stating them
# directly is what makes the assertions meaningful.

PROBE_START_MM, PROBE_STOP_MM, N_POINTS = 100.0, 200.0, 401
ENV_SIGMA_PS = 136.0            # ~320 ps FWHM
GHZ_PER_CYC_PER_PS = 1e3


def make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0, quad_ghz_per_ps2=0.0,
               noise_frac=0.0, amp=0.4, phi0=0.7, seed=1,
               n_points=N_POINTS, sigma_ps=ENV_SIGMA_PS):
    """Return ``(probe_mm, v_mean, truth)`` for a synthetic XCORR sweep."""
    probe_mm = np.linspace(PROBE_START_MM, PROBE_STOP_MM, n_points)
    t = probe_mm_to_ps(probe_mm)
    mu = 0.5 * (t[0] + t[-1])
    dt = t - mu

    f0 = f0_ghz / GHZ_PER_CYC_PER_PS               # cycles/ps
    k = chirp_ghz_per_ps / GHZ_PER_CYC_PER_PS      # cycles/ps^2
    q = quad_ghz_per_ps2 / GHZ_PER_CYC_PER_PS      # cycles/ps^3

    phase = phi0 + 2.0 * np.pi * (f0 * dt + 0.5 * k * dt ** 2 + (q / 3.0) * dt ** 3)
    env = amp * np.exp(-dt ** 2 / (2.0 * sigma_ps ** 2))
    y = env * (1.0 + np.cos(phase))
    if noise_frac:
        y = y + np.random.default_rng(seed).normal(0.0, noise_frac * amp, y.shape)

    half_w = 0.5 * DEFAULT_FWHM_PS
    truth = {
        "t_mu_ps": mu,
        "f0_ghz": abs(f0_ghz),
        # |f(mu+W/2) - f(mu-W/2)| for f = f0 + k*dt + q*dt^2. The quadratic term is
        # even in dt, so it cancels across a symmetric window and the bandwidth is
        # |k|*W regardless of q.
        "bandwidth_ghz": abs((chirp_ghz_per_ps * half_w + quad_ghz_per_ps2 * half_w ** 2)
                             - (-chirp_ghz_per_ps * half_w + quad_ghz_per_ps2 * half_w ** 2)),
        "dt_ps": float(np.median(np.diff(t))),
    }
    return probe_mm, y, truth


# =============================================================================
# coordinate maths
# =============================================================================

def test_probe_mm_to_ps():
    """Double pass: 1 mm of stage travel is 2 mm of optical path."""
    assert_close(float(probe_mm_to_ps(C_MM_PER_PS)), 2.0, 1e-12, "1 mm -> ps")
    assert_close(float(probe_mm_to_ps(10.0, zero_mm=10.0)), 0.0, 1e-12, "zero offset")
    # Monotonic and linear.
    v = probe_mm_to_ps([0.0, 1.0, 2.0])
    assert_close(float(v[2] - v[1]), float(v[1] - v[0]), 1e-12, "linearity")


def test_delta_t_and_separation():
    assert_close(delta_t_ps(C_MM_PER_PS), 2.0, 1e-12, "delta_t_ps")
    assert_close(delta_t_ps(0.0), 0.0, 1e-12, "delta_t_ps at zero")
    assert_close(separation_mm(30.0, 12.5), 17.5, 1e-12, "separation_mm")
    # Negative separations are legal: the operator's zero need not be an endpoint.
    assert_close(separation_mm(5.0, 12.5), -7.5, 1e-12, "separation_mm negative")


# =============================================================================
# recovery of known physics
# =============================================================================

def test_recovers_unchirped_carrier():
    probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0)
    r = fit_sweep(probe, y)
    assert_true(r.ok, f"fit failed: {r.status}")
    assert_true(r.trusted, f"not trusted: {r.status}")
    assert_close(r.f_central_ghz, truth["f0_ghz"], 1.0, "f0")
    assert_close(r.bandwidth_ghz, 0.0, 1.0, "bandwidth of an unchirped trace")
    assert_close(r.t_mu_ps, truth["t_mu_ps"], 5.0, "envelope centre")
    assert_true(r.r2_fringe > 0.99, f"r2_fringe {r.r2_fringe}")


def test_recovers_linear_chirp():
    """The headline case: f0 and the swept bandwidth, both to well under a GHz."""
    probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625)
    r = fit_sweep(probe, y)
    assert_true(r.ok and r.trusted, f"fit not trusted: {r.status}")
    assert_close(r.f_central_ghz, truth["f0_ghz"], 1.0, "f0")
    assert_close(r.bandwidth_ghz, truth["bandwidth_ghz"], 0.5, "bandwidth")
    assert_true(truth["bandwidth_ghz"] > 15.0, "test case must actually be chirped")


def test_recovers_cubic_phase():
    """A quadratic frequency term is real TOD; BIC has to admit the cubic to see it."""
    probe, y, truth = make_sweep(f0_ghz=55.0, chirp_ghz_per_ps=0.05,
                                 quad_ghz_per_ps2=2.0e-4)
    r = fit_sweep(probe, y)
    assert_true(r.ok and r.trusted, f"fit not trusted: {r.status}")
    assert_true(r.order == 3, f"BIC chose order {r.order}, expected 3")
    assert_close(r.f_central_ghz, truth["f0_ghz"], 1.5, "f0")
    assert_close(r.bandwidth_ghz, truth["bandwidth_ghz"], 1.0, "bandwidth")


def test_bic_declines_an_unearned_cubic():
    """The other half of the same gate: a purely quadratic phase must stay order 2,
    or c3 soaks up noise and the covariance the readout leans on inflates."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    r = fit_sweep(probe, y)
    assert_true(r.ok, f"fit failed: {r.status}")
    assert_true(r.order == 2, f"BIC chose order {r.order}, expected 2")


def test_survives_noise():
    """0.4 % additive noise, the level the §8c.5 prototype was validated at."""
    for seed in (1, 2, 3, 4, 5):
        probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625,
                                     noise_frac=0.004, seed=seed)
        r = fit_sweep(probe, y)
        assert_true(r.ok and r.trusted, f"seed {seed}: {r.status}")
        assert_close(r.f_central_ghz, truth["f0_ghz"], 2.0, f"f0 (seed {seed})")
        assert_close(r.bandwidth_ghz, truth["bandwidth_ghz"], 1.0,
                     f"bandwidth (seed {seed})")


def test_sign_of_chirp_does_not_change_the_readout():
    """Phi's overall sign is unobservable (cos is even), so the readout takes abs().
    A trace and its conjugate must give the same numbers, not numbers that differ by
    a sign the UI would then have to guess at."""
    _, y_pos, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625)
    probe, y_neg, _ = make_sweep(f0_ghz=-60.0, chirp_ghz_per_ps=-0.0625)
    a = fit_sweep(probe, y_pos)
    b = fit_sweep(probe, y_neg)
    assert_true(a.ok and b.ok, "both fits must succeed")
    assert_close(b.f_central_ghz, a.f_central_ghz, 0.1, "f0 under sign flip")
    assert_close(b.bandwidth_ghz, a.bandwidth_ghz, 0.1, "bandwidth under sign flip")


def test_point_order_does_not_matter():
    """A descending scan is a legal XcorrConfig, and history may arrive unsorted."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625)
    fwd = fit_sweep(probe, y)
    rev = fit_sweep(probe[::-1], y[::-1])
    assert_true(fwd.ok and rev.ok, "both directions must fit")
    assert_close(rev.f_central_ghz, fwd.f_central_ghz, 1e-6, "f0 is order-independent")
    assert_close(rev.bandwidth_ghz, fwd.bandwidth_ghz, 1e-6, "bandwidth likewise")


# =============================================================================
# the uncertainties that replaced shape_ok
# =============================================================================

def test_sigmas_are_finite_and_small_on_a_clean_trace():
    probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    r = fit_sweep(probe, y)
    assert_true(r.ok, f"fit failed: {r.status}")
    for name, s in (("f_central", r.f_central_sigma_ghz),
                    ("bandwidth", r.bandwidth_sigma_ghz)):
        assert_true(np.isfinite(s), f"sigma({name}) is not finite")
        assert_true(s > 0.0, f"sigma({name}) must be positive, got {s}")
        assert_true(s < 5.0, f"sigma({name}) implausibly large on a clean trace: {s}")


def test_sigma_grows_with_noise():
    """The covariance has to be measuring something. A ten-fold noisier trace must
    report a larger uncertainty, otherwise the error bars are decoration."""
    quiet = fit_sweep(*make_sweep(chirp_ghz_per_ps=0.0625, noise_frac=0.002)[:2])
    loud = fit_sweep(*make_sweep(chirp_ghz_per_ps=0.0625, noise_frac=0.02)[:2])
    assert_true(quiet.ok and loud.ok, "both fits must succeed")
    assert_true(loud.f_central_sigma_ghz > quiet.f_central_sigma_ghz,
                f"sigma did not grow with noise: {quiet.f_central_sigma_ghz} -> "
                f"{loud.f_central_sigma_ghz}")


def test_no_shape_ok_field_survives():
    """INV-6: the borrowed pass/fail is gone. Guard against it creeping back in via
    a well-meaning 'restore compatibility' edit -- it carried another experiment's
    tolerance in rad/nm and was invisible precisely because it looked principled."""
    r = fit_sweep(*make_sweep()[:2])
    assert_true(not hasattr(r, "shape_ok"),
                "FrequencyTrace must not expose shape_ok (spec §8c.0)")


# =============================================================================
# the three gates
# =============================================================================

def test_nyquist_gate_fires():
    """Sampling is 1.67 ps, so Nyquist is ~300 GHz. Ask for 280 GHz and the readout
    must declare itself untrusted rather than report an alias as a measurement."""
    probe, y, _ = make_sweep(f0_ghz=280.0, chirp_ghz_per_ps=0.0)
    r = fit_sweep(probe, y)
    assert_close(r.nyquist_ghz, 300.0, 5.0, "nyquist")
    assert_true(not r.nyquist_ok, "near-Nyquist trace must fail the Nyquist gate")
    assert_true(not r.trusted, "and must not be trusted")
    assert_true("near_nyquist" in r.status, f"status should name the gate: {r.status}")


def test_window_gate_fires_when_the_readout_extrapolates():
    """A readout window wider than the fitted core is a cubic extrapolation, which is
    exactly where a loose c2/c3 explodes. It must be flagged, not silently plotted."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625)
    r = fit_sweep(probe, y, fwhm_ps=2000.0)
    assert_true(r.ok, f"fit itself should still succeed: {r.status}")
    assert_true(not r.window_inside_ok, "window must be reported as outside the core")
    assert_true(not r.trusted and "window_outside_fit" in r.status,
                f"status should name the gate: {r.status}")


def test_a_normal_trace_passes_all_three_gates():
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    r = fit_sweep(probe, y)
    assert_true(r.r2_ok and r.window_inside_ok and r.nyquist_ok,
                f"gates: r2={r.r2_ok} window={r.window_inside_ok} nyq={r.nyquist_ok}")
    assert_true(r.trusted and r.status == "ok", f"status {r.status}")


# =============================================================================
# degenerate input -- a failed fit greys a panel, it never fails a run
# =============================================================================

def test_degenerate_inputs_return_not_ok():
    cases = {
        "too_few_points": (np.linspace(0.0, 1.0, 8), np.zeros(8)),
        "shape_mismatch": (np.linspace(0.0, 1.0, 64), np.zeros(63)),
        "flat": (np.linspace(100.0, 200.0, 401), np.ones(401)),
        "nan": (np.linspace(100.0, 200.0, 401), np.full(401, np.nan)),
        "inf": (np.linspace(100.0, 200.0, 401), np.full(401, np.inf)),
        "zeros": (np.linspace(100.0, 200.0, 401), np.zeros(401)),
        "empty": (np.empty(0), np.empty(0)),
        "single_point": (np.array([1.0]), np.array([1.0])),
    }
    for name, (x, y) in cases.items():
        r = fit_sweep(x, y)
        assert_true(not r.ok, f"{name}: expected ok=False, got status {r.status!r}")
        assert_true(bool(r.status), f"{name}: a failure must carry a status string")
        # The UI reads these unconditionally; NaN is fine, a raise is not.
        assert_true(np.isnan(r.f_central_ghz), f"{name}: f_central should be NaN")
        assert_true(r.t_ps.size == 0, f"{name}: no core to plot")


def test_pure_noise_does_not_raise():
    """No signal at all. Any outcome is acceptable except an exception or a trace
    that claims to be trusted."""
    rng = np.random.default_rng(7)
    probe = np.linspace(100.0, 200.0, 401)
    r = fit_sweep(probe, rng.normal(0.0, 1.0, 401))
    assert_true(not r.trusted, f"pure noise must not be trusted, got {r.status!r}")


# =============================================================================
# fringe_fit internals worth pinning
# =============================================================================

def test_envelope_fit_is_scale_free():
    """§8c.0 replaced fringe_core's 1e4-peak rescale with a normalised solve. So the
    same trace scaled by 1e-4 or 1e4 must fit identically -- this is the assertion
    that the absolute-tolerance trap is actually gone."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, amp=0.4)
    t = probe_mm_to_ps(probe)
    base = ff.fit_upper_envelope(t, y)
    for gain in (1e-4, 1e4):
        scaled = ff.fit_upper_envelope(t, y * gain)
        assert_close(scaled[1], base[1], 1.0, f"mu at gain {gain:g}")
        assert_close(scaled[2], base[2], 2.0, f"sigma at gain {gain:g}")
        assert_close(scaled[0] / gain, base[0], 0.05 * abs(base[0]) + 1e-9,
                     f"amplitude at gain {gain:g}")


def test_envelope_warm_start_adapts_to_the_scan_range():
    """The moment-based warm start replaced SIGMA_INIT = 4.0 nm. Halving the physical
    envelope width must halve the fitted sigma -- a fixed constant could not."""
    for sigma in (70.0, 140.0):
        probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, sigma_ps=sigma)
        t = probe_mm_to_ps(probe)
        p = ff.fit_upper_envelope(t, y)
        assert_close(p[2], sigma, 0.15 * sigma, f"fitted sigma for sigma={sigma}")


def test_phase_truncation_only_ever_shrinks_and_runs_at_most_twice():
    """The two anti-circularity guards from §8c.0, asserted directly."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    t = probe_mm_to_ps(probe)
    fit = ff.fit_fringe(t, y)
    assert_true(fit.ok, f"fit failed: {fit.status}")
    assert_true(fit.n_passes in (1, 2), f"n_passes must be 1 or 2, got {fit.n_passes}")
    assert_true(fit.t_core_ps.size <= t.size, "the core can never grow")
    lo, hi = fit.span_ps
    assert_true(t[0] <= lo <= hi <= t[-1], "the core must lie inside the sweep")


def test_fit_fringe_core_is_contiguous():
    """The Hilbert transform needs uniform sampling; a core with a hole in it would
    silently corrupt the phase rather than fail."""
    probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    fit = ff.fit_fringe(probe_mm_to_ps(probe), y)
    assert_true(fit.ok, f"fit failed: {fit.status}")
    steps = np.diff(fit.t_core_ps)
    assert_close(float(np.max(steps)), truth["dt_ps"], 1e-6, "max core step")
    assert_close(float(np.min(steps)), truth["dt_ps"], 1e-6, "min core step")


def test_fit_is_deterministic():
    """No RNG, no global state: the same trace must fit to the same numbers twice.
    This is what lets the UI recompute a readout on demand rather than caching it."""
    probe, y, _ = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)
    a, b = fit_sweep(probe, y), fit_sweep(probe, y)
    assert_close(b.f_central_ghz, a.f_central_ghz, 0.0, "f0 is deterministic")
    assert_close(b.bandwidth_ghz, a.bandwidth_ghz, 0.0, "bandwidth is deterministic")


# =============================================================================

def main() -> int:
    print("XCORR frequency analysis (C18)")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name[5:], fn)
    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} — " + ", ".join(_FAILURES))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

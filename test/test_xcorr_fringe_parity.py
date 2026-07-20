"""Parity of the XCORR delay-domain fit against `fringe_core` (task C21, spec §8c.0).

INV-6 replaced `fringe_core` with a direct ps-native fit. That was the right call, but
it gave up one real thing: `fringe_core` is battle-tested on thousands of real traces
and the new pipeline is not. C21 buys that back by running **both** over the same
data and requiring the frequency readouts to agree wherever both are valid.

**`fringe_core` is used here as an OFFLINE ORACLE — never a runtime dependency.**
That distinction is what makes this legal under defect G22 (the file lives only on a
WIP feature branch the user has ruled off-limits). Nothing in
``app_apps/analysis/xcorr/`` imports it; only this test does, and only to score
against. If the oracle is not importable, the real-data leg reports that and the rest
of the suite still runs — a test that cannot find the oracle must not go green
silently, but it also must not block the build.

The oracle is driven through the **rejected affine map** (spec §8c.2) on purpose:
that is the only way to make `fringe_core` answer a delay-domain question at all, and
the map is exact for the polynomial phase (an affine substitution commutes with
everything the fit does), so any disagreement is a real disagreement between the two
pipelines rather than an artefact of the mapping.

Two legs:

* **Synthetic** — always runs. Traces with known f₀ and chirp, both pipelines scored
  against the truth *and* against each other. This catches a units error, a sign
  convention, or a gross readout bug, and it is the floor.
* **Real traces** — runs only when data is found. **This is the leg that matters**,
  and as of 2026-07-20 it has *never executed*: no real XCORR sweep exists on this
  machine. The only run on disk (``xcorr_runs/XCORR_20260720_031217.h5``) is the
  aborted G19 run — five probe points, all zeros, because the ESP301 went silent
  before the scope was ever read. Point this at real data with:

      set XCORR_PARITY_DATA=<dir of runs>

  and it will pick up every ``.h5`` in it. Until then the suite prints SKIPPED for
  that leg and says why. **Do not read a green run as evidence the fit works on real
  traces** — see spec §8c.5, which says the same thing about the prototype.

No pytest (AGENTS.md §5). Run it directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_fringe_parity.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_apps.analysis.xcorr.frequency import fit_sweep, probe_mm_to_ps  # noqa: E402
from test_xcorr_frequency import make_sweep  # noqa: E402

# --- the affine map the oracle needs (spec §8c.2 — rejected for runtime use) ---

SPAN_NM = 22.0        # fringe_core is calibrated for a bump filling ~24 nm
LAM0 = 802.0
COUNTS_PEAK = 1.0e4   # fringe_core's Nelder-Mead uses ABSOLUTE tolerances, so the
                      # trace must be rescaled or the envelope is never actually fit.
                      # Precisely the trap §8c.0 removed from the new pipeline.

#: Agreement required between the two pipelines, GHz. Generous next to the ~0.003 GHz
#: actually measured on synthetic traces: this test exists to catch a *disagreement in
#: kind* — a factor, a sign, a wrong origin — not to pin either pipeline to the
#: other's last digit. Tightening it would make the new fit's own improvements
#: (a moment-based warm start, scale-free tolerances) read as failures.
PARITY_TOL_GHZ = 1.0

#: Below this the oracle is not answering a well-posed question either, so a
#: disagreement says nothing. Both pipelines must clear it for a trace to be scored.
PARITY_MIN_R2 = 0.90

_FAILURES: list[str] = []
_SKIPS: list[str] = []


class Skip(Exception):
    """Raised by a leg that has no data or no oracle to run against."""


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  ok      {name}")
    except Skip as exc:
        _SKIPS.append(f"{name}: {exc}")
        print(f"  SKIP    {name} — {exc}")
    except Exception:
        _FAILURES.append(name)
        print(f"  FAIL    {name}")
        traceback.print_exc()


def assert_true(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)


# --- the oracle ---------------------------------------------------------------

def _load_oracle():
    """Import `fringe_core`, or explain why the leg cannot run.

    Deliberately a late, local import: nothing in the package may depend on this
    module, and keeping the import inside the test is what enforces that.
    """
    try:
        from app_apps.analysis.phase_control.subprocess.domain import fringe_core
    except Exception as exc:
        raise Skip(f"fringe_core not importable ({exc.__class__.__name__}) — it lives "
                   "only on feat/cubic-phase-fringe-fit (defect G22)") from exc
    return fringe_core


def oracle_readout(fc, probe_mm, y, fwhm_ps=320.0):
    """`fringe_core`'s answer for one XCORR sweep, in GHz — via the affine map.

    Returns ``(f0_ghz, bandwidth_ghz, t_mu_ps, r2)``, or None if it declined the trace.
    """
    t = probe_mm_to_ps(np.asarray(probe_mm, float))
    order = np.argsort(t)
    t, y = t[order], np.asarray(y, float)[order]

    t_mid = 0.5 * (float(t.min()) + float(t.max()))
    scale = SPAN_NM / (float(t.max()) - float(t.min()))     # nm per ps
    lam = LAM0 + (t - t_mid) * scale

    peak = float(np.max(np.abs(y)))
    if not np.isfinite(peak) or peak <= 0:
        return None
    try:
        R = fc.analyze(lam, y * (COUNTS_PEAK / peak),
                       anchor=None, scanfree=True, trunc_method="none")
    except Exception:
        return None
    if R.get("csig") is None or R.get("pU") is None:
        return None

    def f_at(t_ps: float) -> float:
        u = (LAM0 + (t_ps - t_mid) * scale) - R["l0"]
        return abs(1e3 * float(fc.fringe_freq_cyc_per_nm(R["csig"], u)) * scale)

    t_mu = t_mid + (float(R["pU"][1]) - LAM0) / scale
    half = 0.5 * fwhm_ps
    return (f_at(t_mu), abs(f_at(t_mu + half) - f_at(t_mu - half)),
            t_mu, float(R.get("r2_fringe", float("nan"))))


def compare(fc, probe_mm, y, label: str, tol=PARITY_TOL_GHZ) -> str | None:
    """Score both pipelines on one trace. Returns a one-line report, or None if the
    trace was not scoreable (which is not a failure — see PARITY_MIN_R2)."""
    new = fit_sweep(probe_mm, y)
    old = oracle_readout(fc, probe_mm, y)

    if old is None:
        assert_true(not new.trusted,
                    f"{label}: the new fit claims a TRUSTED readout on a trace the "
                    f"oracle declined outright — that is the dangerous direction")
        return None
    o_f0, o_bw, o_mu, o_r2 = old
    if not new.ok:
        raise AssertionError(f"{label}: oracle fit the trace (r2 {o_r2:.3f}) but the "
                             f"new pipeline failed with {new.status!r}")
    if not (np.isfinite(o_r2) and o_r2 >= PARITY_MIN_R2 and new.r2_fringe >= PARITY_MIN_R2):
        return None

    d_f0 = abs(new.f_central_ghz - o_f0)
    d_bw = abs(new.bandwidth_ghz - o_bw)
    assert_true(d_f0 <= tol,
                f"{label}: f0 disagrees by {d_f0:.3f} GHz "
                f"(new {new.f_central_ghz:.3f}, oracle {o_f0:.3f}, tol {tol})")
    assert_true(d_bw <= tol,
                f"{label}: bandwidth disagrees by {d_bw:.3f} GHz "
                f"(new {new.bandwidth_ghz:.3f}, oracle {o_bw:.3f}, tol {tol})")
    return (f"    {label}: df0={d_f0:.4f} dbw={d_bw:.4f} GHz  "
            f"(r2 new {new.r2_fringe:.4f} / oracle {o_r2:.4f})")


# =============================================================================
# leg 1 — synthetic (always runs)
# =============================================================================

def test_parity_on_synthetic_traces():
    """Both pipelines over a spread of carriers and chirps. The floor, not the point."""
    fc = _load_oracle()
    cases = [
        ("flat carrier", dict(f0_ghz=60.0, chirp_ghz_per_ps=0.0)),
        ("light chirp", dict(f0_ghz=60.0, chirp_ghz_per_ps=0.03)),
        ("nominal chirp", dict(f0_ghz=60.0, chirp_ghz_per_ps=0.0625)),
        ("heavy chirp", dict(f0_ghz=60.0, chirp_ghz_per_ps=0.12)),
        ("low carrier", dict(f0_ghz=25.0, chirp_ghz_per_ps=0.05)),
        ("high carrier", dict(f0_ghz=140.0, chirp_ghz_per_ps=0.05)),
        ("cubic phase", dict(f0_ghz=55.0, chirp_ghz_per_ps=0.05,
                             quad_ghz_per_ps2=2.0e-4)),
        ("noisy", dict(f0_ghz=60.0, chirp_ghz_per_ps=0.0625, noise_frac=0.004)),
    ]
    scored = 0
    for label, kw in cases:
        probe, y, _ = make_sweep(**kw)
        line = compare(fc, probe, y, label)
        if line:
            print(line)
            scored += 1
    assert_true(scored >= 6,
                f"only {scored}/{len(cases)} synthetic traces were scoreable — the "
                "oracle is declining traces it should handle")


def test_both_pipelines_agree_with_the_truth():
    """Parity alone would pass if BOTH pipelines were wrong the same way. Anchor it:
    on synthetic traces the answer is known, so score each against truth as well."""
    fc = _load_oracle()
    for chirp in (0.0, 0.0625, 0.12):
        probe, y, truth = make_sweep(f0_ghz=60.0, chirp_ghz_per_ps=chirp,
                                     noise_frac=0.004)
        new = fit_sweep(probe, y)
        old = oracle_readout(fc, probe, y)
        assert_true(new.ok and old is not None, f"chirp {chirp}: both must fit")
        for name, got, want in (("new f0", new.f_central_ghz, truth["f0_ghz"]),
                                ("oracle f0", old[0], truth["f0_ghz"]),
                                ("new bw", new.bandwidth_ghz, truth["bandwidth_ghz"]),
                                ("oracle bw", old[1], truth["bandwidth_ghz"])):
            assert_true(abs(got - want) <= 2.0,
                        f"chirp {chirp}: {name} = {got:.3f}, truth {want:.3f}")


# =============================================================================
# leg 2 — real traces (skips when there is no data; THIS is the leg that matters)
# =============================================================================

def _discover_runs() -> list[Path]:
    roots = []
    env = os.environ.get("XCORR_PARITY_DATA")
    if env:
        roots.append(Path(env))
    roots.append(Path(__file__).resolve().parents[2] / "xcorr_runs")
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out.extend(sorted(root.glob("*.h5")))
        elif root.is_file():
            out.append(root)
    return out


def _sweeps_from_run(path: Path):
    """Yield ``(label, probe_mm, v_mean_pos)`` for every scan group in a run file.

    Skips groups too short or too flat to carry a fringe — the aborted G19 run is
    entirely such groups, and counting them as parity evidence would be exactly the
    false confidence this test exists to prevent.
    """
    import h5py

    with h5py.File(path, "r") as f:
        scans = f.get("scans")
        if scans is None:
            return
        for name in sorted(scans):
            g = scans[name]
            if "probe_mm" not in g or "v_mean_pos" not in g:
                continue
            probe = np.asarray(g["probe_mm"][...], float)
            v = np.asarray(g["v_mean_pos"][...], float)
            if probe.size < 32 or not np.isfinite(v).all() or float(np.ptp(v)) <= 0.0:
                continue
            yield f"{path.name}:{name}", probe, v


def test_parity_on_real_traces():
    """The leg C21 is actually for. Skips loudly rather than passing vacuously."""
    fc = _load_oracle()
    runs = _discover_runs()
    if not runs:
        raise Skip("no run files found; set XCORR_PARITY_DATA to a directory of .h5 runs")

    try:
        import h5py  # noqa: F401
    except ImportError as exc:
        raise Skip("h5py not importable (defect G7)") from exc

    sweeps = [s for path in runs for s in _sweeps_from_run(path)]
    if not sweeps:
        raise Skip(f"{len(runs)} run file(s) found, but none contains a usable sweep "
                   "(>=32 points with non-zero signal) — the only run on disk is the "
                   "aborted G19 run, whose 5 points are all zero")

    scored = 0
    for label, probe, v in sweeps:
        line = compare(fc, probe, v, label)
        if line:
            print(line)
            scored += 1
    print(f"    scored {scored}/{len(sweeps)} real sweeps")
    assert_true(scored > 0,
                f"{len(sweeps)} real sweeps found but none was scoreable — both "
                "pipelines are failing on real data, which is the finding, not a pass")


# =============================================================================

def main() -> int:
    print("XCORR fit vs fringe_core oracle (C21)")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name[5:], fn)
    print()
    for s in _SKIPS:
        print(f"SKIPPED  {s}")
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} — " + ", ".join(_FAILURES))
        return 1
    if _SKIPS:
        print("\npassed, WITH SKIPS — a skipped real-data leg is not evidence the fit "
              "works on real traces.")
    else:
        print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

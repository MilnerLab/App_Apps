"""Unit tests for the XCORR scan planner.

The planner is a pure function, so this is the one place in the routine where unit
tests are decisive rather than mock theatre (AGENTS.md §7). Everything else is
verified end-to-end through ``tools/run_xcorr_headless.py``.

No pytest: nothing in this repo declares it, and a test dependency is not something
to install ad hoc (AGENTS.md §5). Run it directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_planner.py

Exit code 0 means every case passed; failures are printed with a traceback and the
process exits 1.
"""
from __future__ import annotations

import math
import sys
import traceback
from pathlib import Path

# app_apps is not pip-installed — it is imported by virtue of App_Apps/ being the
# working directory when app.py runs. Put it on the path so this script works from
# anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_apps.routines.xcorr.config import XcorrConfig  # noqa: E402
from app_apps.routines.xcorr.planner import (  # noqa: E402
    PlanError,
    expand_range,
    max_frequency_hz,
    plan_scan,
    probe_step_for,
)


# --- tiny harness ---------------------------------------------------------

def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def raises(exc: type[BaseException], fn, *needles: str) -> None:
    """Assert fn() raises `exc`, and that every needle appears in the message."""
    try:
        fn()
    except exc as e:  # noqa: PERF203
        msg = str(e)
        for n in needles:
            assert n in msg, f"expected {n!r} in error message, got: {msg}"
        return
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def make_cfg(**over) -> XcorrConfig:
    base = dict(
        probe_start_mm=70.0, probe_stop_mm=80.0, probe_step_mm=2.5,
        grating_start_mm=-30.0, grating_stop_mm=-20.0, grating_step_mm=10.0,
        delay_base_start_mm=18.0, delay_base_stop_mm=19.0, delay_base_step_mm=1.0,
        out_dir=Path("."),
    )
    base.update(over)
    return XcorrConfig(**base)


# --- expand_range ---------------------------------------------------------

def test_expand_range_inclusive_of_stop():
    assert expand_range(0.0, 10.0, 2.5, name="x") == (0.0, 2.5, 5.0, 7.5, 10.0)


def test_expand_range_keeps_endpoint_despite_float_error():
    # 0..10 by 0.1 is the classic case where naive accumulation drops the endpoint.
    r = expand_range(0.0, 10.0, 0.1, name="x")
    assert len(r) == 101, len(r)
    assert approx(r[-1], 10.0)


def test_expand_range_descending():
    assert expand_range(10.0, 0.0, 5.0, name="x") == (10.0, 5.0, 0.0)


def test_expand_range_single_point_when_start_equals_stop():
    assert expand_range(3.0, 3.0, 1.0, name="x") == (3.0,)


def test_expand_range_truncates_when_step_does_not_divide():
    # 0..10 by 3 -> 0,3,6,9; 10 is not reachable and must not be faked.
    r = expand_range(0.0, 10.0, 3.0, name="x")
    assert len(r) == 4 and approx(r[-1], 9.0), r


def test_expand_range_rejects_non_positive_step():
    raises(PlanError, lambda: expand_range(0.0, 10.0, 0.0, name="probe"), "step must be > 0")
    raises(PlanError, lambda: expand_range(0.0, 10.0, -1.0, name="probe"), "step must be > 0")


def test_expand_range_include_endpoint_appends_when_step_falls_short():
    # 0..10 by 3 truncates at 9; with include_endpoint the exact stop is appended as a
    # final short step.
    assert expand_range(0.0, 10.0, 3.0, name="x", include_endpoint=True) == (0.0, 3.0, 6.0, 9.0, 10.0)


def test_expand_range_include_endpoint_no_duplicate_when_already_inclusive():
    # Step divides evenly -> endpoint already present -> no spurious extra point.
    assert expand_range(0.0, 10.0, 2.5, name="x", include_endpoint=True) == (0.0, 2.5, 5.0, 7.5, 10.0)


def test_expand_range_include_endpoint_descending():
    assert expand_range(10.0, 0.0, 3.0, name="x", include_endpoint=True) == (10.0, 7.0, 4.0, 1.0, 0.0)


# --- limit validation (R2/S1) ---------------------------------------------

def test_probe_beyond_soft_limit_is_refused_naming_the_offender():
    # Neutralise the grating tracking (grating=0, intercept=0) so the base sweep is
    # commanded verbatim and the offending value is the raw probe position.
    cfg = make_cfg(
        probe_start_mm=280.0, probe_stop_mm=300.0, probe_step_mm=10.0,
        grating_start_mm=0.0, grating_stop_mm=0.0, grating_step_mm=1.0,
        probe_intercept_mm=0.0,
    )
    raises(PlanError, lambda: plan_scan(cfg), "probe", "300.0000", "290.5")


def test_probe_made_illegal_by_grating_tracking_is_caught():
    """The base sweep is inside the probe limits, but grating + intercept pushes it out.

    The probe analogue of the delay-correction check: validating the base range alone
    would pass a sweep the stage cannot actually reach once it tracks the grating.
    """
    cfg = make_cfg(
        probe_start_mm=170.0, probe_stop_mm=170.0, probe_step_mm=1.0,   # legal alone
        grating_start_mm=30.0, grating_stop_mm=30.0, grating_step_mm=1.0,
        probe_intercept_mm=110.0,                                       # 170+30+110=310
    )
    raises(PlanError, lambda: plan_scan(cfg), "grating-tracked probe")


def test_probe_offset_tracks_grating_one_to_one():
    cfg = make_cfg(
        probe_start_mm=0.0, probe_stop_mm=10.0, probe_step_mm=5.0,
        grating_start_mm=-20.0, grating_stop_mm=0.0, grating_step_mm=10.0,
        probe_intercept_mm=110.0,
    )
    plan = plan_scan(cfg)
    for s in plan.setpoints:
        assert s.probe_base_mm == (0.0, 5.0, 10.0)  # base sweep is grating-independent
        assert approx(s.probe_offset_mm, s.grating_mm + 110.0)


def test_grating_beyond_soft_limit_is_refused():
    cfg = make_cfg(grating_start_mm=70.0, grating_stop_mm=80.0, grating_step_mm=5.0)
    raises(PlanError, lambda: plan_scan(cfg), "grating")


def test_legal_delay_base_made_illegal_by_the_correction_is_caught():
    """The base range is inside 0..25, but the correction pushes it out.

    This is the failure the corrected-value check exists for: validating the base
    range alone would pass it, and the stage would refuse the move mid-run.
    """
    cfg = make_cfg(
        grating_start_mm=-70.0, grating_stop_mm=70.0, grating_step_mm=70.0,
        delay_base_start_mm=24.0, delay_base_stop_mm=24.0, delay_base_step_mm=1.0,
        delay_slope=0.05,
    )
    raises(PlanError, lambda: plan_scan(cfg), "grating-corrected delay")


def test_endpoint_exactly_on_a_limit_is_accepted():
    cfg = make_cfg(
        probe_start_mm=-9.5, probe_stop_mm=290.5, probe_step_mm=100.0,
        grating_start_mm=0.0, grating_stop_mm=0.0, grating_step_mm=1.0,
        probe_intercept_mm=0.0,
    )
    assert approx(plan_scan(cfg).setpoints[0].probe_base_mm[0], -9.5)


def test_n_traces_must_be_at_least_one():
    raises(PlanError, lambda: plan_scan(make_cfg(n_traces=0)), "n_traces")


# --- loop order (D5) ------------------------------------------------------

def test_fewest_steps_axis_goes_outer_grating_fewer():
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=-20.0, grating_step_mm=10.0,       # 2
        delay_base_start_mm=18.0, delay_base_stop_mm=22.0, delay_base_step_mm=1.0,  # 5
    )
    plan = plan_scan(cfg)
    assert plan.outer_axis == "grating", plan.outer_axis
    # The grating index must be the slow-moving one.
    assert [(s.grating_index, s.delay_index) for s in plan.setpoints[:6]] == [
        (0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (1, 0),
    ]


def test_fewest_steps_axis_goes_outer_delay_fewer():
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=10.0, grating_step_mm=10.0,        # 5
        delay_base_start_mm=18.0, delay_base_stop_mm=19.0, delay_base_step_mm=1.0,  # 2
    )
    plan = plan_scan(cfg)
    assert plan.outer_axis == "delay", plan.outer_axis
    assert [(s.grating_index, s.delay_index) for s in plan.setpoints[:6]] == [
        (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (0, 1),
    ]


def test_tie_prefers_grating_outer_for_backlash():
    plan = plan_scan(make_cfg())  # 2 grating x 2 delay
    assert plan.outer_axis == "grating", plan.outer_axis


# --- correction and flattening -------------------------------------------

def test_correction_is_applied_per_grating_position():
    plan = plan_scan(make_cfg(delay_slope=0.005, delay_intercept_mm=0.1))
    for s in plan.setpoints:
        assert approx(s.delay_correction_mm, 0.005 * s.grating_mm + 0.1)
        assert approx(s.delay_mm, s.delay_base_mm + s.delay_correction_mm)


def test_zero_slope_leaves_delay_at_its_base():
    plan = plan_scan(make_cfg(delay_slope=0.0, delay_intercept_mm=0.0))
    for s in plan.setpoints:
        assert approx(s.delay_mm, s.delay_base_mm)
        assert approx(s.delay_correction_mm, 0.0)


def test_grid_is_fully_flattened_and_counted():
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=-20.0, grating_step_mm=10.0,        # 2
        delay_base_start_mm=18.0, delay_base_stop_mm=19.0, delay_base_step_mm=1.0,  # 2
        probe_start_mm=70.0, probe_stop_mm=80.0, probe_step_mm=2.5,                 # 5
    )
    plan = plan_scan(cfg)
    assert len(plan.setpoints) == 4, len(plan.setpoints)
    assert all(len(sp.probe_base_mm) == 5 for sp in plan.setpoints)
    assert plan.n_points == 20


def test_group_names_are_unique_and_sort_into_grid_order():
    """Group names sort grating-major — which is grid order, not execution order.

    XCORR_SPEC.md §6.1 says sorting yields *scan* order. That is only true when the
    grating took the outer loop; this config is 4 grating x 3 delay, so D5 puts
    delay outer and the file is written in a different sequence than it sorts.
    """
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=0.0, grating_step_mm=10.0,   # 4
        delay_base_start_mm=18.0, delay_base_stop_mm=20.0, delay_base_step_mm=1.0,  # 3
    )
    plan = plan_scan(cfg)
    names = [s.group_name for s in plan.setpoints]

    assert plan.outer_axis == "delay"
    assert len(set(names)) == len(names) == 12
    assert names != sorted(names)  # execution order differs from sort order
    assert sorted(names) == [f"g{g:04d}_d{d:04d}" for g in range(4) for d in range(3)]


def test_group_names_sort_into_execution_order_when_grating_is_outer():
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=-20.0, grating_step_mm=10.0,        # 2
        delay_base_start_mm=18.0, delay_base_stop_mm=20.0, delay_base_step_mm=1.0,  # 3
    )
    plan = plan_scan(cfg)
    names = [s.group_name for s in plan.setpoints]
    assert plan.outer_axis == "grating"
    assert names == sorted(names), names
    assert names[0] == "g0000_d0000"


# --- slope sanity warning -------------------------------------------------

def test_oversized_slope_warns_but_does_not_refuse():
    cfg = make_cfg(
        grating_start_mm=10.0, grating_stop_mm=30.0, grating_step_mm=10.0,
        delay_base_start_mm=10.0, delay_base_stop_mm=12.0, delay_base_step_mm=1.0,
        delay_slope=0.1,   # drags 1 mm per grating step vs a 1 mm delay step
    )
    plan = plan_scan(cfg)
    # Every corrected setpoint is still legal — this is a warning, not a refusal.
    assert plan.warnings and "delay_slope" in plan.warnings[0], plan.warnings
    assert all(0.0 <= s.delay_mm <= 25.0 for s in plan.setpoints)


def test_physical_slope_does_not_warn():
    cfg = make_cfg(
        grating_start_mm=-30.0, grating_stop_mm=-10.0, grating_step_mm=10.0,
        delay_base_start_mm=18.0, delay_base_stop_mm=20.0, delay_base_step_mm=1.0,
        delay_slope=0.005,  # 0.05 mm per 10 mm — the real correction
    )
    assert plan_scan(cfg).warnings == ()


# --- frequency model & adaptive probe step --------------------------------

def test_frequency_model_central_and_bandwidth():
    cfg = make_cfg()
    # zero separation, zero offset -> zero frequency
    assert approx(max_frequency_hz(cfg, 30.1, 0.0), 0.0)
    # central only: 81.5 GHz/mm * 1 mm offset
    assert approx(max_frequency_hz(cfg, 30.1, 1.0), 81.5e9)
    # bandwidth only: 1.905 GHz/mm * 105 mm, halved
    assert approx(max_frequency_hz(cfg, -74.9, 0.0), (1.905 * 105.0 / 2.0) * 1e9)


def test_non_adaptive_uses_the_fixed_step_at_every_setpoint():
    plan = plan_scan(make_cfg(probe_step_mm=2.5))  # adaptive off by default
    assert all(approx(sp.probe_step_mm, 2.5) for sp in plan.setpoints)
    # sweeps are identical when the step is fixed
    first = plan.setpoints[0].probe_base_mm
    assert all(sp.probe_base_mm == first for sp in plan.setpoints)


def test_adaptive_step_caps_at_zero_frequency():
    cfg = make_cfg(adaptive_probe_step=True, probe_step_mm=0.2, probe_step_max_mm=5.0)
    assert approx(probe_step_for(cfg, 30.1, 0.0), 5.0)  # no carrier -> coarsest


def test_adaptive_step_clamps_to_floor_at_high_frequency():
    cfg = make_cfg(adaptive_probe_step=True, probe_step_mm=0.2, probe_oversample=1.5)
    # ~344 GHz — Nyquist step falls below the 0.2 mm floor, so it clamps up.
    assert approx(probe_step_for(cfg, -74.9, 3.0), 0.2)


def test_adaptive_step_is_between_floor_and_cap_mid_band():
    cfg = make_cfg(adaptive_probe_step=True, probe_step_mm=0.2, probe_step_max_mm=5.0,
                   probe_oversample=1.5)
    step = probe_step_for(cfg, 30.1, 1.0)  # 81.5 GHz
    assert 0.2 < step < 5.0, step
    # coarser than a low-freq point is finer? high freq -> finer step
    assert probe_step_for(cfg, 30.1, 1.0) < probe_step_for(cfg, 30.1, 0.2)


def test_adaptive_stepping_cuts_total_points_on_a_low_frequency_run():
    common = dict(
        probe_start_mm=0.0, probe_stop_mm=100.0, probe_step_mm=0.2,
        grating_start_mm=30.1, grating_stop_mm=30.1, grating_step_mm=1.0,
        delay_base_start_mm=0.0, delay_base_stop_mm=0.0, delay_base_step_mm=1.0,
    )
    fixed = plan_scan(make_cfg(**common))
    adaptive = plan_scan(make_cfg(adaptive_probe_step=True, probe_step_max_mm=5.0, **common))
    assert adaptive.n_points < fixed.n_points
    # zero-frequency setpoint collapses to the 5 mm cap: 100/5 + 1 = 21 pts vs 501
    assert adaptive.n_points == 21, adaptive.n_points
    assert fixed.n_points == 501, fixed.n_points


def test_probe_endpoint_always_included_under_adaptive():
    # Every setpoint's probe base sweep must end exactly on probe_stop_mm despite the
    # per-setpoint adaptive step generally not dividing the interval — so analysis has
    # one common right edge across the grid.
    cfg = make_cfg(
        adaptive_probe_step=True,
        probe_start_mm=0.0, probe_stop_mm=125.0, probe_step_mm=0.2,
        probe_step_max_mm=1.0, probe_oversample=2.0, probe_intercept_mm=110.0,
        grating_start_mm=-75.0, grating_stop_mm=30.0, grating_step_mm=5.0,
        delay_base_start_mm=0.0, delay_base_stop_mm=1.0, delay_base_step_mm=0.5,
        delay_slope=-0.005, delay_intercept_mm=17.31,
    )
    plan = plan_scan(cfg)
    assert all(sp.probe_base_mm[0] == 0.0 for sp in plan.setpoints)
    assert all(approx(sp.probe_base_mm[-1], 125.0) for sp in plan.setpoints), \
        {round(sp.probe_base_mm[-1], 4) for sp in plan.setpoints}
    # And the appended endpoint never duplicates when a setpoint's step already divides
    # 125 (the 0.2 mm floor: 625 intervals) — that setpoint keeps a single 125.0.
    assert all(sp.probe_base_mm[-1] != sp.probe_base_mm[-2] for sp in plan.setpoints)


def test_adaptive_rejects_inverted_step_clamp():
    cfg = make_cfg(adaptive_probe_step=True, probe_step_mm=1.0, probe_step_max_mm=0.5)
    raises(PlanError, lambda: plan_scan(cfg), "inverted")


def test_adaptive_rejects_non_positive_oversample():
    # probe_step_for divides by probe_oversample; 0 would ZeroDivisionError deep in
    # setpoint expansion and escape the routine's PlanError handler (defect G25). It
    # must be refused up front as a plan error instead.
    cfg = make_cfg(
        adaptive_probe_step=True,
        probe_step_mm=0.5, probe_step_max_mm=2.5,  # valid clamp, so we reach the oversample check
        probe_oversample=0.0,
    )
    raises(PlanError, lambda: plan_scan(cfg), "probe_oversample")


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
        else:
            print(f"ok    {t.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

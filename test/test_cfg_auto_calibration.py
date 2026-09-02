"""Unit tests for the CFG auto-calibration model, target conversion, and fit.

Pure logic — no Qt, no hardware. Run from the repo root, e.g.
    PYTHONPATH=. .venv/Scripts/python.exe -m pytest test/test_cfg_auto_calibration.py
    PYTHONPATH=. .venv/Scripts/python.exe test/test_cfg_auto_calibration.py
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from app_apps.routines.cfg_auto_calibration.calibration import (
    CentrifugeCalibration,
    hz_to_thz,
    thz_to_hz,
)
from app_apps.routines.cfg_auto_calibration.fit import (
    CalibrationPoint,
    fit_calibration,
    load_calibration_points,
)
from app_apps.routines.cfg_auto_calibration.target import CfgTarget, TargetMode


def _cal() -> CentrifugeCalibration:
    return CentrifugeCalibration(
        beta0=0.10, gamma0=1.0e-4,
        delay_zero_mm=17.0, dt_per_mm=6.6712819,
        grating_zero_mm=30.0, dbeta_per_mm=1.0e-3, dgamma_per_mm=1.0e-6,
        tau_ps=320.0,
    )


# ------------------------------------------------------------------ round-trip
def test_forward_inverse_round_trip_full_cubic():
    cal = _cal()
    grating, delay = 35.0, 17.5
    f0, df = cal.frequencies_at(grating, delay)
    g2, d2 = cal.positions_for(f0, df)
    assert math.isclose(g2, grating, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(d2, delay, rel_tol=1e-6, abs_tol=1e-6)


def test_forward_inverse_round_trip_linear_reduction():
    cal = replace(_cal(), gamma0=0.0, dgamma_per_mm=0.0)
    grating, delay = 28.0, 16.0
    f0, df = cal.frequencies_at(grating, delay)
    g2, d2 = cal.positions_for(f0, df)
    assert math.isclose(g2, grating, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(d2, delay, rel_tol=1e-6, abs_tol=1e-6)


def test_bandwidth_is_linear_in_grating_at_fixed_delay():
    # df = (a0/2pi) tau with a0 linear in dbeta -> linear in grating position.
    cal = replace(_cal(), gamma0=0.0, dgamma_per_mm=0.0)
    delay = 17.0  # dt = 0 at the zero -> df comes purely from dbeta
    _, df1 = cal.frequencies_at(31.0, delay)
    _, df2 = cal.frequencies_at(32.0, delay)
    _, df3 = cal.frequencies_at(33.0, delay)
    assert math.isclose(df2 - df1, df3 - df2, rel_tol=1e-9)


# ------------------------------------------------------------------ target
def test_target_start_end_to_center_bw():
    t = CfgTarget.from_start_end(start_hz=1.0e12, end_hz=3.0e12)
    assert math.isclose(t.center_hz, 2.0e12)
    assert math.isclose(t.bandwidth_hz, 2.0e12)
    assert math.isclose(t.start_hz, 1.0e12)
    assert math.isclose(t.end_hz, 3.0e12)


def test_target_mode_switch_preserves_value():
    t = CfgTarget.from_center_bandwidth(center_hz=2.0e12, bandwidth_hz=2.0e12)
    a, b = t.fields(TargetMode.START_END)
    round_trip = CfgTarget.from_fields(TargetMode.START_END, a, b)
    assert math.isclose(round_trip.center_hz, t.center_hz)
    assert math.isclose(round_trip.bandwidth_hz, t.bandwidth_hz)


def test_units_helpers():
    assert math.isclose(hz_to_thz(1.0e12), 1.0)
    assert math.isclose(thz_to_hz(2.5), 2.5e12)


# ------------------------------------------------------------------ fit
def _synthetic_points(cal: CentrifugeCalibration) -> list[CalibrationPoint]:
    pts = []
    for g in (28.0, 30.0, 32.0, 34.0):
        for d in (16.0, 17.0, 18.0):
            f0, df = cal.frequencies_at(g, d)
            pts.append(CalibrationPoint(grating_mm=g, delay_mm=d,
                                        f0_hz=thz_to_hz(f0), df_hz=thz_to_hz(df)))
    return pts


def test_fit_recovers_chirp_coefficients():
    truth = _cal()
    points = _synthetic_points(truth)
    # Start from a perturbed base (same geometry, wrong chirp).
    base = replace(truth, beta0=0.05, gamma0=0.0, dbeta_per_mm=2.0e-3, dgamma_per_mm=0.0)
    result = fit_calibration(points, base=base)
    assert result.n_points == len(points)
    assert result.rms_f0_hz < 1.0  # Hz — essentially exact on synthetic data
    assert result.rms_df_hz < 1.0
    assert math.isclose(result.calibration.beta0, truth.beta0, rel_tol=1e-3, abs_tol=1e-4)
    assert math.isclose(result.calibration.dbeta_per_mm, truth.dbeta_per_mm,
                        rel_tol=1e-3, abs_tol=1e-6)


def test_fit_holds_geometry_fixed():
    truth = _cal()
    points = _synthetic_points(truth)
    base = replace(truth, beta0=0.05)
    result = fit_calibration(points, base=base)
    assert result.calibration.tau_ps == base.tau_ps
    assert result.calibration.dt_per_mm == base.dt_per_mm
    assert result.calibration.delay_zero_mm == base.delay_zero_mm
    assert result.calibration.grating_zero_mm == base.grating_zero_mm


def test_load_points_json_and_csv(tmp_path: Path):
    truth = _cal()
    pts = _synthetic_points(truth)
    rows = [{"grating_mm": p.grating_mm, "delay_mm": p.delay_mm,
             "f0_hz": p.f0_hz, "df_hz": p.df_hz} for p in pts]

    jpath = tmp_path / "cal.json"
    jpath.write_text(json.dumps({"points": rows}), encoding="utf-8")
    loaded_json = load_calibration_points(jpath)
    assert len(loaded_json) == len(pts)

    cpath = tmp_path / "cal.csv"
    header = "grating_mm,delay_mm,f0_hz,df_hz\n"
    body = "\n".join(f"{r['grating_mm']},{r['delay_mm']},{r['f0_hz']},{r['df_hz']}" for r in rows)
    cpath.write_text(header + body, encoding="utf-8")
    loaded_csv = load_calibration_points(cpath)
    assert len(loaded_csv) == len(pts)
    assert math.isclose(loaded_csv[0].grating_mm, pts[0].grating_mm)


if __name__ == "__main__":
    import sys
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                    import tempfile
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failures else 0)

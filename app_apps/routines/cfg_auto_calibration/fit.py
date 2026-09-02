"""The centrifuge fit map — the calibration black box behind the UI.

Two layers of "fit" exist in this experiment; keep them straight:

  1. Per-scan fit (upstream, NOT here). Each xcorr scan's oscillation — which runs at
     exactly twice the centrifuge frequency — is fit to recover the instantaneous
     frequency curve f_us(t) and reduce it to two scalars (central frequency f0, swept
     range df) for that (grating, delay) setting. That pipeline (fringe_core) lives in the
     xcorr stack; this routine consumes its reduced product.

  2. Calibration-surface fit (HERE). Given a grid of reduced points
     (grating_mm, delay_mm, f0, df), fit the coefficients of the CentrifugeCalibration
     model so the model reproduces the measured surface. Send-to then inverts that model.

`CentrifugeFitMap` is the object the UI holds. It owns the current CentrifugeCalibration,
answers position<->frequency queries in Hz, and refreshes the calibration from a chosen
dataset via `recompute_from_xcorr`.

Input dataset format (`recompute_from_xcorr`): a reduced calibration table, one row per
(grating, delay) setting, as either
  * JSON: {"points": [{"grating_mm": .., "delay_mm": .., "f0_hz": .., "df_hz": ..}, ...]}
  * CSV with a header row: grating_mm,delay_mm,f0_hz,df_hz
This is the reduced output of the xcorr pipeline (layer 1), not raw traces.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from app_apps.routines.cfg_auto_calibration.calibration import (
    CalibrationError,
    CentrifugeCalibration,
    hz_to_thz,
    thz_to_hz,
)


@dataclass(frozen=True)
class CalibrationPoint:
    """One reduced xcorr result: two arm positions and the two frequencies measured."""

    grating_mm: float
    delay_mm: float
    f0_hz: float
    df_hz: float


@dataclass(frozen=True)
class FitResult:
    """Outcome of a calibration-surface fit."""

    calibration: CentrifugeCalibration
    n_points: int
    rms_f0_hz: float
    rms_df_hz: float


class FitMapError(RuntimeError):
    """Raised when a dataset cannot be loaded or fit."""


class CentrifugeFitMap:
    """Holds the live calibration and maps between arm positions and target frequencies."""

    def __init__(self, calibration: CentrifugeCalibration | None = None) -> None:
        self._cal = calibration if calibration is not None else CentrifugeCalibration()

    @property
    def calibration(self) -> CentrifugeCalibration:
        return self._cal

    def set_calibration(self, calibration: CentrifugeCalibration) -> None:
        self._cal = calibration

    # -- queries used by the UI (Hz boundary) --------------------------------
    def positions_for(self, center_hz: float, bandwidth_hz: float) -> tuple[float, float]:
        """Arm positions (grating_mm, delay_mm) realizing a (center, bandwidth) target."""
        try:
            return self._cal.positions_for(hz_to_thz(center_hz), hz_to_thz(bandwidth_hz))
        except CalibrationError as exc:
            raise FitMapError(str(exc)) from exc

    def frequencies_for(self, grating_mm: float, delay_mm: float) -> tuple[float, float]:
        """Forward check: (center_hz, bandwidth_hz) the model predicts at two positions."""
        f0_thz, df_thz = self._cal.frequencies_at(grating_mm, delay_mm)
        return thz_to_hz(f0_thz), thz_to_hz(df_thz)

    # -- recompute from a chosen dataset -------------------------------------
    def recompute_from_xcorr(self, path: str | Path) -> FitResult:
        """Load a reduced calibration dataset and refit; updates the live calibration."""
        points = load_calibration_points(path)
        result = fit_calibration(points, base=self._cal)
        self._cal = result.calibration
        return result


# --------------------------------------------------------------------- I/O
def load_calibration_points(path: str | Path) -> list[CalibrationPoint]:
    """Read a reduced calibration table (JSON or CSV) into CalibrationPoints."""
    p = Path(path)
    if not p.exists():
        raise FitMapError(f"Dataset not found: {p}")
    text = p.read_text(encoding="utf-8-sig")

    rows: list[dict]
    if p.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FitMapError(f"Invalid JSON in {p.name}: {exc}") from exc
        rows = payload.get("points", payload if isinstance(payload, list) else [])
    else:
        rows = list(csv.DictReader(text.splitlines()))

    points: list[CalibrationPoint] = []
    for i, row in enumerate(rows):
        try:
            points.append(CalibrationPoint(
                grating_mm=float(row["grating_mm"]),
                delay_mm=float(row["delay_mm"]),
                f0_hz=float(row["f0_hz"]),
                df_hz=float(row["df_hz"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise FitMapError(
                f"Row {i} of {p.name} is missing grating_mm/delay_mm/f0_hz/df_hz: {exc}"
            ) from exc
    if not points:
        raise FitMapError(f"No calibration points in {p.name}.")
    return points


# --------------------------------------------------------------------- fit
#: Chirp coefficients refreshed by the fit. The geometric constants (zeros, dt_per_mm,
#: tau_ps) are metrology held fixed — they are not identifiable from (f0, df) data alone.
_FIT_FIELDS = ("beta0", "gamma0", "dbeta_per_mm", "dgamma_per_mm")


def fit_calibration(
    points: list[CalibrationPoint],
    base: CentrifugeCalibration,
    iterations: int = 40,
) -> FitResult:
    """Gauss-Newton least-squares fit of the chirp coefficients to a calibration surface.

    Fits ``_FIT_FIELDS`` so the model's (f0, df) match the measured points, holding the
    geometric calibration (the mm<->physical maps and tau) fixed. Residuals and Jacobian
    are formed by forward-evaluating the model in THz; the step is solved via the normal
    equations with a small Levenberg damping for robustness.
    """
    if not points:
        raise FitMapError("Cannot fit an empty dataset.")

    theta = [float(getattr(base, name)) for name in _FIT_FIELDS]
    eps = 1e-6
    lam = 1e-9

    def model_cal(params: list[float]) -> CentrifugeCalibration:
        return replace(base, **dict(zip(_FIT_FIELDS, params)))

    def residuals(params: list[float]) -> list[float]:
        cal = model_cal(params)
        res: list[float] = []
        for pt in points:
            f0, df = cal.frequencies_at(pt.grating_mm, pt.delay_mm)
            res.append(f0 - hz_to_thz(pt.f0_hz))
            res.append(df - hz_to_thz(pt.df_hz))
        return res

    n = len(theta)
    for _ in range(iterations):
        r = residuals(theta)
        m = len(r)
        # Jacobian by forward differences (n small; cheap and dependency-free).
        jac = [[0.0] * n for _ in range(m)]
        for j in range(n):
            bumped = list(theta)
            step = eps * (abs(theta[j]) + eps)
            bumped[j] += step
            r_j = residuals(bumped)
            for i in range(m):
                jac[i][j] = (r_j[i] - r[i]) / step

        # Normal equations JtJ * delta = -Jt r, with Levenberg damping on the diagonal.
        jtj = [[0.0] * n for _ in range(n)]
        jtr = [0.0] * n
        for i in range(m):
            for a in range(n):
                jtr[a] += jac[i][a] * r[i]
                for b in range(n):
                    jtj[a][b] += jac[i][a] * jac[i][b]
        for a in range(n):
            jtj[a][a] *= (1.0 + lam)

        delta = _solve_linear(jtj, [-v for v in jtr])
        if delta is None:
            break
        theta = [theta[a] + delta[a] for a in range(n)]
        if all(abs(d) < 1e-15 for d in delta):
            break

    cal = model_cal(theta)
    rms_f0, rms_df = _rms_residuals(cal, points)
    return FitResult(calibration=cal, n_points=len(points), rms_f0_hz=rms_f0, rms_df_hz=rms_df)


def _rms_residuals(
    cal: CentrifugeCalibration, points: list[CalibrationPoint]
) -> tuple[float, float]:
    sq_f0 = 0.0
    sq_df = 0.0
    for pt in points:
        f0, df = cal.frequencies_at(pt.grating_mm, pt.delay_mm)
        sq_f0 += (thz_to_hz(f0) - pt.f0_hz) ** 2
        sq_df += (thz_to_hz(df) - pt.df_hz) ** 2
    n = len(points)
    return math.sqrt(sq_f0 / n), math.sqrt(sq_df / n)


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve a square linear system by Gaussian elimination with partial pivoting."""
    n = len(b)
    # Work on an augmented copy.
    mat = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(mat[r][col]))
        if abs(mat[pivot][col]) < 1e-30:
            return None
        mat[col], mat[pivot] = mat[pivot], mat[col]
        pv = mat[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = mat[r][col] / pv
            for k in range(col, n + 1):
                mat[r][k] -= factor * mat[col][k]
    return [mat[i][n] / mat[i][i] for i in range(n)]

"""Centrifuge calibration model — the position <-> frequency map.

This module encodes the centrifuge instantaneous-frequency model the operator gave us
and the inversion that turns a target (central frequency, swept range) into the two arm
positions that realize it. It is pure math: no Qt, no IPC, no hardware.

Physics (see the theory note). The centrifuge is two co-propagating chirped pulses of
opposite helicity; their superposition is linearly polarized with an axis that rotates at
half the instantaneous optical-frequency difference between the arms. With the standard
chirped-pulse phase kept to cubic (TOD) order, the polarization rotation frequency is

    f_us(t) = f0 + (a0 / 2pi) * t + (3 * dgamma / 4pi) * t^2                       (Eq. fus)

    Omega0 = beta0 * dt + (1/2) * dbeta * dt + (3/8) * dgamma * dt^2
    f0     = Omega0 / (2pi)

    a0     = dbeta + 3 * gamma0 * dt + (3/2) * dgamma * dt
    df     = f_us(tau/2) - f_us(-tau/2) = (a0 / 2pi) * tau                        (Eq. dfus)

The quadratic term is even in t and cancels in the swept-range difference, so df is set
entirely by the linear coefficient a0.

Control variables:
  * dt    -- the inter-arm delay, set by the DELAY arm (MFA-CC). Shaper advanced by +dt/2,
             delay by -dt/2. dt = dt_per_mm * (delay_mm - delay_zero_mm).
  * dbeta -- extra quadratic chirp from the grating separation, set by the GRATING arm
             (UTS150CC). To leading order dbeta is linear in the separation increment
             dL = grating_mm - grating_zero_mm, so dbeta = dbeta_per_mm * dL.
  * dgamma-- extra cubic chirp from the grating, also taken linear in dL:
             dgamma = dgamma_per_mm * dL (usually small; default slope 0).

Units are the natural ones for the model and stay internal to this module:
  time in ps, chirp beta in rad/ps^2, gamma in rad/ps^3, frequency f in cycles/ps = THz.
Callers convert at the boundary (Hz <-> THz) via the helpers at the bottom.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: Speed of light in stage-friendly units (mm/ps). Same constant the xcorr stack uses for
#: the double-pass retroreflector delay mapping.
_C_MM_PER_PS = 0.299792458

#: Default double-pass delay per mm of DELAY-stage travel: dt = 2 * dL / c.
_DEFAULT_DT_PER_MM = 2.0 / _C_MM_PER_PS  # ~6.6713 ps/mm


class CalibrationError(ValueError):
    """Raised when a target cannot be mapped to a physical arm position."""


@dataclass(frozen=True)
class CentrifugeCalibration:
    """Coefficients of the centrifuge model, in the module's natural units.

    The chirp coefficients (beta0, gamma0, dbeta_per_mm, dgamma_per_mm) are what the
    ``recompute_from_xcorr`` fit refreshes; the geometric constants (the zeros, dt_per_mm,
    tau_ps) are metrology set once and held fixed across a fit.
    """

    # Reference (delay) arm chirp.
    beta0: float = 0.10          # rad/ps^2
    gamma0: float = 0.0          # rad/ps^3

    # DELAY arm: delay_mm -> dt (ps).
    delay_zero_mm: float = 0.0   # mm at which dt = 0
    dt_per_mm: float = _DEFAULT_DT_PER_MM  # ps/mm

    # GRATING arm: grating_mm -> dbeta (rad/ps^2) and dgamma (rad/ps^3).
    grating_zero_mm: float = 0.0                 # mm at which the arms have equal chirp
    dbeta_per_mm: float = 1.0e-3                 # rad/ps^2 per mm of separation
    dgamma_per_mm: float = 0.0                   # rad/ps^3 per mm of separation

    # Swept-range window tau (ps) — operator-supplied width over which df is defined.
    tau_ps: float = 320.0

    # ------------------------------------------------------------------ maps
    def dt_of_delay(self, delay_mm: float) -> float:
        """Inter-arm delay dt (ps) at a DELAY-stage position."""
        return self.dt_per_mm * (delay_mm - self.delay_zero_mm)

    def delay_of_dt(self, dt_ps: float) -> float:
        """DELAY-stage position (mm) realizing an inter-arm delay dt."""
        if self.dt_per_mm == 0.0:
            raise CalibrationError("dt_per_mm is zero; delay stage cannot set dt.")
        return self.delay_zero_mm + dt_ps / self.dt_per_mm

    def separation_of_grating(self, grating_mm: float) -> float:
        """Grating separation increment dL (mm) at a GRATING-stage position."""
        return grating_mm - self.grating_zero_mm

    def dbeta_of_grating(self, grating_mm: float) -> float:
        return self.dbeta_per_mm * self.separation_of_grating(grating_mm)

    def dgamma_of_grating(self, grating_mm: float) -> float:
        return self.dgamma_per_mm * self.separation_of_grating(grating_mm)

    def grating_of_dbeta(self, dbeta: float) -> float:
        """GRATING-stage position (mm) realizing a quadratic-chirp difference dbeta."""
        if self.dbeta_per_mm == 0.0:
            raise CalibrationError("dbeta_per_mm is zero; grating stage cannot set dbeta.")
        return self.grating_zero_mm + dbeta / self.dbeta_per_mm

    # --------------------------------------------------------------- forward
    def frequencies_at(self, grating_mm: float, delay_mm: float) -> tuple[float, float]:
        """Forward model: (f0, df) in THz at a pair of arm positions."""
        dt = self.dt_of_delay(delay_mm)
        dbeta = self.dbeta_of_grating(grating_mm)
        dgamma = self.dgamma_of_grating(grating_mm)
        return self._frequencies_from_physical(dt, dbeta, dgamma)

    def _frequencies_from_physical(
        self, dt: float, dbeta: float, dgamma: float
    ) -> tuple[float, float]:
        omega0 = self.beta0 * dt + 0.5 * dbeta * dt + (3.0 / 8.0) * dgamma * dt * dt
        a0 = dbeta + 3.0 * self.gamma0 * dt + 1.5 * dgamma * dt
        f0 = omega0 / (2.0 * math.pi)
        df = (a0 / (2.0 * math.pi)) * self.tau_ps
        return f0, df

    # --------------------------------------------------------------- inverse
    def positions_for(self, f0_thz: float, df_thz: float) -> tuple[float, float]:
        """Inverse model: arm positions (grating_mm, delay_mm) realizing (f0, df).

        Solves for (dt, dbeta) from the two model equations, then maps back through the
        geometric calibration. ``dgamma`` is tied to ``dbeta`` through the shared grating
        separation (dgamma = r * dbeta with r = dgamma_per_mm / dbeta_per_mm), so the full
        cubic model is honoured. A closed-form quadratic (the dgamma = 0 reduction) seeds a
        short Newton refinement of the full system.

        Raises CalibrationError if no physical solution exists.
        """
        dt, dbeta = self._solve_dt_dbeta(f0_thz, df_thz)
        return self.grating_of_dbeta(dbeta), self.delay_of_dt(dt)

    def _solve_dt_dbeta(self, f0_thz: float, df_thz: float) -> tuple[float, float]:
        two_pi = 2.0 * math.pi
        A = two_pi * f0_thz               # = beta0*dt + 1/2 dbeta dt + 3/8 dgamma dt^2
        B = two_pi * df_thz / self.tau_ps  # = dbeta + 3 gamma0 dt + 3/2 dgamma dt

        # r ties dgamma to dbeta via the shared grating separation.
        r = (self.dgamma_per_mm / self.dbeta_per_mm) if self.dbeta_per_mm != 0.0 else 0.0

        dt0, dbeta0 = self._seed_dgamma_zero(A, B)
        dt, dbeta = dt0, dbeta0

        # Newton on F(dt, dbeta) = 0 for the full (cubic) model.
        for _ in range(50):
            f1 = self.beta0 * dt + 0.5 * dbeta * dt + (3.0 / 8.0) * r * dbeta * dt * dt - A
            f2 = dbeta + 3.0 * self.gamma0 * dt + 1.5 * r * dbeta * dt - B
            if abs(f1) < 1e-12 and abs(f2) < 1e-12:
                break
            j11 = self.beta0 + 0.5 * dbeta + 0.75 * r * dbeta * dt
            j12 = 0.5 * dt + (3.0 / 8.0) * r * dt * dt
            j21 = 3.0 * self.gamma0 + 1.5 * r * dbeta
            j22 = 1.0 + 1.5 * r * dt
            det = j11 * j22 - j12 * j21
            if abs(det) < 1e-30:
                break
            dt -= (j22 * f1 - j12 * f2) / det
            dbeta -= (-j21 * f1 + j11 * f2) / det

        if not (math.isfinite(dt) and math.isfinite(dbeta)):
            raise CalibrationError(
                f"No physical arm position realizes f0={f0_thz:.4g} THz, "
                f"df={df_thz:.4g} THz under the current calibration."
            )
        return dt, dbeta

    def _seed_dgamma_zero(self, A: float, B: float) -> tuple[float, float]:
        """Closed-form (dt, dbeta) for the dgamma = 0 reduction.

        With dgamma = 0:  dbeta = B - 3 gamma0 dt, and substituting into A gives
        (3/2) gamma0 dt^2 - (beta0 + B/2) dt + A = 0.
        """
        a = 1.5 * self.gamma0
        b = -(self.beta0 + 0.5 * B)
        c = A
        if abs(a) < 1e-30:
            # Linear: (beta0 + B/2) dt = A
            dt = A / -b if abs(b) > 1e-30 else 0.0
        else:
            disc = b * b - 4.0 * a * c
            if disc < 0.0:
                # No real root under the reduction; fall back to the linear estimate,
                # Newton on the full model may still converge.
                dt = A / (self.beta0 + 0.5 * B) if abs(self.beta0 + 0.5 * B) > 1e-30 else 0.0
            else:
                root = math.sqrt(disc)
                r1 = (-b + root) / (2.0 * a)
                r2 = (-b - root) / (2.0 * a)
                # Physical delay is the small-magnitude branch; the other root is spurious.
                dt = r1 if abs(r1) <= abs(r2) else r2
        dbeta = B - 3.0 * self.gamma0 * dt
        return dt, dbeta


# --------------------------------------------------------------------- units
_THZ_PER_HZ = 1e-12


def hz_to_thz(hz: float) -> float:
    return float(hz) * _THZ_PER_HZ


def thz_to_hz(thz: float) -> float:
    return float(thz) / _THZ_PER_HZ

from __future__ import annotations

from dataclasses import dataclass, fields
import inspect
from typing import (
    Any,
    Callable,
    ClassVar,
    Sequence,
    get_type_hints,
)

import lmfit
import numpy as np

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde
from base_core.math.functions import spectrum_fit_skew
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length, Time
from base_core.quantities.specific_models import GDD


def _first_param(func: Callable) -> str:
    return next(iter(inspect.signature(func).parameters))


@dataclass
class SpectralFitParams(PrimitiveSerde):
    """Fit parameters for a two-arm interference spectrum with a skewed second arm.

    Theta(Omega) = theta0 + theta1*Omega + theta2*Omega^2
    Ghat(Omega)   = plain Gaussian envelope of the R (reference) arm
    Shat_a(Omega) = peak-normalized skew-normal envelope of the L (grating) arm (eq:skewnorm)
    B(Omega)      = R*Ghat(Omega) + L*Shat_a(Omega)
    V(Omega)      = 2*sqrt(R*L*Ghat(Omega)*Shat_a(Omega)) / B(Omega)   (eq:V_skew)
    I             = B(Omega) * (1 + V(Omega) * cos(Theta(Omega))) + offset
    """

    lambda0: Length = Length(802.38, Prefix.NANO)
    delta_lambda_fwhm: Length = Length(7.4728, Prefix.NANO)
    R: float = 0.25                 # R-arm (reference) amplitude
    theta0: Angle = Angle(0.0)                    # constant phase offset [rad]
    theta1: Time = Time(-0.3, Prefix.PICO)        # linear phase coeff [ps]  (approx -tau)
    theta2: GDD = GDD(0.0, Prefix.PICO)           # quadratic phase coeff [ps^2]
    alpha: float = 0.0              # L-arm skewness (0 = Gaussian shape)
    epsilon: float = 0.0            # L-arm skew location [rad/ps]
    s: float = 9.2847               # L-arm skew scale [rad/ps]
    L: float = 0.25                 # L-arm (grating) amplitude
    offset: float = 0.0
    residual: float = 0.0

    KIND: ClassVar[str] = "skew"

    # --- fitting -----------------------------------------------------------
    def _fit_func(self) -> Callable:
        return spectrum_fit_skew

    def _apply_bounds(self, params: lmfit.Parameters) -> None:
        params["R"].set(min=0.0)
        params["L"].set(min=0.0)
        params["s"].set(min=1e-6)

    def fit_full(self, wavelengths_nm: np.ndarray,
                 intensities: np.ndarray) -> "SpectralFitParams":
        """Multi-parameter fit. lambda0/delta_lambda_fwhm fixed; R/L/offset seeded from data."""
        func = self._fit_func()
        first_arg = _first_param(func)
        model = lmfit.Model(func, independent_vars=[first_arg])
        params = model.make_params(**self.to_fit_kwargs(func))
        params["lambda0"].set(vary=False)
        params["delta_lambda_fwhm"].set(vary=False)
        # R/L together play the old A's role; split evenly with no assumed skew
        half_pp = float((intensities.max() - intensities.min()) / 2)
        params["R"].set(value=half_pp / 2, min=0.0)
        params["L"].set(value=half_pp / 2, min=0.0)
        params["alpha"].set(value=0.0)
        params["epsilon"].set(value=0.0)
        params["offset"].set(value=float(intensities.min()))
        self._apply_bounds(params)
        result = model.fit(
            intensities, params=params,
            **{first_arg: wavelengths_nm}, max_nfev=1_000_000,
        )
        return type(self).from_fit_result(self, result)

    def fit_phase_only(self, wavelengths_nm: np.ndarray,
                       intensities: np.ndarray) -> "SpectralFitParams":
        """Phase-only fit — all parameters fixed except theta0."""
        func = self._fit_func()
        first_arg = _first_param(func)
        model = lmfit.Model(func, independent_vars=[first_arg])
        params = model.make_params(**self.to_fit_kwargs(func))
        for name, par in params.items():
            par.vary = (name == "theta0")
        result = model.fit(intensities, params=params, **{first_arg: wavelengths_nm})
        return type(self).from_fit_result(self, result)

    # --- (un)packing for lmfit --------------------------------------------
    def to_fit_kwargs(self, func: Callable[..., Any]) -> dict[str, float]:
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())[1:]
        kwargs: dict[str, float] = {}
        type_hints = get_type_hints(type(self))
        for name in param_names:
            val = getattr(self, name)
            field_type = type_hints.get(name, type(val))
            conv = type(self)._to_float_conv(field_type)
            kwargs[name] = conv(val)
        return kwargs

    @classmethod
    def from_fit_result(cls, base: "SpectralFitParams",
                        result: lmfit.model.ModelResult) -> "SpectralFitParams":
        best = result.best_values
        type_hints: dict[str, type[Any]] = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            name = f.name
            if name in best:
                field_type = type_hints.get(name, float)
                conv = cls._from_float_conv(field_type)
                kwargs[name] = conv(best[name])
            elif name == "residual":
                kwargs[name] = float(np.sum(result.residual ** 2))
            else:
                kwargs[name] = getattr(base, name)
        return cls(**kwargs)

    @classmethod
    def mean(cls, items: Sequence["SpectralFitParams"]) -> "SpectralFitParams":
        if not items:
            raise ValueError("At least one SpectralFitParams is required.")
        type_hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            name = f.name
            values = [getattr(p, name) for p in items]
            field_type = type_hints.get(name, type(values[0]))
            to_float = cls._to_float_conv(field_type)
            from_float = cls._from_float_conv(field_type)
            if field_type in cls._TO_FLOAT:
                nums = [to_float(v) for v in values]
                kwargs[name] = from_float(sum(nums) / len(nums))
            else:
                kwargs[name] = values[0]
        return cls(**kwargs)

    def copy_from(self, other: "SpectralFitParams") -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(other, f.name))

    # --- serialization ----------------------------------------------------
    def to_primitive(self) -> Primitive:
        out: dict[str, Any] = {"kind": self.KIND}
        for f in fields(self):
            val = getattr(self, f.name)
            out[f.name] = val.to_primitive() if isinstance(val, (Length, Angle, Time, GDD)) else val
        return out

    @classmethod
    def from_primitive(cls, v: Primitive) -> "SpectralFitParams":
        type_hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in v:
                continue
            ft = type_hints.get(f.name, float)
            if ft is Length:
                kwargs[f.name] = Length.from_primitive(v[f.name])
            elif ft is Angle:
                kwargs[f.name] = Angle.from_primitive(v[f.name])
            elif ft is Time:
                kwargs[f.name] = Time.from_primitive(v[f.name])
            elif ft is GDD:
                kwargs[f.name] = GDD.from_primitive(v[f.name])
            elif ft in (float, int):
                kwargs[f.name] = ft(v[f.name])
            else:
                kwargs[f.name] = v[f.name]
        return cls(**kwargs)

    # --- type conversions -------------------------------------------------
    _TO_FLOAT: ClassVar[dict[type[Any], Callable[[Any], float]]] = {
        Length: lambda l: float(l.value(Prefix.NANO)),
        Angle: lambda a: float(a.Rad),
        Time: lambda t: float(t.value(Prefix.PICO)),
        GDD: lambda g: float(g.value(Prefix.PICO)),
        float: float,
    }
    _FROM_FLOAT: ClassVar[dict[type[Any], Callable[[float], Any]]] = {
        Length: lambda v: Length(float(v), Prefix.NANO),
        Angle: lambda v: Angle(float(v)),
        Time: lambda v: Time(float(v), Prefix.PICO),
        GDD: lambda v: GDD(float(v), Prefix.PICO),
        float: float,
    }

    @classmethod
    def _to_float_conv(cls, field_type: type[Any]) -> Callable[[Any], float]:
        return cls._TO_FLOAT.get(field_type, lambda v: v)

    @classmethod
    def _from_float_conv(cls, field_type: type[Any]) -> Callable[[float], Any]:
        return cls._FROM_FLOAT.get(field_type, lambda v: v)


# ---------------------------------------------------------------------------
# Config: holds run/config-level state, decides full vs phase-only fitting.
# ---------------------------------------------------------------------------
@dataclass
class StabilizationConfig(PrimitiveSerde):
    params: SpectralFitParams
    wavelength_range: Range[Length] = Range(
        Length(796, Prefix.NANO), Length(810, Prefix.NANO)
    )
    residuals_threshold: float = 15
    avg_spectra: int = 10
    fit_all_params: bool = True
    set_phase: Angle = Angle(0)

    def fit(self, wavelengths_nm: np.ndarray,
            intensities: np.ndarray) -> SpectralFitParams:
        if self.fit_all_params:
            return self.params.fit_full(wavelengths_nm, intensities)
        return self.params.fit_phase_only(wavelengths_nm, intensities)

    def copy_from(self, other: "StabilizationConfig") -> None:
        self.params.copy_from(other.params)
        for f in fields(self):
            if f.name != "params":
                setattr(self, f.name, getattr(other, f.name))

    def to_primitive(self) -> Primitive:
        return {
            "params": self.params.to_primitive(),
            "wavelength_range": {
                "min": self.wavelength_range.min.to_primitive(),
                "max": self.wavelength_range.max.to_primitive(),
            },
            "residuals_threshold": self.residuals_threshold,
            "avg_spectra": self.avg_spectra,
            "fit_all_params": self.fit_all_params,
            "set_phase": self.set_phase.to_primitive(),
        }

    @classmethod
    def from_primitive(cls, v: Primitive) -> "StabilizationConfig":
        wl_range = v["wavelength_range"]
        return cls(
            params=SpectralFitParams.from_primitive(v["params"]),
            wavelength_range=Range(
                Length.from_primitive(wl_range["min"]),
                Length.from_primitive(wl_range["max"]),
            ),
            residuals_threshold=float(v["residuals_threshold"]),
            avg_spectra=int(v["avg_spectra"]),
            fit_all_params=bool(v["fit_all_params"]),
            set_phase=Angle.from_primitive(v["set_phase"]),
        )

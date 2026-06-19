from __future__ import annotations

from dataclasses import dataclass, fields
import inspect
from typing import Any, Callable, ClassVar, Sequence, TypeVar, get_type_hints

import lmfit
import numpy as np

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde
from base_core.math.functions import cfg_spectrum, cfCFG_spectrum
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length

T = TypeVar("T", bound="SpectralFitParams")


def _first_param(func: Callable) -> str:
    return next(iter(inspect.signature(func).parameters))


@dataclass
class SpectralFitParams:
    """Fit parameters matching the cfg_spectrum / cfCFG_spectrum signatures."""

    lambda0: Length = Length(802.38, Prefix.NANO)
    delta_lambda_fwhm: Length = Length(7.4728, Prefix.NANO)
    A: float = 1.0
    dphi0: Angle = Angle(-3.34)
    delta_z: float = 0.09       # arm path-length difference [mm]; tau = delta_z / c
    delta_beta: float = 0.0     # GDD difference [ps²]; 0 when has_acceleration=False
    offset: float = 0.3338
    residual: float = 0.0

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
    def from_fit_result(cls: type[T], base: T, result: lmfit.model.ModelResult) -> T:
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
    def mean(cls: type[T], items: Sequence[T]) -> T:
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
            if f.name not in ("wavelength_range", "avg_spectra", "residuals_threshold", "has_acceleration"):
                setattr(self, f.name, getattr(other, f.name))

    _TO_FLOAT: ClassVar[dict[type[Any], Callable[[Any], float]]] = {
        Length: lambda l: float(l.value(Prefix.NANO)),
        Angle: lambda a: float(a.Rad),
        float: float,
    }
    _FROM_FLOAT: ClassVar[dict[type[Any], Callable[[float], Any]]] = {
        Length: lambda v: Length(float(v), Prefix.NANO),
        Angle: lambda v: Angle(float(v)),
        float: float,
    }

    @classmethod
    def _to_float_conv(cls, field_type: type[Any]) -> Callable[[Any], float]:
        return cls._TO_FLOAT.get(field_type, lambda v: v)

    @classmethod
    def _from_float_conv(cls, field_type: type[Any]) -> Callable[[float], Any]:
        return cls._FROM_FLOAT.get(field_type, lambda v: v)


@dataclass
class StabilizationConfig(SpectralFitParams, PrimitiveSerde):
    wavelength_range: Range[Length] = Range(Length(796, Prefix.NANO), Length(810, Prefix.NANO))
    residuals_threshold: float = 15
    avg_spectra: int = 10
    has_acceleration: bool = True

    def _fit_func(self) -> Callable:
        return cfg_spectrum if self.has_acceleration else cfCFG_spectrum

    def fit_full(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> SpectralFitParams:
        """Multi-parameter fit using current values as initial guesses. Fixes envelope params."""
        func = self._fit_func()
        first_arg = _first_param(func)
        model = lmfit.Model(func, independent_vars=[first_arg])
        params = model.make_params(**self.to_fit_kwargs(func))
        params["lambda0"].set(vary=False)
        params["delta_lambda_fwhm"].set(vary=False)
        # Seed amplitude and offset from data (intensity arrives pre-normalized to [0, 1]
        # by PhaseTracker._prepare, so the config's stored A/offset don't apply directly).
        params["A"].set(value=float(intensities.max() - intensities.min()), min=0.0)
        params["offset"].set(value=float(intensities.min()))
        result = model.fit(
            intensities, params=params,
            **{first_arg: wavelengths_nm}, max_nfev=1_000_000,
        )
        return SpectralFitParams.from_fit_result(self, result)

    def fit_phase_only(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> SpectralFitParams:
        """Phase-only fit — all parameters fixed except dphi0."""
        func = self._fit_func()
        first_arg = _first_param(func)
        model = lmfit.Model(func, independent_vars=[first_arg])
        params = model.make_params(**self.to_fit_kwargs(func))
        for name, par in params.items():
            par.vary = (name == "dphi0")
        result = model.fit(intensities, params=params, **{first_arg: wavelengths_nm})
        return SpectralFitParams.from_fit_result(self, result)

    def to_primitive(self) -> Primitive:
        return {
            "lambda0": self.lambda0.to_primitive(),
            "delta_lambda_fwhm": self.delta_lambda_fwhm.to_primitive(),
            "A": self.A,
            "dphi0": self.dphi0.to_primitive(),
            "delta_z": self.delta_z,
            "delta_beta": self.delta_beta,
            "offset": self.offset,
            "residual": self.residual,
            "wavelength_range": {
                "min": self.wavelength_range.min.to_primitive(),
                "max": self.wavelength_range.max.to_primitive(),
            },
            "residuals_threshold": self.residuals_threshold,
            "avg_spectra": self.avg_spectra,
            "has_acceleration": self.has_acceleration,
        }

    @classmethod
    def from_primitive(cls, v: Primitive) -> "StabilizationConfig":
        wl_range = v["wavelength_range"]
        return cls(
            lambda0=Length.from_primitive(v["lambda0"]),
            delta_lambda_fwhm=Length.from_primitive(v["delta_lambda_fwhm"]),
            A=float(v["A"]),
            dphi0=Angle.from_primitive(v["dphi0"]),
            delta_z=float(v["delta_z"]),
            delta_beta=float(v["delta_beta"]),
            offset=float(v["offset"]),
            residual=float(v["residual"]),
            wavelength_range=Range(
                Length.from_primitive(wl_range["min"]),
                Length.from_primitive(wl_range["max"]),
            ),
            residuals_threshold=float(v["residuals_threshold"]),
            avg_spectra=int(v["avg_spectra"]),
            has_acceleration=bool(v["has_acceleration"]),
        )

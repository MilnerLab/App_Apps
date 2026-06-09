from __future__ import annotations

from dataclasses import dataclass, fields
import inspect
from typing import Any, Callable, ClassVar, Sequence, TypeVar, get_type_hints

import lmfit
import numpy as np

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length

T = TypeVar("T", bound="FitParameter1")


@dataclass
class FitParameter:
    carrier_wavelength: Length = Length(802.38, Prefix.NANO)
    starting_wavelength: Length = Length(808.352, Prefix.NANO)
    bandwidth: Length = Length(7.4728, Prefix.NANO)
    baseline: float = 0.3338
    phase: Angle = Angle(-3.34)
    acceleration: float = 0.0979 * np.pi * 2
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
            raise ValueError("At least one FitParameter is required.")
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

    def copy_from(self, other: "FitParameter") -> None:
        for f in fields(self):
            if f.name not in ("wavelength_range", "avg_spectra", "residuals_threshold"):
                setattr(self, f.name, getattr(other, f.name))

    _TO_FLOAT: ClassVar[dict[type[Any], Callable[[Any], float]]] = {
        Length: lambda l: l.value(Prefix.NANO),
        Angle: lambda a: a.Rad,
        float: float,
    }
    _FROM_FLOAT: ClassVar[dict[type[Any], Callable[[float], Any]]] = {
        Length: lambda v: Length(v, Prefix.NANO),
        Angle: lambda v: Angle(v),
        float: float,
    }

    @classmethod
    def _to_float_conv(cls, field_type: type[Any]) -> Callable[[Any], float]:
        return cls._TO_FLOAT.get(field_type, lambda v: v)

    @classmethod
    def _from_float_conv(cls, field_type: type[Any]) -> Callable[[float], Any]:
        return cls._FROM_FLOAT.get(field_type, lambda v: v)


@dataclass
class FitParameter1:
    central_wavelength: Length = Length(802.38, Prefix.NANO)
    bandwidth: Length = Length(7.4728, Prefix.NANO)
    baseline: float = 0.3338
    phase: Angle = Angle(-3.34)
    tau_ps: float = 0.30
    a_R_THz_per_ps: float = 0.60
    a_L_THz_per_ps: float = 0.60
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
            raise ValueError("At least one FitParameter is required.")
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

    def copy_from(self, other: "FitParameter") -> None:
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
class AnalysisConfig(FitParameter1, PrimitiveSerde):
    wavelength_range: Range[Length] = Range(Length(796, Prefix.NANO), Length(810, Prefix.NANO))
    residuals_threshold: float = 15
    avg_spectra: int = 10
    has_acceleration: bool = True

    def to_primitive(self) -> Primitive:
        return {
            "central_wavelength": self.central_wavelength.to_primitive(),
            "bandwidth": self.bandwidth.to_primitive(),
            "baseline": self.baseline,
            "phase": self.phase.to_primitive(),
            "tau_ps": self.tau_ps,
            "a_R_THz_per_ps": self.a_R_THz_per_ps,
            "a_L_THz_per_ps": self.a_L_THz_per_ps,
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
    def from_primitive(cls, v: Primitive) -> "AnalysisConfig":
        wl_range = v["wavelength_range"]
        return cls(
            central_wavelength=Length.from_primitive(v["central_wavelength"]),
            bandwidth=Length.from_primitive(v["bandwidth"]),
            baseline=float(v["baseline"]),
            phase=Angle.from_primitive(v["phase"]),
            tau_ps=float(v["tau_ps"]),
            a_R_THz_per_ps=float(v["a_R_THz_per_ps"]),
            a_L_THz_per_ps=float(v["a_L_THz_per_ps"]),
            residual=float(v["residual"]),
            wavelength_range=Range(
                Length.from_primitive(wl_range["min"]),
                Length.from_primitive(wl_range["max"]),
            ),
            residuals_threshold=float(v["residuals_threshold"]),
            avg_spectra=int(v["avg_spectra"]),
            has_acceleration=bool(v["has_acceleration"]),
        )

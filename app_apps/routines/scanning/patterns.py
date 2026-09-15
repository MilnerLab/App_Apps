"""Where the points of a scan go — the "what kind of scan" layer.

Pure functions over numbers. No hardware, no IPC, no bus, no threads. That is what makes
this the one part of a scan cheap enough to unit-test decisively, and it is why the
positions are produced **all at once, up front** rather than one at a time during the run:
a scan that invents its next position while the stages are hot cannot be checked against
the soft limits before it commits, and refusing an illegal run before anything moves is
the whole safety story.

A pattern therefore answers one question — *give me every position, in order* — and the
caller validates the answer before a single move is commanded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence


class ScanPatternError(ValueError):
    """A pattern cannot produce a valid list of positions. Raised before any motion."""


class ScanPattern(Protocol):
    """Anything that can say where a scan's points go."""

    def positions(self) -> tuple[float, ...]:
        """Every position, in the order they will be visited."""

    def describe(self) -> str:
        """One line naming the distribution and its parameters, for provenance."""


def expand_range(
    start: float, stop: float, step: float, *, name: str, include_endpoint: bool = False
) -> tuple[float, ...]:
    """Inclusive range from ``start`` to ``stop`` in increments of ``step``.

    ``step`` is unsigned; direction comes from ``stop - start``. ``stop`` is
    included when the step divides the interval to within a relative tolerance —
    without that, ``0..10`` by ``0.1`` would silently drop its endpoint to float
    error. Positions are computed as ``start + i*step`` rather than accumulated,
    so error does not grow along the scan.

    With ``include_endpoint`` the true ``stop`` is *always* the last position, even
    when the step does not divide the interval — it is appended as a final, shorter
    step. Used for a sweep whose step varies between runs of the scan, so that every
    run ends on one common right edge that analysis can interpolate onto and truncate
    to. Left off for a physical outer axis, where a stray short final step is undesirable.
    """
    if step <= 0:
        raise ScanPatternError(f"{name}: step must be > 0, got {step}")

    span = stop - start
    if span == 0.0:
        return (start,)

    n_steps = abs(span) / step
    # Snap to an integer count when we are within a hair of one, so the endpoint
    # survives; otherwise truncate, leaving the last point short of `stop`.
    n_int = round(n_steps)
    count = n_int if math.isclose(n_steps, n_int, rel_tol=1e-9, abs_tol=1e-9) else int(n_steps)

    direction = math.copysign(1.0, span)
    positions = [start + direction * step * i for i in range(count + 1)]
    # Append the exact endpoint when the truncating step fell short of it (a genuine
    # miss, not float noise the snap above already absorbed).
    if include_endpoint and not math.isclose(positions[-1], stop, rel_tol=1e-9, abs_tol=1e-9):
        positions.append(stop)
    return tuple(positions)


@dataclass(frozen=True)
class UniformPattern:
    """Equally spaced points from ``start`` to ``stop``.

    The default distribution, and the only one any scan in this repo has needed so far.
    """

    start: float
    stop: float
    step: float
    name: str = "axis"
    include_endpoint: bool = False

    def positions(self) -> tuple[float, ...]:
        return expand_range(
            self.start, self.stop, self.step,
            name=self.name, include_endpoint=self.include_endpoint,
        )

    def describe(self) -> str:
        return (f"uniform {self.start:g}..{self.stop:g} step {self.step:g}"
                f"{' (endpoint forced)' if self.include_endpoint else ''}")


@dataclass(frozen=True)
class ExplicitPattern:
    """Exactly these positions, in exactly this order.

    For a scan whose points come from somewhere else entirely — a previous run's fit, a
    file of interesting positions, a hand-written list.
    """

    values: Sequence[float]
    name: str = "axis"

    def positions(self) -> tuple[float, ...]:
        if not self.values:
            raise ScanPatternError(f"{self.name}: an explicit pattern needs at least one position")
        return tuple(float(v) for v in self.values)

    def describe(self) -> str:
        return f"explicit, {len(self.values)} position(s)"


@dataclass(frozen=True)
class StepRulePattern:
    """Points whose spacing is decided per-scan by a rule rather than fixed.

    ``step_fn`` is called once, with no arguments, and returns the step this particular
    traversal should use. That is the seam for a spacing that depends on where the *other*
    axes are — a Nyquist-matched step that must be finer where the signal oscillates
    faster, say. The rule itself stays with the physics that motivates it; only the
    plumbing is here.

    Deliberately not a per-point rule. Spacing that changes *within* a sweep would have to
    be evaluated while the stage is moving, which puts the positions beyond reach of the
    pre-flight limit check.
    """

    start: float
    stop: float
    step_fn: Callable[[], float]
    name: str = "axis"
    include_endpoint: bool = True
    #: Memo for :meth:`step`. Not part of the pattern's identity.
    _step: list[float] = field(default_factory=list, compare=False, repr=False)

    def step(self) -> float:
        """The step this pattern uses, asking the rule exactly once.

        Memoised rather than re-evaluated because the positions and the description of a
        pattern must not be able to disagree: a rule that is consulted twice can answer
        differently, and a run would then record a spacing it did not use.
        """
        if not self._step:
            self._step.append(self.step_fn())
        return self._step[0]

    def positions(self) -> tuple[float, ...]:
        return expand_range(
            self.start, self.stop, self.step(),
            name=self.name, include_endpoint=self.include_endpoint,
        )

    def describe(self) -> str:
        return f"step-rule {self.start:g}..{self.stop:g} step {self.step():g}"

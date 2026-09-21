"""``TranslationStageScan`` — walk one translation stage through a list of positions.

The handshake with the routine that owns it is an iterator, because the loop that a scan
already wants to write *is* the protocol::

    for pt in scan:                       # gates, moves, blocks until settled, yields
        value = acquire(pt.commanded)     # the routine's business, not the scan's
        rows.append((pt.commanded, value))

Each ``next()`` gives the caller a stationary stage at a known position. Coming back round
for the next iteration is the caller saying *I am finished with that position* — there is
no separate "done" call to forget. Falling out of the loop means every wanted position was
visited. Nothing else is inferred.

**This class holds no run-control state.** It does not know what a pause is, what an abort
is, or that an operator exists. Those belong to the routine that owns the run, which passes
:meth:`__init__`'s ``before_move`` — typically its own ``checkpoint`` — and lets whatever
that raises propagate. The reason is not purity: it is that pause and abort are properties
of *the run*, and a run may own several of these (one per axis, nested). Duplicating the
policy into each one is how the copies come to disagree.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

from app_apps.routines.scanning.translation_stage import TranslationStage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanPoint:
    """One position of a scan, as handed to the caller with the stage stationary on it."""

    #: 0-based index within this scan.
    index: int
    #: How many positions this scan has in total.
    n: int
    #: The position as the pattern produced it, before any offset.
    base: float
    #: What the stage was actually commanded to: ``base + offset``.
    commanded: float

    @property
    def is_last(self) -> bool:
        return self.index == self.n - 1


class TranslationStageScan:
    """A list of positions plus the stage that will visit them, one per iteration."""

    def __init__(
        self,
        stage: TranslationStage,
        positions: Sequence[float],
        *,
        offset: float = 0.0,
        before_move: Callable[[], None] | None = None,
        on_settled: Callable[[ScanPoint], None] | None = None,
    ) -> None:
        """
        ``positions`` is the complete list, produced by a pattern and — for anything that
        drives real hardware — already validated against the stage's soft limits. This
        class does not generate positions and cannot: a position invented mid-run is a
        position nothing checked before the stage was hot.

        ``offset`` is added to every position to get the commanded one. It exists for the
        common case of a sweep whose axis is defined relative to something else that moved
        (an overlap that tracks another stage), so the pattern stays in the frame the
        physics is written in while the stage is commanded in its own.

        ``before_move`` is called before each move, and anything it raises propagates out
        of the iteration — this is where the owning routine puts its pause/abort gate.
        ``on_settled`` is called once the stage is stationary at the new position, with
        the point that was just reached.
        """
        self._stage = stage
        self._positions = tuple(float(p) for p in positions)
        self._offset = offset
        self._before_move = before_move
        self._on_settled = on_settled
        self._index = 0

    # -- what this scan is ------------------------------------------------

    @property
    def stage(self) -> TranslationStage:
        return self._stage

    @property
    def n(self) -> int:
        return len(self._positions)

    @property
    def positions(self) -> tuple[float, ...]:
        """The base positions, as given."""
        return self._positions

    @property
    def commanded(self) -> tuple[float, ...]:
        """The positions the stage will actually be sent to."""
        return tuple(p + self._offset for p in self._positions)

    @property
    def index(self) -> int:
        """How many positions have been visited so far."""
        return self._index

    @property
    def centre(self) -> float:
        """The commanded position at the middle of the scan.

        Offered because the middle of a sweep is where a correlation peak sits, and so
        the only position at which "maximise the signal" is a meaningful instruction to an
        operator. Parking at the start of the sweep instead — where the stage happens to
        be — has them optimising on the baseline tail.
        """
        if not self._positions:
            raise ValueError(f"{self._stage.role}: an empty scan has no centre")
        return self._positions[len(self._positions) // 2] + self._offset

    # -- the walk ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._positions)

    def __iter__(self) -> Iterator[ScanPoint]:
        """Restart the walk. One scan object, one traversal at a time."""
        self._index = 0
        return self

    def __next__(self) -> ScanPoint:
        # Gate first, so a routine that wants to stop is never made to command one more
        # move before it can. This is also the only place the caller's exception can
        # leave the scan, which is what makes "stopped early" distinguishable from
        # "finished" without a flag to check.
        if self._before_move is not None:
            self._before_move()

        if self._index >= len(self._positions):
            raise StopIteration

        base = self._positions[self._index]
        point = ScanPoint(
            index=self._index,
            n=len(self._positions),
            base=base,
            commanded=base + self._offset,
        )
        self._stage.move_to(point.commanded)
        self._index += 1

        # After the move returned, so the stage is stationary and stays that way until the
        # next iteration commands it again.
        if self._on_settled is not None:
            self._on_settled(point)
        return point

    def __repr__(self) -> str:
        return (f"TranslationStageScan(role={self._stage.role!r}, n={self.n}, "
                f"offset={self._offset:g})")

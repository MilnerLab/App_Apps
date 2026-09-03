"""Mutable, form-editable settings for the Spectrum Soak panel.

Same role ``XcorrSettings`` plays for XCORR and ``CfgRange`` for the CFG form: the
Qt ``ConfigForm`` binds by ``setattr``, so it needs a plain mutable object with plain
numbers in the routine's own units. There is no frozen twin to freeze into here --
the recorder takes its two numbers as arguments -- so this is the whole configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SoakSettings:
    """The knobs behind Routines -> Spectrum Soak.

    ``period_s`` is a *decimation* of the free-running stream, not a trigger: the
    spectrometer integrates at its own rate and nothing here can ask it for a frame.
    0 records everything it delivers.
    """

    duration_s: float = 300.0
    period_s: float = 2.0
    #: Recorded for the comparison this panel exists to support: one file with the loop
    #: off, one with it on. The panel does NOT start or stop the loop -- that is the
    #: Phase Control panel's job, and a recorder that silently reconfigured the loop
    #: would be changing the thing it is supposed to be measuring.
    out_dir: Path = Path.cwd() / "soak_runs"
    tag: str = ""

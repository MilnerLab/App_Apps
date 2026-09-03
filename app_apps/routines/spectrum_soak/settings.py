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
    """The knobs behind the Spectrum Soak panel.

    There is no period: every spectrum the device delivers is recorded. Decimation
    belongs at read time, where it can be undone -- see ``loader.load_soak``.
    """

    duration_s: float = 300.0
    #: Recorded for the comparison this panel exists to support: one file with the loop
    #: off, one with it on. The panel does NOT start or stop the loop -- that is the
    #: Phase Control panel's job, and a recorder that silently reconfigured the loop
    #: would be changing the thing it is supposed to be measuring.
    #:
    #: The lab share, so the two arms land next to each other and survive this machine.
    #: Created on first write; if Z: is not mounted the run fails at Start with the path
    #: in the message, which is the right time to find out.
    out_dir: Path = Path(r"Z:\Droplets\usCFG_characterization\phase_soak")
    tag: str = ""

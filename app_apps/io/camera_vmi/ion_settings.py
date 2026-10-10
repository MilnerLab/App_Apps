from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from base_core.lab_specifics.c2t.config import IonDataAnalysisConfig
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle, Point, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length

from app_apps.analysis.ion_detection import ContourConfig


@dataclass
class IonSettings:
    """Mutable, form-editable settings behind the ion view.

    IonDataAnalysisConfig is frozen (and slotted), so a DirtyFormWidget -- which writes
    fields back with setattr -- cannot edit it. This holds the same values flat and
    builds the frozen config in snapshot(). The detection thresholds stay in their own
    ContourConfig (already a plain mutable dataclass), edited in place.
    """

    contour: ContourConfig = field(default_factory=ContourConfig)

    center_x: float = 0.0
    center_y: float = 0.0
    angle: Angle = field(default_factory=lambda: Angle(0, AngleUnit.DEG))
    transform_parameter: float = 1.0
    analysis_zone: Range[float] = field(default_factory=lambda: Range(0.0, 500.0))
    # Not shown or edited: a live frame has no stage position, but IonDataAnalysisConfig
    # requires the field.
    delay_center: Length = field(default_factory=lambda: Length(0, Prefix.MILLI))

    rolling_frames: int = 50

    def snapshot(self) -> tuple[ContourConfig, IonDataAnalysisConfig]:
        """Independent copies for the detector thread, so later form edits can't race it."""
        return (
            dataclasses.replace(self.contour),
            IonDataAnalysisConfig(
                delay_center=self.delay_center,
                center=Point(self.center_x, self.center_y),
                angle=self.angle,
                analysis_zone=self.analysis_zone,
                transform_parameter=self.transform_parameter,
            ),
        )

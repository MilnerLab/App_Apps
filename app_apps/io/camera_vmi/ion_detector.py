from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from base_core.lab_specifics.base_models import IonData, Measurement
from base_core.lab_specifics.c2t.config import IonDataAnalysisConfig
from base_core.math.models import MarkedPoints

from app_apps.analysis.ion_detection import ContourConfig, threshold_and_extract_hits

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IonFrameResult:
    item_id: int
    n_detected: int        # hits found in the frame, before the analysis-zone cut
    points: MarkedPoints   # transformed (centered, scaled, rotated) and zone-filtered


def detect_ions(
    frame: np.ndarray,
    item_id: int,
    contour: ContourConfig,
    analysis: IonDataAnalysisConfig,
) -> IonFrameResult:
    _, _, hits = threshold_and_extract_hits(frame, contour)
    n = len(hits)
    raw = MarkedPoints(
        np.fromiter((h.cx for h in hits), dtype=np.float64, count=n),
        np.fromiter((h.cy for h in hits), dtype=np.float64, count=n),
        np.full(n, item_id, dtype=np.int64),
    )
    # The same transform C2TScanData.from_raw applies, via IonData. A live frame has no
    # stage position; delay_center only fills the field and is never used here.
    points = IonData(
        id=item_id,
        ions_per_frame=float(n),
        stage_position=analysis.delay_center,
        points=raw,
    ).get_points_after_config(analysis)
    return IonFrameResult(item_id=item_id, n_detected=n, points=points)


class BatchIons:
    """Collects consecutive frames' in-zone ions until target_ions, then yields <cos^2 theta>.

    One avg_c2t over the concatenated batch rather than an average of per-frame values:
    the same cost, but every ion weighs the same and the SEM comes out right. Frames
    are kept whole, so a batch can end slightly above the target.
    """

    def __init__(self, target_ions: int) -> None:
        self.target_ions = max(1, target_ions)
        self._frames: list[MarkedPoints] = []
        self._n = 0

    @property
    def n_ions(self) -> int:
        return self._n

    @property
    def frames(self) -> tuple[MarkedPoints, ...]:
        """The frames of the batch in progress."""
        return tuple(self._frames)

    def add(self, points: MarkedPoints) -> tuple[Measurement, MarkedPoints] | None:
        """Append one frame's points; on reaching the target return (c2t, batch) and reset."""
        if not len(points):
            return None
        # A list, concatenated once: appending to one array would copy it every frame.
        self._frames.append(points)
        self._n += len(points)
        if self._n < self.target_ions:
            return None
        batch = MarkedPoints(
            np.concatenate([p.x for p in self._frames]),
            np.concatenate([p.y for p in self._frames]),
            np.concatenate([p.marker for p in self._frames]),
        )
        self.reset()
        return IonData.avg_c2t(batch), batch

    def reset(self) -> None:
        self._frames = []
        self._n = 0


class IonDetector:
    """Runs detect_ions on its own thread, always on the latest submitted frame.

    Latest-only rather than a queue: if detection falls behind the camera, frames that
    arrived in the meantime are dropped instead of building a backlog, so what is shown
    is never older than one detection.
    """

    def __init__(
        self,
        params: tuple[ContourConfig, IonDataAnalysisConfig],
        on_result: Callable[[IonFrameResult], None],
    ) -> None:
        self._params = params
        self._on_result = on_result
        self._lock = threading.Lock()
        self._pending: tuple[np.ndarray, int] | None = None
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ion_detector")
        self._thread.start()

    def set_params(self, params: tuple[ContourConfig, IonDataAnalysisConfig]) -> None:
        # One reference swap: the loop reads the pair as a unit, never half old/half new.
        self._params = params

    def submit(self, frame: np.ndarray, item_id: int) -> None:
        with self._lock:
            self._pending = (frame, item_id)
            self._wake.set()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                job, self._pending = self._pending, None
                self._wake.clear()
            if self._stopped:
                return
            if job is None:
                continue
            frame, item_id = job
            contour, analysis = self._params
            try:
                self._on_result(detect_ions(frame, item_id, contour, analysis))
            except Exception:
                log.exception("IonDetector: detection failed on frame %d", item_id)

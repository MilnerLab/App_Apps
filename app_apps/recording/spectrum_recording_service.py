"""``SpectrumRecordingService`` — a spectrometer recording in a file of its own.

The Record panel and routines call this. Anything that wants the spectra inside a bigger
file (a scan) builds a :class:`SpectrumRecorder` itself and hands it a group and a lock.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

import h5py

from base_core.framework.events.event_bus import EventBus

from app_apps.recording.events import SpectrumRecordingStateChanged
from app_apps.recording.spectrum_recorder import SpectrumRecorder

log = logging.getLogger(__name__)

#: Everything outside this set is collapsed to "_" in a file name.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def default_recording_path(out_dir: Path, name: str = "", when: datetime | None = None) -> Path:
    """``<out_dir>/SPEC_<name>_YYYYmmdd_HHMMSS.h5``; the name part is omitted when blank."""
    when = when or datetime.now()
    tag = _SAFE_NAME.sub("_", name.strip()).strip("_")
    stem = f"SPEC_{tag}_{when:%Y%m%d_%H%M%S}" if tag else f"SPEC_{when:%Y%m%d_%H%M%S}"
    return Path(out_dir) / f"{stem}.h5"


class SpectrumRecordingService:
    """Owns at most one standalone recording at a time."""

    def __init__(self, bus: EventBus, make_recorder: Callable[[], SpectrumRecorder]) -> None:
        self._bus = bus
        self._make_recorder = make_recorder
        self._recorder: SpectrumRecorder | None = None
        self._file: h5py.File | None = None
        self._path: Path | None = None

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None

    @property
    def path(self) -> Path | None:
        """The file being written, or the last one written once stopped."""
        return self._path

    @property
    def recorder(self) -> SpectrumRecorder | None:
        """The active recorder, for its counters."""
        return self._recorder

    def start(self, out_dir: Path, name: str = "") -> Path:
        if self._recorder is not None:
            raise RuntimeError(f"already recording to {self._path}")
        path = default_recording_path(out_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        f = h5py.File(path, "x")
        recorder = self._make_recorder()
        try:
            recorder.start(f, threading.RLock())
        except Exception:
            f.close()
            raise
        self._file, self._recorder, self._path = f, recorder, path
        log.info("Spectrum recording started: %s", path)
        self._bus.publish(SpectrumRecordingStateChanged())
        return path

    def stop(self) -> None:
        recorder, self._recorder = self._recorder, None
        f, self._file = self._file, None
        if recorder is None:
            return
        try:
            recorder.stop()
        finally:
            if f is not None:
                f.close()
        log.info("Spectrum recording stopped: %s", self._path)
        self._bus.publish(SpectrumRecordingStateChanged())

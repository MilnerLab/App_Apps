from __future__ import annotations

import sys
from pathlib import Path

# Add App_Apps root to sys.path so app_apps.* is importable in the subprocess.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from base_core.framework.subprocess.subprocess_app import SubprocessApp
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import base_registry
from app_apps.analysis.phase_control.subprocess.phase_tracking_worker import PhaseTrackingWorker
from app_apps.analysis.phase_control.subprocess.envelope_worker import EnvelopeWorker
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetEnvelopeConfig,
    SetPaused,
)

if __name__ == "__main__":
    app = SubprocessApp(
        base_registry().extend(ConfigSynced, CorrectionAvailable, Reset, SetStabilizationConfig, SetEnvelopeConfig, SetPaused),
        source="phase_control",
    )
    app.add_worker(PhaseTrackingWorker())
    app.add_worker(EnvelopeWorker())
    app.run()

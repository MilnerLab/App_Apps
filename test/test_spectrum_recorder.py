"""Unit tests for the standalone spectrometer recorder (``app_apps.recording``).

What has to hold, none of it needing hardware:

* **Serde round-trip.** Metadata is stored through ``write_primitive`` and must come back
  from ``read_primitive`` as the same dataclass — nested configs and ``Length`` included.
* **Metadata triggers.** One entry at start, one per grating/delay position report, one per
  real stabilization on/off flip (BUSY is not a flip), one per spectrometer config apply.
* **The ack contract.** Every ``SpectrumAvailable`` is acked — accepted, queue full, or
  read error — or every other spectrum consumer stalls.
* **Embedding.** The recorder writes under any group, sharing a lock with another writer.

Hand-rolled runner, like the rest of test/. Run directly —

    App_Apps/.venv/bin/python App_Apps/test/test_spectrum_recorder.py
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import h5py  # noqa: E402

from base_core.framework.events.event_bus import EventBus  # noqa: E402
from base_core.framework.serialization.h5_utils import read_primitive, write_primitive  # noqa: E402
from base_core.ipc.worker_handle import WorkerStatus  # noqa: E402
from base_core.quantities.enums import Prefix  # noqa: E402
from base_core.quantities.models import Length  # noqa: E402
from spm_002.config import SpectrometerConfig  # noqa: E402

from app_apps.analysis.phase_control.events import (  # noqa: E402
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)
from base_core.math.models import Angle  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (  # noqa: E402
    FringeFitParams,
    StabilizationConfig,
)
from app_apps.io.control_readout.mfa_cc.events import NewMfaccPosition  # noqa: E402
from app_apps.io.control_readout.uts150cc.events import NewUts150ccPosition  # noqa: E402
from app_apps.io.spectrometer.events import (  # noqa: E402
    SpectrometerConfigChanged,
    SpectrumAck,
    SpectrumAvailable,
)
from app_apps.recording import spectrum_recorder as recorder_mod  # noqa: E402
from app_apps.recording.models import SpectrumRecordingInfo, SpectrumRecordingMetadata  # noqa: E402
from app_apps.recording.spectrum_recorder import SpectrumRecorder, timestamp_key  # noqa: E402
from app_apps.recording.spectrum_recording_service import SpectrumRecordingService  # noqa: E402

N_PIXELS = 16


# --- fakes ----------------------------------------------------------------

class FakeBuffer:
    def __init__(self) -> None:
        self.wl = np.linspace(400.0, 800.0, N_PIXELS)
        self.fail = False

    def wavelengths(self, slot: int) -> np.ndarray:
        return self.wl.copy()

    def intensities(self, slot: int) -> np.ndarray:
        if self.fail:
            raise RuntimeError("shm gone")
        return np.full(N_PIXELS, float(slot + 1))


class FakeSpectrometer:
    def __init__(self) -> None:
        self.buffer = FakeBuffer()
        self.config = SpectrometerConfig()
        self.registered: set[str] = set()

    def register_consumer(self, cid: str) -> None:
        self.registered.add(cid)

    def unregister_consumer(self, cid: str) -> None:
        self.registered.discard(cid)


class FakeStabilization:
    def __init__(self, state: WorkerStatus = WorkerStatus.NEW) -> None:
        self.state = state
        self.config = StabilizationConfig(params=FringeFitParams())


class FakeStage:
    def __init__(self, position: float | None, state: WorkerStatus = WorkerStatus.NEW) -> None:
        self.last_position = position
        self.state = state
        self.n_get_position = 0

    def get_position(self) -> None:
        self.n_get_position += 1


def make(bus=None, *, grating=-30.0, delay=18.0, stab_state=WorkerStatus.NEW):
    bus = bus or EventBus()
    spec, stab = FakeSpectrometer(), FakeStabilization(stab_state)
    g, d = FakeStage(grating), FakeStage(delay)
    rec = SpectrumRecorder(bus, spec, stab, g, d)
    return bus, spec, stab, g, d, rec


def wait_until(cond, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for condition")


def metadata_entries(group: h5py.Group) -> list[SpectrumRecordingMetadata]:
    md = group["metadata"]
    return [read_primitive(md, k, SpectrumRecordingMetadata) for k in sorted(md)]


# --- serde ----------------------------------------------------------------

def test_write_read_primitive_round_trips_nested_config():
    entry = SpectrumRecordingMetadata(
        reason="start",
        grating_position=Length(-30.0, Prefix.MILLI),
        delay_position=None,
        stabilization_running=True,
        spectrometer=SpectrometerConfig(average=7),
        stabilization=StabilizationConfig(params=FringeFitParams()),
    )
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        write_primitive(f, "entry", entry)
        assert f["entry"].attrs["type"].endswith("SpectrumRecordingMetadata")
        back = read_primitive(f, "entry", SpectrumRecordingMetadata)
    assert back == entry, (back, entry)
    assert type(back.grating_position) is Length, type(back.grating_position)
    assert back.delay_position is None
    assert type(back.spectrometer.exposure_time).__name__ == "Time"


# --- recording ------------------------------------------------------------

def test_start_writes_info_and_one_start_entry_with_current_state():
    _bus, _s, _st, _g, _d, rec = make(stab_state=WorkerStatus.RUNNING)
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        wait_until(lambda: rec.n_metadata == 1)
        rec.stop()
        entries = metadata_entries(f)
        info = read_primitive(f, "info", SpectrumRecordingInfo)
    assert len(entries) == 1, entries
    e = entries[0]
    assert e.reason == "start"
    assert abs(e.grating_position.value(Prefix.MILLI) - -30.0) < 1e-12
    assert abs(e.delay_position.value(Prefix.MILLI) - 18.0) < 1e-12
    assert e.stabilization_running is True
    assert info.stopped_utc is not None and info.n_traces == 0


def test_traces_are_written_under_their_timestamp_and_all_acked():
    bus, spec, _st, _g, _d, rec = make()
    acks: list = []
    bus.subscribe(SpectrumAck, acks.append)
    t0 = time.time_ns()
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        assert rec.consumer_id in spec.registered
        for i in range(5):
            bus.publish(SpectrumAvailable(slot=i % 2, item_id=i, timestamp_ns=t0 + i))
        wait_until(lambda: rec.n_traces == 5)
        rec.stop()
        assert rec.consumer_id not in spec.registered
        keys = sorted(f["traces"])
        assert keys == [timestamp_key(t0 + i) for i in range(5)], keys
        assert f["traces"][keys[1]].dtype == np.float32
        assert f["traces"][keys[1]].shape == (N_PIXELS,)
        assert float(f["traces"][keys[1]][0]) == 2.0  # slot 1
        assert np.allclose(f["wavelength_nm"][()], spec.buffer.wl)
    assert [a.item_id for a in acks] == list(range(5)), acks


def test_read_error_is_still_acked_and_counted():
    bus, spec, _st, _g, _d, rec = make()
    acks: list = []
    bus.subscribe(SpectrumAck, acks.append)
    spec.buffer.fail = True
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        bus.publish(SpectrumAvailable(slot=0, item_id=1, timestamp_ns=time.time_ns()))
        rec.stop()
    assert len(acks) == 1 and rec.n_dropped == 1 and rec.n_traces == 0


def test_metadata_triggers():
    bus, _s, stab, _g, _d, rec = make()
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        bus.publish(NewUts150ccPosition(position=-25.0))
        bus.publish(NewMfaccPosition(position=17.5))
        stab.state = WorkerStatus.BUSY
        bus.publish(PhaseTrackingStateChanged())       # BUSY: no entry
        stab.state = WorkerStatus.RUNNING
        bus.publish(PhaseTrackingStateChanged())       # NEW -> RUNNING: entry
        bus.publish(PhaseTrackingStateChanged())       # still RUNNING: no entry
        stab.state = WorkerStatus.PAUSED
        bus.publish(PhaseTrackingStateChanged())       # RUNNING -> PAUSED: entry
        bus.publish(SpectrometerConfigChanged())
        rec.stop()
        entries = metadata_entries(f)
    reasons = [e.reason for e in entries]
    assert reasons == ["start", "grating", "delay", "stabilization", "stabilization",
                       "spectrometer_config"], reasons
    assert abs(entries[1].grating_position.value(Prefix.MILLI) - -25.0) < 1e-12
    assert abs(entries[2].delay_position.value(Prefix.MILLI) - 17.5) < 1e-12
    assert abs(entries[2].grating_position.value(Prefix.MILLI) - -25.0) < 1e-12
    assert [e.stabilization_running for e in entries[3:5]] == [True, False]


def test_stabilization_config_entry_only_when_a_setting_changes():
    bus, _s, stab, _g, _d, rec = make()
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        # What the worker does on every accepted frame: live fit state moves, then syncs.
        for i in range(10):
            stab.config.params.c0 = 0.1 * i
            stab.config.params.phase_ref = 0.2 * i
            bus.publish(StabilizationConfigChanged())
        # The target phase changes (Apply / Capture target): one entry.
        stab.config.set_phase = Angle(1.25)
        bus.publish(StabilizationConfigChanged())
        bus.publish(StabilizationConfigChanged())  # synced again, unchanged: no entry
        rec.stop()
        entries = metadata_entries(f)
    assert [e.reason for e in entries] == ["start", "stabilization_config"], \
        [e.reason for e in entries]
    assert float(entries[0].stabilization.set_phase) == 0.0
    assert abs(float(entries[1].stabilization.set_phase) - 1.25) < 1e-12
    assert abs(entries[1].stabilization.params.phase_ref - 1.8) < 1e-12


def test_metadata_snapshots_config_at_event_time():
    bus, spec, _st, _g, _d, rec = make()
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        lock = threading.RLock()
        with lock:  # hold the writer off so the edit below lands while the entry is queued
            rec.start(f, lock)
            spec.config.average = 42
            bus.publish(SpectrometerConfigChanged())
        rec.stop()
        entries = metadata_entries(f)
    assert [e.spectrometer.average for e in entries] == [5, 42], entries


def test_start_queries_positions_of_running_stages_only():
    _bus, _s, _st, g, d, rec = make()
    g.state = WorkerStatus.RUNNING
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        rec.start(f, threading.RLock())
        rec.stop()
    assert (g.n_get_position, d.n_get_position) == (1, 0)


def test_embeds_in_a_subgroup_of_a_shared_file():
    bus, _s, _st, _g, _d, rec = make()
    stop = threading.Event()
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "scan.h5", "w") as f:
        lock = threading.RLock()
        with lock:
            spectro = f.create_group("spectrometer")
            f.create_group("scan")

        def other_writer() -> None:
            # Paced, like a real scan writer. A tight loop would starve the recorder:
            # Python locks are not fair, so a thread that re-takes the lock the instant
            # it lets go can hold it indefinitely.
            i = 0
            while not stop.wait(0.001):
                with lock:
                    f["scan"].create_dataset(f"row{i:05d}", data=np.arange(100.0))
                i += 1

        t = threading.Thread(target=other_writer)
        t.start()
        try:
            rec.start(spectro, lock)
            t0 = time.time_ns()
            for i in range(50):
                bus.publish(SpectrumAvailable(slot=0, item_id=i, timestamp_ns=t0 + i))
            wait_until(lambda: rec.n_traces == 50)
        finally:
            rec.stop()
            stop.set()
            t.join()
        assert len(f["spectrometer/traces"]) == 50
        assert len(f["scan"]) > 0
        assert "info" in f["spectrometer"] and "metadata" in f["spectrometer"]


def test_full_queue_drops_but_acks_promptly():
    bus, _s, _st, _g, _d, rec = make()
    acks: list = []
    bus.subscribe(SpectrumAck, acks.append)
    old_max = recorder_mod._QUEUE_MAX
    recorder_mod._QUEUE_MAX = 4
    try:
        rec = SpectrumRecorder(bus, rec._spectrometer, rec._stabilization, rec._grating, rec._delay)
    finally:
        recorder_mod._QUEUE_MAX = old_max
    with TemporaryDirectory() as tmp, h5py.File(Path(tmp) / "t.h5", "w") as f:
        lock = threading.RLock()
        with lock:  # writer thread stalled on the lock for the whole burst
            rec.start(f, lock)
            t0 = time.time_ns()
            started = time.monotonic()
            for i in range(20):
                bus.publish(SpectrumAvailable(slot=0, item_id=i, timestamp_ns=t0 + i))
            elapsed = time.monotonic() - started
        rec.stop()
    assert len(acks) == 20, len(acks)
    assert elapsed < 0.5, elapsed
    assert rec.n_dropped > 0 and rec.n_traces + rec.n_dropped == 20, (rec.n_traces, rec.n_dropped)


def test_service_writes_a_standalone_file():
    bus, spec, stab, g, d, _rec = make()
    with TemporaryDirectory() as tmp:
        svc = SpectrumRecordingService(bus, lambda: SpectrumRecorder(bus, spec, stab, g, d))
        path = svc.start(Path(tmp) / "sub", "my run/1")
        assert svc.is_recording and path.name.startswith("SPEC_my_run_1_"), path
        bus.publish(SpectrumAvailable(slot=0, item_id=0, timestamp_ns=time.time_ns()))
        wait_until(lambda: svc.recorder.n_traces == 1)
        svc.stop()
        assert not svc.is_recording
        with h5py.File(path, "r") as f:
            info = read_primitive(f, "info", SpectrumRecordingInfo)
            assert info.n_traces == 1 and info.format_name == "milnerlab-spectrum"
            assert len(f["metadata"]) == 1


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
        else:
            print(f"ok    {t.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

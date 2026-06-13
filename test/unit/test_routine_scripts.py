"""Integration tests for the example routines (R.6).

Runs the actual routine functions against a wired Lab — fake ESP (synchronous MoveComplete)
plus a real in-process scope buffer fed by a background TraceAvailable producer — so the whole
stack (script -> lab verbs -> bridge -> consumer handshake) is exercised, no subprocess.
"""
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.events import TraceAvailable
from app_apps.routines.linear import registry
from app_apps.routines.linear.cancel import CancelToken
from app_apps.routines.linear.config import LabConfig
from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.scripts import probe_scan
from app_apps.routines.linear.scripts.probe_scan import (
    overnight_central_freq_series,
    probe_xcorr_scan,
)
from base_core.framework.events.event_bus import EventBus
from control_readout.esp_301.messages import MoveComplete


class FakeEsp:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.pos: dict[int, float] = {}

    def move_to(self, axis: int, position: float) -> None:
        self.pos[axis] = position
        self._bus.publish(MoveComplete(axis=axis, position=position))

    def move_relative(self, axis: int, delta: float) -> None:
        self.pos[axis] = self.pos.get(axis, 0.0) + delta
        self._bus.publish(MoveComplete(axis=axis, position=self.pos[axis]))


class FakeScopeService:
    def register_consumer(self, consumer_id: str) -> None:
        pass

    def unregister_consumer(self, consumer_id: str) -> None:
        pass


class _ScanFixture(unittest.TestCase):
    """Shared wiring: a fake ESP + a real scope buffer with a steady TraceAvailable stream."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.spec = ScopeMemorySpec(
            f"scripts_scope_{uuid.uuid4().hex[:8]}", slot_count=2, channels=2, n_samples=8
        )
        self.writer = ScopeBuffer.create(self.spec)
        # ch0 = [0..7]; with xcorr_top_n=3 -> mean(5,6,7) = 6.0 for every capture
        self.writer.write_trace(0, np.vstack([np.arange(8.0), np.zeros(8)]))
        self.esp = FakeEsp(self.bus)
        self.lab = Lab(
            bus=self.bus,
            cancel=CancelToken(),
            esp=self.esp,
            scope_service=FakeScopeService(),
            scope_spec=self.spec,
            config=LabConfig(xcorr_top_n=3, capture_timeout_s=2.0, move_timeout_s=2.0),
        )
        self._stop = threading.Event()
        self._producer = threading.Thread(target=self._produce, daemon=True)
        self._producer.start()

    def _produce(self) -> None:
        item = 0
        while not self._stop.is_set():
            item += 1
            self.bus.publish(TraceAvailable(slot=0, item_id=item, timestamp_ns=0))
            time.sleep(0.003)

    def tearDown(self) -> None:
        self._stop.set()
        self._producer.join(timeout=1.0)
        self.lab.close()
        self.writer.unlink()
        self.writer.close()


class TestProbeXcorrScan(_ScanFixture):
    def test_records_xcorr_per_position_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = probe_xcorr_scan(self.lab, 0.0, 0.2, 0.1, save_path=os.path.join(d, "s.csv"))
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                header = f.readline().strip()
        self.assertEqual(header, "probe_mm,xcorr")

        records = self.lab.records
        self.assertEqual([r["probe_mm"] for r in records], [0.0, 0.1, 0.2])
        for r in records:
            self.assertEqual(r["xcorr"], 6.0)
        self.assertEqual(self.esp.pos[1], 0.2)  # probe = ESP axis 1, last position


class TestOvernightSeries(_ScanFixture):
    def test_sweeps_setpoints_and_tags_rows(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = overnight_central_freq_series(
                self.lab,
                delay_setpoints_mm=[1.0, 2.0],
                start_mm=0.0,
                stop_mm=0.1,
                step_mm=0.1,
                save_path=os.path.join(d, "ov.csv"),
            )
            self.assertTrue(os.path.exists(path))

        records = self.lab.records
        # 2 setpoints x 2 probe positions = 4 rows, tagged by delay
        self.assertEqual(len(records), 4)
        self.assertEqual(sorted({r["delay_mm"] for r in records}), [1.0, 2.0])
        self.assertEqual(self.esp.pos[2], 2.0)  # delay = ESP axis 2, last setpoint


class TestScriptsRegister(unittest.TestCase):
    def test_routines_self_register_on_import(self) -> None:
        registry.clear_registry()
        importlib.reload(probe_scan)  # re-runs the @routine decorators against a clean registry
        names = registry.routine_names()
        self.assertIn("probe_xcorr_scan", names)
        self.assertIn("probe_scan_with_spectrum", names)
        self.assertIn("overnight_central_freq_series", names)
        # the param metadata the UI / an LLM would read
        spec = registry.get_routine("probe_xcorr_scan")
        required = [p.name for p in spec.params if p.required]
        self.assertEqual(required, ["start_mm", "stop_mm", "step_mm"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the XCORR acquisition path: the point collector, the buffer, the mock.

The scope free-runs into a shared-memory buffer and ``XcorrPointCollector`` turns that
stream into measurement points, so the three hardware-free pieces that decide whether a
point is right are:

* **The reduction (D3).** Per trace, the mean of the strictly positive samples; then the
  mean and spread *across* traces. The within-then-across order is the whole point of D3
  and a pooled mean is not the same number.
* **The freshness gate.** A trace whose capture began before the probe move returned
  belongs to the old position. ``TraceAvailable.timestamp_ns`` marks the start of the
  capture, so the tests drive the collector with hand-built stamps either side of the
  boundary. ``skip`` drops further traces as a margin.
* **The ack contract.** ``SlotCoordinator`` promotes the next trace only once every
  registered consumer has acked, so a missing ack stalls the stream for the alignment
  view and for the scan itself. Every path through ``_on_trace`` must ack: accepted,
  idle, gated out, skipped and read error.

Plus ``ScopeBuffer``, which now writes a record into the corner of a larger slot, and
``MockScope``, which must report a start stamp and a sample interval.

No pytest (AGENTS.md §5). Run directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_acquire.py
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from base_core.framework.events.event_bus import EventBus  # noqa: E402

from app_apps.io.oscilloscope.events import TraceAck, TraceAvailable  # noqa: E402
from app_apps.routines.xcorr.point_collector import XcorrPointCollector  # noqa: E402
from oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec  # noqa: E402
from oscilloscope.config import ScopeConfig  # noqa: E402
from oscilloscope.mock_driver import MockScope  # noqa: E402

N_SAMPLES = 8
CHANNELS = 2


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# --- fakes ----------------------------------------------------------------


class FakeHandle:
    """The four members of ``OscilloscopeWorkerHandle`` the collector touches.

    ``trace(slot)`` returns a frame whose channel-1 row is ``rows[slot]``, so a test can
    prove which slot a value actually came from.
    """

    def __init__(self, rows: dict[int, np.ndarray] | None = None) -> None:
        self.config = ScopeConfig(channels=CHANNELS, n_samples=N_SAMPLES)
        self.rows = rows or {}
        self.registered: set[str] = set()
        self.fail = False

    def register_consumer(self, cid: str) -> None:
        self.registered.add(cid)

    def unregister_consumer(self, cid: str) -> None:
        self.registered.discard(cid)

    def trace(self, slot: int) -> np.ndarray:
        if self.fail:
            raise RuntimeError("simulated shared-memory read failure")
        row = self.rows.get(slot, np.zeros(N_SAMPLES))
        return np.vstack([row, np.zeros(N_SAMPLES)])


def make_collector(handle=None):
    bus = EventBus()
    handle = handle or FakeHandle()
    acks: list[TraceAck] = []
    bus.subscribe(TraceAck, acks.append)
    collector = XcorrPointCollector(bus, handle)
    collector.start()
    return bus, handle, collector, acks


def arm(collector: XcorrPointCollector, *, n_traces: int, gate_ns: int, skip: int = 0,
        timeout_s: float = 2.0) -> dict:
    """Run ``collect`` on another thread and wait until it is actually armed.

    ``collect`` blocks, which is what the routine thread does; the frames it waits for
    arrive on the IPC reader thread, which is the test's main thread here.
    """
    out: dict = {}

    def run() -> None:
        out["values"], out["counts"] = collector.collect(
            n_traces=n_traces, channel=1, gate_ns=gate_ns, skip=skip, timeout_s=timeout_s
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while collector._active is None and time.monotonic() < deadline:
        time.sleep(0.001)
    out["thread"] = thread
    return out


def feed(bus: EventBus, slot: int, item_id: int, ts_ns: int) -> None:
    bus.publish(TraceAvailable(slot=slot, item_id=item_id, timestamp_ns=ts_ns))


# --- the reduction (D3 step 1) -------------------------------------------

def test_reduction_is_the_positive_mean_per_trace():
    rows = {
        0: np.array([2.0, -5.0, 4.0, 0.0, -1.0, 0.0, 0.0, 0.0]),   # positives 2,4 -> 3.0, n=2
        1: np.array([-3.0, 0.0, 7.0, -1.0, 0.0, 0.0, 0.0, 0.0]),   # single positive -> 7.0, n=1
    }
    bus, _, collector, _ = make_collector(FakeHandle(rows))
    try:
        out = arm(collector, n_traces=2, gate_ns=0)
        feed(bus, 0, 1, 10)
        feed(bus, 1, 2, 11)
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    assert approx(out["values"][0], 3.0) and out["counts"][0] == 2, out
    assert approx(out["values"][1], 7.0) and out["counts"][1] == 1, out


def test_all_negative_and_all_zero_traces_reduce_to_zero():
    # Zero is not strictly positive, so neither trace has any positive samples.
    rows = {0: np.full(N_SAMPLES, -1.0), 1: np.zeros(N_SAMPLES)}
    bus, _, collector, _ = make_collector(FakeHandle(rows))
    try:
        out = arm(collector, n_traces=2, gate_ns=0)
        feed(bus, 0, 1, 10)
        feed(bus, 1, 2, 11)
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    assert out["values"] == [0.0, 0.0], out["values"]
    assert out["counts"] == [0, 0], out["counts"]


def test_within_vs_between_distinction():
    """D3: per-trace positive-mean then average, not a global positive-mean.

    Two traces with very different positive counts must be weighted equally by the
    across-trace step, unlike a naive pooled positive-mean which weights by count.
    """
    t1 = np.array([10.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0])   # within-mean 10
    t2 = np.array([2.0, 2.0, 2.0, 2.0, -1.0, -1.0, -1.0, -1.0])       # within-mean 2
    bus, _, collector, _ = make_collector(FakeHandle({0: t1, 1: t2}))
    try:
        out = arm(collector, n_traces=2, gate_ns=0)
        feed(bus, 0, 1, 10)
        feed(bus, 1, 2, 11)
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    across = float(np.mean(out["values"]))                            # D3: (10 + 2)/2 = 6
    pooled = np.concatenate([t1, t2])
    pooled = float(pooled[pooled > 0].mean())                         # naive: 18/5 = 3.6
    assert approx(across, 6.0), across
    assert not approx(across, pooled), (across, pooled)


# --- the freshness gate ---------------------------------------------------

def test_a_trace_that_began_before_the_move_returned_is_rejected():
    rows = {0: np.full(N_SAMPLES, 1.0), 1: np.full(N_SAMPLES, 5.0)}
    bus, _, collector, _ = make_collector(FakeHandle(rows))
    try:
        out = arm(collector, n_traces=1, gate_ns=1_000)
        feed(bus, 0, 1, 999)      # began one ns too early — belongs to the old position
        assert out["thread"].is_alive(), "a gated-out trace must not complete the point"
        feed(bus, 1, 2, 1_000)    # began exactly at the gate — admissible
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    assert out["values"] == [5.0], out["values"]


def test_skip_drops_admissible_traces_before_collecting():
    rows = {0: np.full(N_SAMPLES, 1.0), 1: np.full(N_SAMPLES, 2.0), 2: np.full(N_SAMPLES, 3.0)}
    bus, _, collector, _ = make_collector(FakeHandle(rows))
    try:
        out = arm(collector, n_traces=1, gate_ns=0, skip=2)
        feed(bus, 0, 1, 10)
        feed(bus, 1, 2, 11)
        assert out["thread"].is_alive(), "skipped traces must not complete the point"
        feed(bus, 2, 3, 12)
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    assert out["values"] == [3.0], out["values"]


def test_a_short_burst_times_out_and_reports_what_it_got():
    rows = {0: np.full(N_SAMPLES, 4.0)}
    bus, _, collector, _ = make_collector(FakeHandle(rows))
    try:
        out = arm(collector, n_traces=3, gate_ns=0, timeout_s=0.2)
        feed(bus, 0, 1, 10)
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    # Short, not padded: the routine compares the length against n_traces and fails the
    # point, which is how a scope that stopped triggering is reported rather than averaged.
    assert out["values"] == [4.0], out["values"]


def test_channel_outside_the_configured_count_is_refused():
    _, _, collector, _ = make_collector()
    try:
        collector.collect(n_traces=1, channel=CHANNELS + 1, gate_ns=0, skip=0, timeout_s=0.1)
    except ValueError:
        pass
    else:
        raise AssertionError("a channel the scope is not configured for must raise")
    finally:
        collector.close()


# --- the ack contract -----------------------------------------------------

def test_every_frame_is_acked_on_every_path():
    rows = {0: np.full(N_SAMPLES, 1.0)}
    handle = FakeHandle(rows)
    bus, _, collector, acks = make_collector(handle)
    try:
        feed(bus, 0, 1, 10)                       # idle: nothing armed
        out = arm(collector, n_traces=1, gate_ns=1_000, skip=1)
        feed(bus, 0, 2, 500)                      # gated out
        feed(bus, 0, 3, 1_001)                    # skipped
        handle.fail = True
        feed(bus, 0, 4, 1_002)                    # read error
        handle.fail = False
        feed(bus, 0, 5, 1_003)                    # accepted
        out["thread"].join(timeout=2.0)
    finally:
        collector.close()
    assert [a.item_id for a in acks] == [1, 2, 3, 4, 5], [a.item_id for a in acks]
    assert {a.consumer_id for a in acks} == {XcorrPointCollector.CONSUMER_ID}, acks


def test_close_unregisters_and_stops_acking():
    handle = FakeHandle({0: np.ones(N_SAMPLES)})
    bus, _, collector, acks = make_collector(handle)
    assert handle.registered == {XcorrPointCollector.CONSUMER_ID}, handle.registered
    collector.close()
    assert handle.registered == set(), handle.registered
    feed(bus, 0, 1, 10)
    # Unregistered first, so a frame arriving after close is nobody's to ack.
    assert acks == [], acks


def test_close_releases_a_caller_still_waiting():
    _, _, collector, _ = make_collector()
    out = arm(collector, n_traces=5, gate_ns=0, timeout_s=30.0)
    collector.close()
    out["thread"].join(timeout=2.0)
    assert not out["thread"].is_alive(), "close() must not leave the routine thread parked"


# --- ScopeBuffer ----------------------------------------------------------

def test_a_short_record_is_written_into_the_corner_of_a_slot():
    spec = ScopeMemorySpec(f"test_scope_{int(time.time_ns())}", channels=2, n_samples=64)
    buf = ScopeBuffer.create(spec)
    try:
        record = np.vstack([np.arange(10.0), np.arange(10.0) * -1.0])
        buf.write_trace(0, record)
        got = buf.trace(0, 2, 10)
        assert got.shape == (2, 10), got.shape
        assert np.array_equal(got, record), got
        # A copy, not a view: the writer reuses the slot the moment the reader acks.
        buf.write_trace(0, np.zeros((2, 10)))
        assert np.array_equal(got, record), "trace() must not alias shared memory"
    finally:
        buf.unlink()
        buf.close()


def test_a_record_larger_than_a_slot_is_refused():
    spec = ScopeMemorySpec(f"test_scope_{int(time.time_ns())}", channels=2, n_samples=16)
    buf = ScopeBuffer.create(spec)
    try:
        buf.write_trace(0, np.zeros((2, 17)))
    except ValueError:
        pass
    else:
        raise AssertionError("a record the slot cannot hold must raise, not truncate")
    finally:
        buf.unlink()
        buf.close()


# --- MockScope ------------------------------------------------------------

def test_mock_reports_a_start_stamp_and_a_sample_interval():
    cfg = ScopeConfig(n_samples=2500, sample_rate_hz=2.5e8)
    scope = MockScope(cfg)
    before = time.time_ns()
    trace = scope.acquire_trace()
    after = time.time_ns()
    assert before <= trace.timestamp_ns <= after, trace.timestamp_ns
    assert approx(trace.dt_s, 4e-9), trace.dt_s


def test_mock_trace_shape_follows_the_config():
    scope = MockScope(ScopeConfig(channels=2, n_samples=2500))
    assert scope.acquire_trace().samples.shape == (2, 2500)
    scope = MockScope(ScopeConfig(channels=1, n_samples=500))
    assert scope.acquire_trace().samples.shape == (1, 500)


def test_mock_consecutive_traces_differ():
    from oscilloscope.mock_params import MockScopeParams

    scope = MockScope(ScopeConfig(), MockScopeParams(noise=0.01, seed=7))
    a = scope.acquire_trace().samples
    b = scope.acquire_trace().samples
    assert not np.array_equal(a, b), "a free-running mock must not return an identical buffer"


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

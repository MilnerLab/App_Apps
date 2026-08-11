"""Unit tests for XCORR spectrometer recording: the motion gate and the /spectra store.

Three things decide whether this feature is correct, and none of them need hardware:

* **The motion gate.** A spectrum whose integration window overlaps a stage move must be
  discarded; one wholly inside a stationary window must be kept and stamped with that
  window's positions. ``timestamp_ns`` marks the *end* of the integration, so the test
  drives the recorder with hand-built timestamps either side of that boundary.
* **The ack contract.** ``SlotCoordinator`` promotes the next spectrum only once every
  registered consumer has acked, so a missing ack stalls the phase-stabilization loop —
  a subsystem this feature is not allowed to degrade. Every path through
  ``_on_spectrum`` must ack: accepted, gated out, queue full, and read error.
* **The store.** ``/spectra`` must survive being appended from a second thread while
  ``/scans`` is written from the first, and a file with no spectra must still load
  through the v1 reader path.

No pytest (AGENTS.md §5). Run directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_spectra.py
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

from app_apps.analysis.xcorr.run_loader import load_run, load_spectra  # noqa: E402
from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable  # noqa: E402
from app_apps.routines.xcorr.planner import Setpoint  # noqa: E402
from app_apps.routines.xcorr.spectrum_recorder import XcorrSpectrumRecorder  # noqa: E402
from app_apps.routines.xcorr.storage import (  # noqa: E402
    SpectrumRecord,
    XcorrH5Writer,
)

N_PIXELS = 16
SPAN_NS = 250_000_000  # 250 ms, the stock exposure * average


# --- fakes ----------------------------------------------------------------

class FakeBuffer:
    """Stands in for ``SpectrumBuffer``. Slot content is a function of the slot index,
    so a test can prove which slot a record actually came from."""

    def __init__(self) -> None:
        self.wl = np.linspace(400.0, 800.0, N_PIXELS)

    def wavelengths(self, slot: int) -> np.ndarray:
        return self.wl.copy()

    def intensities(self, slot: int) -> np.ndarray:
        return np.full(N_PIXELS, float(slot + 1))


class FakeHandle:
    """The three members of ``SpectrometerWorkerHandle`` the recorder touches."""

    def __init__(self, buffer=None) -> None:
        self.buffer = buffer if buffer is not None else FakeBuffer()
        self.registered: set[str] = set()

    def register_consumer(self, cid: str) -> None:
        self.registered.add(cid)

    def unregister_consumer(self, cid: str) -> None:
        self.registered.discard(cid)


class FakeWriter:
    """Captures what the recorder would have written, without touching HDF5."""

    def __init__(self) -> None:
        self.records: list[SpectrumRecord] = []
        self.wavelengths = None
        self.attrs: dict = {}
        self.n_dropped = -1

    def open_spectra(self, wavelengths, attrs) -> None:
        if self.wavelengths is None:
            self.wavelengths = np.asarray(wavelengths)
            self.attrs = dict(attrs)

    def append_spectra(self, records) -> int:
        self.records.extend(records)
        return len(records)

    def close_spectra(self, *, n_dropped: int) -> None:
        self.n_dropped = n_dropped


def make_recorder(bus=None, writer=None, handle=None):
    bus = bus or EventBus()
    writer = writer or FakeWriter()
    handle = handle or FakeHandle()
    rec = XcorrSpectrumRecorder(bus, handle, writer, span_ns=SPAN_NS)
    return bus, writer, handle, rec


def drain(rec: XcorrSpectrumRecorder, timeout: float = 2.0) -> None:
    """Wait for the writer thread to empty the queue."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not rec._queue.empty():
        time.sleep(0.005)


def acks_for(bus: EventBus) -> list:
    seen: list = []
    bus.subscribe(SpectrumAck, seen.append)
    return seen


# --- the motion gate ------------------------------------------------------

def test_gate_closed_before_first_open_rejects_everything():
    _bus, _w, _h, rec = make_recorder()
    assert rec._admit(time.time_ns()) is None, "nothing may be admitted before a window opens"


def test_spectrum_wholly_inside_an_open_window_is_admitted():
    _bus, _w, _h, rec = make_recorder()
    rec.gate_open(3, grating_mm=-30.0, delay_mm=18.0, probe_index=7, probe_mm=155.0)
    # Integration began after the gate opened.
    ctx = rec._admit(time.time_ns() + 2 * SPAN_NS)
    assert ctx is not None, "a spectrum integrated entirely while stationary must be kept"
    assert (ctx.setpoint_index, ctx.probe_index) == (3, 7), ctx
    assert (ctx.grating_mm, ctx.delay_mm, ctx.probe_mm) == (-30.0, 18.0, 155.0), ctx


def test_spectrum_straddling_the_window_start_is_rejected():
    """Its integration began before the stages had settled — it saw a move."""
    _bus, _w, _h, rec = make_recorder()
    rec.gate_open(0, 0.0, 0.0, 0, 0.0)
    open_ns = rec._gate_open_ns
    assert rec._admit(open_ns + SPAN_NS - 1) is None
    # One nanosecond later the whole span fits inside the window.
    assert rec._admit(open_ns + SPAN_NS) is not None


def test_spectrum_finishing_after_the_next_move_started_is_rejected():
    _bus, _w, _h, rec = make_recorder()
    rec.gate_open(0, 0.0, 0.0, 0, 0.0)
    open_ns = rec._gate_open_ns
    rec.gate_close()
    closed_ns = rec._gate_closed_ns
    assert rec._admit(closed_ns + 1) is None, "integration ran into the next move"
    # A late-arriving event for a spectrum that finished before the move is still fine —
    # this is why gate_close() deliberately leaves the context alone.
    inside = max(open_ns + SPAN_NS, closed_ns - 1)
    if inside <= closed_ns:
        assert rec._admit(inside) is not None


def test_gate_close_is_idempotent():
    """_move() closes the gate on every axis, so a three-axis point closes it three
    times. Only the first may set the boundary, or a later close would extend the
    window past a move that already started."""
    _bus, _w, _h, rec = make_recorder()
    rec.gate_open(0, 0.0, 0.0, 0, 0.0)
    rec.gate_close()
    first = rec._gate_closed_ns
    time.sleep(0.001)
    rec.gate_close()
    assert rec._gate_closed_ns == first, "a second close must not move the boundary"


# --- the ack contract -----------------------------------------------------

def test_accepted_spectrum_is_acked():
    bus, writer, _h, rec = make_recorder()
    seen = acks_for(bus)
    rec.start()
    try:
        rec.gate_open(1, -30.0, 18.0, 2, 150.0)
        bus.publish(SpectrumAvailable(slot=0, item_id=11, timestamp_ns=time.time_ns() + 2 * SPAN_NS))
        drain(rec)
    finally:
        rec.close()
    assert len(seen) == 1 and seen[0].item_id == 11, seen
    assert seen[0].consumer_id == XcorrSpectrumRecorder.CONSUMER_ID
    assert len(writer.records) == 1, writer.records


def test_gated_out_spectrum_is_still_acked():
    """The one that matters: dropping data must never mean withholding an ack, or the
    phase-stabilization loop stalls for the whole duration of every stage move."""
    bus, writer, _h, rec = make_recorder()
    seen = acks_for(bus)
    rec.start()
    try:
        rec.gate_open(0, 0.0, 0.0, 0, 0.0)
        rec.gate_close()
        bus.publish(SpectrumAvailable(slot=0, item_id=5, timestamp_ns=time.time_ns() + 10 * SPAN_NS))
        drain(rec)
    finally:
        rec.close()
    assert len(seen) == 1 and seen[0].item_id == 5, seen
    assert writer.records == [], "a gated-out spectrum must not reach the file"
    assert rec.n_gated_out == 1, rec.n_gated_out


def test_read_error_is_swallowed_and_acked():
    class Exploding:
        def wavelengths(self, slot):
            raise RuntimeError("shared memory went away")

        def intensities(self, slot):
            raise RuntimeError("shared memory went away")

    bus, _w, _h, rec = make_recorder(handle=FakeHandle(buffer=Exploding()))
    seen = acks_for(bus)
    rec.start()
    try:
        rec.gate_open(0, 0.0, 0.0, 0, 0.0)
        bus.publish(SpectrumAvailable(slot=0, item_id=9, timestamp_ns=time.time_ns() + 2 * SPAN_NS))
        drain(rec)
    finally:
        rec.close()
    assert len(seen) == 1, "a read failure must not swallow the ack"


def test_full_queue_drops_and_acks_without_blocking():
    bus, _w, _h, rec = make_recorder()
    seen = acks_for(bus)
    # No writer thread started, so nothing drains: the queue fills and stays full.
    rec._unsub = bus.subscribe(SpectrumAvailable, rec._on_spectrum)
    rec.gate_open(0, 0.0, 0.0, 0, 0.0)
    n = rec._queue.maxsize + 20
    started = time.monotonic()
    for i in range(n):
        bus.publish(SpectrumAvailable(slot=0, item_id=i, timestamp_ns=time.time_ns() + 2 * SPAN_NS))
    elapsed = time.monotonic() - started
    rec._unsub()
    assert len(seen) == n, f"every publish must ack, got {len(seen)} of {n}"
    assert rec.n_dropped == 20, rec.n_dropped
    assert elapsed < 2.0, f"a full queue must not block the IPC thread ({elapsed:.1f}s)"


def test_close_unregisters_the_consumer():
    bus, writer, handle, rec = make_recorder()
    rec.start()
    assert XcorrSpectrumRecorder.CONSUMER_ID in handle.registered
    rec.close()
    assert handle.registered == set(), "a consumer left registered stalls every other consumer"
    assert writer.n_dropped == 0, writer.n_dropped


# --- the store ------------------------------------------------------------

def _setpoint(gi: int = 0, di: int = 0) -> Setpoint:
    return Setpoint(
        grating_mm=-30.0 + gi,
        delay_mm=18.0 + di,
        delay_base_mm=18.0 + di,
        delay_correction_mm=0.0,
        probe_offset_mm=80.0,
        probe_base_mm=(70.0, 71.0),
        probe_step_mm=1.0,
        max_freq_ghz=100.0,
        grating_index=gi,
        delay_index=di,
    )


def _record(i: int, setpoint_index: int = 0, probe_index: int = 0) -> SpectrumRecord:
    return SpectrumRecord(
        counts=np.full(N_PIXELS, float(i)),
        timestamp_ns=1_000 + i,
        setpoint_index=setpoint_index,
        probe_index=probe_index,
        grating_mm=-30.0 + setpoint_index,
        delay_mm=18.0,
        probe_mm=150.0 + probe_index,
    )


def test_spectra_round_trip():
    wl = np.linspace(400.0, 800.0, N_PIXELS)
    with TemporaryDirectory() as td:
        path = Path(td) / "run.h5"
        with XcorrH5Writer(path) as w:
            w.write_group(_setpoint(), [(150.0, 1.0, 0.1, 10)],
                          n_traces_per_point=10, utc_start="t0")
            w.open_spectra(wl, {"model": "fake"})
            w.append_spectra([_record(i) for i in range(5)])
            w.append_spectra([_record(i, setpoint_index=1) for i in range(5, 8)])
            w.close_spectra(n_dropped=3)
            w.mark_finished(aborted=False)

        s = load_spectra(path)
        assert s.n_rows == 8, s.n_rows
        assert np.allclose(s.wavelength_nm, wl)
        assert np.allclose(s.counts[:, 0], np.arange(8, dtype=np.float32))
        assert s.attrs["n_dropped"] == 3, s.attrs
        assert s.attrs["model"] == "fake", s.attrs
        # The join key, not the row index.
        assert list(s.for_setpoint(1)) == [5, 6, 7], s.for_setpoint(1)

        one = load_spectra(path, setpoint_index=1)
        assert one.n_rows == 3 and np.allclose(one.counts[:, 0], [5.0, 6.0, 7.0])

        run = load_run(path)
        assert run.has_spectra and run.spectra_meta["n_rows"] == 8, run.spectra_meta


def test_run_without_spectra_has_no_group_and_still_loads():
    with TemporaryDirectory() as td:
        path = Path(td) / "run.h5"
        with XcorrH5Writer(path) as w:
            w.write_group(_setpoint(), [(150.0, 1.0, 0.1, 10)],
                          n_traces_per_point=10, utc_start="t0")
            # The recorder never opened /spectra — close must not invent one.
            w.close_spectra(n_dropped=0)
            w.mark_finished(aborted=False)

        with h5py.File(path, "r") as f:
            assert "/spectra" not in f, "a run with no spectrometer must write no /spectra"

        run = load_run(path)
        assert not run.has_spectra and run.spectra_meta == {}, run.spectra_meta
        assert len(run.scans) == 1


def test_wrong_pixel_count_is_skipped_not_fatal():
    with TemporaryDirectory() as td:
        path = Path(td) / "run.h5"
        with XcorrH5Writer(path) as w:
            w.open_spectra(np.arange(N_PIXELS, dtype=float), {})
            bad = SpectrumRecord(
                counts=np.zeros(N_PIXELS + 5), timestamp_ns=1, setpoint_index=0,
                probe_index=0, grating_mm=0.0, delay_mm=0.0, probe_mm=0.0,
            )
            n = w.append_spectra([_record(0), bad, _record(1)])
            assert n == 2, n
            w.mark_finished(aborted=False)
        assert load_spectra(path).n_rows == 2


def test_concurrent_scan_and_spectrum_writes():
    """/scans comes from the routine thread and /spectra from the recorder's writer
    thread. h5py is not thread-safe; the writer's lock is what makes this legal."""
    with TemporaryDirectory() as td:
        path = Path(td) / "run.h5"
        errors: list[BaseException] = []
        with XcorrH5Writer(path) as w:
            w.open_spectra(np.arange(N_PIXELS, dtype=float), {})

            def spectra() -> None:
                try:
                    for i in range(200):
                        w.append_spectra([_record(i)])
                except BaseException as exc:  # noqa: BLE001 - reported below
                    errors.append(exc)

            t = threading.Thread(target=spectra)
            t.start()
            try:
                for gi in range(20):
                    w.write_group(_setpoint(gi=gi), [(150.0, 1.0, 0.1, 10)] * 5,
                                  n_traces_per_point=10, utc_start="t0")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            t.join(timeout=30.0)
            w.mark_finished(aborted=False)

        assert not errors, errors
        run = load_run(path)
        assert len(run.scans) == 20, len(run.scans)
        assert load_spectra(path).n_rows == 200


# --- routine wiring -------------------------------------------------------

class RecordingGate:
    """Stands in for the recorder inside a real ``XcorrRoutine``, logging gate calls."""

    def __init__(self) -> None:
        self.events: list = []

    def gate_close(self) -> None:
        self.events.append(("close",))

    def gate_open(self, si, grating_mm, delay_mm, probe_index, probe_mm) -> None:
        self.events.append(("open", si, grating_mm, delay_mm, probe_index, probe_mm))


class StubStage:
    def move_to(self, position, on_done, on_error) -> None:
        on_done()


class Skipped(Exception):
    """This leg cannot run here. Reported, never silently green (cf. the parity test)."""


def _routine_with_gate():
    """Build a real ``XcorrRoutine`` around stub stages, or raise :class:`Skipped`.

    ``XcorrRoutine.__init__`` builds a ``ScopeConfig(channel=..., n_samples=..., mock=...)``.
    The ``Devices`` checkout on this machine has none of those fields — the XCORR
    acquisition path lives on another branch of that repo (the same reason
    ``test_xcorr_acquire.py`` cannot import ``oscilloscope.reduce``). That is a
    pre-existing environment mismatch, not a fault in the gate wiring, so these legs
    report SKIPPED rather than red. **Run them once ``Devices`` is on the matching
    branch — until then the routine↔recorder wiring is unverified.**
    """
    from app_apps.routines.xcorr.config import XcorrConfig
    from app_apps.routines.xcorr.routine import XcorrRoutine

    cfg = XcorrConfig(
        probe_start_mm=70.0, probe_stop_mm=70.0, probe_step_mm=1.0,
        grating_start_mm=-30.0, grating_stop_mm=-30.0, grating_step_mm=1.0,
        delay_base_start_mm=18.0, delay_base_stop_mm=18.0, delay_base_step_mm=1.0,
    )
    try:
        r = XcorrRoutine(
            bus=EventBus(), config=cfg,
            probe=StubStage(), delay=StubStage(), grating=StubStage(), scope=None,
        )
    except TypeError as exc:
        raise Skipped(f"XcorrRoutine is not constructible against this Devices checkout: {exc}")
    gate = RecordingGate()
    r._recorder = gate
    return r, gate


def test_every_move_closes_the_gate():
    """_move() is the single choke point for all three axes; if it does not close the
    gate, a spectrum integrated during a move gets stamped with a stale position."""
    r, gate = _routine_with_gate()
    try:
        r._move(StubStage(), 150.0, "probe")
        r._move(StubStage(), 18.0, "delay")
        r._move(StubStage(), -30.0, "grating")
    finally:
        r.stop()
    assert gate.events == [("close",)] * 3, gate.events


def test_move_closes_the_gate_even_when_the_position_is_rejected():
    from app_apps.routines.xcorr.routine import XcorrError

    r, gate = _routine_with_gate()
    try:
        try:
            r._move(StubStage(), 9999.0, "probe")
        except XcorrError:
            pass
        else:
            raise AssertionError("an out-of-limit move must raise")
    finally:
        r.stop()
    assert gate.events == [("close",)], gate.events


def test_pause_closes_the_gate_and_no_op_does_not():
    r, gate = _routine_with_gate()
    try:
        r._wait_while_paused()            # not paused — must not touch the gate
        assert gate.events == [], gate.events
        r._resume.clear()
        r._abort.set()                    # so the wait unwinds immediately
        r._wait_while_paused()
    finally:
        r.stop()
    assert gate.events == [("close",)], gate.events


def test_sweep_opens_the_gate_with_the_commanded_positions():
    r, gate = _routine_with_gate()
    sp = _setpoint()
    r._acquire_point = lambda probe_mm: (1.0, 0.1, 10)
    from app_apps.routines.xcorr.planner import ScanPlan
    plan = ScanPlan(setpoints=(sp,), outer_axis="grating", outer_reason="test")
    try:
        rows, aborted = r._sweep_probe(plan, 0, sp, 0, len(sp.probe_base_mm))
    finally:
        r.stop()
    assert not aborted and len(rows) == 2, (rows, aborted)

    opens = [e for e in gate.events if e[0] == "open"]
    assert len(opens) == 2, gate.events
    # Positions stamped are the commanded ones, and the gate opens only after the move.
    for i, (base, ev) in enumerate(zip(sp.probe_base_mm, opens)):
        assert ev == ("open", 0, sp.grating_mm, sp.delay_mm, i, base + sp.probe_offset_mm), ev
    assert gate.events.index(("close",)) < gate.events.index(opens[0]), gate.events


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = skipped = 0
    for t in tests:
        try:
            t()
        except Skipped as exc:
            skipped += 1
            print(f"SKIP  {t.__name__}: {exc}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
        else:
            print(f"ok    {t.__name__}")
    print(f"\n{len(tests) - failed - skipped}/{len(tests)} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

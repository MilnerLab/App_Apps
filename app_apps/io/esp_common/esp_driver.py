"""
Low-level serial driver for Newport ESP-series motion controllers (ESP301, ESP100).

Pure hardware layer — depends only on a serial transport, no app framework. The
same driver serves the 3-axis ESP301 (probe/delay/truncation) and the 1-axis
ESP100 (grating); the controller is addressed per-axis (D8/Q1).

Transport is injected so the driver is unit-testable against a fake serial object;
use :meth:`EspDriver.open` to construct one backed by a real ``serial.Serial``.

Command reference: ESP301 Command Interface Manual (EDH0283En). Motion commands are
``<axis><MNEMONIC><args>\\r`` and queries return a single ASCII line. The data-
acquisition engine (``DC/DE/DD/DF/DG``) records a position-vs-time trajectory on the
controller's own clock — used for jitter-free probe-scan pairing (D10/Q4).

.. note::
   The exact serial syntax of the ``DC`` setup command and its ``dataAcquisitionMode``
   value for *actual position*, the minimum ``dataRate`` and the max ``dataNumber``
   are a build-time spike (M1.A.2b) to confirm against hardware/manual. The framing
   below is the best interpretation and is isolated in small helpers so it is trivial
   to correct.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

EOL = b"\r"


@runtime_checkable
class SerialLike(Protocol):
    """Minimal subset of :class:`serial.Serial` the driver needs (DI seam for tests)."""

    is_open: bool

    def write(self, data: bytes) -> int | None: ...
    def readline(self) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


class EspError(RuntimeError):
    """Raised when the controller reports a nonzero error code."""

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        super().__init__(f"ESP error {code}" + (f": {message}" if message else ""))


# Data-acquisition modes (DC). NOTE: confirm the 'actual position' value on hardware.
DAQ_POSITION = 1  # TODO(M1.A.2b): verify mode id for measured/actual position


@dataclass(frozen=True)
class Trajectory:
    """A position-vs-time record read back from the controller's DAQ buffer."""

    times_s: list[float]
    positions: list[float]

    def __len__(self) -> int:
        return len(self.positions)


class EspDriver:
    """Serial driver for an ESP motion controller.

    All public methods are thread-safe: a single lock serializes access to the
    shared serial port, so a position-polling thread and a command thread may use
    one driver instance concurrently (the ESP worker pattern, D20).
    """

    def __init__(self, transport: SerialLike) -> None:
        self._io = transport
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def open(cls, port: str, baud: int = 19200, *, timeout: float = 1.0) -> "EspDriver":
        """Open a real serial connection. Imported lazily so tests need no hardware."""
        import serial  # pyserial

        transport = serial.Serial(port, baud, timeout=timeout, write_timeout=timeout)
        return cls(transport)

    def close(self) -> None:
        with self._lock:
            if getattr(self._io, "is_open", False):
                self._io.close()

    # ------------------------------------------------------------------
    # Transport primitives
    # ------------------------------------------------------------------

    def _command(self, text: str) -> None:
        """Send a command that expects no reply."""
        with self._lock:
            self._io.write(text.encode("ascii") + EOL)

    def _query(self, text: str) -> str:
        """Send a query and return the stripped ASCII reply line."""
        with self._lock:
            self._io.write(text.encode("ascii") + EOL)
            raw = self._io.readline()
        return raw.decode("ascii", errors="replace").strip()

    def _query_float(self, text: str) -> float:
        reply = self._query(text)
        try:
            return float(reply)
        except ValueError as exc:
            raise EspError(-1, f"expected float, got {reply!r}") from exc

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_to(self, axis: int, position: float) -> None:
        """Absolute move (``PA``). Non-blocking; returns once the command is sent."""
        self._command(f"{axis}PA{position:.4f}")

    def move_relative(self, axis: int, delta: float) -> None:
        """Relative move (``PR``)."""
        self._command(f"{axis}PR{delta:.4f}")

    def stop(self, axis: int) -> None:
        """Stop motion on an axis (``ST``)."""
        self._command(f"{axis}ST")

    def home(self, axis: int, mode: int | None = None) -> None:
        """Search for home (``OR``)."""
        self._command(f"{axis}OR{mode}" if mode is not None else f"{axis}OR")

    def set_velocity(self, axis: int, velocity: float) -> None:
        self._command(f"{axis}VA{velocity:.4f}")

    def motor_on(self, axis: int) -> None:
        self._command(f"{axis}MO")

    def motor_off(self, axis: int) -> None:
        self._command(f"{axis}MF")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, axis: int) -> float:
        """Tell position (``TP``)."""
        return self._query_float(f"{axis}TP")

    def get_velocity(self, axis: int) -> float:
        """Tell velocity (``TV``)."""
        return self._query_float(f"{axis}TV")

    def is_motion_done(self, axis: int) -> bool:
        """Motion-done status (``MD``): True once the axis has stopped."""
        return self._query(f"{axis}MD?").strip() in ("1", "1\r", "TRUE")

    def get_error(self) -> int:
        """Tell error code (``TE?``); 0 means no error."""
        try:
            return int(float(self._query("TE?")))
        except ValueError:
            return -1

    def raise_on_error(self) -> None:
        code = self.get_error()
        if code != 0:
            raise EspError(code)

    def wait_for_stop(
        self, axis: int, *, timeout_s: float = 30.0, poll_s: float = 0.02
    ) -> None:
        """Block until the axis reports motion-done or the timeout elapses."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_motion_done(axis):
                return
            time.sleep(poll_s)
        raise EspError(-1, f"axis {axis} did not stop within {timeout_s}s")

    # ------------------------------------------------------------------
    # Data acquisition (DC/DE/DD/DF/DG) — controller-clocked trajectory (Q4/D10)
    # ------------------------------------------------------------------

    def setup_acquisition(
        self,
        axis: int,
        *,
        rate_hz: float,
        n_samples: int,
        mode: int = DAQ_POSITION,
    ) -> None:
        """Configure the DAQ engine (``DC``) to record ``n_samples`` at ``rate_hz``."""
        # DC(dataAcquisitionMode, axis, data3, data4, dataRate, dataNumber)
        self._command(f"DC{mode},{axis},0,0,{rate_hz:.4f},{n_samples}")

    def enable_acquisition(self, enabled: bool = True) -> None:
        """Arm/disarm acquisition (``DE``)."""
        self._command(f"DE{1 if enabled else 0}")

    def acquisition_done(self) -> bool:
        """DAQ done-status (``DD``)."""
        return self._query("DD").strip() in ("1", "TRUE")

    def acquisition_count(self) -> int:
        """Number of samples collected so far (``DF``)."""
        try:
            return int(float(self._query("DF")))
        except ValueError:
            return 0

    def read_acquisition(self, *, rate_hz: float) -> Trajectory:
        """Read the DAQ buffer (``DG``) and build a :class:`Trajectory`.

        ``DG`` returns the recorded positions; timestamps are reconstructed from the
        configured ``rate_hz`` (the controller samples on a fixed interval).
        """
        reply = self._query("DG")
        positions = _parse_float_list(reply)
        dt = 1.0 / rate_hz if rate_hz > 0 else 0.0
        times = [i * dt for i in range(len(positions))]
        return Trajectory(times_s=times, positions=positions)


def _parse_float_list(reply: str) -> list[float]:
    """Parse a comma/whitespace/newline-separated list of floats from a DG reply."""
    out: list[float] = []
    for token in reply.replace(",", " ").split():
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out

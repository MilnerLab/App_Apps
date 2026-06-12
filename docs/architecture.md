# usCFG Software — Architecture

> Technical design. See [summary.md](summary.md) for context/strategy and
> [tasks.md](tasks.md) for sequencing. Last updated: 2026-06-12.

---

## 0. STRUCTURE UPDATE (2026-06-12) — `main` is the integration base ⚠️ SUPERSEDES §2

The contributor **consolidated and reworked everything onto `main`** (all three repos);
`start_l2p` is **superseded**. `origin/main` was merged into our `feature/routines`
(merge `5302ebe`; our tested pure modules kept). The IPC framework **moved namespaces**
and the per-device layering changed. **Follow this** — §2 below describes the older
`start_l2p` variant and is kept only for history.

**Framework namespaces (Base_Core `main`):**
- `base_core.ipc.*` — `subprocess_service.SubprocessService`, `worker_handle.BaseWorkerHandle`,
  `message.{Message,Request,Reply,OKReply,ErrorReply}`, `worker_messages.{Start,Stop,Reset}Worker`, `BaseWorker`.
- `base_core.framework.shm.*` — `spec.MemorySpec`, `buffer.SharedMemoryBuffer`,
  `slot_coordinator.SlotCoordinator`, `writer_service.WriterSubprocessService`,
  `writer_subprocess_main.WriterSubprocessMain`.

**Per-device pattern — main side (`app_apps/io/<dev>/`):**
| File | Role |
|------|------|
| `buffer.py` | `MemorySpec` subclass (name, slot_count, shape, dtype) + `SharedMemoryBuffer` subclass (typed `write_*`/`read_*`). |
| `events.py` | `<X>Available`/`<X>Ack` (buffer slot events) + `Request<Cmd>` (bus command events). |
| `service.py` | `WriterSubprocessService[Available, Ack]` subclass; builds a `SlotCoordinator(spec, owner_id, bus, make_available, ack_type)`; declares `_entry_module` = the Devices subprocess module path. |
| `<dev>_worker_handler.py` | `BaseWorkerHandle` subclass: `WORKER_ID`; `_on_attached()` → `_subscribe(RequestX, handler)`; typed methods call `_emit(msg)` / `_request(msg, on_reply)`. Bridges bus `Request*` → IPC. |
| `module.py` | `BaseModule`: `register()` builds spec+service+handle and registers instances; `on_startup()` `service.start()` + `handle.start()`; `on_shutdown()` stops both. |

Plus `app/service_config.py` = `ServiceConfig` feature-flag dataclass (which services enable).

**Per-device pattern — worker side (Devices `<pkg>/`):**
- Subprocess entry subclasses `WriterSubprocessMain`; `setup()` calls
  `self.register_worker(<Worker>(bus=self.bus, connector=self.connector, config=..., port=...))`;
  entry `if __name__ == "__main__": <Process>.main()`.
- The `BaseWorker` + device messages live under `<pkg>/<subpkg>/` (e.g.
  `control_readout/elliptec/{messages,ell14_worker,config}.py`).

**Placement realignment for our work:** the ESP **driver** (`esp_driver.py`) belongs in a
Devices package (e.g. `newport_esp/`) alongside its `BaseWorker`; our `control/`
(PID + ownership) and `analysis/spectrum_info/` stay in `app_apps` (lower-layer, tested,
structure-independent). The ESP **io module** follows the buffer/events/service/handler/
module pattern above.

> ⚠️ **Not runnable locally yet:** the merged tree imports `base_core.ipc/shm`, which are on
> Base_Core `origin/main` but not in the local Base_Core checkout. Our pure unit tests
> (driver/PID/ownership/spectrum) still pass (no framework import). Framework-coupled
> code needs Base_Core+Devices updated to `main` first.

---

## 1. Layered architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ App_Apps (composition root)                                            │
│   app.py  ── ModuleManager bootstraps BaseModules ── DI Container      │
│   io/*        services that own device subprocesses + VMs + panels     │
│   analysis/*  analysis services (spectrum_info, phase_control)         │
│   routines/*  experiment orchestration (Routine + Steps)               │
│   app/*       shell + panel window (UI aggregation)                    │
└───────────────▲───────────────────────▲───────────────▲───────────────┘
                │                        │               │
        ┌───────┴───────┐        ┌───────┴──────┐  ┌─────┴───────┐
        │ Base_Core      │        │ Base_Qt      │  │ Devices      │
        │ framework +    │        │ panels, VMs, │  │ drivers +    │
        │ math/physics   │        │ dispatcher   │  │ subprocess   │
        └────────────────┘        └──────────────┘  └─────────────┘
```

- **Base_Core** — DI `Container`, `EventBus`, `BaseModule`, `SubprocessService`,
  `Routine`/`Step`, `TaskRunner`, shared-memory ring buffer + coordinator,
  serialization stores (h5), and stable `math`/`physics`/`fitting`/`quantities`.
- **Base_Qt** — `PanelVM`, `QtDispatcher`, `LabMainWindow`, `PanelWindow`.
- **Devices** — per-device packages: low-level driver + a subprocess wrapper.
- **App_Apps** — wires everything via DI modules; hosts services, analysis,
  routines, and UI.

Decoupling is via the **EventBus**: device subprocesses surface events (e.g.
`SpectrumAvailable`, `RotatorMoved`), and VMs / routines / analysis subscribe.
High-rate data (spectra, scope traces) flows through **shared-memory ring buffers**
rather than the event bus.

---

## 2. Canonical device pattern (the `spm_002` reference)

Every new device follows this shape. Two halves:

### 2a. Device side — `Devices/<pkg>/`

| File | Responsibility |
|------|----------------|
| `messages.py` | Frozen `Message` dataclasses (`NAME`, `KIND = COMMAND/EVENT`) for main↔subprocess. |
| `config.py` | Device config dataclass (often `PrimitiveSerde`). |
| `<driver>.py` | Low-level driver (pyserial / Ethernet). Pure hardware, no framework. |
| `<pkg>_process.py` | A `Worker` subclass + `if __name__ == "__main__": SubprocessApp(registry, source=…).add_worker(...).run()`. |

Worker base classes (in `base_core.framework.subprocess.worker`):

- **`ProducerWorker[Buffer, Data]`** — streaming. Implements `attach_buffer`,
  `acquire`, `write_to_slot`. Used by `spm_002` (spectra) → and by the
  **oscilloscope** (traces). **[RECOMMENDED]**
- **Base `Worker`** — request/reply + events. Implements `handle(msg, request_id)`
  and calls `reply_ok(request_id)` / publishes event messages. Used by motion +
  rotation stages (command a move, emit position events). **[RECOMMENDED]**

### 2b. Main side — `App_Apps/app_apps/io/<name>/`

| File | Responsibility |
|------|----------------|
| `module.py` | `BaseModule`: `register()` builds the service in DI; `on_startup()` starts it + registers lifecycle cleanup + worker-error handling. |
| `service.py` | `SubprocessService` subclass. Owns the subprocess endpoint; exposes `.worker(name).request_async(...)/.send(...)`; subscribes to app events; republishes device events. |
| `events.py` | App-level event dataclasses (bus payloads). |
| `ui/<name>_vm.py` | `PanelVM`: subscribes to events (`@ui_thread`), exposes commands the panel calls. |

Wiring: add the module to [app.py](../app.py) `modules=[…]` and register the panel in
[panel_window.py](../app_apps/app/panel_window.py) (the only two shared edits, per D4).

### 2c. Transport

- **Commands/events** → `JsonlSubprocessEndpoint` (JSONL over stdio), messages
  registered via `base_registry().extend(...)`.
- **High-rate frames** → `SharedRingBuffer` + `SharedBufferCoordinator`; consumers
  ack slots so the producer can recycle them (`BufferOutput` on the main side).

### 2d. Framework contracts (verified on `start_l2p`, 2026-06-12)

- **`Worker`** base: `run()` (abstract loop, polls `_should_stop`), `handle(msg,
  request_id)` (dispatch commands), `emit(Message)` (event → main), `reply_ok/reply_error`,
  `start()`/`close()` (open/close hardware), `_reset()`.
- **`CommandWorker`** enqueues commands and runs `handle()` **on the worker thread**
  → safe for **blocking** hardware I/O (serial). Its `run()` is the dequeue loop.
- **`ProducerWorker[Buf,Data]`** = streaming via shared buffer (`attach_buffer`,
  `acquire`, `write_to_slot`); used by the spectrometer + **oscilloscope**.
- **Events auto-reach the bus:** `SubprocessService.start()` streams decoded inbound
  messages with `on_item=bus.publish`, so **every `emit`'d EVENT Message is published
  to the app EventBus automatically**. Services just manage lifecycle + expose
  `.worker(name).request_async/.send`; VMs / control loops **subscribe to the worker's
  event Messages directly** (no service-side translation). Hardware subprocesses use
  **`DeviceService(SubprocessService)`** as the base.
- **`Message`**: frozen dataclass, `NAME`/`KIND` classvars (`Kind.COMMAND/REPLY/EVENT`),
  `to_payload`/`from_payload`; `target` field routes a command to a named worker, so for
  the single-subprocess ESP301 the **axis is a payload field, not the target**.

**Implementation decision — ESP motion worker = `CommandWorker` + a dedicated
position-poll thread + a serial lock.** No framework base exists for a "command +
periodic telemetry" device: `CommandWorker` handles moves on the worker thread (blocking
serial OK), a poll thread `emit`s `PositionUpdate` on a timer, and both guard the serial
port with one lock. (Flag to the collaborator as a candidate future framework primitive.)

### ⚠️ Known gaps / external dependencies (from start_l2p, 2026-06-12)

- **Routines framework is ABSENT** — `base_core.framework.routines.{routine,step}` exists
  on **no** Base_Core branch, yet App_Apps' `centrifuge_calibration` imports it. It blocks
  our **routine-based** work (reference-calibration, control loops, `ProbeScanRoutine`) —
  **but NOT the device milestone (M1)**, which uses only `Worker`/`SubprocessService`.
  → **[PENDING — user to confirm with collaborator]** whether/when they push
  `Routine`/`Step`. Fallback if it slips = define a minimal `Routine`/`Step` locally in
  `app_apps/` (per D3) to stay unblocked. Does **not** block M1.
- **Full-app launch (`python -m app`) is gated on the collaborator** (missing routines +
  in-flux elliptec). ⇒ We develop/test **M1 device packages standalone** (run the
  subprocess module directly, drive it with JSONL, unit-test the driver) rather than via
  the full app — keeps M1 unblocked regardless of the collaborator's timeline.

---

## 3. Device inventory & comms

| Device | Stage role(s) | Controller | Comms | Worker type | Notes |
|--------|---------------|-----------|-------|-------------|-------|
| Spectrometer | spectrum readout | SPM-002 | DLL (32-bit subprocess) | Producer + buffer | **Done** (`spm_002`). |
| ESP301 | probe, delay, truncation | Newport ESP301 (3-axis) | Serial (pyserial) | Base Worker | Workhorse. Proven cmd set in `test/esp100_test.py`. **[RESOLVED Q1]** one subprocess, 3 axes (axis-addressed). |
| ESP100 | grating | Newport ESP100 (1-axis) | Serial (pyserial) | Base Worker | Same command set as ESP301. |
| RGV100BL | HWP rotation | Newport **XPS** | Ethernet (`newportxps`) | Base Worker | Proven in `test/newportxps_test.py`. Needs admin XPS account. |
| ELL14 | QWP rotation | Thorlabs Elliptec | Serial (elliptec) | Base Worker | **[RESOLVED Q2]** reuse collaborator's `control_readout`/elliptec rotator; QWP-specific logic in our control layer; defer wiring to M4.7. *(Hardware plan: RGV100BL takes over HWP, freeing the ELL14 for the QWP.)* |
| Oscilloscope | scope trace | **Tektronix TBS** (older) | PyVISA/SCPI (mock now) | Producer + buffer | **[RESOLVED Q3]** Triggered single-trace interface: `configure(channels, timebase, trigger)` + `arm()` + `acquire_trace() -> (t[], v[]/chan, trigger_ts)`. Worker streams each triggered trace into a shared buffer w/ `timestamp_ns`. CH2 reserved for optional analog-position sync. Mock = synthetic chirped traces; real driver = PyVISA SCPI (`CURVe?`/`WFMOutpre?`). |
| Picomotors | mirror tip/tilt | Newport 8742 | Ethernet (`pylablib`) | Base Worker | Manual GUI only, no PID. Proven in `test/picomotors_ethernet_test.py`. |
| Servo shutters | block centrifuge arm(s) | Arduino/ESP32 (multiple servos) | **TBD** | Base Worker | **[NEW — from Q5]** Used by the reference-calibration routine. Actuation details unknown → build high-level only, **assume manual arm-block for now; servo actuation = TODO**. |

**Position readout. [RESOLVED Q4]** Two regimes:

- **PID-homed / occasional stages (grating, delay, truncation) + probe step-mode:**
  the worker publishes position **events** on a timed poll (~20 Hz) over JSONL.
  Low-rate; a shared buffer is overkill.
- **Probe-delay stage during scanned data collection:** use the **ESP301 internal
  data-acquisition engine** (`DC` setup → `DE` arm → move → `DD`/`DF` status →
  `DG` readback) to record a **position-vs-time trajectory on the controller's own
  clock** (jitter-free). Pair each scope trace to a probe position by mapping the two
  device clocks through a single software start-anchor and interpolating.

**Accuracy spec:** relative probe position accurate to **15 µm = 0.1 ps** (double-pass
`2Δx/c`). **Absolute zero is a nice-to-have only** (optional home/index). Because the
spec is *relative*, the controller-clocked trajectory delivers it: per-sample position
is exact, and only inter-oscillator drift over a scan (~0.5 µm for a 1 s sweep)
touches relative accuracy — well within budget. The one-time clock offset affects only
the (don't-care) absolute position.

**Fallback** if the ESP buffer/mode proves limiting: acquire only in the
constant-velocity **plateau**, poll `TP` position at the highest practical rate, gate
on polled velocity, and linearly interpolate real samples onto trace timestamps.

**M1.A build-time spikes (not design blockers):** confirm the `DC`
`dataAcquisitionMode` value for *actual position*, the minimum `dataRate`, and the max
`dataNumber` (buffer depth → long scans may need chunked re-arming). Scope traces
remain the only continuous high-rate stream (shared buffer).

---

## 4. Subsystem mapping (SDS → architecture)

### 4.1 Data input
- **Spectrum readout** (done) — `spm_002` streams raw spectra into a shared buffer.
  A small `ReferenceBuffer` component holds the reference snapshot + a `deque(maxlen=5)`
  of recent raw spectra.
- **Reference baseline [RESOLVED Q5].** The reference is a **single-arm drift
  baseline**: a servo shutter blocks one centrifuge arm, the passing arm is set
  horizontal via the HWP, and its spectrum is captured. With one arm blocked there is
  **no fringe** (single arm ⇒ no two-arm interference — an automatic consequence, not a
  tuning target). It's a **compare-against** baseline (drift detection), **not** a term
  in the fit. **Triggers:** GUI button, or idle **and** ≥15 min since last (debounce);
  future: gate on laser-on/downtime (deferred). Implemented by a **reference-calibration
  routine** coordinating shutter + HWP + spectrometer. **For now the routine assumes a
  MANUAL arm-block; servo actuation is a TODO** (servos run off an Arduino/ESP32 whose
  details are TBD).
- **True angular / linear position** — from the rotation / linear stage workers (§3).
- **Oscilloscope trace** — from the scope worker via shared buffer.

### 4.2 Data analysis — `app_apps/analysis/spectrum_info/` (new, **[RECOMMENDED]**)
New module, reuses **only stable Base_Core** math/physics; does **not** import the
in-flux `phase_control` app code (D7). Start as a pure, unit-testable domain library
(synthetic spectra) before any service/subprocess/UI.

**Physical model [RESOLVED Q6 / Q6b].** The spectrometer trace is an
**envelope-bounded sinusoid**:
$$I(\nu)=E_\text{lo}(\nu)+\big(E_\text{up}(\nu)-E_\text{lo}(\nu)\big)\cdot\tfrac12\big(1+\cos\Phi\big)$$
- `E_up`, `E_lo` are **independent parametric envelopes** (upper + lower). The **lower
  envelope** is a *free parameter*, not flat — minimizing it is the **QWP ellipticity
  metric**. (All three existing `*_projection` functions force a flat lower envelope =
  `baseline`, so none of them fit our model — we write our own.)
- The fringe phase `Φ` comes from a **single system-wide chirp** with instantaneous
  frequency **quadratic in time**: `f(t) = f0 + f1·t + f2·t²` (⇒ **cubic** temporal
  phase), plus a delay term and an initial phase. Per-arm chirps are **not
  observable** from one spectrum (only the system value), confirmed against model #3
  where arms enter only via `(1/a_R − 1/a_L)`.

**`SpectrumInfo` fields:**
- `central_wavelength_nm` (λ0), `bandwidth_nm`
- `chirp = [f0, f1, f2]` — instantaneous-frequency-vs-time coefficients, where
  `f0` = central frequency, **`f1` = linear chirp rate** (= quadratic-in-phase; the
  grating "acceleration" control target), **`f2` = TOD** (quadratic-in-frequency /
  cubic-in-phase, from laser/grating misalignment — a **nuisance** parameter fit only
  for phase-stabilization fit accuracy, never a control target)
- `delay`, `initial_phase`
- `nu_start`, `nu_end` — `f(t)` evaluated at the pulse-window edges
- `envelope_upper`, `envelope_lower` — **parametric** fit params (lower = QWP metric)
- `fit_residual`, `timestamp`

**Reuse map.** Write our **own** lmfit model in `spectrum_info/` (closest existing
reference is `cfCFG_projection`, the cubic-in-λ variant — for inspiration only).
Reuse Base_Core for **units/quantities** and **fitting helpers**;
`physics.CircularChirpedPulse` is useful to **generate synthetic test spectra**.

**Fit cadence [RESOLVED Q7].** Fit **per-spectrum** (retain per-parameter scatter +
residual → noise estimates for data validation / PID gating). The control loop
consumes the **rolling-averaged** fitted parameter; **anti-spasm lives in the control
layer** (deadband, slew/rate limit, conservative gain, QWP global-scan-then-PID), not
in the fit. `average-then-fit` kept as a low-SNR fallback toggle.

Wavelength→probe-delay mapping is covered in §4.3 (XCORR) below.

### 4.3 Cross-correlation (XCORR) — `app_apps/analysis/xcorr/`
**Physics [RESOLVED Q8].** The XCORR trace is the **same envelope-bounded chirped
sinusoid** as the spectrometer trace, but plotted **intensity-vs-time-within-pulse**
(obtained by a **probe-delay scan**) instead of intensity-vs-wavelength (one-shot).
Matching the same sinusoid across the two abscissae yields the **wavelength↔probe-delay
(time)** map. This characterizes the centrifuge's `f(t)`; it's **relatively stable**
and would need redoing **per grating-stage / delay-stage combination**.

**Priority [RESOLVED Q8].** XCORR is an **occasional characterization** tool (the
weekly recal), **not** a real-time control input — day-to-day stabilization uses the
one-shot spectrometer, and **PID drives the centrifuge to target initial/final
frequencies** directly. ⇒ **Milestone 3 (XCORR) drops below M2 (analysis) and M4
(PID) in priority.**

**Reuse win.** XCORR analysis = our **`spectrum_info` fit model reused with a time
abscissa** + axis-matching to the wavelength-domain fit. Inputs: `(probe positions[],
scope traces[])` from a scan; in dev it runs on recorded/synthetic data.

- **Test data [RESOLVED Q9].** Synthetic only for now (forward-model generator);
  validate on real data later.
- **Calibration store [RESOLVED Q10].** A dedicated **HDF5** calibrations file
  (configurable path, separate from run data), built with Base_Core `h5_utils`
  (read-only reuse — *not* `c2t_store`, which overwrites). Each calibration is a **new
  group keyed by UTC timestamp + grating/delay-stage combination**, never overwriting;
  an index table lists all. Entry: timestamp, stage-combo IDs/positions,
  wavelength↔delay table, contributing fit params.
- **Recal cadence [RESOLVED Q11].** XCORR recal is **NOT auto-run** — it takes much
  longer than the phase/reference calibration. (Only the *reference baseline* auto-runs
  during downtime at >15 min, Q5.) The XCORR-recal trigger / GUI reminder is
  **deferred — low priority, to design later.** "Last calibration time" is available
  from the store's newest entry if/when we build the reminder.

### 4.4 Command output & PID control
Per the SDS, controlled DOFs:

| DOF | Stage | Controlled quantity | PID? |
|-----|-------|---------------------|------|
| Probe | ESP301 | position (scan or step) | No |
| Delay | ESP301 | central frequency | **Yes** |
| Truncation | ESP301 | truncation time | **Yes** |
| Grating | ESP100 | acceleration | **Yes** |
| HWP | RGV100BL | phase stabilization; make one arm horizontal | **Yes** |
| QWP | ELL14 | minimize lower-envelope (ellipticity); global scan first | **Yes** |
| Mirrors | Picomotors | tip/tilt | No (manual GUI) |

**Control architecture [RESOLVED Q12].**
- A generic `PIDController` lives in `app_apps/control/` (NOT Base_Core, per D3).
- **Pure Routine-per-loop:** each control loop is an **independent `Routine`** that
  reads the shared `SpectrumInfo` **read-only**, owns **one distinct stage**, and emits
  commands via that stage's service. No central supervisor.
- **Callers sequence loops.** Loops are not designed to run simultaneously; an
  experiment routine or the GUI runs them in order (acquire → stabilize/move → release
  → next). Invariant: **one stage ↔ at most one active loop**. Future simultaneity is
  free (no shared stage; read-only shared input) and would only ever need a thin
  supervisor for *coordinated* sequencing — deferred.
- **Concurrency safety (two levels), because workers are async:**
  1. *Intra-owner* — each controller's **service** serializes in-flight commands per
     axis (existing busy-flag / `cancel_previous` pattern, cf.
     `ControlReadoutService._rotating`).
  2. *Inter-owner* — a **per-stage ownership token**: a loop/routine must
     **`try_acquire()`** a stage before driving it and **release** when done.
     Contention policy = **reject** (non-blocking; second caller gets an explicit
     "stage busy"), since simultaneous ownership is pathological by design — we want it
     loud, with no stale commands, no unbounded waits, deadlock-resistant.
  The ownership guard is a **required M4 deliverable**.

**Per-DOF control mapping [RESOLVED Q13].** Measured variable + setpoint per loop
(gains / update-rate / safety-limits stay empirical, tuned at build):

| DOF (actuator) | Controlled quantity | Measured variable (`SpectrumInfo`) | Setpoint |
|---|---|---|---|
| Delay (ESP301) | central frequency | `central_wavelength` → ν₀ (`f0`) | user target central freq |
| Truncation (ESP301) | truncation time | `nu_end` (→ time via XCORR if wanted) | user target truncation time |
| Grating (ESP100) | acceleration / chirp rate | **`f1`** (linear freq term = quadratic phase) | user target `f1` |
| HWP (RGV100BL) | phase stabilization | `initial_phase` | hold at reference phase (reject drift) |
| QWP (ELL14) | ellipticity | `envelope_lower` amplitude | minimize → 0 (coarse→fine→PID, Q14) |

`f2` (TOD) is fit but **not controlled** — retained for phase-stab fit accuracy.
Per-loop gains / update-rate / safety-limits stay **empirical, tuned at build**.
- **QWP special [RESOLVED Q14]** — **coarse full-range scan → fine local scan →
  PID**: coarse-scan the QWP's full relevant angular range to find the global
  lower-envelope minimum, fine-scan around it, then hand off to PID to track. Step
  sizes configurable; robust against local minima. (Exact angular span = one optical
  period of the QWP response — confirm at build.)
- **HWP special [RESOLVED Q15]** — "make one arm horizontal" = **minimize the
  single-arm spectrum amplitude** (other arm shutter-blocked) via the HWP. Shares
  hardware/flow with the reference-calibration routine (Q5).

### 4.5 Probe delay: scan vs step (amendment)
- **[RECOMMENDED]** the probe loop supports a **scan** mode (continuous sweep, more
  data points, faster) and keeps **step** mode as a toggle — step mode doubles as the
  *perfect-sync verification reference* the SDS asks for (stage stationary during
  acquisition). Absolute position is not important; we pair scope traces with true
  position during the scan.
- Scan sync uses the **ESP301 internal recording** path resolved in Q4 (controller-
  clocked trajectory + single software anchor + interpolation), fallback plateau-poll.
- **Orchestration [RESOLVED Q16].** A dedicated **`ProbeScanRoutine`** arms ESP301 DC
  recording + scope acquisition, commands a constant-velocity sweep across the range,
  reads back the DG trajectory, and pairs traces→interpolated positions via the
  timestamp anchor. If the range exceeds the ESP acquisition buffer (`dataNumber`), it
  **auto-splits into back-to-back constant-velocity segments** (re-arm per chunk). Step
  mode kept as the verification toggle. Sweep velocity chosen so `v × timing-jitter ≪
  15 µm` (Q4).

### 4.6 Low-priority QoL automation (deferred)
Automatic data acquisition, experiment queue + autoproceed, data
validation/rejection/warning. **[RECOMMENDED]** defer to last milestone; sketch only.

---

## 5. Proposed new directory layout (all additive)

```
Devices/
  esp_common/              # shared serial command set / esp_driver helper
    esp_driver.py
  esp301/                  # 3-axis: probe, delay, truncation
    messages.py  config.py  esp301_process.py
  esp100/                  # 1-axis: grating
    messages.py  config.py  esp100_process.py
  newport_xps/             # RGV100BL via XPS
    messages.py  config.py  xps_driver.py  newport_xps_process.py
  oscilloscope/            # mock now
    messages.py  config.py  scope_driver.py (mock)  oscilloscope_process.py
  picomotor/               # 8742 mirrors
    messages.py  config.py  picomotor_driver.py  picomotor_process.py
  servo_shutter/           # arm-block servos (Arduino/ESP32); STUB worker for now
    messages.py  config.py  servo_shutter_process.py
  # ELL14/QWP: reuse collaborator's control_readout (Q2) — no new package

App_Apps/app_apps/
  io/
    esp301/  esp100/  newport_xps/  oscilloscope/  picomotor/  servo_shutter/
      module.py  service.py  events.py  ui/<name>_vm.py
  analysis/
    spectrum_info/         # fit params, envelopes, phase, frequencies
    xcorr/                 # cross-correlation + calibration store
  control/                 # PIDController + per-DOF loop Routines + ownership guard
  routines/
    reference_calibration/  probe_scan/  <experiment routines, automation>
  docs/                    # these files
```

**[RESOLVED Q17]** ESP stages: split `esp301`/`esp100` packages sharing `esp_common`.
Control package = `app_apps/control/` (holds the generic `PIDController`, loop
Routines, and the ownership guard).

---

## 6. Conflict-avoidance rules (recap of D2–D4)

- New files / directories only. No edits to existing infra files.
- The only two files we edit that the collaborator might also touch:
  [app.py](../app.py) and [panel_window.py](../app_apps/app/panel_window.py) — append
  one block each per feature.
- Anything that "wants" to go in Base_Core/Base_Qt goes in `app_apps/` first and is
  flagged for later promotion.

---

## <a name="open-questions"></a>7. Open questions

✅ **All resolved as of 2026-06-12** (see decision log D8–D19 in [summary.md](summary.md)).
The "My lean" column has been replaced with the resolution. Remaining build-time
details (servo-shutter actuation comms, exact ESP301 `DC` mode/rate/buffer values, PID
gains/limits, QWP angular span) are flagged inline in their sections / tasks, not here.

| # | Topic | Question | Resolution |
|---|-------|----------|---------|
| ~~Q1~~ | ESP301 | One subprocess handling 3 axes, or one per axis? | **RESOLVED → one subprocess per controller, axis-addressed (ESP301=3 axes, ESP100=1); shared driver/worker code.** |
| ~~Q2~~ | ELL14/QWP | Reuse collaborator's elliptec, or build our own? | **RESOLVED → reuse collaborator's ELL14 rotator; QWP logic in our control layer; defer wiring to M4.7. HWP moves to RGV100BL.** |
| ~~Q3~~ | Scope | Oscilloscope model + driver interface? | **RESOLVED → Tektronix TBS (PyVISA/SCPI). Triggered single-trace interface (`configure`/`arm`/`acquire_trace`), producer→shared buffer, CH2 for analog-position sync. Mock now, real PyVISA driver later.** |
| ~~Q4~~ | Position | Position poll rate; scan-mode sync | **RESOLVED → 20 Hz events for occasional/PID stages; probe scan uses ESP301 internal `DC/DE/DD/DF/DG` recording (controller clock) paired via a single software anchor; plateau-poll fallback. Spec: 15 µm = 0.1 ps relative; absolute zero nice-to-have.** |
| ~~Q5~~ | Reference | Reference-spectrum meaning + cadence | **RESOLVED → single-arm drift baseline (shutter blocks one arm, HWP-horizontal, no fringe); compare-against, not in fit. Trigger: GUI or idle+≥15min debounce (laser-on gate later). Reference-calibration routine; manual arm-block for now, servo (Arduino/ESP32) = TODO.** |
| ~~Q6~~ | Fit result | Exact `SpectrumInfo` fields + model | **RESOLVED → envelope-bounded sinusoid; independent parametric upper/lower envelopes (lower = QWP metric); single system chirp, quadratic f(t)=[f0,f1,f2] (cubic phase); fields = λ0, bw, chirp, delay, initial_phase, ν_start/ν_end, env_up, env_lo, residual, ts. Write our own lmfit model.** |
| ~~Q7~~ | Fit input | Per-spectrum fit or rolling-average? | **RESOLVED → per-spectrum fit + rolling stats; control consumes rolling-averaged param; anti-spasm in PID layer; average-then-fit fallback toggle.** |
| ~~Q8~~ | λ→delay | Physical model for wavelength→probe-delay | **RESOLVED → spectrometer & XCORR are the same bounded sinusoid (vs λ vs vs time); match across axes for λ↔delay. Characterization only (per grating/delay combo); NOT real-time control (PID hits target freqs). XCORR reuses spectrum_info fit. M3 de-prioritized below M2/M4.** |
| ~~Q9~~ | Test data | Recorded datasets available now? | **RESOLVED → synthetic only for now. Build a forward-model generator (envelope-bounded sinusoid, quadratic f(t), independent upper/lower envelopes, configurable noise) → synthetic spectra (vs λ) + synthetic XCORR traces (vs t); it doubles as ground truth for the fitter. Validate on real data later.** |
| ~~Q10~~ | Calib file | Format + location for append-only calibration | **RESOLVED → dedicated HDF5 file (Base_Core h5_utils, read-only reuse); new group per calibration keyed by UTC timestamp + grating/delay-stage combo; never overwrite; index table.** |
| ~~Q11~~ | Reminder | Recal trigger / GUI nag | **RESOLVED → XCORR recal NOT auto-run (too slow); only the reference baseline auto-runs (>15 min, Q5). XCORR-recal trigger / reminder DEFERRED (low priority). Last-calib time from store's newest entry.** |
| ~~Q12~~ | Control home | Control loops as Routines or services? | **RESOLVED → pure Routine-per-loop (no supervisor); callers sequence; invariant 1 stage↔≤1 loop; concurrency = service per-axis serialization + per-stage ownership token with REJECT (try-acquire). Guard required in M4.** |
| ~~Q13~~ | PID params | Per-DOF measured variable + setpoint | **RESOLVED → Delay←ν₀(f0); Truncation←ν_end; Grating←f1 (chirp rate; f2=TOD nuisance); HWP←initial_phase; QWP←envelope_lower. Gains/rate/limits empirical at build.** |
| ~~Q14~~ | QWP scan | Global-minimum scan range/resolution | **RESOLVED → coarse full-range scan → fine local scan → PID; step sizes configurable; span ≈ one optical period (confirm at build).** |
| ~~Q15~~ | HWP horizontal | Observable defining "one arm horizontal" | **RESOLVED → minimize the single-arm spectrum amplitude (other arm shutter-blocked) via the HWP. Same flow as the reference-calibration routine (Q5).** |
| ~~Q16~~ | Probe scan | Scan orchestration + long-scan buffering | **RESOLVED → dedicated `ProbeScanRoutine` (arm ESP DC + scope → constant-velocity sweep → DG readback → pair via timestamp anchor); auto-chunk into segments if range > ESP buffer; step mode = verification toggle.** |
| ~~Q17~~ | Naming | Package naming choices | **RESOLVED → separate `esp301`/`esp100` packages sharing an `esp_common`/`esp_driver` helper.** |

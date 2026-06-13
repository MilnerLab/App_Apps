# usCFG Software — Task Plan

> Execution plan. See [architecture.md](architecture.md) for design and
> [summary.md](summary.md) for strategy. Each task is a **build → test → review →
> commit** unit (see summary §4). `[ ]` = todo, `[~]` = in progress, `[x]` = done.
> Last updated: 2026-06-12.

> ⚠️ **Structure update (2026-06-12):** integration base is now **`main`** (D21–D23);
> device/io wiring follows the **`base_core.ipc`/`shm`** Handle/Service/buffer pattern in
> [architecture §0](architecture.md). Tasks below that name `base_core.framework.subprocess`
> / `Worker`/`CommandWorker` describe the superseded `start_l2p` variant — translate to the
> new pattern when building.
>
> **Branches (2026-06-12):** device handles + analysis + control = App_Apps
> `feature/io-control-analysis` (pushed); drivers = Devices `feature/device-drivers` (pushed);
> the linear routine-authoring layer = App_Apps `feature/routine-authoring` (current).
>
> **Done so far** (committed, not pushed unless noted): full **device layer** mock-first
> (M1.A/C/D/E/G — ESP301, RGV100BL, picomotors, servo shutters in `control_readout`;
> TBS2012C scope in `oscilloscope/`); **`control/`** PIDController + stage-ownership guard;
> **`analysis/spectrum_info`** model+generator+fit; **`analysis/xcorr`** cross-correlation +
> calibration store. **~62 unit tests green on `.venv312`.** Routines framework confirmed
> present on `main`.

Build order followed **D6 (devices first)**. Analysis is **no longer blocked** — the new
framework is on `main` and our pure modules are built and tested.

---

## Milestone 0 — Environment baseline ✅ **DONE (superseded by `main`)**

- [x] **M0.1** Base_Core / Devices / Base_Qt all updated to **`main`** (the integration base;
      `start_l2p` superseded, D21).
- [x] **M0.2** Editable packages reinstalled to local `C:` paths; built **`.venv312`**
      (Python 3.12 — framework needs ≥3.11 `typing.Self`). Devices `pyproject` stale-`elliptec`
      fixed locally.
- [~] **M0.3** `python -m app` **still gated on the collaborator** (`elliptec.base`,
      `base_qt.ui.apply` WIP-broken on `main`). We proceed via **standalone testing**
      (mocks/fake handles/unit tests) — see [status.md](status.md).
- [x] **M0.4** Working branches created (now `feature/io-control-analysis`,
      `feature/device-drivers`, `feature/routine-authoring`).

> **Decoupling:** M1 device packages are developed/tested **standalone** — run the
> subprocess module directly (`python -m <device>.<device>_process`), drive it with
> JSONL commands, and unit-test the driver — so M1 does **not** wait on the full app
> launching. Routine-based milestones (M2.5, M4, M5) **do** need the routines framework
> (collaborator, or a local fallback).

---

## Milestone 1 — Devices (additive, independent of collaborator)

Each device = a new `Devices/<pkg>/` package + a new `app_apps/io/<name>/` module,
following the `spm_002` pattern (architecture §2).

### M1.A — Newport ESP301 motion (probe / delay / truncation) ✅ **BUILT (import-verified, py3.12)**

> Implemented on the new `base_core.ipc`/`shm` pattern, **hosted in the control_readout
> subprocess** (per agreement with collaborator). Devices `feature/esp301`:
> `control_readout/esp_301/` = `esp_driver.py` (relocated) + `config.py` + `messages.py`
> (`Request[OKReply]`/`Message`, `@register`) + `Esp301Worker` (`BaseWorker` + ~20 Hz
> position-poll thread → `_notify` telemetry over the IPC pipe + motion-complete
> detection), registered in `control_readout_process.setup()`. App_Apps
> `feature/routines`: `EspHandle` (`BaseWorkerHandle`) + `RequestMove` event + module
> wiring. Driver's 13 unit tests pass against the relocated module.
> ⚠️ **Cannot run the live subprocess yet:** `control_readout_process` also imports the
> contributor's `RotatorWorker`, which is broken on main (`elliptec_ell14.py` imports a
> moved-away `elliptec.base`). Pre-existing; flag to upstream. Same theme as `shell.py`
> → `base_qt.ui.apply` (also missing on main). My ESP301 code is correct + import-clean.
> **Remaining:** live hardware bring-up + ESP301 `DC` trajectory spike (M1.A.2b); a VM/panel.

<details><summary>original sub-tasks (superseded by the above)</summary>
- [ ] **M1.A.0** `Devices/esp_common/esp_driver.py`: shared serial driver (command set
      from `test/esp100_test.py`), parameterized by axis. *Test:* unit-test command
      framing; smoke-test against real controller.
- [ ] **M1.A.1** `Devices/esp301/`: `config.py`, `messages.py`
      (`MoveTo`, `Stop`, `Home`, `PositionUpdate`, `MoveComplete`, all axis-addressed).
- [ ] **M1.A.2** `esp301_process.py`: base `Worker` handling move/home/stop +
      timed `PositionUpdate` events for 3 axes (one serial conn, axis field). *Test:*
      run process standalone against a serial loopback / real ESP301. **[RESOLVED Q1]**
- [ ] **M1.A.2b** *(spike)* Confirm ESP301 `DC` data-acquisition: `dataAcquisitionMode`
      value for actual position, min `dataRate`, max `dataNumber` (buffer depth). Add a
      `record_trajectory()` path (DC→DE→move→DD/DF→DG) to `esp_driver`. *Test:* arm,
      short move, read back a position-vs-time array. **[RESOLVED Q4 — primary scan path]**
- [ ] **M1.A.3** `app_apps/io/esp301/`: `service.py`, `module.py`, `events.py`.
      Wire module into [app.py](../app.py). *Test:* service starts, forwards a move.
- [ ] **M1.A.4** `ui/esp301_vm.py` + panel; register in
      [panel_window.py](../app_apps/app/panel_window.py). *Test:* manual jog/home from
      the GUI moves the stage and updates the readout.
</details>

### M1.B — Newport ESP100 (grating)
- [ ] **M1.B.1** `Devices/esp100/` (1 axis) reusing `esp_common/esp_driver.py`;
      `app_apps/io/esp100/` module/service/VM + panel. *Test:* jog grating axis.
      **[RESOLVED Q17]**

### M1.C — Newport XPS / RGV100BL (HWP rotation)
- [ ] **M1.C.1** `Devices/newport_xps/`: `xps_driver.py` (`newportxps`), config,
      messages (angular move + `AnglePositionUpdate`). *Test:* read status, move HWP.
- [ ] **M1.C.2** `app_apps/io/newport_xps/` module/service/VM + panel. *Test:* GUI
      rotate + true-angle readout.

### M1.D — Oscilloscope (Tektronix **TBS2012C**, mock first) ✅ **BUILT (import-verified, py3.12)**

> Devices `feature/esp301`: `oscilloscope/` = config, buffer (`ScopeMemorySpec`/`ScopeBuffer`,
> shape `(channels, n_samples)`), messages (`SetScopeConfig`), `mock_driver` (synthetic
> envelope-bounded chirped trace on CH1 + position ramp on CH2 for analog-sync, Q4),
> `tbs_driver` (PyVISA/SCPI skeleton for the real TBS2012C), `OscilloscopeWorker` (producer,
> streams traces into slots), process entry. App_Apps `feature/routines`: `io/oscilloscope/`
> = service (`WriterSubprocessService`), handle, module + buffer re-export + events. 5 mock
> unit tests pass. **Remaining:** real `tbs_driver` hardware bring-up; a trace-display panel
> (blocked on contributor's base_qt UI rework).

<details><summary>original sub-tasks (superseded by the above)</summary>
- [ ] **M1.D.1** `Devices/oscilloscope/`: driver interface `configure()/arm()/
      acquire_trace() -> (t[], v[]/chan, trigger_ts)`; a **mock** impl (synthetic
      chirped trace) now; config; messages. Producer worker + shared ring buffer.
- [ ] **M1.D.1b** Real `tbs_driver.py` via PyVISA/SCPI (`DATa:SOUrce`, `WFMOutpre?`,
      `CURVe?`, `ACQuire:STATE`) — swap-in later when the scope is on the bench. Needs
      a VISA backend (NI-VISA or `pyvisa-py`).
- [ ] **M1.D.2** `app_apps/io/oscilloscope/` module/service + `BufferOutput`
      consumer + a trace-display panel. *Test:* mock traces stream + render.
</details>

### M1.C — RGV100BL (HWP rotation, Newport XPS) ✅ **BUILT (mock, py3.12)**
> Devices `control_readout/rgv100bl/` (config, messages, mock + `xps_driver` skeleton via
> `newportxps`, `Rgv100blWorker` — command-style, notifies `HwpAngleUpdate` after each move),
> registered in control_readout. App_Apps: `RgvHandle` + `RequestRotateHwp`. Mock test passes.
> **Remaining:** real XPS bring-up (admin account); a panel (blocked on base_qt UI rework).

### M1.E — Picomotors (mirrors, manual) ✅ **BUILT (mock, py3.12)**
> Devices `control_readout/picomotor/` (config, messages `StepBy`, mock + `picomotor_driver`
> skeleton via `pylablib`, `PicomotorWorker`, no PID), registered in control_readout. App_Apps:
> `PicomotorHandle` + `RequestStepPicomotor`. Mock test passes. **Remaining:** real 8742 +
> a manual jog panel (blocked on base_qt UI rework).

### M1.F — ELL14 / QWP (reuse + defer) **[RESOLVED Q2]**
- No device-layer work in this milestone. The QWP reuses the collaborator's ELL14
  rotator; QWP-specific logic (ellipticity min, global scan) lives in our control layer
  and is built at **M4.7**. HWP is driven by the RGV100BL (M1.C).

### M1.G — Servo shutters (high-level stub) ✅ **BUILT (py3.12)** **[Q5]**
> Devices `control_readout/servo_shutter/` (config, messages `BlockArm`/`UnblockArm` +
> `ArmStateChanged`, `ManualShutterStub` driver, `ServoShutterWorker`), registered in
> control_readout. App_Apps: `ServoShutterHandle` + `RequestSetArmBlocked`. Mock test passes.
> **TODO:** real Arduino/ESP32 servo actuation once comms details exist (D16).

---

## Milestone 2 — Spectrum analysis  (`analysis/spectrum_info/`)

Reuses Base_Core math/physics only (architecture §4.2). **Spectrum source = SPM-002
spectrometer, direct to computer (not via scope).** Core domain library built & tested.

- [x] **M2.0** Forward-model generator (envelope-bounded sinusoid, quadratic
      `f(t)`, independent upper/lower envelopes, configurable noise) → synthetic
      spectra; serves as fitter ground truth. **[RESOLVED Q9]** — `spectrum_info/generator.py`.
- [x] **M2.1** Pure domain library: our own lmfit model = envelope-bounded sinusoid
      with independent parametric upper/lower envelopes + single system chirp
      (quadratic `f(t)=[f0,f1,f2]`, cubic phase) + delay + initial phase. Fit raw
      spectrum → `SpectrumInfo`. **[RESOLVED Q6/Q6b/Q7]** — `spectrum_info/{model,fit}.py`,
      8 unit tests green.
- [ ] **M2.2** `ReferenceBuffer` (1 reference snapshot + deque(5) of recent raw
      spectra); reset event. Reference = single-arm drift baseline. *Test:* unit test
      buffer behavior. **[RESOLVED Q5]**
- [ ] **M2.5** *(routine, depends on HWP M1.C)* Reference-calibration routine:
      block one arm (**manual for now — servo actuation = TODO**) → HWP-minimize
      single-arm amplitude (= Q15 "make horizontal") → capture reference. Triggers:
      GUI button or idle+≥15min debounce (laser-on gate later). *Test:* dry-run with a
      synthetic single-arm spectrum. **[RESOLVED Q5/Q15]**
- [ ] **M2.3** Analysis service/subprocess consuming the spectrum shared buffer;
      publishes `SpectrumInfo` events. *Test:* feed recorded spectra; observe events.
- [ ] **M2.4** Spectrum-info panel (fit params + envelopes overlay). *Test:* live display.

---

## Milestone 3 — XCORR + calibration storage  (`analysis/xcorr/`)

> **Lower priority [RESOLVED Q8]:** characterization-only (weekly recal), not a
> real-time control input. Build after M2 (analysis) and ideally after M4 (PID).

> **Readout (2026-06-12 LAB):** XCORR signal = oscilloscope **CH1 photodiode**. Per probe-delay
> step: capture a scope trace, take the **mean of the 20 highest samples** → one scalar; sweep
> probe delay, plot scalar vs delay → bounded sinusoid. (Distinct from the SPM-002 spectrum.)

- [x] **M3.1** XCORR core: `cross_correlate`, lag→delay, `WavelengthDelayCalibration`
      (wavelength↔probe-delay table). *Test:* synthetic data, 8 unit tests green.
      **[RESOLVED Q8/Q9]** — `analysis/xcorr/calibration.py`. *(Remaining: the per-step
      "mean of top-20" scope reduction + full probe-scan wiring lands with M5.1.)*
- [x] **M3.2** Append-only **HDF5** calibration store (new group per UTC-timestamp +
      grating/delay-stage combo; never overwrite; index table). *Test:* 4 unit tests green.
      **[RESOLVED Q10]** — `analysis/xcorr/store.py`.
- [ ] **M3.3** *(deferred, low priority)* XCORR-recal reminder/trigger — XCORR is NOT
      auto-run (too slow). Design the nudge later; last-calib time comes from the store.
      **[RESOLVED Q11 → deferred]**

---

## Milestone 4 — PID control  (`control/`)

- [x] **M4.1** Generic `PIDController` in `app_apps/control/pid.py` (App-level, per D3:
      kp/ki/kd, output limits, deadband, slew limit, anti-windup). *Test:* 8 unit tests green.
- [x] **M4.1b** **Per-stage ownership guard** (`app_apps/control/ownership.py`:
      `try_acquire()`/`acquire()`/`release()`/`hold()` ctx mgr; REJECT on contention).
      *Test:* 8 unit tests green. **[RESOLVED Q12 — required]**
- [ ] **M4.2** Control-loop **Routine** base consuming `SpectrumInfo` (read-only) +
      true position → stage command; owns one stage via the guard; anti-spasm
      (deadband + slew limit) here. Test against a **fake stage**. **[RESOLVED Q12/Q13]**
      (per-DOF measured variables in architecture §4.4; gains/limits empirical at build)
- [ ] **M4.3** Delay loop — measured ν₀ (`f0`/λ0) → delay stage. *Test:* fake plant converges. **[RESOLVED Q13]**
- [ ] **M4.4** Truncation loop — measured `nu_end` → truncation stage. **[RESOLVED Q13]**
- [ ] **M4.5** Grating loop — measured `f1` (chirp rate) → grating stage; `f2`=TOD ignored. **[RESOLVED Q13]**
- [ ] **M4.6** HWP loop — measured `initial_phase`, hold reference → HWP. Plus
      make-arm-horizontal = minimize single-arm amplitude (shared with M2.5). **[RESOLVED Q13/Q15]**
- [ ] **M4.7** QWP loop: coarse full-range scan → fine local scan → PID tracking on
      the lower-envelope metric. Reuses collaborator's ELL14 rotator (Q2). **[RESOLVED Q14]**

---

## Milestone R — Linear routine-authoring layer (physicist/LLM-friendly)

> **Design recorded, build awaiting go-ahead.** Full design + LLM-automation roadmap in
> [routine_authoring_plan.md](routine_authoring_plan.md). Goal: a routine = a plain blocking
> function (`@routine` + a `lab` facade), ~10 lines instead of ~300, additive on top of the
> existing `Routine`/`Step`. Same verb set is the target for voice/autonomous LLM tiers.
> Branch `feature/routine-authoring`. All additive; no Base_Core/Base_Qt edits.

- [x] **R.0** Design + physics/action-grammar docs (`experiment_physics.md`,
      `routine_authoring_plan.md`); async→sync bridge validated against the real threading model.
- [x] **R.1** `cancel.py` + `bridge.py` — `await_event`/`await_reply`/`cancellable_sleep`
      + `CancelToken`/`RoutineCancelled`/`RoutineTimeout`. Subscribe-first-then-emit. *Test:*
      11 tests against the real `EventBus`, cross-thread wakeup. (commit `778cee1`)
- [x] **R.2** `registry.py` — `@routine` decorator + registry (no BaseModule for authors);
      `RoutineSpec` captures param metadata for UI/LLM. 10 tests. (commit `57fa3c8`)
- [x] **R.3** `lab.py` + `config.py` — facade (probe/delay/truncation/hwp/picomotor/shutter/
      scope/spectrometer + record/save-CSV/fit/plot/sleep/frange/xcorr_point); qwp raises
      LabUnavailable (deferred M4.7). **No Devices changes were needed** — the synchronous
      command workers already emit completion telemetry (`MoveComplete`/`HwpAngleUpdate`/
      `StepsMoved`/`ArmStateChanged`), so the facade awaits those (OKReply already = settled
      for HWP/picomotor/servo). 11 tests incl. real in-process shm consumer path. (commit `b857db6`)
- [x] **R.4** `runner.py` + `events.py` — `LinearRoutineRunner(Routine)`: single-flight
      `launch(name, **params)` on a TaskRunner thread with fresh Lab + cancel token; `stop()`
      cooperative cancel; lifecycle events (Started/Completed/Failed/CancelledEvent). Per-stage
      `StageOwnership` deferred (single-flight already prevents routine-vs-routine races;
      integrates at lab-verb level when M4 loops can run alongside a routine). 9 tests. (commit `b7b7ead`)
- [x] **R.5** `module.py` (`LinearRoutinesModule`) + `scripts/` package + one `app.py` line.
      Builds the `lab_factory` via defensive `container.try_get` (un-composed devices →
      `LabUnavailable`); imports `scripts` for `@routine` self-registration; adds `runner.stop`
      to lifecycle. 4 tests (real Container/AppContext). (commit `97bb221`)
- [x] **R.6** Example scripts (`scripts/probe_scan.py`, self-registering): `probe_xcorr_scan`
      (probe sweep → XCORR mean-of-top-N vs position → CSV/plot), `probe_scan_with_spectrum`
      (adds SPM-002 fit per point), `overnight_central_freq_series` (sweep delay setpoints =
      dominant central-freq knob D19, validate-and-repeat). 3 integration tests (wired Lab:
      fake ESP + real in-process scope buffer + background trace producer). Verifier-audited.
      (commit `3894d3b`) — NOTE: probe is automated *stepping*; true continuous scan = M5.1
      (ESP DC trajectory); full ν_start/ν_end control = M4 PID loops.
- [ ] **R.7** Routine **authoring guide** (write-a-routine-in-5-min; full verb reference;
      pasteable as LLM context).
- [ ] **R.8** *(roadmap, not committed)* T1 voice/standby trigger; T2 supervised planner;
      T3 bounded autonomous loop — see roadmap §6 of the plan.

---

## Milestone 5 — Probe scan + routines + UI polish

- [ ] **M5.1** `ProbeScanRoutine`: arm ESP301 DC recording + scope → constant-velocity
      sweep → DG trajectory readback → pair traces↔interpolated positions via timestamp
      anchor; auto-chunk into segments if range > ESP buffer; step-mode verification
      toggle. *Test:* synthetic scan produces paired data within 15 µm relative.
      **[RESOLVED Q16]**
- [ ] **M5.2** Experiment routine(s) tying input → analysis → control. *Test:* dry run.
- [ ] **M5.3** Panel layout / status / control surfaces consolidation.

---

## Milestone 6 — QoL automation (low priority, deferred)

- [ ] **M6.1** Automatic data acquisition.
- [ ] **M6.2** Experiment queue + autoproceed.
- [ ] **M6.3** Data validation / rejection / warning.

---

## Dependency summary

```
M0 ──> M1 (devices)
        ├─ M1.A ESP301 ─┐
        ├─ M1.B ESP100  │
        ├─ M1.C XPS/HWP │
        ├─ M1.D scope   ├─> M3 (XCORR needs scope+position)
        ├─ M1.E pico    │
        └─ M1.F ELL14   │
M2 (analysis) ──────────┴─> M4 (PID) ──> M5 (routines/scan) ──> M6 (QoL)
   MR (linear routine-authoring layer) ── enables M5 + LLM tiers; build any time
```

Open questions Q1–Q17 are catalogued in
[architecture.md §7](architecture.md#open-questions).

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
> **Done so far** (on `feature/routines`, committed, not pushed): ESP serial driver +
> PIDController + stage-ownership guard (`6b6d96c`); spectrum_info model+generator+fit
> (`3d23795`); 37 unit tests green.

Build order follows **D6 (devices first)** — analysis is blocked on the collaborator's
`phase_control` fix (expected ~2026-06-12).

---

## Milestone 0 — Environment baseline (blocking)

- [ ] **M0.1** Check out `start_l2p` in `Base_Core` and `Devices` (or branch from it).
- [ ] **M0.2** Reinstall editable packages so `base_core`/`devices` resolve to the
      local `C:` paths instead of the dead `D:` path (`pip install -e .` in each).
- [ ] **M0.3** Verify `python -m app` launches. ⚠️ **May be blocked** until the
      collaborator pushes the **routines framework** + fixes elliptec (see architecture
      "Known gaps"). If blocked, we proceed on M1 regardless via standalone testing.
- [ ] **M0.4** Create our working branch (off `start_l2p`) in App_Apps + Devices.

*Owner note:* you set up the env; I verify and report.

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

### M1.D — Oscilloscope (Tektronix TBS, mock first) **[RESOLVED Q3]**
- [ ] **M1.D.1** `Devices/oscilloscope/`: driver interface `configure()/arm()/
      acquire_trace() -> (t[], v[]/chan, trigger_ts)`; a **mock** impl (synthetic
      chirped trace) now; config; messages. Producer worker + shared ring buffer.
- [ ] **M1.D.1b** Real `tbs_driver.py` via PyVISA/SCPI (`DATa:SOUrce`, `WFMOutpre?`,
      `CURVe?`, `ACQuire:STATE`) — swap-in later when the scope is on the bench. Needs
      a VISA backend (NI-VISA or `pyvisa-py`).
- [ ] **M1.D.2** `app_apps/io/oscilloscope/` module/service + `BufferOutput`
      consumer + a trace-display panel. *Test:* mock traces stream + render.

### M1.E — Picomotors (mirrors, manual)
- [ ] **M1.E.1** `Devices/picomotor/`: `picomotor_driver.py` (`pylablib`
      `Newport.Picomotor8742`), config, messages (`StepBy`). *Test:* step a mirror axis.
- [ ] **M1.E.2** `app_apps/io/picomotor/` module/service + manual jog panel
      (no PID). *Test:* GUI buttons step the picomotor.

### M1.F — ELL14 / QWP (reuse + defer) **[RESOLVED Q2]**
- No device-layer work in this milestone. The QWP reuses the collaborator's ELL14
  rotator; QWP-specific logic (ellipticity min, global scan) lives in our control layer
  and is built at **M4.7**. HWP is driven by the RGV100BL (M1.C).

### M1.G — Servo shutters (high-level only) **[NEW — Q5]**
- [ ] **M1.G.1** Define the device interface + messages (`BlockArm(arm)`,
      `UnblockArm(arm)`, state events) and a **stub** worker that logs/prompts for a
      **manual** arm-block. *Test:* the reference-calibration routine (M2.5) drives the
      stub. **TODO:** real Arduino/ESP32 servo actuation once comms details exist.

---

## Milestone 2 — Spectrum analysis  (`analysis/spectrum_info/`)

Unblocks after collaborator's `phase_control` fix (so import paths/stable Base_Core
are confirmed). Reuses Base_Core math/physics only (architecture §4.2).

- [ ] **M2.0** Forward-model generator (envelope-bounded sinusoid, quadratic
      `f(t)`, independent upper/lower envelopes, configurable noise) → synthetic
      spectra + XCORR traces; serves as fitter ground truth. **[RESOLVED Q9]**
- [ ] **M2.1** Pure domain library: our own lmfit model = envelope-bounded sinusoid
      with independent parametric upper/lower envelopes + single system chirp
      (quadratic `f(t)=[f0,f1,f2]`, cubic phase) + delay + initial phase. Fit raw
      spectrum → `SpectrumInfo` (λ0, bandwidth, chirp, delay, initial_phase,
      ν_start/ν_end, env_up, env_lo, residual, ts). *Test:* unit tests on synthetic
      spectra (generate via `CircularChirpedPulse` / our forward model). **[RESOLVED Q6/Q6b/Q7]**
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

- [ ] **M3.1** XCORR analysis = **reuse `spectrum_info` fit** on the scan trace (time
      abscissa) + match to the wavelength-domain fit → wavelength↔probe-delay table.
      Input `(positions[], traces[])` from a probe scan. *Test:* synthetic/recorded
      data (synthetic for now). **[RESOLVED Q8/Q9]**
- [ ] **M3.2** Append-only **HDF5** calibration store (Base_Core h5_utils; new group
      per UTC-timestamp + grating/delay-stage combo; never overwrite; index table).
      *Test:* write twice, confirm both retained. **[RESOLVED Q10]**
- [ ] **M3.3** *(deferred, low priority)* XCORR-recal reminder/trigger — XCORR is NOT
      auto-run (too slow). Design the nudge later; last-calib time comes from the store.
      **[RESOLVED Q11 → deferred]**

---

## Milestone 4 — PID control  (`control/`)

- [ ] **M4.1** Generic `PIDController` in `app_apps/control/` (App-level, per D3).
      *Test:* unit tests (step response, anti-windup, limits, deadband, slew limit).
- [ ] **M4.1b** **Per-stage ownership guard** (registry of tokens; `try_acquire()` →
      reject if owned; release). *Test:* second acquirer is rejected; release frees it.
      **[RESOLVED Q12 — required]**
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
   (M2 blocked on collaborator phase_control fix ~06-12)
```

Open questions Q1–Q17 are catalogued in
[architecture.md §7](architecture.md#open-questions).

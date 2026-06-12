# usCFG Software — Project Summary & Decision Log

> Orientation doc. Read this first, then [architecture.md](architecture.md) for the
> technical design and [tasks.md](tasks.md) for the execution plan.
> Living document — updated as we make decisions. Last updated: 2026-06-11.

---

## 1. What we're building

Control/readout/analysis software for the ultrashort optical centrifuge (usCFG)
experiment, per the *usCFG Software Design Specification*. At a high level:

- **Device I/O** for motion stages, rotation stages, an oscilloscope, and mirror
  piezomotors (the spectrometer is already implemented).
- **Data analysis**: spectrum fit parameters (envelopes, quadratic chirp,
  instantaneous phase, start/end frequency) and a cross-correlation (XCORR)
  wavelength→probe-delay calibration.
- **PID control loops** that drive several stages from the analysis outputs.
- **Routines** that orchestrate experiments, plus low-priority automation.

The full subsystem breakdown and how each piece maps onto the existing codebase
lives in [architecture.md](architecture.md).

---

## 2. Repository / environment situation

Four sibling repos under `MilnerLab code/`, installed as editable packages:

| Repo | Import name(s) | Role |
|------|----------------|------|
| `Base_Core` | `base_core` | Framework: DI, events, subprocess, routines, math/physics |
| `Base_Qt` | `base_qt` | Qt UI framework (panels, VMs, dispatcher) |
| `Devices` | `elliptec`, `spm_002`, `control_readout` | Low-level device drivers + subprocess wrappers |
| `App_Apps` | `app_apps`, `app` | Composition root: services, routines, analysis, UI |

### Known issues at start (2026-06-11)

1. **Branch skew.** App_Apps (`feature/routines`) imports framework + device modules
   (`base_core.framework.subprocess.*`, `base_core.framework.routines.*`,
   `spm_002.shared_spectrum_buffer`, `control_readout.*`) that exist **only on the
   `origin/start_l2p` branch** of Base_Core and Devices. Local checkouts of those
   repos are on `main` / `feature/routines`, which **lack** those modules.
2. **Dead editable installs.** The `.venv` editable finders map `base_core`,
   `devices` to a `D:\…` path that does not exist on this machine. Imports currently
   resolve to nothing.
3. **Collaborator in-flight work.** `start_l2p` (a collaborator's branch) is itself
   mid-development: `control_readout` / `elliptec` rotator integration imports
   `elliptec.messages` / `elliptec.rotator_worker` that don't exist yet, and
   `app_apps.analysis.phase_control` has broken import paths
   (`...phase_control.domain.*` vs `...phase_control.subprocess.domain.*`).

**Consequence:** as checked out, App_Apps will not import or run. Resolving this is
Milestone 0 (see [tasks.md](tasks.md)).

---

## 3. Strategy decisions (locked)

These are agreed and stable. New decisions get appended to the log in §5.

- **D1 — Integration base = `start_l2p`.** Every branch we create is based on the
  latest `start_l2p`, so merges with the collaborator are forward-only, never
  divergent. App_Apps `feature/routines` already merged `start_l2p`.
- **D2 — Additive-only.** We add **new files / new directories** only. We do not
  modify existing infrastructure. New files never produce merge conflicts and this
  matches the "don't touch existing infra" rule.
- **D3 — Never touch `Base_Core` / `Base_Qt`.** If we genuinely need a new framework
  primitive (e.g. a PID helper, a generic motion-stage worker base), we put it inside
  `app_apps/` first and **flag it loudly**; promotion to the framework happens later,
  on the collaborator's terms.
- **D4 — Confine unavoidable edits to two aggregation files.** Only
  [app.py](../app.py) (the `modules = [...]` list) and
  [app_apps/app/panel_window.py](../app_apps/app/panel_window.py) (`_build_panels`)
  must be edited to wire new features in. We keep edits as clean appended blocks.
- **D5 — Docs live in `App_Apps/docs/`.** Versioned with our primary work area.
- **D6 — Build order: devices first.** Analysis (Milestone 1 originally) depends on
  the collaborator's in-flux `phase_control` being fixed (expected ~2026-06-12).
  Device packages are fully additive and independent, so we build those first.
- **D7 — Follow the `spm_002` pattern for all new devices.** It is the one complete,
  clean reference device. We do **not** depend on the half-finished
  `control_readout`/`elliptec` rotator wiring.

---

## 4. How we work

Each task in [tasks.md](tasks.md) is a **build → test → review → commit** unit:

1. I build one small, coherent piece (new files only where possible).
2. I test it (unit test on synthetic data, or a hardware/manual check where noted).
3. I hand it to you to **review, verify, and commit**.
4. We move to the next task.

I will call out with **emphasis** any time I believe I must modify existing
infrastructure, before doing so.

---

## 5. Decision log

| ID | Date | Decision | Rationale |
|----|------|----------|-----------|
| D1 | 2026-06-11 | Base all work on `start_l2p` | Forward-only merges with collaborator |
| D2 | 2026-06-11 | Additive-only (new files) | Zero merge conflicts; matches infra rule |
| D3 | 2026-06-11 | Never edit Base_Core / Base_Qt | Stay out of collaborator's repos |
| D4 | 2026-06-11 | Confine edits to app.py + panel_window.py | Minimal, trivial conflict hunks |
| D5 | 2026-06-11 | Docs in App_Apps/docs/ | Versioned with our work |
| D6 | 2026-06-11 | Build devices before analysis | Analysis blocked on collaborator |
| D7 | 2026-06-11 | Follow spm_002 device pattern | Only clean, complete reference |
| D8 | 2026-06-11 | One subprocess per motion controller, axis-addressed | Matches single serial port; shared ESP driver for ESP301+ESP100 (Q1) |
| D9 | 2026-06-11 | ESP packaging: split `esp301`/`esp100` over shared `esp_common` driver | Explicit per-device, shared serial code (Q17) |
| D10 | 2026-06-11 | Probe scan sync via ESP301 internal data acquisition (DC/DE/DD/DF/DG); plateau-poll fallback | Controller-clocked trajectory → 15 µm relative spec met; only absolute offset is software-anchored (don't-care) (Q4) |
| D11 | 2026-06-11 | QWP reuses collaborator's ELL14 rotator; HWP moves to RGV100BL; defer QWP wiring to M4.7 | Same hardware driver; QWP logic lives in our control layer (Q2) |
| D12 | 2026-06-11 | Scope = Tektronix TBS via PyVISA/SCPI; triggered single-trace interface; mock first | Known model; standard SCPI; CH2 free for analog-position sync (Q3) |
| D13 | 2026-06-12 | Spectrum model = envelope-bounded sinusoid; single system chirp, quadratic f(t) (cubic phase); independent parametric upper/lower envelopes; our own lmfit model | Per-arm chirp not observable; lower envelope must be free (QWP metric); existing functions force flat lower envelope (Q6/Q6b) |
| D14 | 2026-06-12 | Fit per-spectrum; control consumes rolling-averaged param; anti-spasm in PID layer (deadband/slew limit) | Keeps noise estimates + smooth control; separates measurement bandwidth from control stability (Q7) |
| D15 | 2026-06-12 | XCORR = same bounded sinusoid vs time; reuse spectrum_info fit; characterization-only; M3 below M2/M4 | Spectrometer (one-shot) + PID-to-target-freq cover daily ops; XCORR only for per-combo f(t) characterization (Q8) |
| D16 | 2026-06-12 | Reference = single-arm drift baseline (shutter + HWP-horizontal); reference-calibration routine; new servo-shutter device (Arduino/ESP32, actuation TODO, manual for now); Q15 = minimize single-arm amplitude | Drift baseline needs a clean single-arm spectrum; servo details unknown (Q5/Q15) |
| D17 | 2026-06-12 | Pure Routine-per-loop control; callers sequence; per-stage ownership token (try-acquire, REJECT on contention) + service per-axis serialization; guard required in M4 | Loops never share a stage / never run simultaneously by design; async workers need explicit single-owner guarantee; reject keeps pathological contention loud (Q12) |
| D18 | 2026-06-12 | Probe scan = dedicated ProbeScanRoutine (ESP DC + scope, constant-v sweep, DG readback, auto-chunk); QWP = coarse→fine→PID; XCORR calib = append-only HDF5 (new group per timestamp+stage-combo); XCORR recal NOT auto-run (too slow) — reminder deferred low-priority | Q16/Q14/Q10/Q11 |
| D19 | 2026-06-12 | Per-DOF control: Delay←ν₀(f0), Truncation←ν_end, Grating←f1 (chirp rate), HWP←initial_phase, QWP←envelope_lower; f2=TOD nuisance (fit not controlled) | Grating cares about linear-freq term; f2 is TOD from laser/grating misalignment, kept for fit accuracy only (Q13) |
| D20 | 2026-06-12 | ESP worker = CommandWorker + poll thread + serial lock; events auto-publish to bus; M1 developed/tested standalone (not via full-app launch) | Verified framework contracts; full-app launch gated on collaborator (missing routines + in-flux elliptec) |

---

## 6. Open questions

The full, numbered open-questions list lives at the bottom of
[architecture.md](architecture.md#open-questions) and is referenced from individual
tasks. Resolve them with me one at a time; I update the docs as we decide.

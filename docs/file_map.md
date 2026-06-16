# usCFG Software — File Map

> An onboarding map of the **App_Apps** repo: what each directory/key file is for. Kept shallow
> (dirs + notable files, not every file) so it stays current. See [architecture.md](architecture.md)
> for design, [status.md](status.md) for state, [routine_authoring_guide.md](routine_authoring_guide.md)
> to write routines. Last updated 2026-06-15.

## Top level
```
app.py                     # app entry: composes modules (the modules=[...] list) + ServiceConfig
scripts/                   # dev tooling (verify, demos) — not shipped in the app
docs/                      # design + onboarding docs (this file lives here)
test/unit/                 # stdlib unittest suite (mocks/synthetic; no hardware)
test/integration/          # in-process end-to-end tests (OpticalPlant <-> real chain)
.venv312/                  # Python 3.12 venv (gitignored) — run everything with this
```
> Loose `test/*.py` (e.g. `esp100_test.py`, `tektronix_oscilloscope_pyvisa.py`) and root
> `AcquireAndDisplay.py` are legacy bench/hardware probe scripts, not part of the suite.

## `app_apps/` — the application packages

### `app_apps/io/` — device layer (handle + service + buffer + worker, mock-first)
```
io/esp_common/             # shared ESP serial driver helpers
io/control_readout/        # handles for the shared control/readout subprocess devices:
                           #   ESP301 (probe/delay/truncation), RGV100BL (HWP), picomotors,
                           #   servo shutters, + buffers/worker-handlers; ui/ panels
io/oscilloscope/           # TBS2012C scope: ScopeBuffer/ScopeMemorySpec, events, service, handle
io/spectrometer/           # SPM-002: SpectrumBuffer/SpectrumMemorySpec, events, service, handle
```

### `app_apps/analysis/` — turn raw readout into physical quantities
```
analysis/spectrum_info/    # the interferometric-spectrum model + fit (OURS)
  model.py                 #   SpectrumParams/SpectrumInfo, envelope-bounded chirped-sinusoid, C_NM_THZ
  generator.py             #   synthetic_spectrum() forward model (tests + fitter ground truth)
  fit.py                   #   fit_spectrum() (lmfit) + estimate_fringe_rate() (FFT seed for phase0)
  reference.py             #   ReferenceBuffer: single drift baseline + rolling history (M2.2)
  rotation.py              #   PulseChirp + rotational_frequency_ghz() (spectrum THz -> centrifuge GHz)
analysis/xcorr/            # cross-correlation + wavelength<->probe-delay calibration + HDF5 store
analysis/phase_control/    # COLLABORATOR's phase-stabilization subprocess (envelope optimizer,
                           # phase tracker/corrector, ui/) — do not edit
```

### `app_apps/control/` — control primitives (OURS)
```
control/pid.py             # PIDController (deadband, slew-limit, anti-windup)
control/ownership.py       # per-stage ownership guard (no two loops drive one stage)
control/lock.py            # run_pid_lock(): the reusable measure->correct->settle feedback engine
```

### `app_apps/routines/` — experiment routines
```
routines/linear/           # the physicist/LLM-friendly LINEAR layer (OURS, Milestone R)
  registry.py              #   @routine decorator + RoutineSpec (name/params/safe/bounds)
  lab.py                   #   the `lab` facade (probe/delay/truncation/hwp/.../scope/spectrometer
                           #     + record/save-CSV/fit/plot/sleep/frange/xcorr_point) + fit_spectrum
  bridge.py / cancel.py    #   await_event/await_reply/cancellable_sleep + CancelToken (async->sync)
  runner.py                #   LinearRoutineRunner: single-flight launch + lifecycle events
  module.py / events.py    #   DI wiring (lab_factory) + lifecycle event types
  scripts/                 #   self-registering routines authors copy from:
    probe_scan.py          #     probe_xcorr_scan, probe_scan_with_spectrum, overnight_central_freq_series
    control_loops.py       #     lock_central_frequency / lock_terminal_frequency / lock_phase (M4)
routines/centrifuge_calibration/  # older framework Step-based routine (pre-linear-layer style)
```

### `app_apps/assistant/` — LLM control layer (OURS, off by default)
```
assistant/client.py        # LLMClient protocol + ClaudeClient (Haiku; lazy, no network until enabled)
assistant/tools.py         # build tool-use schemas from the routine registry
assistant/validation.py    # validate/coerce proposed params against a RoutineSpec (+ bounds)
assistant/assistant.py     # Assistant: handle()/confirm()/dry_run() + the safety gate + kill switch
assistant/planner.py       # T2: write/verify/register a human-approved generated routine
assistant/{events,models,prompt,module}.py  # event types, result models, system prompt, DI wiring
```

### `app_apps/app/` — app composition
```
app/service_config.py      # ServiceConfig feature flags (spectrometer, rotator, ..., assistant)
app/panel_window.py        # GUI panel host (blocked on the Base_Qt app-shell rework)
```

## `scripts/`
```
scripts/check.py           # THE verify command: mypy (linear/assistant/control) + unit suite
                           #   `--integration` also runs test/integration. Must print CHECK PASSED.
scripts/integration_demo.py# runnable closed-loop demo: prints trace tables + saves CSV/PNG
scripts/assistant_smoke.py # gated live Claude smoke test (needs ANTHROPIC_API_KEY)
```

## `test/`
```
test/unit/test_*.py        # ~190 stdlib unittest cases (devices, analysis, control, routines, assistant)
test/integration/          # in-process E2E: OpticalPlant drives the real fit/PID/routine/assistant chain
  optical_plant.py         #   stateful plant = fake ESP+RGV+spectrometer(+scope) producers
  test_closed_loop.py      #   control loops converge (delay/truncation/HWP)
  test_xcorr_pipeline.py   #   probe_xcorr_scan -> scope -> mean-of-top-N -> CSV
  test_assistant_pipeline.py #  natural-language command -> assistant -> runner -> plant
  test_scan_pipeline.py    #   spectrum scan routines
  report.py                #   interpretable trace tables + CSV/PNG for the demo
```

## Sibling repos (under `MilnerLab code/`, installed editable; **never edit Base_Core/Base_Qt**)
```
Base_Core   -> base_core    : framework (ipc, shm buffers, EventBus, TaskRunner, Routine, math)
Base_Qt     -> base_qt      : Qt UI framework (app shell) — currently mid-rework upstream
Devices     -> control_readout / oscilloscope / spm_002 : device drivers + workers + buffers
```

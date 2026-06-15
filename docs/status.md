# usCFG Software — Status & Handoff

> A plain-language snapshot of where the usCFG software stands, what's been built, and what has
> to happen elsewhere before the blocked pieces can move. Read [summary.md](summary.md) for the
> decision log, [architecture.md](architecture.md) for the technical design, and
> [routine_authoring_guide.md](routine_authoring_guide.md) for how to write a routine.
>
> Last updated: 2026-06-14.

---

## ⚠️ What my collaborator needs to do (read this first)

Everything below the line is built and tested in isolation (**191 unit tests, all green**), but
three upstream items gate end-to-end runs. None are bugs in our code — they're integration gates.
In rough priority order:

1. **Fix the `elliptec.base` import so the shared device subprocess starts.**
   `control_readout` pulls in `elliptec_ell14.py`, which still imports from the old `elliptec.base`
   location that moved during the repo consolidation. Until that import is reconciled, the shared
   control/readout subprocess can't fully start, so our command-style devices (ESP301, RGV100BL,
   picomotors, servo shutters) **can't be exercised together in a live subprocess** — they only
   run standalone against mocks. *Unblocks:* integrated device runs, and any routine that touches
   real hardware.

2. **Restore the app shell so `python -m app` launches.**
   The entry point references `base_qt.ui.apply` and the main-window class, both removed/WIP during
   the Qt rework. While that's the case the full application won't start, so **no GUI panels can be
   built or visually checked** — ours or anyone's. *Unblocks:* every UI panel (devices, spectrum,
   control), and exercising the routine layer + assistant from the running app.

3. **(For grating control) flesh out the ESP100 driver / expose a handle.**
   The grating stage (ESP100) is the contributor-owned empty stub. Our `lock_chirp_rate`/grating
   loop is **deliberately not built** because there's no driver or `EspHandle`-style handle to
   drive. Once a handle exists, the grating loop is a ~15-line copy of the existing control loops.

**Smaller:** reconcile the leftover `elliptec` entry in the `Devices` `pyproject` upstream (we
patched it locally for the editable install) so everyone's package list matches the new layout.

> Until items 1–2 land, we keep developing and testing standalone: mock drivers, fake handles,
> in-process shared-memory buffers, and the `.venv312` unit suite. Our code imports cleanly against
> the current `main` regardless.

---

## What's been built

### Foundation
Re-based onto the consolidated `main` (new `base_core.ipc` + `base_core.framework.shm` shared-memory
framework). Python 3.12 env (`.venv312`, the framework needs ≥3.11 for `typing.Self`). Every device
follows the same five-part shape: **buffer · events · service · worker handler · module**.

### Devices (mock-first; real-driver skeletons ready for bring-up)
| Device | For | How it runs |
|--------|-----|-------------|
| **ESP301** | probe / delay / truncation stages | command-style in the shared subprocess; polling thread reports position + motion-complete |
| **TBS2012C scope** | spectrum / XCORR traces | own streaming subprocess, bulk frames via shared memory |
| **RGV100BL** | half-wave-plate rotation | command-style, shared subprocess |
| **Picomotors** | mirror alignment | command-style, shared subprocess |
| **Servo shutters** | block one centrifuge arm for reference measurements | command-style stub; physical actuation done by hand for now |

### Analysis and control
- **Spectrum analysis** (`analysis/spectrum_info`) — envelope-bounded chirped-sinusoid model,
  synthetic generator, and fit → `SpectrumInfo` (ν₀, ν_start/ν_end, chirp, phase0, envelopes…).
- **Reference buffer** (`analysis/spectrum_info/reference.py`, **new**) — single drift-baseline
  reference spectrum + bounded rolling history of raw frames; pure numpy (M2.2).
- **XCORR** (`analysis/xcorr`) — cross-correlation, wavelength↔probe-delay calibration, append-only
  HDF5 store. Characterization use (weekly), not the daily loop.
- **Control primitives** (`control/`) — `PIDController` (deadband, slew-limit, anti-windup) +
  per-stage **ownership guard** (no two loops drive one stage).

### Linear routine-authoring layer (`routines/linear`) — Milestone R complete
A routine is now a plain blocking function: `@routine def fn(lab, **params)` over a `lab.*` facade
(probe/delay/truncation/hwp/picomotor/shutter/scope/spectrometer + record/save-CSV/fit/plot/sleep).
~10 lines instead of ~300. Single-flight runner with cooperative cancel + lifecycle events. The
async→sync bridge (reader-thread delivery) makes blocking calls deadlock-safe. See the
[authoring guide](routine_authoring_guide.md). **No `Devices` changes were needed** — the sync
command workers already emit completion telemetry.

### Control loops (M4) — buildable degrees of freedom done
Feedback loops are just routines. A reusable, hardware-free PID engine
(`control/lock.py::run_pid_lock`) backs three control routines, each verified against a simulated
plant (gains/signs are parameters tuned on real hardware):

| Routine | Locks | Measures | Actuates |
|---|---|---|---|
| `lock_central_frequency` | ν₀ | `nu0_thz` | delay stage |
| `lock_terminal_frequency` | ν_end | `nu_end_thz` | truncation stage |
| `lock_phase` | φ₀ | `phase0` | HWP rotation |

*Deferred (device gaps):* grating loop (ESP100 stub, see collaborator item 3) and QWP
ellipticity loop (ELL14 wiring, M4.7).

### LLM assistant (`assistant/`) — text core complete, off by default
A small Claude model maps a natural-language command to the **closed set of registered routines**
(or proposes a new routine for human review). Built fully testable without network or an API key:
- Tool schemas generated from the routine registry; param validation + bounds + one self-correction.
- **Safety:** off by default + runtime kill switch (`enable()`/`disable()`); only registered
  routines are callable; `safe`-tagged routines auto-run, everything that moves hardware needs
  human confirmation; dry-run; single-flight; generated code is never auto-run (gated
  write → `check.py` → register).
- Claude wiring (`ClaudeClient`, Haiku) is present but **lazy** — no network until enabled with a
  key. *Deferred:* the voice adapter (wake-word → STT → TTS) is designed as an edge adapter, built
  after the text core proves out.

### Testing
**191 unit tests, all green** on `.venv312` (synthetic/mock data, no hardware or full app needed).
One verify command gates everything:
```
.venv312/Scripts/python.exe scripts/check.py     # mypy (routines.linear, assistant, control) + unit suite -> CHECK PASSED
```

### Where the work lives
A clean linear stack of feature branches on top of `origin/main` (each contains the previous):
`feature/io-control-analysis` (devices + analysis + control) → `feature/routine-authoring` (linear
layer) → `feature/llm-assistant` (assistant + M4 loops + reference buffer). A `dev` integration
branch (= `origin/main`) exists to receive these via PR, keeping `main` pristine.

---

## What still needs real hardware (future, to coordinate at the lab)
Everything above is verified in software. These need a bench session and can't be done remotely:
- **Real driver bring-up** per device (ESP301 serial, TBS2012C VISA/SCPI, RGV100BL/XPS,
  picomotor 8742, servo actuation) — swap the mock for the real-driver skeleton, confirm motion +
  telemetry.
- **Control-loop tuning** — the PID gains/signs in the three lock routines are placeholders; tune
  on the real plant and confirm convergence + anti-spasm bounds.
- **ESP301 DC trajectory** (M5.1) — continuous-velocity probe scan with position read-back, vs the
  current automated stepping.
- **Spectrometer↔fit validation** — confirm the SPM-002 raw spectrum fits to sane `SpectrumInfo`.

When core software dev is called done, this becomes a per-device bring-up checklist (what each test
proves, and the order to do them in) to coordinate alongside the hardware being in place.

---

## Bottom line
Device, analysis, control, the linear routine layer, the buildable control loops, and the text-core
LLM assistant are **done and verified in isolation (191 tests)**. Two upstream reworks — the shared
device subprocess starting (item 1) and the app shell launching (item 2) — stand between "tested on
its own" and "running inside the app." Everything that doesn't depend on those keeps moving against
mocks.

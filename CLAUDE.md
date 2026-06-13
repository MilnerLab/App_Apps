# CLAUDE.md — App_Apps (usCFG software)

Control/readout/analysis software for the **ultrashort optical centrifuge (usCFG)**.
Read `docs/` first: `summary.md` (decision log), `architecture.md` (design),
`experiment_physics.md` (physics + hardware map + the `lab.*` action grammar),
`routine_authoring_plan.md` (linear routine layer + LLM roadmap), `tasks.md` (milestones),
`status.md` (what's built + collaborator blockers).

## Verify command (run before declaring work done)
```
.venv312/Scripts/python.exe scripts/check.py
```
Runs mypy (scoped to `app_apps.routines.linear`) + the full `test/unit` suite. Must print
`CHECK PASSED`.

## Environment
- **Use `.venv312`** (Python 3.12). The framework needs ≥3.11 (`typing.Self`). Run tests/
  imports/`check.py` with `.venv312/Scripts/python.exe`. Old `.venv` (3.10) is a fallback only.
- **Run from the `App_Apps` root** (so `app_apps` and `app` import). Sibling repos under
  `MilnerLab code/`: `Base_Core` (`base_core`), `Base_Qt` (`base_qt`), `Devices`
  (`control_readout`, `oscilloscope`, `spm_002`) — all installed editable.
- Tests are stdlib `unittest` in `test/unit/test_*.py`. mypy is a dev dependency.

## Hard rules (collaboration)
- **Additive-only.** Add new files/dirs; avoid modifying existing infra. New files don't
  conflict with the collaborator who reworked `main`.
- **Never edit `Base_Core` or `Base_Qt`.** If you truly need a new framework primitive, put
  it under `app_apps/` first and flag it loudly for later promotion.
- **`Devices` edits** are allowed but keep them additive (the collaborator shares that repo).
- The only shared App_Apps files we edit are `app.py` (the `modules=[...]` list) — append a
  clean block.
- **Don't push or open PRs unless asked. Never push to `main`.** Commit on feature branches;
  branch off the current feature branch, not `main` (it lacks our io/analysis/control work).
- Drop the `Co-Authored-By` trailer from commits unless asked otherwise.

## Branches (2026-06)
- App_Apps `feature/io-control-analysis` — device handles + analysis + control (pushed).
- App_Apps `feature/routine-authoring` — the linear routine layer (current; local only).
- Devices `feature/device-drivers` — all device drivers (pushed).
- `Base_Core`/`Base_Qt`/`Devices` track `main`; we never edit Base_Core/Base_Qt.

## Current state / blockers
- **Device layer** (ESP301, TBS2012C scope, RGV100BL, picomotors, servo shutters) built
  mock-first; **analysis** (spectrum_info, xcorr) and **control** (PID + ownership) built;
  **linear routine layer** R.1–R.5 built. ~128 unit tests green.
- **`python -m app` does not launch yet** — two collaborator WIP breakages on `main`:
  `elliptec.base` (control_readout subprocess) and `base_qt.ui.apply`/`lab_main_window`
  (shell + UI panels). Our code is import-clean regardless; develop/test standalone
  (mock drivers, fake handles, unit tests) until they land.

## Physics quick-reference
Optical centrifuge = rotating linear polarization; this lab's variant is a
constant-frequency (zero-acceleration) build (Michelson, two opposite-circular chirped arms).
Knobs ↔ hardware (dominant, with offsets/cross-coupling): delay→central freq ν₀; grating→
chirp rate/span; truncation→ν_end; HWP→initial phase; QWP→ellipticity. Readout: **SPM-002
spectrometer** (interferometric spectrum, direct) and **scope CH1 photodiode** (XCORR =
mean-of-top-20 per probe-delay step). Target this round: ν_start≈0 → ν_end up to ~200 GHz.
See `docs/experiment_physics.md`.

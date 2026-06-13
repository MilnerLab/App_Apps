# usCFG Software — Status & Handoff

> A plain-language snapshot of where the usCFG software stands, what's been
> built, and what currently has to happen elsewhere in the codebase before the
> blocked pieces can move. Read [summary.md](summary.md) for the decision log and
> [architecture.md](architecture.md) for the technical design.
>
> Last updated: 2026-06-12.

---

## The short version

The device layer, the analysis layer, and the core control primitives are
**built and tested in isolation** (~62 unit tests, all passing, on synthetic and
mock data). Everything we've written imports cleanly against the current `main`
framework.

Two pieces of the wider codebase are mid-rework right now, and until they settle,
anything that needs the *whole application* to launch — or the shared device
subprocess to come fully online — can't be exercised end-to-end yet. Those two
items are described at the bottom. Nothing about them affects the correctness of
what we've built; they're integration gates, not bugs in our code.

---

## What's been built

### Foundation

The project was re-based onto the consolidated `main` branch after the three
sibling repositories (`Base_Core`, `Base_Qt`, `Devices`) were reworked and merged
together. That rework replaced the old subprocess framework with a new one
(`base_core.ipc` plus shared-memory buffers in `base_core.framework.shm`), and we
adopted its conventions throughout.

A Python 3.12 environment (`.venv312`) was set up because the new framework needs
Python 3.11 or newer. Tests and imports run through it. The earlier 3.10
environment still exists as a fallback.

Every new device follows the same five-part shape the rest of the codebase uses:
a **buffer** (shared-memory layout), an **events** module (the messages it sends
and receives), a **service**, a **worker handler**, and a **module** that wires it
in. This keeps our additions consistent with the established structure.

### Devices

Five device groups are implemented, each with a **mock driver** used in the tests
and a **real-driver skeleton** ready for hardware bring-up later:

| Device | What it's for | How it runs |
|--------|---------------|-------------|
| **ESP301** | probe, delay, and truncation motion stages | command-style; lives in the shared control/readout subprocess, with a polling thread that reports position |
| **TBS2012C oscilloscope** | capturing spectrum and cross-correlation traces | its own streaming subprocess, moving bulk frames through shared memory |
| **RGV100BL** | rotating the half-wave plate | command-style; shared subprocess |
| **Picomotors** | mirror alignment | command-style; shared subprocess |
| **Servo shutters** | blocking a single centrifuge arm for reference measurements | command-style stub; the physical actuation is left as a to-do and is done by hand for now |

The guiding split: a device that streams high-rate data (the scope) gets its own
subprocess; a device that just takes commands shares the common control/readout
subprocess. The shared-subprocess devices were added alongside the existing
rotator without disturbing it.

### Analysis and control

- **Spectrum analysis** — a model of the measured spectrum as an
  envelope-bounded chirped sinusoid, a synthetic-data generator, and a fitting
  routine. This is what turns a raw trace into the physical parameters the
  control loops will eventually act on.
- **Cross-correlation (XCORR)** — correlation of two traces, a calibration that
  maps wavelength to probe delay, and an append-only store so calibrations are
  never overwritten. This is characterization work, used occasionally rather than
  in the daily loop.
- **Control primitives** — a PID controller with the safety features that keep a
  stage from over-reacting (deadband, slew limiting, anti-windup), and an
  ownership guard that guarantees no two control loops ever drive the same stage
  at once.

### Testing

Around 62 unit tests cover the device mocks, the spectrum model and fit, the
cross-correlation and calibration store, the PID controller, and the ownership
guard. They all pass. They run on synthetic and mock data, so they don't need any
hardware or the full application to be running.

### Where the work lives

All of it is committed but **not yet pushed**, kept on our two feature branches
(App_Apps and Devices). A rollback tag was placed before the big re-base so the
earlier state is always recoverable.

---

## What has to happen elsewhere before the blocked pieces can move

Two parts of the shared codebase are still being reworked. They sit upstream of
our integration, so they gate the end-to-end pieces — not because anything we
wrote is wrong, but because the application can't fully assemble until they land.

### 1. The application shell needs to launch again

The app's entry point currently refers to a couple of UI pieces
(`base_qt.ui.apply` and the main-window class) that were removed during the Qt
rework and haven't been replaced yet. While that's the case, the full application
won't start, which means **no GUI panels can be built or visually checked** —
ours or anyone's. Once the UI rework is finished and the shell can launch again,
the user-interface work for our devices and analysis is unblocked.

### 2. The shared device subprocess needs to come fully online

One module in the consolidated device code still imports from the old `elliptec`
location, which moved during the consolidation. Because of that, the shared
control/readout subprocess can't fully start. Our command-style devices (ESP301,
the rotator, the picomotors, the shutters) import and test cleanly on their own,
but they can't be exercised *together in a running subprocess* until that import
is reconciled with the new layout. Once it is, integrated device runs are
unblocked.

### A smaller related note

The Devices package list needed a small local correction (a leftover `elliptec`
entry that no longer matches the consolidated layout) for the editable install to
work. It's worth reconciling that upstream so everyone's package list matches the
new structure.

### Intentionally left to others

- The **ESP100 (grating)** device is a stub owned elsewhere and was left
  untouched.
- The **quarter-wave-plate** control reuses the existing ELL14 rotator; its
  control logic is deferred to a later milestone.

---

## Bottom line

The device, analysis, and control-primitive layers are done and verified in
isolation. Two upstream reworks — the application shell relaunching, and the
shared device subprocess coming online — are what stand between "tested on its
own" and "running inside the app." Everything that *doesn't* depend on those two
can keep moving against mocks in the meantime.

# Writing a Routine — Author's Guide

> Write an experiment routine in ~5 minutes, no OOP required. A routine is a plain Python
> function; every device call **blocks until it finishes**, so you write the experiment
> top-to-bottom like a script. This guide is also written to be pasted into an LLM as context
> for generating routines.
>
> Physics + hardware background: [experiment_physics.md](experiment_physics.md). Design/
> internals: [routine_authoring_plan.md](routine_authoring_plan.md).

---

## 1. The 5-minute version

Create a file in `app_apps/routines/linear/scripts/` (e.g. `my_scan.py`):

```python
from app_apps.routines.linear.registry import routine


@routine("my_scan")
def my_scan(lab, start_mm, stop_mm, step_mm):
    """Scan the probe stage and record the XCORR signal at each position."""
    for x in lab.frange(start_mm, stop_mm, step_mm):
        lab.probe.move_to(x)          # blocks until the stage settles
        lab.record(probe_mm=x, xcorr=lab.xcorr_point())
    return lab.save("my_scan.csv")
```

Then make it self-register by importing it in
[`scripts/__init__.py`](../app_apps/routines/linear/scripts/__init__.py):

```python
from app_apps.routines.linear.scripts import my_scan  # noqa: F401
```

That's it. The routine now appears to the app and can be launched.

### Running it
A routine runs in the background via the runner (the UI / a caller does this):

```python
runner = container.get(LinearRoutineRunner)
runner.launch("my_scan", start_mm=0.0, stop_mm=5.0, step_mm=0.05)
```

`launch()` returns immediately; the routine runs on a background thread. `runner.stop()`
cancels it. Lifecycle is broadcast as `RoutineStarted` / `RoutineCompleted` / `RoutineFailed`
/ `RoutineCancelledEvent` events for any UI/observer.

---

## 2. The mental model (4 rules)

1. **The first argument is always `lab`.** Everything you do goes through it. Your own
   parameters come after and are passed at launch (`runner.launch("name", a=1, b=2)`).
2. **Device calls block.** `lab.probe.move_to(3.0)` does not return until the move is done.
   Write steps in order; no callbacks, no `await`.
3. **One routine runs at a time** (single-flight). Don't rely on two routines overlapping.
4. **Cancellation is cooperative.** `stop()` makes the *next* blocking `lab` call raise and
   unwind cleanly. In a long pure-Python loop with no `lab` calls, sprinkle
   `lab.checkpoint()` so it can still be cancelled.

Don't use `time.sleep` (use `lab.sleep`) and don't touch the event bus or device handles
directly — only use `lab`.

---

## 3. The `lab` verb reference

### Motion stages (block until settled)
| Verb | What it does |
|------|--------------|
| `lab.probe.move_to(mm)` → `float` | Move the probe stage to an absolute position; returns settled position. |
| `lab.probe.move_by(delta_mm)` → `float` | Relative move. |
| `lab.probe.position` → `float \| None` | Last polled position (non-blocking; `None` until first update). |
| `lab.delay.move_to(mm)` / `.move_by` / `.position` | Delay stage (dominant **central-frequency** knob). |
| `lab.truncation.move_to(mm)` / `.move_by` / `.position` | Truncation stage (**terminal frequency** ν_end). |

### Rotators
| Verb | What it does |
|------|--------------|
| `lab.hwp.rotate_to(angle)` | Rotate the half-wave plate (initial phase). `angle` is an `Angle`. |
| `lab.hwp.home()` | Home the HWP. |
| `lab.qwp...` | **Not available yet** — raises `LabUnavailable` (QWP wiring deferred to M4.7). |

`Angle` comes from the framework: `from base_core.math.models import Angle` and
`from base_core.math.enums import AngleUnit` → `Angle(45, AngleUnit.DEG)`.

### Picomotors & shutters
| Verb | What it does |
|------|--------------|
| `lab.picomotor.step(axis, steps)` → `int` | Open-loop mirror step; returns accumulated step count. |
| `lab.shutter.close(arm)` | Block a centrifuge arm (for single-arm reference). |
| `lab.shutter.open(arm)` | Unblock the arm. |

### Readout (block until data arrives)
| Verb | What it does |
|------|--------------|
| `lab.scope.capture(channel=0)` → `np.ndarray` | One oscilloscope trace; `channel=None` returns all channels `(channels, n_samples)`. CH1 (`channel=0`) is the XCORR photodiode. |
| `lab.xcorr_point(channel=0, n_top=None)` → `float` | One XCORR scalar = mean of the `n_top` highest samples of a fresh capture (default 20). |
| `lab.spectrometer.read()` → `SpectrumReading` | One SPM-002 spectrum: `.wavelengths`, `.intensities` (1-D arrays). |
| `lab.fit_spectrum(reading)` → `SpectrumInfo` | Fit a `SpectrumReading` to the chirped-sinusoid model. |

`SpectrumInfo` fields you'll usually want: `nu0_thz` (central freq), `nu_start_thz`,
`nu_end_thz`, `g2` (chirp), `amp_lower` / `lower_envelope_metric` (ellipticity), `phase0`,
`fit_residual`. Span = `nu_start_thz - nu_end_thz`.

### Flow & data helpers
| Verb | What it does |
|------|--------------|
| `lab.frange(start, stop, step)` | Inclusive float range for sweeps; checks cancellation each step. |
| `lab.sleep(seconds)` | Cancellable sleep (never use `time.sleep`). |
| `lab.checkpoint()` | A cancellation point for long pure-CPU loops. |
| `lab.record(**fields)` | Append one row of results. |
| `lab.records` → `list[dict]` | Copy of the rows so far. |
| `lab.save(path)` → `str` | Write rows to CSV (columns = union of keys). Returns the path. |
| `lab.plot(x, y, save_path=None)` | Plot recorded `y` vs `x`; saves a PNG if `save_path` given (needs matplotlib). |
| `lab.log(message)` | Log a progress line. |
| `lab.params` → `dict` | The parameters this run was launched with. |

If a device isn't wired into the current app, its verb raises `LabUnavailable` with a clear
message — your routine fails fast rather than hanging. Every blocking call also has a timeout
(raises `RoutineTimeout`) so a dead device can't hang you forever.

---

## 4. Recipes

**Scan + save + plot**
```python
@routine("delay_freq_sweep")
def delay_freq_sweep(lab, start_mm, stop_mm, step_mm):
    for x in lab.frange(start_mm, stop_mm, step_mm):
        lab.delay.move_to(x)
        info = lab.fit_spectrum(lab.spectrometer.read())
        lab.record(delay_mm=x, nu0=info.nu0_thz, span=info.nu_start_thz - info.nu_end_thz)
    lab.save("delay_freq_sweep.csv")
    lab.plot("delay_mm", "nu0", save_path="delay_freq_sweep.png")
```

**Validate-and-repeat (overnight)**
```python
@routine("overnight")
def overnight(lab, setpoints_mm, start_mm, stop_mm, step_mm, min_xcorr=0.05):
    for sp in setpoints_mm:
        lab.checkpoint()
        lab.delay.move_to(sp)
        first = len(lab.records)
        for x in lab.frange(start_mm, stop_mm, step_mm):
            lab.probe.move_to(x)
            lab.record(delay_mm=sp, probe_mm=x, xcorr=lab.xcorr_point())
        peak = max((r["xcorr"] for r in lab.records[first:]), default=0.0)
        if peak < min_xcorr:
            lab.log(f"weak pass at {sp} mm (peak {peak:.3g}); consider retaking")
    return lab.save("overnight.csv")
```

See the shipped examples in
[`scripts/probe_scan.py`](../app_apps/routines/linear/scripts/probe_scan.py).

---

## 5. Common mistakes
- **Using `range()` over a huge sweep with no `lab` calls** → can't be cancelled mid-loop.
  Use `lab.frange` or add `lab.checkpoint()`.
- **`time.sleep(...)`** → blocks cancellation. Use `lab.sleep(...)`.
- **Calling `lab.qwp` / a device that isn't composed in** → `LabUnavailable`. Check what's
  wired for your run.
- **Assuming a move "succeeded" means the value is right** → a *failed* device command
  currently surfaces as `RoutineTimeout`, not a typed error (known v1 limitation).
- **Heavy CPU between device calls** → fine, but it won't cancel until the next `lab` call or
  `lab.checkpoint()`.

---

## 6. Paste-in spec for an LLM

> You write a Python function for a lab-control system. Rules: the function's first parameter
> is `lab`; your parameters follow and are supplied at launch. Every `lab` device call blocks
> until done — write steps sequentially, no async/callbacks. Decorate with
> `@routine("name")` (import `from app_apps.routines.linear.registry import routine`). Use
> only these verbs:
>
> Motion (block, mm): `lab.probe.move_to(x)/.move_by(d)/.position`, same for `lab.delay`,
> `lab.truncation`. Rotation: `lab.hwp.rotate_to(Angle(deg, AngleUnit.DEG))`, `lab.hwp.home()`
> (`lab.qwp` is unavailable). `lab.picomotor.step(axis, steps)`. `lab.shutter.close(arm)` /
> `lab.shutter.open(arm)`. Readout (block): `lab.scope.capture(channel=0)` (np.ndarray),
> `lab.xcorr_point(channel=0, n_top=20)` (float), `lab.spectrometer.read()` (→ `.wavelengths`,
> `.intensities`), `lab.fit_spectrum(reading)` (→ `.nu0_thz`, `.nu_start_thz`, `.nu_end_thz`,
> `.g2`, `.amp_lower`, `.phase0`, `.fit_residual`). Helpers: `lab.frange(a, b, step)`,
> `lab.sleep(s)`, `lab.checkpoint()`, `lab.record(**fields)`, `lab.records`,
> `lab.save(path)→csv`, `lab.plot(x, y, save_path=None)`, `lab.log(msg)`, `lab.params`.
> Never use `time.sleep` (use `lab.sleep`) and never touch the event bus or device handles
> directly. Hardware mapping: delay→central frequency ν₀, grating→chirp/span, truncation→ν_end,
> HWP→initial phase. XCORR = scope CH1 photodiode; spectrum = SPM-002. Output a single
> `@routine`-decorated function with a one-line docstring.

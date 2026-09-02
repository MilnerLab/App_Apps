# Phase-control / device-UI work plan

**ALL THREE SECTIONS ARE IMPLEMENTED** on `feat/phase-template`, one commit each. The plan
below is kept as the record of WHY, since the measurements in it are the justification for
every threshold that shipped. Status legend: **SETTLED** = design agreed, **DONE** = shipped.

| section | commit | notes |
|---|---|---|
| 1. Visibility abort + ack fix | `phase: abort the fit on washed-out fringes...` | **DONE.** New `fringe_visibility.py`; gate in `PhaseTracker.update`; `min_visibility` config field. Ack moved before the fit. Recovery scan bounded at 0.5 s. |
| 2. Device panel | `devices: a real Devices panel...` | **DONE.** New `DevicesView`, `MotionControls`, `MotionViewModel`. RGV interlock against `PhaseControlService`. No speed, as decided. |
| 3. Frozen template | `phase: frozen-template tracking...` | **DONE.** New `phase_template.py`, `template_tracker.py`, `test/phase_template_test.py`. |

Two deviations from the plan as written, both forced and both documented in the code:

* **The truncation-recovery budget could not go in `fringe_core.py`.** The standalone it must
  stay byte-identical to lives on a `D:` path that is not present on this machine, so the
  copy-across the parity rule demands is impossible here. Instead `analyze_trace` calls
  `fc.analyze(..., recover=False)` and runs the budgeted scan in `fringe_fit.py`, delegating
  every decision back to fringe_core (`_analyze_once`, `_explains`, `TRUNCREC_*`). No math is
  restated. **If the standalone becomes reachable, move it there and pass `recover=True`.**
* **Reference capture stamps `integration_ms` / `averages` main-side,** in
  `PhaseStabilizationHandle`, because the spectrometer settings live on
  `SpectrometerWorkerHandle.config` and the phase subprocess cannot see them.

Measured here, and differing from the numbers below (which were taken on the real bench and
are the ones the thresholds are calibrated to -- these are only a sanity repro on synthetic
traces): the visibility index reads 0.16 at V_true 0.15 and floors at 0.041, so 0.12 still
separates; the closed-form fit costs ~300 us rather than 99 us on this machine; and the rigid
1-parameter fit's phase moves ~10 mrad under 0.3x-2x intensity and +-50 counts of baseline,
not the sub-milliradian the 3-term comparison suggested. 10 mrad is 0.6 deg against a
`PHASE_TOLERANCE` of 10 deg, so the rigid freeze still stands.

Status legend: **SETTLED** = design agreed. **OPEN** = needs a
decision before code is written.

Two items from this round are already **DONE** and on disk:

* FWHM marker lines on the spectrometer plot (`stabilization_control_view_model.py`)
  — two cyan dotted verticals at `fringe_core.fwhm_band_nm(pU)`, the same band the
  `f_cfg` label quotes. Hidden, never stale, when there is no fit.
* Correction-sign toggle (`invert_correction`) — config field, plumbed to
  `PhaseCorrector`, exposed in the Control loop group, editable while running.

---

## 1. Fit abort on low fringe visibility — **SETTLED**

### The problem, measured

Synthetic 800 nm trace, 700 px, bright (300 counts over a 155-count floor), read
noise sigma 4, fringe visibility swept. `fringe_core.analyze` wall time:

| V_true | fit time | status returned |
|-------:|---------:|-----------------|
| 0.60   |   381 ms | ok              |
| 0.30   |   273 ms | ok              |
| 0.15   |   260 ms | ok              |
| 0.08   |   357 ms | ok              |
| 0.04   | 12 523 ms| ok              |
| 0.02   | 21 496 ms| ok              |
| **0.00** | **46 718 ms** | **ok**   |

This is a cliff, not a slope, and it lands exactly on the reported failure: while
settings change, the fringes fly and average out, leaving a clean bright Gaussian
with no oscillation. That is the V=0 row.

Two distinct problems in that row, and the second is the worse one:

1. 47 seconds of CPU inside one frame.
2. It returns **`status="ok"`**. The pipeline fits noise, passes its own gates, and
   hands the control loop a confident phase with no physical basis.

The existing `dead_window` guard does not catch this. Its thresholds
(`DEAD_GAP_FRAC = 1e-3`, `DEAD_OSC_STD = 1e-6`, `fringe_core.py:1170`) are
"mathematically zero", not "physically useless", and it only runs *after* two
Nelder-Mead envelope fits — i.e. after the cost has already been paid.

### The gate

A visibility measure with **no optimizer in it**: two filter passes plus a
median-absolute-deviation noise estimate. Measured cost **~1.9 ms**, against a
260 ms good fit (0.7% overhead) and a 47 000 ms bad one.

    sigma_n = 1.4826 * MAD(second difference of y) / sqrt(6)  # fringes are smooth
                                                              # pixel-to-pixel, noise is not
    dc      = gaussian_filter1d(y, 0.05*N)                    # envelope midline
    ac2     = uniform_filter1d((y-dc)^2, 0.05*N)              # = A^2/2 + sigma_n^2
    amp     = sqrt(max(2*(ac2 - sigma_n^2), 0))               # noise-corrected fringe amp
    above   = dc - percentile(y, 5)                           # envelope over continuum
    V       = median( amp / above )  over  above > 0.5*max(above)

The noise subtraction is what makes it usable: without it the estimator floors out
at ~0.04 on a fringe-free trace and the good/bad classes stop separating.

Measured on the bright sweep above:

| V_true | V_meas | verdict at threshold 0.12 |
|-------:|-------:|---------------------------|
| 0.15   | 0.2461 | fit (260 ms)              |
| 0.08   | 0.1398 | fit (357 ms)              |
| 0.04   | 0.0805 | **abort** (was 12.5 s)    |
| 0.02   | 0.0504 | **abort** (was 21.5 s)    |
| 0.00   | 0.0458 | **abort** (was 46.7 s)    |

Threshold **0.12**, a factor ~1.2 below the last good point and ~1.5 above the
fringe-free floor. The estimator is biased high (V_true 0.60 reads 0.76) — it is a
monotone contrast index, not a calibrated visibility, and must be documented as
such. It is a gate, so monotonicity is all that is required.

Light-level dependence was checked (10x and 33x dimmer): the metric is
light-independent down to ~30 counts, well below normal operation. It is not an
SNR — an SNR measure was tried first and rejected precisely because it tracked
brightness rather than contrast.

### Where it goes

**`PhaseTracker.update`, App-side, before `analyze_trace` is called.**

NOT in `fringe_core.py`. That file is a verbatim copy of the standalone and its
header forbids patching this side; changes must be made in the standalone,
re-harnessed, and copied across whole. The gate is control-loop policy, not
analysis, so `PhaseTracker` is where it belongs anyway, and putting it there means
zero parity risk.

Behaviour on abort: no fit, no commit, no correction, `update` returns False. The
spectrometer stream is untouched and keeps running; the loop simply holds until
visibility returns. Log rate-limited so the operator sees
`holding: visibility 0.05 < 0.12` rather than a flood.

* New config field `min_visibility: float = 0.12`, exposed in the Tracking group,
  editable while running (it must be tunable against a live trace).
* Serialization back-compat: absent in an old config -> default.

### Also fix, same area

* **Bound the truncation-recovery scan.** On a frame that fails `_explains`,
  `analyze` runs up to `TRUNCREC_MAX_NM/TRUNCREC_STEP_NM` = 16 cuts x 2 sides =
  **32 extra full `_analyze_once` calls** (`fringe_core.py:2114`). The scan is
  ordered smallest-cut-first and stops at the first success, so a scan that finds
  something terminates early — a scan that runs all 32 was going to fail anyway. A
  wall-clock budget (~0.5 s) caps the worst case and costs the good path nothing.
  Prefer this over an iteration cap, which would also throttle successful
  recoveries.
  NOTE: this is inside `fringe_core.py`; see the parity constraint above. It has to
  be made in the standalone and copied across, or passed in as a parameter from the
  App side.
* **Ack the spectrum slot before fitting, not after.** `phase_tracking` is a
  registered `SlotCoordinator` consumer and acks only after the fit completes
  (`phase_stabilization_worker.py:137`). No ack -> no `SpectrumAvailable` -> the
  live plot and every other consumer stall for the fit's whole duration. That is
  why a slow fit freezes the *application*, not just the loop. Copy the arrays out,
  ack, then fit. Drop-stale coalescing (`_latest_item_id`) already handles the
  staleness this admits, and `spectrum_recorder.py` already follows exactly this
  pattern.

The visibility gate alone removes the common case. The ack fix is what stops any
future slow fit from freezing the UI. Both are wanted.

---

## 2. Device panel — **SETTLED**

Everything requested already exists as a `ViewHost` window off the Devices menu
(`app_apps/app/shell.py:57`). The ask is to promote them into a real panel and give
the thin ones actual controls.

Current state:

| device | today | needed |
|---|---|---|
| Picomotors (8742) | complete — arrow pads, increments, presets, per-axis counters, zero | move into the panel unchanged |
| UTS150CC / MFA-CC / FMS300PP | start/pause/resume/stop **only** | readout, relative move, absolute move, home, speed |
| RGV100BL | start/pause/resume/stop **only** | angle readout, relative, absolute, home, + stabilization interlock |
| ELL14 | relative rotate + home | angle readout, absolute move |

Register as a dockable panel in `AppPanelWindow._build_panels`, tabbed with Phase
Control and XCORR Display. Picomotors go in the same panel.

**Handles already expose everything needed for position work** — `move_to`,
`home`, `get_position`, and a published `NewXPosition` event per stage. So the
position/move/home half is pure App-side UI with no contract change.

**Stage speed is NOT available and needs Devices-repo work.** The device layer has
it (`esp_301/controller.py:230 set_velocity`, `rgv100bl_device.py:53 set_velocity`,
`ell14/device.py:74 set_speed`) but there is **no IPC message for it** — e.g.
`esp_301/uts150cc/messages.py` carries only Move / Home / GetPos. Exposing speed
means adding a message + worker handler per device in `C:\git\Milner_Lab\Devices`,
then a handle method App-side. **DECIDED: ship the panel without speed.** No
Devices-repo change in this work; the panel is App-side only.

### RGV interlock

Moving the RGV by hand while stabilization is running fights the loop. Required
behaviour: confirmation dialog; on confirm, **turn stabilization off first, then
move**. Not "move and hope" — the loop must be stopped before the plate is touched.

Wiring: `PhaseStabilizationHandle.state` gives the running state and `.stop()`
stops it; `PhaseControlService.stop_worker()` stops whichever worker is active. The
RGV view model needs one of those injected. Prefer the service, so the interlock
also covers the envelope worker, which drives the same plate.

---

## 3. Frozen-template phase tracking — **SETTLED**

Shape is measured once from 10 spectra; thereafter only the phase is fit against
that frozen template.

### Reference capture — the cold path is UNCHANGED

The full existing pipeline stays exactly as it is for capture: optimizer, seeds,
multi-start, BIC order selection, truncation recovery, and the usual per-trace
accept/reject gate (`StabilizationConfig.accepts`) all still run, **per trace**,
on each of the 10.

**CONFIRMED:** collect 10 **consecutively** accepted traces, average those, fit
the average. A rejection resets the count to zero — the run must be unbroken.

That consecutiveness rule also covers a hazard that would otherwise need its own
mechanism: averaging traces whose phase drifted between them washes the fringes
out of the average (the section-1 failure), and the template would then be fit to
noise and trusted indefinitely. An unbroken run of 10 accepted traces plus the
visibility check on the averaged trace before fitting is sufficient; no extra
phase-spread test is needed.

Nothing about the cold path changes. It is not made faster, not made looser, and
its rejection behaviour is untouched.

* Buttons: **Capture reference** (collect next 10 accepted, fit, install),
  **Save reference** (write template to file), **Recall reference** (load a saved
  template, overriding the current one).
* Stored template: `l0`, `c1..c3`, envelope (`pU`, `pLn`), plus capture UTC and
  spectrometer integration/averaging so a recalled template can be checked against
  the machine it is loaded onto.

### Per-frame phase fit

Freeze everything — envelope, carrier, chirp, `l0` — and leave **one** free
parameter, the phase. That fit has an exact closed form:

    w = (y - mid) * half
    C = w . cos(Phi)
    S = w . sin(Phi)
    delta = -atan2(S, C)          amplitude = hypot(C, S)

Measured against the true least-squares minimiser of the same 1-parameter model
(700 px, noise sigma 4):

| property | result |
|---|---|
| agreement with brute-force minimiser | **0.00 mrad** — same answer to machine precision |
| accuracy vs truth | **4.2 mrad (0.24 deg)** over -3..+3 rad |
| cost | **99 us/frame** vs 260 ms (good cold fit) to 46 700 ms (washed-out) |
| amplitude when fringes vanish | drops **226x** (2 811 041 -> 12 392) |

No optimizer, no seed, no iteration — not as a simplification, but because the
1-parameter problem is analytically solvable. Every trace during the window is fit
this way, with the same template and only the phase differing, as specified.

The returned amplitude is a direct per-frame fringe-strength measure and can serve
as the in-loop gate, so section 1's 1.9 ms metric is needed only to protect
reference capture.

### The loop

* Every incoming trace: closed-form `delta` against the frozen template.
* Apply the gain to those phases, weighting recent traces more:
  `dhat <- (1-g)*dhat + g*delta_i`, computed on the unit vector so it is
  circular-safe (phase is mod 2pi; the arithmetic mean of 0.01 and 6.27 rad is pi).
* Every n seconds (default 15, UI-tunable) issue one correction that drives `dhat`
  to zero.
* Flush `dhat` on template re-capture, target change, config change, pause, stop.

### What `CONVERSION_CONST = 1/4` is

It converts a phase error in degrees into a half-wave-plate rotation in degrees:

    hwp_deg = sign * phase_error_deg * (1/4) * gain

The only justification anywhere in the codebase is the original comment in
`Phase_Control/.../phase_corrector.py:15`:

    CONVERSION_CONST = 1 / 4       # depends on optics
    CORRECTION_SIGN  = -1          # depends on QWP orientation

**Derivation (mine, not in the code).** A half-wave plate with its fast axis at
angle theta converts LCP -> RCP with phase factor `exp(-2i*theta)` and RCP -> LCP
with `exp(+2i*theta)`. The RELATIVE phase between the two circular components
therefore changes by **4*theta** when the plate rotates by theta. Inverting:

    d(theta_hwp) = d(phase) / 4        ->    CONVERSION_CONST = 1/4

This is a geometric (Pancharatnam-Berry) phase. It depends only on rotation
angle — not on wavelength, plate material, retardance error, or alignment — so
the MAGNITUDE 1/4 is exact rather than a calibration. What the geometry does NOT
fix is the sign, because which arm carries which handedness is set by the quarter
wave plate. That is exactly the behaviour reported from the bench (a stable lock
pi away from the setpoint), it is what the original author's comment says, and it
is why a sign TOGGLE is the correct fix and a magnitude calibration is not.

Caveat: the derivation assumes the measured spectral fringe phase is the relative
phase between the two circular components 1:1. If the plate acts on only one arm,
or the interferogram measures a different difference, the factor could be 1/2 or 2
instead. That is a property of the bench layout, not of the software.

**One measurement settles it.** Command a known HWP rotation `d_theta` with the
loop stopped and read the phase change `d_delta` off the template fit. If 1/4 is
right, `d_delta = 4 * d_theta`. Seconds of work, and it retires the only remaining
risk in removing the gain.

### Re-capture trigger — **SETTLED**

`1/4` is **CONFIRMED** correct (see derivation above). Probe stage is irrelevant.
**Stabilization MUST keep running through routines** — so the template cannot
simply be suspended for the duration of a scan; it has to be re-captured whenever
the shape actually changes, automatically.

Two triggers, both required:

**1. Command-driven (primary, zero lag).** A commanded **delay** (`RequestMoveMfacc`)
or **grating** (`RequestMoveUts150cc`) move invalidates the template immediately.
Probe moves (`RequestMoveFms300pp`) do not. Deterministic, no threshold, and it
fires before the bad data arrives rather than after.

**2. Smoothed-Hilbert shape check (per trace).** Compare each incoming trace's
smoothed instantaneous frequency against the 10-average template's:

    n     = (y - mid)/half                       # frozen envelope
    f     = d/dlambda unwrap(angle(hilbert(n))) / 2pi
    f_sm  = fc.smooth_absf(x, f)                 # existing helper, SMOOTH_FRAC=0.06
    mismatch = rms(f_sm - f_ref) / mean|f_ref|   over the core

Instantaneous frequency is the derivative of phase, so a constant phase offset
cancels exactly — the metric sees SHAPE and is blind to the thing that changes
every frame. Measured:

| condition | mismatch |
|---|---:|
| same shape, phase +0.0 | 0.0029 |
| same shape, phase +1.5 | 0.0029 |
| same shape, phase -3.0 | 0.0023 |
| same shape, phase +pi  | 0.0014 |
| delay move: c1 +1%  | **0.0109** |
| delay move: c1 +5%  | **0.0508** |
| delay move: c1 +25% | **0.2493** |
| grating: c2 x2      | **0.0464** |
| grating: c2 x0.5    | **0.0231** |
| grating: c2 -> 0    | **0.0468** |
| grating: c3 x5      | **0.0367** |

Phase-invariant to ~0.003 across a full pi; the smallest shape change tested sits
3.8x above that floor. Threshold **~0.008-0.010**. Cost ~1 ms/frame.

This is what makes running through routines possible: the loop re-captures on a
real shape change rather than being suspended, and trigger 1 means it does not
have to wait for the mismatch to be observed on corrupted data.

**While invalid: hold, do not correct.** Recovery is automatic once 10
consecutively accepted traces arrive (~5 s at 2 Hz).

### Two hazards that automatic re-capture introduces — MUST be handled

**(a) Phase continuity across a re-capture.** The closed-form `delta` is measured
relative to the template's own `Phi`, which carries its own `c0`. Track the
ABSOLUTE phase:

    phase_at_ref = phase_poly(template_csig, lambda_ref - l0) + delta

Do this and a re-capture is continuous, because both templates describe the same
physical phase at `lambda_ref`. Track bare `delta` instead and every re-capture
silently redefines zero, the next window reads the jump as real error, and the
loop commands a large spurious correction — at every setpoint of every scan.

**(b) Global sign ambiguity — the serious one.** The cold fit is sign-ambiguous:
`signal_model` is `mid + half*cos(Phi)` and cosine is even, so `Phi -> -Phi` is a
bit-identical fit and which one the optimiser lands on is a seed accident. Measured
on a sign-flipped template, same trace:

    phase vs template +Phi :  +0.7020
    phase vs template -Phi :  -0.7020        <-- loop drives the WRONG WAY

And critically, **the Hilbert check cannot catch it**: mismatch between the two
templates is **0.00000**, because `smooth_absf` takes `|f|`. So this hazard is not
covered by trigger 2 and needs its own guard.

Guard: enforce sign continuity at capture — if `sign(c1_new) != sign(c1_old)`,
flip the new template (`Phi -> -Phi`) before installing it. Deterministic, free,
and it only has to hold relative to the previous template, never absolutely.
Without it, one unlucky re-capture mid-scan inverts the loop and it locks pi away
(exactly the bench symptom that motivated the sign toggle).

### Deliberately NOT included

Raised earlier, **not requested**, and therefore not planned. Listed only so they
are not silently lost:

* Letting envelope amplitude/baseline float as extra linear terms. **Tested and
  rejected as unnecessary** — a 3-term variant matched the rigid 1-parameter fit
  to within 0.1 mrad under 0.3x-2x intensity scaling and +-50 counts of baseline
  shift, and was slower (232 us vs 99 us). Phase enters only through the
  correlation term, so intensity drift cannot bias it. The rigid freeze that was
  asked for is correct.
* Automatic staleness detection / residual warning on the template.
* Changing `PHASE_TOLERANCE` from 10 deg.
* Self-calibrating `CONVERSION_CONST` from commanded-vs-observed phase change.
  Largely retired by the geometric-phase derivation above (the magnitude is exact,
  only the sign is setup-dependent, and the sign already has a toggle). The
  one-shot bench measurement described above is the cheaper way to confirm it.
* Low-coherence (resultant-length) gating of the window.

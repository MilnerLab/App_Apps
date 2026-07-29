# Phase Stabilization — Fringe Fit

How the phase-stabilization loop reads the interferometer's phase off a single
spectrum and turns it into a small rotation of a half-wave plate.

All of the analysis math lives in `fringe_core.py` (the single source of truth);
`fringe_fit.py` is a thin adapter that translates between the app's data objects and
`fringe_core.analyze()`. The control loop itself is `phase_tracker.py`,
`phase_corrector.py`, and `phase_stabilization_worker.py`.

---

## 1. What we are measuring

A spectrometer looks at an interferometer with two arms. Each arm alone would give a
smooth, roughly Gaussian bump of light. Because the two arms interfere, the recorded
spectrum instead shows ripples ("fringes") riding on top of that bump:

```
I(λ) = mid(λ) + half(λ)·cos(Φ(λ))
```

- `mid(λ) = ½(U + L)` is the local average brightness and `half(λ) = ½(U − L)` is the
  local ripple height, where `U` and `L` are the upper and lower outlines of the
  fringes (each modelled as a Gaussian). `U` traces the fringe peaks, `L` the troughs.
- `Φ(λ)` is the **phase** — the quantity we actually want. How it varies with
  wavelength is described by a cubic polynomial in `u = λ − l0`, where `l0` is a
  reference point near the middle of the data (the centre of the well-fringed region):
  ```
  Φ(u) = c0 + c1·u + c2·u² + c3·u³
  ```
  In words: `c0` is the phase at the reference point — the one number the loop is trying
  to hold steady. `c1` sets how fast the fringes oscillate. `c2` says how that spacing
  changes across the spectrum (the fringes get wider or narrower — "chirp"). `c3` is a
  smaller, third-order version of the same idea, conventionally called third-order
  dispersion (TOD).
- The **local fringe frequency** — how many ripples per nanometre you see at a given
  wavelength — is `f(u) = Φ'(u)/2π = (c1 + 2c2·u + 3c3·u²)/2π`, in cycles/nm. It carries
  a sign, and if the chirp is strong enough it can pass through zero. At that wavelength
  the fringes momentarily stop oscillating and then start again running the other way; we
  call that point a **null**.

The loop uses only `c0`, at a chosen reference wavelength. The other coefficients
describe the shape of the laser pulse and are reported for diagnostics.

## 2. The analysis pipeline (`analyze()`)

One spectrum in, one fit out. In order:

1. **Look for a clipped arm.** If one interferometer arm has been cut off at one end of
   the spectrum, the fringes disappear there. Find that band and drop it (see
   *Telling a clipped arm from a null* below).
2. **Fit the fringe outlines.** Fit the upper and lower Gaussian outlines using a
   lopsided error measure (a *quantile* or "pinball" loss, at quantile ≈ 0.91) so the
   upper curve is pulled up onto the fringe peaks rather than through their middle. The
   vertical offset of the Gaussians is constrained to a background level measured from
   the full spectrum (the *baseline anchor*).
3. **Crop to the well-fringed region and normalize.** Keep the part of the spectrum where
   the fringes are strong — where the gap between the two outlines is above
   `TRUNC_THRESHOLD` of its largest value — and rescale the data to
   `n = (y − mid)/half`, which then oscillates between −1 and +1.
4. **Make a first guess at the phase.** A Hilbert transform of the normalized fringes
   gives the local fringe frequency, but without its sign — only `|f|`. From that, build
   two candidate starting guesses:
   - a **two-trim guess**: throw away the extremes of the phase range, then fit a
     quadratic to what is left. This is right wherever the phase climbs steadily.
   - a **signed null-flip guess**, built at each dip in `|f|`, which restores the correct
     sign of the chirp on both sides of a null. It is only accepted when it actually
     reduces the mismatch with the measured fringes.

   The polynomial order (just `c1`; plus `c2`; plus `c3`) is then chosen by the Bayesian
   information criterion (**BIC**) — a standard rule that only keeps an extra term if it
   improves the fit by more than you would expect from noise alone. So a spurious `c3`
   is rejected without anyone tuning a threshold.
5. **Refit the cubic** against the raw counts, holding the fringe outlines fixed.
6. **Decide how much to trust the answer.** Convert the fit's uncertainties into an
   uncertainty on the phase at the reference wavelength, and decide *where* and *whether*
   the phase can be believed (§4).

## 3. Fit output

`analyze()` returns the fringe outlines (`pU`, `pLn`), the reference point `l0`, the
cubic coefficients `csig = (c0..c3)`, the reference wavelength `ref_wl`, quality numbers
(`rms_frac`, `inlier_pct`), and the trust flags below. The reported phase is `Φ`
evaluated at `ref_wl`, taken mod 2π.

## 4. Trust and accuracy checks

These answer two deliberately separate questions: *how precise is the number?* and
*is it measuring the right thing?*

- **`trust_ok` — is the phase precise enough?** The uncertainty on the phase at the
  reference wavelength, multiplied by a safety factor `TRUST_NSIG`, must fit inside the
  required tolerance (a fractional tolerance on `c1` and `c2`, and an absolute budget on
  `c0` in radians). This covers `c0` only — the one number the loop acts on. A spectrum
  that cannot meet it is reported as `underdetermined` rather than handed over as a
  number that looks just as good as all the others.
- **`shape_ok` — are `c1`, `c2`, `c3` well enough determined?** The same check applied to
  those three. Only consumers that use the fit *away* from the reference wavelength need
  it: the chart overlay and the RF frequency readout. It is kept separate so the control
  loop is never blocked by an uncertainty it does not care about.
- **`ref_offset_ok` — is the reference wavelength inside the region the fit actually
  covers?** This catches a systematic error the uncertainties cannot see: when a clipped
  arm shrinks the fringed region, the middle of that region slides away from the
  operator's chosen reference, and the phase quoted back there is skewed by the crop.
  Enforced by the accept gate, not by the fit itself.

## 5. Choosing the reference wavelength

The phase is reported at a reference wavelength — normally the operator's configured
`lambda_ref` (≈ the brightness centroid, ~802 nm, where most of the pulse energy sits).
If a clipped arm leaves that wavelength outside the usable data, `ref_wl` falls back to
`l0`, the middle of the fringed region: the best-determined point, and for a one-sided
clip the point furthest from the cut. `ref_fallback` records that this happened. A
`ReferencePolicy` object carried from frame to frame requires `REF_HYST` consecutive
frames before switching, so a locked loop cannot flip back and forth between two
references.

## 6. RF frequency readout

The fringe spacing in the spectrum corresponds to a radio frequency the shot will
generate. The conversion comes from a timing calibration of the setup (9 nm of
wavelength maps to ~320 ps of delay, linearly), giving **28.125 GHz per cycle/nm**. The
readout quotes the range of the **signed** frequency across 802 ± 9 nm. Where the chirp
sweeps through a null the frequency genuinely goes negative, and the readout shows that
(a zero crossing, or a negative value). It is gated on `shape_ok` because it evaluates
the cubic beyond the region that was fitted.

## 7. From phase to correction (the control loop)

- **`PhaseTracker`** fits each spectrum from scratch, with no dependence on the previous
  frame's answer. The only things it carries between frames are the `ReferencePolicy` and
  a cache of where the clip edge was. It commits any fit that passes the accept gate and
  exposes `current_phase = c0 at ref_wl (mod 2π)`.
- **`PhaseCorrector`** turns the phase error (measured − target, wrapped to whichever
  direction is shorter) into a *relative* rotation of the half-wave plate. Each frame it
  applies `gain ×` the current error, so corrections accumulate over time — this is an
  integral controller, and `gain` alone sets how fast the loop responds. Errors smaller
  than `PHASE_TOLERANCE` are ignored (a dead band), and `MAX_STEP_DEG` limits how far a
  single move can go.
- **Rotator travel limit.** The rotation-stage handle refuses any correction that would
  push the plate past its physical travel (`RGV_MAX_DEG`) and logs it — a correction that
  large means the phase driving it is wrong.
- **Auto-pause.** After `AUTOPAUSE_FAILS` failed fits in a row the worker stops moving the
  plate *and* stops fitting every frame (it only probes occasionally). It resumes on the
  next good fit, or when the operator changes a setting.

## 8. Operator controls

The operator can drag two things directly on the chart: the **centre of the fringe
outlines** (`env_center`, pinned so that a clip on one side cannot drag it) and the
**left clip edge** (`manual_cut_left`, which excludes everything below it). Only left-hand
cuts are physically possible on this setup.

---

# Why it is built this way

The reasoning behind the choices above. This is the home for that reasoning — the code
itself carries only what a reader needs in order to follow *what* it does.

### Anchoring the Gaussian offset to a measured background
The analysis window (`ZOOM`, ±3.1σ of the bump) contains no pure-background points, so
nothing in the window pins down the vertical offset of the Gaussians. Under the 0.91
quantile loss the fit can lower its own error by floating that offset *upward* and
narrowing σ to match. On a bright, strongly fringed spectrum it settled at offset 255
when the true value was 155, squeezing σ about 12% too narrow, which corrupted the
clipped-arm width and inflated `rms_frac`. So the background is measured from the *full*
spectrum, outside the bump, and the offset is held near it (`U_base ± K·D`).

This problem is invisible in the saved `.xls` traces, which have weak fringes (a ~25-count
bump over a ~145-count background) and fit correctly at any window with either optimizer.
The bug only appears on bright live data, so those saved files must not be trusted to
catch it.

### Quantile-loss ratio (`RATIO = 10`)
Once the loss is being minimized properly, the exact penalty ratio between peaks and
troughs barely matters: the offset error is +3.6 counts at R=10 versus −0.2 at R=5, which
is indistinguishable in practice. This is not a knob worth turning.

### Nelder-Mead, not L-BFGS-B, for the outline fit
The quantile loss has sharp kinks in it. A gradient-based optimizer (L-BFGS-B on the
analytic subgradient) stalled at loss 7182.5 after 19 iterations, at offset 255 and
σ 3.41. Nelder-Mead works directly on function values and does not care about kinks, so
it is safe here. (This was one symptom of the 2026-07-16 duplicated-code bug — see
*One copy of the math*.)

### Pinning the outline centre (`ENV_CENTRE_*`)
The centre of the upper outline, `muU`, is the point the cubic is expanded about.
Clipping the beam on the blue side pushes the apparent brightness peak to the right —
measured, `muU` walks from ~802.2 to ~802.9 nm under a left clip — and the phase reported
at a fixed reference then inherits the wobble of an anchor that has moved ~0.7 nm with
poorly known curvature (phase at 802 nm scattered by 0.72 rad against a 0.314 rad
budget). Holding `muU` inside a narrow band around a known centre stops it from chasing
the clip. It is a band, not a fixed value, so a clean frame can still find its own
centre. The default centre is set by the operator dragging it, because it cannot be
derived from a stream of clipped frames.

### Only left-hand cuts are real (`PHYSICAL_CUT_SIDES`)
On this setup only the left (blue) arm can be optically clipped, and that will not change
(operator, 2026-07-20). So a detection on the right side is not a borderline call — it is
a known false alarm, every time. Measured the same day: on a beam confirmed clean, the
detector reported a right-side cut on 42 of 195 frames, always out in the red wing where
the Gaussian has rolled off and the fringes are naturally faint. Rather than chase that
with a wing-aware threshold, right-side detection is switched off entirely. Restore
`("left", "right")` only if the interferometer is rebuilt so the red arm can be clipped.

### Telling a clipped arm from a null
A clipped arm removes one end of the spectrum: the fringes (the interference term
`2·A_a·A_b·cos Φ`) stop abruptly where `A_b = 0`, leaving only the smooth Gaussian tail of
the surviving arm. The thing that looks similar is a null, where the fringe frequency
crosses zero and the trace is *also* locally flat. Two features tell them apart, and both
are required:

- **Where the flat part sits.** At a null the trace is parked against one of the outlines,
  so `|n| ≈ 1`. In a clipped band the trace sits strictly between the outlines, with
  `|n|` well below 1.
- **Whether it touches the edge.** A null is interior — fringes resume on both sides of
  it — while a clipped region runs from its edge all the way to the end of the spectrum.

Detection only runs where the gap between the outlines beats the camera read noise by
`TRUNCDET_SNR_GAP`; below that the answer is "unknown", not "no clip". The small step in
average brightness that a clip produces is deliberately not used: at the real ~7% fringe
visibility it is only ~1.7 counts, comparable to read noise. Detection is diagnostic only
— it never feeds the fit directly.

### Finding a null: look for a V, not just a minimum
The frequency from the Hilbert transform has no sign, so a real null appears as a *V* in
`|f|` — a minimum that rises again on *both* sides. On a spectrum with no null, `|f|` only
rises on one side, so the two-sided test finds nothing. That is exactly how "there is no
null here" gets detected. It replaced an earlier approach (take the minimum, fold around
it) that invented a null on every spectrum.

### The two starting guesses
The full fit is seeded by: the contrast crop (which removes the faint wings), a
phase-range trim plus a quadratic fit to what survives — correct wherever the phase
climbs steadily — and, at each dip in `|f|`, a signed parabolic guess that carries the
correct chirp sign through the null. The second one is accepted only when it reduces the
fringe mismatch (sum of squared errors) by at least a set margin, so a wrong or imagined
null cannot poison the fit.

### BIC for choosing the polynomial order
The order here is the degree of the frequency polynomial. Rather than hand-tuning a
penalty, the fit uses BIC (`n·ln(SSE/n) + k·ln(n)`), which charges a fixed price per extra
coefficient, so a spurious `c3` is dropped automatically. It is applied only when the
frequency keeps one sign; at a null `c3` cannot be determined from the data at all, so the
order is capped at 2 by design.

### The scan-free path versus the recovery scan
A clipped arm is handled by trimming the fringed region (the active
`TRUNC_METHOD="phase"` conditional phase trim). The "coarse cut" derivative idea was tried
and dropped (2026-07-19): it came out net-negative in testing — it over-cropped good
spectra into 16 extra false rejections on the realistic test suite — and never fixed the
clipped-arm case it was built for.

### Calibrating the trust factor (`TRUST_NSIG = 3.0`)
The fit's own uncertainty estimate is too optimistic, for two reasons it cannot see: the
fringe outlines are held fixed while the phase is fitted (so their error skews the phase
without showing up in the uncertainty), and the phase noise is not the simple white
intensity noise the uncertainty calculation assumes. `TRUST_NSIG` absorbs both. It is the
accuracy-versus-yield trade, and both sides are specified: at least 98% of reported fits
correct, at most 5% of good fits rejected (user, 2026-07-16). Swept over 2470 fits, two
random seeds:

| NSIG | accuracy | good fits wrongly rejected |
|------|----------|-----------|
| 2.0  | 97.97%   | 0.8%      |
| 3.0  | 98.54%   | 3.7%  ← shipped (margin on both requirements) |
| 3.25 | 98.69%   | 4.9%  (right on the 5% line, no headroom) |
| 5.0  | 99.31%   | 15.0%     |
| 16.0 | ~99.86%  | ~69.5%    |

Do not chase 99.9% with this knob — accuracy saturates while yield collapses. The
remaining ~1–1.5% is real third-order dispersion right at the limit of what the data can
resolve (true `c3 = ±0.005`), which BIC correctly declines to model. Fixing that means
revisiting order selection, not tightening this factor.

### Phase tolerance = 5% of 2π (`TRUST_TOL_C0 = 0.314`)
It used to be 0.126 (2% of 2π), a number that was never checked against what the loop
actually needs, and it was the term that decided the outcome on real spectra. 5% is the
honest requirement (user, 2026-07-19): the beam itself jitters by more than 2% from shot
to shot even while stabilized, and stabilization was never meant to suppress that jitter —
it exists to stop the slow drift that smears the phase out over a long scan. A threshold
ten times tighter than the jitter it lives in just rejects frames for an error the loop
does not care about.

### Reference-offset check (`REF_MAX_OFFSET_FRAC = 0.12`)
`trust_at` tests *precision* and is blind to *bias*: when one arm is clipped, the fringed
region shrinks, its centre slides away from the operator's reference wavelength, and the
phase quoted back there splits into two distinct populations that the uncertainty estimate
cannot distinguish. Measured over 58 consecutive frames, grouped by whether the fringed
region had shifted: the phase at 802 nm read 4.128 rad in one group and 2.734 rad in the
other — a 1.40 rad step, on frames that all reported `trust=True`, `rms_frac ~0.10`, and
97–100% inliers. The threshold 0.12 sits in the empty gap between the two populations
(which ran 0.04 and 0.15). It is a separation threshold read off real data — re-measure it
if the spectrometer window or the crop changes.

### Falling back to a different reference, with hysteresis
The default reference (brightness centroid, ~802 nm) is where the pulse energy is, but a
clip landing 1–2 nm from it drops reported accuracy to ~87–90% and makes the trust check
reject about 40% of fits it would otherwise get right. Falling back to `l0`, the centre of
the fringed region, restores that. The `ReferencePolicy` hysteresis (`REF_HYST = 5`)
exists because a loop locked to one wavelength must not flip back and forth between two;
a caller that supplies no policy switches immediately.

### RF readout — signed, and the ±9 nm band
The range is reported with its sign rather than as `|f|`. Where the chirp sweeps the
frequency through zero, taking the absolute value folds the negative part upward, and
because the exact zero almost never lands on a sample point, the result is a meaningless
small positive number (~0.03 GHz) instead of the true zero crossing or negative
excursion. The ±9 nm band deliberately reaches beyond the fitted region, because the
question being answered is "what RF does this shot generate across the whole pulse" —
which is why the readout is gated on `shape_ok`.

### Corrector — gain, step cap, and what bounds accumulation
The hardware takes about 0.5 s to respond, and the phase noise is faster than one
measure-and-move cycle, so the loop is deliberately sluggish (`LOOP_GAIN = 0.05`, taking
roughly 1/gain frames to pull in). Chasing noise faster than the cycle time just injects
that noise into the stage. `MAX_STEP_DEG` caps any single move, and only really binds if
the operator winds the gain up toward 1. Neither of those bounds an *accumulation* of
correctly-sized steps that all point the same way — the failure where a biased reading
slowly winds the plate through whole turns. That is caught at the one place absolute
position is known: the rotator travel limit.

### One copy of the math
`fringe_core.py` is the only place the analysis lives; `fringe_fit.py` contains no math.
The app once carried a second, hand-maintained copy, and every bug found on 2026-07-16
was the two copies drifting apart: Nelder-Mead's absolute `fatol` passed to L-BFGS-B as
its relative `ftol` (giving offset 255 instead of 155), the cubic seeded with `c1 = 0`
(so the fringe spacing started wrong on every spectrum), and no baseline anchor. One
copy, no drift. Calibrated constants are imported from `fringe_core` and never restated
in the adapter or in config.

---

# Dead ends & disabled experiments

Recorded so they are not tried again. The corresponding flags are still in the code, set
to `False`.

- **Global-kernel Hilbert detrend** (do not re-add). The idea — subtract
  `gaussian_filter1d(n, σ)` to centre `n` on zero before the Hilbert transform — is sound,
  but a single fixed smoothing width cannot be "about one fringe period" everywhere on a
  spectrum whose fringe spacing changes: on `da_15.95ga_-75` it followed the fast fringes
  in the blue third and removed 14.7% of the fringe amplitude, collapsing `r2_fringe` from
  0.645 to −0.116. Six of the seven test spectra came out bit-identical anyway, because
  the full fit dominates its starting guess — so the guess barely matters. Any retry needs
  a smoothing width that follows the local fringe spacing, and must first show that the
  starting guess matters at all.
- **`JOINT_ENV_FIT`** (off). Fitting the phase and both Gaussian outlines together is a
  clear win on unclipped spectra (`r2_sig` 0.988 → 0.995), but on clipped ones the freed
  outlines absorb the mismatch left by the missing arm, so the fit passes the trust check
  while the phase is actually wrong (+23 confidently-wrong results) and the recovery scan
  that used to catch those never fires. Using it safely means restricting it to spectra
  the frozen fit already explains with `side == "none"` — a second pass that has not been
  built.
- **`DEADZONE_REFIT`** (off). Refitting the outlines on the full window minus the knife
  dead zone — an alternative to the recovery scan, left off pending evaluation.
- **`SCANFREE` / alternate `TRUNC_METHOD`** (off; "phase" is active). The deterministic
  scan-free path and the "knife"/"none" clip-handling methods were built to compare
  against the shipped recovery scan; the phase-trim method is the one in use.

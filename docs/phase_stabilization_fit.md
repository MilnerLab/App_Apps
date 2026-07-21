# Phase Stabilization — Fringe Fit

How the phase-stabilization loop measures the interferometer phase from a single
spectrum and turns it into a half-wave-plate correction.

The analysis math lives in `fringe_core.py` (the single source of truth);
`fringe_fit.py` is a thin adapter that translates between the app's dataclasses and
`fringe_core.analyze()`. The control-loop pieces are `phase_tracker.py`,
`phase_corrector.py`, and `phase_stabilization_worker.py`.

---

## 1. The physical model

A spectrometer looks at a two-arm interferometer. Each arm contributes a smooth
Gaussian-like envelope; the arms interfere, so the recorded spectrum is a fringe
pattern riding on that envelope:

```
I(λ) = mid(λ) + half(λ)·cos(Φ(λ))
```

- `mid(λ) = ½(U + L)` and `half(λ) = ½(U − L)`, where `U` and `L` are the upper and
  lower envelopes (Gaussians). `half` is the local fringe amplitude; `mid` the local mean.
- `Φ(λ)` is the **spectral phase**, expanded as a cubic about a basis origin `l0`
  (the fitted core centroid), in `u = λ − l0`:
  ```
  Φ(u) = c0 + c1·u + c2·u² + c3·u³
  ```
  `c0` is the phase at the reference (the quantity the loop locks to); `c1` the carrier
  (fringe frequency), `c2` the chirp, `c3` third-order dispersion (TOD).
- The **instantaneous fringe frequency** is `f(u) = Φ'(u)/2π = (c1 + 2c2·u + 3c3·u²)/2π`
  cycles/nm. It is signed, and flips through a *null* where it passes through zero.

The loop consumes `c0` at a reference wavelength; the rest describe the pulse.

## 2. The analysis pipeline (`analyze()`)

One spectrum in, one fit out. In order:

1. **Truncation detection.** Detect a spectrally clipped interferometer arm and drop
   the fringe-free band (see §Rationale: truncation).
2. **Envelope fit.** Fit the upper/lower envelope Gaussians under an asymmetric
   *pinball* (quantile ≈ 0.91) loss so the fit hugs the fringe crests, with the offset
   bounded to a continuum measurement taken from the full frame (the *baseline anchor*).
3. **Contrast crop + normalize.** Keep the high-visibility core (where the envelope
   contrast exceeds `TRUNC_THRESHOLD` of its peak) and normalize the fringes to
   `n = (y − mid)/half`, which rides in [−1, 1].
4. **Seed the phase.** Hilbert-transform the normalized fringes to get the unsigned
   instantaneous frequency `|f|`. Build a **two-trim seed** (trim the phase-value range,
   polyfit a quadratic) that is correct where the phase is monotonic, plus a **signed
   null-flip seed** per `|f|` dip that carries the real chirp through a null — taken only
   when it cuts the fringe residual. **BIC** then selects the phase order (carrier /
   chirp / +TOD), so spurious TOD is rejected without a tuning knob.
5. **Refit the cubic** on the raw counts with the envelopes held fixed.
6. **Trust.** Propagate the fit covariance to the reference and decide *where* and
   *whether* the phase can be trusted (§4).

## 3. Fit output

`analyze()` returns the envelopes (`pU`, `pLn`), the basis origin `l0`, the cubic
coefficients `csig = (c0..c3)`, the reference wavelength `ref_wl`, quality metrics
(`rms_frac`, `inlier_pct`), and the trust/accuracy flags below. The reported phase is
`Φ` evaluated at `ref_wl`, taken mod 2π.

## 4. Trust and accuracy gates

Two independent questions, deliberately separate:

- **`trust_ok` — is the phase precise?** The propagated covariance at the reference,
  scaled by `TRUST_NSIG`, must fit inside the accuracy spec (relative tolerances on
  `c1`/`c2`, an absolute mod-2π budget on `c0`). Covers `c0` only — the one quantity the
  loop acts on. A trace that cannot meet it is reported `underdetermined` rather than
  handed over as a number that looks like all the others.
- **`shape_ok` — are the carrier/chirp/TOD supportable?** The same test for `c1..c3`,
  needed only by consumers that evaluate the fit *away* from the reference (the chart
  overlay and the RF readout). Kept separate so the loop is not gated on an error it
  does not care about.
- **`ref_offset_ok` — is the reference inside the data that supports it?** An *accuracy*
  (bias) check the covariance is blind to: when a clip shrinks the core, its centroid
  slides off the operator's reference and the phase there is biased by the crop. Enforced
  by the accept gate, not the fit.

## 5. Adaptive phase reference

The phase is reported at a reference wavelength — by default the operator's configured
`lambda_ref` (≈ the intensity centroid, ~802 nm, where the pulse energy is). When a clip
leaves that reference unsupported, `ref_wl` falls back to the **core centroid `l0`** (the
fit's own basis origin, best-conditioned, and for a one-sided clip the point furthest from
the cut); `ref_fallback` flags it. A `ReferencePolicy` carried across frames adds
hysteresis (`REF_HYST` consecutive frames before switching) so a locked loop cannot
chatter between two references.

## 6. RF frequency readout

The spectral fringe frequency maps to the RF frequency the shot generates via a dispersive
time-mapping calibration (9 nm ↔ ~320 ps, linear ⇒ **28.125 GHz per cycle/nm**). The
readout quotes the range of the **signed** frequency across 802 ± 9 nm; where the chirp
sweeps through a null the frequency genuinely goes negative, and the readout shows that
(a zero-crossing / negative value), gated on `shape_ok` because it extrapolates the cubic
past the fitted core.

## 7. From phase to correction (the control loop)

- **`PhaseTracker`** fits each spectrum (cold, seed-independent), carrying only the
  `ReferencePolicy` and the clip-edge cache across frames. It commits a fit that passes
  the accept gate and exposes `current_phase = c0 at ref_wl (mod 2π)`.
- **`PhaseCorrector`** turns the phase error (measured − target, wrapped to the shortest
  way round) into a *relative* half-wave-plate rotation increment. It is effectively an
  integral controller: each frame applies `gain ×` the measured error, so the loop
  integrates and `gain` alone sets its bandwidth. A dead-band (`PHASE_TOLERANCE`) ignores
  sub-tolerance error; a per-step cap (`MAX_STEP_DEG`) bounds one move.
- **RGV travel limit.** The rotator handle clamps any correction that would drive the
  plate past its physical travel (`RGV_MAX_DEG`) and logs it — a correction that large
  means the phase feeding it is wrong.
- **Auto-pause.** After `AUTOPAUSE_FAILS` consecutive failed fits the worker stops
  driving the plate *and* stops fitting every frame (a slow probe only), and auto-resumes
  on the next committed fit or an operator config change.

## 8. Operator controls

The operator can drag two pieces of analysis geometry live on the chart: the **envelope
centre** (`env_center`, pinned so a one-sided clip cannot drag the fit origin) and the
**manual left clip edge** (`manual_cut_left`, excluding fringes below it). Only left cuts
are physical on this setup.

---

# Decision rationales

The *why* behind the choices above. This section is the home for that reasoning — the code
itself carries only what a reader needs to follow *what* it does.

### Baseline anchor for the envelope offset
The analysis window (`ZOOM`, ±3.1σ of the bump) contains no pure-baseline points, so the
Gaussian offset is unconstrained. Under the 0.91 quantile loss the fit lowers its own loss
by floating the offset *up* and shrinking σ with it — on a bright, high-visibility trace it
settled at offset 255 against a truth of 155, squeezing σ ~12% narrow, corrupting the
truncation width and inflating `rms_frac`. So the continuum is measured from the *full*
frame (outside the bump) and the offset is bounded to it (`U_base ± K·D`). This is invisible
on the saved low-visibility `.xls` traces (bump ~25 counts over ~145 baseline), which fit
correctly at any window with either optimizer — the bug only bites bright live data, so
those files must not be trusted to catch it.

### Pinball-loss ratio (`RATIO = 10`)
With a correctly minimized loss the crest/tail penalty ratio barely matters: the offset
error is +3.6 counts at R=10 vs −0.2 at R=5 — indistinguishable. It is not a loose knob to
turn.

### Nelder-Mead, not L-BFGS-B (envelope refinement)
The pinball loss is kinked; a smooth-gradient optimizer (L-BFGS-B on the analytic
subgradient) stopped at loss 7182.5 after 19 iterations (offset 255, σ 3.41). Nelder-Mead
on the loss itself is unconditionally safe on a kinked objective. (This was one face of the
2026-07-16 two-copy drift bug — see "single source of truth".)

### Envelope-centre pin (`ENV_CENTRE_*`)
The upper-envelope mean `muU` is the anchor the cubic is expanded about. Left-clipping the
beam pushes the intensity peak rightward (measured: `muU` walks ~802.2 → ~802.9 under a left
clip), and the phase reported at the fixed reference then inherits noise from an anchor that
has wandered ~0.7 nm with uncertain curvature (phase@802 scatter 0.72 rad vs a 0.314 budget).
Pinning `muU` to a narrow band around a known centre refuses to follow the clip. It is a
band, not a hard value, so a clean frame still fits its own centre; the default is
operator-dragged because it is not derivable from a truncated stream.

### Physical cut sides — left only (`PHYSICAL_CUT_SIDES`)
On this setup only the left (blue) arm can be optically clipped, and will stay that way
(operator, 2026-07-20). So a right-side detection is not a marginal call — it is a known
false positive with probability 1. Measured the same day: on a confirmed-clean beam the
detector reported a right cut on 42/195 frames, always in the red wing where the Gaussian
has rolled off and contrast is naturally low. Rather than chase that with a wing-aware
threshold, right-side detection is suppressed outright. Restore `("left", "right")` only if
the interferometer is rebuilt so the red arm can be clipped.

### Truncation detection — separating a clip from a null
A clipped arm removes a spectral tail: the fringes (the cross term `2·A_a·A_b·cos Φ`) stop
abruptly where `A_b = 0`, leaving the lone arm's smooth Gaussian tail. The confounder is a
null, where the instantaneous frequency crosses zero and the fringe is *also* locally flat.
Two things separate them, and both are required: **pinning** (at a null the trace parks on
an envelope, `|n| ≈ 1`; a clipped band sits strictly inside them, `|n|` well below 1) and
**edge-touching** (a null is interior — fringes resume on both sides — while a clipped tail
runs from its edge to the boundary). Detection is confined to where the envelope gap beats
read noise by `TRUNCDET_SNR_GAP`; below that the answer is "unknown", not "none". The DC
step from a clip is not relied on: it is ~1.7 counts at the real ~7% visibility, comparable
to read noise. Detection is diagnostic only — it never feeds the fit directly.

### Null localization — two-sided prominence, not argmin-fold
The Hilbert instantaneous frequency is unsigned. A genuine null is a *V* in `|f|` that rises
on both sides of an interior minimum; a monotonic trace has `|f|` rising on one side only, so
the two-sided prominence collapses. That is how "no null exists" is detected — replacing an
old argmin+fold that fabricated a null on every trace.

### Two-trim + null-flip seed
The full-signal fit is seeded by: the contrast crop (removes low-visibility wings), a
phase-value trim (cuts the folded null plateau) + quadratic polyfit of the surviving
one-sided arm — correct wherever the phase is near-monotonic — and, per `|f|` dip, a signed
parabolic seed that carries the real chirp through the null, taken only when it cuts the
fringe SSE by ≥ the margin. So an inaccurate or false null candidate cannot poison the fit.

### BIC phase-order selection
Order = degree of the frequency polynomial. Instead of a hand-tuned ridge, BIC
(`n·ln(SSE/n) + k·ln(n)`) penalizes extra terms, so spurious TOD is rejected automatically.
Applied only when the frequency is one-signed; at a null, TOD is unidentifiable and the
order is capped at 2 by design.

### Scan-free path vs. the recovery scan
Truncation is handled by trimming the core (the active `TRUNC_METHOD="phase"` conditional
phase trim). The "coarse cut" derivative idea was tried and dropped (2026-07-19): net-
negative in testing — it over-cropped good traces into false-drops (+16 vs no coarse cut on
the realistic suite) and never fixed the truncation corner it was built for.

### Trust gate calibration (`TRUST_NSIG = 3.0`)
The Gauss-Newton covariance under-estimates the true error for two reasons it cannot see:
the envelopes are held fixed through the phase fit (their error biases the phase without
entering the covariance), and the phase noise is not the white intensity noise the SSE/dof
scaling assumes. `NSIG` absorbs both. It *is* the accuracy/yield trade, both sides specced
(≥98% of reported fits correct, ≤5% of good fits declined; user, 2026-07-16). Swept over
2470 fits, two seeds:

| NSIG | accuracy | false-drop |
|------|----------|-----------|
| 2.0  | 97.97%   | 0.8%      |
| 3.0  | 98.54%   | 3.7%  ← shipped (margin on both bars) |
| 3.25 | 98.69%   | 4.9%  (on the 5% line, no headroom) |
| 5.0  | 99.31%   | 15.0%     |
| 16.0 | ~99.86%  | ~69.5%    |

Do not chase 99.9% with this knob — it saturates while yield collapses. The residual ~1–1.5%
is real TOD at the identifiability floor (true `c3 = ±0.005`) that BIC declines to model;
fixing it means revisiting order selection, not tightening this gate.

### Phase tolerance = 5% of 2π (`TRUST_TOL_C0 = 0.314`)
Was 0.126 (2% of 2π), never checked against what the loop needs and the binding term on real
traces. 5% is the honest spec (user, 2026-07-19): the beam jitters more than 2% shot-to-shot
even while stabilized, and stabilization was never meant to suppress that jitter — it exists
to stop the slow drift that smears phase across a long scan. A gate an order of magnitude
below the jitter it lives in rejects frames for an error the loop does not care about.

### Reference-offset accuracy gate (`REF_MAX_OFFSET_FRAC = 0.12`)
`trust_at` is a *precision* test and is blind to a *bias*: when one arm is clipped the fitted
core shrinks and its centroid slides off the operator's reference, and the phase quoted back
there splits into two populations the covariance cannot tell apart. Measured over 58
consecutive frames, grouped by whether the core had displaced: phase @ 802 nm read 4.128 rad
vs 2.734 rad — a 1.40 rad step on frames that all reported `trust=True`, `rms_frac ~0.10`,
97–100% inliers. 0.12 sits in the gap the two populations leave (0.04 → 0.15). It is a
separation threshold read off real data — re-measure it if the spectrometer window or the
core crop changes.

### Adaptive reference + hysteresis
The default reference (intensity centroid ~802 nm) is where the pulse energy is, but a clip
1–2 nm from it drops reported accuracy to ~87–90% and makes the trust gate decline ~40% of
fits it would otherwise get right. Falling back to the core centroid `l0` restores that. The
`ReferencePolicy` hysteresis (`REF_HYST = 5`) exists because a loop locked to one wavelength
must not chatter between two; a caller with no policy switches immediately.

### RF readout — signed, and the band
The range is reported *signed*, not `|f|`: where the chirp sweeps the frequency through zero,
`abs()` folds the negative arm up and — because the exact zero rarely lands on a grid node —
surfaces a meaningless small positive (~0.03 GHz) instead of the true zero-crossing/negative
excursion. The band (±9 nm) deliberately extrapolates past the fitted core to answer "what RF
does this shot generate across the pulse", which is why the readout is gated on `shape_ok`.

### Corrector — gain, step cap, accumulation limit
The plant has ~0.5 s of dead time and the phase noise is faster than the measure-and-move
cycle, so the loop is deliberately overdamped (`LOOP_GAIN = 0.05`, ~1/gain frames to pull
in): chasing noise faster than the cycle just injects it into the stage. `MAX_STEP_DEG` caps
a single move (binds only when the operator winds the gain toward 1). Neither bounds an
*accumulation* of correctly-sized steps that all point the same way — that failure (a biased
readout winding the plate through whole turns) is caught where absolute position is known, at
the RGV travel limit.

### Single source of truth for the math
`fringe_core.py` is the one place the analysis lives; `fringe_fit.py` holds no math. The app
once carried a second, hand-maintained copy, and every bug found 2026-07-16 was drift between
the two: Nelder-Mead's absolute `fatol` passed as L-BFGS-B's relative `ftol` (offset 255 vs
155), the cubic seeded with `c1 = 0` (carrier wrong on every trace), and no baseline anchor.
One copy, no drift. Calibrated constants are imported from `fringe_core`, never restated in
the adapter or config.

---

# Dead ends & disabled experiments

Recorded so they are not re-tried. The corresponding flags remain in the code as `False`.

- **Global-kernel Hilbert detrend** (do not re-add). The idea — subtract
  `gaussian_filter1d(n, σ)` to zero-mean `n` before the Hilbert transform — is sound, but one
  global kernel cannot be "about one period" everywhere on a chirped/null trace: on
  `da_15.95ga_-75` it tracked the fast blue-third fringe and ate 14.7% of the fringe RMS,
  collapsing `r2_fringe` 0.645 → −0.116. Six of seven traces were bit-identical anyway (the
  full fit dominates the seed), so the seed barely matters. Any retry needs a local,
  chirp-following kernel and must first show the seed matters at all.
- **`JOINT_ENV_FIT`** (off). Re-solving the phase and both envelope Gaussians jointly is a
  clean win on untruncated traces (`r2_sig` 0.988 → 0.995) but on truncated traces the freed
  envelope absorbs the missing-arm misfit, passes the trust gate with the phase actually wrong
  (+23 confidently-wrong), and suppresses the recovery scan that used to catch them. Safe use
  needs gating to traces the frozen fit already explains with `side == "none"` — a second pass
  not yet built.
- **`DEADZONE_REFIT`** (off). Refitting the envelopes on the full window minus the knife
  deadzone — an alternative to the recovery scan, left off pending evaluation.
- **`SCANFREE` / alternate `TRUNC_METHOD`** (off / "phase" active). The deterministic
  scan-free path and the "knife"/"none" truncation methods were built for comparison against
  the shipped recovery scan; the phase-trim method is the one in use.

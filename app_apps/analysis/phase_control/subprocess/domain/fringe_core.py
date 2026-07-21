"""Cubic-phase fringe analysis — THE source of truth for the fit math.

This module is the ONE place the analysis lives. `plot_traces.py` imports it for the
standalone/harness path, and the App_Apps production port carries a VERBATIM COPY of this
file plus a thin adapter (`fringe_fit.py`). Nothing here may be hand-edited in only one of
those two places.

That rule exists because breaking it is what produced every bug found on 2026-07-16: the
port had drifted into passing Nelder-Mead's absolute `fatol` as L-BFGS-B's relative `ftol`
(envelope offset 255 against a truth of 155), into seeding the cubic with `c1=0` (carrier
wrong on every real trace), and into having no baseline anchor at all. Two hand-maintained
copies of the same math is the defect, not the symptom. `test/fringe_parity_test.py` in the
app asserts this file's `analyze()` and the app's adapter agree bit-for-bit, and it fails
loudly the moment the copies diverge.

Pure numpy/scipy: no matplotlib, no file I/O, no globals mutated at runtime. Safe to import
from a subprocess worker.

Pipeline (see analyze()):
  1. detect a spectrally clipped interferometer arm and drop the fringe-free band
  2. fit the envelopes under an asymmetric pinball loss, offset anchored to the continuum
  3. crop to the high-visibility core (contrast trim), normalize the fringes
  4. Hilbert -> two-trim seed: phase-value trim + polyfit, with a signed null-flip seed
     taken per |f| dip only on a clear SSE win, then BIC picks the phase order (see
     core_seed_fit)
  5. refit the cubic phase on the RAW counts with the envelopes held fixed
  6. propagate the fit covariance and decide WHERE (and whether) the phase can be trusted
"""
import time
import numpy as np
from scipy.optimize import curve_fit, minimize, least_squares
from scipy.signal import hilbert
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d

# ============================ TUNABLE PARAMETERS =============================
# --- Data window ---
ZOOM = (790, 814)     # analysis window (nm) around the ~802 nm peak

# --- Envelope fit (asymmetric pinball / quantile loss) ---
RATIO = 10            # penalty ratio above:below the fit; higher hugs the crests tighter.
                      # NOT a loose knob: with a correctly-minimized loss the R-sweep gives
                      # an `off` error of +3.6 counts at R=10 vs -0.2 at R=5 (and +0.3 for a
                      # tail-weighted loss) -- indistinguishable. Do not turn it.
SIGMA_INIT = 4.0      # initial Gaussian sigma guess (nm) for the L2 warm start
FIT_MAXFEV = 10000    # curve_fit evaluation cap (warm start)
FIT_XATOL = 1e-4      # Nelder-Mead x-tolerance (pinball refinement)
FIT_FATOL = 1e-4      # Nelder-Mead f-tolerance
FIT_MAXITER = 20000   # Nelder-Mead iteration cap

# --- Baseline anchor for the envelope offset --------------------------------
# ZOOM is +-3.1 sigma around a sigma~3.9 bump, so it contains ZERO pure-baseline points.
# With no baseline in view the Gaussian's offset is unconstrained, and the tau=0.91
# quantile lowers its own loss by floating `off` UP and shrinking sigma with it -- on a
# high-visibility trace it settled at off=255 against a truth of 155, squeezing sigma ~12%
# narrow, which corrupts the truncation width and inflates rms_frac. So we measure the
# continuum from the FULL frame (outside the bump) and bound `off` to it.
#
# This is invisible on the saved .xls traces: they are low-visibility (bump ~25 counts over
# a ~145 baseline) and fit correctly at every window with either optimizer. The bug only
# bites bright/high-contrast live data. Do not trust those files to catch it.
ANCHOR_EXCLUDE_NM = 14.0   # |lambda - bump centre| beyond which the frame is continuum
ANCHOR_K = 0.5             # admissible offset band = U_base +- K*D. Holds across a 5x
                           # brightness range with no retuning (U_base predicts the
                           # converged truth to 0.7 counts on the high-vis trace).
ANCHOR_MIN_PTS = 40        # below this much continuum, decline to anchor (stay unbounded)

# --- Wing truncation before the Hilbert transform ---
# Keep only the high-visibility core where the envelope contrast (upper-lower gap) is
# >= min + THRESHOLD*(max-min); higher => tighter crop. Swept on the harness (4 tuning
# seeds x seven values 0.10..0.45): pass rate rises MONOTONICALLY as we crop tighter up
# to a broad flat plateau at 0.35-0.45 (~98.6%, vs 97.7% at the old 0.25), i.e. the
# low-SNR fringe wings hurt the phase fit more than their extra lever-arm helps. 0.40 is
# the plateau centre; held out on 3 fresh seeds it stays ahead (99.0% vs 98.8%) and it
# leaves real-data cores healthy (~150-185 pts) with the da17 null unmoved (@799.53).
TRUNC_THRESHOLD = 0.30  # (was 0.40) fraction of peak envelope contrast kept as the core.
                        # Eased off from 0.40: the tighter crop shortened the lever arm enough
                        # to fail trust on 2020607181645 AND clipped clean cores enough that
                        # their faded blue-edge visibility read as a lead-in dead run, firing
                        # the knife on a clean trace. 0.30 keeps the arm; the knife now removes
                        # the dead sliver, so the contrast crop no longer has to.

# --- Truncated-arm detection (DIAGNOSTIC ONLY -- never feeds the fit) ---------
# One interferometer arm clipped (slit / aperture / shaper mask) removes a spectral
# tail of THAT arm. The fringes are the cross term 2*A_a*A_b*cos(Phi), so in the band
# where A_b = 0 the oscillation stops ABRUPTLY and only the un-clipped arm's smooth
# Gaussian tail survives (the DC also steps down by A_b^2, but at the real ~7%
# visibility that step is ~1.7 counts -- comparable to read noise, so we do NOT rely
# on it). We detect the fringe collapse: a run of samples where the measured local
# fringe peak-to-peak has died relative to the fitted envelope gap (U-L, which still
# predicts fringes there), running out to the edge of the detectable region.
#
# The confounder is a NULL: where the instantaneous frequency crosses zero the fringe
# is ALSO locally flat, over a half-width ~sqrt(pi/2|c2|) that can reach several nm.
# Two things separate a null from a clip, and we require BOTH:
#   (a) PINNING -- at a null the trace is parked on an envelope (|n| ~ 1); a clipped
#       band sits strictly INSIDE them (the lone arm's power A_a^2 lies between the
#       trough and crest levels), so |n| is well below 1.
#   (b) EDGE-TOUCHING -- a null is interior (fringes resume on both sides); a clipped
#       tail runs from its edge out to the boundary of the detectable region.
# Detection is deliberately confined to where fringes are detectable AT ALL: the
# envelope gap must beat the read noise by TRUNCDET_SNR_GAP, otherwise noise-only
# wings would read as "fringes missing" on every trace.
TRUNCDET_WIN_PERIODS = 1.0    # sliding window = this many fringe periods (>=1 so a live
                              # window's variance sees a full crest->trough swing)
TRUNCDET_WIN_MIN_NM = 0.45    # floor/cap on that window (nm), for very high/low frequencies
TRUNCDET_WIN_MAX_NM = 4.0     # >= the slowest period we generate (0.3 cyc/nm = 3.3 nm)
TRUNCDET_LADDER = 5           # fixed window sizes spanning MIN..MAX; each sample uses the
                              # rung nearest its own local period
TRUNCDET_FLOC_NM = 4.0        # window for the local zero-crossing rate (the local period)
TRUNCDET_HYST_SIGMA = 2.0     # Schmitt-trigger rails (x sigma) for counting those
                              # crossings, so read noise cannot chatter a slow fringe's
                              # crossing into several and read 3x the true frequency
TRUNCDET_DC_WIN_NM = 1.0      # rolling-median window that blunts the fringe before the
                              # stiff Gaussian DC fit (the fit is what does the work)
TRUNCDET_DC_PASSES = 3        # times to RE-FIT that Gaussian DC with the fringe-free band
                              # excluded. The docstring's "clip moves the DC by only
                              # ~9%" is a LOW-VISIBILITY approximation: the step is
                              # b^2/(a^2+b^2) of the bump, ~5% at contrast k=0.4 but ~28%
                              # at k=0.9. A symmetric Gaussian fit through a coherent 28%
                              # one-sided step slides its centre toward the surviving arm,
                              # so bump under-predicts on the clipped side (clip MISSED)
                              # and over-predicts opposite (clip reported on the WRONG
                              # side) -- the measured bright-trace failure. soft_l1 absorbs
                              # scattered outliers, not a coherent block, so we drop the
                              # block and re-fit instead. Bootstraps from a MISSED clip
                              # because the excluded set is read off h_meas (DC-free), not
                              # off the possibly-empty dead mask.
TRUNCDET_DCREFIT_SIGMA = 3.0  # a sample INSIDE the hump whose fringe amplitude h_meas is
                              # below this*sigma carries no fringe -- it is the clipped
                              # band (or a null) -- so it is excluded from the DC re-fit.
                              # Wings (low bump) stay in to anchor the Gaussian offset.
TRUNCDET_DCREFIT_TOL = 0.01   # re-fit has converged when the bump moves less than this
                              # fraction of its peak between passes
TRUNCDET_DEAD_FRAC = 0.35     # measured fringe amplitude < this * predicted => locally dead
TRUNCDET_PIN = 0.75           # p90|y-DC| >= this * h_ref => the fringe still reaches its
                              # crest nearby, so it is ALIVE (a null parks at ~1*h; a
                              # clipped band sits at only b/(2a) ~ 0.16*h). One-way veto.
TRUNCDET_VETO_SIGMA = 2.0     # ...and the veto's swing must also clear this * sigma, so
                              # noise (p90 ~ 1.645 sigma) cannot vote itself alive. It is
                              # boxed in from both sides and there is little room to tune:
                              # noise sets a floor of 1.645, while at the live boundary a
                              # GENUINE fringe only reaches p90 ~ h_ref = SNR_GAP/2 = 2.5
                              # sigma, so anything at/above 2.5 stops real slow fringes
                              # from vetoing themselves alive and turns them into false
                              # positives (measured: 3.0 doubled them, all at f1 < 1.6).
TRUNCDET_MAJORITY_NM = 0.6    # de-speckle: a sample is dead if most of this span is
TRUNCDET_EDGE_TOL_NM = 2.0    # slack when asking whether a dead run touches an edge.
                              # NOT a fudge: right AT the live boundary the predicted fringe
                              # is only ~SNR_GAP/2 = 2.5 sigma, so v is at its noisiest there
                              # and the first nm or so of a genuine dead band reads "alive"
                              # by accident. The run therefore starts INSIDE the live edge
                              # and a strict test discards it -- the detector finds the clip
                              # and then throws the answer away. Measured on one such trace
                              # (clip 1.5 nm blue of 802): v = 0.140 across the dead band vs
                              # 0.961 outside it, i.e. the physics separates cleanly, yet the
                              # run began 1.63 nm inside the live edge and 0.5 nm of slack
                              # rejected it. This tolerance is the width of that unmeasurable
                              # band. Swept on near-core clips (1-4 nm, the ones the fit core
                              # can feel), detection / false positives / FPs reaching the core:
                              #   0.5 -> 68.0% / 31.2% / 0      2.0 -> 93.2% / 35.8% / 0
                              #   1.0 -> 80.2% / 33.3% / 0      3.0 -> 94.5% / 36.2% / 0
                              #   1.5 -> 88.0% / 34.2% / 0      4.0 -> 94.8% / 36.2% / 0
                              # 2.0 is the knee. The FP cost is real but free in the only
                              # sense that matters: those FPs still land outside the
                              # 40%-contrast core, so they remove no phase information.
TRUNCDET_CUT_PAD_NM = 0.25    # extra guard band added to the reported clip edge, on top
                              # of the w/2 smearing correction, before the fit drops those
                              # samples: keeping one fringe-free point costs far more than
                              # dropping a few good ones
TRUNCDET_K_BUMP_FRAC = 0.25   # gauge the contrast k where bump >= this * peak bump
TRUNCDET_K_PCT = 90           # ...as this percentile of h/bump there (not the median:
                              # a clipped band's zeros must not drag k down)
TRUNCDET_K_ITERS = 2          # then refine k as the median over the samples that look
                              # live, which survives a mostly-clipped trace
TRUNCDET_ALL_FRAC = 0.9       # a dead run covering this much of the detectable region
                              # is reported as "all", not as one side
TRUNCDET_MIN_RUN_NM = 0.7     # shortest edge-touching dead run that counts as truncation
TRUNCDET_SNR_GAP = 5.0        # only test where model gap >= this * noise sigma. A dead
                              # window's noise-subtracted amplitude still fluctuates up to
                              # ~0.7 sigma, so v_dead ~ 0.7*sigma/half stays under
                              # DEAD_FRAC only while gap >~ 4*sigma. The real traces run
                              # gap/sigma = 4.3 (15.95ga) to 12.9 (da17), i.e. this gate
                              # is what the instrument actually supports -- not a knob to
                              # loosen. Below it the answer is "unknown", not "none".
TRUNCDET_NOISE_GAP_FRAC = 0.25  # noise is sampled from this lowest quantile of model gap

# Which arms the OPTICS can physically clip. On this setup only the left (blue) arm can be
# occluded, and will stay that way for the foreseeable future (operator, 2026-07-20). That
# is not a tuning preference -- it is a fact about the hardware, so a RIGHT-side detection
# is not a marginal call to be thresholded, it is a KNOWN FALSE POSITIVE with probability 1.
# Measured the same day: on a confirmed-clean beam the detector reported a right cut at
# ~808.5 nm on 42/195 frames, and during a real LEFT clip it reported side="both" (real
# left + phantom right at ~811 nm) on 17% of frames -- every one of those right edges sat
# in the red wing where the Gaussian envelope has rolled off and fringe contrast is
# naturally low, which the dead-fringe test misreads as a clip. Rather than chase that with
# a wing-aware threshold, we use the physical constraint: suppress right-side detection
# outright. Set this back to ("left", "right") if the interferometer is ever rebuilt so the
# red arm can be clipped -- at which point the wing false-positive must be solved properly.
PHYSICAL_CUT_SIDES = ("left",)

# --- Envelope-centre pin ----------------------------------------------------------
# The upper-envelope Gaussian mean muU is the anchor the cubic phase is expanded about
# (l0 tracks it). Left-clipping the beam pushes the *intensity* peak rightward -- measured
# 2026-07-20, muU walks from ~802.2 on a clean frame to ~802.9 under a left clip -- and the
# phase reported back at the fixed reference then inherits noise from an anchor that has
# wandered ~0.7 nm away with uncertain curvature (phase@802 scatter 0.72 rad vs a 0.314
# budget). Pinning muU to a narrow band around a KNOWN centre stops the clip from dragging
# the anchor. It is a band, not a hard value, so a clean frame still fits its own centre;
# it is narrow because the beam centre does not move on this setup -- only the clip's effect
# on the intensity peak does, and that is exactly what we are refusing to follow.
#
# ENV_CENTRE_DEFAULT is only the default: the operator drags it live (it is not derivable
# from a truncated stream, and a wrong pin is visible and correctable on the chart). 802.0
# is the nominal beam centre. ENV_CENTRE_TOL is the half-width of the pin.
ENV_CENTRE_DEFAULT = 802.0
ENV_CENTRE_TOL = 0.1

# --- Null localization (symmetry / two-sided prominence of the Hilbert |f|) ---
# The Hilbert instantaneous frequency is UNSIGNED (== |f|); a genuine null is a V
# in |f| that rises on BOTH sides of an interior minimum. We smooth |f|, find that
# minimum, and require the V to be prominent enough on each side. A monotonic (no
# in-window null) trace has |f| rising on one side only, so the two-sided
# prominence collapses -- that is how "no null exists" is detected, instead of the
# old argmin+fold that FABRICATED a null on every trace.
SMOOTH_FRAC = 0.06    # gaussian sigma for smoothing |f|, as a fraction of core length
MAX_NULL_CAND = 3     # how many candidate nulls (deepest interior |f| minima, i.e.
                      # zero-derivative dips) to try as anchors; each competes as a
                      # "with-null" seed against the "no-null" seed and the SSE decides,
                      # so an inaccurate or false candidate cannot poison the fit

# --- Two-trim + null-flip seed core (replaces BIC/order selection) -------------
# The seed for the full-signal fit is chosen by the two-trim scheme, validated on the
# synthetic grid (endtrim_synth.py, 100%/216) and on the six real traces: the contrast
# crop above already removed the low-visibility wings (trim 1); here we take the Hilbert
# phase, drop the top/bottom PHASE_TRIM of its VALUE range (trim 2) to cut the folded
# null plateau, and polyfit a quadratic seed of the surviving one-sided arm. That seed
# is right whenever the phase is (near-)monotonic. Where the fringe frequency crosses a
# zero-path null the monotonic Hilbert phase FOLDS, so for each |f| dip we also build a
# SIGNED parabolic seed (fit_freq_null + recover_offset) that carries the real chirp
# through the null, and take that "flip" only if it cuts the fringe SSE by >= the margin.
PHASE_TRIM = 0.15       # phase-VALUE trim fraction (validated value; endtrim_synth TRIM)
FLIP_SSE_MARGIN = 0.15  # take a null flip only if it cuts fringe SSE by >= this fraction
ALIVE_WIN_FRAC = 0.07   # rolling-RMS window (fraction of core) for the fringe-alive mask
ALIVE_THR = 0.45        # keep samples whose local fringe RMS exceeds this (n rides
                        # [-1,1] so a live fringe has RMS ~0.7; post-clip noise is below)
# --- Truncated-END trim (before Hilbert): the OSCILLATION mask ----------------
# fringe_alive keys on the MAGNITUDE of n (rolling RMS), which a truncated arm defeats:
# past the clip only one arm survives, so with the envelopes mis-estimated across the
# collapsed gap n sits PINNED near +-1 (high |n|) while it no longer OSCILLATES. Measured
# on a real left-clip: the dead band reads rolling-|n| ~0.9 (higher than the live core)
# yet its local oscillation is ~0.05. So the end-trim keys on oscillation -- the rolling
# STD of n about its local mean -- not magnitude. A genuine null reads low oscillation too,
# but it is LOW-|n| (n passes through 0) and, decisively, INTERIOR (fringes resume past it),
# so trimming only edge-touching dead runs leaves it untouched. Threshold is a low floor: a
# dead band is ~0.05, a null bottom ~0.11, the weakest real fringe ~0.25, so 0.15 sits in
# the gap and only trims genuinely oscillation-free ends.
OSC_WIN_FRAC = 0.07     # rolling-std window (fraction of core) for the oscillation measure
OSC_DEAD_THR = 0.15     # below this local oscillation, a sample carries no live fringe
MAX_FLIP_CAND = 2       # deepest null candidates to try flipping (each costs one fit)

# --- Scan-free deterministic pipeline (replaces the recovery scan) -------------
# The derivative "coarse cut" idea was tried and DROPPED (2026-07-19): measured net-negative
# on every harness -- it over-crops good traces into false-drops (even after the smoothing
# and one-sided-envelope fixes it added 16 false-drops vs no coarse cut on the realistic
# synth suite) and it never fixed the truncation corner it was built for. The scan-free path
# is: full-window envelopes -> contrast crop -> oscillation end-trim -> Hilbert -> TRUNCATION
# TRIM -> seed/fit. On the realistic suite this beats the shipped scan (99.6% vs 98.8%, fewer
# wrong) with no 12 s scan. Truncation is handled by trimming the core; two methods, both
# built for comparison (TRUNC_METHOD):
SCANFREE = False        # master flag: True routes analyze()/_analyze_once through the
                        # deterministic scan-free path and SKIPS the recovery scan.
TRUNC_METHOD = "phase"  # "phase" | "knife" | "none":
                        #   phase = conditional phase-VALUE trim (baseline both ends, more on
                        #           a detected dead side); reuses the validated phase-trim idea.
                        #   knife = knife-edge cut detector (the physical cut is 0.2-0.3 nm
                        #           wide, so find that sharp oscillation edge with a dead zone
                        #           beyond it and cut there).
                        #   none  = no truncation handling (the plain end-trimmed core).
# -- conditional phase trim (TRUNC_METHOD="phase") --
PHASE_TRIM_BASE = 0.05  # baseline phase-VALUE trim at BOTH ends. The flat dead zone accrues
                        # ~no phase, so the legacy 15% both-ends trim wasted good signal; 5%
                        # is a light residue clean-up.
PHASE_TRIM_DEAD = 0.25  # phase-VALUE trim at an end with a DETECTED dead region (the clip).
PHASE_DEAD_RATIO = 0.45 # an end is "dead" if its local oscillation is below this * the core
                        # median oscillation.
PHASE_DEAD_FRAC = 0.12  # fraction of the core at each end examined for the dead test.
# -- knife-edge cut detector (TRUNC_METHOD="knife") --
KNIFE_MIN_NM = 0.12     # the knife-edge cut is physically ~0.2-0.3 nm wide; accept a high->
KNIFE_MAX_NM = 0.55     # dead oscillation transition whose width falls in this range.
KNIFE_DEAD_RATIO = 0.30 # beyond the edge, oscillation must fall below this * core median...
KNIFE_MIN_DEAD_NM = 1.0 # ...over a dead run at least this long, reaching the window edge.
KNIFE_DEEPEN_NM = 0.50  # once the sharp edge is found, cut this much FURTHER into the live
                        # side. The knife cut lands at the FIRST live sample, but the first
                        # ~0.3-0.5 nm past it is a weak transition sliver (low contrast,
                        # dragged envelope) that pulls the fit. Cutting to the first CLEAN
                        # fringe (measured: trunc2020 needs ~800.2, edge is at 799.8) keeps
                        # the knife's precise null-safe detection but drops the sliver.

# --- Phase-order selection (BIC, replaces ridge regularization) ---------------
# Order q means the instantaneous frequency is a degree-(q-1) polynomial: phase = c0..cq
# (q=1 carrier / q=2 chirp / q=3 +TOD). Instead of a hand-tuned ridge, BIC = n·ln(SSE/n)
# + k·ln(n) penalizes extra terms, so spurious TOD is rejected automatically -- no tuning
# knob. Applied only when the frequency is one-signed (no null); at a null TOD is
# unidentifiable and the order is capped at 2 (see core_seed_fit).

# --- Soft null penalty (on the |f| V-fit that provides the null-flip seed) -------
# The null seed fits the Hilbert |f| with a frequency polynomial whose value at the
# anchor is softly pulled to zero, so the seed genuinely has its null at the located
# minimum. Soft (a penalty, not a hard constraint) so real data still moves it.
NULL_PEN_FREQ = 3.0   # weight of the f(u=0)->0 penalty in the |f| (cycles/nm) fit

# --- Final full raw-signal fit (cubic/TOD phase, envelopes held fixed) ---
SIGNAL_LOSS_FRAC = 1.0  # soft-L1 scale as a fraction of the local half-amplitude (counts)
# After the frozen-envelope phase fit, OPTIONALLY re-solve the phase AND both envelope
# Gaussians jointly (joint_env_refine): the frozen quantile envelope never sees the fringe,
# so its troughs ride above the raw counts (a `half` error r2_fringe is blind to). Freeing
# the envelope lifts the RAW reconstruction (r2_sig) markedly.
#
# DISABLED by default -- measured 2026-07-19 (joint_env_fit_figs.py / the synth_truncation
# safety sweep): it is a clean win on UNTRUNCATED traces (0 new wrong, r2_sig 0.988->0.995),
# but on TRUNCATED traces the freed envelope absorbs the missing-arm misfit and PASSES the
# trust gate while the phase is actually wrong (+23 confidently-wrong on the dim sweep), and
# it suppresses the recovery scan that used to catch them (missed 196->295). The truncated
# corner's pretty 0.938->0.996 is in that false-confidence bucket -- a mirage, not recovery.
# Safe use requires gating to traces the FROZEN fit already _explains AND side=='none' (so it
# can never change a recovery decision); that needs a 2nd pass or a tail refactor. Left off
# pending that decision. See joint_env_refine + joint_env_fit_figs.py / envelope_candidates.
JOINT_ENV_FIT = False
# Pipeline B (envelope -> knife -> refit): after the knife locates the dead sliver, refit the
# Gaussian envelopes on the full window MINUS that deadzone. The prelim envelope is fit on the
# full window (contaminated by the sliver on a truncated trace); the knife then gives the dead
# boundary, and this single refit puts the envelope on the correct domain -- replacing the
# recovery scan with one clean pass. Fit on full-minus-deadzone (NOT the narrow contrast core,
# which degenerates the gap Gaussian). Only fires when the knife actually found a cut.
DEADZONE_REFIT = False

# DEAD END, measured 2026-07-19 -- do not re-add a global-kernel Hilbert detrend.
# The idea was sound: n = (y-mid)/half should be zero-mean, and in the tails `half` is small
# so envelope error becomes a large offset in n, which the Hilbert transform mixes into the
# phase. Implemented as n - gaussian_filter1d(n, sigma) with sigma from the mean
# zero-crossing period. Result on the seven real traces: SIX were bit-identical (the full
# fit dominates the seed, same finding as the flip-scan null experiment), and the seventh,
# da_15.95ga_-75, COLLAPSED -- r2_fringe 0.645 -> -0.116.
# Mechanism: that trace has a null, so its local period runs 4.2 samples in the blue third
# to 8.0 in the middle. ONE global kernel cannot be "about one period" everywhere -- at
# sigma=5.3 it was 0.8x the local period in the blue third, where it tracked the fringe and
# ate 14.7% of the fringe RMS, while removing only 1.4% in the middle where the baseline
# actually lives. Any retry needs a LOCAL, chirp-following kernel, and must first show that
# the seed matters at all: six of seven traces say it does not.

# --- Accuracy spec / trust gate ---------------------------------------------
# The accuracy the pipeline is REQUIRED to deliver (mirrors the synth_test tolerances:
# 1% relative on the frequency c1 and chirp c2 with small absolute floors near zero, a
# mod-2pi budget on c0, absolute on TOD). The fit's own covariance, propagated to the
# spectral centre, is checked against this: a trace that cannot meet it is reported as
# "underdetermined" rather than handed over as a number that looks like all the others.
# This matters for truncated traces, where a clip costs lever arm and the coefficients
# get genuinely underdetermined WITHOUT the residual showing it (R^2 stays ~0.96).
TRUST_REL = 0.01        # relative spec on c1, c2
TRUST_FLOOR_C1 = 0.05   # rad/nm     absolute floor near c1 = 0
TRUST_FLOOR_C2 = 0.02   # rad/nm^2   absolute floor near c2 = 0
TRUST_TOL_C0 = 0.314    # rad        (0.05 * 2pi, the phase-stabilization budget)
                        # Was 0.126 (2% of 2pi), which was never checked against what the
                        # loop actually needs -- and it was the BINDING term on real traces.
                        # Measured 3*sigma/tol on the seven real traces: da_15.95ga_-55.29
                        # failed at c0=2.03 and da_15.95ga_-75 at c0=1.49, i.e. both real
                        # rejections were the PHASE tolerance, not curvature. (The synthetic
                        # gallery's "all 11 wrong fail on c2, never c0" is a synth-only
                        # result; do not generalise it to real data.)
                        # 5% of 2pi is the honest spec, per the user 2026-07-19: the beam
                        # already jitters more than 2% shot to shot even while stabilized,
                        # and phase stabilization was never meant to suppress that noise. It
                        # exists to stop the slow drift that SMEARS phase across a long scan
                        # until the information is wiped out entirely. A gate tuned an order
                        # of magnitude below the jitter it lives in rejects frames for an
                        # error the loop does not care about.
TRUST_TOL_C3 = 0.006    # rad/nm^3   noise-limited TOD floor

# --- Accuracy gate: is the reference INSIDE the data that supports it? -------------
# `trust_at` is a PRECISION test -- propagated covariance against the spec above. It is
# blind to the failure mode measured on real truncated data 2026-07-20, which is a BIAS:
# when one arm is clipped the fitted core shrinks and its centroid `l0` slides off the
# operator's reference, and the phase quoted back at that reference then splits into two
# populations that the covariance cannot tell apart. Measured over 58 consecutive FITDIAG
# frames from one run, grouped by whether the core had displaced:
#
#     phase @ 802 nm (what the loop consumes)   4.128 rad   vs   2.734 rad
#     |ref - l0| / core half-span                0.15-0.29   vs   0.00-0.04
#
# A 1.40 rad step, on frames that ALL reported trust=True, rms_frac ~0.10, inl 97-100%.
# Nothing in the residual or the covariance objects, because the fit is precisely
# explaining a core that has moved. This constant is the missing accuracy term: the
# reference must sit near the middle of the support, not merely inside it.
#
# 0.12 sits in the gap the two measured populations leave (0.04 -> 0.15) with margin on
# both sides. It is a SEPARATION threshold read off real data, not a derived quantity --
# re-measure it if the spectrometer window or the core crop changes.
REF_MAX_OFFSET_FRAC = 0.12

TRUST_NSIG = 3.0        # require NSIG * sigma to fit inside the spec.
                        # CALIBRATED, not derived. The Gauss-Newton covariance is an
                        # under-estimate of the true error here for two reasons it cannot
                        # see: the envelopes (mid/half) are held FIXED through the phase
                        # fit, so their error biases the phase without ever entering the
                        # covariance; and the phase noise is not the white intensity noise
                        # the SSE/dof scaling assumes. NSIG absorbs both.
                        #
                        # This knob IS the accuracy/yield trade, and both sides are specced:
                        # accuracy of REPORTED fits >= 98%, and <= 5% of the fits that would
                        # have been RIGHT may be thrown away (user, 2026-07-16). Swept on
                        # verify_phase.py over the full operating space (2470 fits,
                        # brightness continuum x carrier x legal clips, two seeds pooled) --
                        # accuracy / false-drop rate (declined-but-would-have-passed, as a
                        # fraction of all good fits):
                        #     2.00 -> 97.97% /  0.8%     3.50 -> 98.89% /  6.2%
                        #     2.50 -> 98.31% /  2.0%     4.00 -> 98.99% /  9.0%
                        #     3.00 -> 98.54% /  3.7%  <- shipped: margin on BOTH bars
                        #     3.25 -> 98.69% /  4.9%     5.00 -> 99.31% / 15.0%
                        # 3.25 also passes but sits ON the 5% line with no headroom for
                        # seed-to-seed variation, so 3.0 is the defensible pick.
                        #
                        # Do NOT chase 99.9% with this knob. It saturates while the yield
                        # collapses: 16.0 throws away TWO THIRDS of all good fits and STILL
                        # misses 99.9% (1 wrong in 726, where counting error alone is
                        # +-0.14%). The residual ~1-1.5% is not something the gate can see --
                        # it is real TOD at the identifiability floor (true c3 = +-0.005)
                        # that BIC declines to model, whose un-modelled cubic then biases c1
                        # by ~0.06-0.10 rad/nm; that only breaks the spec when c1 is SMALL,
                        # because the tolerance is max(1%*|c1|, 0.05). Half of those have an
                        # in-window null, where order is capped at 2 BY DESIGN since TOD is
                        # unidentifiable at a null. Getting past it means revisiting the BIC
                        # order selection -- and buying spurious-TOD failures on the ~1/3 of
                        # traces whose true c3 is genuinely 0 -- not tightening this gate.

# --- Adaptive phase reference ------------------------------------------------
# The phase is reported at a REFERENCE wavelength. The default is the intensity centroid
# muU (~802 nm): that is where the pulse energy is and what phase stabilization asks about.
# But a clip near the core leaves 802 sitting a nm or two from the edge of the surviving
# fringes, and the phase there stops being supported by data -- measured, a cut 1-2 nm from
# 802 drops reported accuracy to ~87-90% and makes the trust gate decline ~40% of fits it
# would otherwise have got right.
#
# So when the primary reference is untrustworthy we fall back to the CORE CENTROID l0 =
# mean(core), which is both the fit's own basis origin (d = 0, so no covariance is
# propagated and the answer is as well-conditioned as the data allow) and, for a one-sided
# clip, automatically the point furthest from the cut. The reference actually used is
# reported in R["ref_wl"], and R["ref_fallback"] says whether it moved.
#
# Switching reference is not free for the app -- a stabilization loop locked to one
# wavelength must not chatter between two -- so the app passes a ReferencePolicy that
# requires REF_HYST consecutive traces before switching EITHER way. The standalone and the
# harness pass no policy, which means switch immediately (per user: "for testing you can
# immediately do the fallback").
REF_HYST = 5          # consecutive traces agreeing before the app changes reference

# --- Frequency plot ---
FREQ_YLIM = 6         # frequency-axis cap (cycles/nm)

# --- Spectral fringe frequency -> generated RF frequency ----------------------
# Dispersive time-mapping calibration (user, 2026-07-19): 9 nm of spectrum maps to ~320 ps
# of delay, assumed LINEAR over the band. So a spectral fringe of period P nm becomes a
# temporal beat of period P * (320/9) ps, and the RF frequency it generates is the
# reciprocal of that:
#     f_RF [GHz] = f [cycles/nm] * (9 nm / 320 ps) = f * 28.125
# The "roughly" in the calibration is the dominant error here -- it is a stated ~1 sig fig,
# so do not report this to a precision the calibration cannot carry (see format_ghz).
NM_PER_PS = 9.0 / 320.0          # 0.028125 nm/ps
GHZ_PER_CYC_PER_NM = 1e3 * NM_PER_PS   # 28.125 GHz per (cycle/nm)

# The band the range is quoted over: 802 +- 9 nm. This deliberately EXTRAPOLATES beyond the
# fitted core (typically ~150-185 points over 6-9 nm), because the question the readout
# answers is "what RF does this shot generate across the pulse", not "what did we fit".
# Extrapolating a cubic is exactly where a badly-determined c2/c3 shows up, multiplied by
# d^2/d^3 -- which is why the readout must be gated on shape_ok, not trust_ok.
RF_BAND_CENTRE_NM = 802.0
RF_BAND_HALFWIDTH_NM = 9.0

TAU = RATIO / (RATIO + 1.0)   # ~0.91 quantile: fit hugs the upper envelope of the fringes
# =============================================================================


def fringe_freq_cyc_per_nm(csig, u):
    """Instantaneous spectral fringe frequency (cycles/nm) at offset u = lambda - l0.

    dPhi/du / 2pi for the fitted cubic. SIGNED -- the sign flips through a null, and callers
    that want a frequency magnitude must take abs() themselves. Keeping the sign here is what
    lets rf_range_ghz find the null (where |f| -> 0) instead of silently reporting the
    turning point as a minimum of a quantity that never went negative.
    """
    c = np.asarray(csig, float)
    u = np.asarray(u, float)
    return (c[1] + 2.0 * c[2] * u + 3.0 * c[3] * u ** 2) / (2.0 * np.pi)


def rf_range_ghz(csig, l0, centre_nm=None, halfwidth_nm=None, n=401):
    """RF frequency range (min_GHz, max_GHz) generated across the quoted spectral band.

    Returns the extremes of |f| over lambda in centre +- halfwidth, converted through the
    dispersive time-mapping calibration (see GHZ_PER_CYC_PER_NM). Sampled on a grid rather
    than evaluated at the two endpoints, because f is a PARABOLA in u whenever c3 != 0 and a
    line otherwise: with a chirp, the extreme frequency is often interior, and with an
    in-band null |f| touches ZERO in the middle while both endpoints are large. Endpoint-only
    evaluation would report a range that excludes both the true minimum and, past the vertex,
    the true maximum.

    The band deliberately extends past the fitted core -- this is extrapolation, and it is
    only as good as c1/c2/c3. Gate the readout on R["shape_ok"].
    """
    c = RF_BAND_CENTRE_NM if centre_nm is None else float(centre_nm)
    h = RF_BAND_HALFWIDTH_NM if halfwidth_nm is None else float(halfwidth_nm)
    u = np.linspace(c - h, c + h, int(n)) - float(l0)
    f = np.abs(fringe_freq_cyc_per_nm(csig, u)) * GHZ_PER_CYC_PER_NM
    return float(np.min(f)), float(np.max(f))


def format_ghz(v):
    """Nearest GHz, but never fewer than two significant figures (100, 20, 1.5).

    Rounding to the nearest GHz is right for the tens-to-hundreds values this readout
    normally shows, but it destroys a 1.4 GHz reading and turns a 0.4 GHz one into "0". So
    the integer form is used only where it already carries two figures, and below ~10 GHz we
    add decimals to keep two. Thresholds are 9.95/0.995/0.0995 rather than 10/1/0.1 so a
    value that ROUNDS UP across the boundary (9.96 -> "10") is formatted by the rule it lands
    in, not the one it started in.
    """
    v = float(v)
    a = abs(v)
    if not np.isfinite(v):
        return "--"
    if a >= 9.95:
        return f"{v:.0f}"
    if a >= 0.995:
        return f"{v:.1f}"
    if a >= 0.0995:
        return f"{v:.2f}"
    return f"{v:.3f}"


def format_rf_range(lo_ghz, hi_ghz, shape_ok=True):
    """The overlay string: "12-47 GHz", or a single value when the range is degenerate.

    ``shape_ok=False`` marks the number as unsupported rather than hiding it -- the phase can
    still be locked on such a frame, so blanking the readout would misreport a working shot,
    while quoting it bare would launder an extrapolated c2 the fit cannot vouch for.
    """
    lo, hi = float(lo_ghz), float(hi_ghz)
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return "-- GHz"
    # Collapse to one number when the two ends round to the same displayed value: an
    # unchirped shot is a single frequency, and "34-34 GHz" reads as a bug.
    s_lo, s_hi = format_ghz(lo), format_ghz(hi)
    body = s_lo if s_lo == s_hi else f"{s_lo}-{s_hi}"
    return f"{body} GHz" + ("" if shape_ok else " (unverified)")


def gauss(x, a, mu, sigma, off):
    return a * np.exp(-(x - mu) ** 2 / (2 * sigma ** 2)) + off


def live_band(x, y):
    """Physical detector band: drop the zero-padded wings the SPM-001-M files carry
    (they pad outside ~740-860 nm). Any baseline statistic MUST mask these first or the
    zeros drag the continuum -- and with it the offset -- straight down."""
    nz = y > 0
    if not nz.any():
        return np.zeros_like(x, bool)
    return (x >= x[nz][0]) & (x <= x[nz][-1])


def baseline_anchor(x, y, centre=None):
    """(U_base, D) of the continuum, measured OUTSIDE the bump on the FULL frame.

    U_base = mean of points >= P95 of the baseline region, L_base = mean of points <= P5,
    D = U_base - L_base. The upper continuum U_base is the level the upper envelope must
    decay to, so it is what pins the Gaussian's offset; D sizes the honest uncertainty
    band around it.

    Must be given the FULL frame, not the ZOOM slice -- the whole point is that ZOOM
    contains no continuum. Returns None when too little baseline is in view, in which case
    the caller stays unbounded (i.e. today's behaviour)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if centre is None:
        centre = 0.5 * (ZOOM[0] + ZOOM[1])
    m = live_band(x, y) & (np.abs(x - centre) > ANCHOR_EXCLUDE_NM)
    if np.count_nonzero(m) < ANCHOR_MIN_PTS:
        return None
    v = y[m]
    hi, lo = np.percentile(v, 95), np.percentile(v, 5)
    U_base = float(np.mean(v[v >= hi]))
    L_base = float(np.mean(v[v <= lo]))
    return U_base, U_base - L_base


def anchor_bounds(anchor):
    """Admissible offset band (U_base -+ K*D) from a baseline_anchor() result."""
    if anchor is None:
        return None
    U_base, D = anchor
    return (U_base - ANCHOR_K * D, U_base + ANCHOR_K * D)

def pinball_loss(p, x, y):
    # residual > 0 means data above fit => fit is BELOW the maximum => penalize TAU
    r = y - gauss(x, *p)
    return np.sum(np.where(r > 0, TAU * r, (TAU - 1.0) * r))

def pinball_grad(p, x, y):
    # NOT USED IN PRODUCTION -- kept only so envelope_diag.py can reproduce the
    # L-BFGS-B-vs-Nelder-Mead comparison that condemned it. Do NOT wire this back into
    # fit_upper_envelope: this is a SUBGRADIENT of a piecewise-linear loss, not a
    # gradient of a smooth one, and pairing it with L-BFGS-B is the regression described
    # in fit_upper_envelope's docstring (it quits at a 25% worse loss and calls it
    # convergence). It only becomes legitimate if the kink is Huberized first.
    #
    # d/dp = sum_i w_i * (-dg/dp_i), with w_i = TAU where data is above the fit,
    # TAU-1 where below.
    a, mu, sig, off = p
    E = np.exp(-(x - mu) ** 2 / (2 * sig ** 2))
    r = y - (a * E + off)
    w = np.where(r > 0, TAU, TAU - 1.0)
    dg_da = E
    dg_dmu = a * E * (x - mu) / sig ** 2
    dg_dsig = a * E * (x - mu) ** 2 / sig ** 3
    return -np.array([np.sum(w * dg_da), np.sum(w * dg_dmu),
                      np.sum(w * dg_dsig), float(np.sum(w))])

def fit_upper_envelope(x, y, off_bounds=None, mu_bounds=None):
    """Gaussian hugging the upper envelope of the fringes: warm start from a symmetric L2
    fit, then refine under the asymmetric pinball loss.

    `off_bounds` bounds the offset to the continuum measured off the full frame (see
    baseline_anchor). Inside ZOOM there is no baseline to pin it, and the tau-quantile
    loss exploits that by floating the offset up.

    `mu_bounds` pins the Gaussian mean (the envelope centre) to a narrow band -- the
    operator's dragged ENV_CENTRE +- ENV_CENTRE_TOL. See ENV_CENTRE_DEFAULT: a one-sided
    clip drags the intensity peak, and following it moves the phase anchor; pinning mu
    holds it. None leaves the centre free, which is the standalone/harness behaviour.

    The refinement is Nelder-Mead. This once used L-BFGS-B on the analytic subgradient,
    which really did stop at loss 7182.5 after 19 iterations (off=255.1, sigma=3.41) where
    NM reaches 5381.1 (off=164.9, sigma=3.73) -- but measured 2026-07-16, the cause was NOT
    that the pinball kink defeats a quasi-Newton method. It was a units bug: the caller
    passed Nelder-Mead's ABSOLUTE f-tolerance (FIT_FATOL=1e-4) as L-BFGS-B's `ftol`, which
    is a RELATIVE reduction criterion whose default is 2.22e-9 -- ~45000x looser, so it quit
    early and reported convergence. At any tighter ftol L-BFGS-B lands on NM's optimum
    (off=164.2, loss 5381.0, 50 iters) and is NOT erratic on wide windows (sigma=3.87,
    matching NM, against the 5.31 once claimed). The analytic gradient is correct -- it
    agrees with central differences to 6 significant figures.

    So NM is kept not because L-BFGS-B is broken but because it is unconditionally safe on a
    kinked loss and costs nothing here (~3.3 -> ~15 ms per fit, against 28-80 ms fits and a
    100 ms exposure on an acquisition-limited pipeline). If that ever stops being free,
    reinstating L-BFGS-B is legitimate PROVIDED ftol is left at its default. NB the App port
    (App_Apps fringe_fit.py) still carries the original bug: `fit_ftol: float = 1e-4` passed
    at the minimize() call. Fixing that one line is its real fix; it does not need this swap."""
    off0 = float(np.median(y))
    imax = int(np.argmax(y))
    p0 = [y[imax] - off0, x[imax], SIGMA_INIT, off0]
    try:
        p0, _ = curve_fit(gauss, x, y, p0=p0, maxfev=FIT_MAXFEV)
    except RuntimeError:
        # The L2 warm start is an optimization, not a requirement -- fall back to the
        # moment-based guess and let the pinball refinement below do the work. It fails
        # to converge when the samples are a narrow, off-centre slice of the Gaussian's
        # flank, which is exactly what a one-sided arm truncation leaves behind: a
        # monotonic sliver does not pin (amp, mu, sigma, offset). Such a trace has too
        # few fringes to be trusted anyway and is caught downstream by the trust gate --
        # but it must not take the whole analysis down with a RuntimeError.
        pass
    bounds = None
    if off_bounds is not None or mu_bounds is not None:
        p0 = list(p0)
        mb = tuple(mu_bounds) if mu_bounds is not None else (-np.inf, np.inf)
        ob = tuple(off_bounds) if off_bounds is not None else (-np.inf, np.inf)
        p0[1] = float(np.clip(p0[1], *mb))           # start the centre inside its band
        p0[3] = float(np.clip(p0[3], *ob))           # ...and the offset inside its band
        bounds = [(-np.inf, np.inf), mb, (-np.inf, np.inf), ob]
    res = minimize(pinball_loss, p0, args=(x, y), method="Nelder-Mead", bounds=bounds,
                   options={"maxiter": FIT_MAXITER, "maxfev": FIT_MAXITER,
                            "xatol": FIT_XATOL, "fatol": FIT_FATOL})
    return res.x

def smooth_absf(x, f_inst):
    """Smoothed unsigned Hilbert frequency |f| (gaussian, sigma ~ SMOOTH_FRAC*N)."""
    return gaussian_filter1d(np.abs(f_inst), max(int(len(x) * SMOOTH_FRAC), 3))


def null_candidates(x, fs):
    """Candidate null locations = interior local minima of smoothed |f| (points where
    its derivative crosses zero into a dip). Returned as (prominence, x_null) sorted
    deepest-V first, capped at MAX_NULL_CAND. A monotonic |f| has no interior minimum,
    so it yields no candidate -- exactly the no-null case. We do NOT threshold on
    prominence here: every candidate is offered to the fit, which accepts a null only
    if fitting one actually lowers the residual (so false candidates are harmless)."""
    N = len(x)
    m = max(int(0.08 * N), 2)
    fmax = float(np.max(fs)) + 1e-9
    out = []
    for i in range(m, N - m):
        if fs[i] <= fs[i - 1] and fs[i] < fs[i + 1]:          # local minimum (dip)
            prom = min(np.max(fs[:i + 1]) - fs[i], np.max(fs[i:]) - fs[i]) / fmax
            out.append((float(prom), float(x[i])))
    out.sort(reverse=True)
    return out[:MAX_NULL_CAND]


def fringe_alive(nc):
    """Boolean mask of the fringe-bearing samples of the normalized core: local RMS of
    the normalized fringe above ALIVE_THR. This cuts a truncated tail (where n is noise)
    that the smooth Gaussian contrast crop cannot see -- a lighter cut than detect_truncation,
    which on a null+truncation clip can miss the side entirely. It also trims the immediate
    null neighbourhood (n->0 there), which costs no phase information. A clean untruncated
    core is alive throughout, so its path is untouched."""
    w = max(int(len(nc) * ALIVE_WIN_FRAC), 5)
    roll = np.sqrt(uniform_filter1d(np.asarray(nc, float) ** 2, w))
    return roll > ALIVE_THR


def fringe_oscillating(nc):
    """Local oscillation amplitude of the normalized core: rolling STD of n about its own
    local mean (= rolling RMS of the AC part). Unlike fringe_alive this ignores a pinned DC
    excursion, so it reads ~0 through a truncated dead band even where |n| is high. Used only
    to trim truncated ENDS (see OSC_DEAD_THR); a null dips too but is protected by being
    interior."""
    x = np.asarray(nc, float)
    w = max(int(len(x) * OSC_WIN_FRAC), 5)
    m = uniform_filter1d(x, w)
    var = uniform_filter1d(x ** 2, w) - m ** 2
    return np.sqrt(np.maximum(var, 0.0))


def _dead_ends(n):
    """Return (left_dead, right_dead, osc, med): is the fringe oscillation at each END of the
    normalized core `n` collapsed relative to the core median? Used by both truncation
    methods to locate the clipped side. A clip leaves the dying edge with far less local
    oscillation than the live core; a clean trace's ends are ~as lively as its middle."""
    osc = fringe_oscillating(n)
    med = float(np.median(osc)) + 1e-12
    k = max(int(round(PHASE_DEAD_FRAC * len(n))), 3)
    left_dead = float(np.mean(osc[:k])) < PHASE_DEAD_RATIO * med
    right_dead = float(np.mean(osc[-k:])) < PHASE_DEAD_RATIO * med
    return left_dead, right_dead, osc, med


def conditional_phase_trim(n, phase):
    """TRUNCATION METHOD "phase". Return (keep_mask, info) over the core.

    Trim in phase-VALUE space (the legacy idea, made conditional): PHASE_TRIM_BASE off BOTH
    ends as light residue clean-up, and PHASE_TRIM_DEAD off an end whose fringe oscillation
    has collapsed (a clip). The flat dead zone accrues ~no phase, so trimming it costs no
    lever arm while removing the sliver that drags the fit. The phase ramp's sign maps each
    END (index 0 / index -1) to a phase-VALUE extreme (low / high)."""
    n = np.asarray(n, float); phase = np.asarray(phase, float)
    left_dead, right_dead, _, _ = _dead_ends(n)
    left_trim = PHASE_TRIM_DEAD if left_dead else PHASE_TRIM_BASE
    right_trim = PHASE_TRIM_DEAD if right_dead else PHASE_TRIM_BASE
    lo, hi = float(phase.min()), float(phase.max())
    span = hi - lo + 1e-12
    inc = phase[-1] >= phase[0]            # phase increases with index?
    low_trim, high_trim = (left_trim, right_trim) if inc else (right_trim, left_trim)
    keep = (phase >= lo + low_trim * span) & (phase <= hi - high_trim * span)
    side = ("both" if left_dead and right_dead else "left" if left_dead
            else "right" if right_dead else "none")
    return keep, {"side": side, "left_trim": left_trim, "right_trim": right_trim}


def knife_edge_cut(x, n):
    """TRUNCATION METHOD "knife". Return (keep_mask, info) over the core.

    The interferometer arm is truncated with a KNIFE EDGE, so the cut is physically only
    ~0.2-0.3 nm wide: the fringe oscillation drops from live to the floor across that narrow
    span, with a dead zone (no fringe) beyond it to the spectral edge. Detect exactly that
    signature at each end -- a sharp high->dead oscillation transition of width
    KNIFE_MIN_NM..KNIFE_MAX_NM with a >= KNIFE_MIN_DEAD_NM dead run past it -- and cut the
    core at the edge. A gradual visibility roll-off (no knife) is NOT a sharp edge, so a
    clean trace is left alone."""
    x = np.asarray(x, float); n = np.asarray(n, float)
    N = len(x); dx = float(np.mean(np.diff(x)))
    _, _, osc, med = _dead_ends(n)
    dead = osc < KNIFE_DEAD_RATIO * med
    live = osc > 0.7 * med
    dmin = int(round(KNIFE_MIN_DEAD_NM / dx))
    ddeep = max(int(round(KNIFE_DEEPEN_NM / dx)), 0)   # extra samples cut into the live side
    keep = np.ones(N, bool)
    info = {"side": "none", "cut_left": None, "cut_right": None, "edge_nm": None}

    def edge_width(lo_i, hi_i):
        """nm span of the transition between the last dead sample <= lo_i and the first
        clearly-live sample >= hi_i."""
        d = np.flatnonzero(dead[:hi_i + 1])
        l = np.flatnonzero(live[lo_i:]) + lo_i
        if not len(d) or not len(l):
            return None
        return abs(x[l[0]] - x[d[-1]])

    # LEFT edge: a dead run from sample 0, then a sharp rise to live.
    if live.any():
        li = int(np.argmax(live))                 # first clearly-live sample
        if li >= dmin:                            # long dead run leads in
            w = edge_width(li - 1, li)
            if w is not None and KNIFE_MIN_NM <= w <= KNIFE_MAX_NM:
                cut = min(li + ddeep, N - 16)     # deepen past the transition sliver
                keep[:cut] = False
                info["side"], info["cut_left"], info["edge_nm"] = "left", float(x[cut]), w
        # RIGHT edge: symmetric.
        hi_ = N - 1 - int(np.argmax(live[::-1]))  # last clearly-live sample
        if (N - 1 - hi_) >= dmin:
            w = edge_width(hi_, hi_ + 1)
            if w is not None and KNIFE_MIN_NM <= w <= KNIFE_MAX_NM:
                cut = max(hi_ + 1 - ddeep, 16)    # deepen past the transition sliver
                keep[cut:] = False
                info["cut_right"], info["edge_nm"] = float(x[cut - 1]), w
                info["side"] = "both" if info["side"] == "left" else "right"
    return keep, info


def fit_freq_null(u, f_inst, u_anchor):
    """Build a QUADRATIC-phase null seed: fit the Hilbert |f| with a LINEAR frequency
    g0 + g1 u (so the phase is quadratic -- no TOD in the seed; TOD is left for the
    full-signal fit to earn), with a soft penalty pulling f(u_anchor) -> 0 so the seed
    genuinely has its null at the candidate. Returns phase coeffs [0, c1, c2, 0]."""
    absf = np.abs(f_inst)
    w = float(np.median(absf))
    ue = float(np.ptp(u)) + 1e-9

    def resid(g):
        return np.concatenate([np.abs(g[0] + g[1] * u) - absf,
                               [NULL_PEN_FREQ * (g[0] + g[1] * u_anchor)]])

    best = None
    for s in (1.0, -1.0):
        sol = least_squares(resid, [0.0, s * w / ue], loss="soft_l1", max_nfev=4000)
        if best is None or sol.cost < best.cost:
            best = sol
    return _freq_to_phase([best.x[0], best.x[1], 0.0])


def _bic_sse(sse, k, n):
    """Bayesian information criterion from a residual sum of squares: the extra-term
    penalty k·ln(n) is what rejects spurious higher-order phase curvature."""
    return n * np.log((float(sse) + 1e-12) / n) + k * np.log(n)


def _freq_to_phase(g):
    """Convert a frequency polynomial g (cycles/nm) to phase coeffs c1..c3 (radians),
    via c_k coefficient of u^k in Phi = 2*pi*integral(f). c0 is set separately."""
    return np.array([0.0, 2 * np.pi * g[0], np.pi * g[1], (2 * np.pi / 3.0) * g[2]])


def recover_offset(u, n, c, q):
    """Given phase-shape coeffs c1..cq from the |f| fit (sign-ambiguous), recover the
    absolute offset c0 by matching cos(Phi) to the normalized fringe n. Because
    cos(base + c0) = cos(base)cos(c0) - sin(base)sin(c0) is LINEAR in (cos c0, sin c0),
    solve it in closed form (project n onto {cos base, sin base}) -- robust even for
    tens of fringes, where a 1-D solve on the oscillatory residual would stick in a
    local minimum. The overall phase sign is irrelevant here (cos is even; the
    ground-truth comparison resolves it) and reconstructs identically. Returns a full
    4-vector with c0 and only c1..cq populated."""
    cc = np.zeros(4)
    for j in range(1, q + 1):
        cc[j] = c[j]
    base = cc[1] * u + cc[2] * u ** 2 + cc[3] * u ** 3
    A = np.vstack([np.cos(base), np.sin(base)]).T
    (a, b), *_ = np.linalg.lstsq(A, n, rcond=None)
    cc[0] = float(np.arctan2(-b, a))
    return cc


def fit_signal(u, y, mid, half, seed, q, f_scale):
    """Refine the phase coeffs on the RAW counts (envelopes fixed), only c0..cq free,
    seeded from the Hilbert |f| fit. Returns full 4-vector (c(q+1)..c3 = 0)."""
    def resid(cc):
        cp = np.zeros(4); cp[:q + 1] = cc
        return signal_model(cp, u, mid, half) - y
    sol = least_squares(resid, seed[:q + 1], loss="soft_l1", f_scale=f_scale, max_nfev=6000)
    cp = np.zeros(4); cp[:q + 1] = sol.x
    return cp


def _joint_trust_ok(u, x, half, csig, order, resid, pU):
    """Is the phase at the spectral centre trustworthy for this (envelope, csig) pair?
    Mirrors _analyze_once's primary-reference trust, evaluated locally so the joint fit can
    be gated without turning a trusted answer untrusted."""
    cov = coef_cov(u, half, csig, order, resid)
    # BOTH gates here, deliberately. This guards the joint envelope refit, which moves the
    # envelope and so can degrade the phase SHAPE as easily as the phase itself; it is not
    # the control loop's accept decision. (Dormant: JOINT_ENV_FIT is False.)
    ok_phase, _, _, ok_shape = trust_at(csig, cov, float(pU[1]) - float(np.mean(x)))
    return bool(ok_phase and ok_shape)


def joint_env_refine(x, y, u, pU0, pLn0, csig0, order, anchor, mid0, half0, n0):
    """Final joint fit: free BOTH envelope Gaussians (pU, pLn) TOGETHER with the phase
    coeffs, seeded from the frozen pinball envelopes + core_seed_fit, under fit_signal's
    soft-L1 loss. The frozen fit holds the envelope fixed and lets the fringe troughs ride
    above the data (a pure `half` error the eye sees but r2_fringe -- which divides the
    envelope out -- cannot); freeing the envelope lets the raw residual pull the troughs down.

    Fully PARAMETRIC by design: a smooth Gaussian gap cannot collapse half->0 at a single
    null the way a per-point / spline envelope does, so this is null-safe where trough-hugging
    empirical envelopes are not (measured: spline drove r2_fringe to <0 on the corner).

    Model: y ~= gauss(x,*pU) - 0.5*gauss(x,*pLn)*(1 - cos(phase_poly(c,u))), i.e. the same
    mid + half*cos with mid=(Ud+Ld)/2, half=(Ud-Ld)/2, Ud=gauss(pU), Ld=Ud-gauss(pLn).

    GATE: adopt the joint fit only if it lowers the raw residual AND does not turn a
    trustworthy phase untrustworthy (per _joint_trust_ok). Otherwise the frozen envelope is
    returned unchanged -- the joint fit can only help or no-op. Returns
    (pU, pLn, mid, half, n, csig, used)."""
    q = int(order)
    f_scale = SIGNAL_LOSS_FRAC * float(np.median(half0)) + 1e-9

    def frozen():
        return pU0, pLn0, mid0, half0, n0, csig0, False

    try:
        xlo, xhi = float(x[0]), float(x[-1]); pad = 0.5 * (xhi - xlo) + 1e-9
        ab = anchor_bounds(anchor)
        offU_lo, offU_hi = ab if ab is not None else (-np.inf, np.inf)
        # offLn >= 0 keeps the gap G > 0 -> half > 0 everywhere (well-posed normalization).
        lo = [0.0, xlo - pad, 1e-2, offU_lo, 0.0, xlo - pad, 1e-2, 0.0] + [-np.inf] * (q + 1)
        hi = [np.inf, xhi + pad, np.inf, offU_hi, np.inf, xhi + pad, np.inf, np.inf] + \
             [np.inf] * (q + 1)
        p0 = np.clip(np.concatenate([pU0, pLn0, csig0[:q + 1]]), lo, hi)

        def resid(p):
            pU = p[0:4]; pLn = p[4:8]; c = np.zeros(4); c[:q + 1] = p[8:8 + q + 1]
            model = gauss(x, *pU) - 0.5 * gauss(x, *pLn) * (1.0 - np.cos(phase_poly(c, u)))
            return model - y

        # max_nfev capped low: the trust gate below keeps only an improved fit, so an
        # early stop is safe (a not-yet-converged solve just fails the gate -> frozen kept),
        # while it bounds the worst-case cost on a pathological near-Nyquist trace.
        sol = least_squares(resid, p0, bounds=(lo, hi), loss="soft_l1",
                            f_scale=f_scale, max_nfev=2000)
        pU = sol.x[0:4]; pLn = sol.x[4:8]
        csig = np.zeros(4); csig[:q + 1] = sol.x[8:8 + q + 1]
        Ud = gauss(x, *pU); G = gauss(x, *pLn)
        mid = Ud - 0.5 * G; half = 0.5 * G
        if not np.all(half > 0) or not np.all(np.isfinite(half)):
            return frozen()
        n = (y - mid) / half

        # gate: must lower the raw residual and not break a trusted phase
        r_fz = signal_model(csig0, u, mid0, half0) - y
        r_jt = signal_model(csig, u, mid, half) - y
        if float(np.sum(r_jt ** 2)) >= float(np.sum(r_fz ** 2)):
            return frozen()
        ok_fz = _joint_trust_ok(u, x, half0, csig0, q, r_fz, pU0)
        ok_jt = _joint_trust_ok(u, x, half, csig, q, r_jt, pU)
        if ok_fz and not ok_jt:                 # never demote a trustworthy answer
            return frozen()
        return pU, pLn, mid, half, n, csig, True
    except Exception:
        return frozen()


def _trim_seed_fit(u, phase, y, mid, half, f_scale, trim, q=2):
    """Phase-VALUE trim on a Hilbert phase, quadratic polyfit seed, full-signal refit.
    Drops the top/bottom `trim` of the phase range (the folded null plateau), polyfits a
    degree-q seed of the surviving arm, and refines it on the raw counts with fit_signal.
    Returns (csig, cph, sse) with cph the polyfit seed and csig the refined coeffs."""
    lo, hi = float(phase.min()), float(phase.max()); span = hi - lo + 1e-12
    keep = (phase >= lo + trim * span) & (phase <= hi - trim * span)
    if keep.sum() < q + 2:
        keep = np.ones_like(phase, bool)
    cph = np.concatenate([np.polyfit(u[keep], phase[keep], q)[::-1], np.zeros(3 - q)])
    csig = fit_signal(u, y, mid, half, cph, q, f_scale)
    sse = float(np.sum((signal_model(csig, u, mid, half) - y) ** 2))
    return csig, cph, sse


def core_seed_fit(u, y, mid, half, n, phase, f_inst, cands, f_scale, origin,
                  trim=PHASE_TRIM, use_flip=True):
    """Two-trim + null-flip seed core on an already contrast-cropped core.

    The SEED for the full-signal fit is chosen by the two-trim scheme; two quadratic seeds
    compete and the one whose fit best reconstructs the raw fringe (lowest SSE) wins:
      * NO-NULL: phase-value trim on the Hilbert phase -> polyfit seed. This is the plain
        two-trim; correct whenever the phase is (near-)monotonic.
      * NULL (the flip): for each detected |f| dip, fit_freq_null fits a SIGNED linear
        frequency to |f| (it does NOT reflect the noisy Hilbert phase -- it fits a smooth
        signed model and integrates) and recover_offset sets c0 from the fringe, giving a
        signed parabolic seed that carries the real chirp through the null. A flip is taken
        only if it beats the no-null fit by FLIP_SSE_MARGIN, so it never perturbs a trace
        that was already fine (the carrier is not needlessly negated).
    All seeds and fits use the fringe-ALIVE subset of the core, so a truncated tail the
    contrast crop missed does not drag the fit.

    ORDER: BIC over q in {2, 3} (seeded from the same phase-value trim; the flip seed
    enters at q=2). The k*ln(n) penalty admits a cubic only when it is earned -- which the
    carrier sweep needs (real TOD) -- and refuses an unidentifiable one, including at a null
    where a free c3 curves harmlessly around the fold without cutting SSE. No explicit
    null/flip order-cap is needed: the flip has already fixed c1,c2 THROUGH the null via the
    seed, and BIC handles c3 the same way everywhere. (q=1 is dropped: a pure carrier still
    needs c2 sampled, and q=1 never won on the validation grid.) Returns (csig, cph, order)."""
    alive = fringe_alive(n)
    if alive.sum() < 6:
        alive = np.ones_like(n, bool)
    ua, ya, ma, ha, na, pha = (u[alive], y[alive], mid[alive], half[alive],
                               n[alive], phase[alive])

    # NO-NULL quadratic seed: the plain two-trim (phase-value trim + polyfit), on alive
    csig2, cph2, sse2 = _trim_seed_fit(ua, pha, ya, ma, ha, f_scale, trim, q=2)
    if use_flip and len(ua) > 8:
        thresh = sse2 * (1.0 - FLIP_SSE_MARGIN)
        for _, xn in cands[:MAX_FLIP_CAND]:
            u_anchor = xn - origin                # xn is a wavelength; map to the u basis
            seed = recover_offset(ua, na, fit_freq_null(ua, f_inst[alive], u_anchor), 2)
            c_flip = fit_signal(ua, ya, ma, ha, seed, 2, f_scale)
            sse = float(np.sum((signal_model(c_flip, ua, ma, ha) - ya) ** 2))
            if sse < thresh and sse < sse2:
                csig2, cph2, sse2 = c_flip, seed, sse

    # ORDER by BIC over {2, 3}. BIC's k*ln(n) penalty admits a cubic only when it earns its
    # keep -- which the carrier sweep needs (its traces carry real TOD) -- and refuses one it
    # cannot support, so the 2/3 with c3~0 keep the tight covariance the trust gate needs.
    # This handles the null case too, with no separate cap: at a null a free c3 curves
    # harmlessly around the fold without cutting SSE, so BIC declines it; the flip (above)
    # has already fixed c1, c2 through the null via the seed. A taken flip therefore no
    # longer gates the order -- its csig2 simply competes at q=2. (q=1 dropped: it never won.)
    cand = {2: (csig2, cph2, sse2),
            3: _trim_seed_fit(ua, pha, ya, ma, ha, f_scale, trim, q=3)}
    order = min(cand, key=lambda q: _bic_sse(cand[q][2], q + 1, len(ya)))
    csig, cph, _ = cand[order]
    return csig, cph, order


def _shift_matrix(d):
    """M with b = M @ c, where b are the phase coeffs re-expanded about an origin
    shifted by d (matching powers in Phi(u) = Phi(u' + d), u' = u - d)."""
    return np.array([[1.0, d, d ** 2, d ** 3],
                     [0.0, 1.0, 2 * d, 3 * d ** 2],
                     [0.0, 0.0, 1.0, 3 * d],
                     [0.0, 0.0, 0.0, 1.0]])


def coef_cov(u, half, csig, q, resid):
    """Covariance of the fitted phase coeffs, from the Gauss-Newton Jacobian at the
    solution: model = mid + half*cos(Phi) => d(model)/dc_j = -half*sin(Phi)*u^j, so
    cov = inv(J'J) * SSE/dof. Orders above q were held at zero and get zero variance.

    This is what makes the fit honest about a truncated trace. A clip costs LEVER ARM,
    and the coefficients degrade very unevenly: the chirp c2 is read off how much the
    frequency changes ACROSS the span, so halving the span guts it (measured: c2 is the
    failing coefficient in ~2/3 of clipped-trace failures, while c1 and c3 are mostly
    fine). The residual cannot see this -- those fits still reconstruct at R^2 ~ 0.96 --
    so only the covariance distinguishes "fit the data and knows the phase" from "fit
    the data and cannot pin the chirp"."""
    n = len(u)
    k = q + 1
    if n <= k:
        return np.full((4, 4), np.inf)
    s = -half * np.sin(phase_poly(csig, u))
    J = np.stack([s * u ** j for j in range(k)], axis=1)
    try:
        JTJ_inv = np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        return np.full((4, 4), np.inf)
    cov = np.zeros((4, 4))
    cov[:k, :k] = JTJ_inv * (float(np.sum(resid ** 2)) / (n - k))
    return cov


def trust_at(csig, cov, d, nsig=None):
    """Can the fit meet the accuracy spec at an origin shifted by d (i.e. at the
    spectral centre)? Returns (ok_phase, sigmas_at_d, coeffs_at_d, ok_shape).

    TWO gates, because there are two consumers and they need different things.

    `ok_phase` covers c0 alone -- the unwrapped phase AT the reference. That is the entire
    quantity the stabilization loop consumes: it corrects phase at one wavelength, and the
    fitted carrier and chirp are only the vehicle for evaluating it there. This is what
    `trust_ok` means and what the app's accept gate must use.

    `ok_shape` covers c1..c3 -- the frequency and chirp. Nothing in the control loop reads
    these, but anything that evaluates the fit AWAY from the reference does: the chart
    overlay, and the fringe-frequency (GHz) readout, which extrapolates across the whole
    793-811 nm window where a wrong c2 shows up multiplied by d^2.

    Keeping them fused was over-rejecting frames whose phase was fine, and would also have
    let a phase-trustworthy fit quote a frequency range it could not support. Neither is
    what you want, and no single flag can express both.

    `nsig` overrides TRUST_NSIG for this call (the app surfaces it as a UI knob). It is a
    parameter rather than a mutated global so that two callers -- or two threads -- can
    never silently reconfigure each other.

    Propagating to the CENTRE is what catches the other truncation failure mode: if the
    clip removed the fringes around the spectral centre, reporting the phase there is
    extrapolation into a band with no interference -- physically unknowable. A small c3
    error blows up as 3*c3*d^2 (measured |dc1| ~ 0.3 at d = 4.5 nm), and the propagated
    sigma sees exactly that, so one test covers both short-span and extrapolation."""
    ns = TRUST_NSIG if nsig is None else float(nsig)
    M = _shift_matrix(d)
    b = M @ np.asarray(csig, float)
    cov_at = M @ cov @ M.T
    sig = np.sqrt(np.clip(np.diag(cov_at), 0.0, np.inf))
    need = [TRUST_TOL_C0, max(TRUST_REL * abs(b[1]), TRUST_FLOOR_C1),
            max(TRUST_REL * abs(b[2]), TRUST_FLOOR_C2), TRUST_TOL_C3]
    finite = bool(np.all(np.isfinite(sig)))
    ok_phase = bool(finite and sig[0] * ns <= need[0])
    ok_shape = bool(finite and all(s * ns <= t for s, t in zip(sig[1:], need[1:])))
    return ok_phase, sig, b, ok_shape


class ReferencePolicy:
    """Hysteretic choice between the primary phase reference and the fallback.

    Stateful ACROSS traces, so the app keeps one instance per stabilization run and passes
    it to every analyze() call. It switches only after `hyst` consecutive traces agree, in
    BOTH directions: `hyst` traces where the primary cannot be trusted to move off it, and
    `hyst` traces where it can to come back. A single bad frame therefore never moves the
    reference, and a single good frame never moves it back -- which is the point, because a
    loop locked to one wavelength must not chatter between two.

    Not thread-safe and deliberately not global: one run, one policy.
    """

    def __init__(self, hyst=REF_HYST):
        self.hyst = max(int(hyst), 1)
        self.fallback = False       # False = on the primary reference (muU)
        self.streak = 0             # consecutive traces disagreeing with the current state
        self.switches = 0

    def update(self, primary_ok):
        """Feed one trace's primary-reference verdict; return True to use the fallback."""
        want_fallback = not primary_ok
        if want_fallback == self.fallback:
            self.streak = 0
        else:
            self.streak += 1
            if self.streak >= self.hyst:
                self.fallback = want_fallback
                self.streak = 0
                self.switches += 1
        return self.fallback


def phase_poly(c, u):
    # Cubic (TOD) instantaneous phase in u = l - l0; c3 is the third-order term.
    c0, c1, c2, c3 = c
    return c0 + c1 * u + c2 * u ** 2 + c3 * u ** 3

def signal_model(c, u, mid, half):
    # Full raw-fringe model: the two fixed envelopes carrying a cubic-phase cosine.
    return mid + half * np.cos(phase_poly(c, u))


# ============================ DEGENERATE-INPUT GUARD =========================
# A dead / featureless window (e.g. an all-zero trace, or a pure Gaussian bump
# with no fringes) is characterized by the two envelopes collapsing onto each
# other -- the fitted envelope gap (U-L, == gauss(x,*pLn)) is tiny relative to
# the peak counts -- and by the absence of any fittable oscillation. We detect
# that up front and flag it ("dead_window") rather than pushing meaningless data
# through the Hilbert / phase / cubic fits, which would emit garbage coefficients.
DEAD_GAP_FRAC = 1e-3   # flag if peak envelope gap < this * peak amplitude span
DEAD_OSC_STD = 1e-6    # flag if the de-trended fringe has essentially no variance


# ======================== TRUNCATED-ARM DETECTION ============================
# All of this is READ-ONLY: it consumes the fitted envelopes and the raw trace and
# returns a report. Nothing here feeds the truncation bounds, the seeds, the phase
# fit or the model selection, so it cannot move a single fitted coefficient.

def _sliding(a, w):
    """(N, w) centred sliding windows over a, edge-padded so N is preserved."""
    h = w // 2
    return np.lib.stride_tricks.sliding_window_view(np.pad(a, h, mode="edge"), w)


def _rolling_amp(r, w, sigma):
    """Local fringe AMPLITUDE in a centred w-sample window, with the read-noise power
    removed. Measured as variance, NOT peak-to-peak: on the real traces the fringe is
    only ~4-13x the noise sigma, and a ptp samples the noise EXTREMES (a dead window's
    ptp reaches ~3.5 sigma, comparable to the whole live fringe) whereas a variance
    averages the noise DOWN over the window.

    Each window is de-trended by its own best-fit line first, so a steep envelope /
    baseline slope cannot masquerade as fringe amplitude; the grid is uniform, so that
    line is closed-form (slope = sum(t*Y)/sum(t^2) about the window centre) and all
    windows solve at once. The two fitted dof are paid for via SSE/(w-2).

    For a fringe h*cos(Phi) sampled over >= one period, var = h^2/2, and the noise adds
    sigma^2 in quadrature -- so h = sqrt(2*(var - sigma^2)), clipped at 0."""
    W = _sliding(r, w)
    t = np.arange(w) - (w // 2)
    D = W - (W.mean(axis=1)[:, None] + ((W @ t) / float(np.sum(t * t)))[:, None] * t[None, :])
    var = np.sum(D * D, axis=1) / max(w - 2, 1)
    return np.sqrt(2.0 * np.maximum(var - sigma ** 2, 0.0))


def _rolling_pct(a, w, q, stride):
    """Rolling q-th percentile of a over a centred w-sample window, evaluated every
    `stride` samples and linearly interpolated back. The profile varies on the window
    scale, so sampling it every ~0.2 nm loses nothing and costs a fraction of the full
    per-sample percentile."""
    idx = np.arange(0, len(a), stride)
    vals = np.percentile(_sliding(a, w)[idx], q, axis=1)
    return np.interp(np.arange(len(a)), idx, vals)


def _local_freq(r, dx, w_f, thr):
    """Local fringe frequency (cycles/nm) from the zero-crossing density of the
    DC-removed trace, over a centred w_f-sample window.

    This is what lets one detector serve a chirped trace: the window a measurement needs
    is set by the LOCAL period, which a chirp swings by an order of magnitude across one
    window (measured: 2.5 cycles/nm at centre falling to 0.5 near an out-of-window null).

    Crossings are counted through a Schmitt trigger (state flips only once the trace
    swings past +-thr), never as raw sign changes. A slow fringe crosses zero SLOWLY --
    at 0.4 cycles/nm the trace moves 1.3 counts per sample against sigma ~ 1 -- so read
    noise chatters each true crossing into several and the raw rate reads ~3x high
    (measured 1.19 against a true 0.40). That picked a window a third of the period, the
    per-window de-trend then ate the fringe, and the amplitude came back at 0.18 of
    truth, collapsing the contrast estimate. A fast fringe steps ~10 counts per sample
    and is unaffected either way.

    It degrades the right way at both extremes: a clipped band is noise, which never
    clears the trigger => low f => LONG windows (safe: a long window still measures a
    live fringe correctly, it only blurs the dead-band boundary); a near-null band
    crosses rarely => also long, so the veto can still find a crest."""
    s = np.zeros(len(r), np.int8)
    s[r > thr] = 1
    s[r < -thr] = -1
    nz = s != 0
    if not nz.any():
        return np.zeros(len(r))
    # hold the last triggered state across the dead band between the rails
    s = s[np.maximum.accumulate(np.where(nz, np.arange(len(s)), 0))]
    c = np.concatenate([(np.diff(s) != 0).astype(float), [0.0]])
    dens = uniform_filter1d(c, size=w_f, mode="nearest") / dx   # crossings per nm
    return 0.5 * dens


def _ladder_profiles(x, y, dc_fit, w_loc, dx, sigma):
    """Fringe amplitude h(l) and deviation-from-DC p90(l), each measured with a window
    matched to the LOCAL period w_loc(l).

    A per-sample variable window is computed as a small ladder of fixed window sizes,
    each evaluated everywhere, with each sample then reading off the rung nearest its
    own w_loc. Two complementary estimators, because neither alone covers the regime:
      h    -- window variance with the noise power removed. Averages noise DOWN (a dead
              window reads only ~0.7 sigma), which the real traces need at gap/sigma ~ 4;
              but it collapses if the window is shorter than the local period, since the
              per-window de-trend then eats the fringe itself.
      p90  -- 90th percentile of |y - DC|. Reads ~h wherever the fringe reaches a crest
              in the window regardless of period, and only ~1.645 sigma on noise. Used
              as a one-way VETO ("the trace still swings its full amplitude near here,
              so the fringe is alive whatever the variance says"), which is what keeps
              both nulls and slow chirped wings from reading as clipped."""
    rungs = np.geomspace(TRUNCDET_WIN_MIN_NM, TRUNCDET_WIN_MAX_NM, TRUNCDET_LADDER)
    dev = np.abs(y - dc_fit)
    stride = max(int(round(0.2 / dx)), 1)
    h_l, p_l = [], []
    for nm in rungs:
        w = int(max(round(nm / dx), 3)) | 1
        h_l.append(_rolling_amp(y, w, sigma))
        p_l.append(_rolling_pct(dev, w, 90, stride))
    j = np.argmin(np.abs(np.log(w_loc[:, None] / rungs[None, :])), axis=1)
    take = lambda L: np.take_along_axis(np.stack(L, axis=1), j[:, None], axis=1).ravel()
    return take(h_l), take(p_l)


def _majority(mask, w):
    """Majority vote over a centred w-sample window.

    The dead/live call is per-sample and noisy: in a clipped band v hovers near the
    threshold, so single samples flick above it. A strict consecutive-run scan then
    severs a 7 nm dead band at the first stray sample and reports 0.2 nm -- which is
    exactly how the first version missed ~40% of clear clips despite a correct profile.
    """
    return uniform_filter1d(mask.astype(float), size=w, mode="nearest") >= 0.5


def _noise_sigma(y, ref):
    """Read-noise sigma from first differences, sampled in the QUIETEST part of the
    window -- the lowest quantile of `ref` (the expected fringe strength), i.e. the
    wings, where the fringe contaminates the differences least. For white noise
    std(diff) = sqrt(2)*sigma.

    Standard deviation, not MAD: the instrument quantizes to QUANT ~ 1/3 count, and at
    the real sigma ~ 0.5-1.4 counts the differences collapse onto so few levels that the
    median lands ON a quantum and the MAD reads a whole step low (measured: 0.35 against
    a true 0.51, a 30% under-estimate, which then over-extends the tested region into
    pure noise). Quantization adds only q^2/12 ~ 0.01 to the variance. The quiet region
    is fringe-free by construction, so the outlier-robustness of a MAD buys little here,
    and any outlier that does land in it inflates sigma -- which shrinks the tested
    region, failing safe toward "no detection"."""
    cut = float(np.quantile(ref, TRUNCDET_NOISE_GAP_FRAC))
    quiet = ref <= cut
    d = np.diff(y[quiet]) if np.count_nonzero(quiet) >= 20 else np.diff(y)
    if len(d) < 8:
        return float("nan")
    return float(np.std(d)) / np.sqrt(2.0)


def _fit_gauss_robust(x, z):
    """Robust (soft-L1) symmetric Gaussian fit to z, seeded from the data. Used for the
    intensity DC trend, where a null's local bulge must not drag the curve.

    `x_scale="jac"` is NOT cosmetic -- it is the single biggest latency fix in the whole
    pipeline. The four parameters live on wildly different scales (amp ~1e3, mu ~802,
    sigma ~4, offset ~160), and least_squares defaults to x_scale=1.0, i.e. a trust region
    that takes the same step in all four: a step that means nothing to mu is enormous for
    sigma. On a clipped trace that mis-scaling stops the solve converging at all -- it
    walks the full max_nfev=3000 while moving the answer ~1%, and MEASURED
    (archive/probes/cc_prof.py) that ONE call was 94-96% of the entire frame, 2.4-4.7 s.
    Scaling by the Jacobian columns is the textbook fix and changes the PATH, not the
    optimum. Measured over 43 traces (7 real + a synthetic clean/clipped/both-arms sweep,
    archive/probes/cc_dcfit_scale.py): total 44448 -> 1249 ms (36x), worst case
    4694 -> 85 ms (55x), nfev 3000 -> ~25 (it now CONVERGES; it never needed the budget).

    Accuracy is unchanged, checked against GROUND TRUTH rather than against the old
    behaviour -- necessary, because on exactly the cases whose verdict moves, the OLD fit
    is the one that failed to converge, so "differs from before" is not "wrong". Scored on
    the synthetic sweep where the true clip is known (archive/probes/cc_dcfit_verdict.py),
    old and new are identical: 24 miss / 6 wrong-side / 6 clean. The verdicts that differ
    all sit INSIDE the wrong-side class (a slightly different, equally wrong edge on a
    right-of-centre clip the detector never gets right anyway -- the documented synth
    weakness). No detection is gained or lost, on synthetic or on any of the 7 real traces.

    Do NOT "fix" this instead by lowering max_nfev: measured (cc_dcfit_nfev.py), a flat cap
    is the wrong trade -- it truncates the genuinely-converging fits (real traces need
    8-78 nfev, one needs 562) without addressing why the pathological ones run away.
    An analytic Jacobian on top of this was also measured (1249 -> 1006 ms); it is a
    further ~20% for extra code and is deliberately not taken."""
    off0 = float(np.median(np.concatenate([z[:max(len(z) // 10, 3)],
                                           z[-max(len(z) // 10, 3):]])))
    i = int(np.argmax(gaussian_filter1d(z, 5)))
    p0 = [max(z[i] - off0, 1e-6), x[i], 4.0, off0]
    sol = least_squares(lambda p: gauss(x, *p) - z, p0, loss="soft_l1",
                        x_scale="jac", max_nfev=3000)
    return sol.x




def _edge_runs(dead, i_lo, i_hi, tol):
    """Lengths (in samples) of the longest dead runs touching each end of [i_lo, i_hi],
    where "touching" allows a slack of `tol` samples.

    NOT a scan outward from the boundary sample: the boundary is the detectability
    limit, so v there is at its noisiest and the majority filter is edge-affected. One
    stray sample at i_lo would then hide the entire run behind it -- measured, a correct
    7 nm dead band reported as 0.0 nm and a clear clip missed. Enumerating runs and
    asking which come near an edge is insensitive to that."""
    d = np.zeros(len(dead) + 2, np.int8)
    d[1:-1] = dead
    df = np.diff(d)
    starts = np.flatnonzero(df == 1)
    ends = np.flatnonzero(df == -1) - 1          # inclusive
    left_n = right_n = 0
    left_end = right_start = None                # interior edges of those runs
    for s, e in zip(starts, ends):
        s, e = max(int(s), i_lo), min(int(e), i_hi)
        if e < s:
            continue
        if s <= i_lo + tol and (e - s + 1) > left_n:
            left_n, left_end = e - s + 1, e
        if e >= i_hi - tol and (e - s + 1) > right_n:
            right_n, right_start = e - s + 1, s
    return left_n, right_n, left_end, right_start


def detect_truncation(x, y):
    """Detect an abrupt end of oscillations from one clipped interferometer arm.

    Self-contained: takes only the trace, so nothing it computes can leak into the fit.
    Deliberately does NOT reuse the pipeline's envelope pair -- those Gaussians have
    free centre and width, so they SLIDE ONTO the surviving fringes and absorb the very
    truncation we are looking for (measured: a left-clip at 801 nm pulls the fitted gap
    Gaussian to mu=803.5, sigma=2.4 against a true 802.0 / 3.8). Comparing the trace to
    a reference that has already swallowed the clip is circular, and it missed ~40% of
    clear clips.

    Instead we use the invariant the clip actually breaks. Both arms share an envelope,
    so with A_a = a*env, A_b = b*env:
        fringe amplitude  h    = 2ab*env         (needs BOTH arms)
        intensity bump    B    = (a^2+b^2)*env   (survives on the lone arm)
        contrast          h/B  = 2ab/(a^2+b^2)   -- CONSTANT in wavelength
    Clipping arm b sends h -> 0 while B barely moves (b^2 << a^2 at real visibility),
    so the contrast collapses. B is measured from the local DC trend, which no free
    Gaussian can slide away from, and the contrast k is read off the trace itself.

    Returns a report dict; "side" is one of "none" / "left" / "right" / "both" / "all"
    (the whole detectable region is fringe-free), or "unknown" when the trace cannot
    support the test. Profiles come back too, so the decision is inspectable.
    """
    T = {"side": "none", "detected": False, "msg": "",
         "left_nm": 0.0, "right_nm": 0.0, "x_lo": None, "x_hi": None,
         "v": None, "dead": None, "live": None, "f_est": 0.0, "win_nm": 0.0,
         "sigma": float("nan"), "k": float("nan"),
         "cut_left": None, "cut_right": None}

    dx = float(np.mean(np.diff(x)))

    # Local DC: a rolling median to blunt the fringe, then a stiff robust Gaussian
    # through it -- the intensity envelope BASE + (a^2+b^2)*env. Clipping steps it down by
    # b^2*env, which is ~9% only at low visibility but reaches ~28% on a bright trace; the
    # symmetric Gaussian is re-fit below with that fringe-free step excluded so it cannot
    # slide (see TRUNCDET_DC_PASSES). Neither a null's local bulge nor a fringe the median
    # failed to remove can bend a 4-parameter Gaussian off the true trend. Because of that
    # stiffness the median window need not match the fringe period, so it is fixed: the
    # global period estimate it used to be tied to was itself noise-chattered on exactly
    # the slow fringes where it mattered.
    w = int(max(round(TRUNCDET_DC_WIN_NM / dx), 3)) | 1
    dc_loc = median_filter(y, size=w, mode="nearest")
    try:
        pDC = _fit_gauss_robust(x, dc_loc)
    except Exception:
        T.update(side="unknown", msg="DC trend fit failed")
        return T
    dc_fit = gauss(x, *pDC)
    bump = dc_fit - pDC[3]                     # (a^2+b^2)*env, the lone-arm-safe scale
    if not np.isfinite(bump).all() or float(np.max(bump)) <= 0:
        T.update(side="unknown", msg="no intensity bump")
        return T

    sigma = _noise_sigma(y, bump)
    T["sigma"] = sigma
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = max(float(np.max(bump)) * 1e-4, 1e-9)   # noiseless synthetic

    # Local period -> per-sample window, then the two matched profiles. The crossings are
    # counted against the smooth DC FIT, never against the rolling median: a running
    # median whose window is shorter than half a period TRACKS the fringe (the median of
    # a monotonic segment is its midpoint), leaving only noise to cross zero. That reads
    # as a very high frequency, collapses the window to its floor, and kills the
    # amplitude estimate -- it made every fringe below ~1.2 cycles/nm untestable.
    f_loc = _local_freq(y - dc_fit, dx, int(max(round(TRUNCDET_FLOC_NM / dx), 3)),
                        TRUNCDET_HYST_SIGMA * sigma)
    w_loc = np.clip(TRUNCDET_WIN_PERIODS / np.maximum(f_loc, 1e-6),
                    TRUNCDET_WIN_MIN_NM, TRUNCDET_WIN_MAX_NM)
    T["f_est"] = float(np.median(f_loc))
    h_meas, p90_dev = _ladder_profiles(x, y, dc_fit, w_loc, dx, sigma)

    # Re-fit the DC with the fringe-free band INSIDE the hump excluded, so a bright clip's
    # coherent step cannot slide the symmetric Gaussian off centre (see TRUNCDET_DC_PASSES).
    # The excluded set is (inside the hump) AND (no fringe: h_meas < a few sigma), read off
    # h_meas -- which is DC-independent (each window is de-trended) -- so this corrects the
    # bump even when the biased first fit found NO dead samples at all. A null is also
    # excluded here (harmless: the DC is smooth and anchored by the rest; the null's
    # interior/pinned nature still stops it being called a clip downstream). h_meas and the
    # p90 veto are NOT recomputed: h_meas is DC-free, and the veto only acts inside the live
    # band where the DC was already right, so both barely move under the re-fit.
    for _ in range(TRUNCDET_DC_PASSES):
        in_hump = bump >= TRUNCDET_K_BUMP_FRAC * float(np.max(bump))
        exclude_dc = in_hump & (h_meas < TRUNCDET_DCREFIT_SIGMA * sigma)
        if not exclude_dc.any() or np.count_nonzero(~exclude_dc) < 8:
            break
        try:
            pDC2 = _fit_gauss_robust(x[~exclude_dc], dc_loc[~exclude_dc])
        except Exception:
            break
        bump2 = gauss(x, *pDC2) - pDC2[3]
        if not np.isfinite(bump2).all() or float(np.max(bump2)) <= 0:
            break
        moved = float(np.max(np.abs(bump2 - bump)))
        pDC, bump = pDC2, bump2
        if moved < TRUNCDET_DCREFIT_TOL * float(np.max(bump)):
            break
    dc_fit = gauss(x, *pDC)

    # Intrinsic contrast k = h/B, read where the bump is strong. A clipped band
    # contributes h/B ~ 0, so a plain median would be dragged down until it "predicted"
    # the very absence we are testing for. Start from a high percentile (biased toward
    # the unclipped part), then re-read k as the median over the samples that look live.
    # The percentile alone is not enough: it only lands in the live part while the live
    # fraction exceeds 100-K_PCT, so a trace clipped down to ~18% of its core gauged
    # k ~ 0 and the detector then reported the surviving fringes as the clipped side.
    # Refining against the live set holds k down to a much smaller surviving fraction.
    strong = bump >= TRUNCDET_K_BUMP_FRAC * float(np.max(bump))
    if np.count_nonzero(strong) < 8:
        T.update(side="unknown", msg="intensity bump too weak to gauge contrast")
        return T
    ratio = h_meas / np.maximum(bump, 1e-9)
    k = float(np.percentile(ratio[strong], TRUNCDET_K_PCT))
    for _ in range(TRUNCDET_K_ITERS):
        sel = strong & (h_meas >= 0.5 * k * bump)
        if np.count_nonzero(sel) < 8:
            break
        k = float(np.median(ratio[sel]))
    k = min(max(k, 1e-6), 1.0)                 # physical: k = 2ab/(a^2+b^2) <= 1
    T["k"] = k
    h_ref = k * bump                           # fringe amplitude BOTH arms would give

    # Region where a missing fringe is even detectable: the predicted fringe must beat
    # the noise. Below that, noise-only wings read as "fringes missing" on EVERY trace.
    live = 2.0 * h_ref >= TRUNCDET_SNR_GAP * sigma
    idx = np.where(live)[0]
    if len(idx) < 8:
        T.update(side="unknown", msg="no region where fringes beat the noise")
        return T
    i_lo, i_hi = int(idx[0]), int(idx[-1])
    live = np.zeros_like(live)                 # contiguous span (h_ref is a Gaussian,
    live[i_lo:i_hi + 1] = True                 # so its super-level set is an interval)
    T["x_lo"], T["x_hi"] = float(x[i_lo]), float(x[i_hi])

    # v ~ 1 where both arms are present, v ~ 0 where the clipped arm has no power.
    v = h_meas / np.maximum(h_ref, 1e-9)
    # Veto: the trace still swings its full amplitude somewhere within a local period,
    # so the fringe is ALIVE and the flatness is a null (parked on an envelope) or a
    # slow chirped wing, not a clip. A clipped band sits at the lone-arm level, only
    # b/(2a) ~ 0.16 of h away from the DC trend, so it never trips this.
    #
    # The swing must also BEAT THE NOISE: p90|y-DC| has a floor of ~1.645*sigma, while
    # PIN*h_ref falls to zero out in the wings, so a pure-noise wing would otherwise veto
    # itself alive. That punched holes in the dead mask exactly at the edges of the
    # detectable region, splitting a 6 nm dead band into fragments and stranding the
    # survivor beyond the edge tolerance -- a clear clip then reported 0.33 nm and missed.
    alive = p90_dev >= np.maximum(TRUNCDET_PIN * h_ref, TRUNCDET_VETO_SIGMA * sigma)
    dead = (v < TRUNCDET_DEAD_FRAC) & (~alive) & live
    w_maj = int(max(round(TRUNCDET_MAJORITY_NM / dx), 3)) | 1
    dead = _majority(dead, w_maj) & live
    T["v"], T["dead"], T["live"], T["alive"] = v, dead, live, alive

    left_n, right_n, i_lend, i_rstart = _edge_runs(dead, i_lo, i_hi,
                                                   int(round(TRUNCDET_EDGE_TOL_NM / dx)))
    n_live = i_hi - i_lo + 1

    # Where do the fringes actually resume? Report the interior edge of each dead run,
    # pushed back by half the analysis window that measured it: every profile here is a
    # sliding-window statistic, so the dead run's interior end is smeared ~w/2 SHORT of
    # the true clip (the window sees live crests before reaching it). Callers that drop
    # these samples need the conservative edge, not the smeared one, or they keep
    # feeding fringe-free points to the fit. The detector owns this correction because
    # it is the only thing that knows its own resolution.
    T["cut_left"] = (float(x[i_lend] + 0.5 * w_loc[i_lend] + TRUNCDET_CUT_PAD_NM)
                     if i_lend is not None else None)
    T["cut_right"] = (float(x[i_rstart] - 0.5 * w_loc[i_rstart] - TRUNCDET_CUT_PAD_NM)
                      if i_rstart is not None else None)
    T["left_nm"] = left_n * dx
    T["right_nm"] = right_n * dx
    hit_l = T["left_nm"] >= TRUNCDET_MIN_RUN_NM
    hit_r = T["right_nm"] >= TRUNCDET_MIN_RUN_NM
    # A right-side detection is a known false positive on this hardware (see
    # PHYSICAL_CUT_SIDES): the red arm cannot be clipped, so drop the right run before it
    # can become a side or a cut. Done HERE, at the single point where side is decided, so
    # every downstream consumer -- applied_cuts, hits_core, the recovery-scan side, the clip
    # cache -- inherits a consistent "no right cut" without each needing its own guard. The
    # "all" branch is deliberately left intact: nothing-oscillates-anywhere is a genuinely
    # dead trace, not a phantom red-wing edge, and it must still be reported.
    if "right" not in PHYSICAL_CUT_SIDES and not (
            max(left_n, right_n) >= TRUNCDET_ALL_FRAC * n_live):
        hit_r = False
        T["cut_right"] = None
        T["right_nm"] = 0.0
    if max(left_n, right_n) >= TRUNCDET_ALL_FRAC * n_live:   # nothing oscillates anywhere
        T.update(side="all", detected=True, msg="no fringes in the detectable region")
    elif hit_l and hit_r:
        T.update(side="both", detected=True)
    elif hit_l:
        T.update(side="left", detected=True)
    elif hit_r:
        T.update(side="right", detected=True)
    return T



# ===================== TRUNCATION RECOVERY (cut scan) ========================
# The detector's job -- "find the clip, then cut there" -- turned out to be the hard way
# round. Every version of it had to measure something (contrast against a prediction, the
# residue against noise, the residue against its own peak, the abruptness of the edge) and
# each measurement was defeated by a different property of a real trace: the envelope is 2%
# off a Gaussian and bump/noise is 411 so that 2% is 8 sigma; k = h/bump is not constant in
# lambda (0.70 at f=1.2 cyc/nm -> 0.38 at f>3.5, the slit smearing fast chirped fringes);
# the local period estimate reads 0.63 against a true 1.56 because a fringe-free band has no
# crossings to count; and a clean trace's envelope RISE is steeper than a real clip's edge
# (measured slope 3.44 vs 1.83), so even abruptness does not separate them.
#
# The fit does not need any of that. A wrong cut fits badly and the right cut fits at
# r2_fringe = 0.990 -- so try a few and keep the one that works. It cannot be fooled by
# envelope shape, contrast rolloff, DC model error or the period estimate, because it never
# measures them.
#
# Cost: nothing on a good frame (it passes first time and never scans), and ~170 ms per
# candidate only on frames that would otherwise have been DROPPED and produced nothing at
# all. A drop becomes a diagnosis.
TRUNCREC_TRIGGER = 0.15       # rms_frac above this => the model does not explain the trace,
                              # so try cutting.
                              #
                              # The window for this is NARROW and measured, not chosen:
                              #     clean frames     rms_frac p50 0.068, p99 0.145, max 0.147
                              #     truncated frames rms_frac p05 0.165, p50 0.252
                              # so it must sit above 0.145 and below 0.165. It was 0.20 --
                              # ABOVE the truncated p05 -- so truncated frames in 0.165-0.20
                              # never scanned, and since their median 0.252 is UNDER the
                              # app's 0.30 accept gate they COMMITTED with the carrier ~3%
                              # wrong. Raising this hides truncation; lowering it re-fits
                              # good frames for nothing. Re-measure both distributions
                              # before touching it.
TRUNCREC_STEP_NM = 0.25       # cut grid. The real edge needed 0.10 nm resolution to land
                              # between underdetermined (800.05) and COMMIT (800.15), but
                              # the acceptable window is ~0.5 nm wide (800.15-800.55 all
                              # commit) so 0.25 always has a candidate inside it.
TRUNCREC_MIN_SPAN_NM = 3.0    # never accept a cut that leaves less than this.
                              #
                              # A HARD FLOOR ONLY -- not the quality guard. It was 5.0, which
                              # is WIDER THAN A TRUNCATED TRACE'S CORE (measured 4.47 nm: the
                              # clip collapses the envelope gap, so the 40%-contrast core is
                              # already narrow). Every candidate was then rejected before it
                              # was tried and the scan was dead code on exactly the traces it
                              # exists for. It only ever fired on truncated.csv because that
                              # core happens to be 8.6 nm.
                              #
                              # The real guard against cutting too far is the TRUST GATE
                              # inside _explains: rms_frac always improves as you cut (you are
                              # deleting the hardest data), and a 0.6 nm span duly scored a
                              # lovely 0.070 with c2=3.000 -- but the propagated covariance
                              # called it underdetermined and it was refused. That plus Occam
                              # (first success wins, so a needless cut is unreachable) is what
                              # keeps this honest. This floor is only here to stop the scan
                              # wasting time on spans no fit could ever support.
TRUNCREC_MAX_NM = 4.0         # how far into the core to scan from each side. Physical: the
                              # operator's clips land near 802 (">803, <801"), and a clip
                              # that does not reach the core is removed by the contrast cut
                              # anyway, so there is nothing further out worth finding.


def _rms_frac(R):
    """Scale-free fit residual: rms / median half-amplitude. ~0.06-0.09 on a good live fit,
    0.36 on a real trace fit through a fringe-free band."""
    if R.get("csig") is None or R.get("half") is None:
        return float("inf")
    med = float(np.median(np.asarray(R["half"], float)))
    return float(R["rms_sig"]) / (med + 1e-9)


TRUNCREC_SCAN_ON_HITS_CORE = True
                              # Also scan when the DEAD MASK says a fringe-free band reaches
                              # into the fit core while the SIDE CLASSIFIER says "none" --
                              # regardless of rms_frac.
                              #
                              # WHY rms_frac alone cannot do this (measured, 15.3/15.3d):
                              # clips missed on ~1 hardware frame in 4 sit at rms_frac
                              # 0.131-0.147 and real CLEAN frames sit at 0.11-0.144, so the
                              # two distributions OVERLAP and no threshold separates them.
                              # `hits_core` does: it is the raw dead mask (28/30 correct on
                              # synthetic clips) rather than `_edge_runs` (0/30), and it is
                              # False on every real clean trace measured. The frames it
                              # catches are exactly the hardware signature: an OUTBOARD clip
                              # on the WEAK arm, `side=none, hits_core=True`, which then
                              # COMMITS with the carrier ~3% wrong because rms_frac is under
                              # both this trigger and the app's 0.30 accept gate.
TRUNCREC_HC_IMPROVE = 0.70    # ...but a frame reached this way ALREADY EXPLAINS ITSELF
                              # uncut, which breaks the invariant that makes the scan safe.
                              # Normally a needless cut is unreachable (the scan only runs
                              # after the uncut fit FAILED, and the first success wins, so
                              # the smallest adequate cut is taken). Reached on an explaining
                              # frame, that guard is gone: rms_frac always improves as you
                              # cut -- you are deleting the hardest data -- so the scan can
                              # always find *some* cut that scores better and would silently
                              # replace a good answer. See the 3.2/18.3 rad cut-first result
                              # in CLIPCACHE_STATUS sec.4 for what that failure looks like.
                              # So on THESE frames only, keep the uncut fit unless the cut
                              # improves rms_frac by at least this factor. A genuine missed
                              # clip clears it easily (fitting through a fringe-free band is
                              # a gross residual, ~0.25 vs ~0.07); a cosmetic over-cut of a
                              # clean frame does not.


def _explains(R):
    """Does this fit account for the trace, and can we vouch for it?"""
    return (R.get("status") == "ok" and bool(R.get("trust_ok"))
            and _rms_frac(R) < TRUNCREC_TRIGGER)


def _missed_clip(R):
    """Dead mask reaches the fit core, yet the side classifier saw nothing.

    The self-contradiction (`trunc=none HITS-CORE`) that HISTORY sec.7 flagged and 15.3e
    quantified: the mask works, the classifier does not. Treated as "unexplained" so the
    scan gets a look, WITHOUT touching `_explains` itself -- `_explains` is also the
    scan's own accept test, and a recovered fit can still legitimately carry
    hits_core=True, so folding this in there would reject the cut it just found.
    """
    T = R.get("trunc") or {}
    return bool(T.get("hits_core")) and T.get("side") in (None, "none")


def applied_cuts(trunc):
    """The cut edges the FIT ACTUALLY APPLIED, as `(lo, hi)`; None where no cut was made.

    NOT the same as `trunc["cut_left"]` / `trunc["cut_right"]`, and the difference bites.
    The detector reports a candidate edge on BOTH sides whenever it finds a dead run, but
    the fit only honours the side(s) the detector actually CLAIMS -- so a clean trace can
    (and does: `live_desktop_spectrum.csv` reports `cut_right = 810.98` at `side="none"`)
    carry an edge that nothing ever cut on.

    Anything that reports the cut to a human -- a chart marker, a log line -- must ask this
    question, not read the raw keys, or it will draw a clip on an unclipped frame. It lives
    here rather than in the caller because it is the SAME rule the fit uses, and a second
    copy of a rule is how the trunc_threshold 0.30/0.40 parity bug happened.
    """
    if not isinstance(trunc, dict):
        return None, None
    side = trunc.get("side")
    return (trunc.get("cut_left") if side in ("left", "both") else None,
            trunc.get("cut_right") if side in ("right", "both") else None)


def _analyze_once(x, y, anchor=None, ref_policy=None, trust_nsig=None,
                  trunc_threshold=None, ref_primary=None, force_trunc=None,
                  scanfree=None, trunc_method=None, env_center=None):
    """Run the full recovery pipeline on one in-window trace.

    `trust_nsig` / `trunc_threshold` override TRUST_NSIG / TRUNC_THRESHOLD for this call
    (the app surfaces them as UI knobs). They are parameters, never mutated globals, so
    concurrent callers cannot reconfigure each other. None = use the module default, which
    is the value the harness calibrated.

    `ref_primary` is the wavelength the phase is WANTED at. None => the fitted intensity
    centroid muU, which is what the standalone and the harness use. A live caller that lets
    the operator pin a reference (the app's `lambda_ref`) must pass it here, or the answer
    silently drifts from the wavelength they asked for to wherever the envelope centred
    this frame -- a fraction of a nm, but it is THEIR lock point, not ours to move.

    `ref_policy` is an optional ReferencePolicy carried ACROSS traces by a live caller, to
    add hysteresis to the phase-reference choice. Omit it (standalone, harness) and the
    reference falls back immediately whenever the spectral centre cannot be trusted.
    The reference actually used is R["ref_wl"]; the coefficients in R["csig_at_centre"]
    are expressed at it. Callers must READ ref_wl rather than assume 802.

    `anchor` is the (U_base, D) continuum measurement from baseline_anchor() on the FULL
    frame, taken BEFORE this window was cut -- ZOOM itself contains no continuum, so the
    caller has to supply it (in the app, that means measuring it before PhaseTracker
    windows the spectrum and threading it down). Omitting it leaves the envelope offset
    unbounded, which is safe on low-visibility traces and wrong on bright ones.

    Returns a dict R with every intermediate needed for plotting and for
    ground-truth comparison. R["status"] is "ok" for a normal recovery, or one
    of the degenerate/failure tags ("too_few", "nonfinite", "dead_window",
    "error") when the trace cannot or should not be fit. On a non-"ok" status
    the coefficient fields are None.
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    R = {"status": "ok", "msg": ""}

    # The operator's envelope-centre pin, as a (lo, hi) band for the upper-envelope mean.
    # None => the centre is free (standalone/harness). See ENV_CENTRE_DEFAULT.
    mu_bounds = (None if env_center is None
                 else (float(env_center) - ENV_CENTRE_TOL, float(env_center) + ENV_CENTRE_TOL))

    # --- Contract guards ---------------------------------------------------------
    if len(x) < 16:
        return {"status": "too_few", "msg": f"only {len(x)} pts in window", "csig": None}
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return {"status": "nonfinite", "msg": "NaN/Inf in trace", "csig": None}

    t_run0 = time.perf_counter()
    pU_prelim = None      # set only when DEADZONE_REFIT actually replaces the envelope
    try:
        use_scanfree = SCANFREE if scanfree is None else scanfree
        if use_scanfree:
            # ===== Scan-free deterministic pipeline (replaces the 12 s recovery scan) =====
            # On the realistic synth suite this BEATS the scan (99.6% vs 98.8%, fewer wrong)
            # with no scan. Pipeline: full-window envelopes -> contrast crop -> oscillation
            # end-trim -> Hilbert -> TRUNCATION TRIM -> seed/fit.
            #
            # The derivative "coarse cut" was tried and DROPPED (2026-07-19): net-negative on
            # every harness (it over-crops good traces into false-drops -- +16 vs no coarse
            # cut even on the realistic suite) and it never fixed the truncation corner it was
            # built for. Truncation is now handled by trimming the CORE; TRUNC_METHOD selects
            # "phase" (conditional_phase_trim) or "knife" (knife_edge_cut).
            #
            # ENVELOPES ON THE FULL WINDOW. pU (intensity bump) survives a clip and needs its
            # wings to pin mu/sigma (a narrowed domain sent mu to 927 nm). pLn (contrast gap)
            # is ALSO fit full-window: narrowing its domain to exclude a clip degenerates the
            # gap Gaussian (a=3e8 on truncated.csv) and tightens the contrast crop enough to
            # push clean traces to `underdetermined`. The clipped sliver is removed downstream
            # by the end-trim + truncation trim, not by the envelope fit.
            t_tr0 = time.perf_counter()
            t_trunc = 0.0
            n_full = len(x); x_all, y_all = x.copy(), y.copy()
            trunc = {"side": "none", "detected": False, "dead": None, "live": None,
                     "cut_left": None, "cut_right": None, "msg": "scan-free"}
            pU = fit_upper_envelope(x, y, off_bounds=anchor_bounds(anchor), mu_bounds=mu_bounds)
            pLn = fit_upper_envelope(x, -(y - gauss(x, *pU)))
            aLn, muLn, sLn, offLn = pLn
            peak_gap = abs(aLn + offLn)
            span = float(np.ptp(y)) + 1e-12
            detrended = y - gauss(x, *pU)
            if peak_gap < DEAD_GAP_FRAC * span or np.std(detrended) < DEAD_OSC_STD * (span + 1):
                t_run = (time.perf_counter() - t_run0) * 1e3
                return {"status": "dead_window", "csig": None, "pU": pU, "pLn": pLn,
                        "peak_gap": peak_gap, "span": span, "t_run": t_run, "trunc": trunc,
                        "msg": f"envelope gap {peak_gap:.3g} vs span {span:.3g}: no fringes"}

            # normalize + contrast crop (existing closed-form TRUNC_THRESHOLD).
            Ud = gauss(x, *pU); Ld = Ud - gauss(x, *pLn)
            mid = 0.5 * (Ud + Ld); half = 0.5 * (Ud - Ld); n = (y - mid) / half
            max_diff = aLn + offLn
            min_diff = min(gauss(x[0], *pLn), gauss(x[-1], *pLn))
            thr = TRUNC_THRESHOLD if trunc_threshold is None else float(trunc_threshold)
            level = min_diff + (max_diff - min_diff) * thr
            arg = (level - offLn) / aLn
            if 0.0 < arg < 1.0:
                delta = abs(sLn) * np.sqrt(-2.0 * np.log(arg))
                x_left, x_right = muLn - delta, muLn + delta
            else:
                x_left, x_right = x[0], x[-1]
            keep = (x >= x_left) & (x <= x_right)
            if int(np.count_nonzero(keep)) < 16:
                t_run = (time.perf_counter() - t_run0) * 1e3
                return {"status": "too_few", "csig": None, "trunc": trunc, "t_run": t_run,
                        "t_trunc": t_trunc,
                        "msg": f"contrast core has {int(np.count_nonzero(keep))} pts"}

            # TRUNCATION TRIM: remove the clipped sliver from the CONTRAST core -- BEFORE the
            # oscillation end-trim, which would otherwise erase the very dead run the detector
            # needs to see (measured: after the end-trim the "knife" finds no dead run and
            # misses 2020607181645). TRUNC_METHOD selects the detector. A clean trace has no
            # clipped edge -> no trim. A preliminary Hilbert phase is taken here for the
            # "phase" method; the shared tail re-Hilberts the final core.
            method = TRUNC_METHOD if trunc_method is None else trunc_method
            if method != "none" and int(np.count_nonzero(keep)) >= 32:
                kx, kn = x[keep], n[keep]
                if method == "knife":
                    ksub, tinfo = knife_edge_cut(kx, kn)
                else:
                    ksub, tinfo = conditional_phase_trim(kn, np.unwrap(np.angle(hilbert(kn))))
                trunc.update(tinfo); trunc["detected"] = tinfo.get("side", "none") != "none"
                if 16 <= int(ksub.sum()) < len(kx):
                    core_idx = np.flatnonzero(keep)[ksub]
                    keep = np.zeros(n_full, bool); keep[core_idx] = True
            t_trunc = (time.perf_counter() - t_tr0) * 1e3

            # oscillation end-trim: drop any remaining leading/trailing DEAD runs the contrast
            # crop cannot see. Interior nulls stay (they are protected by being interior).
            osc = fringe_oscillating(n[keep])
            live = osc > OSC_DEAD_THR
            if live.any():
                lo_i = int(np.argmax(live))
                hi_i = len(live) - 1 - int(np.argmax(live[::-1]))
                if (lo_i > 0 or hi_i < len(live) - 1) and (hi_i - lo_i + 1) >= 16:
                    core_idx = np.flatnonzero(keep)[lo_i:hi_i + 1]
                    keep = np.zeros(n_full, bool); keep[core_idx] = True

            if int(np.count_nonzero(keep)) < 16:
                t_run = (time.perf_counter() - t_run0) * 1e3 - t_trunc
                return {"status": "too_few", "csig": None, "trunc": trunc, "t_run": t_run,
                        "t_trunc": t_trunc,
                        "msg": f"live core has {int(np.count_nonzero(keep))} pts"}

            # --- DEADZONE REFIT (pipeline B) -----------------------------------------
            # The knife has now located the dead sliver (cut_left/cut_right). Refit the
            # envelopes on the full window MINUS that deadzone: this keeps the fringe wings
            # (so the gap Gaussian stays well-posed -- refitting on the narrow contrast core
            # sends a->3e8) while dropping the clipped sliver that dragged the prelim envelope.
            # One clean envelope on the right domain, in a single pass -- no recovery scan.
            if DEADZONE_REFIT and (trunc.get("cut_left") is not None
                                   or trunc.get("cut_right") is not None):
                lo = trunc.get("cut_left"); hi = trunc.get("cut_right")
                live = (x >= (lo if lo is not None else x[0])) & \
                       (x <= (hi if hi is not None else x[-1]))
                if int(live.sum()) >= 24:
                    pUr = fit_upper_envelope(x[live], y[live], off_bounds=anchor_bounds(anchor))
                    pLnr = fit_upper_envelope(x[live], -(y[live] - gauss(x[live], *pUr)))
                    Gr = gauss(x, *pLnr); halfr = 0.5 * Gr
                    if (np.all(halfr > 0) and abs(pLnr[0]) < 1e5
                            and x[0] - 2 <= pLnr[1] <= x[-1] + 2):
                        pU_prelim = pU          # diagnostics: how far the refit moved muU
                        pU, pLn = pUr, pLnr
                        Ud = gauss(x, *pU); mid = Ud - halfr; half = halfr
                        n = (y - mid) / half

            x, y, n, mid, half = x[keep], y[keep], n[keep], mid[keep], half[keep]
        else:
            # --- Truncated-arm detection (runs FIRST: it needs only the raw trace) ----
            # In its own try/except: a detector failure degrades to "no truncation known"
            # and the fit proceeds exactly as it would without this feature.
            t_tr0 = time.perf_counter()
            if force_trunc is not None:
                # The recovery scan supplies the cut directly; the detector is not consulted.
                trunc = dict(force_trunc)
            else:
                try:
                    trunc = detect_truncation(x, y)
                except Exception as e:
                    trunc = {"side": "unknown", "detected": False, "v": None, "dead": None,
                             "live": None, "x_lo": None, "x_hi": None, "left_nm": 0.0,
                             "right_nm": 0.0, "cut_left": None, "cut_right": None,
                             "msg": f"detector failed: {type(e).__name__}: {e}"}
            t_trunc = (time.perf_counter() - t_tr0) * 1e3

            # --- Drop the fringe-free band BEFORE anything is fit --------------------
            # Where the clipped arm has no power there are no fringes, so those samples
            # carry NO phase information -- they are a bare Gaussian tail. Fitting a cosine
            # through them drags the whole cubic off: measured, the base frequency came back
            # ~0.5 rad/nm wrong (~100x the clean-trace error) and only 20-24% of such traces
            # recovered their coefficients, while still converging and still reporting a fit.
            # So the fringe-free band is excluded from the envelopes AND from the phase fit.
            # An untruncated trace has fit_lo/fit_hi = -inf/+inf, so its path is untouched.
            fit_lo, fit_hi = applied_cuts(trunc)
            if trunc.get("side") == "all":
                t_run = (time.perf_counter() - t_run0) * 1e3 - t_trunc
                return {"status": "dead_window", "csig": None, "trunc": trunc,
                        "t_run": t_run, "t_trunc": t_trunc, "x_all": x, "y_all": y,
                        "msg": "arm truncated across the whole window: no fringes to fit"}
            fit_ok = np.ones(len(x), bool)
            if fit_lo is not None:
                fit_ok &= x >= fit_lo
            if fit_hi is not None:
                fit_ok &= x <= fit_hi
            if np.count_nonzero(fit_ok) < 16:
                t_run = (time.perf_counter() - t_run0) * 1e3 - t_trunc
                return {"status": "too_few", "csig": None, "trunc": trunc,
                        "t_run": t_run, "t_trunc": t_trunc, "x_all": x, "y_all": y,
                        "msg": f"only {int(np.count_nonzero(fit_ok))} fringe-bearing pts "
                               f"survive the arm truncation"}
            xw, yw = x[fit_ok], y[fit_ok]

            # --- Envelopes (fit ONLY where fringes exist) ----------------------------
            # Fitting these on the full window would let the fringe-free band drag them:
            # both are free Gaussians, so they slide onto the surviving fringes (measured, a
            # left-clip pulls the gap Gaussian to mu=803.5/sigma=2.4 against a true
            # 802.0/3.8). mid/half are held FIXED through the phase fit, so that distortion
            # would corrupt the model amplitude even on the samples we do keep.
            # Only pU is anchored. pLn is fit to the NEGATED RESIDUAL, whose "baseline" is not
            # the continuum at all, so the U_base/P5 statistic does not transfer to it -- that
            # needs its own derivation and is deliberately left unbounded rather than guessed.
            pU = fit_upper_envelope(xw, yw, off_bounds=anchor_bounds(anchor), mu_bounds=mu_bounds)
            resid_env = yw - gauss(xw, *pU)
            pLn = fit_upper_envelope(xw, -resid_env)  # upper envelope of the negated residual

            # --- Dead-window / no-fringe detection -----------------------------------
            # Peak envelope gap (U-L at its center) vs the overall count span; plus the
            # variance of the envelope-removed trace. Either collapsing => no fringes.
            aLn, muLn, sLn, offLn = pLn
            peak_gap = abs(aLn + offLn)
            span = float(np.ptp(yw)) + 1e-12          # judged on the fringe-bearing band, to
            detrended = yw - gauss(xw, *pU)           # match what the envelopes were fit on
            if peak_gap < DEAD_GAP_FRAC * span or np.std(detrended) < DEAD_OSC_STD * (span + 1):
                t_run = (time.perf_counter() - t_run0) * 1e3
                return {"status": "dead_window", "csig": None, "pU": pU, "pLn": pLn,
                        "peak_gap": peak_gap, "span": span, "t_run": t_run,
                        "msg": f"envelope gap {peak_gap:.3g} vs span {span:.3g}: no fringes"}

            # --- Truncation bounds (closed-form Gaussian threshold crossings) --------
            max_diff = aLn + offLn                                 # Gaussian peak, at muLn
            min_diff = min(gauss(x[0], *pLn), gauss(x[-1], *pLn))  # gap falls off toward edges
            thr = TRUNC_THRESHOLD if trunc_threshold is None else float(trunc_threshold)
            level = min_diff + (max_diff - min_diff) * thr
            arg = (level - offLn) / aLn                            # exp(-(x-mu)^2/2s^2) at crossing
            if 0.0 < arg < 1.0:
                delta = abs(sLn) * np.sqrt(-2.0 * np.log(arg))
                x_left, x_right = muLn - delta, muLn + delta
            else:
                x_left, x_right = x[0], x[-1]

            # --- Normalize the fringes using both envelopes --------------------------
            Ud = gauss(x, *pU)
            Ld = Ud - gauss(x, *pLn)
            mid = 0.5 * (Ud + Ld)
            half = 0.5 * (Ud - Ld)
            n = (y - mid) / half

            # --- Truncate to the high-visibility core (and to the fringe-bearing band) --
            keep = (x >= x_left) & (x <= x_right) & fit_ok
            n_full = len(x)
            x_all, y_all = x.copy(), y.copy()
            x, y, n, mid, half = x[keep], y[keep], n[keep], mid[keep], half[keep]
            if len(x) < 16:
                t_run = (time.perf_counter() - t_run0) * 1e3 - t_trunc
                return {"status": "too_few", "csig": None, "msg": f"core has {len(x)} pts",
                        "t_run": t_run, "t_trunc": t_trunc, "trunc": trunc}

            # --- Bound to the Hilbert-valid region: drop truncated dead ENDS ----------
            # The contrast crop is a SMOOTH-envelope threshold and cannot see a clipped arm
            # whose envelope gap has collapsed: the lower-envelope Gaussian slides straight
            # across the clip, so x_left/x_right land near the window edge and the fringe-free
            # dead band stays inside the core. The fit already answers on the fringe-alive
            # subset -- but the reconstruction metrics (rms_frac, r2_fringe) and the trust
            # covariance below are evaluated on the WHOLE core, so a good fit is scored against
            # the dead band and reads r2_fringe ~0.32 / underdetermined, which then drives the
            # recovery scan for nothing. The fringe-alive mask CAN see the clip (post-clip
            # samples do not oscillate, so their local fringe RMS is below ALIVE_THR), so trim
            # the leading and trailing dead RUNS and let Hilbert, the seed, the fit AND the
            # gating share one untruncated span. Only the ENDS are trimmed: an interior
            # not-alive run is a genuine null (fringes resume past it), it carries no
            # truncation, and the Hilbert transform needs the samples uniform -- so it stays.
            # A clean core is alive edge-to-edge (lo_i=0, hi_i=last) and is left untouched.
            osc = fringe_oscillating(n)
            live = osc > OSC_DEAD_THR
            if live.any():
                lo_i = int(np.argmax(live))
                hi_i = len(live) - 1 - int(np.argmax(live[::-1]))
                if (lo_i > 0 or hi_i < len(live) - 1) and (hi_i - lo_i + 1) >= 16:
                    core_idx = np.flatnonzero(keep)[lo_i:hi_i + 1]
                    keep = np.zeros(n_full, bool); keep[core_idx] = True
                    sl = slice(lo_i, hi_i + 1)
                    x, y, n, mid, half = x[sl], y[sl], n[sl], mid[sl], half[sl]

        # --- Hilbert transform: analytic signal -> phase & instantaneous freq -----
        dx = float(np.mean(np.diff(x)))
        analytic = hilbert(n)
        phase = np.unwrap(np.angle(analytic))
        f_inst = np.gradient(phase, dx) / (2 * np.pi)

        # --- Null candidates (zero-derivative dips of smoothed |f|) ---------------
        # Polynomial basis origin = the phase anchor l0. Free, it is mean(x) -- but mean(x)
        # is the centroid of the SURVIVING samples, so a left clip drops the left points and
        # drags the anchor right (measured 802.0 -> 802.85), and the phase reported back at a
        # fixed reference is then an extrapolation from a moving origin with uncertain
        # curvature (scatter 0.72 rad vs a 0.314 budget). When the operator pins the centre,
        # anchor there instead: phase@ref becomes c0 directly, no lever arm, no extrapolation.
        # mean(x) was chosen for CONDITIONING, and env_centre sits inside the data, so the fit
        # stays just as well-posed. This -- not the muU bound -- is what actually holds the
        # phase; the muU pin keeps the envelope Gaussian consistent with it.
        origin = float(np.mean(x)) if env_center is None else float(env_center)
        u = x - origin
        fs = smooth_absf(x, f_inst)
        cands = null_candidates(x, fs)    # [(prominence, x_null), ...], deepest first
        prom = cands[0][0] if cands else 0.0
        f_scale_sig = SIGNAL_LOSS_FRAC * float(np.median(half)) + 1e-9

        # --- Two-trim + null-flip seed core --------------------------------------
        # Contrast trim (above) removed the low-visibility wings; here the phase-value
        # trim drops the folded null plateau and a quadratic seed is refined on the raw
        # counts, with a signed "flip" seed taken per |f| dip only if it cuts the fringe
        # SSE by FLIP_SSE_MARGIN. Order is BIC-selected over {2,3} everywhere -- BIC alone
        # declines an unidentifiable cubic at a null (csig is the graded answer, cph its
        # Hilbert-domain seed). has_null below is thus the single place a null is decided.
        csig, cph, order = core_seed_fit(u, y, mid, half, n, phase, f_inst, cands,
                                         f_scale_sig, origin)
        # --- Joint envelope + phase refinement (UNTRUNCATED ONLY) -----------------
        # Free both envelope Gaussians together with the phase so the fit -- not a separate
        # quantile pass -- sets trough depth. Updates pU/pLn/mid/half/n/csig in place when it
        # helps; a no-op otherwise. Downstream (metrics, covariance, spectral-centre ref,
        # trust) reads these, so everything below sees the refined envelope automatically.
        #
        # RESTRICTED TO UNTRUNCATED TRACES. On a truncated trace the freed envelope absorbs
        # the missing-arm misfit and PASSES the trust gate while the phase is actually wrong
        # (measured on synth_truncation: +23 confidently-wrong traces, and it suppresses the
        # recovery scan that used to catch them). The frozen envelope + trust gate + recovery
        # scan is the safety machinery for clips; freeing the envelope defeats it. On
        # untruncated traces the same fit is a clean win (0 new wrong), so it runs only there.
        env_refit = False
        _side = (trunc.get("side") if isinstance(trunc, dict) else None)
        if JOINT_ENV_FIT and _side == "none":
            pU, pLn, mid, half, n, csig, env_refit = joint_env_refine(
                x, y, u, pU, pLn, csig, order, anchor, mid, half, n)
        l0 = origin
        c0, c1, c2, c3 = csig
        phase_cubic = phase_poly(csig, u)
        f_model = (c1 + 2 * c2 * u + 3 * c3 * u ** 2) / (2 * np.pi)
        # A null exists iff the fitted instantaneous frequency changes sign in-window.
        has_null = bool(np.min(f_model) < 0.0 < np.max(f_model))
        if has_null:
            sgn = np.sign(f_model)
            xings = np.where(np.diff(sgn) != 0)[0]
            i = xings[int(np.argmin(np.abs(u[xings])))]
            null_wl = float(x[i] - f_model[i] * (x[i + 1] - x[i]) /
                            (f_model[i + 1] - f_model[i]))
        else:
            null_wl = None
        y_model = signal_model(csig, u, mid, half)
        f_hilbert = (cph[1] + 2 * cph[2] * u + 3 * cph[3] * u ** 2) / (2 * np.pi)
        if float(np.dot(f_hilbert, f_model)) < 0:   # display sign: match the raw-fit f
            f_hilbert = -f_hilbert
        y_hilbert = signal_model(cph, u, mid, half)
        resid_sig = y - y_model
        rms_sig = float(np.sqrt(np.mean(resid_sig ** 2)))
        # Reconstruction fidelity of the full model against the raw core counts.
        ss_res = float(np.sum(resid_sig ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2)) + 1e-12
        r2_sig = 1.0 - ss_res / ss_tot
        # HONEST fit metric: raw-count R2 is inflated by the fixed Gaussian envelope
        # (matched for free) and by the near-DC region at a null. Strip the envelope
        # and score the PHASE directly -- model fringe cos(Phi) vs the normalized
        # data fringe n=(y-mid)/half. A wrong phase can't hide here (a bad fit goes
        # negative even where raw R2 looks OK).
        n_model = np.cos(phase_cubic)
        ss_res_n = float(np.sum((n - n_model) ** 2))
        ss_tot_n = float(np.sum((n - np.mean(n)) ** 2)) + 1e-12
        r2_fringe = 1.0 - ss_res_n / ss_tot_n

        # Did the fringe-free band reach into the nominal fit core? Those samples are now
        # EXCLUDED rather than fit through, so this is no longer a "the fit is wrong"
        # flag -- it means the truncation cost the phase fit some of its lever arm, so
        # the fit stood on fewer fringes than the core would suggest.
        trunc["hits_core"] = bool(trunc.get("dead") is not None and
                                  np.any(trunc["dead"] & (x_all >= x_left) &
                                         (x_all <= x_right)))

        # `hits_core` is measured against the NOMINAL contrast core [x_left, x_right] --
        # i.e. BEFORE the cut (`fit_ok`) and BEFORE the dead-end trim. It therefore answers
        # "did the clip land where the phase wanted to be fit", NOT "did the fit run through
        # dead samples". Both cases are useful and they are not the same question:
        #   hits_core & not hits_fit -> the pipeline already excluded the dead band (cut or
        #                               end-trim). The fit is honest; it just stood on fewer
        #                               fringes, so its lever arm is shorter than the core
        #                               width suggests.
        #   hits_fit                 -> dead samples are IN the fitted set. That is the fit
        #                               being asked to explain data with no fringes in it.
        # `keep` is the final fitted mask over the full window, so this is the fit's own
        # domain and not a re-derivation of it.
        trunc["hits_fit"] = bool(trunc.get("dead") is not None and
                                 np.any(trunc["dead"] & keep))

        # t_run stays comparable to pre-detector runs: the detector is timed separately.
        # --- Is the answer supported by the data, and where? ----------------------
        # Primary reference = the SPECTRAL CENTRE (fitted intensity peak muU): that is
        # where the pulse energy is and what a phase-stabilization reference asks about,
        # and the un-clipped arm dominates the intensity, so muU survives the truncation
        # even when the fringes around it do not.
        # Fallback = the CORE CENTROID l0. See the REF_HYST block for why.
        cov = coef_cov(u, half, csig, order, resid_sig)
        ref_primary = float(pU[1]) if ref_primary is None else float(ref_primary)
        ok_primary, sig_primary, b_primary, shape_primary = trust_at(
            csig, cov, ref_primary - l0, nsig=trust_nsig)
        ok_fallback, sig_fallback, b_fallback, shape_fallback = trust_at(
            csig, cov, 0.0, nsig=trust_nsig)                              # d=0 at l0

        # ACCURACY measurement, alongside the precision gate above. See
        # REF_MAX_OFFSET_FRAC: a displaced core biases the phase at the reference by
        # ~1.4 rad while every precision statistic stays clean, so the covariance can
        # never catch it.
        #
        # MEASURED here, ENFORCED in the app's accept gate (StabilizationConfig.accepts) --
        # deliberately not folded into `trust_ok`. Tried that first and it was wrong on
        # hardware: `trust_ok` feeds `_explains`, so a displaced core read as "this fit does
        # not explain the trace" and every such frame was sent into the recovery scan. The
        # scan searches for a CUT, which cannot fix a core that is merely off-centre, so it
        # burned the full CLIPCACHE_BUDGET_MS and returned the same answer. Measured on the
        # live run of 2026-07-20: median wall 271 ms with p90 736 ms and a worst frame of
        # 7.5 s, throughput 2.1 f/s against 3.9 f/s before -- the loop's dead time doubled
        # to buy nothing. "The data cannot support the phase HERE" and "the model does not
        # explain the trace" are different claims and only the second one warrants a scan.
        half_span = 0.5 * float(x[-1] - x[0])
        ref_offset = abs(ref_primary - float(l0))
        ref_offset_frac = ref_offset / half_span if half_span > 0 else np.inf
        ref_offset_ok = bool(ref_offset_frac <= REF_MAX_OFFSET_FRAC)

        # No policy => switch immediately (standalone + harness). A policy => the app's
        # hysteresis decides, and it only gets to choose the fallback if that is in fact
        # trustworthy -- hysteresis may delay a switch, never fabricate a good verdict.
        use_fallback = (ref_policy.update(ok_primary) if ref_policy is not None
                        else not ok_primary)
        use_fallback = bool(use_fallback and ok_fallback)

        # ...but a DISPLACED-CORE failure must not be answered by moving the reference to
        # l0. The control loop locks to whatever `ref_wl` says, and l0 is exactly the
        # quantity that just moved -- it wobbles ~1 nm frame to frame as the contrast crop
        # breathes (see ClipCache's docstring), and at the measured carrier of ~8.17 rad/nm
        # a 0.5 nm wobble in the reference IS 4 rad of phase. Falling back would hand the
        # loop a bigger error than the bias we are rejecting, dressed as a good frame.
        # So: when the reference is unsupportable because the core drifted off it, report
        # the frame as underdetermined and let the accept gate DROP it. Dropping is cheap
        # (the loop is integrating at gain ~0.05 over ~20 frames); a wrong number is not.
        if not ref_offset_ok:
            use_fallback = False

        if use_fallback:
            ref_wl, trust_ok, shape_ok = float(l0), ok_fallback, shape_fallback
            csig_sigma, csig_at_centre = sig_fallback, b_fallback
        else:
            ref_wl, trust_ok, shape_ok = ref_primary, ok_primary, shape_primary
            csig_sigma, csig_at_centre = sig_primary, b_primary

        t_run = (time.perf_counter() - t_run0) * 1e3 - t_trunc
    except Exception as e:  # hard failure -> the harness buckets this as a crash
        t_run = (time.perf_counter() - t_run0) * 1e3
        return {"status": "error", "csig": None, "t_run": t_run,
                "msg": f"{type(e).__name__}: {e}"}

    aU, muU, sU, offU = pU
    fwhmU = 2.3548 * abs(sU)

    # A fit the data cannot support is reported as "underdetermined", NOT as a number
    # indistinguishable from a good one. The coefficients and their sigmas still come
    # back for inspection; the status is the contract.
    # `trust_ok` is now the PHASE gate alone (c0 at ref_wl), so an "underdetermined" status
    # always means c0 and the message says so directly -- no argmax over four coefficients,
    # which would have named c1 or c2 as "worst" while c0 was the clause that actually
    # failed. A fit whose SHAPE is untrustworthy but whose phase is fine stays status "ok"
    # and reports shape_ok=False; that is a usable frame for the loop and an unusable one
    # for the frequency readout, and conflating the two is what over-rejected real frames.
    # NB a displaced core does NOT set a failing status here -- it is an accept-gate
    # concern, not a "this fit failed" concern; see the ref_offset_ok block above. The
    # message is still built so whoever drops the frame can say why.
    if ref_offset_ok:
        R["ref_offset_msg"] = ""
    else:
        R["ref_offset_msg"] = (
            f"core displaced off the reference: |{ref_primary:.2f} - {l0:.2f}| = "
            f"{ref_offset:.2f} nm = {ref_offset_frac:.0%} of the {half_span:.2f} nm "
            f"core half-span (max {REF_MAX_OFFSET_FRAC:.0%}); the phase at "
            f"{ref_primary:.2f} nm would be biased by the crop, not measured")

    if not trust_ok:
        R["status"] = "underdetermined"
        where = "the core centroid" if use_fallback else "the spectral centre"
        alt = "" if use_fallback else " (the core-centroid fallback could not be trusted either)"
        R["msg"] = (f"phase underdetermined at {where} {ref_wl:.2f} nm (c0 sigma="
                    f"{csig_sigma[0]:.3g}, need {TRUST_NSIG if trust_nsig is None else trust_nsig:g}"
                    f"*sigma <= {TRUST_TOL_C0:g} rad); "
                    f"{len(x)} pts over {x[-1] - x[0]:.2f} nm{alt}")

    R.update(dict(
        x_all=x_all, y_all=y_all, keep=keep, n_full=n_full,
        x=x, y=y, n=n, mid=mid, half=half, dx=dx,
        pU=pU, pLn=pLn, muU=muU, fwhmU=fwhmU, aU=aU,
        x_left=x_left, x_right=x_right,
        phase=phase, f_inst=f_inst, fs=fs,
        l0=l0, has_null=has_null, null_wl=null_wl, prom=prom, order=order,
        csig=csig, cph=cph, c0=c0, c1=c1, c2=c2, c3=c3, phase_cubic=phase_cubic,
        f_model=f_model, y_model=y_model, f_hilbert=f_hilbert, y_hilbert=y_hilbert,
        resid_sig=resid_sig, rms_sig=rms_sig,
        r2_sig=r2_sig, r2_fringe=r2_fringe, t_run=t_run, env_refit=env_refit,
        pU_prelim=pU_prelim,
        trunc=trunc, t_trunc=t_trunc,
        csig_sigma=csig_sigma, csig_at_centre=csig_at_centre, trust_ok=trust_ok,
        cov=cov, fit_span=(float(x[0]), float(x[-1])),
        # The reference the coefficients above are expressed at, and how it was chosen.
        # csig_at_centre is ALWAYS at ref_wl -- read ref_wl, never assume 802.
        ref_wl=ref_wl, ref_fallback=use_fallback, ref_primary=ref_primary,
        trust_primary_ok=ok_primary, trust_fallback_ok=ok_fallback,
        # How far the fitted core sits from the reference, as a fraction of its own
        # half-span. The accuracy gate's input, logged so a run can be audited.
        ref_offset_nm=ref_offset, ref_offset_frac=ref_offset_frac,
        ref_offset_ok=ref_offset_ok, ref_offset_msg=R.get("ref_offset_msg", ""),
        shape_ok=shape_ok, shape_primary_ok=shape_primary,
        shape_fallback_ok=shape_fallback,
    ))
    return R


def analyze(x, y, anchor=None, ref_policy=None, trust_nsig=None, trunc_threshold=None,
            ref_primary=None, recover=True, scanfree=None, trunc_method=None,
            clip_cache=None, env_center=None, manual_cut_left=None):
    """Fit one trace, and if the model cannot explain it, find the cut that can.

    Fit normally first. If that explains the trace (`_explains`), return it -- this is the
    common case and costs nothing extra. Only if the fit FAILS do we scan candidate cuts,
    i.e. we spend time only on frames that would otherwise have been dropped and produced
    nothing at all.

    Why a scan rather than a detector: every detector has to MEASURE the clip, and each
    measurement is defeated by a different property of a real trace (see the TRUNCREC_*
    block). The fit needs none of them -- a wrong cut fits badly, the right one fits at
    r2_fringe = 0.990 -- so we ask the fit instead.

    We take the SMALLEST cut that explains the trace, not the best-scoring one. rms_frac
    always improves as you cut more, because you are deleting the hardest data, so a scan
    that minimised it would happily "explain" any bad frame by cutting down to three
    fringes. Stopping at the first success makes a needless cut unreachable.

    R["recovered"] says a cut was found this way, and R["trunc"]["side"]/["cut_left"]/
    ["cut_right"] carry it, so a caller cannot tell a scanned cut from a detected one and
    does not need to.
    """
    # MANUAL truncation short-circuits everything. The operator has dragged the clip edge
    # on the chart, and that is assumed robust (2026-07-20) -- far more reliable than the
    # auto-detector, which we measured barely seeing the real left clip while hallucinating
    # right cuts. So when a manual cut is supplied we skip the detector, the recovery scan
    # AND the clip cache: there is nothing to search for, the answer is one deterministic
    # fit on the operator-selected domain. env_center (the dragged envelope pin) rides along.
    if manual_cut_left is not None:
        ft = {"side": "left", "detected": True, "cut_left": float(manual_cut_left),
              "cut_right": None, "dead": None, "live": None, "left_nm": 0.0,
              "right_nm": 0.0, "x_lo": None, "x_hi": None, "msg": "manual cut"}
        R = _analyze_once(x, y, anchor=anchor, ref_policy=ref_policy, trust_nsig=trust_nsig,
                          trunc_threshold=trunc_threshold, ref_primary=ref_primary,
                          force_trunc=ft, env_center=env_center)
        R["recovered"] = False
        R["rms_frac"] = _rms_frac(R)
        return R

    use_scanfree_0 = SCANFREE if scanfree is None else scanfree
    if clip_cache is not None and recover and not use_scanfree_0:
        return _analyze_cached(x, y, clip_cache, anchor=anchor, ref_policy=ref_policy,
                               trust_nsig=trust_nsig, trunc_threshold=trunc_threshold,
                               ref_primary=ref_primary, env_center=env_center)
    R = _analyze_once(x, y, anchor=anchor, ref_policy=ref_policy, trust_nsig=trust_nsig,
                      trunc_threshold=trunc_threshold, ref_primary=ref_primary,
                      scanfree=scanfree, trunc_method=trunc_method, env_center=env_center)
    R["recovered"] = False
    R["rms_frac"] = _rms_frac(R)
    # Scan-free pipeline: the deterministic fit is the ONLY fit path (PLAN constraint #1/#2).
    # It lands the truncated fit in one pass, so there is no recovery scan to fall back to.
    use_scanfree = SCANFREE if scanfree is None else scanfree
    if use_scanfree or not recover:
        return R
    ok = _explains(R)
    # A frame that explains itself but hits the core is a SUSPECTED missed clip: scan it,
    # but keep the uncut answer unless the cut is decisively better (TRUNCREC_HC_IMPROVE).
    on_suspicion = bool(ok and TRUNCREC_SCAN_ON_HITS_CORE and _missed_clip(R))
    if ok and not on_suspicion:
        return R
    R2 = _recovery_scan(x, y, R, anchor=anchor, ref_policy=ref_policy,
                        trust_nsig=trust_nsig, trunc_threshold=trunc_threshold,
                        ref_primary=ref_primary, env_center=env_center)
    if R2 is None:
        return R
    if on_suspicion and not (_rms_frac(R2) < TRUNCREC_HC_IMPROVE * _rms_frac(R)):
        R["hc_scan_declined"] = True
        return R
    R2["hc_scan"] = on_suspicion
    return R2


def _recovery_scan(x, y, R, anchor=None, ref_policy=None, trust_nsig=None,
                   trunc_threshold=None, ref_primary=None, deadline=None, env_center=None):
    """Scan candidate cuts until one explains the trace. Returns the recovered R, or None.

    Split out of `analyze` so the clip cache can fall back to the EXACT same search
    instead of forking it (one source of truth). `deadline` is a `time.perf_counter()`
    value: past it the scan gives up and returns None -- the hard cap of PLAN sec.3b, a
    failure signal, NOT an anytime/best-so-far commit. Nothing else about the search is
    parameterised; in particular the candidate ORDER is fixed, because "smallest cut
    first, first success wins" is what makes a needless cut unreachable. Reordering the
    grid (e.g. seeding it at a remembered lambda) would quietly replace the minimal cut
    with a merely-adequate one, so the cache short-circuits this scan rather than steering
    it.
    """
    # ref_policy is STATEFUL ACROSS FRAMES (REF_HYST consecutive traces to switch), so the
    # scan must not touch it: 32 candidates would drive the hysteresis counter 32x in one
    # frame and switch the reference on the first bad trace instead of the fifth. Candidates
    # run with ref_policy=None; only the winner is re-fit with the policy.

    x = np.asarray(x, float)
    # Anchor the grid to the FIT CORE, not to the ZOOM window. A clip only matters where it
    # intrudes on the core (outside it the contrast cut removes it anyway), and the window
    # edge can be ~10 nm from the core -- scanning from there wastes the whole budget out in
    # the wings and never reaches the clip (measured: 32 candidates, none within 6 nm of a
    # real 800.3 edge). R["x"] IS the core the first fit used.
    if R.get("x") is None or len(R["x"]) < 2:
        return None
    lo, hi = float(R["x"][0]), float(R["x"][-1])
    if (hi - lo) <= TRUNCREC_MIN_SPAN_NM:
        return None       # no room for any cut at all
    best = None
    # Smallest cut first, alternating sides, so the first success IS the minimal one.
    steps = int(TRUNCREC_MAX_NM / TRUNCREC_STEP_NM)
    for n in range(1, steps + 1):
        d = n * TRUNCREC_STEP_NM
        for side in ("left", "right"):
            if deadline is not None and time.perf_counter() > deadline:
                return None           # hard cap: fail the frame, do not commit a half-search
            cut = lo + d if side == "left" else hi - d
            if side == "left" and (hi - cut) < TRUNCREC_MIN_SPAN_NM:
                continue
            if side == "right" and (cut - lo) < TRUNCREC_MIN_SPAN_NM:
                continue
            ft = {"side": side, "detected": True, "v": None, "dead": None, "live": None,
                  "x_lo": None, "x_hi": None, "left_nm": d if side == "left" else 0.0,
                  "right_nm": d if side == "right" else 0.0,
                  "cut_left": cut if side == "left" else None,
                  "cut_right": cut if side == "right" else None,
                  "msg": f"cut found by recovery scan ({side} at {cut:.2f} nm)"}
            try:
                R2 = _analyze_once(x, y, anchor=anchor, ref_policy=None,
                                   trust_nsig=trust_nsig, trunc_threshold=trunc_threshold,
                                   ref_primary=ref_primary, force_trunc=ft,
                                   env_center=env_center)
            except Exception:
                continue
            R2["rms_frac"] = _rms_frac(R2)
            if _explains(R2):
                best = (R2, ft)
                break
        if best is not None:
            break
    if best is None:
        return None                   # nothing explains it: the caller reports the failure

    R2, ft = best
    if ref_policy is not None:
        # Re-fit the winner with the policy so its reference choice is hysteretic like any
        # other frame. This is the frame's SECOND policy update (the first was R above), so
        # a recovered frame counts double toward the REF_HYST streak -- switching after ~3
        # effective frames rather than 5. Accepted deliberately: the alternative is either
        # to fit every good frame twice (doubling the cost of the common case) or to let a
        # recovered frame use the non-hysteretic rule, and a truncated frame is exactly when
        # the reference is most likely to move, so it is the worst one to leave unguarded.
        try:
            R3 = _analyze_once(x, y, anchor=anchor, ref_policy=ref_policy,
                               trust_nsig=trust_nsig, trunc_threshold=trunc_threshold,
                               ref_primary=ref_primary, force_trunc=ft,
                               env_center=env_center)
            R3["rms_frac"] = _rms_frac(R3)
            if _explains(R3):
                R2 = R3
        except Exception:
            pass
    R2["recovered"] = True
    return R2


# ===================== CLIP-EDGE CACHE (cross-frame state) ===================
# WHY THIS EXISTS (measured, Task 15): on the instrument the RECOVERY SCAN is both the
# thing that works (14/14 correct sides came from it; the detector's `side` was 0/30 on
# synth) and the entire latency cost (mean fit 225 -> 645 ms when an arm is clipped, i.e.
# ~18 blind candidate fits per frame). Every frame re-derives, from scratch, a cut that
# has not moved: the knife is a piece of metal, stable in WAVELENGTH, while everything
# else about the trace drifts. Remembering lambda turns that global search into a single
# hypothesis test -- one fit instead of eighteen.
#
# It also breaks a documented circularity. "Detect the deadzone first, then fit the
# envelope on clean data" was recorded INFEASIBLE, because detection needs a
# normalization and the normalization needs the deadzone gone. The cache supplies the
# PREVIOUS frame's answer, so the first envelope of THIS frame is already fit on the
# fringe-bearing band -- via the existing `force_trunc` path, which is exactly that
# pipeline and needed no new fitting code.
#
# WHAT KEEPS IT HONEST. The cache can only ever be consulted on a frame whose UNCUT fit
# already failed `_explains` (see `_analyze_cached` -- the plan called for the opposite
# order and the measurements overruled it), so it can only turn a failure into a success.
# On top of that:
#   * `_explains` judges every frame on its OWN data; nothing commits on the cache's word;
#   * n consecutive failures flush the cache -- this catches a knife that moved INWARD,
#     because the stale cut then leaves dead samples in the fit and the fit fails;
#   * the SHRINK PROBE catches the other direction, which no metric can. A knife that
#     moved OUTWARD leaves a stale cut that still fits beautifully -- it just throws away
#     live fringes. `rms_frac` IMPROVES as you cut more (you are deleting the hardest
#     data), so an over-deep cut is both invisible and REWARDED by the quality metric.
#     Measured, an over-deep cut is not merely a lever-arm loss: on a clean real trace a
#     stale cut passed every check while putting the phase at the reference 3.2 rad out,
#     and an injected 802.0 nm cut 18.3 rad out. That is why the ordering above matters
#     and why this probe is not optional.
#
# WHAT MAY VOTE. Only a cut that some FIT accepted without the cache's help: a fresh
# recovery scan, or a shrink probe. A cache-hit frame resets the failure counter and
# contributes NO lambda -- it was fit on data the cache itself selected, so its agreement
# is not evidence; that is a closed loop and it would freeze the estimate wherever it
# happened to start.
#
# `detect_truncation`'s own cut_left/cut_right are NOT voted, though the plan proposed the
# raw dead mask as the primary vote source on the strength of a 28/30 SYNTHETIC hit rate.
# Measured on the real traces (archive/probes/cc_gate.py) that number does not transfer at all:
#     2020607181645  clipped at 800.14 -- mask marks 15 dead samples at 807.26 (wrong end)
#     da17_1GA_-75   clipped at 797.95 -- mask marks NOTHING
#     live_desktop   clean             -- mask marks 16 dead samples at 812 (false)
# So on real data the mask neither finds the clips nor stays quiet without them, and a
# cut sourced from it would be a fiction the cache then defends. [[synthetic-data-gap]]
# again: it is also why the cached cut cannot be GATED on this frame's mask, which would
# otherwise have let the cut be applied first and saved a fit.
#
# State lives in the CALLER, exactly like `ref_policy`: omit `clip_cache` and analyze()
# is bit-identical to before.
CLIPCACHE_HIST = 5            # accepted cuts kept per side; the applied cut is their
                              # MEDIAN, so one bad vote cannot move it.
CLIPCACHE_MAX_FAILS = 3       # consecutive frames a cached cut may fail `_explains`
                              # before it is flushed. 1 would thrash on an ordinary bad
                              # frame (noise, a dropped shot); this needs the failure to
                              # persist, which a moved knife does and a bad frame does not.
CLIPCACHE_BUDGET_MS = 3000.0  # hard cap on analyze() wall time. The user's number: >3 s
                              # "feels like forever", and today's worst case is 4207 ms
                              # and unbounded. Exceeding it FAILS the frame (and counts
                              # toward MAX_FAILS) rather than committing a partial search.
CLIPCACHE_SHRINK_NM = 1.0     # how much shallower the shrink probe cuts. BIG on purpose:
                              # a small step sits inside frame-to-frame noise, so its
                              # pass/fail says nothing, and if a shallower cut passes at
                              # all the knife has probably moved a real distance -- a fine
                              # step would then need many frames to walk back. 4x the scan
                              # grid (TRUNCREC_STEP_NM = 0.25) converges in one or two
                              # probes and its verdict is unambiguous.
CLIPCACHE_SHRINK_EVERY = 8    # frames between probes while a cached cut is in USE. Was
                              # 20; tightened because the exposure being bounded here is
                              # a wrong phase, not just a short lever arm (see above).
                              # A probe only ever runs on a frame that already needed the
                              # cut, so this is one extra fit per 8 clipped frames -- and
                              # a successful shrink re-probes immediately, so walking back
                              # a knife that moved 3 nm takes 3 frames, not 3 probes.

# --- the NEGATIVE cache: remember that the scan FAILED --------------------------
# Measured on the real traces (archive/probes/cc_smoke.py, 12-frame replay), and it is the
# largest remaining cost by a wide margin:
#     2020607181645  460 -> 93 ms   (cache hit; the scan runs once)
#     da17_1GA_-75   417 -> 66 ms   (cache hit)
#     da_15.95ga_-55.29           1845 ms EVERY FRAME
#     da_15.95ga_-75              2779 ms EVERY FRAME
# The last two commit (trust_ok=True) but sit at rms_frac 0.35/0.49, far above
# TRUNCREC_TRIGGER, so `_explains` is False and the scan runs -- EXHAUSTIVELY, ~18
# candidates, finding nothing, on every single frame, forever. A positive cache cannot
# help: there is no cut to remember, because no cut exists. What repeats here is the
# FAILURE, so that is what must be remembered.
#
# This does not change the frame's answer: an exhausted scan returns the same primary fit
# it started from. It only stops re-proving the same negative. The retry exists because
# the trace CAN become recoverable (the operator inserts a knife), and the backoff is in
# frames rather than seconds so it scales with acquisition rate.
CLIPCACHE_SCANFAIL_MAX = 2    # consecutive exhaustive scan failures before backing off.
                              # 2, not 1: one failure can be a bad frame.
CLIPCACHE_GROW_STEPS = 6      # deepening probes (in TRUNCREC_STEP_NM = 0.25 nm units)
                              # tried around a FAILED cached cut before falling back to
                              # the global scan: 1.5 nm of travel for at most 6 fits,
                              # against ~18 for the scan. Sized from the simulator's
                              # scripted 1.0 nm inward move, which is already a large
                              # deliberate adjustment by hand.
CLIPCACHE_SCANFAIL_RETRY = 10 # while backed off, re-attempt the scan every N frames, so
                              # a newly-inserted clip is found within ~3 s at 3 fps and
                              # the steady-state cost of a permanently-bad trace is one
                              # scan per 10 frames instead of one per frame.


class ClipCache:
    """Remembered clip edge, in WAVELENGTH, carried across frames by the caller.

    Never a sample index and never a core-relative offset: the core centroid `l0` wobbles
    ~1 nm frame to frame (the contrast crop breathes), while the knife does not move at
    all. Caching an index would re-introduce that wobble as a moving cut.
    """

    def __init__(self, hist=CLIPCACHE_HIST, max_fails=CLIPCACHE_MAX_FAILS,
                 budget_ms=CLIPCACHE_BUDGET_MS, shrink_nm=CLIPCACHE_SHRINK_NM,
                 shrink_every=CLIPCACHE_SHRINK_EVERY,
                 scanfail_max=CLIPCACHE_SCANFAIL_MAX,
                 scanfail_retry=CLIPCACHE_SCANFAIL_RETRY,
                 grow_steps=CLIPCACHE_GROW_STEPS):
        self.hist, self.max_fails = int(hist), int(max_fails)
        self.budget_ms, self.shrink_nm = float(budget_ms), float(shrink_nm)
        self.shrink_every, self.grow_steps = int(shrink_every), int(grow_steps)
        self.scanfail_max, self.scanfail_retry = int(scanfail_max), int(scanfail_retry)
        self.votes = {"left": [], "right": []}
        self.fails = 0
        self.frames = 0
        self.last_probe = 0           # frame number of the last shrink probe
        self.probe_now = False        # a successful shrink re-probes IMMEDIATELY (the
                                      # knife has demonstrably moved; expect more)
        self.scan_fails = 0           # consecutive exhaustive scan failures
        self.last_scan = 0            # frame number of the last scan ATTEMPT
        self.stats = {"hit": 0, "miss": 0, "cold": 0, "fail": 0, "flush": 0,
                      "scan": 0, "probe": 0, "shrunk": 0, "capped": 0,
                      "scanfail": 0, "scanfail_capped": 0, "suppressed": 0, "grown": 0}
        self.log = []                 # (frame, event, detail) -- diagnostics only

    # --- state -------------------------------------------------------------------
    def cut(self, side):
        """Median of the remembered cuts for one side, or None."""
        v = self.votes[side]
        return float(np.median(v)) if v else None

    def has_cut(self):
        return bool(self.votes["left"] or self.votes["right"])

    def vote(self, side, lam, why=""):
        if side not in self.votes or lam is None or not np.isfinite(lam):
            return
        self.votes[side].append(float(lam))
        del self.votes[side][:-self.hist]
        self.log.append((self.frames, "vote", "%s %.2f %s" % (side, lam, why)))

    def flush(self, why=""):
        self.votes = {"left": [], "right": []}
        self.fails = 0
        self.probe_now = False
        self.stats["flush"] += 1
        self.log.append((self.frames, "flush", why))

    def force(self, det, shrink=0.0):
        """The cut to apply this frame, as a `force_trunc` dict, or None.

        Built ON TOP of the raw detector report so `dead`/`live`/`v` (hence `hits_core`
        and every diagnostic downstream) still describe THIS frame's trace -- the cache
        overrides only the cut itself. `shrink` moves both edges outward by that many nm,
        which is the shrink probe.
        """
        l, r = self.cut("left"), self.cut("right")
        if l is None and r is None:
            return None
        if shrink:
            l = None if l is None else l - shrink
            r = None if r is None else r + shrink
        ft = dict(det) if det else {}
        ft.update(side=("both" if (l is not None and r is not None)
                        else "left" if l is not None else "right"),
                  detected=True, cut_left=l, cut_right=r,
                  msg=("cut from clip cache (shrink %.2f nm)" % shrink) if shrink
                      else "cut from clip cache")
        return ft

    def due_probe(self):
        return (self.has_cut() and self.shrink_every > 0
                and (self.probe_now
                     or (self.frames - self.last_probe) >= self.shrink_every))

    def skip_scan(self):
        """Has the scan failed often enough, recently enough, to be worth skipping?"""
        return (self.scanfail_max > 0 and self.scan_fails >= self.scanfail_max
                and (self.frames - self.last_scan) < self.scanfail_retry)

    def on_scan(self, found, exhausted=True):
        """Record the outcome of an ATTEMPTED scan.

        A CAPPED scan (`exhausted` False) counts toward the backoff too, and the reason is
        worth stating because the opposite is tempting: a capped scan does not prove no cut
        exists, only that none was reachable inside the budget -- but the scan is
        deterministic in its ordering, so re-running it on the next frame cuts off at the
        same place and learns the same nothing. The operative question is not "does a cut
        exist" but "is one findable within the budget", and a capped scan answers that.
        MEASURED both ways on the real traces: excluding capped scans leaves
        `da_15.95ga_-55.29` and `da_15.95ga_-75` re-running a doomed 3 s search on EVERY
        frame (3104/3115 ms median); including them drops that to ~150 ms.
        The counterexample that made this look wrong -- clipcache_sim's `knife moves
        INWARD`, where the backoff once cost 12 correct frames -- was never really about
        capped scans: the cap itself was truncating the search before it reached the moved
        edge. That is now handled where it belongs, by the GROW PROBE, which finds a moved
        edge in 1-4 fits without any global search.
        """
        self.last_scan = self.frames
        if found:
            self.scan_fails = 0
        else:
            self.scan_fails += 1
            self.stats["scanfail" if exhausted else "scanfail_capped"] += 1

    # --- outcomes ----------------------------------------------------------------
    def on_success(self):
        self.fails = 0

    def on_fail(self, why=""):
        self.fails += 1
        self.stats["fail"] += 1
        self.log.append((self.frames, "fail",
                         "%s (%d/%d)" % (why, self.fails, self.max_fails)))
        if self.fails >= self.max_fails:
            self.flush("%d consecutive failures: %s" % (self.fails, why))

    def summary(self):
        s = dict(self.stats)
        s.update(frames=self.frames, left=self.cut("left"), right=self.cut("right"),
                 fails=self.fails)
        return s


def _analyze_cached(x, y, cache, anchor=None, ref_policy=None, trust_nsig=None,
                    trunc_threshold=None, ref_primary=None, env_center=None):
    """`analyze` with a clip cache. Same contract, same return dict (+ R["clip_cache"]).

    Order of operations, and why:
      1. `detect_truncation` on the RAW, UNTRIMMED trace -- ONCE per frame, ~25 ms. It is
         self-contained, so its verdict is independent of whatever cut we are about to
         apply. Its report is then handed to `_analyze_once` as `force_trunc`, which is
         exactly what that function would have computed itself: no work is duplicated.
      2. Fit UNCUT, exactly as today. If that explains the trace, return it: the cache had
         no influence on the answer at all.
      3. Only if that FAILS, fit with the remembered cut. Hit => done, two fits (~90 ms
         against ~645 ms for the scan).
      4. Still unexplained => today's recovery scan, under the wall-clock cap and the
         repeat-failure backoff.

    WHY UNCUT FIRST, against PLAN_clip_cache sec.1 (which specified cached-cut-first so
    the cut would feed the first envelope fit): MEASURED, on the real traces, applying a
    stale cut first is not a lever-arm problem, it is a WRONG-PHASE problem.
      * cache warmed on `2020607181645_truncated`, then fed the CLEAN
        `live_desktop_spectrum` (= knife removed): the stale cut still passed `_explains`
        on every frame -- 173 core points instead of 260 -- and moved the phase at the
        reference by 3.21 rad;
      * an injected 802.0 nm cut on that same clean trace: also passed `_explains`, 125
        points, 18.3 rad out.
    Both are silent: `_explains` cannot object, because rms_frac IMPROVES as you cut more.
    Under the plan's ordering nothing would have caught either until the next shrink probe
    (up to 20 frames, ~7 s at 3 fps) -- and 3.2 rad is ten times the 0.314 rad the trust
    gate is there to enforce, fed straight to the control loop.
    Fitting uncut first makes that class of error UNREACHABLE: the cut is only ever
    consulted on a frame the uncut fit could not explain, i.e. only where it can help.
    It costs one extra fit (~40 ms) on genuinely clipped frames and nothing elsewhere,
    which still lands inside the plan's 100-200 ms target. The plan's stated reason for
    cut-first -- deleting the prelim-envelope -> knife -> refit sequence -- does not apply
    to the SHIPPED pipeline anyway: that sequence is pipeline B (`SCANFREE`/
    `DEADZONE_REFIT`, both OFF). In the shipped path the uncut fit is not wasted work, it
    IS the answer on every clean frame.
    """
    t0 = time.perf_counter()
    deadline = t0 + cache.budget_ms * 1e-3
    cache.frames += 1
    kw = dict(anchor=anchor, trust_nsig=trust_nsig, trunc_threshold=trunc_threshold,
              ref_primary=ref_primary, env_center=env_center)

    t_d0 = time.perf_counter()
    try:
        det = detect_truncation(x, y)
    except Exception as e:
        det = {"side": "unknown", "detected": False, "v": None, "dead": None,
               "live": None, "x_lo": None, "x_hi": None, "left_nm": 0.0, "right_nm": 0.0,
               "cut_left": None, "cut_right": None,
               "msg": "detector failed: %s: %s" % (type(e).__name__, e)}
    t_det = (time.perf_counter() - t_d0) * 1e3

    def finish(R, tag):
        R["clip_cache"] = tag
        R["t_detect"] = t_det
        R["t_wall"] = (time.perf_counter() - t0) * 1e3
        R.setdefault("recovered", False)
        return R

    def run(ft, policy=ref_policy):
        R = _analyze_once(x, y, ref_policy=policy, force_trunc=ft, **kw)
        R["rms_frac"] = _rms_frac(R)
        R["recovered"] = False
        return R

    # --- 2. today's first fit, UNCUT (the detector's own verdict only) ------------
    cache.stats["cold"] += 1
    R = run(det)
    # A frame that explains itself but whose dead mask reaches the fit core is a suspected
    # missed clip (see `_missed_clip`): let it fall through to the cached cut / scan, but
    # hold on to the uncut fit and keep it unless the cut is decisively better.
    R_uncut = R if (TRUNCREC_SCAN_ON_HITS_CORE and _explains(R) and _missed_clip(R)) else None
    if _explains(R) and R_uncut is None:
        # Nothing to recover: this IS today's answer, bit-for-bit, and the cache had no
        # say in it. Note the cache is NOT flushed here -- a trace can explain uncut and
        # still be clipped (the contrast crop removes a shallow clip unaided, e.g.
        # truncated.csv), so a success here is not evidence the knife is gone. The cached
        # cut simply goes unused this frame, which is the whole point of the ordering.
        cache.on_success()
        return finish(R, "uncut-ok")

    def hc_beats(Rn):
        """Is this cut decisively better than the uncut fit we are holding?

        Vacuously True on an ordinary failing frame: there is no standing answer to keep,
        so the normal accept rule (`_explains`) is the only test.
        """
        return (R_uncut is None
                or _rms_frac(Rn) < TRUNCREC_HC_IMPROVE * _rms_frac(R_uncut))

    def keep_uncut():
        """Suspicion was not confirmed: the uncut fit, which already explained the trace,
        stands. Reached ONLY on suspicion frames, so it is never a lost recovery."""
        R_uncut["hc_scan_declined"] = True
        cache.on_success()
        return finish(R_uncut, "uncut-ok")

    def finish_hc(Rn, tag):
        """`finish`, marking a cut that was accepted on suspicion rather than on failure."""
        if R_uncut is not None:
            Rn["hc_scan"] = True
        return finish(Rn, tag)

    # --- 3. the fit failed: try the REMEMBERED cut before searching for a new one --
    ft = cache.force(det)
    if ft is not None and det.get("side") != "all":
        Rc = run(ft)
        if _explains(Rc) and hc_beats(Rc):
            cache.on_success()
            cache.stats["hit"] += 1
            if cache.due_probe():
                Rc = _shrink_probe(x, y, cache, Rc, det, run)
            return finish_hc(Rc, "hit")
        if R_uncut is not None:
            # SUSPICION FRAME, and the remembered cut did not beat an answer that already
            # stands. That is NOT evidence the cut has gone stale, so it must not touch the
            # cache: `on_fail` counts toward invalidation (`CLIPCACHE_MAX_FAILS` flushes the
            # whole memory) and the grow probe exists to chase an edge that MOVED, which a
            # frame we cannot even prove is clipped gives no reason to believe.
            # This is also the cheap path the cache buys us: one cached fit decides the
            # suspicion instead of an ~18-candidate scan.
            return keep_uncut()
        cache.stats["miss"] += 1
        cache.on_fail("cached cut did not explain the trace")
        Rg = _grow_probe(x, y, cache, det, run)
        if Rg is not None:
            return finish_hc(Rg, "grown")

    # --- 4. recovery scan, capped, and not re-run if it keeps failing -------------
    if cache.skip_scan():
        if R_uncut is not None:      # suspicion only: the uncut fit still stands
            return keep_uncut()
        cache.stats["suppressed"] += 1
        R["msg"] = (R.get("msg", "") + " [clip-cache: recovery scan suppressed after %d "
                    "consecutive failures; retry in %d frames]"
                    % (cache.scan_fails,
                       cache.scanfail_retry - (cache.frames - cache.last_scan))).strip()
        return finish(R, "scan-suppressed")
    cache.stats["scan"] += 1
    R2 = _recovery_scan(x, y, R, ref_policy=ref_policy, deadline=deadline, **kw)
    cache.on_scan(R2 is not None, exhausted=time.perf_counter() <= deadline)
    if R2 is None:
        if R_uncut is not None:      # suspicion only: nothing better was found, keep uncut
            return keep_uncut()      # note: BEFORE the cap's on_fail, so a slow suspicion
        if time.perf_counter() > deadline:   # frame cannot invalidate a good cached cut
            cache.stats["capped"] += 1
            R["msg"] = (R.get("msg", "") + " [clip-cache: %.0f ms cap exceeded, frame "
                        "failed]" % cache.budget_ms).strip()
            # The cap counts toward invalidation only when a cached cut was in force --
            # that is what n-fails invalidates. With no cache there is nothing to flush
            # and incrementing the counter would just mislabel a slow frame as a stale one.
            if cache.has_cut():
                cache.on_fail("wall-clock cap exceeded")
        return finish(R, "scan-failed")
    # ORDER MATTERS: the improvement guard runs BEFORE the votes. A cut this frame
    # declines must not be taught to the cache -- otherwise the guard protects THIS
    # frame's answer and then the rejected cut is forced onto every later frame, which
    # is the corruption the guard exists to prevent, merely deferred by one frame.
    if not hc_beats(R2):
        return keep_uncut()
    t = R2.get("trunc", {})
    for s in ("left", "right"):
        if t.get("cut_" + s) is not None:
            cache.vote(s, t["cut_" + s], "recovery scan")
    cache.on_success()
    cache.last_probe = cache.frames             # a fresh scan IS a minimal-cut check
    return finish_hc(R2, "scan-recovered")


def _grow_probe(x, y, cache, det, run):
    """The cached cut failed. Try a few DEEPER cuts around it before searching globally.

    Returns the recovered R, or None to fall through to the full scan.

    Why this is not the "amortized seeded search" the plan rejected, and why it does not
    break the scan's minimal-cut rule: we only get here after the UNCUT fit failed AND the
    cached cut failed. A knife that moved OUTWARD cannot land here -- its stale cut is too
    DEEP, which still fits, so it lands on the cache-hit path and is walked back by the
    shrink probe. So a miss means the cut we need is deeper than the one we have, and
    deepening from the last known edge searches exactly that direction.

    MEASURED (clipcache_sim, `knife moves INWARD` 1.0 nm): without this the frame falls
    into the full scan, which needs 2.9-5.5 s to reach a cut that far in and therefore
    trips the 3 s cap -- 12 consecutive wrong frames where the uncapped baseline was
    right. With it the new edge is found in 1-4 fits, inside the cap, and the cache
    relearns immediately.
    """
    if not cache.has_cut() or cache.grow_steps <= 0:
        return None
    base_l, base_r = cache.cut("left"), cache.cut("right")
    for k in range(1, cache.grow_steps + 1):
        d = k * TRUNCREC_STEP_NM
        ft = dict(det)
        l = None if base_l is None else base_l + d      # deeper = further into the core
        r = None if base_r is None else base_r - d
        ft.update(side=("both" if (l is not None and r is not None)
                        else "left" if l is not None else "right"),
                  detected=True, cut_left=l, cut_right=r,
                  msg="cut from clip cache, deepened %.2f nm" % d)
        try:
            Rg = run(ft)
        except Exception:
            continue
        if _explains(Rg):
            cache.votes = {"left": [], "right": []}     # the knife MOVED; old votes are
            for s, v in (("left", l), ("right", r)):    # about a position that is gone
                if v is not None:
                    cache.vote(s, v, "grow probe")
            cache.on_success()
            cache.stats["grown"] += 1
            return Rg
    return None


def _shrink_probe(x, y, cache, R_hit, det, run):
    """Test whether a SHALLOWER cut also explains the trace; adopt it if so.

    This is the recovery scan's Occam rule ("take the smallest cut that explains") applied
    ACROSS frames. Without it, a knife that moved outward or was removed leaves a cached
    cut that passes every check we have while silently deleting live fringes -- and since
    `rms_frac` improves as you cut more, no quality metric will ever complain. Costs one
    fit per CLIPCACHE_SHRINK_EVERY frames.

    On success the shrunk cut REPLACES the frame's answer (it is the better-supported fit:
    same explanation, more lever arm) and the next frame probes again immediately.
    """
    cache.last_probe = cache.frames
    cache.probe_now = False
    cache.stats["probe"] += 1
    # Big step first, then BISECT it twice on failure. The plan's objection to a small
    # step -- that it sits inside frame-to-frame noise, so its verdict says nothing -- is
    # about the OPENING move, and it stands. These are different: once the big step has
    # failed we hold a bracket (the current cut explains, one shrink_nm shallower does
    # not), so halving it is a bisection with an unambiguous answer, not a nudge.
    # MEASURED (clipcache_sim, outward move to v=-4.5): with the big step alone the cut
    # converges to the last 1.0 nm multiple above the true edge and stalls 0.7 nm deep,
    # holding span at 9.9 nm against the baseline's 10.5.
    Rs, ft = None, None
    for frac in (1.0, 0.5, 0.25):
        ft = cache.force(det, shrink=cache.shrink_nm * frac)
        if ft is None:
            return R_hit
        try:
            Rs = run(ft)
        except Exception:
            return R_hit
        if _explains(Rs):
            break
    else:
        return R_hit
    # The shallower cut stands up. Move the cache there -- REPLACING the history, not
    # appending to it: the old votes describe a knife position that no longer exists, and
    # a median over both would sit between the two and match neither.
    cache.votes = {"left": [], "right": []}
    for s in ("left", "right"):
        if ft.get("cut_" + s) is not None:
            cache.vote(s, ft["cut_" + s], "shrink probe")
    cache.probe_now = True
    cache.stats["shrunk"] += 1
    Rs["clip_cache"] = "shrunk"
    return Rs

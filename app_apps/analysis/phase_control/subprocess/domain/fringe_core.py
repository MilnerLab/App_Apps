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
  3. crop to the high-visibility core, normalize the fringes
  4. Hilbert -> instantaneous frequency -> null candidates -> BIC picks the phase order
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
TRUNC_THRESHOLD = 0.40  # (was 0.25) fraction of peak envelope contrast kept as the core

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
                      # "with-null" seed against the "no-null" seed and BIC decides,
                      # so an inaccurate or false candidate cannot poison the fit

# --- Phase-order model selection (replaces ridge regularization) --------------
# Instead of shrinking the higher-order phase terms with a hand-tuned ridge, we fit
# nested phase models of increasing order and pick the one that earns its keep by
# BIC: order q means the instantaneous frequency is a degree-(q-1) polynomial, i.e.
# phase = c0..cq (q=1 carrier / q=2 chirp / q=3 +TOD). BIC = n·ln(SSE/n) + k·ln(n)
# penalizes extra terms, so spurious TOD is rejected automatically -- no tuning knob.
#
# Whether an in-window null exists is NOT thresholded: for each order we try BOTH a
# no-null seed (polyfit of the smooth monotonic phase) and, for q>=2, a null seed
# (|f| V-fit anchored at the |f| minimum), keep the better-fitting one, and let BIC
# choose. A weak real null (frequency just grazing zero) and a no-null core have
# nearly identical |f| prominence, so a threshold cannot separate them -- the raw-fit
# residual can. has_null is then read off the winning fit (does f cross zero?).

# --- Soft null penalty (on the |f| V-fit that provides the null seed) ------------
# The null seed fits the Hilbert |f| with a frequency polynomial whose value at the
# anchor is softly pulled to zero, so the seed genuinely has its null at the located
# minimum. Soft (a penalty, not a hard constraint) so real data still moves it.
NULL_PEN_FREQ = 3.0   # weight of the f(u=0)->0 penalty in the |f| (cycles/nm) fit

# --- Final full raw-signal fit (cubic/TOD phase, envelopes held fixed) ---
SIGNAL_LOSS_FRAC = 1.0  # soft-L1 scale as a fraction of the local half-amplitude (counts)

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
TRUST_TOL_C0 = 0.126    # rad        (0.02 * 2pi, the phase-stabilization budget)
TRUST_TOL_C3 = 0.006    # rad/nm^3   noise-limited TOD floor
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

TAU = RATIO / (RATIO + 1.0)   # ~0.91 quantile: fit hugs the upper envelope of the fringes
# =============================================================================


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

def fit_upper_envelope(x, y, off_bounds=None):
    """Gaussian hugging the upper envelope of the fringes: warm start from a symmetric L2
    fit, then refine under the asymmetric pinball loss.

    `off_bounds` bounds the offset to the continuum measured off the full frame (see
    baseline_anchor). Inside ZOOM there is no baseline to pin it, and the tau-quantile
    loss exploits that by floating the offset up.

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
    if off_bounds is not None:
        p0 = list(p0)
        p0[3] = float(np.clip(p0[3], *off_bounds))   # start inside the band
        bounds = [(-np.inf, np.inf)] * 3 + [tuple(off_bounds)]
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


def fit_freq(u, f_inst, has_null, q):
    """Fit the UNSIGNED Hilbert |f| with |degree-(q-1) frequency polynomial|, i.e.
    f(u) = g0 + g1 u + ... (q terms). A soft penalty pulls f(0)->0 when a null is
    present. Returns g padded to length 3 (units: cycles/nm)."""
    absf = np.abs(f_inst)
    w = float(np.median(absf))
    ue = float(np.max(np.abs(u))) + 1e-9

    def resid(g):
        gp = np.zeros(3); gp[:q] = g
        r = np.abs(gp[0] + gp[1] * u + gp[2] * u ** 2) - absf
        if has_null:
            r = np.concatenate([r, [NULL_PEN_FREQ * gp[0]]])
        return r

    best = None
    for s in (1.0, -1.0):
        g0 = np.zeros(q)
        g0[0] = 0.0 if has_null else s * w      # null => f(0)~0; else carrier ~ median|f|
        if q >= 2:
            g0[1] = s * w / ue                   # slope so |f| spans ~median over the core
        sol = least_squares(resid, g0, loss="soft_l1", max_nfev=4000)
        if best is None or sol.cost < best.cost:
            best = sol
    gp = np.zeros(3); gp[:q] = best.x
    return gp


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
    spectral centre)? Returns (ok, sigmas_at_d, coeffs_at_d).

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
    ok = bool(np.all(np.isfinite(sig)) and
              all(s * ns <= t for s, t in zip(sig, need)))
    return ok, sig, b


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
    intensity DC trend, where a null's local bulge must not drag the curve."""
    off0 = float(np.median(np.concatenate([z[:max(len(z) // 10, 3)],
                                           z[-max(len(z) // 10, 3):]])))
    i = int(np.argmax(gaussian_filter1d(z, 5)))
    p0 = [max(z[i] - off0, 1e-6), x[i], 4.0, off0]
    sol = least_squares(lambda p: gauss(x, *p) - z, p0, loss="soft_l1", max_nfev=3000)
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
    if max(left_n, right_n) >= TRUNCDET_ALL_FRAC * n_live:   # nothing oscillates anywhere
        T.update(side="all", detected=True, msg="no fringes in the detectable region")
    elif hit_l and hit_r:
        T.update(side="both", detected=True)
    elif hit_l:
        T.update(side="left", detected=True)
    elif hit_r:
        T.update(side="right", detected=True)
    return T



def analyze(x, y, anchor=None, ref_policy=None, trust_nsig=None, trunc_threshold=None,
            ref_primary=None):
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

    # --- Contract guards ---------------------------------------------------------
    if len(x) < 16:
        return {"status": "too_few", "msg": f"only {len(x)} pts in window", "csig": None}
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return {"status": "nonfinite", "msg": "NaN/Inf in trace", "csig": None}

    t_run0 = time.perf_counter()
    try:
        # --- Truncated-arm detection (runs FIRST: it needs only the raw trace) ----
        # In its own try/except: a detector failure degrades to "no truncation known"
        # and the fit proceeds exactly as it would without this feature.
        t_tr0 = time.perf_counter()
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
        fit_lo = trunc.get("cut_left") if trunc.get("side") in ("left", "both") else None
        fit_hi = trunc.get("cut_right") if trunc.get("side") in ("right", "both") else None
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
        pU = fit_upper_envelope(xw, yw, off_bounds=anchor_bounds(anchor))
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

        # --- Hilbert transform: analytic signal -> phase & instantaneous freq -----
        dx = float(np.mean(np.diff(x)))
        analytic = hilbert(n)
        phase = np.unwrap(np.angle(analytic))
        f_inst = np.gradient(phase, dx) / (2 * np.pi)

        # --- Null candidates (zero-derivative dips of smoothed |f|) ---------------
        origin = float(np.mean(x))        # polynomial basis origin (well-conditioned)
        u = x - origin
        fs = smooth_absf(x, f_inst)
        cands = null_candidates(x, fs)    # [(prominence, x_null), ...], deepest first
        prom = cands[0][0] if cands else 0.0
        f_scale_sig = SIGNAL_LOSS_FRAC * float(np.median(half)) + 1e-9

        # --- Phase-order model selection (replaces ridge) ------------------------
        # All seeds are QUADRATIC (no TOD in the Hilbert fit): a "no-null" seed from a
        # polyfit of the smooth monotonic phase, and one "with-null" seed per candidate
        # dip (|f| V-fit anchored there). We fit every quadratic seed and keep the
        # lowest-SSE one, so a false/inaccurate null candidate simply loses.
        nonull1 = np.concatenate([np.polyfit(u, phase, 1)[::-1], np.zeros(2)])
        nonull2 = np.concatenate([np.polyfit(u, phase, 2)[::-1], np.zeros(1)])
        null_seeds = [recover_offset(u, n, fit_freq_null(u, f_inst, xn - origin), 2)
                      for _, xn in cands]

        def fit_from(seed, q):
            sq = np.zeros(4); sq[:q + 1] = seed[:q + 1]
            cq = fit_signal(u, y, mid, half, sq, q, f_scale_sig)
            sse = float(np.sum((signal_model(cq, u, mid, half) - y) ** 2))
            return dict(csig=cq, cph=sq, sse=sse)

        q2 = min((fit_from(s, 2) for s in [nonull2] + null_seeds), key=lambda t: t["sse"])
        f2 = (q2["csig"][1] + 2 * q2["csig"][2] * u) / (2 * np.pi)
        # A null in-window means TOD is unidentifiable and a free cubic would just curve
        # around the null. So if the best quadratic already has a null, CAP the order at
        # 2 (c3=0; true |c3|<=0.005 < the tolerance anyway). TOD is fit only when the
        # frequency stays one-signed (well-sampled, no null) -- BIC then admits q=3 only
        # if it earns its keep.
        if bool(np.min(f2) < 0.0 < np.max(f2)):
            order, sel = 2, q2
        else:
            cand = {1: fit_from(nonull1, 1), 2: q2, 3: fit_from(nonull2, 3)}
            order = min(cand, key=lambda q: _bic_sse(cand[q]["sse"], q + 1, len(y)))
            sel = cand[order]
        l0 = origin

        # Full-signal fit (the graded answer) and its seed (the Hilbert-domain fit).
        csig = sel["csig"]
        cph = sel["cph"]
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

        # t_run stays comparable to pre-detector runs: the detector is timed separately.
        # --- Is the answer supported by the data, and where? ----------------------
        # Primary reference = the SPECTRAL CENTRE (fitted intensity peak muU): that is
        # where the pulse energy is and what a phase-stabilization reference asks about,
        # and the un-clipped arm dominates the intensity, so muU survives the truncation
        # even when the fringes around it do not.
        # Fallback = the CORE CENTROID l0. See the REF_HYST block for why.
        cov = coef_cov(u, half, csig, order, resid_sig)
        ref_primary = float(pU[1]) if ref_primary is None else float(ref_primary)
        ok_primary, sig_primary, b_primary = trust_at(csig, cov, ref_primary - l0,
                                                      nsig=trust_nsig)
        ok_fallback, sig_fallback, b_fallback = trust_at(csig, cov, 0.0,   # d=0 at l0
                                                         nsig=trust_nsig)

        # No policy => switch immediately (standalone + harness). A policy => the app's
        # hysteresis decides, and it only gets to choose the fallback if that is in fact
        # trustworthy -- hysteresis may delay a switch, never fabricate a good verdict.
        use_fallback = (ref_policy.update(ok_primary) if ref_policy is not None
                        else not ok_primary)
        use_fallback = bool(use_fallback and ok_fallback)

        if use_fallback:
            ref_wl, trust_ok = float(l0), ok_fallback
            csig_sigma, csig_at_centre = sig_fallback, b_fallback
        else:
            ref_wl, trust_ok = ref_primary, ok_primary
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
    if not trust_ok:
        R["status"] = "underdetermined"
        worst = ["c0", "c1", "c2", "c3"][int(np.argmax(csig_sigma / np.maximum(
            [TRUST_TOL_C0, max(TRUST_REL * abs(csig_at_centre[1]), TRUST_FLOOR_C1),
             max(TRUST_REL * abs(csig_at_centre[2]), TRUST_FLOOR_C2), TRUST_TOL_C3],
            1e-12)))]
        where = "the core centroid" if use_fallback else "the spectral centre"
        alt = "" if use_fallback else " (the core-centroid fallback could not be trusted either)"
        R["msg"] = (f"phase underdetermined at {where} {ref_wl:.2f} nm ({worst} sigma="
                    f"{csig_sigma[int('c0c1c2c3'.index(worst) / 2)]:.3g}); "
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
        r2_sig=r2_sig, r2_fringe=r2_fringe, t_run=t_run,
        trunc=trunc, t_trunc=t_trunc,
        csig_sigma=csig_sigma, csig_at_centre=csig_at_centre, trust_ok=trust_ok,
        cov=cov, fit_span=(float(x[0]), float(x[-1])),
        # The reference the coefficients above are expressed at, and how it was chosen.
        # csig_at_centre is ALWAYS at ref_wl -- read ref_wl, never assume 802.
        ref_wl=ref_wl, ref_fallback=use_fallback, ref_primary=ref_primary,
        trust_primary_ok=ok_primary, trust_fallback_ok=ok_fallback,
    ))
    return R

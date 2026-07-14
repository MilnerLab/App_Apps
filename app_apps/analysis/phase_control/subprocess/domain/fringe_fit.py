#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Fringe fitter for phase stabilization — adapted from the standalone tool
# Data/20260709/spectrometer/fit_fringes.py (a pure-NumPy, self-contained fit).
#
# Adaptation for the live loop: analyze_trace() gained a `seed=` argument giving
# it two paths (the standalone tool's behaviour is preserved exactly when
# seed=None):
#   COLD (seed=None): from-scratch guess via envelope fits + STFT-ridge frequency
#                     search. Expensive/fragile; run on Start and to re-seed.
#   WARM (seed=prev): fixed-lam0 reference frame; envelope + full cubic phase are
#                     re-fit warm-started from the seed, skipping the STFT search.
#                     Fast + robust — this is the per-shot stabilization path.
# PhaseTracker drives cold vs warm and re-seeds when the fit degrades.
# =============================================================================
"""
fit_fringes.py
==============
Extract the PHASE and (quadratic) FREQUENCY of the spectral fringe pattern in a
Photon Control spectrometer trace, for locking / stabilization across traces.

Physical model
--------------
The measured spectrum is a slowly varying background modulated by an oscillation
(the fringe / mode comb) whose peaks trace a Gaussian upper envelope U(lam) and
whose troughs trace a skewed-Gaussian lower envelope L(lam):

    y(lam) = M(lam) + A(lam) * cos( phi(lam) )
    M = (U + L) / 2        (local mean)
    A = (U - L) / 2        (local half-amplitude / fringe contrast)

The instantaneous fringe frequency is a MONOTONIC quadratic in wavelength
(user-supplied prior): its vertex lies outside the band, and it may pass through
ZERO and go negative.  Fringe spacing only measures |f|, so where f crosses zero
the period diverges and the fringe visibility collapses -- that is the "null".
The phase is the integral, a cubic dominated by the quadratic term:

    f(lam) = q0 + q1*(lam-lam0) + q2*(lam-lam0)^2          [cycles / nm], monotonic
    phi(lam) = 2*pi * integral f  d lam  +  phi0
             = a0 + a1*u + a2*u^2 + a3*u^3 ,  u = lam - lam0

The single number you lock to for stabilization is  phi(lam_ref) mod 2*pi
(the fringe phase at a fixed reference wavelength).

Model:  y(lam) = M(lam) + V(lam) * A(lam) * cos(phi(lam))
  U(lam) Gaussian upper line profile, L(lam) skew-Gaussian lower line profile,
  A = (U - L)/2, M = DC level (dips to the true value at the null),
  V(lam) in [0,1] local visibility (-> 0 at the null).

Method (fast core runs in < 10 ms)
----------------------------------
1. Auto-window to the illuminated band; estimate the dark floor.
2. Fit the parametric envelopes robustly through the fringe maxima / minima,
   trimming null-depressed and noise samples (Gaussian U, skew-Gaussian L).
   Form A=(U-L)/2, the DC mean M (smoothed peak/trough midline), and the local
   visibility V = (pi/2)*<|Y-M|>/A which collapses to ~0 at the null.
3. Frequency -- two regimes, auto-selected by whether a visibility NULL exists:
     * null present (f crosses zero; sparse near the null): measure spacing between
       contrast-gated fringe maxima and sign-flip across the null.  Spacing resolves
       the small |f| near the null cleanly.
     * no null (dense monotonic fringes, only ~5-6 samples/period): maxima spacing
       UNDERCOUNTS, so track the STFT spectral ridge instead (one batched FFT).
   Either way, robustly fit a MONOTONIC quadratic f(lam) (honours the prior).
4. Phase: integrate f, find phi0 by a linear cos/sin projection, polish (a0..a3)
   with a monotonicity-constrained Levenberg-Marquardt (VARPRO amplitude).

A short-time Fourier spectrogram is used ONLY for verification (stft_spectrogram +
the bottom plot panel: fitted |f| overlaid on the true ridge), never in the fast
core.  Envelopes/frequency come from local features and the phase from the whole
band, so nothing is tuned to one trace beyond the model.  Fringe fidelity is
ultimately limited by SNR: dense low-contrast traces (fringe ~= noise) cap the
achievable fringe correlation regardless of the fit.

Usage
-----
    python fit_fringes.py [path_to_trace.xls]
Outputs a text report and <trace>_fit.png next to the data file.
"""

import sys, os, time
import numpy as np


# ----------------------------------------------------------------------------- IO
def load_trace(path):
    """Read a Photon Control .xls (really tab-separated text): Wavelength\tAmplitude."""
    w, y = [], []
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) == 2:
                try:
                    w.append(float(p[0])); y.append(float(p[1]))
                except ValueError:
                    pass
    return np.asarray(w, float), np.asarray(y, float)


# ------------------------------------------------------------------- small helpers
def _smooth(a, n):
    k = np.ones(n) / n
    out = np.convolve(a, k, mode="same")
    out[:n] = a[:n].mean(); out[-n:] = a[-n:].mean()
    return out


def _local_maxima(z):
    """Indices of strict-ish local maxima of z (vectorised)."""
    return np.where((z[1:-1] >= z[:-2]) & (z[1:-1] > z[2:]))[0] + 1


def _local_minima(z):
    return np.where((z[1:-1] <= z[:-2]) & (z[1:-1] < z[2:]))[0] + 1


def _erf(x):
    """Vectorised erf (Abramowitz & Stegun 7.1.26); no scipy dependency."""
    s = np.sign(x); x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


def _stft_ridge(x, r, dw, wprof, win=128, hop=4, fmin=0.3):
    """Track the local fringe frequency as the STFT spectral ridge.

    A short, batched Hann-windowed FFT (one vectorised rfft over all windows)
    yields, per window, the dominant *positive* frequency |f| via a parabolic
    sub-bin peak interpolation.  This is robust where naive peak-spacing fails --
    dense fringes (only ~5-6 samples / period) that simple maxima detection
    undercounts.  Windows are weighted by peak height x local fringe contrast so
    the subsequent quadratic fit follows the bright ridge and ignores harmonics,
    edge junk and the low-contrast null.  Returns (mid_nm_offset, fabs, weight).
    """
    n = r.size
    win = int(min(win, max(32, (n // 3) | 1)))
    if n < win + hop:
        return (np.empty(0), np.empty(0), np.empty(0))
    starts = np.arange(0, n - win + 1, hop)
    idx = starts[:, None] + np.arange(win)[None, :]
    seg = r[idx]
    seg = (seg - seg.mean(axis=1, keepdims=True)) * np.hanning(win)[None, :]
    S = np.abs(np.fft.rfft(seg, axis=1))                 # (nwin, nfreq), one FFT call
    freqs = np.fft.rfftfreq(win, d=dw)
    df = freqs[1] - freqs[0]
    j0 = max(1, int(np.searchsorted(freqs, fmin)))
    jj = np.argmax(S[:, j0:], axis=1) + j0
    rows = np.arange(jj.size)
    jm1 = jj - 1; jp1 = np.minimum(jj + 1, freqs.size - 1)
    a = S[rows, jm1]; b = S[rows, jj]; c = S[rows, jp1]
    d = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)          # parabolic sub-bin offset
    fabs = freqs[jj] + np.clip(d, -1.0, 1.0) * df
    cen = starts + win // 2
    wt = b * (wprof[cen] / (wprof.max() + 1e-12))        # peak height x local contrast
    return x[cen], fabs, wt, cen


def stft_spectrogram(x, r, dw, win=161, hop=6):
    """Short-time Fourier magnitude spectrogram of the fringe residual r(lam).

    VERIFICATION / diagnostic only -- NOT used by the fast phase extraction (which
    must run < 10 ms).  Returns (centres_nm_offset, |S| [freq x time], freqs_cyc/nm)
    so the fitted frequency can be overlaid on the true local frequency content:
    a single bright ridge => a clean chirp the model can track; a ridge that dips to
    zero => a visibility null; two ridges => a beat the single-cosine model cannot fit.
    """
    n = r.size
    win = int(min(win, max(16, (n // 3) | 1)))
    ham = np.hanning(win)
    freqs = np.fft.rfftfreq(win, d=dw)
    starts = np.arange(0, n - win + 1, hop)
    idx = starts[:, None] + np.arange(win)[None, :]
    seg = (r[idx] - r[idx].mean(axis=1, keepdims=True)) * ham[None, :]
    S = np.abs(np.fft.rfft(seg, axis=1)).T
    return x[starts + win // 2], S, freqs


# --------------------------------------------------------------- FAST core routine
def analyze_trace(w, y, lam_ref=802.0, seed=None):
    """
    Extract fringe phase and quadratic frequency.  Runs in < 10 ms.

    lam_ref : FIXED reference wavelength (nm) at which the stabilization phase is
              reported.  Keep it constant across traces so phase_ref is directly
              comparable trace-to-trace (do NOT tie it to a per-trace feature such
              as the moving band peak).  Choose a wavelength inside the high-
              contrast band; 802 nm suits this laser.

    seed    : optional prior result dict (the ``_warm`` payload of an earlier
              analyze_trace).  When given, this runs the WARM path used by the live
              stabilization loop: the (expensive, fragile) sliding-window FFT
              frequency search and null detection are SKIPPED; instead the band
              centre lam0, the envelope shapes and the cubic phase are all
              warm-started from the seed and merely re-fit (envelopes + full cubic
              phase) against this trace.  lam0 is held FIXED to the seed's value so
              the phase reference frame — and hence phase_ref — stays comparable
              across traces.  When None (the default) the COLD path runs: a from-
              scratch guess via envelope fits + STFT ridge, identical to the
              standalone tool.  Seed with the previous GOOD result each shot and
              re-seed cold (seed=None) whenever the fit degrades.

    Model:  y(lam) = M(lam) + V(lam) * A(lam) * cos(phi(lam))
      U (Gaussian) and L (skew-Gaussian) are the parametric upper/lower line
      profiles; A=(U-L)/2 is the line-profile amplitude; M is the DC level (dips to
      the true value at the null); V in [0,1] is the local visibility (-> 0 at the
      null).  phi is the cubic whose derivative is the monotonic quadratic frequency.

    Returns a dict with:
      lam0         : centring wavelength (band peak)             [nm]
      freq_coef    : (q0,q1,q2), f(lam)=q0+q1*(lam-lam0)+q2*(lam-lam0)^2 [cyc/nm]
      freq_zero_nm : wavelength where f crosses zero (the null)  [nm]
      null_nm      : measured visibility null used for signing   [nm]
      freq_band_edges, monotonic
      phase_coef   : (a0,a1,a2,a3), phi(lam)=sum a_k*(lam-lam0)^k [rad]
      lam_ref, phase_ref  : phi(lam_ref) mod 2*pi -- stabilization observable [rad]
      visibility   : coherence R = fitted amp / line-profile amp (~1 = good)
      band         : (lam_lo, lam_hi) trustworthy wavelength range
      upper_gauss, lower_skew : envelope parameters
      quality      : {'fringe_corr','rms','n_peaks'}
      _arrays      : intermediate arrays for plotting
    """
    # 1) window to illuminated band + dark floor
    sm = _smooth(y, 51)
    nz = y[y > 0]
    floor = float(np.median(nz[:200])) if nz.size else float(np.min(y))
    pkv = sm.max()
    idx = np.where(sm > floor + 0.08 * (pkv - floor))[0]
    lo, hi = int(idx[0]), int(idx[-1])
    W = w[lo:hi + 1]; Y = y[lo:hi + 1]
    N = W.size
    warm = seed is not None
    # WARM: hold the band centre FIXED to the seed so u = lam - lam0 (and every phase
    # coefficient expressed in it) transfers directly trace-to-trace; COLD: the band peak.
    lam0 = float(seed["lam0"]) if warm else float(W[np.argmax(sm[lo:hi + 1])])
    x = W - lam0

    # 2) PARAMETRIC envelopes (line profile): Gaussian upper U, skew-Gaussian lower L,
    #    fit robustly through the fringe maxima / minima (trimming null & noise points).
    #    WARM: warm-start each envelope from the seed's parameters so the robust fit
    #    converges in a couple of iterations and can't wander to a wrong shape.
    ys = np.convolve(Y, np.array([1., 2, 3, 2, 1]) / 9.0, mode="same")  # de-quantise a touch
    M0 = _smooth(Y, 31)
    pk_all = _local_maxima(ys); tr_all = _local_minima(ys)
    gu, keep_u = _fit_upper(x[pk_all], Y[pk_all].astype(float), floor,
                            init=seed["gu"] if warm else None)
    sl, keep_l = _fit_lower(x[tr_all], Y[tr_all].astype(float), floor, abs(gu[3]),
                            init=seed["sl"] if warm else None)
    U = _gauss(gu, x); L = _skewgauss(sl, x)
    Aprof = np.maximum(0.5 * (U - L), 1e-6)        # smooth line-profile half-amplitude

    # 2b) DC mean M = actual local level (dips to the true value at the null, unlike
    #     (U+L)/2), from a lightly smoothed peak/trough midline.
    Ehi = np.interp(x, x[pk_all], ys[pk_all])
    Elo = np.interp(x, x[tr_all], ys[tr_all])
    Mmid = _smooth(0.5 * (Ehi + Elo), 9)

    # 2c) local VISIBILITY V(lam) = actual fringe amplitude / line-profile amplitude.
    #     <|cos|> = 2/pi, so amplitude ~ (pi/2)*<|Y - M|>.  V -> 0 at the null.
    r = Y - Mmid
    absr = np.convolve(np.abs(r), np.ones(15) / 15.0, mode="same")
    V = np.convolve(np.clip((np.pi / 2.0) * absr / Aprof, 0.0, 1.2),
                    np.ones(9) / 9.0, mode="same")
    Aeff = V * Aprof                               # effective fringe amplitude
    Amax = Aeff.max()

    # 2d) locate the frequency ZERO (visibility null): interior minimum of V flanked
    #     by high-visibility lobes.  Spacing measures only |f|; the signed monotonic
    #     frequency changes sign across the null (period -> infinity there).
    lam_z = None
    best = np.inf
    for i in _local_minima(V):
        if V[i] >= 0.5 or i <= 2 or i >= N - 3:
            continue
        if V[:i].max() > 0.6 and V[i + 1:].max() > 0.6 and V[i] < best:
            best = V[i]; lam_z = float(W[i])

    # 3) MONOTONIC quadratic frequency f(lam).  Two regimes need different estimators:
    #    - a trace WITH a visibility null (f crosses zero): near the null the fringes
    #      are sparse and |f| is small, which contrast-gated maxima SPACING measures
    #      cleanly.  (The STFT ridge cannot resolve below ~1 cyc/nm and would distort
    #      the through-zero fit.)  Use spacings, sign-flipped across the null.
    #    - a trace with NO null (dense monotonic fringes, ~5-6 samples/period): maxima
    #      spacing UNDERCOUNTS badly; the STFT spectral ridge tracks |f| faithfully.
    c = np.clip(r / Aeff, -1.5, 1.5)
    band = Aeff > 0.30 * Amax
    Wt = (Aeff / Amax)
    Wb = Wt * band
    if warm:
        # WARM: skip the STFT/maxima frequency search entirely — warm-start the cubic
        # phase (whose derivative IS the quadratic frequency) straight from the seed and
        # let the LM below re-fit a0..a3 against this trace.
        th = np.array(seed["phase_coef"], dtype=float)
        n_peaks = -1                              # not measured on the warm path
    else:
        dw = float(np.median(np.diff(W)))
        keep = (ys[pk_all] - M0[pk_all] > 0.35 * Aprof[pk_all]) & (Aeff[pk_all] > 0.25 * Amax)
        pk = pk_all[keep]
        if lam_z is not None:
            # --- maxima-spacing frequency (null / sparse regime) ---
            xp = x[pk]; prom = ys[pk] - M0[pk]
            sp = np.diff(xp); mid = 0.5 * (xp[:-1] + xp[1:])
            good = sp < 1.8 * np.median(sp)           # drop spacings that jump the null
            ff = 1.0 / sp
            ww = np.minimum(prom[:-1], prom[1:])      # weight by fringe strength
            mm, ff, ww = mid[good], ff[good], ww[good]
            ff = ff * np.where(mm + lam0 < lam_z, 1.0, -1.0)   # signed: + blue, - red
        else:
            # --- STFT spectral-ridge frequency (dense regime) ---
            mid_r, fabs_r, wt_r, cen_r = _stft_ridge(x, r, dw, Aeff)
            gate = (Aeff[cen_r] > 0.30 * Amax) & (fabs_r < 0.9 * (0.5 / dw))
            mm, ff, ww = mid_r[gate], fabs_r[gate], wt_r[gate]

        def _robust_polyfit(cols):
            Vf = np.column_stack(cols)
            s = np.linalg.lstsq(Vf * ww[:, None], ff * ww, rcond=None)[0]
            for _ in range(2):                        # robust outlier rejection
                rr = ff - Vf @ s
                ok = np.abs(rr) < 2.0 * (np.std(rr) + 1e-9)
                s = np.linalg.lstsq((Vf * ww[:, None])[ok], (ff * ww)[ok], rcond=None)[0]
            return s
        one = np.ones_like(mm)
        n_peaks = int(pk.size)
        q0, q1, q2 = _robust_polyfit([one, mm, mm * mm])
        # honour the MONOTONIC prior: if f'(u)=q1+2 q2 u changes sign on the band the
        # quadratic has turned over (noisy low-SNR ridge fit).  Re-fit a quadratic with
        # its vertex CLAMPED to the nearer band edge -- monotonic on the band by
        # construction, yet keeps quadratic curvature (better than dropping to a line).
        xlo, xhi = float(x.min()), float(x.max())
        if q2 != 0.0 and (q1 + 2 * q2 * xlo) * (q1 + 2 * q2 * xhi) < 0:
            u_vertex = -q1 / (2 * q2)
            ue = xlo if abs(u_vertex - xlo) < abs(u_vertex - xhi) else xhi
            q0, q2 = _robust_polyfit([one, mm * mm - 2 * ue * mm])   # vertex fixed at ue
            q1 = -2 * q2 * ue

        # 4) phase: integrate f to the phase shape, seed the cubic, polish with LM.
        #   cubic-phase seed:  phi = a0 + a1 u + a2 u^2 + a3 u^3   (separable in amplitude).
        #   phi0 from a linear cos/sin projection of the normalised signal at the seed.
        fr = q0 + q1 * x + q2 * x * x
        Phi = np.concatenate([[0.0], np.cumsum(0.5 * (fr[1:] + fr[:-1]) * np.diff(x))]) * 2 * np.pi
        Bm = np.vstack([np.cos(Phi), np.sin(Phi)]).T * Wb[:, None]
        P, Qc = np.linalg.lstsq(Bm, c * Wb, rcond=None)[0]
        phi0 = np.arctan2(-Qc, P)
        th = np.array([phi0, 2 * np.pi * q0, np.pi * q1, (2 * np.pi / 3.0) * q2])
    x2 = x * x
    x3 = x2 * x
    W2 = Wb * Wb
    cW = W2 * c          # weighted target (Wb^2) for consistent weighted LS

    def _resid(t):
        # amplitude projection via closed-form 2x2 normal equations (weighted)
        ph = t[0] + t[1] * x + t[2] * x2 + t[3] * x3
        cc, ss = np.cos(ph), np.sin(ph)
        scc = W2 * cc
        Scc = scc @ cc; Scs = scc @ ss; Sss = (W2 * ss) @ ss
        bc = cc @ cW; bs = ss @ cW
        det = Scc * Sss - Scs * Scs
        if abs(det) < 1e-12:
            Pp = Qp = 0.0
        else:
            Pp = (Sss * bc - Scs * bs) / det
            Qp = (Scc * bs - Scs * bc) / det
        e = (c - (Pp * cc + Qp * ss)) * Wb
        return 0.5 * float(e @ e), Pp, Qp, cc, ss, e

    # frequency f(u) = (a1 + 2 a2 u + 3 a3 u^2)/2pi must stay MONOTONIC on the band:
    # its derivative 2 a2 + 6 a3 u may not change sign over [u_lo, u_hi].
    u_lo, u_hi = float(x[band].min()), float(x[band].max())

    def _monotonic(t):
        d_lo = 2 * t[2] + 6 * t[3] * u_lo
        d_hi = 2 * t[2] + 6 * t[3] * u_hi
        return d_lo * d_hi >= 0.0

    # Levenberg-Marquardt with step acceptance; reject steps that break monotonicity.
    lam = 1e-3
    f0, Pp, Qp, cc, ss, e = _resid(th)
    for _ in range(20):
        d = (-Pp * ss + Qp * cc) * Wb
        J = np.vstack([d, d * x, d * x2, d * x3]).T
        A = J.T @ J
        g = J.T @ e
        improved = False
        while True:
            dth = np.linalg.solve(A + lam * (np.diag(np.diag(A)) + 1e-9 * np.eye(4)), g)
            trial = _resid(th + dth)
            if trial[0] < f0 and _monotonic(th + dth):
                th = th + dth
                improved = True
                gain = (f0 - trial[0]) / (f0 + 1e-12)
                f0, Pp, Qp, cc, ss, e = trial
                lam = max(lam * 0.5, 1e-7)
                break
            lam *= 3.0
            if lam > 1e6:
                break
        if lam > 1e6 or (improved and gain < 1e-6):
            break

    # fold the projection offset delta into the phase:
    #   P cos(ph) + Q sin(ph) = R cos(ph - delta)
    R = float(np.hypot(Pp, Qp))
    delta = float(np.arctan2(Qp, Pp))
    a0, a1, a2, a3 = th[0] - delta, th[1], th[2], th[3]
    vis = R

    # frequency implied by the polished phase (report this one)
    def freq(u):
        return (a1 + 2 * a2 * u + 3 * a3 * u * u) / (2 * np.pi)
    q0f, q1f, q2f = a1 / (2 * np.pi), a2 / np.pi, 3 * a3 / (2 * np.pi)

    # reference phase for stabilization (fixed lam_ref -> comparable across traces)
    ur = lam_ref - lam0
    phase_ref = (a0 + a1 * ur + a2 * ur**2 + a3 * ur**3) % (2 * np.pi)

    # quality on the trustworthy band
    ph = a0 + a1 * x + a2 * x**2 + a3 * x**3
    fringe = vis * Aeff * np.cos(ph)
    model = Mmid + fringe
    bi = np.where(band)[0]
    fr_corr = float(np.corrcoef((Y - Mmid)[band], fringe[band])[0, 1])
    rms = float(np.sqrt(np.mean((Y[band] - model[band]) ** 2)))

    # frequency zero-crossing(s) of the (monotonic) quadratic, if within the band
    zero_nm = np.nan
    roots = np.roots([q2f, q1f, q0f]) if q2f != 0 else (
        np.array([-q0f / q1f]) if q1f != 0 else np.array([]))
    for rt in np.atleast_1d(roots):
        if np.isreal(rt) and W[0] - lam0 <= rt.real <= W[-1] - lam0:
            zero_nm = float(lam0 + rt.real)
    f_lo = float(freq(W[bi[0]] - lam0)); f_hi = float(freq(W[bi[-1]] - lam0))
    return {
        "lam0": lam0,
        "freq_coef": (q0f, q1f, q2f),
        "freq_zero_nm": zero_nm,          # wavelength where f crosses 0 (the null)
        "null_nm": lam_z,                 # measured visibility null used for signing
        "freq_band_edges": (f_lo, f_hi),  # signed f at blue / red band edges [cyc/nm]
        "monotonic": bool(_monotonic(th)),
        "phase_coef": (a0, a1, a2, a3),
        "lam_ref": float(lam_ref),
        "phase_ref": float(phase_ref),
        "visibility": vis,                # coherence R = fit amp / envelope amp (~1)
        "band": (float(W[bi[0]]), float(W[bi[-1]])),
        "floor": floor,
        "upper_gauss": {"baseline": float(gu[0]), "amp": float(gu[1]),
                        "center_nm": lam0 + float(gu[2]), "sigma_nm": abs(float(gu[3])),
                        "fwhm_nm": 2.3548 * abs(float(gu[3]))},
        "lower_skew": {"baseline": float(sl[0]), "amp": float(sl[1]),
                       "center_nm": lam0 + float(sl[2]), "sigma_nm": abs(float(sl[3])),
                       "alpha": float(sl[4])},
        "quality": {"fringe_corr": fr_corr, "rms": rms, "n_peaks": n_peaks},
        # WARM payload: everything analyze_trace(..., seed=THIS) needs to warm-start the
        # next trace (fixed lam0 frame, envelope shapes, cubic phase). Pass the previous
        # GOOD result's "_warm" back in as `seed` to run the fast per-shot path.
        "_warm": {
            "lam0": float(lam0),
            "gu": np.asarray(gu, float).copy(),
            "sl": np.asarray(sl, float).copy(),
            "phase_coef": (float(a0), float(a1), float(a2), float(a3)),
            "freq_coef": (float(q0f), float(q1f), float(q2f)),
        },
        "_arrays": {"W": W, "Y": Y, "x": x, "U": U, "L": L, "Aprof": Aprof,
                    "Mmid": Mmid, "V": V, "Aeff": Aeff, "model": model, "band": band,
                    "pk_all": pk_all, "tr_all": tr_all, "keep_u": keep_u,
                    "keep_l": keep_l, "freq": freq},
    }


def display_curve(res, n=300):
    """Model components for the on-screen overlay, downsampled to n points over the band.

    Returns (wavelengths_nm, baseline_M, amplitude, phase) as plain lists.  The fitted
    fringe model at the tracked phase is  M + amplitude*cos(phase); shift `phase` by a
    constant (set_phase - phase_ref) to draw the same fit at any target phase.  This lets
    the UI reconstruct both the current-phase and set-phase curves from one payload — no
    stale legacy guess, and the set-phase line stays responsive without a new spectrum.
    """
    a = res["_arrays"]
    W = a["W"]; M = a["Mmid"]; x = a["x"]
    a0, a1, a2, a3 = res["phase_coef"]
    ph = a0 + a1 * x + a2 * x ** 2 + a3 * x ** 3
    amp = float(res["visibility"]) * a["Aeff"]
    if W.size > n:
        idx = np.linspace(0, W.size - 1, n).round().astype(int)
        W, M, amp, ph = W[idx], M[idx], amp[idx], ph[idx]
    return W.tolist(), M.tolist(), amp.tolist(), ph.tolist()


# ---------------------------------------------------- envelope fits (diagnostic)
# Both envelopes are POSITIVE bumps (a line profile) on the dark floor: the upper
# traces the fringe maxima (Gaussian), the lower traces the fringe minima (skewed
# Gaussian).  Near a visibility null the fringes vanish, so maxima are depressed
# toward the mean and minima are elevated toward it -- those samples, plus noise in
# the wings, are OUTLIERS.  We therefore fit robustly, iteratively trimming samples
# that fall on the "wrong" side of the envelope (below the upper / above the lower).
def _gauss(p, xx):
    b, A, m, s = p
    return b + A * np.exp(-(xx - m) ** 2 / (2.0 * s * s))


def _skewgauss(p, xx):
    b, A, m, s, al = p
    t = (xx - m) / s
    return b + A * np.exp(-t * t / 2.0) * (1.0 + _erf(al * t / np.sqrt(2.0)))


def _fit_upper(xs, ys, floor, init=None):
    """Robust Gaussian through fringe maxima (trims null-depressed / noise points).

    init : optional (b, A, m, s) warm-start (previous trace's fit); falls back to a
    data-driven cold seed when None."""
    if init is not None:
        p = np.asarray(init, float).copy()
    else:
        m = xs[np.argmax(ys)]; A = ys.max() - floor
        p = np.array([floor, A, m, 2.5])
    keep = np.ones(xs.size, bool)
    for _ in range(4):
        xk, yk = xs[keep], ys[keep]
        for _ in range(8):                        # Gauss-Newton on kept samples
            b, A, m, s = p
            e = np.exp(-(xk - m) ** 2 / (2 * s * s))
            r = yk - (b + A * e)
            J = np.vstack([np.ones_like(xk), e, A * e * (xk - m) / s**2,
                           A * e * (xk - m) ** 2 / s**3]).T
            dp = np.linalg.lstsq(J, r, rcond=None)[0]
            p = p + dp
            if np.linalg.norm(dp) < 1e-6:
                break
        res = ys - _gauss(p, xs); sd = np.std(res[keep]) + 1e-9
        nk = ((res > -1.3 * sd) & (np.abs(res) < 3 * sd)) | (res > 0)  # keep on/above
        if np.array_equal(nk, keep):
            break
        keep = nk
    return p, keep


_SQ2 = np.sqrt(2.0)
_TWO_SQPI = 2.0 / np.sqrt(np.pi)


def _skew_jac(p, xx):
    """Skew-Gaussian value and analytic Jacobian (b, A, m, s, alpha)."""
    b, A, m, s, al = p
    t = (xx - m) / s
    E = np.exp(-t * t / 2.0)
    z = al * t / _SQ2
    ez = np.exp(-z * z)
    F = 1.0 + _erf(z)
    g = b + A * E * F
    dm = A * ((t * E / s) * F - E * _TWO_SQPI * ez * al / (_SQ2 * s))
    ds = A * ((t * t * E / s) * F - E * _TWO_SQPI * ez * al * t / (_SQ2 * s))
    dal = A * E * _TWO_SQPI * ez * t / _SQ2
    return g, np.vstack([np.ones_like(xx), E * F, dm, ds, dal]).T


def _fit_lower(xs, ys, floor, sig, init=None):
    """Robust POSITIVE-bump skewed Gaussian through fringe minima (trims elevated).

    Uses an analytic Jacobian so it stays inside the runtime budget.  init : optional
    (b, A, m, s, alpha) warm-start (previous trace's fit); cold seed when None."""
    A = max(ys.max() - floor, 1.0)
    if init is not None:
        p = np.asarray(init, float).copy()
    else:
        m = xs[np.argmax(ys)]
        p = np.array([floor, A, m, sig * 1.2, -1.0])
    keep = np.ones(xs.size, bool)
    lo = np.array([floor - 4, 0.3, xs.min() - 3, 0.5, -8.0])
    hi = np.array([floor + 4, 3 * A + 5, xs.max() + 3, 25.0, 8.0])
    for _ in range(3):
        xk, yk = xs[keep], ys[keep]
        lm = 1e-2; g, _ = _skew_jac(p, xk); c0 = np.sum((yk - g) ** 2)
        for _ in range(12):                       # Levenberg-Marquardt on kept samples
            g, J = _skew_jac(p, xk); r = yk - g
            AA = J.T @ J
            while True:
                st = np.linalg.solve(AA + lm * (np.diag(np.diag(AA)) + 1e-9 * np.eye(5)), J.T @ r)
                pn = np.clip(p + st, lo, hi)
                gg, _ = _skew_jac(pn, xk)
                c = np.sum((yk - gg) ** 2)
                if c < c0:
                    p, c0 = pn, c; lm = max(lm * 0.5, 1e-6); break
                lm *= 3.0
                if lm > 1e6:
                    break
            if lm > 1e6 or np.linalg.norm(st) < 1e-7:
                break
        res = ys - _skewgauss(p, xs); sd = np.std(res[keep]) + 1e-9
        nk = ((res < 1.3 * sd) & (np.abs(res) < 3 * sd)) | (res < 0)  # keep on/below
        if np.array_equal(nk, keep):
            break
        keep = nk
    return p, keep


# ------------------------------------------------------------------------- report
def format_report(res):
    q0, q1, q2 = res["freq_coef"]
    a = res["phase_coef"]
    lam0 = res["lam0"]
    L = []
    L.append("=" * 68)
    L.append("FRINGE PHASE / FREQUENCY EXTRACTION")
    L.append("=" * 68)
    L.append(f"centre wavelength lam0        : {lam0:.4f} nm")
    L.append(f"trustworthy band             : {res['band'][0]:.2f} - {res['band'][1]:.2f} nm")
    L.append("")
    L.append("STABILIZATION PHASE (lock to this):")
    L.append(f"  phi(lam_ref) mod 2pi       : {res['phase_ref']:.4f} rad"
             f"  ({np.degrees(res['phase_ref']):.2f} deg)")
    L.append(f"  at lam_ref                 : {res['lam_ref']:.4f} nm")
    L.append(f"  coherence (fit amp / env)  : {res['visibility']:.3f}  (~1 = good)")
    L.append("")
    L.append("FREQUENCY  f(lam) = q0 + q1*(lam-lam0) + q2*(lam-lam0)^2   [cycles/nm]")
    L.append("  (signed & monotonic across the band; sign convention: + blue, - red)")
    L.append(f"  q0 = {q0:.6f}   q1 = {q1:.6f}   q2 = {q2:.6f}")
    L.append(f"  monotonic on band          : {res['monotonic']}")
    fe = res["freq_band_edges"]
    L.append(f"  signed f at band edges     : {fe[0]:+.3f} (blue) .. {fe[1]:+.3f} (red) cyc/nm")
    L.append(f"  |fringe period| band edges : {1/abs(fe[0]):.3f} .. {1/abs(fe[1]):.3f} nm")
    if res["null_nm"] is not None:
        L.append(f"  visibility NULL (f=0)      : {res['null_nm']:.3f} nm measured"
                 + (f", fit zero {res['freq_zero_nm']:.3f} nm"
                    if np.isfinite(res["freq_zero_nm"]) else ""))
    elif np.isfinite(res["freq_zero_nm"]):
        L.append(f"  f=0 crossing (extrapolated): {res['freq_zero_nm']:.3f} nm"
                 "  (no measured null; fringes stay resolved across the band)")
    L.append("")
    L.append("PHASE  phi(lam) = a0 + a1*u + a2*u^2 + a3*u^3 ,  u = lam-lam0   [rad]")
    L.append(f"  a0 = {a[0]:.5f}   a1 = {a[1]:.5f}   a2 = {a[2]:.5f}   a3 = {a[3]:.6f}")
    u = res["upper_gauss"]; lw = res["lower_skew"]
    L.append("")
    L.append("ENVELOPES (parametric; drive the model via M=(U+L)/2 dip-corrected & V):")
    L.append(f"  upper (Gaussian) : center {u['center_nm']:.3f} nm, "
             f"FWHM {u['fwhm_nm']:.3f} nm, amp {u['amp']:.2f}, base {u['baseline']:.2f}")
    L.append(f"  lower (skew-Gau) : center {lw['center_nm']:.3f} nm, "
             f"sigma {lw['sigma_nm']:.3f} nm, alpha {lw['alpha']:.2f}, base {lw['baseline']:.2f}")
    Ld = res["quality"]
    L.append("")
    L.append(f"QUALITY: fringe corr {Ld['fringe_corr']:.3f}, "
             f"RMS {Ld['rms']:.2f} counts, {Ld['n_peaks']} fringe peaks used")
    L.append("=" * 68)
    return "\n".join(L)


# --------------------------------------------------------------------------- plot
def save_plot(res, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print("(plot skipped: matplotlib unavailable: %s)" % e)
        return
    a = res["_arrays"]; W = a["W"]; Y = a["Y"]
    fig, (ax1, ax3, ax2, ax4) = plt.subplots(4, 1, figsize=(11, 10), sharex=True,
                                             gridspec_kw={"height_ratios": [3, 1, 1, 1.5]})
    ax1.plot(W, Y, lw=0.7, color="0.4", label="data")
    # parametric envelopes actually used: U (Gaussian), L (skew), DC mean M
    ax1.plot(W, a["U"], "C0--", lw=1.2, label="upper Gaussian U")
    ax1.plot(W, a["L"], "C2--", lw=1.2, label="lower skew-Gauss L")
    ax1.plot(W, a["Mmid"], color="0.5", lw=1.0, ls=":", label="DC mean M")
    # detected extrema: filled = kept for the fit, hollow = trimmed (null / noise)
    pa, ta, ku, kl = a["pk_all"], a["tr_all"], a["keep_u"], a["keep_l"]
    ax1.scatter(W[pa][ku], Y[pa][ku], s=9, c="C0", zorder=5, label="maxima kept")
    ax1.scatter(W[ta][kl], Y[ta][kl], s=9, c="C2", zorder=5, label="minima kept")
    ax1.scatter(W[pa][~ku], Y[pa][~ku], s=9, facecolors="none", edgecolors="0.6", zorder=4)
    ax1.scatter(W[ta][~kl], Y[ta][~kl], s=9, facecolors="none", edgecolors="0.6", zorder=4)
    # draw the model only where the effective amplitude is meaningful (avoid noisy tails)
    mm = a["Aeff"] > 0.12 * a["Aeff"].max()
    mod = a["model"].copy(); mod[~mm] = np.nan
    ax1.plot(W, mod, lw=1.3, color="C3", label="model M + V*A*cos phi")
    b0, b1 = res["band"]
    ax1.axvspan(b0, b1, color="C1", alpha=0.07, label="fit band")
    ax1.axvline(res["lam_ref"], color="k", ls=":", lw=1)
    ax1.set_ylabel("amplitude (counts)")
    ax1.legend(loc="upper right", fontsize=7, ncol=3)
    zt = ("  |  null %.2f nm" % res["null_nm"]) if res["null_nm"] is not None else ""
    ax1.set_title("Fringe fit  |  phase@%.3fnm = %.3f rad  |  coh %.2f  |  corr %.3f%s"
                  % (res["lam_ref"], res["phase_ref"], res["visibility"],
                     res["quality"]["fringe_corr"], zt))
    # visibility panel
    ax3.plot(W, a["V"], "C5", lw=1.3)
    ax3.axhline(0, color="0.6", lw=0.6)
    ax3.set_ylabel("visibility V"); ax3.set_ylim(0, 1.15)
    ax3.axvspan(b0, b1, color="C1", alpha=0.07); ax3.grid(alpha=0.3)
    if res["null_nm"] is not None:
        ax3.axvline(res["null_nm"], color="k", ls=":", lw=1)
    # signed frequency panel
    freq = a["freq"]; x = a["x"]
    ax2.plot(W, freq(x), "C4", lw=1.3)
    ax2.axhline(0, color="0.5", lw=0.8, ls="--")
    if res["null_nm"] is not None:
        ax2.axvline(res["null_nm"], color="k", ls=":", lw=1)
    ax2.set_ylabel("signed freq (cyc/nm)")
    ax2.axvspan(b0, b1, color="C1", alpha=0.07); ax2.grid(alpha=0.3)
    # STFT verification panel: fitted |f| overlaid on the true local frequency content
    dw = float(np.median(np.diff(W)))
    cen, S, freqs = stft_spectrogram(x, Y - a["Mmid"], dw)
    ax4.imshow(S, aspect="auto", origin="lower", cmap="magma",
               extent=[W[0] + (cen[0] - x[0]), W[0] + (cen[-1] - x[0]), freqs[0], freqs[-1]])
    ax4.plot(W, np.abs(freq(x)), "c-", lw=1.6, label="fitted |f|")
    ax4.set_ylim(0, min(freqs[-1], max(1.0, 1.3 * np.abs(freq(x[a["band"]])).max())))
    ax4.set_ylabel("STFT |f| (cyc/nm)"); ax4.set_xlabel("wavelength (nm)")
    ax4.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print("saved plot ->", path)


# --------------------------------------------------------------------------- main
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "da17_1GA_-75.xls")
    path = sys.argv[1] if len(sys.argv) > 1 else default
    w, y = load_trace(path)

    # timed core (the per-trace stabilization measurement).
    # Report the WARM steady-state time (what a running lock loop sees); the very
    # first call also pays a one-off NumPy warm-up cost.
    res = analyze_trace(w, y)            # warm-up
    ts = []
    for _ in range(15):
        t0 = time.perf_counter()
        res = analyze_trace(w, y)
        ts.append((time.perf_counter() - t0) * 1e3)
    dt = float(np.median(ts))

    print(format_report(res))
    print("core runtime: %.2f ms warm (min %.2f)  (budget 10 ms)"
          % (dt, min(ts)))

    out_png = os.path.splitext(path)[0] + "_fit.png"
    save_plot(res, out_png)


if __name__ == "__main__":
    main()

"""Fit the four measurements of one centrifuge characterisation and plot them together.

1. XCORR of one chirped arm (DA only)  -> Gaussian -> FWHM in mm and ps.
2. XCORR of the centrifuge             -> ``xcorr.fringe_fit.fit_fringe`` -> beat frequency
   f(t) at the two FWHM times mu +- FWHM_1/2 (mu = the centrifuge envelope's own centre).
3. Spectrum of one arm (DA only)       -> Gaussian -> FWHM in nm.
4. Spectrum of the centrifuge          -> the phase-stabilization fit (``analyze_trace``, the
   same cold fit ``stabilization_tracker._build_template`` runs) -> f_cfg at the FWHM edges.

Then the usCFG reconstruction (thesis §2.3.2, Tab. 2.1, Eqs. 2.64-2.82): T_FWHM of the delay
arm (1) + spectral width (3) + spectral phase Delta_t, Delta_phi'' (4) -> beta_R, beta_L ->
centrifuge FWHM window and f_+-. Compared against (2), whose fringes oscillate at 2*f_cfg.

XCORR files: tab-separated, no header, col0 = stage (mm), the rest = repeats at that point.
Rows may be ragged (an aborted scan leaves a partial last row). Spectrum files: CSV with a
``wavelength_nm,intensity`` header.

Usage:
    python tools/spectrum_analyzer_test.py [--xcorr-pulse F] [--xcorr-cfg F]
        [--spec-arm F] [--spec-cfg F] [--window-nm LO HI] [--zero-mm X] [--window-ps W] [--save PNG]
"""
from __future__ import annotations

import argparse
import sys
from unittest import mock
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc  # noqa: E402
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (  # noqa: E402
    FitTunables, analyze_trace, display_curve,
)
from app_apps.analysis.xcorr import fringe_fit as ff  # noqa: E402
from app_apps.analysis.xcorr.frequency import C_MM_PER_PS, probe_mm_to_ps  # noqa: E402

DATA = Path("/mnt/valeryshare/Droplets/20261002")
XCORR_PULSE = DATA / "XCORR" / "20261002202_DA_GA=8_DA=17p77_Scan4.csv"
XCORR_CFG = DATA / "XCORR" / "202610021156AM_CFG_GA=8_DA=17p77.csv"
SPEC_ARM = DATA / "spectrum_3_DA.csv"
SPEC_CFG = DATA / "spectrum_2.csv"

FWHM_PER_SIGMA = fc.FWHM_PER_SIGMA
#: Speed of light, nm/ps.
C_NM_PER_PS = 299792.458
#: The xcorr SFG signal oscillates at twice f_cfg -- two x-aligned field positions per
#: rotation period (thesis §2.3.2).
XCORR_FRINGES_PER_CFG = 2.0


# --- loading ------------------------------------------------------------------

def load_xcorr(path):
    """(x_mm, y_mean, y_sem, repeats) sorted by x. Ragged rows are averaged over what they have."""
    rows = [[float(v) for v in line.replace(",", " ").split()]
            for line in Path(path).read_text().splitlines() if line.strip()]
    rows.sort(key=lambda r: r[0])
    x = np.array([r[0] for r in rows])
    reps = [np.asarray(r[1:]) for r in rows]
    y = np.array([r.mean() for r in reps])
    sem = np.array([r.std(ddof=1) / np.sqrt(r.size) if r.size > 1 else np.nan for r in reps])
    return x, y, sem, reps


def load_spectrum(path):
    d = np.genfromtxt(path, delimiter=",", skip_header=1)
    d = d[np.argsort(d[:, 0])]
    return d[:, 0], d[:, 1]


# --- Gaussian -----------------------------------------------------------------

def fit_gauss(x, y, yerr=None):
    """Gaussian + offset. Returns (p, perr, r2) with p = (a, mu, sigma, off)."""
    off0 = float(np.percentile(y, 5))
    a0 = float(y.max() - off0)
    mu0 = float(x[np.argmax(y)])
    above = x[y >= off0 + 0.5 * a0]
    s0 = (above[-1] - above[0]) / FWHM_PER_SIGMA if above.size > 1 else np.ptp(x) / 6
    w = None
    if yerr is not None and np.all(np.isfinite(yerr)) and np.all(yerr > 0):
        w = yerr
    p, cov = curve_fit(ff.gauss, x, y, p0=[a0, mu0, s0, off0], sigma=w, maxfev=20000)
    perr = np.sqrt(np.diag(cov))
    r2 = 1.0 - np.sum((y - ff.gauss(x, *p)) ** 2) / np.sum((y - y.mean()) ** 2)
    return p, perr, r2


# --- panels -------------------------------------------------------------------

def panel_xcorr_pulse(ax, path, zero_mm):
    x, y, sem, reps = load_xcorr(path)
    t = probe_mm_to_ps(x, zero_mm)
    p, perr, r2 = fit_gauss(t, y, sem)
    fwhm_ps, fwhm_ps_err = FWHM_PER_SIGMA * abs(p[2]), FWHM_PER_SIGMA * perr[2]
    fwhm_mm = fwhm_ps * C_MM_PER_PS / 2.0  # double pass

    print(f"[1] XCORR pulse  {Path(path).name}")
    print(f"    mu = {p[1]:.2f} ps   FWHM = {fwhm_ps:.2f} +- {fwhm_ps_err:.2f} ps"
          f"  ({fwhm_mm:.3f} mm stage)   R2 = {r2:.4f}")

    for ti, r in zip(t, reps):
        ax.plot(np.full(r.size, ti), r, ".", color="0.8", ms=2, zorder=0)
    ax.errorbar(t, y, yerr=sem, fmt="o", ms=3, color="C0", label="mean ± SEM")
    tt = np.linspace(t[0], t[-1], 2000)
    ax.plot(tt, ff.gauss(tt, *p), "C3", label="Gaussian fit")
    for e in (p[1] - fwhm_ps / 2, p[1] + fwhm_ps / 2):
        ax.axvline(e, color="C3", ls="--", lw=0.8)
    ax.set_title(f"XCORR one arm: FWHM = {fwhm_ps:.1f} ± {fwhm_ps_err:.1f} ps")
    ax.set_xlabel("delay (ps)")
    ax.set_ylabel("signal")
    ax.legend(fontsize=8)
    return fwhm_ps, fwhm_ps_err


def fit_fringe_quadratic(t, y):
    """``ff.fit_fringe`` with the phase order forced to 2 (c3 = 0).

    ``_fit_core`` refits both orders {2, 3} and keeps the lower BIC; making the cubic's BIC
    infinite for this one call leaves seeding, envelopes and covariance exactly the
    library's, and only removes the choice.
    """
    with mock.patch.object(ff, "_bic", lambda sse, k, n: 0.0 if k == 3 else np.inf):
        return ff.fit_fringe(t, y)


def xcorr_readout(fit, window_ps, recon=None):
    """f_cfg at mu -+ W/2 of one xcorr fringe fit, with its fit error and, given the
    reconstruction, the FWHM systematic against T_cfg (same side, Fig. 2.8)."""
    csig, cov, t0, mu = fit.csig, fit.cov, fit.t0_ps, fit.t_mu_ps

    def f_ghz(tp):
        """f_cfg, GHz: the fitted fringe frequency over XCORR_FRINGES_PER_CFG."""
        f_fringe = ff.fringe_freq_cyc_per_ps(csig, np.asarray(tp, float) - t0)
        return 1e3 * np.abs(f_fringe) / XCORR_FRINGES_PER_CFG

    def f_sigma(tp):
        u = float(tp) - t0
        g = 1e3 * np.array([0.0, 1.0, 2.0 * u, 3.0 * u ** 2]) / (2.0 * np.pi) / XCORR_FRINGES_PER_CFG
        var = float(g @ cov @ g)
        return np.sqrt(var) if np.isfinite(var) and var >= 0 else float("nan")

    edges = (mu - window_ps / 2, mu + window_ps / 2)
    fe = [float(f_ghz(e)) for e in edges]
    se = [f_sigma(e) for e in edges]
    i_lo, i_hi = int(np.argmin(fe)), int(np.argmax(fe))
    xc = dict(fit=fit, f_ghz=f_ghz, f_sigma=f_sigma, edges=edges, fe=fe, mu=mu, rising=fe[1] > fe[0],
              f_mu=float(f_ghz(mu)), f_min=fe[i_lo], f_max=fe[i_hi],
              s_min=se[i_lo], s_max=se[i_hi], t_min=edges[i_lo], t_max=edges[i_hi],
              window=window_ps, sys_min=float("nan"), sys_max=float("nan"),
              inside=bool(fit.t_core_ps[0] <= edges[0] and edges[1] <= fit.t_core_ps[-1]))
    if recon is not None:
        # The xcorr alone cannot know the centrifuge window T_FWHM^cfg (Eq. 2.80): whatever
        # FWHM it is read over, each edge's shift against the T_cfg edge on the same side
        # is its systematic.
        e2 = (mu - recon["T_cfg_fwhm"] / 2, mu + recon["T_cfg_fwhm"] / 2)
        f2 = [float(f_ghz(e)) for e in e2]
        sys_side = [abs(fe[k] - f2[k]) for k in (0, 1)]
        xc.update(f_min_rw=min(f2), f_max_rw=max(f2), f2=f2,
                  sys_min=sys_side[i_lo], sys_max=sys_side[i_hi])
    return xc


def print_xcorr_readout(name, fit, xc, recon):
    c = fit.csig
    s = np.sqrt(np.clip(np.diag(fit.cov), 0.0, np.inf))
    print(f"    -- {name}: order={fit.order}  r2_fringe={fit.r2_fringe:.3f}  mu = {xc['mu']:.2f} ps")
    print(f"       c2 = {c[2]:+.4e} +- {s[2]:.1e}   c3 = {c[3]:+.3e} +- {s[3]:.1e}")
    print(f"       f_cfg(mu) = {xc['f_mu']:.2f} GHz")
    print(f"       f_min = {xc['f_min']:.2f} +- {xc['s_min']:.2f} (fit)"
          + (f" +- {xc['sys_min']:.2f} (FWHM)" if recon is not None else "")
          + f" GHz  at t = {xc['t_min']:.2f} ps")
    print(f"       f_max = {xc['f_max']:.2f} +- {xc['s_max']:.2f} (fit)"
          + (f" +- {xc['sys_max']:.2f} (FWHM)" if recon is not None else "")
          + f" GHz  at t = {xc['t_max']:.2f} ps")
    if recon is not None:
        print(f"       over T_cfg = {recon['T_cfg_fwhm']:.2f} ps: {xc['f_min_rw']:.2f} -> {xc['f_max_rw']:.2f} GHz")
    if not xc["inside"]:
        print("       WARNING: the window leaves the fitted core -- the polynomial is extrapolating")


def panel_xcorr_cfg(ax, path, zero_mm, window_ps, recon=None):
    x, y, sem, _ = load_xcorr(path)
    t = probe_mm_to_ps(x, zero_mm)
    fit = ff.fit_fringe(t, y)
    fit_q = fit_fringe_quadratic(t, y)
    print(f"[2] XCORR centrifuge  {Path(path).name}   window W = {window_ps:.2f} ps")
    ax.plot(t, y, ".-", color="0.5", ms=3, lw=0.6, label="mean")
    ax.set_xlabel("delay (ps)")
    ax.set_ylabel("signal")
    if not fit.ok:
        print(f"    fit failed: {fit.status}")
        ax.set_title(f"XCORR centrifuge: fit failed ({fit.status})")
        return None

    print(f"    (fringe frequency = {XCORR_FRINGES_PER_CFG:g} * f_cfg; f_cfg quoted below)")
    xc = xcorr_readout(fit, window_ps, recon)
    xc.update(t=t, y=y, sem=sem)
    print_xcorr_readout("BIC order choice (library default)", fit, xc, recon)
    xq = None
    if fit_q.ok:
        xq = xcorr_readout(fit_q, window_ps, recon)
        print_xcorr_readout("quadratic phase forced (c3 = 0)", fit_q, xq, recon)
        xc["quad"] = xq
    else:
        print(f"    quadratic fit failed: {fit_q.status}")

    if recon is not None:
        # For contrast, the Fig. 2.8 situation: a Gaussian fitted over the WHOLE centrifuge
        # xcorr, oscillations and all, taken as the centrifuge duration.
        pg, peg, r2g = fit_gauss(t, y, sem)
        Wg = FWHM_PER_SIGMA * abs(pg[2])
        f_ghz, mu, f2 = xc["f_ghz"], xc["mu"], xc["f2"]
        fg = [float(f_ghz(mu - Wg / 2)), float(f_ghz(mu + Wg / 2))]
        sg = [abs(fg[k] - f2[k]) for k in (0, 1)]
        i_lo, i_hi = int(np.argmin(fg)), int(np.argmax(fg))
        print(f"    [Fig. 2.8 check: Gaussian over the whole cfg xcorr gives FWHM = {Wg:.1f} ps "
              f"(R2 {r2g:.2f}) -> {fg[i_lo]:.2f} +- {sg[i_lo]:.2f} (FWHM) -> "
              f"{fg[i_hi]:.2f} +- {sg[i_hi]:.2f} (FWHM) GHz, BIC fit]")

    tc = fit.t_core_ps
    up = ff.gauss(tc, *fit.p_upper)
    ax.plot(tc, fit.signal_core, "C3", lw=1, label="fringe fit")
    ax.plot(tc, up, "C2", lw=0.8, label="envelopes")
    ax.plot(tc, up - ff.gauss(tc, *fit.p_lower), "C2", lw=0.8)
    for e in xc["edges"]:
        ax.axvline(e, color="k", ls="--", lw=0.8)
    ax.legend(fontsize=8, loc="upper left")

    ax2 = ax.twinx()
    ax2.plot(tc, xc["f_ghz"](tc), "C1", lw=1.2, label=f"xcorr fit (order {fit.order})")
    ax2.plot(xc["edges"], xc["fe"], "o", color="C1")
    if xq is not None:
        tq = fit_q.t_core_ps
        ax2.plot(tq, xq["f_ghz"](tq), "C0", lw=1.2, ls=":", label="xcorr fit (quadratic)")
        ax2.plot(xq["edges"], xq["fe"], "^", color="C0")
    if recon is not None:
        # Eq. (2.50) with linear chirps: f_cfg(t) = f_cfg(t_c) + (beta_R - beta_L)/(4 pi) (t - t_c),
        # t_c placed on the xcorr envelope centre. Neither the sign of Theta nor the direction
        # of the probe-delay axis is measured, so the time direction is taken from the xcorr.
        mu = xc["mu"]
        slope = abs(recon["slope"]) * (1.0 if xc["rising"] else -1.0)
        f_rec = recon["f_c"] + slope * (tc - mu)
        ax2.plot(tc, np.abs(f_rec), "C4", lw=1.2, ls="--", label="spectral reconstruction")
        ax2.plot([mu - recon["T_cfg_fwhm"] / 2, mu + recon["T_cfg_fwhm"] / 2],
                 [recon["f_lo"], recon["f_hi"]] if slope > 0 else [recon["f_hi"], recon["f_lo"]],
                 "s", color="C4")
    ax2.legend(fontsize=8, loc="lower right")
    ax2.set_ylabel("f_cfg (GHz)", color="C1")

    def line(label, d):
        if recon is None:
            return f"{label}: {d['f_min']:.2f} ± {d['s_min']:.2f} → {d['f_max']:.2f} ± {d['s_max']:.2f} GHz"
        return (f"{label}: {d['f_min']:.2f} ± {d['s_min']:.2f} ± {d['sys_min']:.2f} → "
                f"{d['f_max']:.2f} ± {d['s_max']:.2f} ± {d['sys_max']:.2f} GHz")
    title = [line(f"xcorr order {fit.order}", xc)]
    if xq is not None:
        title.append(line("xcorr quadratic", xq))
    if recon is not None:
        title.append(f"spectral reconstruction: {recon['f_lo']:.2f} ± {recon['sig']['f_lo']:.2f} → "
                     f"{recon['f_hi']:.2f} ± {recon['sig']['f_hi']:.2f} GHz")
    ax.set_title("\n".join(title), fontsize=9)
    return xc


def plot_xcorr_order_comparison(xc, recon=None):
    """Second figure: the centrifuge xcorr fitted with the BIC-chosen cubic phase vs. the
    forced quadratic -- signal, residuals, and f_cfg(t) with 1-sigma bands."""
    import matplotlib.pyplot as plt

    xq = xc.get("quad")
    fits = [(f"order {xc['fit'].order} (BIC)", xc, "C1", "-")]
    if xq is not None:
        fits.append(("quadratic (c3 = 0)", xq, "C0", ":"))
    t, y, sem = xc["t"], xc["y"], xc["sem"]

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(11, 10), sharex=True,
                                     gridspec_kw=dict(height_ratios=[3, 1.3, 3]))
    a1.errorbar(t, y, yerr=sem, fmt=".", ms=3, color="0.55", lw=0.6, label="mean ± SEM")
    for name, d, col, ls in fits:
        f = d["fit"]
        a1.plot(f.t_core_ps, f.signal_core, color=col, ls=ls, lw=1.3,
                label=f"{name}: r2_fringe = {f.r2_fringe:.3f}")
        resid = np.interp(f.t_core_ps, t, y) - f.signal_core
        a2.plot(f.t_core_ps, resid, color=col, ls=ls, lw=1.0,
                label=f"{name}: rms = {np.sqrt(np.mean(resid ** 2)):.2e}")
    a1.set_ylabel("signal")
    a1.legend(fontsize=8, loc="upper right")
    a2.axhline(0.0, color="k", lw=0.6)
    a2.set_ylabel("data − fit")
    a2.legend(fontsize=8, loc="upper right")

    for name, d, col, ls in fits:
        f = d["fit"]
        tt = np.linspace(f.t_core_ps[0], f.t_core_ps[-1], 600)
        ff_ = d["f_ghz"](tt)
        sg = np.array([d["f_sigma"](v) for v in tt])
        a3.plot(tt, ff_, color=col, ls=ls, lw=1.5, label=name)
        a3.fill_between(tt, ff_ - sg, ff_ + sg, color=col, alpha=0.2, lw=0)
        a3.errorbar(d["edges"], d["fe"], yerr=[d["f_sigma"](e) for e in d["edges"]],
                    fmt="o", color=col, ms=5, capsize=3)
    mu = xc["mu"]
    if recon is not None:
        T = recon["T_cfg_fwhm"]
        tt = np.linspace(xc["fit"].t_core_ps[0], xc["fit"].t_core_ps[-1], 600)
        slope = abs(recon["slope"]) * (1.0 if xc["rising"] else -1.0)
        a3.plot(tt, np.abs(recon["f_c"] + slope * (tt - mu)), "C4", ls="--", lw=1.3,
                label="spectral reconstruction")
        e = [mu - T / 2, mu + T / 2]
        fv = [recon["f_lo"], recon["f_hi"]] if slope > 0 else [recon["f_hi"], recon["f_lo"]]
        sv = [recon["sig"]["f_lo"], recon["sig"]["f_hi"]] if slope > 0 else \
             [recon["sig"]["f_hi"], recon["sig"]["f_lo"]]
        a3.errorbar(e, fv, yerr=sv, fmt="s", color="C4", ms=5, capsize=3)
        for v in e:
            for a in (a1, a2, a3):
                a.axvline(v, color="C4", ls="--", lw=0.7)
    for v in xc["edges"]:
        for a in (a1, a2, a3):
            a.axvline(v, color="k", ls=":", lw=0.7)
    a3.set_ylabel("f_cfg (GHz)")
    a3.set_xlabel("delay (ps)")
    a3.legend(fontsize=8, loc="upper left")
    lines = [f"{name}: {d['f_min']:.2f} ± {d['s_min']:.2f} → {d['f_max']:.2f} ± {d['s_max']:.2f} GHz"
             for name, d, _, _ in fits]
    if recon is not None:
        lines.append(f"spectral reconstruction: {recon['f_lo']:.2f} ± {recon['sig']['f_lo']:.2f} → "
                     f"{recon['f_hi']:.2f} ± {recon['sig']['f_hi']:.2f} GHz")
    a1.set_title("Centrifuge xcorr: cubic vs. quadratic phase\n" + "\n".join(lines), fontsize=9)
    a1.set_xlim(xc["fit"].t_core_ps[0] - 40, xc["fit"].t_core_ps[-1] + 40)
    fig.tight_layout()
    return fig


def panel_spec_arm(ax, path):
    wl, inten = load_spectrum(path)
    p, perr, r2 = fit_gauss(wl, inten)
    fwhm, fwhm_err = FWHM_PER_SIGMA * abs(p[2]), FWHM_PER_SIGMA * perr[2]
    print(f"[3] Spectrum one arm  {Path(path).name}")
    print(f"    lambda0 = {p[1]:.3f} nm   FWHM = {fwhm:.3f} +- {fwhm_err:.3f} nm   R2 = {r2:.4f}")

    ax.plot(wl, inten, color="0.5", lw=0.7, label="spectrum")
    ax.plot(wl, ff.gauss(wl, *p), "C3", label="Gaussian fit")
    for e in (p[1] - fwhm / 2, p[1] + fwhm / 2):
        ax.axvline(e, color="C3", ls="--", lw=0.8)
    ax.set_xlim(p[1] - 4 * fwhm, p[1] + 4 * fwhm)
    ax.set_title(f"Spectrum one arm: λ0 = {p[1]:.2f} nm, FWHM = {fwhm:.2f} ± {fwhm_err:.2f} nm")
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("counts")
    ax.legend(fontsize=8)
    return p[1], fwhm, perr[1], fwhm_err


def panel_spec_cfg(ax, path, window_nm):
    wl_full, i_full = load_spectrum(path)
    m = (wl_full >= window_nm[0]) & (wl_full <= window_nm[1])
    wl, inten = wl_full[m], i_full[m]
    anchor = fc.baseline_anchor(wl_full, i_full)
    t = FitTunables()
    r = analyze_trace(wl, inten, t, anchor=anchor)
    # analyze_trace does not hand on the 4x4 covariance of csig; fringe_core.analyze computes
    # it (coef_cov, Gauss-Newton at the solution). Re-run the identical first pass to get it
    # and only use it if it is the same fit (it is not when the truncation recovery ran).
    R = fc.analyze(wl, inten, anchor=anchor, trust_nsig=t.trust_nsig,
                   trunc_threshold=t.trunc_threshold, recover=False)
    same = R.get("csig") is not None and np.allclose(R["csig"], r.csig) and R["l0"] == r.l0
    csig_cov = np.asarray(R["cov"], float) if same else None

    print(f"[4] Spectrum centrifuge  {Path(path).name}  window {window_nm[0]:g}-{window_nm[1]:g} nm")
    ax.plot(wl_full, i_full, color="0.6", lw=0.7, label="spectrum")
    ax.set_xlim(*window_nm)
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("counts")
    if not r.accepted:
        print(f"    fit rejected: {r.status} {r.msg}")
        ax.set_title(f"Spectrum centrifuge: fit rejected ({r.status})")
        return None, None

    c = r.csig
    if csig_cov is None:
        print("    WARNING: no covariance for csig (recovery re-fit) -- spectral phase error not propagated")
    else:
        sc = np.sqrt(np.diag(csig_cov))
        print(f"    sigma(csig) = [{sc[0]:.3g}, {sc[1]:.3g}, {sc[2]:.3g}, {sc[3]:.3g}]  (about l0)")
    print(f"    status={r.status}  shape_ok={r.shape_ok}  trust_ok={r.trust_ok}"
          f"  rms_frac={r.rms_frac:.3f}  trunc={r.trunc_side}")
    print(f"    l0 = {r.l0:.3f} nm   csig = [{c[0]:.4g}, {c[1]:.4g}, {c[2]:.4g}, {c[3]:.4g}]")
    band = fc.fwhm_band_nm(r.pU)
    rng = fc.cfg_range(r.csig, r.l0, r.pU, r.cut_left)
    title = "Spectrum centrifuge"
    if band is not None:
        f_fr = [abs(float(fc.fringe_freq_cyc_per_nm(c, b - r.l0))) for b in band]
        print(f"    FWHM band = {band[0]:.3f} - {band[1]:.3f} nm  ({band[1] - band[0]:.3f} nm)")
        print(f"    fringe rate at edges: {f_fr[0]:.3f} / {f_fr[1]:.3f} cyc/nm")
        for b in band:
            ax.axvline(b, color="k", ls="--", lw=0.8)
    if rng is not None:
        hi_nm, f_hi, lo_nm, f_lo, signed = rng
        print(f"    f_cfg = {f_lo:.2f} GHz @ {lo_nm:.2f} nm  ->  {f_hi:.2f} GHz @ {hi_nm:.2f} nm"
              f"{'  (sign change inside band)' if signed else ''}")
        title += f": f_cfg {f_lo:.1f} → {f_hi:.1f} GHz"
        if not r.shape_ok:
            title += " (shape untrusted)"

    xx = np.linspace(wl[0], wl[-1], 4000)
    mid, half, phase = display_curve(r, xx)
    ax.plot(xx, mid + half * np.cos(phase), "C3", lw=1, label="stabilization fit")
    ax.plot(xx, mid + half, "C2", lw=0.8, label="envelopes")
    ax.plot(xx, mid - half, "C2", lw=0.8)
    ax.set_title(title)
    ax.legend(fontsize=8)
    return r, csig_cov


# --- usCFG reconstruction (thesis §2.3.2) -------------------------------------

def reconstruct_uscfg(lam0_nm, dlam_fwhm_nm, T_DA_fwhm_ps, csig, l0_nm, ga_compressed=True):
    """f_+- of the centrifuge FWHM window from one-arm xcorr + spectra. Times ps, f in GHz.

    R = delay arm (DA, chirp beta_0), L = grating arm (GA, beta_0 + Delta_beta), Tab. 2.1.
    ``csig`` is the stabilization fit's cubic phase in u = lambda - l0 (rad, rad/nm^k).

    Signs. Eq. (2.70) fixes only |phi''_0|, and the fit cannot tell Theta from -Theta, so
    the sign of Delta_phi'' relative to phi''_0 is not measured -- it is set by the optics:
    the grating arm is a compressor, so it REMOVES GDD and is the shorter arm,
    |phi''_L| < |phi''_0|, i.e. phi''_0 takes the sign of Delta_phi'' (``ga_compressed``).
    The remaining global flip (both signs at once) only mirrors f_cfg and is normalised out.
    """
    c = C_NM_PER_PS
    ln2 = np.log(2.0)
    c0, c1, c2, c3 = (float(v) for v in csig)

    # Spectral phase Theta(Omega), Omega = omega - omega_0, expanded at the carrier
    # lambda0 -- Eq. (2.64): Theta = Theta0' - Delta_t Omega + 1/2 Delta_phi'' Omega^2.
    u = lam0_nm - l0_nm
    dPhi_dlam = c1 + 2.0 * c2 * u + 3.0 * c3 * u ** 2               # rad/nm
    d2Phi_dlam2 = 2.0 * c2 + 6.0 * c3 * u                           # rad/nm^2
    dlam_dw = -lam0_nm ** 2 / (2.0 * np.pi * c)                     # lambda = 2 pi c / omega
    d2lam_dw2 = 2.0 * lam0_nm ** 3 / (2.0 * np.pi * c) ** 2
    dTheta_dw = dPhi_dlam * dlam_dw                                 # ps
    d2Theta_dw2 = d2Phi_dlam2 * dlam_dw ** 2 + dPhi_dlam * d2lam_dw2  # ps^2
    dt = -dTheta_dw                                                 # Eq. (2.64)
    dphi2 = d2Theta_dw2                                             # Eq. (2.64), = phi''_R - phi''_L

    # Arm R (delay arm) from its xcorr width.
    dw = 2.0 * np.pi * c * dlam_fwhm_nm / lam0_nm ** 2              # Delta_omega_FWHM, rad/ps
    a = 4.0 * ln2 / dw ** 2                                         # Eq. (2.65)
    T_TL_fwhm = 4.0 * ln2 / dw                                      # below Eq. (2.69)
    gdd_sign = np.sign(dphi2) if ga_compressed else -np.sign(dphi2)  # GA compresses: |phi''_L| < |phi''_0|
    phi2_R = gdd_sign * np.sqrt(T_DA_fwhm_ps ** 2 - T_TL_fwhm ** 2) / dw  # Eq. (2.70), phi''_R = phi''_0
    beta_R = -phi2_R / (a ** 2 + phi2_R ** 2)                       # Eq. (2.68)
    T_R = np.sqrt((a ** 2 + phi2_R ** 2) / a)                       # Eq. (2.67)

    # Arm L (grating arm) from the differential GDD.
    phi2_L = phi2_R - dphi2                                         # Tab. 2.1
    beta_L = -phi2_L / (a ** 2 + phi2_L ** 2)                       # Eq. (2.68)
    T_L = np.sqrt((a ** 2 + phi2_L ** 2) / a)                       # Eq. (2.67)
    T_L_fwhm = np.sqrt(4.0 * ln2) * T_L                             # T_j,FWHM^2 = 4 ln2 T_j^2

    # Centrifuge window and the frequencies bounding it.
    T_eff = T_R * T_L / np.sqrt(T_R ** 2 + T_L ** 2)                # Eq. (2.78)
    T_cfg_fwhm = 2.0 * np.sqrt(2.0 * ln2) * T_eff                   # Eq. (2.80)
    f_c = -dt * (beta_R * T_R ** 2 + beta_L * T_L ** 2) / (
        4.0 * np.pi * (T_R ** 2 + T_L ** 2))                        # Eq. (2.82), THz
    slope = (beta_R - beta_L) / (4.0 * np.pi)                       # Eq. (2.81), THz/ps
    f_minus = f_c - slope * np.sqrt(2.0 * ln2) * T_eff              # Eq. (2.81)
    f_plus = f_c + slope * np.sqrt(2.0 * ln2) * T_eff               # Eq. (2.81)
    overlap_ok = abs(dt) <= np.sqrt(T_R ** 2 + T_L ** 2)            # Eq. (2.79)

    # The global sign of f_cfg is a convention (which way it spins), so report the edges
    # with the larger-|f| one positive; a sign change inside the window survives this.
    sgn = 1.0 if abs(f_plus) >= abs(f_minus) and f_plus >= 0 or abs(f_minus) > abs(f_plus) and f_minus >= 0 else -1.0
    return dict(
        dt=dt, dphi2=dphi2, dw=dw, T_TL_fwhm=T_TL_fwhm,
        phi2_R=phi2_R, phi2_L=phi2_L, beta_R=beta_R, beta_L=beta_L,
        dbeta=beta_L - beta_R, T_R_fwhm=np.sqrt(4.0 * ln2) * T_R, T_L_fwhm=T_L_fwhm, T_eff=T_eff, T_cfg_fwhm=T_cfg_fwhm,
        f_c=1e3 * sgn * f_c, slope=1e3 * sgn * slope,
        f_lo=1e3 * sgn * min(f_minus, f_plus, key=lambda f: sgn * f),
        f_hi=1e3 * sgn * max(f_minus, f_plus, key=lambda f: sgn * f),
        overlap_ok=overlap_ok,
    )


def report_reconstruction(lam0, dlam, T_DA, r):
    """Print the reconstruction (grating arm compressed) and, for contrast, the other pairing."""
    print("[5] usCFG reconstruction from spectra + one-arm xcorr (thesis Tab. 2.1)")
    print(f"    inputs: lambda0 = {lam0:.3f} nm, dlambda_FWHM = {dlam:.3f} nm, "
          f"T_DA,FWHM = {T_DA:.2f} ps")
    rec = reconstruct_uscfg(lam0, dlam, T_DA, r.csig, r.l0, ga_compressed=True)
    alt = reconstruct_uscfg(lam0, dlam, T_DA, r.csig, r.l0, ga_compressed=False)
    print(f"    Delta_omega_FWHM = {rec['dw']:.3f} rad/ps   T_TL = {1e3 * rec['T_TL_fwhm']:.1f} fs")
    print(f"    |Delta_t| = {abs(rec['dt']):.4f} ps   |Delta_phi''| = {abs(rec['dphi2']):.5f} ps^2")
    print(f"    |phi''_DA| = {abs(rec['phi2_R']):.4f} ps^2   |phi''_GA| = {abs(rec['phi2_L']):.4f} ps^2")
    print(f"    |beta_DA| = {abs(rec['beta_R']):.5e}   |beta_GA| = {abs(rec['beta_L']):.5e}   "
          f"Delta_beta = {abs(rec['beta_L']) - abs(rec['beta_R']):+.4e} rad/ps^2")
    print(f"    T_DA,FWHM = {rec['T_R_fwhm']:.2f} ps   T_GA,FWHM = {rec['T_L_fwhm']:.2f} ps   "
          f"T_cfg,FWHM = {rec['T_cfg_fwhm']:.2f} ps"
          f"   (Eq. 2.79 overlap {'ok' if rec['overlap_ok'] else 'VIOLATED'})")
    print(f"    f_cfg(t_c) = {rec['f_c']:.2f} GHz   f = {rec['f_lo']:.2f} -> {rec['f_hi']:.2f} GHz"
          f"   (delta f = {rec['f_hi'] - rec['f_lo']:.2f} GHz)")
    print(f"    [if the GA were stretched instead: T_GA = {alt['T_L_fwhm']:.2f} ps, "
          f"f = {alt['f_lo']:.2f} -> {alt['f_hi']:.2f} GHz]")
    return rec


def propagate_uscfg(lam0, dlam, T_DA, csig, l0, s_lam0, s_dlam, s_T_DA, csig_cov):
    """Linear error propagation through ``reconstruct_uscfg``.

    Inputs p = (T_DA, lambda0, dlambda, c0..c3). T_DA, lambda0 and dlambda come from
    independent Gaussian fits, the c_k from the stabilization fit with their full
    covariance, so cov(p) is block diagonal. J = d(out)/dp by central differences
    (step 1e-3 sigma), cov(out) = J cov(p) J^T. Returns (sigmas, cov, budget) where
    ``budget[name]`` is each input group's contribution to each output's sigma.
    """
    outs = ("f_lo", "f_hi", "f_c", "T_cfg_fwhm", "T_L_fwhm", "dt", "dphi2", "beta_R", "beta_L", "dbeta")
    p0 = np.array([T_DA, lam0, dlam, *np.asarray(csig, float)])
    C = np.zeros((7, 7))
    C[0, 0], C[1, 1], C[2, 2] = s_T_DA ** 2, s_lam0 ** 2, s_dlam ** 2
    if csig_cov is not None:
        C[3:, 3:] = csig_cov

    def run(p):
        rec = reconstruct_uscfg(p[1], p[2], p[0], p[3:], l0)
        # The sign conventions are normalised inside; differentiate magnitudes.
        return np.array([abs(rec[k]) for k in outs])

    J = np.zeros((len(outs), 7))
    for j in range(7):
        h = 1e-3 * np.sqrt(C[j, j]) if C[j, j] > 0 else 0.0
        if h == 0.0:
            continue
        dp = np.zeros(7)
        dp[j] = h
        J[:, j] = (run(p0 + dp) - run(p0 - dp)) / (2.0 * h)
    cov = J @ C @ J.T
    sig = dict(zip(outs, np.sqrt(np.diag(cov))))
    i_lo, i_hi = outs.index("f_lo"), outs.index("f_hi")
    sig["df"] = float(np.sqrt(cov[i_lo, i_lo] + cov[i_hi, i_hi] - 2.0 * cov[i_lo, i_hi]))
    sig["rho_lo_hi"] = float(cov[i_lo, i_hi] / np.sqrt(cov[i_lo, i_lo] * cov[i_hi, i_hi]))

    budget = {}
    for name, idx in (("T_DA (xcorr)", [0]), ("lambda0", [1]), ("dlambda (spectrum)", [2]),
                      ("c0..c3 (cfg spectrum)", [3, 4, 5, 6])):
        Jg = J[:, idx]
        budget[name] = dict(zip(outs, np.sqrt(np.diag(Jg @ C[np.ix_(idx, idx)] @ Jg.T))))
    return sig, cov, budget


def report_uncertainty(rec, sig, budget):
    print("[5b] error propagation (1 sigma, statistical fit errors only)")
    print(f"    f_lo   = {rec['f_lo']:6.2f} +- {sig['f_lo']:.2f} GHz")
    print(f"    f_hi   = {rec['f_hi']:6.2f} +- {sig['f_hi']:.2f} GHz")
    print(f"    f(t_c) = {rec['f_c']:6.2f} +- {sig['f_c']:.2f} GHz")
    print(f"    delta f = {rec['f_hi'] - rec['f_lo']:6.2f} +- {sig['df']:.2f} GHz"
          f"   (corr(f_lo, f_hi) = {sig['rho_lo_hi']:+.2f})")
    print(f"    T_cfg  = {rec['T_cfg_fwhm']:.2f} +- {sig['T_cfg_fwhm']:.2f} ps   "
          f"T_GA = {rec['T_L_fwhm']:.2f} +- {sig['T_L_fwhm']:.2f} ps")
    print(f"    |Delta_t| = {abs(rec['dt']):.4f} +- {sig['dt']:.4f} ps   "
          f"|Delta_phi''| = {abs(rec['dphi2']):.5f} +- {sig['dphi2']:.5f} ps^2")
    print(f"    |Delta_beta| = {abs(rec['dbeta']):.4e} +- {sig['dbeta']:.1e} rad/ps^2")
    print("    budget (contribution to sigma)      f_lo     f_hi     f_c   T_cfg")
    for name, b in budget.items():
        print(f"      {name:<28s} {b['f_lo']:7.3f}  {b['f_hi']:7.3f}  {b['f_c']:6.3f}  {b['T_cfg_fwhm']:6.3f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xcorr-pulse", default=str(XCORR_PULSE))
    ap.add_argument("--xcorr-cfg", default=str(XCORR_CFG))
    ap.add_argument("--spec-arm", default=str(SPEC_ARM))
    ap.add_argument("--spec-cfg", default=str(SPEC_CFG))
    ap.add_argument("--window-nm", nargs=2, type=float, default=list(fc.ZOOM))
    ap.add_argument("--zero-mm", type=float, default=0.0)
    ap.add_argument("--window-ps", type=float, default=None,
                    help="readout window for panel 2; default = FWHM from panel 1")
    ap.add_argument("--save", default=None, help="write the figure here instead of showing it")
    args = ap.parse_args(argv)

    import matplotlib.pyplot as plt

    fig, axs = plt.subplots(2, 2, figsize=(15, 9))
    fwhm_ps, s_fwhm_ps = panel_xcorr_pulse(axs[0, 0], args.xcorr_pulse, args.zero_mm)
    lam0, dlam, s_lam0, s_dlam = panel_spec_arm(axs[1, 0], args.spec_arm)
    r, csig_cov = panel_spec_cfg(axs[1, 1], args.spec_cfg, args.window_nm)
    recon = sig = None
    if r is not None:
        recon = report_reconstruction(lam0, dlam, fwhm_ps, r)
        sig, _, budget = propagate_uscfg(lam0, dlam, fwhm_ps, r.csig, r.l0,
                                         s_lam0, s_dlam, s_fwhm_ps, csig_cov)
        recon["sig"] = sig
        report_uncertainty(recon, sig, budget)
    xc = panel_xcorr_cfg(axs[0, 1], args.xcorr_cfg, args.zero_mm,
                         args.window_ps if args.window_ps is not None else fwhm_ps, recon)

    if recon is not None and xc is not None:
        print("[6] f_cfg FWHM range, side by side")
        print(f"    spectral reconstruction : {recon['f_lo']:6.2f} -> {recon['f_hi']:6.2f} GHz"
              f"   (+-{sig['f_lo']:.2f} / {sig['f_hi']:.2f})   over T_cfg = {recon['T_cfg_fwhm']:.1f} ps")
        for name, d in (("xcorr, BIC order", xc), ("xcorr, quadratic", xc.get("quad"))):
            if d is None:
                continue
            print(f"    {name:<24s}: {d['f_min']:6.2f} -> {d['f_max']:6.2f} GHz"
                  f"   (+-{d['s_min']:.2f} / {d['s_max']:.2f} fit, +-{d['sys_min']:.2f} / "
                  f"{d['sys_max']:.2f} FWHM)   over W = {d['window']:.1f} ps")
    fig.tight_layout()
    fig2 = plot_xcorr_order_comparison(xc, recon) if xc is not None else None
    if args.save:
        fig.savefig(args.save, dpi=120)
        if fig2 is not None:
            out = Path(args.save)
            fig2.savefig(out.with_name(out.stem + "_xcorr_orders" + out.suffix), dpi=120)
    else:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

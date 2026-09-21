"""Plot a spectrum recording as a map: wavelength on x, time on y, counts as colour.

Reads the layout ``SpectrumRecorder`` writes (``wavelength_nm`` + ``traces/<time_ns>``),
either at the root of a ``SPEC_*.h5`` file or in a subgroup of a bigger file (``GROUP``,
found automatically when there is only one). Rig-state changes from ``metadata/`` are
drawn as dashed lines labelled with their reason.

Edit the settings below, then run ``python tools/plot_spectrum_recording.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm

# -- settings -------------------------------------------------------------
FILE = Path(__file__).resolve().parents[1] / "SPEC_first_test_20260916_220124.h5"
GROUP: str | None = None                  # HDF5 group holding the recording; None = auto
WL_RANGE: tuple[float, float] | None = None  # e.g. (760, 840) nm; None = full range
LOG_SCALE = False
SHOW_METADATA = True                      # dashed lines at rig-state changes
SAVE: Path | None = None                  # write the figure here instead of showing it


def find_group(f: h5py.File, name: str | None) -> h5py.Group:
    if name:
        return f[name]
    if "traces" in f:
        return f
    found: list[h5py.Group] = []
    f.visititems(lambda _, obj: found.append(obj)
                 if isinstance(obj, h5py.Group) and "traces" in obj else None)
    if len(found) != 1:
        raise SystemExit(f"Found {len(found)} recording groups; set GROUP to one of: "
                         + ", ".join(g.name for g in found))
    return found[0]


def main() -> None:
    with h5py.File(FILE, "r") as f:
        g = find_group(f, GROUP)
        wl = g["wavelength_nm"][()]
        keys = sorted(g["traces"].keys())
        if not keys:
            raise SystemExit("No traces in recording")
        t_ns = np.array([int(k) for k in keys], dtype=np.int64)
        counts = np.stack([g["traces"][k][()] for k in keys])
        meta = []
        if SHOW_METADATA and "metadata" in g:
            for k in sorted(g["metadata"].keys()):
                raw = g["metadata"][k][()]
                entry = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                meta.append((int(k), entry.get("reason", "") if isinstance(entry, dict) else ""))

    t0 = t_ns[0]
    t_s = (t_ns - t0) / 1e9

    if WL_RANGE:
        sel = (wl >= min(WL_RANGE)) & (wl <= max(WL_RANGE))
        wl, counts = wl[sel], counts[:, sel]

    norm = None
    if LOG_SCALE:
        pos = counts[counts > 0]
        norm = LogNorm(vmin=pos.min() if pos.size else 1, vmax=max(counts.max(), 1))
        counts = np.where(counts > 0, counts, np.nan)

    fig, ax = plt.subplots(figsize=(9, 7), constrained_layout=True)
    # "nearest" handles uneven time steps (dropped frames, pauses) without resampling.
    mesh = ax.pcolormesh(wl, t_s, counts, shading="nearest", cmap="viridis", norm=norm)
    fig.colorbar(mesh, ax=ax, label="Counts")

    for ts, reason in meta:
        y = (ts - t0) / 1e9
        if t_s[0] <= y <= t_s[-1]:
            ax.axhline(y, color="w", lw=0.8, ls="--", alpha=0.7)
            ax.text(wl[-1], y, f" {reason}", color="w", fontsize=7, va="bottom", ha="right")

    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Time since first spectrum (s)")
    ax.set_title(f"{FILE.name} — {len(keys)} spectra over {t_s[-1]:.1f} s")

    if SAVE:
        fig.savefig(SAVE, dpi=150)
        print(f"Saved {SAVE}")
    else:
        plt.show()


if __name__ == "__main__":
    main()

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length

from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (
    FitTunables,
    FringeFitResult,
)
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import LOOP_GAIN


# ---------------------------------------------------------------------------
# Params: the cubic-phase fringe fit's tunable inputs plus its last committed
# outputs (kept together so the chart overlay can reconstruct the fitted curve
# without re-fitting, and so both cross the IPC boundary in one payload).
# ---------------------------------------------------------------------------
@dataclass
class FringeFitParams(PrimitiveSerde):
    # --- tunables (user-editable inputs to fringe_fit.analyze_trace) ---
    # The v3 analysis owns its own calibrated constants (see fringe_core); only these two
    # are user-facing. The old folded-chirp knobs (ratio, sigma_init, phase_loss_scale,
    # signal_loss_frac, init_smooth_div) are gone with that pipeline -- from_primitive
    # ignores them, so configs persisted before this change still load.
    # Defaults are IMPORTED from fringe_core, not retyped -- a hardcoded 0.40 here while
    # fringe_core had recalibrated to 0.30 is exactly the kind of stale-constant drift that
    # throws the carrier off. Persisted configs still override these on load.
    trunc_threshold: float = fc.TRUNC_THRESHOLD   # high-visibility core keep-level
    trust_nsig: float = fc.TRUST_NSIG  # accuracy/yield trade; see FitTunables.trust_nsig.
                                       # 3.0 = >=98% of reported fits correct, <=5% of good
                                       # fits declined. Lower to commit more frames while
                                       # aligning; raising past ~5 buys little accuracy and
                                       # costs a lot of yield.
    lambda_ref: Length = field(default_factory=lambda: Length(802.0, Prefix.NANO))
    # --- operator-dragged analysis geometry (see fringe_core) ---
    # env_center: the envelope/phase anchor, pinned so a one-sided clip cannot drag it (the
    # fit's origin l0 is set here and muU is bounded to +-ENV_CENTRE_TOL of it). Dragged live
    # on the chart; default is the nominal beam centre. manual_cut_left: the operator-dragged
    # clip edge (nm) -- fringes below it are excluded from the fit. None => no manual cut, and
    # since the auto-detector is unreliable here, no cut at all. Only left cuts are physical.
    env_center: float = fc.ENV_CENTRE_DEFAULT      # nm
    manual_cut_left: float | None = None           # nm, or None

    # --- committed fit outputs (results; drive the overlay + phase readout) ---
    pU: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])   # upper env Gaussian
    pLn: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])  # gap Gaussian
    l0: float = 0.0                    # phase basis origin (nm) = core centroid
    c0: float = 0.0                    # cubic phase coeffs in u = lambda - l0
    c1: float = 0.0
    c2: float = 0.0
    c3: float = 0.0
    phase_ref: float = 0.0             # unwrapped cubic phase at ref_wl (rad)
    ref_wl: float = 0.0                # WHERE phase_ref was evaluated. This is NOT always
                                       # lambda_ref: a clip near the core makes the phase at
                                       # the spectral centre unsupportable, and the fit falls
                                       # back to the core centroid. Read this, not lambda_ref.
    ref_fallback: bool = False         # True => the reference moved off the spectral centre
    shape_ok: bool = True              # the fit can support its CARRIER and CHIRP, not just
                                       # the phase. Carried through to the UI because the GHz
                                       # frequency-range readout extrapolates to 802+-9 nm,
                                       # far outside the fitted core, where a poorly
                                       # determined c2 enters as d^2. The phase lock does not
                                       # need this and must not be gated on it.
    cut_left: float | None = None      # WHERE the knife edge was found (nm), per side; None
    cut_right: float | None = None     # = no edge there. Samples outside [cut_left, cut_right]
                                       # were EXCLUDED from the fit, so these bound what the
                                       # committed answer actually rests on. Carried to the UI
                                       # so the chart can draw the boundary instead of leaving
                                       # the operator to infer it from a gap in the fringes.
    rms_sig: float = 0.0               # last raw-signal fit RMS (counts)
    rms_frac: float = 0.0              # last scale-free fit residual (rms / median half-amp)
    inlier_pct: float = 0.0            # last core inlier fraction (%)

    # --- convenience ---
    def tunables(self) -> FitTunables:
        return FitTunables(
            trunc_threshold=self.trunc_threshold,
            trust_nsig=self.trust_nsig,
        )

    def commit(self, r: FringeFitResult, phase_ref: float) -> None:
        """Store an accepted fit's outputs for the overlay + readout."""
        self.pU = [float(v) for v in r.pU]
        self.pLn = [float(v) for v in r.pLn]
        self.l0 = float(r.l0)
        self.c0, self.c1, self.c2, self.c3 = (float(v) for v in r.csig)
        self.phase_ref = float(phase_ref)
        self.ref_wl = float(r.ref_wl)
        self.ref_fallback = bool(r.ref_fallback)
        self.shape_ok = bool(r.shape_ok)
        self.cut_left = None if r.cut_left is None else float(r.cut_left)
        self.cut_right = None if r.cut_right is None else float(r.cut_right)
        self.rms_sig = float(r.rms_sig)
        self.rms_frac = float(r.rms_frac)
        self.inlier_pct = float(r.inlier_pct)

    def as_result(self) -> FringeFitResult:
        """Wrap the committed outputs as a FringeFitResult so the chart overlay
        can reuse ``fringe_fit.display_curve``."""
        return FringeFitResult(
            accepted=True,
            pU=(self.pU[0], self.pU[1], self.pU[2], self.pU[3]),
            pLn=(self.pLn[0], self.pLn[1], self.pLn[2], self.pLn[3]),
            l0=self.l0,
            csig=(self.c0, self.c1, self.c2, self.c3),
            phase_ref=self.phase_ref,
            rms_sig=self.rms_sig,
            rms_frac=self.rms_frac,
            inlier_pct=self.inlier_pct,
            has_null=False,
            ref_wl=self.ref_wl,
            ref_fallback=self.ref_fallback,
            shape_ok=self.shape_ok,
            cut_left=self.cut_left,
            cut_right=self.cut_right,
        )

    def copy_from(self, other: "FringeFitParams") -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(other, f.name))

    # --- serialization ---
    def to_primitive(self) -> Primitive:
        out: dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            out[f.name] = val.to_primitive() if isinstance(val, Length) else val
        return out

    @classmethod
    def from_primitive(cls, v: Primitive) -> "FringeFitParams":
        # Only fields this class still declares are read, so a config persisted before the
        # v3 port -- which carries the dead folded-chirp knobs (ratio, sigma_init,
        # phase_loss_scale, signal_loss_frac, init_smooth_div) -- loads cleanly and simply
        # ignores them. Missing new fields (trust_nsig, ref_wl) fall back to the defaults.
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in v:
                continue
            if f.name == "lambda_ref":
                kwargs[f.name] = Length.from_primitive(v[f.name])
            elif f.name in ("pU", "pLn"):
                kwargs[f.name] = [float(x) for x in v[f.name]]
            elif f.name in ("ref_fallback", "shape_ok"):
                kwargs[f.name] = bool(v[f.name])
            elif f.name in ("cut_left", "cut_right", "manual_cut_left"):
                # Genuinely optional: None means no knife edge / no manual cut on that side.
                # These must NOT go through float() -- that is what broke the decode.
                kwargs[f.name] = None if v[f.name] is None else float(v[f.name])
            else:
                kwargs[f.name] = float(v[f.name])
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Config: run-level state — the analysis window, the per-shot acceptance gate,
# and the target phase.
# ---------------------------------------------------------------------------
@dataclass
class StabilizationConfig(PrimitiveSerde):
    params: FringeFitParams
    wavelength_range: Range[Length] = field(default_factory=lambda: Range(
        Length(790, Prefix.NANO), Length(814, Prefix.NANO)
    ))
    rms_frac_threshold: float = 0.30    # accept if scale-free fit residual below this
                                        # (rms / median half-amp). Replaces the old absolute
                                        # rms_threshold (counts), which rejected bright traces:
                                        # a good fit's rms scales with fringe amplitude, so an
                                        # absolute count gate tuned on dim traces (good ~1 ct)
                                        # rejected bright ones (good ~10 ct). 0.30 is permissive;
                                        # tighten from the logged rms_frac (good live fits ~0.1).
    inlier_threshold: float = 80.0      # accept if folded-phase inliers above this (%)
    set_phase: Angle = field(default_factory=lambda: Angle(0))
    loop_gain: float = LOOP_GAIN        # fraction of the measured phase error corrected per
                                        # committed frame; see PhaseCorrector. Lives here and
                                        # not on FringeFitParams because it is a control-loop
                                        # property, not a fit property -- the fit is identical
                                        # whatever this is set to.

    def accepts(self, r: FringeFitResult) -> bool:
        """Per-shot quality gate.

        `trust_ok` is the important one and it is NOT redundant with the residual gates: a
        clipped trace costs lever arm and the phase at the reference goes genuinely
        underdetermined while the fit still reconstructs at R^2 ~ 0.96. The residual cannot
        see that -- only the propagated covariance can. Without this clause the app would
        commit confident-looking phases it has no basis for, which is exactly the failure
        mode the trust gate exists to stop. Tune it via params.trust_nsig, not by removing it.

        `trust_ok` now covers the PHASE ONLY (c0 at ref_wl). That is deliberate and it is the
        whole fix for the over-rejection: the loop corrects phase at one wavelength and never
        reads the carrier or chirp, so gating on those threw away frames for an error it does
        not act on. Measured over 1240 test traces, 11 of the 13 fits that fail a
        four-coefficient grader have a CORRECT phase. Phase-only accuracy of committed fits is
        99.84% with 0.0% of good fits declined, against 3.7% declined under the fused gate.

        `shape_ok` (c1..c3) is therefore NOT checked here. It exists for consumers that
        evaluate the fit away from ref_wl -- the chart overlay and the GHz frequency-range
        readout -- and those must check it themselves. Folding it back in here would
        reintroduce exactly the rejections this change removed.

        `ref_offset_ok` is the ACCURACY clause, and it is the one thing here that the fit's
        own statistics cannot express. `trust_ok` asks whether the phase at the reference is
        PRECISE; this asks whether it is where the data actually is. When a clip shrinks the
        core its centroid slides off the reference, and the phase quoted back there splits
        into two populations 1.4 rad apart -- on frames with trust_ok=True, rms_frac ~0.10
        and 97-100% inliers. Nothing else in this gate can see that, and 1.4 rad is four and
        a half times the 0.314 rad the trust gate exists to enforce. See
        fringe_core.REF_MAX_OFFSET_FRAC for the measurement and the threshold.

        It is checked HERE rather than inside the fit on purpose: folding it into `trust_ok`
        made `_explains` fail, which sent every clipped frame into a multi-second recovery
        scan that could not fix it. Dropping the frame is the whole remedy.
        """
        return (r.accepted
                and r.trust_ok
                and r.ref_offset_ok
                and r.rms_frac < self.rms_frac_threshold
                and r.inlier_pct > self.inlier_threshold)

    def copy_from(self, other: "StabilizationConfig") -> None:
        self.params.copy_from(other.params)
        for f in fields(self):
            if f.name != "params":
                setattr(self, f.name, getattr(other, f.name))

    def to_primitive(self) -> Primitive:
        return {
            "params": self.params.to_primitive(),
            "wavelength_range": {
                "min": self.wavelength_range.min.to_primitive(),
                "max": self.wavelength_range.max.to_primitive(),
            },
            "rms_frac_threshold": self.rms_frac_threshold,
            "inlier_threshold": self.inlier_threshold,
            "set_phase": self.set_phase.to_primitive(),
            "loop_gain": self.loop_gain,
        }

    @classmethod
    def from_primitive(cls, v: Primitive) -> "StabilizationConfig":
        wl_range = v["wavelength_range"]
        return cls(
            params=FringeFitParams.from_primitive(v["params"]),
            wavelength_range=Range(
                Length.from_primitive(wl_range["min"]),
                Length.from_primitive(wl_range["max"]),
            ),
            # Backward-compat: a config persisted before the gate switch has
            # "rms_threshold" (counts) and no "rms_frac_threshold" -> use the default.
            rms_frac_threshold=float(v.get("rms_frac_threshold", 0.30)),
            inlier_threshold=float(v["inlier_threshold"]),
            set_phase=Angle.from_primitive(v["set_phase"]),
            # Pre-tunable-gain configs have no "loop_gain" -> the calibrated default.
            loop_gain=float(v.get("loop_gain", LOOP_GAIN)),
        )

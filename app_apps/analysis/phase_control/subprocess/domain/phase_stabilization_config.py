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
from app_apps.analysis.phase_control.subprocess.domain.fringe_visibility import MIN_VISIBILITY
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import (
    AVG_SPECTRA,
    PHASE_TOLERANCE,
)


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
    # Defaults are IMPORTED from fringe_core, not retyped -- a hardcoded 0.40 here while the
    # standalone had recalibrated to 0.30 is exactly what broke fit parity with a
    # byte-identical fringe_core.py. Persisted configs still override these on load.
    trunc_threshold: float = fc.TRUNC_THRESHOLD   # high-visibility core keep-level
    trust_nsig: float = fc.TRUST_NSIG  # accuracy/yield trade; see FitTunables.trust_nsig.
                                       # 3.0 = >=98% of reported fits correct, <=5% of good
                                       # fits declined. Lower to commit more frames while
                                       # aligning; raising past ~5 buys little accuracy and
                                       # costs a lot of yield.
    lambda_ref: Length = field(default_factory=lambda: Length(802.0, Prefix.NANO))

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
    trust_ok: bool = True              # did the last committed fit's own covariance support
                                       # the phase at ref_wl? With an ROI set this is NO
                                       # LONGER a gate -- see StabilizationConfig.accepts --
                                       # so it is carried across IPC and shown in the panel
                                       # instead. The operator took the judgement; they have
                                       # to be able to see what they are judging.
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
        self.trust_ok = bool(r.trust_ok)
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
            trust_ok=self.trust_ok,
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
            elif f.name in ("ref_fallback", "shape_ok", "trust_ok"):
                kwargs[f.name] = bool(v[f.name])
            elif f.name in ("cut_left", "cut_right"):
                # Optional: None is the ordinary value on an unclipped side, and it means
                # "no edge here", not zero. Coercing it through float() both raises on load
                # and, if it did not, would park a knife-edge marker at 0 nm.
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
    min_visibility: float = MIN_VISIBILITY
                                        # abort the fit ENTIRELY below this fringe-contrast
                                        # index (fringe_visibility, ~1.9 ms, no optimizer).
                                        # This is not a quality gate like the two above --
                                        # those judge a fit that already ran. A washed-out
                                        # trace costs 47 s in the optimizer and then returns
                                        # status="ok" with a phase fit to noise, so it has to
                                        # be caught BEFORE the fit, not after. Editable while
                                        # running: it can only be tuned against a live trace.
    set_phase: Angle = field(default_factory=lambda: Angle(0))
    invert_correction: bool = False     # flip the HWP rotation direction. NOT a tuning knob:
                                        # the sense of the correction depends on the QUARTER
                                        # wave plate's orientation, so the same measured error
                                        # calls for opposite rotations on two setups that are
                                        # otherwise identical. Safe to change mid-run -- the
                                        # corrector is retuned in place. If the loop locks
                                        # stably but pi away from the setpoint, this is the
                                        # knob: see PhaseCorrector.CORRECTION_SIGN.
    # --- block-averaged correction loop (see phase_corrector) --------------------------
    avg_spectra: int = AVG_SPECTRA      # accepted fits per correction. The loop collects
                                        # this many phases, circular-averages them, CLEARS
                                        # the block and corrects once on the mean. The block
                                        # never straddles a move, so the averaged error
                                        # always describes the state the stage is in now.
                                        # Raise it for a quieter error and a slower loop;
                                        # the correction rate is avg_spectra frames, not a
                                        # clock, so it follows the spectrometer settings.
    phase_tolerance: Angle = field(default_factory=lambda: PHASE_TOLERANCE)
                                        # deadband. Inside this the loop holds and issues
                                        # nothing; outside it corrects the WHOLE error in
                                        # one move. There is no proportional region between
                                        # the two -- see PhaseCorrector.
    capture_n: int = 10                 # consecutively accepted traces averaged into one
                                        # reference. This is the COLD stage: every parameter
                                        # is free, the average is fit once, and the fitted
                                        # shape is then frozen for phase-only tracking.
                                        # Consecutive on purpose -- a run broken by a
                                        # rejection restarts, so the reference is never
                                        # averaged across a disturbance.
    min_amplitude_frac: float = 0.10    # hold if the closed-form fit amplitude falls below
                                        # this fraction of the reference's capture
                                        # amplitude. The in-loop descendant of the legacy
                                        # residuals_threshold: the amplitude drops ~226x
                                        # when the fringes wash out, so anything in 0.05-0.3
                                        # separates cleanly.
    move_settle_s: float = 0.5          # ignore accepted fits for this long after a
                                        # correction goes out. The legacy loop had the
                                        # rotator's own is_busy flag to gate on; the stage
                                        # lives in another process here, so this is a fixed
                                        # stand-in for it. It only has to cover the move,
                                        # because the block is already cleared -- its job is
                                        # to keep spectra taken DURING the rotation out of
                                        # the next block, not to pace the loop.
    roi_lo: float | None = None         # ROI: the analysis region, in nm, asserted by the
    roi_hi: float | None = None         # operator by dragging two chart bounds. BOTH None
                                        # (the default) means "Auto" and the pipeline behaves
                                        # exactly as it always has, in every respect.
                                        #
                                        # Setting it is the operator taking responsibility
                                        # for where the good data is -- and, with it, for
                                        # judging the fit, which is drawn on the chart. So it
                                        # is also the switch for `accepts` below: the guards
                                        # that exist to FIND the good region, and the ones
                                        # that MISFIRE on a short array, stop gating and
                                        # become readouts (trust_ok, rms_frac, inlier_pct in
                                        # the panel). The four structural guards stay: <16
                                        # points, non-finite, dead_window, and the LOCKED
                                        # path's min_amplitude_frac -- which is the loop's
                                        # real safety net, since the fringe amplitude drops
                                        # ~226x when the fringes wash out.
                                        #
                                        # It is NOT the zoom. Zoom sets the chart's x limits
                                        # and never reaches the fit; this reaches the fit and
                                        # never moves the chart.
                                        #
                                        # Like manual_cut_left it lives here rather than on
                                        # FringeFitParams, which is REPLACED wholesale by
                                        # every committed fit -- an operator choice parked
                                        # there would survive exactly one frame.
    manual_cut_left: float | None = None
                                        # operator override for the short-wavelength
                                        # terminal the f_cfg readout quotes at, in nm. None
                                        # = use the fit's own detected cut, which is the
                                        # default and the normal case. Set by dragging the
                                        # left knife-edge marker; cleared by "Auto".
                                        #
                                        # It lives on the config rather than on
                                        # FringeFitParams because params is REPLACED by
                                        # every committed fit -- an operator's choice
                                        # parked there would survive one frame and vanish.
    @property
    def roi(self) -> tuple[float, float] | None:
        """The ROI as (lo, hi) in nm, or None for auto.

        None unless BOTH bounds are set and ordered -- a half-set or inverted ROI is an
        interaction in progress, not an instruction, and must not silently reconfigure the
        fit. `fringe_core.analyze` takes exactly this value.
        """
        lo, hi = self.roi_lo, self.roi_hi
        if lo is None or hi is None:
            return None
        lo, hi = float(lo), float(hi)
        return (lo, hi) if hi > lo else None

    def pinned_lambda_ref(self, current: float | None = None) -> float:
        """The wavelength the phase is measured at, in nm.

        Normally the operator's ``lambda_ref``, unchanged. An ROI that EXCLUDES it cannot
        support the phase there -- the polynomial would be extrapolated past the only data
        the fit saw -- so the reference moves. It moves as little as possible: to ``current``
        (the pin already in force) if the ROI still contains that, and only otherwise to the
        ROI midpoint. Pinning matters because a reference that wandered per frame would
        redefine zero underneath the control loop.
        """
        lam = float(self.params.lambda_ref.value(Prefix.NANO))
        roi = self.roi
        if roi is None or roi[0] <= lam <= roi[1]:
            return lam
        if current is not None and roi[0] <= float(current) <= roi[1]:
            return float(current)
        return 0.5 * (roi[0] + roi[1])

    def accepts(self, r: FringeFitResult) -> bool:
        """Per-shot quality gate.

        **With an ROI set this collapses to `r.accepted`** -- documented as "a solver-success
        flag only", i.e. exactly "the solver produced coefficients". The gates below are then
        computed anyway (they are already paid for) and SHOWN rather than enforced. This is
        conditional on purpose and the conditionality is load-bearing: measured over 30
        frames at one setpoint, dropping the same guards on the WIDE 790-814 band degrades
        the scatter 10x (13.6 -> 138.7 mrad), because there the contrast crop is doing real
        work trimming the low-contrast wings. On an 800-804 ROI the same removal is what
        makes the loop work at all -- 1.6 deg scatter against a 10 deg deadband, where the
        crop previously cut the core to 0 points and every frame was rejected.

        Frames with status != "ok" still reject in both modes: those carry csig=None and
        never reach here as an accepted result.

        `trust_ok` is the important one and it is NOT redundant with the residual gates: a
        clipped trace costs lever arm and the phase at the reference goes genuinely
        underdetermined while the fit still reconstructs at R^2 ~ 0.96. The residual cannot
        see that -- only the propagated covariance can. Without this clause the app would
        commit confident-looking phases it has no basis for, which is exactly the failure
        mode the trust gate exists to stop. Tune it via params.trust_nsig, not by removing it.

        `trust_ok` now covers the PHASE ONLY (c0 at ref_wl). That is deliberate and it is the
        whole fix for the over-rejection: the loop corrects phase at one wavelength and never
        reads the carrier or chirp, so gating on those threw away frames for an error it does
        not act on. Measured over 1240 harness traces, 11 of the 13 fits that fail a
        four-coefficient grader have a CORRECT phase. Phase-only accuracy of committed fits is
        99.84% with 0.0% of good fits declined, against 3.7% declined under the fused gate.

        `shape_ok` (c1..c3) is therefore NOT checked here. It exists for consumers that
        evaluate the fit away from ref_wl -- the chart overlay and the GHz frequency-range
        readout -- and those must check it themselves. Folding it back in here would
        reintroduce exactly the rejections this change removed.
        """
        if self.roi is not None:
            return r.accepted
        return (r.accepted
                and r.trust_ok
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
            "min_visibility": self.min_visibility,
            "set_phase": self.set_phase.to_primitive(),
            "invert_correction": self.invert_correction,
            "avg_spectra": self.avg_spectra,
            "capture_n": self.capture_n,
            "min_amplitude_frac": self.min_amplitude_frac,
            "phase_tolerance": self.phase_tolerance.to_primitive(),
            "move_settle_s": self.move_settle_s,
            "roi_lo": self.roi_lo,
            "roi_hi": self.roi_hi,
            "manual_cut_left": self.manual_cut_left,
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
            # Absent in a config persisted before the gate existed -> the calibrated default.
            min_visibility=float(v.get("min_visibility", MIN_VISIBILITY)),
            set_phase=Angle.from_primitive(v["set_phase"]),
            # Configs persisted before the toggle existed ran the baseline sign -> False.
            invert_correction=bool(v.get("invert_correction", False)),
            # The knobs of the timed EWMA loop (loop_gain, slow_correction,
            # correction_period_s, shape_mismatch_max) went with it. A config persisted
            # while it existed still carries them; they are simply not read, so an old file
            # loads onto the block loop's defaults without complaint.
            avg_spectra=int(v.get("avg_spectra", AVG_SPECTRA)),
            capture_n=int(v.get("capture_n", 10)),
            min_amplitude_frac=float(v.get("min_amplitude_frac", 0.10)),
            phase_tolerance=(Angle.from_primitive(v["phase_tolerance"])
                             if "phase_tolerance" in v else PHASE_TOLERANCE),
            move_settle_s=float(v.get("move_settle_s", 0.5)),
            # Absent in every config written before the ROI existed -> None -> auto, which
            # is byte-identical to the pre-ROI pipeline.
            roi_lo=(None if v.get("roi_lo") is None else float(v["roi_lo"])),
            roi_hi=(None if v.get("roi_hi") is None else float(v["roi_hi"])),
            # Absent in every config written before the drag existed -> None -> auto.
            manual_cut_left=(None if v.get("manual_cut_left") is None
                             else float(v["manual_cut_left"])),
        )

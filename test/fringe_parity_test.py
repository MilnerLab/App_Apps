"""Anti-drift parity: the app's fit MUST equal the standalone's, bit-for-bit.

This is the test that would have caught every bug found on 2026-07-16. The app used to
carry a hand-maintained second copy of the analysis, and it had silently drifted into an
envelope offset of 255 (truth 155) and a carrier of ~0 (truth 6.7-23.8) on every real
trace. There was no test that could see it, because the old parity test compared the port
against numbers FROZEN from a 2026-07-14 standalone run -- so it pinned the port to a
snapshot and went green while both the port and the standalone moved on.

So this test does not compare against frozen constants. It imports the LIVE standalone and
requires exact agreement. If someone edits one copy of fringe_core.py and not the other,
this fails immediately and says so.

Run directly:  python test/fringe_parity_test.py
(numpy/scipy/pandas only -- no app framework, so the repo .venv shim is not needed.)
"""
from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (  # noqa: E402
    FitTunables, analyze_trace,
)
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as app_core  # noqa: E402

STANDALONE_DIR = r"D:\Documents\University\UBC research\2026\Data\20260709\spectrometer"
ZOOM = (790.0, 814.0)
TRACES = ["da17_1GA_-75.xls", "da_15.95ga_-55.29.xls", "da_15.95ga_-75.xls"]
CSV_TRACES = ["live_desktop_spectrum.csv", "2020607181645_truncated.csv"]

# App tunable -> the fringe_core constant it must be an alias of. Anything on this list is a
# CALIBRATED value: the app may expose it, but must never restate it. See
# test_tunable_defaults_are_not_copied.
TUNABLE_TO_CORE = {"trunc_threshold": "TRUNC_THRESHOLD", "trust_nsig": "TRUST_NSIG"}


def _load_standalone():
    """Import the standalone fringe_core by path (it is not on sys.path / not a package)."""
    p = os.path.join(STANDALONE_DIR, "fringe_core.py")
    if not os.path.exists(p):
        return None, p
    spec = importlib.util.spec_from_file_location("standalone_fringe_core", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["standalone_fringe_core"] = m   # register BEFORE exec or dataclasses break
    spec.loader.exec_module(m)
    return m, p


def _read(path: str):
    if path.lower().endswith(".csv"):
        d = np.genfromtxt(path, delimiter=",", names=True)
        return d["wavelength_nm"].astype(float), d["intensity"].astype(float)
    with open(path) as fh:
        lines = fh.readlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Wavelength")) + 1
    df = pd.read_csv(path, sep="\t", skiprows=start, names=["Wavelength", "Amplitude"])
    return df.Wavelength.values.astype(float), df.Amplitude.values.astype(float)


def test_core_files_identical() -> None:
    """The two fringe_core.py copies must be identical. This is the actual contract;
    everything below is just evidence that it holds.

    Compared as normalized TEXT, not bytes: this repo has core.autocrlf on, so the app's
    copy is checked out CRLF while the standalone stays LF. A raw byte compare would fail
    on every clean clone for a reason that has nothing to do with drift, and a test that
    cries wolf gets ignored -- which is precisely how the old frozen-number parity test
    ended up green while the port's carrier was wrong on every trace.
    """
    std, p = _load_standalone()
    if std is None:
        print(f"SKIP file-identity: standalone not found at {p}")
        return
    app_p = app_core.__file__
    a = open(p, encoding="utf-8").read().replace("\r\n", "\n")
    b = open(app_p, encoding="utf-8").read().replace("\r\n", "\n")
    same = a == b
    print(f"{'PASS' if same else 'FAIL'}  fringe_core.py identical across repos "
          f"({len(a.splitlines())} lines)")
    if not same:
        print(f"        standalone: {p}")
        print(f"        app       : {app_p}")
        print("        -> the copies have DRIFTED. Copy the standalone across WHOLE;")
        print("           do not hand-patch either side. That drift is what produced")
        print("           envelope offset 255 (truth 155) and carrier c1=0 in production.")
        import difflib
        for line in list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                              "standalone", "app", lineterm=""))[:40]:
            print("        " + line)
    assert same, "fringe_core.py has DRIFTED between the standalone and the app"


def test_tunable_defaults_are_not_copied() -> None:
    """Calibrated constants must be ALIASED from fringe_core, never retyped in the app.

    Identical math is not the whole contract. On 2026-07-19 the two fringe_core.py files
    were byte-identical and test_fit_parity still failed on all four real traces, by up to
    24.6 rad/nm of carrier -- because ``FitTunables.trunc_threshold`` was a hardcoded 0.40
    from an earlier calibration while the standalone had since moved TRUNC_THRESHOLD to
    0.30. Same code, different constants, different analysis. A hardcoded default is a
    second copy of the analysis wearing a different hat.

    Two checks, and the structural one is the one that matters. Comparing VALUES only tells
    you the copies agree today; requiring the app to write ``fc.TRUNC_THRESHOLD`` makes them
    unable to disagree tomorrow. That distinction is the entire lesson of this file.
    """
    import ast

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, "app_apps", "analysis", "phase_control", "subprocess", "domain")
    sources = {
        "FitTunables": os.path.join(base, "fringe_fit.py"),
        "FringeFitParams": os.path.join(base, "phase_stabilization_config.py"),
    }

    ok = True
    for cls_name, path in sources.items():
        tree = ast.parse(open(path, encoding="utf-8").read())
        cls = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
        assert cls is not None, f"{cls_name} not found in {path}"
        for stmt in cls.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            name = stmt.target.id
            if name not in TUNABLE_TO_CORE:
                continue
            want = TUNABLE_TO_CORE[name]
            d = stmt.value
            aliased = (isinstance(d, ast.Attribute) and d.attr == want
                       and isinstance(d.value, ast.Name))
            ok &= aliased
            got = ast.unparse(d) if d is not None else "<no default>"
            print(f"{'PASS' if aliased else 'FAIL'}  {cls_name}.{name} default is "
                  f"{got!r}" + ("" if aliased else
                                f" -- must be an alias of fringe_core.{want}, not a literal"))

    # ...and the values the app would actually pass must match the live standalone.
    std, _ = _load_standalone()
    if std is not None:
        t = FitTunables()
        for name, const in TUNABLE_TO_CORE.items():
            app_v, std_v = getattr(t, name), getattr(std, const)
            same = app_v == std_v
            ok &= same
            print(f"{'PASS' if same else 'FAIL'}  FitTunables().{name} = {app_v} "
                  f"vs standalone {const} = {std_v}")
    assert ok, "a calibrated constant is duplicated in the app instead of aliased"


def test_fit_parity() -> None:
    std, _ = _load_standalone()
    if std is None:
        import pytest; pytest.skip("standalone not importable")
    t = FitTunables()
    ok_all = True
    print(f"\n{'trace':26s} {'c1 (app)':>12s} {'c1 (standalone)':>16s} {'max|dc|':>10s} "
          f"{'ref_wl':>8s} {'status':>16s}")
    for name in TRACES + CSV_TRACES:
        path = os.path.join(STANDALONE_DIR, name)
        if not os.path.exists(path):
            print(f"{name:26s}  (missing, skipped)")
            continue
        lam, amp = _read(path)
        m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
        anchor = std.baseline_anchor(lam, amp)

        R = std.analyze(lam[m], amp[m], anchor=anchor)
        A = analyze_trace(lam[m], amp[m], t, anchor=anchor)

        if R.get("csig") is None:
            ok = (not A.accepted) and A.status == R["status"]
            print(f"{name:26s} {'--':>12s} {'--':>16s} {'--':>10s} {'--':>8s} "
                  f"{R['status']:>16s}  {'PASS' if ok else 'FAIL'}")
            ok_all &= ok
            continue

        d = np.abs(np.asarray(A.csig, float) - np.asarray(R["csig"], float))
        ok = (bool(np.all(d == 0.0))
              and A.status == R["status"]
              and A.ref_wl == R["ref_wl"]
              and A.trust_ok == R["trust_ok"])
        ok_all &= ok
        print(f"{name:26s} {A.csig[1]:12.6f} {R['csig'][1]:16.6f} {d.max():10.2e} "
              f"{A.ref_wl:8.2f} {A.status:>16s}  {'PASS' if ok else 'FAIL'}")
    assert ok_all


def test_operator_lambda_ref_is_honoured() -> None:
    """The operator's configured lambda_ref is the lock point and must be reported AT --
    not silently replaced by the fitted envelope centroid, which wanders frame to frame.
    It may only move when the data cannot support the phase there, and then ref_fallback
    must say so."""
    path = os.path.join(STANDALONE_DIR, "live_desktop_spectrum.csv")
    if not os.path.exists(path):
        print("SKIP lambda_ref: trace missing")
        return
    lam, amp = _read(path)
    m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
    std, _ = _load_standalone()
    anchor = std.baseline_anchor(lam, amp) if std else None
    t = FitTunables()

    r = analyze_trace(lam[m], amp[m], t, anchor=anchor, lambda_ref_nm=802.0)
    ok = r.accepted and (not r.ref_fallback) and r.ref_wl == 802.0
    print(f"\n{'PASS' if ok else 'FAIL'}  lambda_ref=802.0 honoured exactly "
          f"(got ref_wl={r.ref_wl:.4f}, fallback={r.ref_fallback})")

    # A different operator choice must be honoured too, and give a different phase.
    r2 = analyze_trace(lam[m], amp[m], t, anchor=anchor, lambda_ref_nm=804.0)
    ok2 = r2.accepted and r2.ref_wl == 804.0 and r2.csig == r.csig
    print(f"{'PASS' if ok2 else 'FAIL'}  lambda_ref=804.0 honoured, same underlying fit")

    # Omitting it falls back to the fitted centroid (the standalone/harness behaviour).
    r3 = analyze_trace(lam[m], amp[m], t, anchor=anchor)
    ok3 = r3.accepted and r3.ref_wl != 802.0 and abs(r3.ref_wl - 802.0) < 1.0
    print(f"{'PASS' if ok3 else 'FAIL'}  no lambda_ref -> fitted centroid "
          f"({r3.ref_wl:.4f})")
    assert ok and ok2 and ok3


def test_phase_and_shape_gates_are_separate() -> None:
    """The loop gate (c0) and the shape gate (c1..c3) must not be re-fused.

    Measured on 1240 harness traces: of the 13 fits a four-coefficient grader calls wrong,
    11 have a CORRECT phase and fail only on carrier/chirp -- quantities the stabilization
    loop never reads. Gating the loop on them declined 3.7% of good fits; splitting the gates
    took that to 0.0% at 99.84% phase accuracy, and made all seven real traces commit.

    da_15.95ga_-55.29 is the case that proves the split is load-bearing rather than cosmetic:
    its phase is supportable and its CHIRP is not (3*sigma/tol = 0.82 vs 2.49), so it must
    commit for stabilization AND be marked unverified for the GHz readout. One boolean cannot
    say both, which is the entire reason there are two.
    """
    path = os.path.join(STANDALONE_DIR, "da_15.95ga_-55.29.xls")
    if not os.path.exists(path):
        import pytest; pytest.skip("trace missing")
    std, _ = _load_standalone()
    if std is None:
        import pytest; pytest.skip("standalone not importable")

    lam, amp = _read(path)
    m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
    anchor = std.baseline_anchor(lam, amp)
    R = std.analyze(lam[m], amp[m], anchor=anchor, ref_primary=802.0)
    A = analyze_trace(lam[m], amp[m], FitTunables(), anchor=anchor, lambda_ref_nm=802.0)

    ok = R["trust_ok"] is True and R["shape_ok"] is False
    print(f"\n{'PASS' if ok else 'FAIL'}  da_15.95ga_-55.29: phase trusted={R['trust_ok']}, "
          f"shape trusted={R['shape_ok']} (must be True/False -- commits, readout unverified)")

    ok2 = A.trust_ok == R["trust_ok"] and A.shape_ok == R["shape_ok"]
    print(f"{'PASS' if ok2 else 'FAIL'}  the adapter carries both flags through unchanged")

    # The accept gate must consult the phase gate ONLY. Read via AST: importing the config
    # needs base_core, which is absent outside the instrument checkout.
    import ast
    cfg_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "app_apps", "analysis", "phase_control", "subprocess", "domain",
                         "phase_stabilization_config.py")
    tree = ast.parse(open(cfg_p, encoding="utf-8").read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "accepts"), None)
    assert fn is not None, "StabilizationConfig.accepts not found"
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    ok3 = "trust_ok" in attrs and "shape_ok" not in attrs
    print(f"{'PASS' if ok3 else 'FAIL'}  accepts() gates on trust_ok and NOT shape_ok"
          + ("" if ok3 else "  <- folding shape_ok back in restores the 3.7% false drops"))
    assert ok and ok2 and ok3


def test_rf_range_readout() -> None:
    """The GHz overlay: correct conversion, and the '>= 2 sig figs' formatting rule."""
    std, _ = _load_standalone()
    core = std if std is not None else app_core

    # 28.125 GHz per cycle/nm, from 9 nm ~ 320 ps assumed linear.
    ok = abs(core.GHZ_PER_CYC_PER_NM - 28.125) < 1e-9
    print(f"\n{'PASS' if ok else 'FAIL'}  9nm/320ps => {core.GHZ_PER_CYC_PER_NM} GHz per cycle/nm")

    # A pure carrier with no chirp is ONE frequency across the whole band.
    c1 = 2.0 * np.pi * 1.0                     # exactly 1 cycle/nm
    lo, hi = core.rf_range_ghz((0.0, c1, 0.0, 0.0), 802.0)
    ok2 = abs(lo - 28.125) < 1e-6 and abs(hi - 28.125) < 1e-6
    print(f"{'PASS' if ok2 else 'FAIL'}  1 cycle/nm, no chirp -> {lo:.3f}-{hi:.3f} GHz "
          f"(flat at 28.125)")

    # With a chirp the extreme is at a band EDGE, and an in-band null puts the minimum in
    # the MIDDLE -- an endpoints-only implementation would miss the zero. c1 + 2*c2*u = 0
    # at u = -c1/(2*c2); place that null 3 nm above the origin.
    c2 = -c1 / (2 * 3.0)
    lo2, hi2 = core.rf_range_ghz((0.0, c1, c2, 0.0), 802.0)
    ok3 = lo2 < 0.5                             # the null is found, not stepped over
    print(f"{'PASS' if ok3 else 'FAIL'}  chirped with an in-band null -> {lo2:.3f}-{hi2:.1f} "
          f"GHz (min must reach ~0 at the null)")

    cases = [(100.0, "100"), (20.4, "20"), (9.96, "10"), (1.52, "1.5"),
             (0.44, "0.44"), (0.037, "0.037")]
    ok4 = all(core.format_ghz(v) == want for v, want in cases)
    print(f"{'PASS' if ok4 else 'FAIL'}  format_ghz: "
          + ", ".join(f"{v}->{core.format_ghz(v)}" for v, _ in cases)
          + "  (nearest GHz, never under 2 sig figs)")

    ok5 = core.format_rf_range(12.0, 47.0, True) == "12-47 GHz"
    ok5 &= core.format_rf_range(28.125, 28.125, True) == "28 GHz"      # collapses
    ok5 &= "unverified" in core.format_rf_range(12.0, 47.0, False)
    print(f"{'PASS' if ok5 else 'FAIL'}  format_rf_range: "
          f"{core.format_rf_range(12.0, 47.0, True)!r} / "
          f"{core.format_rf_range(28.125, 28.125, True)!r} / "
          f"{core.format_rf_range(12.0, 47.0, False)!r}")
    assert ok and ok2 and ok3 and ok4 and ok5


def test_no_hidden_state() -> None:
    """Two fits of the same trace must be bit-identical (every fit is cold)."""
    path = os.path.join(STANDALONE_DIR, TRACES[0])
    if not os.path.exists(path):
        print("SKIP cold-fit determinism: trace missing")
        return
    lam, amp = _read(path)
    m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
    t = FitTunables()
    a = analyze_trace(lam[m], amp[m], t)
    b = analyze_trace(lam[m], amp[m], t)
    ok = a.csig == b.csig and a.ref_wl == b.ref_wl
    print(f"\n{'PASS' if ok else 'FAIL'}  two cold fits of the same trace are identical")
    assert ok


def test_reference_policy_hysteresis() -> None:
    """The reference must not chatter: REF_HYST consecutive traces to switch, both ways."""
    p = app_core.ReferencePolicy(hyst=5)
    seq = [True] * 3 + [False] * 4          # 4 bad frames: not enough to switch
    for v in seq:
        p.update(v)
    ok = p.fallback is False
    p.update(False)                          # the 5th consecutive bad frame switches
    ok &= p.fallback is True
    for _ in range(4):                       # 4 good frames: not enough to switch back
        p.update(True)
    ok &= p.fallback is True
    p.update(True)                           # the 5th switches back
    ok &= p.fallback is False
    print(f"{'PASS' if ok else 'FAIL'}  ReferencePolicy needs 5 consecutive traces, both ways")
    assert ok


def test_config_view_field_routing() -> None:
    """Every PhaseConfigView spec must resolve on exactly one of the two config
    dataclasses.

    PhaseConfigView._obj() routes a field name to either FringeFitParams or
    StabilizationConfig, and a mis-route is invisible until _populate runs at app START --
    which is how the v3 port shipped with `trust_nsig` routed to StabilizationConfig and
    crashed the whole panel window on launch. The view now derives that routing from the
    dataclass instead of hand-listing it; this test checks the assumption that derivation
    rests on (the two classes share no field names) and that every spec lands somewhere.

    Read via AST, not import: the view needs base_qt/PySide6 and the config needs
    base_core, neither of which is present outside the instrument checkout. A test that
    can only run in one place is a test that does not run.
    """
    import ast
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, "app_apps", "analysis", "phase_control")
    cfg_p = os.path.join(base, "subprocess", "domain", "phase_stabilization_config.py")
    view_p = os.path.join(base, "ui", "phase_config_view.py")

    tree = ast.parse(open(cfg_p, encoding="utf-8").read())

    def flds(cls_name: str) -> set[str]:
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and n.name == cls_name:
                return {s.target.id for s in n.body
                        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
        return set()

    params, config = flds("FringeFitParams"), flds("StabilizationConfig")
    overlap = params & config
    ok = not overlap
    print(f"\n{'PASS' if ok else 'FAIL'}  FringeFitParams/StabilizationConfig field names "
          f"are disjoint{'' if ok else f' -- AMBIGUOUS: {sorted(overlap)}'}")

    view = open(view_p, encoding="utf-8").read()
    specs = set(re.findall(r'^\s{8}"(\w+)":\s', view, re.M))
    ok &= bool(specs)
    unroutable = specs - params - config
    ok &= not unroutable
    print(f"{'PASS' if not unroutable else 'FAIL'}  all {len(specs)} config-view specs "
          f"resolve on a config object"
          f"{'' if not unroutable else f' -- {sorted(unroutable)} would crash _populate'}")
    for s in sorted(specs):
        print(f"        {s:22s} -> {'params' if s in params else 'config'}")
    assert ok


def test_truncation_recovery() -> None:
    """Every truncated instrument frame must COMMIT, and the scan must earn its cost.

    These files are the only truncation ground truth we have, and they are the test every
    synthetic harness failed to be: on 2026-07-17 the shipped detector scored 96.7%
    detection / 1 false positive in 145 synthetic traces while finding ZERO dead samples in
    a real one -- the harness builds envelopes at bump/noise ~20 where the instrument runs
    at ~411, so a real trace's ordinary 2% envelope error is 8 sigma there and 0.4 sigma in
    synth. The harness could not fail. These files can.

    Two separate contracts, and it matters that they are separate:

    1. COMMIT (all four traces). Truncated or not, the frame must produce a trusted fit.
       This is the contract the app actually depends on and it holds regardless of HOW the
       fit got there.
    2. The scan is LOAD-BEARING on at least one trace, and IDLE on the clean ones. A
       recovery scan costs ~18 extra fits, so a test that never exercises it is not
       protecting anything -- and one that demands it everywhere goes vacuous the moment
       the ordinary path improves.

    That is not hypothetical: this test previously asserted ``truncated.csv`` must recover.
    When TRUNC_THRESHOLD tightened to 0.30 the contrast crop started removing that clip on
    its own (rms_frac 0.103, r2_fringe 0.973, no scan) and the assertion became a demand
    that the pipeline be WORSE. ``want_recover`` is therefore measured per trace, and only
    ``2020607181645_truncated.csv`` -- a left-arm clip deep inside the core, where the
    primary fit lands at rms_frac 0.338 / r2 0.730 -- still needs the scan. If a future
    change makes that one fit first time as well, flip its flag and say so here; do not
    reintroduce a cut to keep a test green.
    """
    std, _ = _load_standalone()
    if std is None:
        import pytest; pytest.skip("standalone not importable")

    # (trace, must the recovery scan fire?) -- measured 2026-07-19 at TRUNC_THRESHOLD=0.30.
    CASES = (
        ("truncated.csv", False),                 # clip cropped away by the contrast cut
        ("lightly_truncated.csv", False),         # clip outside the core entirely
        ("2020607181645_truncated.csv", True),    # left-arm clip inside the core: needs it
        ("live_desktop_spectrum.csv", False),     # clean control: must never scan
    )

    ok_all = True
    seen_recovery = False
    print(f"\n{'trace':30s} {'scan':>6s} {'want':>6s} {'rms_frac':>9s} {'r2_fringe':>10s} "
          f"{'c1':>9s}  verdict")
    for name, want_recover in CASES:
        path = os.path.join(STANDALONE_DIR, name)
        if not os.path.exists(path):
            print(f"{name:30s}  (missing, skipped)")
            continue
        lam, amp = _read(path)
        m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
        anchor = std.baseline_anchor(lam, amp)
        R = std.analyze(lam[m], amp[m], anchor=anchor, ref_primary=802.0)

        rec = bool(R.get("recovered"))
        rf = float(R.get("rms_frac", float("inf")))
        r2f = float(R.get("r2_fringe", float("nan")))
        committed = (R.get("status") == "ok" and bool(R.get("trust_ok")) and rf < 0.30)
        ok = committed and (rec == want_recover)
        ok_all &= ok
        seen_recovery |= rec
        c1 = R["csig"][1] if R.get("csig") is not None else float("nan")
        why = ""
        if not ok:
            why = ("  <- must COMMIT (trusted, rms_frac<0.30)" if not committed
                   else f"  <- scan fired={rec}, expected {want_recover}")
        print(f"{name:30s} {str(rec):>6s} {str(want_recover):>6s} {rf:9.3f} {r2f:10.3f} "
              f"{c1:9.3f}  {'COMMIT' if committed else R.get('status')}  "
              f"{'PASS' if ok else 'FAIL'}{why}")

        # Where the scan fired, prove it is what rescued the frame -- otherwise we are
        # paying ~18 fits for nothing and would never notice.
        if rec:
            off = std.analyze(lam[m], amp[m], anchor=anchor, ref_primary=802.0,
                              recover=False)
            rf_off = float(off.get("rms_frac", float("inf")))
            r2_off = float(off.get("r2_fringe", float("nan")))
            load_bearing = rf_off > 0.30 and rf_off > 2.0 * rf
            ok_all &= load_bearing
            print(f"{'  ^ same trace, recover=False':30s} {'-':>6s} {'-':>6s} "
                  f"{rf_off:9.3f} {r2_off:10.3f} {'-':>9s}  "
                  f"{off.get('status')}  {'PASS' if load_bearing else 'FAIL'}"
                  + ("" if load_bearing
                     else "  <- primary fit already explains it; the scan is vacuous here"))

    ok_all &= seen_recovery
    if not seen_recovery:
        print("FAIL  the recovery scan never fired on ANY trace -- it is untested code")
    assert ok_all


if __name__ == "__main__":
    # Run every test, report the failures, exit non-zero if any failed.
    #
    # Do NOT go back to `results = [test_a(), test_b(), ...]; all(results)`. These tests
    # signal failure by RAISING (assert), as pytest requires -- under that list form the
    # first failure aborts the run, and worse, a passing test returns None, so `all()` was
    # False and the runner printed "PARITY FAILED" with every single check reading PASS.
    # The old `return <bool>` style avoided that but made the tests unable to fail under
    # pytest at all (it only warns on a returned value). Raise for pytest, catch here.
    failures = []
    for fn in (
        test_core_files_identical,
        test_tunable_defaults_are_not_copied,
        test_fit_parity,
        test_operator_lambda_ref_is_honoured,
        test_phase_and_shape_gates_are_separate,
        test_rf_range_readout,
        test_no_hidden_state,
        test_reference_policy_hysteresis,
        test_config_view_field_routing,
        test_truncation_recovery,
    ):
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e or 'assertion failed'}")
        except Exception as e:                       # noqa: BLE001 - report, don't mask
            failures.append(f"{fn.__name__}: {type(e).__name__}: {e}")

    print()
    if not failures:
        print("ALL PARITY TESTS PASS")
        sys.exit(0)
    print(f"PARITY FAILED ({len(failures)} of 10)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

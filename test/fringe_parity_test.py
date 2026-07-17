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
CSV_TRACES = ["live_desktop_spectrum.csv"]


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


def test_core_files_identical() -> bool:
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
        return True
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
    return same


def test_fit_parity() -> bool:
    std, _ = _load_standalone()
    if std is None:
        print("SKIP fit parity: standalone not importable")
        return True
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
    return ok_all


def test_operator_lambda_ref_is_honoured() -> bool:
    """The operator's configured lambda_ref is the lock point and must be reported AT --
    not silently replaced by the fitted envelope centroid, which wanders frame to frame.
    It may only move when the data cannot support the phase there, and then ref_fallback
    must say so."""
    path = os.path.join(STANDALONE_DIR, "live_desktop_spectrum.csv")
    if not os.path.exists(path):
        print("SKIP lambda_ref: trace missing")
        return True
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
    return ok and ok2 and ok3


def test_no_hidden_state() -> bool:
    """Two fits of the same trace must be bit-identical (every fit is cold)."""
    path = os.path.join(STANDALONE_DIR, TRACES[0])
    if not os.path.exists(path):
        print("SKIP cold-fit determinism: trace missing")
        return True
    lam, amp = _read(path)
    m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
    t = FitTunables()
    a = analyze_trace(lam[m], amp[m], t)
    b = analyze_trace(lam[m], amp[m], t)
    ok = a.csig == b.csig and a.ref_wl == b.ref_wl
    print(f"\n{'PASS' if ok else 'FAIL'}  two cold fits of the same trace are identical")
    return ok


def test_reference_policy_hysteresis() -> bool:
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
    return ok


def test_config_view_field_routing() -> bool:
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
    return ok


def test_truncation_recovery() -> bool:
    """The instrument's own truncated frame must commit, and clean frames must not scan.

    This is the ONLY ground truth we have for truncation, and it is the test every
    synthetic harness failed to be. On 2026-07-17 the shipped detector scored 96.7%
    detection / 1 false positive in 145 while finding ZERO dead samples in this trace --
    the harness builds Gaussian envelopes at bump/noise ~20 and the instrument runs at
    ~411, so a real trace's ordinary 2% envelope error is 8 sigma there and 0.4 sigma in
    synth. The harness could not fail. These two files can.

    truncated.csv        : clip at ~800.3 nm, INSIDE the fit core -> must be cut and commit
    lightly_truncated.csv: clip outside the core -> the contrast cut already handles it,
                           so the fit must pass first time and never scan
    """
    here = os.path.join(STANDALONE_DIR, "truncated.csv")
    if not os.path.exists(here):
        print("SKIP truncation recovery: truncated.csv missing")
        return True
    std, _ = _load_standalone()
    if std is None:
        print("SKIP truncation recovery: standalone not importable")
        return True

    ok_all = True
    print(f"\n{'trace':24s} {'recovered':>10s} {'rms_frac':>9s} {'r2_fringe':>10s} "
          f"{'c1':>9s}  verdict")
    for name, want_recover in (("truncated.csv", True), ("lightly_truncated.csv", False)):
        path = os.path.join(STANDALONE_DIR, name)
        if not os.path.exists(path):
            print(f"{name:24s}  (missing, skipped)")
            continue
        lam, amp = _read(path)
        m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
        anchor = std.baseline_anchor(lam, amp)
        R = std.analyze(lam[m], amp[m], anchor=anchor, ref_primary=802.0)
        rec = bool(R.get("recovered"))
        rf = float(R.get("rms_frac", float("inf")))
        r2f = float(R.get("r2_fringe", float("nan")))
        committed = (R.get("status") == "ok" and bool(R.get("trust_ok")) and rf < 0.30)
        ok = (rec == want_recover) and committed
        ok_all &= ok
        c1 = R["csig"][1] if R.get("csig") is not None else float("nan")
        print(f"{name:24s} {str(rec):>10s} {rf:9.3f} {r2f:10.3f} {c1:9.3f}  "
              f"{'COMMIT' if committed else R.get('status')}  {'PASS' if ok else 'FAIL'}"
              + ("" if ok else f"  <- wanted recovered={want_recover} and a commit"))

    # ...and the scan must be what makes the difference, not a coincidence.
    lam, amp = _read(here)
    m = (lam >= ZOOM[0]) & (lam <= ZOOM[1])
    anchor = std.baseline_anchor(lam, amp)
    off = std.analyze(lam[m], amp[m], anchor=anchor, ref_primary=802.0, recover=False)
    rf_off = float(off.get("rms_frac", float("inf")))
    ok = rf_off > 0.30                      # without the scan this frame IS dropped
    ok_all &= ok
    print(f"{'  (recover=False)':24s} {'-':>10s} {rf_off:9.3f} "
          f"{float(off.get('r2_fringe', float('nan'))):10.3f} "
          f"{off['csig'][1] if off.get('csig') is not None else float('nan'):9.3f}  "
          f"{off.get('status')}  {'PASS' if ok else 'FAIL'}"
          + ("" if ok else "  <- the scan is not what rescues this frame; test is vacuous"))
    return ok_all


if __name__ == "__main__":
    results = [
        test_core_files_identical(),
        test_fit_parity(),
        test_operator_lambda_ref_is_honoured(),
        test_no_hidden_state(),
        test_reference_policy_hysteresis(),
        test_config_view_field_routing(),
        test_truncation_recovery(),
    ]
    print()
    if all(results):
        print("ALL PARITY TESTS PASS")
        sys.exit(0)
    print("PARITY FAILED")
    sys.exit(1)

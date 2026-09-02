# XCORR Program — Motion Control & Device Resilience (Maintainer Guide)

How the XCORR scan drives the three ESP301 stages and how it handles — and will recover
from — device/serial failures.

**Scope.** This covers motion control, timeouts/aborts, failure surfacing and recovery. It
does **not** document the scan planner math, the HDF5 storage format, or the frequency
analysis. For the ESP301 driver and its serial/USB transport (including the bridge-wedge
failure mode), see `control_readout/esp_301/esp_301.md` in the **Devices** repo.

---

## 1. The three axes and the scan's drive loop

Roles are bound by the `XcorrRoutine` constructor signature — there is no axis-role enum
(`config.py`, `routine.py`):

| Role | Axis | Stage | Purpose | Soft limits (mm) |
|---|---|---|---|---|
| `probe` | 1 | FMS300PP | scanned axis | `-9.5 … 290.5` |
| `delay` | 2 | MFA-CC | central frequency | `0.0 … 25.0` |
| `grating` | 3 | UTS150CC | chirp difference | `-75.0 … 75.0` |

`AXIS_LIMITS` (config.py) were read live from the ESP301 (`SL?`/`SR?`, 2026-07-19). The
whole run executes on **one** `BaseRoutine` `TaskRunner` thread, wrapped in `try/finally`
for the flush-and-park guarantee. Per setpoint:

1. `_move(grating)` → `_move(delay)` — **grating first**, because the delay position tracks
   the grating (`commanded = base + slope·grating + intercept`).
2. `_sweep_probe` — for each probe point: `_move(probe)` (commanded =
   `probe_base + grating + probe_intercept`) then `_acquire_point` (**stubbed**, returns
   zeros in Build Step 1).
3. `writer.write_group(...)` — one HDF5 group per setpoint, flushed before the next.

### How a move actually reaches the controller

`_move` → `handle.move_to(pos, on_done, on_error)` → IPC to the `control_readout`
subprocess → `ESP301Controller.move_absolute` → `wait_for_motion` (polls `MD?`). `_call`
turns that async request into a **blocking, reply-correlated** call: it waits on a
`threading.Event` until `on_done`/`on_error` fires or `timeout_s` elapses. This is the only
handle in the repo that uses the reply callbacks — every other `_on_reply` is `pass`.

---

## 2. Timeouts, aborts, and two framework holes worked *with*

### The timeout ordering invariant — do not break it

- `XcorrConfig.timeout_s` = **130 s** — the routine's per-move reply wait (`_call`).
- The driver's `wait_for_motion` timeout = **120 s** (a genuinely stuck axis).

**130 > 120 is deliberate and must stay that way.** The driver must be the one to time out
first and return a structured error; if `timeout_s` were the shorter of the two the routine
would raise a false *"no reply"* while the move is still legitimately running. With the
resync fix, a *comms fault* now fails in **seconds** (not 120 s), so this margin only
matters for the true stuck-axis case — but keep it.

### Abort (defects G15/G16) — accepted, not fixed

- `abort()` sets `_abort` (a `threading.Event` set from the **caller's** thread, never
  dispatched — a dispatched abort would queue *behind* the loop it must stop).
- It is checked only at **probe points**. An in-flight move **cannot** be interrupted:
  `Device._lock` *is* `controller._lock`, held across the blocking `wait_for_motion`. So
  abort takes effect at the next probe point; the current group is flushed with
  `status="aborted"`. **Do not** engineer around this without revisiting the decision in
  `routine.py`'s module docstring.

### Start (defect A11 / G12) — RUNNING ≠ live link

`_start_handles` starts the three workers and polls for `WorkerStatus.RUNNING` (20 s).
**Critical caveat:** the ESP301 connection failure is **non-fatal**, so a stage can reach
RUNNING having *registered without a working serial link*. RUNNING is a worker-lifecycle
state, not proof the COM7 link answers. The first real move is what exercises the link.

---

## 3. How a device/serial failure surfaces in a run

The failure path, end to end:

1. Driver raises `ESP301Error("... communication fault, not a stuck stage ... Check the
   COM7 link, not the mechanics.")` in the subprocess (see `esp_301.md` FM-3) — fast, on a
   silent link (FM-1 residual or FM-2 wedge).
2. That becomes the move's `on_error` → `_call` raises `XcorrError`.
3. `_run_scan`'s `except` catches it → publishes **`XcorrFailed`** (with the run path and
   `n_groups_written`), the HDF5 file is closed, `_running` cleared.

**Net effect:** the run stops cleanly, and **every completed setpoint group is preserved**
on disk up to the failure. A full bridge wedge (FM-2) mid-scan surfaces this way within
seconds now, versus a 120 s hang per move pre-fix.

**Log triage:** *"communication fault … Check the COM7 link"* → it's the **link**
(re-enumerate, §4), not the mechanics. A `Timed out waiting for axis N` after ~120 s →
a genuinely stuck stage (should be rare post-fix).

---

## 4. Recovery

### Manual (today)

1. Run ends in `XcorrFailed`. Re-enumerate the ESP301 (`esp_301.md` §4 — replug or
   elevated `pnputil /restart-device`).
2. Restart the scan.

> **Limitation — no resume-from-setpoint.** A restart re-plans from the config and
> re-walks the grid from the beginning; there is no built-in "continue from setpoint k".
> To skip completed ground, narrow the config ranges by hand. Positions survive the
> re-enum (`esp_301.md` §4), so no re-home is needed either way.

### Planned auto-recovery watchdog — **PARKED** (build after prevention is settled)

Operator decision 2026-07-21: **scheduled-task delegation** for elevation (not a
whole-process-elevated run).

- **Elevation:** pre-create (once, with admin) a Scheduled Task set to *Run with highest
  privileges* that runs
  `pnputil /restart-device "USB\VID_104D&PID_3001\0000000000000000"`. The unelevated scan
  triggers it on demand via `schtasks /run /tn <task>` — no UAC prompt, so it works
  unattended (the 50 h scan requirement).
- **Flow:** catch the comms-fault `XcorrError` → close/release COM7 → trigger the re-enum
  task → poll for COM7 to re-appear → reconnect → verify `1TP` → **resume from the current
  setpoint** (positions retained, no re-home).
- **Guard rail:** cap re-enums (e.g. 3/hour); beyond that, fail loudly — a genuinely dead
  link must not loop forever.
- **Dependencies:** requires (a) resume-from-setpoint in the routine (not present today, see
  §4 limitation) and (b) the FM-2 prevention finding from the H1–H5 test-to-failure
  (`Docs/XCORR_WEDGE_TESTING_20260721.md`). **Prevention is primary — the watchdog is the
  seatbelt.** Build both.

---

## 5. Config knobs relevant to motion & resilience (`config.py`)

| Field | Default | Note |
|---|---|---|
| `timeout_s` | 130.0 | per-move reply wait; **must exceed** the driver's 120 s (§2) |
| `settle_s` | 0.0 | explicit dwell after each move before acquiring — don't trust `MD?` settling |
| `n_traces` | 10 | software-averaged (the TDS2012C's `NUMAVg` only accepts 4/16/64/128) |
| `AXIS_LIMITS` | — | soft limits; the planner validates every corrected setpoint against these |
| `SCOPE_RESOURCE` | — | TDS2012C USBTMC (not the TBS2012C — defect G8) |

---

## 6. Provenance / pointers

- `control_readout/esp_301/esp_301.md` (Devices) — the ESP301 driver & transport guide.
- `Devices-esp301-fix/ESP301_SERIAL_FIX.md` — resync fix + post-mortem.
- `Docs/XCORR_WEDGE_TESTING_20260721.md` — FM-2 bridge-wedge test-to-failure (H1–H5).
- `Docs/XCORR_SESSION_20260721.md` — session handoff (also covers the §2/§3 scan features).
- Code: `app_apps/routines/xcorr/routine.py`, `config.py`.

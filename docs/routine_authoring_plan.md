# Plan — Physicist-friendly routine authoring + LLM-automation roadmap

> Design + roadmap for making routines writable in minutes by non-OOP physicists (and weak
> LLMs), and for the lab-automation tiers that build on that surface. Companion to
> [experiment_physics.md](experiment_physics.md) (the action grammar + physics) and the
> decision log in [summary.md](summary.md). Status: **design recorded; not yet built.**
> Branch: `feature/routine-authoring`. Last updated: 2026-06-12.

---

## 1. Problem

The backend is excellent but the *routine API is too verbose for the target user.* A trivial
"move stage → wait → read scope → save" routine today is **~250–350 lines across 5 files** and
demands ~10 framework concepts (Routine/Step subclasses, DI modules, EventBus pub/sub, frozen
event dataclasses, TaskRunner callbacks, stale-callback guards, manual unsubscription,
shared-buffer slot acks, lifecycle hooks). The intended authors are **physicists who script
fluently but don't think in async state machines** — and a weak LLM can't reliably emit that
boilerplate either.

## 2. The idea — a linear, blocking authoring layer

Let a routine be a **plain top-to-bottom function**:

```python
@routine("delay_freq_sweep")
def delay_freq_sweep(lab, start_mm, stop_mm, step_mm):
    for x in lab.frange(start_mm, stop_mm, step_mm):
        lab.delay.move_to(x)          # BLOCKS until settled
        spec = lab.spectrometer.read()
        info = lab.fit_spectrum(spec)
        lab.record(delay=x, nu0=info.nu0_thz, span=info.nu_start_thz - info.nu_end_thz)
    lab.save("delay_freq_sweep.csv")
    lab.plot("delay", "nu0")
```

~10 lines, no subclasses/callbacks/event-dataclasses/DI. This same closed verb set is exactly
what a voice or autonomous LLM targets (§6).

## 3. The async→sync bridge (core mechanism) — and why it's safe

The framework is asynchronous (fire command → reply arrives later on a callback). The bridge
lets the author *write* synchronously while the system *runs* asynchronously. Each blocking
verb: (1) subscribe to the completion/telemetry event **first**, (2) emit the command over IPC,
(3) block on a `threading.Event` in a `wait(POLL)` loop checking cancel + timeout, (4) return
the payload, (5) unsubscribe in `finally`.

**Verified safe against the real threading model** (read during planning):
- `base_core/ipc/service_connector.py` — device replies (`on_reply`) and telemetry events
  (`bus.publish`) are delivered on a **dedicated IPC reader thread**, never the routine's own
  thread.
- `base_core/framework/events/event_bus.py` — `publish` runs handlers synchronously on the
  publisher's thread under an `RLock`; `subscribe/unsubscribe` are thread-safe.

So a routine on its own background thread (an existing `TaskRunner` thread) can block while a
*different* thread (the reader) wakes it. **No deadlock.** Strictly additive — uses only public
EventBus / handle / TaskRunner APIs; **no edits to Base_Core/Base_Qt/Devices framework.**

**Hard invariant:** the routine thread must never `bus.publish(X)` and then wait on `X` on
itself (synchronous publish → self-deadlock). Enforced by construction — authors never touch
the bus; only the `lab` facade does, and it only *emits over IPC* while *waiting on
reader-thread events*.

## 4. Design

### 4.1 The `lab` facade
Built once per run from DI handles + bus + cancel token + recording sink. Verb table and the
full vocabulary live in [experiment_physics.md §2.7](experiment_physics.md). Two readout
verbs reflect the real data flow: `lab.scope.capture()` / `lab.xcorr_point()` (CH1 photodiode,
mean of top-20 samples) and `lab.spectrometer.read()` (SPM-002, direct). `lab.save` → **CSV**
(human-accessible). Facade name = **`lab`**.

### 4.2 Completion signals — RESOLVED at build (no Devices changes needed)
Original plan was to add `*Complete` events to the Devices workers. On reading the workers
this proved **unnecessary**: the command workers (RGV/picomotor/servo) handle commands
**synchronously** — they call the blocking driver method, emit telemetry, *then* reply OK — so
OKReply already means "settled" and each already emits a usable completion event. The facade
therefore awaits the existing telemetry: ESP301 `MoveComplete(axis)` (poll thread), HWP
`HwpAngleUpdate`, picomotor `StepsMoved(axis)`, servo `ArmStateChanged(arm)`, scope
`TraceAvailable`, spectrometer `SpectrumAvailable`. **Zero Devices-repo edits.** (Limitation:
a failed command surfaces as a `RoutineTimeout` rather than a typed error, since error replies
aren't currently forwarded to the bus — a future refinement.)

### 4.3 Registration without a BaseModule
`@routine("name")` writes into a module-level registry (import side-effect). Author scripts
live in `app_apps/routines/linear/scripts/`; the package `__init__` imports them so they
self-register. **One** `LinearRoutinesModule(BaseModule)` (written once) registers a single
`LinearRoutineRunner(Routine)` and adds `runner.stop` to lifecycle, added once to `app.py`'s
`modules=[...]`. After that authors never edit `app.py`/DI/modules.

### 4.4 Runner, cancellation, ownership, cleanup
`LinearRoutineRunner(Routine)` adapts each registered function to the app lifecycle (start/stop,
`is_running` for UI) exactly like `CentrifugeCalibrationRoutine`. `start()` is single-flight
(v1), builds a fresh `Lab`, acquires `StageOwnership` + registers the scope consumer, submits
`fn(lab, **params)` to a dedicated 1-worker `TaskRunner`. `stop()` sets the cancel token
(cooperative; each blocking primitive checks every ~50 ms and raises `RoutineCancelled`).
`try/finally` teardown (success/error/cancel) releases stages, unregisters the scope consumer
(auto-acks pending slots), drops subscriptions. `lab.scope.capture()` always `TraceAck`s in its
own `finally`.

### 4.5 File layout (additive only)
```
app_apps/routines/linear/
  registry.py   cancel.py   bridge.py   lab.py   runner.py   module.py
  scripts/      # self-registering author functions
tests/routines/linear/   # test_bridge / test_lab / test_runner (fake handles, no subprocess)
```

### 4.6 Known limitations (v1, documented)
Single-flight (one routine at a time — avoids cross-routine event mismatch on shared `axis`
keys; concurrency later needs correlation IDs). Cooperative cancel (bounded at device waits;
long pure-CPU sections cancel only at `lab.checkpoint()`). Wedged author CPU can't be
force-killed without killing the process. Every blocking primitive has a **timeout** so a
crashed subprocess can't hang a routine forever.

## 5. Build order (separate go-ahead)
1. `cancel.py` + `bridge.py` (test against real EventBus, publish from a 2nd thread).
2. `registry.py`.
3. `lab.py` (verb table + add `*Complete` events to Devices workers, §4.2).
4. `runner.py` (ownership/consumer teardown).
5. `module.py` + one `app.py` line.
6. Example scripts incl. an **overnight ν_start/ν_end scan** (validate-and-repeat).
7. Tests with fake handles that publish completion events from a timer thread (no subprocesses).

Plus two docs: a **routine authoring guide** (write-a-routine-in-5-minutes, full verb reference,
pasteable as LLM context) and this design as the standing reference.

---

## 6. LLM-automation tiers — merit assessment + roadmap

Framing: like robotics "LLM-as-high-level-controller over low-level actuators," the routine
verb set **is** the low-level action API. Honest question: *how far up does automation pay?*

| Tier | What it is | Real merit | Verdict |
|---|---|---|---|
| **T0 — Named verbs (the DSL)** | physicists/scripts call `lab.*`; deterministic | **High, immediate.** Unblocks overnight scans (probe *scanning* not stepping → est. 3–4× data), removes the OOP barrier. No LLM. | **Build first** — foundation for everything above. |
| **T1 — Voice / standby trigger** | weak LLM maps an utterance → a *registered* routine + params, **confirms**, runs it | **Moderate, high-leverage / low-risk.** Pure dispatch over a closed verb set + human confirm before actuation; hallucination bounded (can only pick existing routines). Convenience, not autonomy. | **Strong candidate** once T0 exists. Cheap, safe, useful at the bench. |
| **T2 — Supervised planner** | LLM *composes* verbs into a new routine from a physics goal; human reviews the generated script before it runs | **Real but conditional.** Useful for exploratory parameter spaces; depends on a good physics doc + a human gate. Risk: subtly wrong sequences, hardware safety. | **Worthwhile with guardrails** (dry-run, ownership limits, param clamps, human approval). |
| **T3 — Fully autonomous loop** | LLM plans → runs → reads results → re-plans overnight, no human | **Narrow merit, high risk.** Autonomous wet labs work because of cheap parallel trials + strong objective functions; a single beamline with expensive alignment and high per-failure cost **inverts** that economics (low trial throughput, costly failures). Merit exists **only** as tightly-bounded, well-instrumented optimization (e.g. auto-maximize an alignment metric over ν_start/ν_end within hard limits) — closed-loop optimization with an LLM supervisor, not open-ended "do science." | **Defer / scope tightly.** Pursue only as bounded optimization with hard interlocks; treat open-ended autonomy as research, not a deliverable. |

**Bottom line:** durable value is **T0 + T1** (plus the overnight scripted automation they
enable). T2 is worth it *with a human gate*. T3's value is real only as constrained closed-loop
optimization. Every tier rides the **same DSL substrate + physics doc**, so building T0 well is
the highest-leverage move regardless of how far up we go. Safety pattern is constant across
tiers: **closed verb set, stage-ownership guard, parameter clamps, human confirmation before
actuation, dry-run mode.**

## 7. Status
- ✅ Design recorded (this doc); physics/action-grammar doc written
  ([experiment_physics.md](experiment_physics.md)).
- ✅ Branches renamed & pushed: App_Apps `feature/io-control-analysis`, Devices
  `feature/device-drivers`; this work on App_Apps `feature/routine-authoring`.
- ✅ **Linear routine layer COMPLETE** (R.1–R.7): bridge (`778cee1`), registry (`57fa3c8`),
  lab facade (`b857db6`), runner+events (`b7b7ead`), module+app wiring (`97bb221`), example
  scripts (`3894d3b`), authoring guide ([routine_authoring_guide.md](routine_authoring_guide.md)).
  No Devices changes needed (§4.2). Verify command `scripts/check.py` + read-only verifier
  agent (`.claude/agents/verifier.md`) added; full check 131 green, mypy clean. Verifier-audited.
- ⬜ T1+ LLM tiers — roadmap only (the authoring guide §6 is the LLM action spec they'd use).
- Runs live in the app once the contributor's `main` breakages (`elliptec.base`,
  `base_qt.ui.apply`) are fixed; our wiring is ready and inert until then.

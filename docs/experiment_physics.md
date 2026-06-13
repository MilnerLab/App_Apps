# usCFG — Experiment & Physics Reference

> Two-layer document.
> **Part 1** is a plain-language onboarding read: what the experiment does and why the
> software has the knobs it has. **Part 2** is a terse, structured machine reference —
> hardware map, control variables, ranges, and the `lab.*` verb vocabulary — written to be
> pasted directly into an LLM's context (for routine generation) or used as a lookup table.
>
> Sources: lab knowledge (flagged **[LAB]**) and two papers — the constant-frequency /
> "ultraslow" centrifuge ([arXiv:2507.12689](https://arxiv.org/abs/2507.12689)) and molecular
> rotation in helium nanodroplets ([PRL, arXiv:2509.02913](https://arxiv.org/abs/2509.02913)).
> Cross-refs: [summary.md](summary.md) (decision log), [architecture.md](architecture.md),
> [tasks.md](tasks.md). Last updated: 2026-06-12.

---

# Part 1 — How the experiment works

## 1.1 The optical centrifuge, in one paragraph

An **optical centrifuge** is an intense laser pulse whose **linear polarization vector spins**
around the propagation axis. A molecule in the field develops an induced dipole; the field
exerts a torque that drags the molecule's most-polarizable axis along with the rotating
polarization, **forcing the molecule to rotate** and climbing it up a ladder of rotational
states. By controlling how the polarization spins in time, we control the molecule's rotation.

## 1.2 This lab's twist: a *constant-frequency* (zero-acceleration) centrifuge

A conventional centrifuge **accelerates** the rotation (~100 GHz/ps). This lab builds the
field differently so it can rotate at a **constant, tunable frequency** with essentially
**zero angular acceleration** ([arXiv:2507.12689](https://arxiv.org/abs/2507.12689)):

- Start from a **frequency-chirped pulse** (its instantaneous optical frequency sweeps in
  time).
- Split it in a **Michelson interferometer** into two arms, given **opposite circular
  polarizations**.
- A controllable **time delay** between the arms means that, at any instant, the two arms
  carry slightly different optical frequencies. Their interference produces a **linearly
  polarized field that rotates** at frequency `f_CFG = ½ · Δf`, where `Δf` is the
  instantaneous frequency difference between the arms.
- Because the chirp is (to first order) linear, `Δf` — and therefore `f_CFG` — is **constant
  in time**. Adjusting the chirp rate and the inter-arm delay sets the rotation frequency.

So the experimental "rotation frequency" is set optically, by how the two chirped arms are
arranged — not by a mechanical spinner.

## 1.3 What we're after this round

**[LAB]** Previous work sat in a low-frequency window (the ultraslow paper scanned ~8.5–17
GHz). This round pushes **much higher — terminal rotation frequency up to ~200 GHz, starting
from ~0 GHz** — to excite **higher rotational states** and probe regimes that may **approach
the Landau critical velocity** (the superfluid-helium threshold above which a moving impurity
can shed excitations). Higher final frequency = higher rotational excitation.

**[LAB] Molecules:** primarily **NO dimers**; possibly also **OCS** and **CS₂**. The choice
doesn't materially change the control software.

## 1.4 The knobs, and why each exists

The spinning field is characterized by a few physical quantities; each maps onto a piece of
hardware the software drives:

- **Central frequency** of the rotation — set mainly by the **delay stage** (path length of
  the delay arm).
- **Chirp rate / frequency span** (how the start and end frequencies differ) — set mainly by
  the **grating stage** (grating separation).
- **Terminal (final) frequency** — set mainly by **truncating** the pulse in time (the
  **truncation stage**): cut the chirp earlier or later and you stop the frequency sweep at a
  different value.
- **Initial rotation phase / polarization orientation** — set by the **half-wave plate (HWP)**
  on the RGV100BL rotator.
- **Polarization ellipticity** (how cleanly linear the rotating field is) — trimmed by the
  **quarter-wave plate (QWP)** on the ELL14 rotator. A perfectly linear rotating field gives
  the cleanest signal; the QWP minimizes the unwanted component.

These are the **dominant** correlations, each with an **offset and some cross-coupling** —
moving one stage nudges more than one quantity. That is exactly why the software fits the
*measured* spectrum and closes feedback loops on the fitted quantities rather than trusting an
open-loop dial.

## 1.5 How we read the field out

Two independent readouts:

1. **Spectrometer (Photon Control SPM-002).** **[LAB]** Connects **directly to the computer**
   (not through the oscilloscope). It records the **interferometric spectrum** of the
   centrifuge field — which appears as a **sinusoidal fringe pattern bounded by an envelope**.
   Fitting that pattern recovers the central frequency, the start/end frequencies, the chirp,
   the envelopes, and the fringe phase. This is the primary, one-shot characterization of the
   field.

2. **Cross-correlation (XCORR) via oscilloscope.** **[LAB]** Oscilloscope **channel 1** carries
   a **photodiode** signal. The XCORR curve is built **point by point**: at each **probe-delay**
   position, capture a scope trace, take the **mean of the 20 highest samples**, and plot that
   scalar against the probe delay. Sweeping the probe delay traces out a bounded sinusoid in
   the *time* domain, giving a wavelength↔probe-delay calibration. XCORR is a slower,
   occasional characterization — not part of the daily loop.

There is **[LAB] no hardware position-sync**: the probe-stage position is associated with each
trace in software (timestamp + interpolation), not via an electrical sync line.

## 1.6 How the molecules are observed (context only)

The software does **not** control molecular detection, but for interpreting results: the rotating
molecules are measured by **Coulomb explosion** — an intense **~120 fs** probe pulse ionizes the
target, and **time-of-flight velocity-map imaging (TOF-VMI)** records the fragment angular
distribution. The alignment metric **⟨cos²θ₂D⟩** oscillates at **2·f_CFG** while the molecule
follows the field, and a long **field-free** rotation persists for **~ns** after the field turns
off ([arXiv:2507.12689](https://arxiv.org/abs/2507.12689),
[arXiv:2509.02913](https://arxiv.org/abs/2509.02913)).

---

# Part 2 — Machine reference

> Terse, structured, and LLM-pasteable. The `lab.*` names are the action grammar a routine
> author (human or LLM) uses; see [routine_authoring_plan.md](routine_authoring_plan.md).

## 2.1 Hardware inventory

| Hardware | Driver / package | Role | Software handle |
|---|---|---|---|
| ESP301 controller, 3 axes | `control_readout/esp_301` | **probe**, **delay**, **truncation** motion stages (one serial port) | `EspHandle` → `lab.probe/delay/truncation` |
| RGV100BL rotator | `control_readout/rgv100bl` | **HWP** angle (initial phase) | `RgvHandle` → `lab.hwp` |
| ELL14 rotator (collaborator's) | `control_readout` rotator | **QWP** angle (ellipticity trim) | `RotatorHandle` → `lab.qwp` *(wiring deferred, M4.7)* |
| Newport 8742 picomotors | `control_readout/picomotor` | mirror alignment (open-loop steps) | `PicomotorHandle` → `lab.picomotor` |
| Servo shutters (Arduino/ESP32) | `control_readout/servo_shutter` | block one interferometer arm for reference; **actuation TODO** (manual now) | `ServoShutterHandle` → `lab.shutter` |
| Tektronix **TBS2012C** scope | `oscilloscope/` (own subprocess) | **CH1 = photodiode → XCORR signal** | `OscilloscopeWorkerHandle` → `lab.scope` |
| Photon Control **SPM-002** spectrometer | `spm_002` | **interferometric spectrum**, direct to computer | spectrum buffer → `lab.spectrometer` |
| ESP100 controller (grating) | `control_readout/esp_100` *(collaborator's stub)* | **grating** stage (chirp rate) | *(collaborator)* |

## 2.2 Control variables ↔ hardware (the control model)

Dominant correlations; **each has an offset and cross-coupling**, so control closes on *fitted*
quantities, not open-loop dial values. (Refines decision-log **D19**.)

| Physical quantity | Definition | Dominant actuator | Fit field |
|---|---|---|---|
| **Central frequency ν₀** | average of the frequencies at the two half-max points: `½(ν_start + ν_end)` | **delay stage** | `nu0_thz` |
| **Chirp rate / span** | difference of the half-max frequencies: `ν_start − ν_end` | **grating stage** | `g2` (chirp) / `nu_start_thz`, `nu_end_thz` |
| **Terminal frequency ν_end** | red-edge half-max frequency (pulse truncation point) | **truncation stage** | `nu_end_thz` |
| **Initial phase** | fringe phase of the spectrum | **HWP** (RGV100BL) | `phase0` |
| **Ellipticity** | lower-envelope amplitude (residual circular component) | **QWP** (ELL14) | `amp_lower` / `lower_envelope_metric` |
| **TOD (f2)** | cubic spectral phase — **nuisance**, fit only, **not controlled** | — | `g3` |

## 2.3 Stage geometry (path-length relations) **[LAB]**

- **Probe** and **delay** stages each change their arm's path length by **2× the stage
  displacement** (double-pass). *(Consistent with the 15 µm ↔ 0.1 ps double-pass spec.)*
- The **delay stage rides on top of the grating stage**. Moving the **grating stage** moves
  **both** the grating and the delay stage together.
- Moving the grating stage **increases the grating separation** by the stage displacement, and
  is arranged to **nearly preserve the delay-arm↔grating-arm path-length difference** (so
  grating moves change chirp/span with minimal disturbance to ν₀).

## 2.4 Readout details **[LAB]**

- **Spectrum:** SPM-002 → shared spectrum buffer → `fit_spectrum(wavelengths_nm, intensities)`
  → `SpectrumInfo`. Not through the scope.
- **XCORR point:** one scope capture (CH1) → **mean of the 20 highest samples** = one scalar.
  **XCORR scan:** sweep probe delay, record that scalar vs delay → bounded sinusoid →
  wavelength↔delay calibration (append-only HDF5 store).
- **No hardware position-sync:** probe position is associated to each trace in software.

## 2.5 Operating ranges **[LAB]**

- **Rotation frequency:** start ≈ **0 GHz**, terminal up to ≈ **200 GHz** (this round; higher
  than the prior 8.5–17 GHz regime).
- **Goal:** higher rotational excitation; possibly approach the **Landau critical velocity**.
- **Probe pulse:** ~120 fs (detection); **not** a software-controlled quantity.

## 2.6 Analysis outputs a routine can consume

- `SpectrumInfo`: `nu0_thz`, `nu_start_thz`, `nu_end_thz`, `g2`, `g3`, `amp_upper`, `amp_lower`,
  `lower_envelope_metric`, `phase0`, `tau_ps`, `central_wavelength_nm`, `bandwidth_nm`,
  `fit_residual`.
- `WavelengthDelayCalibration`: `wavelength_to_delay()`, `delay_to_wavelength()`; persisted via
  append-only `CalibrationStore` (HDF5).

## 2.7 The `lab.*` verb vocabulary (action grammar)

Blocking verbs a linear routine (human- or LLM-authored) may call:

```
# Motion (block until settled)
lab.probe.move_to(mm) / move_by(mm) ; lab.probe.position
lab.delay.move_to(mm) / move_by(mm)
lab.truncation.move_to(mm) / move_by(mm)
lab.hwp.rotate_to(angle)            # initial phase
lab.qwp.rotate_to(angle)            # ellipticity   (deferred wiring)
lab.picomotor.step(axis, n)         # mirror align  (open-loop)
lab.shutter.open(arm) / close(arm)  # reference; manual actuation for now

# Readout (block until data)
trace = lab.scope.capture()         # CH1 photodiode trace (np.ndarray)
val   = lab.xcorr_point()           # mean of 20 highest samples of a fresh capture
spec  = lab.spectrometer.read()     # SPM-002 spectrum
info  = lab.fit_spectrum(spec)      # -> SpectrumInfo

# Helpers
lab.sleep(s)                        # cancellable
lab.frange(a, b, step)              # iterate stage positions
lab.record(**fields) ; lab.save("name.csv")   # CSV output [LAB-chosen]
lab.plot(x_field, y_field)
lab.log(msg) ; lab.checkpoint()     # cancellation point in long CPU loops
lab.params                          # parameters passed at launch
```

## 2.8 Open items / TODO **[LAB]**

- **Picomotor axis ↔ mirror/DOF mapping** and **servo arm ↔ interferometer-arm mapping**:
  **not yet planned** — fill in when the hardware is in place.
- **Servo shutter actuation:** high-level only; physical actuation (Arduino/ESP32) is a TODO,
  manual blocking for now.
- **QWP (ELL14) control wiring:** deferred to M4.7; reuses the collaborator's rotator.
- **ESP100 grating stage:** collaborator's device; not built here.

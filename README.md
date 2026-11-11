# Module 8: Stage 1 — Establish the boundary condition

## Updated Stage 1 record and diagnostic modes

The current Arduino and GUI now use the complete Stage 1 DATA record. Read [the protocol and power-off verification steps](docs/stage_1/README.md) before running. Upload the updated sketch and restart the updated GUI together; old raw files retain their original format. Two raw-PWM diagnostic modes are now available in addition to the corrected feedforward/P/PI comparison.

Module 7 is unchanged. The Module 8 Arduino copy acquires TEC/A0 and T1–T5/A1–A5, accepts atomic FRAME commands as well as the legacy manual command, preserves the H-bridge pins, starts at zero PWM, and retains its A0 5/60 °C shutdown. Python supplies the sinusoidal drive and controller.

## Current software

Run `python3 python/module_08_rod_acquisition_gui.py` with Serial Monitor/Plotter closed. The current port is `/dev/cu.usbmodem1101`; check it if the Arduino port changes. All six raw temperatures use Arduino time and are continuously saved without calibration corrections. T1 is the model boundary at x = 0; A0 remains the sole feedback temperature.

The earlier linear-approximation feedforward shortcut was removed. Start/STOP, continuous files, sine controls, P/PI controls, six traces, and recording are retained because Stage 1 requires them.

## Stage 1 procedure and responsibility

1. **YOU:** measure and record actual room temperature. Confirm sensor assignments, sensor positions, common-bath result, and hardware safety readiness before powering the TEC. T1 stays in the first hole; record any changed T2–T5 positions separately from Module 7's map.
2. **YOU provide measured calibration; I can format/check it:** load the measured steady-state curve CSV, columns `temperature_C,signed_pwm`. Temperature and signed PWM must increase strictly. Positive PWM heats; negative cools. The GUI interpolates its inverse and rejects out-of-range requests; it does not generate a guessed sinusoidal PWM or extrapolate. If calibration has distinct branches or is non-monotonic, it needs a documented branch-selection treatment rather than averaging the branches into this single monotonic lookup.
3. **YOU:** enter measured χh and χc. Values carried from Module 6 are 0.4872 and 0.20 °C/PWM, giving r = χh/χc = 2.436; verify those are appropriate to the present apparatus. Editing these coefficients stops output and records the change.
4. **YOU operate; I can analyze:** select Open-loop feedforward and set Ta = 40 °C, Tb = 16 °C, period = 100 s as the suggested first trial. This requests 24–56 °C and leaves little safety margin. Reduce mean/amplitude if tracking or overshoot approaches the limits. Periods below 100 s and requested temperatures outside 5–56 °C are rejected. Press Start / apply sine settings only after the hardware checks. Phase zero is the next complete Arduino sample.
5. **YOU operate; I can analyze:** compare the measured T1 waveform with the command and thermal model. Keep the same waveform settings and compare P-only, then PI; tune each while watching measured T1. Mode changes pass through zero and clear integral state, preserving the sine phase and uninterrupted recording. Starting/applying a changed waveform creates a new phase segment.
6. **YOU operate; I can analyze:** compare means, amplitudes, phases, heating/cooling symmetry, harmonic distortion, residuals, raw controller commands and applied PWM. If necessary try 150 s or Tb = 10 °C and retune gains. Never use a period below 100 s. Keep the same Ta, Tb and period during each three-mode comparison.
7. Later: document the quantitative periodic-settling criterion, then retain at least five complete settled cycles with measurable amplitude/phase at three or more rod sensors. Repeat at a second usable frequency. No automatic settled claim is made by this GUI.

## Identical compensation in P-only and PI

The raw command is uP = Kp e or uPI = Kp e + Ki q, with e = Tset − TTEC. It uses A0, never T1. Both modes apply:

- raw command ≥ 0: compensated request = raw command;
- raw command < 0: compensated request = r × raw command;
- clamp compensated request to ±255, round magnitude, and select HEAT/COOL using its sign.

PI's candidate integral is accepted only if the **compensated** candidate does not push farther into saturation. The raw P/I contributions, raw total, compensated request and actual Arduino PWM are separately available. Plot raw total against signed applied PWM in analysis to see the heating slope 1, cooling slope r, and clipped regions. Open-loop lookup already supplies signed actuator PWM and is not multiplied again by r.

## Safety and stopping

STOP OUTPUT sends zero PWM, clears integral state, stops the sine, and keeps recording. A0 interlock, host A0 outside (5,56) °C, measured T1 ≥ 56 °C, stale valid DATA for 5 s, or an Arduino clock reset stops output and requires explicit restart. The updated sketch also guards T1 >=56 °C and drops PWM after 4 s without a valid command; these supplement the independent A0 5/60 °C limits and the physical cutoff. No software can deliver a stop command across a disconnected serial cable; retain hardware safety and supervision.

Test with TEC power disconnected before powered operation. New run CSV stops output and opens new files; do not use it between modes when retaining one continuous comparison. Close also sends zero PWM.

## Files to preserve

Each run creates unique files in `python/run_data/`:

- Raw CSV: complete Arduino Stage 1 record, beginning `time_ms,run_state,control_mode,step`; see the linked protocol for all fields. Every accepted record is flushed without changing raw values.
- `_commands.csv`: supplemental next host command and settings, timestamped against the measurement that prompted it.
- `_events.jsonl`: mode/settings/start/stop changes, loaded curve contents/source, acquisition settings and GUI hash.

The display shows six labeled traces, the requested setpoint, live values, raw/compensated command traces, and accepted/rejected counts. The rolling display does not trim the full CSV.

## Verification completed

Mocked serial/offscreen Qt tests checked compensation in P and PI, heating and cooling directions, compensated saturation/anti-windup, measured-curve-only open loop, T1 safety without T1 feedback, restart inhibition, sine settings, aligned logs, STOP, and new-run handling. Physical control performance and waveform quality still require supervised apparatus tests.

Sensor positions: [Module 8 oscillatory sensor map](docs/sensor_map/README.md).

## Prepared reduced pilot (not yet hardware validated)

Current GUI defaults are Ta = 40 °C, Tb = 10 °C, period = 100 s, with recovered χh = 0.4872434257285 and χc = 0.19566755027076152 °C/PWM, r = 2.4901595847. This is a reduced-amplitude candidate, not the originally specified 16 °C trial. It does not imply instructor approval or current-apparatus calibration validity.

1. Keep TEC power off and restart the GUI.
2. Enter independently measured room temperature.
3. Select Open-loop feedforward and load `data/calibration/module4_original_lookup.csv`.
4. Confirm mean 40 °C, amplitude 10 °C, period 100 s; test Start and STOP with TEC power off.
5. Before powered acquisition, reconcile the historical 35.6 °C zero-PWM reference with the current apparatus/ambient and confirm the chosen reduced trial with the instructor. Do not apply the old curve as a validated present-day calibration merely because its temperature range covers 30–50 °C.
6. When ready, retain one continuous record for open-loop, P-only and PI at these same waveform settings; keep all raw, command and event files.

Original measured curve and provenance: [calibration notes](data/calibration/README.md). The old curve is not extrapolated or ambient-shifted.

Simulation-only screenshot estimates and ambient distinction: [preview notes](docs/simulation_preview/README.md). Physical room temperature reported November 4, 2026: approximately 26 °C; simulator ambient remains 22 °C.

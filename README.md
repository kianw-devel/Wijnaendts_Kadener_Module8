# Module 8: Angstrom acquisition software

Module 7 remains unchanged. The Module 8 Arduino sketch is still the working six-channel Module 7 sketch; upload it only if that sketch is not already installed. The Arduino command format remains `SET PWM <0..255> DIR HEAT|COOL`. PI uses only A0; T1/A1 is the measured rod boundary for later Angstrom analysis, not the feedback channel.

## First run (TEC power off)

1. Set `SERIAL_PORT` in `python/module_08_rod_acquisition_gui.py` to the Arduino port. Close Serial Monitor before opening Python.
2. Run `python3 python/module_08_rod_acquisition_gui.py`. Verify six numerical temperatures, fixed-color traces, raw CSVs, and zero startup output.
3. Select P-only or PI. Enter mean Ta, amplitude Tb, and period; press **Start / apply sine settings**. Test first with TEC power disconnected.
4. Check the live requested setpoint and its trace. Phase zero is the first complete Arduino DATA record after Start; the setpoint is Ta + Tb sin[2π(time_s − origin)/period]. Commands update once per complete measurement, not on a separate wall-clock waveform timer.
5. Press **STOP OUTPUT**. It sends zero PWM and keeps recording. Arduino shutdown, host A0 outside (5, 56) °C, a clock reset, or 5 s without valid DATA also stop output. Restart explicitly after recovery. Software cannot enforce zero PWM if the serial cable is disconnected; use the physical shutdown procedure.

## Waveform and controller comparisons

- Conservative default: Ta = 40 °C, Tb = 10 °C, period = 100 s (30–50 °C). Requested bounds are 5–56 °C; period must be at least 100 s. The specified Ta = 40, Tb = 16 trial reaches 56 °C and leaves no headroom at the host stop threshold. Confirm the apparatus limit before increasing amplitude. Arduino's independent 5/60 °C interlock is unchanged.
- Compare **Open-loop feedforward**, **P-only**, and **PI** at identical applied waveform settings. Mode switching passes through zero PWM and clears integral state, preserving sine phase and the continuous raw file. All transitions are logged. Changing waveform settings requires Start / apply; this begins a new phase segment.
- The original P/PI law, PWM clamp, and conditional-integration anti-windup are retained. Gains require apparatus testing; defaults are not validated Module 8 tuning.
- Constant-temperature operation remains available by editing the constant setpoint. Starting the sine overrides it. Manual PWM operation remains available; selecting Manual stops the sine and passes through zero.
- New run CSV stops output and creates a fresh file set. To preserve a complete comparison or frequency sweep, keep the existing files open and use mode/Start controls instead.

## Open-loop feedforward source

Preferred: load a measured steady-state curve CSV with columns `temperature_C,signed_pwm`, e.g. one equilibrium temperature per applied PWM. Temperatures and signed PWM must increase strictly. Positive PWM heats; negative cools. The GUI interpolates the inverse curve and refuses extrapolation. The loaded points and source path are saved in the event log. Recheck the curve when ambient conditions change.

If measured curves are not available, explicitly check **Use linear approximation**. The fallback is u = (Tset − Tamb)/χh above ambient and u = (Tset − Tamb)/χc below ambient, clamped to ±255. χh = 0.4872 and χc = 0.20 °C/PWM are carried from Module 6; enter the current measured ambient. Their ratio is 2.436. This is a piecewise-linear equilibrium approximation, NOT a measured nonlinear curve or a dynamic compensation. Feedforward does not add P/PI terms. Merely increasing/decreasing a setpoint does not select the power branch: the sign of the signed equilibrium command does.

## Files preserved for every run

Under `python/run_data/`, with timestamped names and exclusive creation:

- `run_....csv`: original Arduino DATA fields only, unchanged and flushed every sample. Raw `setpoint_C=nan` is retained. The `pwm` and `heat_cool` fields report Arduino output.
- `run_..._commands.csv`: timestamp-matched host setpoint, mode, gains, requested/commanded signed PWM, sine mean/amplitude/period, phase origin, phase, segment ID, ambient/coefficient inputs, feedforward source, stop state, and host monotonic time. These are commands issued AFTER receiving the corresponding DATA sample, not proof the actuator had already applied them at that time.
- `run_..._events.jsonl`: mode/settings/start/stop events, curve points, software hash, serial settings, and time conventions. Settings edits are recorded without splitting the raw record.

All six plot histories use Arduino time and a 600 s rolling display; full CSVs are never trimmed. A legend identifies every trace; TEC is thicker. No calibration corrections are applied. Module 7 sensor positions are not assumed to be Module 8 positions: save the newly measured map separately.

## Remaining physical and analysis work

Reposition T2–T5 near 2.54 cm spacing, measure actual positions and uncertainties, verify channel mapping, and record ambient temperature. Run supervised trials with hardware attached. This extension does not automatically claim periodic settling or five usable cycles: select and document the quantitative periodic-settling rule, then preserve at least five settled complete cycles at each of two or more usable frequencies with at least three measurable rod signals. Fit the measured T1 waveform rather than assuming the command was achieved.

Software verification used mocked serial input and offscreen Qt: sine extrema/phase, mode changes, feedforward heating/cooling and curve range handling, raw-to-command row alignment, invalid inputs, stop behavior, stale-data/clock-reset stops, unique file creation, and close-to-zero. No physical heating test was performed.

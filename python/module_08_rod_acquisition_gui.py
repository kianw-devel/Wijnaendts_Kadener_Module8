"""Display Arduino temperature measurements in a rolling strip chart."""

import csv
import math
import time
import bisect
import json
import hashlib
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
import serial
from PySide6 import QtCore, QtGui, QtWidgets


# ---------- Settings to change before running ----------
SERIAL_PORT = "/dev/cu.usbmodem101"  # Change to your Arduino's serial port
BAUD_RATE = 9600
WINDOW_DURATION_SECONDS = 600.0
UPDATE_INTERVAL_MILLISECONDS = 100
RUN_DATA_DIRECTORY = Path(__file__).resolve().parent / "run_data"
INITIAL_SETPOINT_C = 32.5
MINIMUM_SETPOINT_C = 5.0
MAXIMUM_SETPOINT_C = 56.0  # Module 8 requested-temperature limit; Arduino interlock remains 60 C.
MINIMUM_PERIOD_S = 100.0
SERIAL_STALE_SECONDS = 5.0
INITIAL_PROPORTIONAL_GAIN = 1.0
INITIAL_INTEGRAL_GAIN = 0.08
PWM_LIMIT = 255


# One shared field order ties the serial parser, CSV columns, and sensor plots together.
CSV_COLUMNS = (
    "time_s", "tec_C", "rod1_C", "rod2_C", "rod3_C", "rod4_C", "rod5_C",
    "pwm", "heat_cool", "setpoint_C", "safety",
)
TEMPERATURE_COLUMNS = CSV_COLUMNS[1:7]
TRACE_COLORS = ("#d95f02", "#0072B2", "#009E73", "#CC79A7", "#E6AB02", "#6A3D9A")


def parse_measurement(line):
    """Validate a complete DATA line; return original field text or None.

    The caller buffers bytes until newline. Headers and diagnostics never become
    samples. Only setpoint may be nan: Arduino does not receive Python's setpoint.
    Raw strings are retained so CSV values are not corrected or reformatted.
    """
    fields = line.strip().split(",")
    if len(fields) != len(CSV_COLUMNS) + 1 or fields[0] != "DATA":
        return None
    record = dict(zip(CSV_COLUMNS, fields[1:]))
    try:
        if not all(math.isfinite(float(record[key])) for key in CSV_COLUMNS[:8]):
            return None
        if float(record["time_s"]) < 0:
            return None
        pwm = int(record["pwm"])
        if not 0 <= pwm <= PWM_LIMIT or record["heat_cool"] not in ("0", "1"):
            return None
        setpoint = float(record["setpoint_C"])
        if math.isinf(setpoint):
            return None
        if record["safety"] not in ("OK", "SHUTDOWN"):
            return None
    except (ValueError, OverflowError):
        return None
    return record


def calculate_p_control(setpoint, temperature, gain):
    """Return temperature error, signed output, PWM magnitude, and direction."""
    error = setpoint - temperature
    signed_pwm = gain * error
    direction = "HEAT" if signed_pwm >= 0 else "COOL"
    pwm = max(0, min(255, int(round(abs(signed_pwm)))))
    return error, signed_pwm, pwm, direction


def sine_setpoint(time_s, origin_s, mean_c, amplitude_c, period_s):
    """Arduino time provides the common phase clock for setpoint and sensors."""
    return mean_c + amplitude_c * math.sin(2 * math.pi * (time_s - origin_s) / period_s)


def read_feedforward_curve(path):
    """Load measured equilibrium temperature vs signed applied PWM.

    CSV columns: temperature_C,signed_pwm. Positive PWM heats, negative cools.
    The inverse must be strictly monotonic; no extrapolation is allowed.
    """
    with open(path, newline="") as stream:
        rows = list(csv.DictReader(stream))
    points = sorted((float(r["temperature_C"]), float(r["signed_pwm"])) for r in rows)
    if len(points) < 2 or any(not math.isfinite(v) for p in points for v in p):
        raise ValueError("Need at least two finite curve points.")
    if any(abs(pwm) > PWM_LIMIT for _, pwm in points):
        raise ValueError("Curve PWM must be within ±255.")
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(points, points[1:])):
        raise ValueError("Temperature and signed PWM must both increase strictly.")
    return points


def invert_curve(points, temperature):
    if not points[0][0] <= temperature <= points[-1][0]:
        raise ValueError("Requested temperature is outside the measured curve range.")
    i = max(1, min(len(points)-1, bisect.bisect_left([p[0] for p in points], temperature)))
    t0, u0 = points[i-1]
    t1, u1 = points[i]
    return u0 + (u1-u0) * (temperature-t0) / (t1-t0)


class TemperatureWindow(QtWidgets.QMainWindow):
    """Read Arduino measurements and provide manual serial controls."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Module 8: Angstrom rod acquisition")

        # Keep the rolling chart data separate from the CSV output values.
        self.times = deque()
        self.temperatures = deque()
        # All six deques use the exact CSV names; tec_C aliases the original PI data.
        self.sensor_temperatures = {key: deque() for key in TEMPERATURE_COLUMNS}
        self.sensor_temperatures["tec_C"] = self.temperatures
        self.accepted_lines = 0
        self.rejected_lines = 0
        self.setpoints = deque()
        self.errors = deque()
        self.signed_pwms = deque()
        self.proportional_pwms = deque()
        self.integral_pwms = deque()
        self.requested_pwms = deque()
        self.serial_buffer = b""
        self.latest_temperature = None
        self.last_measurement_time = None
        self.setpoint = INITIAL_SETPOINT_C
        self.proportional_gain = INITIAL_PROPORTIONAL_GAIN
        self.integral_gain = INITIAL_INTEGRAL_GAIN
        self.integral_error = 0.0
        self.proportional_output = 0.0
        self.integral_output = 0.0
        self.requested_output = 0.0
        self.applied_output = 0.0
        self.wave_running = False
        self.wave_origin_s = None
        self.wave_mean_c = 40.0
        self.wave_amplitude_c = 10.0
        self.wave_period_s = 100.0
        self.feedforward_points = None
        self.feedforward_source = "linear approximation (not a measured curve)"
        self.last_valid_host_time = None
        self.output_stopped = True
        self.segment_id = 0
        self.event_file = None

        # Create the manual controls and the live values shown above the plots.
        controls = QtWidgets.QGroupBox("Manual controls")
        controls_layout = QtWidgets.QGridLayout(controls)

        self.direction_switch = QtWidgets.QCheckBox("HEAT")
        self.direction_switch.setChecked(True)
        self.direction_switch.toggled.connect(self.direction_changed)
        controls_layout.addWidget(QtWidgets.QLabel("Direction"), 0, 0)
        controls_layout.addWidget(self.direction_switch, 0, 1)

        self.pwm_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.pwm_slider.setRange(0, 255)
        self.pwm_slider.setValue(0)
        self.pwm_slider.valueChanged.connect(self.slider_changed)
        controls_layout.addWidget(QtWidgets.QLabel("PWM"), 1, 0)
        controls_layout.addWidget(self.pwm_slider, 1, 1)

        self.pwm_input = QtWidgets.QLineEdit("0")
        self.pwm_input.setValidator(QtGui.QIntValidator(0, 255, self))
        self.pwm_input.setMaximumWidth(80)
        self.pwm_input.editingFinished.connect(self.text_pwm_changed)
        controls_layout.addWidget(self.pwm_input, 1, 2)

        self.control_mode = QtWidgets.QComboBox()
        self.control_mode.addItems(["Manual", "Open-loop feedforward", "P-only", "PI"])
        self.control_mode.currentTextChanged.connect(self.control_mode_changed)
        controls_layout.addWidget(QtWidgets.QLabel("Controller"), 2, 0)
        controls_layout.addWidget(self.control_mode, 2, 1)

        self.setpoint_input = QtWidgets.QLineEdit(str(INITIAL_SETPOINT_C))
        self.setpoint_input.setValidator(
            QtGui.QDoubleValidator(MINIMUM_SETPOINT_C, MAXIMUM_SETPOINT_C, 2, self)
        )
        self.setpoint_input.setMaximumWidth(80)
        self.setpoint_input.editingFinished.connect(self.setpoint_changed)
        controls_layout.addWidget(QtWidgets.QLabel("Constant setpoint (5–56 C)"), 2, 2)
        controls_layout.addWidget(self.setpoint_input, 2, 3)

        self.gain_input = QtWidgets.QLineEdit(str(INITIAL_PROPORTIONAL_GAIN))
        self.gain_input.setValidator(QtGui.QDoubleValidator(0.0, 1000.0, 2, self))
        self.gain_input.setMaximumWidth(80)
        self.gain_input.editingFinished.connect(self.gain_changed)
        controls_layout.addWidget(QtWidgets.QLabel("Kp (PWM/C)"), 3, 0)
        controls_layout.addWidget(self.gain_input, 3, 1)

        self.integral_gain_input = QtWidgets.QLineEdit(str(INITIAL_INTEGRAL_GAIN))
        self.integral_gain_input.setValidator(
            QtGui.QDoubleValidator(0.0, 1000.0, 4, self)
        )
        self.integral_gain_input.setMaximumWidth(80)
        self.integral_gain_input.editingFinished.connect(self.integral_gain_changed)
        self.integral_gain_input.setEnabled(False)
        controls_layout.addWidget(QtWidgets.QLabel("Ki (PWM/(C s))"), 3, 2)
        controls_layout.addWidget(self.integral_gain_input, 3, 3)

        self.zero_integral_button = QtWidgets.QPushButton("Zero integral")
        self.zero_integral_button.clicked.connect(self.zero_integral)
        controls_layout.addWidget(self.zero_integral_button, 4, 0, 1, 2)

        self.new_run_button = QtWidgets.QPushButton("New run CSV")
        self.new_run_button.clicked.connect(self.start_new_run)
        controls_layout.addWidget(self.new_run_button, 4, 2, 1, 2)
        self.csv_filename_value = QtWidgets.QLabel("No run file")
        controls_layout.addWidget(QtWidgets.QLabel("Current CSV"), 5, 0)
        controls_layout.addWidget(self.csv_filename_value, 5, 1, 1, 3)
        self.csv_filename_value.setWordWrap(True)
        self.csv_filename_value.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        self.temperature_value = QtWidgets.QLabel("-- C")
        self.pwm_value = QtWidgets.QLabel("--")
        self.direction_value = QtWidgets.QLabel("--")
        self.time_value = QtWidgets.QLabel("-- s")
        self.error_value = QtWidgets.QLabel("-- C")
        self.proportional_pwm_value = QtWidgets.QLabel("--")
        self.integral_pwm_value = QtWidgets.QLabel("--")
        self.requested_pwm_value = QtWidgets.QLabel("--")
        self.applied_pwm_value = QtWidgets.QLabel("--")
        live_values = QtWidgets.QFormLayout()
        live_values.addRow("TEC (A0)", self.temperature_value)
        self.sensor_values = {"tec_C": self.temperature_value}
        for channel, key in enumerate(TEMPERATURE_COLUMNS[1:], start=1):
            value = QtWidgets.QLabel("-- C")
            self.sensor_values[key] = value
            live_values.addRow(f"Rod {channel} (A{channel})", value)
        self.safety_value = QtWidgets.QLabel("Waiting for DATA")
        self.line_count_value = QtWidgets.QLabel("Accepted: 0 | Rejected: 0")
        live_values.addRow("Arduino safety", self.safety_value)
        live_values.addRow("Serial lines", self.line_count_value)
        live_values.addRow("PWM", self.pwm_value)
        live_values.addRow("Direction", self.direction_value)
        live_values.addRow("Elapsed time", self.time_value)
        self.current_setpoint_value = QtWidgets.QLabel(f"{self.setpoint:.2f} C")
        live_values.addRow("Current requested setpoint", self.current_setpoint_value)
        live_values.addRow("Error", self.error_value)
        live_values.addRow("uP", self.proportional_pwm_value)
        live_values.addRow("uI", self.integral_pwm_value)
        live_values.addRow("Requested u", self.requested_pwm_value)
        live_values.addRow("Applied u", self.applied_pwm_value)
        controls_layout.addLayout(live_values, 7, 0, 1, 4)

        # Waveform settings are applied only when Start / apply is pressed.
        wave_box = QtWidgets.QGroupBox("Sinusoidal drive: Ta + Tb sin(2πt / period)")
        wave_layout = QtWidgets.QFormLayout(wave_box)
        self.mean_input = QtWidgets.QLineEdit("40")
        self.amplitude_input = QtWidgets.QLineEdit("10")
        self.period_input = QtWidgets.QLineEdit("100")
        self.ambient_input = QtWidgets.QLineEdit("22")
        self.heating_chi_input = QtWidgets.QLineEdit("0.4872")
        self.cooling_chi_input = QtWidgets.QLineEdit("0.20")
        for label, widget in (
            ("Mean Ta (°C)", self.mean_input), ("Amplitude Tb (°C)", self.amplitude_input),
            ("Period (s; minimum 100)", self.period_input),
            ("Measured room temperature (°C)", self.ambient_input),
            ("Linear heating χ (°C/PWM)", self.heating_chi_input),
            ("Linear cooling χ (°C/PWM)", self.cooling_chi_input),
        ):
            wave_layout.addRow(label, widget)
        self.allow_linear = QtWidgets.QCheckBox("Use linear approximation for feedforward (not a measured curve)")
        wave_layout.addRow(self.allow_linear)
        self.load_curve_button = QtWidgets.QPushButton("Load measured steady-state curve CSV")
        self.load_curve_button.clicked.connect(self.load_feedforward_curve)
        wave_layout.addRow(self.load_curve_button)
        self.curve_label = QtWidgets.QLabel("No measured curve loaded")
        self.curve_label.setWordWrap(True)
        wave_layout.addRow(self.curve_label)
        self.wave_status = QtWidgets.QLabel("Output stopped. Default waveform: 30–50 °C. Verify hardware limits.")
        self.wave_status.setWordWrap(True)
        wave_layout.addRow(self.wave_status)
        self.start_wave_button = QtWidgets.QPushButton("Start / apply sine settings")
        self.start_wave_button.clicked.connect(self.start_waveform)
        wave_layout.addRow(self.start_wave_button)
        self.stop_button = QtWidgets.QPushButton("STOP OUTPUT (keep recording)")
        self.stop_button.clicked.connect(self.stop_output)
        wave_layout.addRow(self.stop_button)
        controls_layout.addWidget(wave_box, 6, 0, 1, 4)

        # The first plot displays the measured temperature over Arduino time.
        self.temperature_plot = pg.PlotWidget()
        self.temperature_plot.setLabel("bottom", "Time", units="s")
        self.temperature_plot.setLabel("left", "Temperature", units="C")
        self.temperature_plot.enableAutoRange()
        self.temperature_plot.showGrid(x=True, y=True, alpha=0.25)
        self.temperature_plot.addLegend()
        self.temperature_curve = self.temperature_plot.plot(
            pen=pg.mkPen(TRACE_COLORS[0], width=3), name="TEC (A0)"
        )
        # Fixed colors and labels identify the same sensors throughout the run.
        self.sensor_curves = {"tec_C": self.temperature_curve}
        for channel, key in enumerate(TEMPERATURE_COLUMNS[1:], start=1):
            self.sensor_curves[key] = self.temperature_plot.plot(
                pen=pg.mkPen(TRACE_COLORS[channel], width=1.5),
                name=f"Rod {channel} (A{channel})",
            )
        self.setpoint_curve = self.temperature_plot.plot(
            pen=pg.mkPen("#008c95", width=2, style=QtCore.Qt.PenStyle.DashLine),
            name="Setpoint",
        )
        # Plot error separately because it is a temperature difference rather than
        # an absolute temperature.
        self.error_plot = pg.PlotWidget()
        self.error_plot.setLabel("bottom", "Time", units="s")
        self.error_plot.setLabel("left", "Error (setpoint - measured)", units="C")
        self.error_plot.showGrid(x=True, y=True, alpha=0.25)
        self.error_curve = self.error_plot.plot(pen=pg.mkPen("#555555", width=2))

        # A signed PWM plot displays magnitude and direction at the same time:
        # positive is HEAT, negative is COOL, and zero is off.
        self.pwm_plot = pg.PlotWidget()
        self.pwm_plot.setLabel("bottom", "Time", units="s")
        self.pwm_plot.setLabel("left", "Signed PWM (+ heat / - cool)")
        self.pwm_plot.setYRange(-255, 255)
        self.pwm_plot.showGrid(x=True, y=True, alpha=0.25)
        self.pwm_plot.addLegend()
        self.pwm_curve = self.pwm_plot.plot(
            pen=pg.mkPen("#000000", width=2), name="Applied u"
        )
        self.proportional_pwm_curve = self.pwm_plot.plot(
            pen=pg.mkPen("#1f78b4", width=2), name="uP"
        )
        self.integral_pwm_curve = self.pwm_plot.plot(
            pen=pg.mkPen("#e66101", width=2), name="uI"
        )
        self.requested_pwm_curve = self.pwm_plot.plot(
            pen=pg.mkPen("#7b3294", width=1, style=QtCore.Qt.PenStyle.DashLine),
            name="Requested u",
        )

        # Two resizable columns: scrollable controls left, stacked graphs right.
        central_widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central_widget)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setMinimumWidth(440)
        controls_scroll.setWidget(controls)
        controls_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        graphs = QtWidgets.QWidget()
        graphs_layout = QtWidgets.QVBoxLayout(graphs)
        graphs_layout.setContentsMargins(0, 0, 0, 0)
        graphs_layout.addWidget(self.temperature_plot, 3)
        graphs_layout.addWidget(self.error_plot, 1)
        graphs_layout.addWidget(self.pwm_plot, 2)
        splitter.addWidget(controls_scroll)
        splitter.addWidget(graphs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 920])
        layout.addWidget(splitter)
        self.setCentralWidget(central_widget)

        # Start with the TEC off and put the Arduino under serial control.
        self.serial_port = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0)
        self.send_control_command(0, "HEAT")
        self.csv_file = None
        self.csv_writer = None
        self.open_new_csv()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.read_measurements)
        self.timer.start(UPDATE_INTERVAL_MILLISECONDS)

    def open_new_csv(self):
        """Create a uniquely named CSV for the current controller settings."""
        RUN_DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
        mode = self.control_mode.currentText().lower().replace("-", "_")
        kp = f"{self.proportional_gain:g}".replace(".", "p")
        ki = f"{self.integral_gain:g}".replace(".", "p")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"run_{timestamp}_{mode}_kp{kp}_ki{ki}.csv"
        path = RUN_DATA_DIRECTORY / filename
        self.csv_file = open(path, "x", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        # Exclusive "x" mode above prevents overwriting any earlier run.
        self.csv_writer.writerow(CSV_COLUMNS)
        self.csv_file.flush()
        # Keep host commands separate: the raw Arduino setpoint is unavailable.
        # Match these rows to raw measurements using the same Arduino time_s.
        command_path = path.with_name(path.stem + "_commands.csv")
        self.command_file = open(command_path, "x", newline="")
        self.command_writer = csv.writer(self.command_file)
        self.command_writer.writerow([
            "time_s", "gui_setpoint_C", "controller_mode", "kp_pwm_per_C",
            "ki_pwm_per_C_s", "requested_signed_pwm", "commanded_signed_pwm",
            "waveform", "Ta_C", "Tb_C", "period_s", "wave_origin_s", "phase_rad",
            "segment_id", "ambient_C", "heating_chi_C_per_pwm", "cooling_chi_C_per_pwm",
            "feedforward_source", "output_stopped", "host_monotonic_s",
        ])
        self.command_file.flush()
        event_path = path.with_name(path.stem + "_events.jsonl")
        self.event_file = open(event_path, "x")
        self.log_event("new_run")
        self.event_file.write(json.dumps(dict(
            event="acquisition_metadata", serial_port=SERIAL_PORT, baud_rate=BAUD_RATE,
            temperature_limit_C=MAXIMUM_SETPOINT_C, serial_stale_seconds=SERIAL_STALE_SECONDS,
            csv_columns=CSV_COLUMNS, waveform_clock="Arduino time_s; phase zero at first sample after Start",
            command_semantics="Host command sent after acquiring the timestamped DATA record; raw pwm reports Arduino output",
            gui_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )) + "\n")
        self.event_file.flush()
        self.csv_filename_value.setText(f"Raw: {path}\nGUI setpoints: {command_path}")

    def start_new_run(self, checked=False):
        """Safely stop output and begin recording into a fresh run file."""
        self.stop_output()
        if self.csv_file is not None:
            self.csv_file.close()
            self.command_file.close()
            self.event_file.close()
        for values in (
            self.times,
            self.temperatures,
            self.setpoints,
            self.errors,
            self.signed_pwms,
            self.proportional_pwms,
            self.integral_pwms,
            self.requested_pwms,
        ):
            values.clear()
        for values in self.sensor_temperatures.values():
            values.clear()
        self.last_measurement_time = None
        self.open_new_csv()

    def set_pwm_value(self, pwm):
        """Keep the slider and text box synchronized at a valid PWM value."""
        pwm = max(0, min(255, pwm))
        self.pwm_slider.blockSignals(True)
        self.pwm_input.blockSignals(True)
        self.pwm_slider.setValue(pwm)
        self.pwm_input.setText(str(pwm))
        self.pwm_slider.blockSignals(False)
        self.pwm_input.blockSignals(False)
        return pwm

    def send_control_command(self, pwm, direction=None):
        """Send one direction and PWM command to the Arduino."""
        if direction is None:
            direction = "HEAT" if self.direction_switch.isChecked() else "COOL"
        if pwm and (self.output_stopped or self.latest_temperature is None
                    or self.last_valid_host_time is None
                    or time.monotonic() - self.last_valid_host_time > SERIAL_STALE_SECONDS):
            pwm = 0
        command = f"SET PWM {pwm} DIR {direction}\n"
        self.serial_port.write(command.encode("ascii"))

    def feedback_enabled(self):
        """Return True when either feedback controller is selected."""
        return self.control_mode.currentText() in ("P-only", "PI")

    def control_mode_changed(self, mode):
        """Pass through zero; preserve sine phase when comparing modes."""
        self.send_control_command(0, "HEAT")
        self.set_pwm_value(0)
        self.zero_integral(update_output=False)
        automatic = mode != "Manual"
        self.direction_switch.setEnabled(not automatic)
        self.pwm_slider.setEnabled(not automatic)
        self.pwm_input.setEnabled(not automatic)
        self.integral_gain_input.setEnabled(mode == "PI")
        if mode == "Manual":
            self.stop_output()
        elif self.wave_running:
            if mode == "Open-loop feedforward" and not self.feedforward_ready():
                self.stop_output()
                self.wave_status.setText("Feedforward unavailable: load a curve or explicitly select approximation.")
        self.segment_id += 1
        self.log_event("controller_mode_changed")

    def setpoint_changed(self):
        """Validate the setpoint and update an active P controller."""
        try:
            setpoint = float(self.setpoint_input.text())
        except ValueError:
            setpoint = INITIAL_SETPOINT_C
        if not math.isfinite(setpoint):
            setpoint = INITIAL_SETPOINT_C
        setpoint = max(MINIMUM_SETPOINT_C, min(MAXIMUM_SETPOINT_C, setpoint))
        self.wave_running = False
        self.wave_origin_s = None
        self.setpoint = setpoint
        self.setpoint_input.setText(f"{setpoint:g}")
        self.log_event("constant_setpoint_changed")
        # Explicit setpoint edit enables the existing constant-temperature mode.
        self.output_stopped = False
        if self.feedback_enabled():
            self.update_feedback_control(0.0)

    def gain_changed(self):
        """Validate the proportional gain and update an active controller."""
        try:
            gain = float(self.gain_input.text())
        except ValueError:
            gain = INITIAL_PROPORTIONAL_GAIN
        gain = max(0.0, min(1000.0, gain)) if math.isfinite(gain) else 0.0
        self.proportional_gain = gain
        self.gain_input.setText(f"{gain:g}")
        self.log_event("kp_changed")
        if self.feedback_enabled():
            self.update_feedback_control(0.0)

    def integral_gain_changed(self):
        """Validate the integral gain without advancing the integral state."""
        try:
            gain = float(self.integral_gain_input.text())
        except ValueError:
            gain = INITIAL_INTEGRAL_GAIN
        gain = max(0.0, min(1000.0, gain)) if math.isfinite(gain) else 0.0
        self.integral_gain = gain
        self.integral_gain_input.setText(f"{gain:g}")
        self.log_event("ki_changed")
        if self.feedback_enabled():
            self.update_feedback_control(0.0)

    def zero_integral(self, checked=False, update_output=True):
        """Clear PI controller memory before a new comparison."""
        self.integral_error = 0.0
        self.integral_output = 0.0
        self.integral_pwm_value.setText("0.00")
        if update_output and self.feedback_enabled():
            self.update_feedback_control(0.0)

    def update_feedback_control(self, delta_time):
        """Calculate P/PI output and apply conditional-integration anti-windup."""
        if self.latest_temperature is None or self.output_stopped:
            self.set_pwm_value(0)
            self.send_control_command(0, "HEAT")
            return

        error = self.setpoint - self.latest_temperature
        u_p = self.proportional_gain * error

        if self.control_mode.currentText() == "PI" and delta_time > 0.0:
            candidate_integral = self.integral_error + error * delta_time
            candidate_u_i = self.integral_gain * candidate_integral
            candidate_request = u_p + candidate_u_i
            pushes_high = candidate_request > PWM_LIMIT and error > 0.0
            pushes_low = candidate_request < -PWM_LIMIT and error < 0.0
            if not (pushes_high or pushes_low):
                self.integral_error = candidate_integral

        if self.control_mode.currentText() == "PI":
            u_i = self.integral_gain * self.integral_error
        else:
            self.integral_error = 0.0
            u_i = 0.0

        requested = u_p + u_i
        applied = max(-PWM_LIMIT, min(PWM_LIMIT, requested))
        direction = "HEAT" if applied >= 0.0 else "COOL"
        pwm = int(round(abs(applied)))

        self.proportional_output = u_p
        self.integral_output = u_i
        self.requested_output = requested
        self.applied_output = float(pwm if direction == "HEAT" else -pwm)
        self.error_value.setText(f"{error:.2f} C")
        self.proportional_pwm_value.setText(f"{u_p:.2f}")
        self.integral_pwm_value.setText(f"{u_i:.2f}")
        self.requested_pwm_value.setText(f"{requested:.2f}")
        self.applied_pwm_value.setText(f"{self.applied_output:.0f}")
        self.set_pwm_value(pwm)
        self.direction_switch.setText(direction)
        self.direction_switch.blockSignals(True)
        self.direction_switch.setChecked(direction == "HEAT")
        self.direction_switch.blockSignals(False)
        self.send_control_command(pwm, direction)

    def slider_changed(self, pwm):
        """Send a command when the user moves the PWM slider."""
        self.wave_running = False
        self.output_stopped = False
        pwm = self.set_pwm_value(pwm)
        self.send_control_command(pwm)

    def text_pwm_changed(self):
        """Clamp typed PWM input and send the resulting command."""
        try:
            pwm = int(self.pwm_input.text())
        except ValueError:
            pwm = 0
        self.wave_running = False
        self.output_stopped = False
        pwm = self.set_pwm_value(pwm)
        self.send_control_command(pwm)

    def direction_changed(self, heating):
        """Send the current PWM with the newly selected direction."""
        self.direction_switch.setText("HEAT" if heating else "COOL")
        self.send_control_command(self.pwm_slider.value())

    def read_measurements(self):
        """Read all currently available lines and process valid ones."""
        if (not self.output_stopped and self.last_valid_host_time is not None
                and time.monotonic() - self.last_valid_host_time > SERIAL_STALE_SECONDS):
            self.stop_output()
            self.wave_status.setText("Stopped: no valid DATA for 5 s. Check serial connection.")
        if self.serial_port.in_waiting:
            self.serial_buffer += self.serial_port.read(self.serial_port.in_waiting)

        while b"\n" in self.serial_buffer:
            raw_line, self.serial_buffer = self.serial_buffer.split(b"\n", 1)
            # Process complete newline-terminated records only. Never drop bad bytes
            # silently: a corrupted number must not become a valid measurement.
            try:
                line = raw_line.decode("ascii")
                measurement = parse_measurement(line)
            except UnicodeDecodeError:
                measurement = None
            if measurement is None:
                self.rejected_lines += 1  # Includes headers and diagnostics ignored.
                self.update_line_counts()
                continue
            self.accepted_lines += 1
            self.update_line_counts()

            # Store every accepted RAW field before processing the next command.
            self.csv_writer.writerow([measurement[key] for key in CSV_COLUMNS])
            self.csv_file.flush()  # Flush every sample, including long acquisitions.
            time_seconds = float(measurement["time_s"])
            temperature = float(measurement["tec_C"])
            pwm = int(measurement["pwm"])
            heat_cool = int(measurement["heat_cool"])
            self.safety_value.setText(measurement["safety"])

            if self.last_measurement_time is not None and time_seconds < self.last_measurement_time:
                self.stop_output()
                self.wave_status.setText("Arduino clock reset: stopped. Restart drive explicitly.")
                for values in (self.times, self.setpoints, self.errors, self.signed_pwms,
                               self.proportional_pwms, self.integral_pwms, self.requested_pwms):
                    values.clear()
                for values in self.sensor_temperatures.values():
                    values.clear()
            self.last_valid_host_time = time.monotonic()
            if self.last_measurement_time is None or time_seconds <= self.last_measurement_time:
                delta_time = 0.0
            else:
                delta_time = time_seconds - self.last_measurement_time
            self.last_measurement_time = time_seconds
            # Rod temperatures never enter PI. Inhibit output on an A0 shutdown.
            self.latest_temperature = temperature if measurement["safety"] == "OK" else None

            if self.wave_running and not self.output_stopped:
                if self.wave_origin_s is None:
                    self.wave_origin_s = time_seconds
                    self.log_event("sine_phase_origin")
                self.setpoint = sine_setpoint(time_seconds, self.wave_origin_s,
                                              self.wave_mean_c, self.wave_amplitude_c, self.wave_period_s)

            if measurement["safety"] != "OK" or temperature <= 5 or temperature >= MAXIMUM_SETPOINT_C:
                self.stop_output()
                self.wave_status.setText("Stopped: TEC interlock (host range 5–56 C). Restart explicitly after recovery.")
                self.send_control_command(0, "HEAT")
                self.set_pwm_value(0)
                self.zero_integral(update_output=False)
                self.proportional_output = 0.0
                self.requested_output = 0.0
                self.applied_output = 0.0
                self.applied_pwm_value.setText("0")
            elif self.output_stopped:
                self.send_control_command(0, "HEAT")
            elif self.control_mode.currentText() == "Open-loop feedforward":
                self.update_feedforward_control()
            elif self.feedback_enabled():
                self.update_feedback_control(delta_time)
            else:
                self.proportional_output = 0.0
                self.integral_output = 0.0
                self.requested_output = float(pwm if heat_cool else -pwm)
                self.applied_output = self.requested_output
                self.proportional_pwm_value.setText("0.00")
                self.integral_pwm_value.setText("0.00")
                self.requested_pwm_value.setText(f"{self.requested_output:.2f}")
                self.applied_pwm_value.setText(f"{self.applied_output:.0f}")

            # Host setpoint and controller command at this Arduino timestamp.
            # This resolves nan for analysis without altering any raw Arduino field.
            self.command_writer.writerow([
                time_seconds, self.setpoint, self.control_mode.currentText(),
                self.proportional_gain, self.integral_gain,
                self.requested_output, self.applied_output,
                "sine" if self.wave_running else "constant", self.wave_mean_c,
                self.wave_amplitude_c, self.wave_period_s, self.wave_origin_s,
                ((2 * math.pi * (time_seconds - self.wave_origin_s) / self.wave_period_s)
                 if self.wave_origin_s is not None else ""),
                self.segment_id, self.ambient_input.text(), self.heating_chi_input.text(),
                self.cooling_chi_input.text(), self.feedforward_source,
                self.output_stopped, time.monotonic(),
            ])
            self.command_file.flush()
            error = self.setpoint - temperature

            setpoint = self.setpoint
            self.times.append(time_seconds)
            # Store uncorrected temperatures under the same names used in CSV.
            for key in TEMPERATURE_COLUMNS:
                value = float(measurement[key])
                self.sensor_temperatures[key].append(value)
                self.sensor_values[key].setText(f"{value:.2f} C")
            self.setpoints.append(setpoint)
            self.errors.append(error)
            self.signed_pwms.append(float(pwm if heat_cool else -pwm))
            self.proportional_pwms.append(self.proportional_output)
            self.integral_pwms.append(self.integral_output)
            self.requested_pwms.append(self.requested_output)

            self.temperature_value.setText(f"{temperature:.2f} C")
            self.pwm_value.setText(str(pwm))
            self.direction_value.setText("HEAT" if heat_cool else "COOL")
            self.time_value.setText(f"{time_seconds:.2f} s")
            self.error_value.setText(f"{error:.2f} C")
            self.current_setpoint_value.setText(f"{self.setpoint:.2f} C")
            oldest_time = time_seconds - WINDOW_DURATION_SECONDS
            while self.times and self.times[0] < oldest_time:
                self.times.popleft()
                for values in self.sensor_temperatures.values():
                    values.popleft()
                self.setpoints.popleft()
                self.errors.popleft()
                self.signed_pwms.popleft()
                self.proportional_pwms.popleft()
                self.integral_pwms.popleft()
                self.requested_pwms.popleft()

        # Refresh both plots after processing the available serial data.
        # Each trace reads the deque with its own CSV column name.
        for key, curve in self.sensor_curves.items():
            curve.setData(list(self.times), list(self.sensor_temperatures[key]))
        self.setpoint_curve.setData(list(self.times), list(self.setpoints))
        self.error_curve.setData(list(self.times), list(self.errors))
        self.pwm_curve.setData(list(self.times), list(self.signed_pwms))
        self.proportional_pwm_curve.setData(
            list(self.times), list(self.proportional_pwms)
        )
        self.integral_pwm_curve.setData(list(self.times), list(self.integral_pwms))
        self.requested_pwm_curve.setData(list(self.times), list(self.requested_pwms))

    def log_event(self, event):
        """Save control changes without splitting the continuous raw record."""
        if self.event_file is None:
            return
        record = dict(event=event, arduino_time_s=self.last_measurement_time,
                      host_time=datetime.now().astimezone().isoformat(),
                      controller=self.control_mode.currentText(), setpoint_C=self.setpoint,
                      kp=self.proportional_gain, ki=self.integral_gain,
                      wave_running=self.wave_running, output_stopped=self.output_stopped,
                      Ta_C=self.wave_mean_c, Tb_C=self.wave_amplitude_c,
                      period_s=self.wave_period_s, phase_origin_s=self.wave_origin_s,
                      segment_id=self.segment_id, feedforward_source=self.feedforward_source)
        self.event_file.write(json.dumps(record) + "\n")
        self.event_file.flush()

    def stop_output(self, checked=False):
        """Stop commands immediately but continue displaying and recording."""
        self.wave_running = False
        self.wave_origin_s = None
        self.output_stopped = True
        self.zero_integral(update_output=False)
        self.proportional_output = self.requested_output = self.applied_output = 0.0
        self.set_pwm_value(0)
        self.send_control_command(0, "HEAT")
        self.wave_status.setText("Output stopped; acquisition continues.")
        self.log_event("stop_zero_output")

    def feedforward_ready(self):
        return self.feedforward_points is not None or self.allow_linear.isChecked()

    def load_feedforward_curve(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Measured steady-state curve", "", "CSV (*.csv)")
        if not path:
            return
        try:
            points = read_feedforward_curve(path)
        except (OSError, ValueError, KeyError) as error:
            QtWidgets.QMessageBox.warning(self, "Invalid curve", str(error))
            return
        self.stop_output()
        self.feedforward_points = points
        # Record contents as well as path so the experiment is reproducible.
        self.feedforward_source = str(Path(path).resolve())
        self.curve_label.setText(f"Measured curve: {path}; {points[0][0]:g}–{points[-1][0]:g} °C")
        self.log_event("measured_curve_loaded")
        self.event_file.write(json.dumps(dict(event="curve_points", points=points)) + "\n")
        self.event_file.flush()

    def start_waveform(self, checked=False):
        """Validate the whole requested range before allowing sine output."""
        try:
            mean, amplitude, period = [float(w.text()) for w in
                                      (self.mean_input, self.amplitude_input, self.period_input)]
            if not all(math.isfinite(v) for v in (mean, amplitude, period)):
                raise ValueError("Settings must be finite numbers.")
            if amplitude < 0 or period < MINIMUM_PERIOD_S:
                raise ValueError("Amplitude must be nonnegative; period must be at least 100 s.")
            if mean-amplitude < MINIMUM_SETPOINT_C or mean+amplitude > MAXIMUM_SETPOINT_C:
                raise ValueError("Entire waveform must remain between 5 and 56 °C.")
            if self.control_mode.currentText() == "Manual":
                raise ValueError("Choose Open-loop feedforward, P-only, or PI first.")
            if self.latest_temperature is None or self.last_valid_host_time is None or time.monotonic()-self.last_valid_host_time > SERIAL_STALE_SECONDS:
                raise ValueError("Wait for fresh, safe TEC measurements before starting.")
            ambient, heat, cool = [float(w.text()) for w in
                                   (self.ambient_input, self.heating_chi_input, self.cooling_chi_input)]
            if not all(math.isfinite(v) for v in (ambient,heat,cool)) or min(heat,cool)<=0:
                raise ValueError("Room temperature must be finite and susceptibilities positive.")
            if self.control_mode.currentText() == "Open-loop feedforward":
                if not self.feedforward_ready():
                    raise ValueError("Load a measured curve, or explicitly select the linear approximation.")
                if self.feedforward_points is not None:
                    invert_curve(self.feedforward_points,mean-amplitude)
                    invert_curve(self.feedforward_points,mean+amplitude)
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self,"Cannot start sine",str(error))
            return
        self.stop_output()
        self.wave_mean_c, self.wave_amplitude_c, self.wave_period_s = mean, amplitude, period
        self.wave_running = True
        self.wave_origin_s = None  # Next complete sample defines phase zero.
        self.output_stopped = False
        self.segment_id += 1
        self.setpoint = mean
        self.wave_status.setText(f"Sine active: {mean-amplitude:g}–{mean+amplitude:g} °C, period {period:g} s. T1 is the analysis boundary.")
        self.log_event("sine_start_or_settings_change")

    def update_feedforward_control(self):
        """Invert equilibrium T(u), without adding P or I feedback."""
        try:
            if self.feedforward_points is not None:
                requested = invert_curve(self.feedforward_points,self.setpoint)
            elif self.allow_linear.isChecked():
                ambient=float(self.ambient_input.text())
                heat=float(self.heating_chi_input.text());cool=float(self.cooling_chi_input.text())
                if not all(math.isfinite(v) for v in (ambient,heat,cool)) or min(heat,cool)<=0:
                    raise ValueError("Invalid feedforward coefficients.")
                self.feedforward_source="linear approximation (not a measured curve)"
                error=self.setpoint-ambient
                requested=error/(heat if error>=0 else cool)
            else:
                raise ValueError("Feedforward source is unavailable.")
        except ValueError as error:
            self.stop_output()
            self.wave_status.setText(str(error))
            return
        applied=max(-PWM_LIMIT,min(PWM_LIMIT,requested))
        direction="HEAT" if applied>=0 else "COOL"
        pwm=int(round(abs(applied)))
        self.proportional_output=self.integral_output=0.0
        self.requested_output=requested
        self.applied_output=float(pwm if direction=="HEAT" else -pwm)
        self.proportional_pwm_value.setText("0.00")
        self.integral_pwm_value.setText("0.00")
        self.requested_pwm_value.setText(f"{requested:.2f}")
        self.applied_pwm_value.setText(f"{self.applied_output:.0f}")
        self.set_pwm_value(pwm)
        self.direction_switch.blockSignals(True)
        self.direction_switch.setChecked(direction=="HEAT")
        self.direction_switch.setText(direction)
        self.direction_switch.blockSignals(False)
        self.send_control_command(pwm,direction)

    def update_line_counts(self):
        """Count accepted samples and all ignored/rejected complete serial lines."""
        self.line_count_value.setText(
            f"Accepted: {self.accepted_lines} | Rejected: {self.rejected_lines}"
        )

    def closeEvent(self, event):
        """Turn the TEC off, then close files and the serial connection."""
        self.timer.stop()
        try:
            self.send_control_command(0, "HEAT")
            self.serial_port.flush()
        except serial.SerialException:
            pass
        self.serial_port.close()
        self.csv_file.close()
        self.command_file.close()
        self.log_event("close_zero_output")
        self.event_file.close()
        event.accept()


def main():
    """Start the display application."""
    application = QtWidgets.QApplication(sys.argv)
    window = TemperatureWindow()
    window.resize(1400, 850)
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()

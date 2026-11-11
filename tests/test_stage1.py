"""Offline checks only: never open a physical serial port or energize the TEC.
Run with the Python installation that has PySide6 and pyqtgraph installed.
"""
import csv
import importlib.util
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gui',ROOT/'python/module_08_rod_acquisition_gui.py')
gui=importlib.util.module_from_spec(spec);spec.loader.exec_module(gui)

class Port:
    def __init__(self,*args,**kwargs):self.incoming=b'';self.sent=[]
    @property
    def in_waiting(self):return len(self.incoming)
    def read(self,n):data=self.incoming[:n];self.incoming=self.incoming[n:];return data
    def write(self,data):self.sent.append(data.decode());return len(data)
    def flush(self):pass
    def close(self):pass

def data(**changes):
    row=dict.fromkeys(gui.CSV_COLUMNS,'nan')
    row.update(time_ms='1000',run_state='STOPPED',control_mode='MANUAL',step='0',
               signed_pwm='0',safety='OK',command_seq='0')
    row.update(dict(zip(('TEC_C','T1_C','T2_C','T3_C','T4_C','T5_C'),('23','23.1','23.2','23.3','23.4','23.5'))))
    row.update(changes)
    return 'DATA,'+','.join(str(row[k]) for k in gui.CSV_COLUMNS)+'\n'

class Stage1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=gui.QtWidgets.QApplication.instance() or gui.QtWidgets.QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.port=Port()
        self.p1=patch.object(gui.serial,'Serial',return_value=self.port);self.p1.start()
        self.p2=patch.object(gui,'RUN_DATA_DIRECTORY',Path(self.temp.name));self.p2.start()
        self.w=gui.TemperatureWindow();self.w.timer.stop()
    def tearDown(self):
        self.w.close();self.p2.stop();self.p1.stop();self.temp.cleanup()
    def receive(self,**kw):
        self.port.incoming+=data(**kw).encode();self.w.read_measurements()
    def test_parser_and_verbatim_storage(self):
        self.assertIsNone(gui.parse_measurement('DATA,'+','.join(gui.CSV_COLUMNS)))
        self.assertIsNone(gui.parse_measurement(data().replace('STOPPED','bogus')))
        self.assertIsNone(gui.parse_measurement(data(signed_pwm=300)))
        self.assertIsNone(gui.parse_measurement(data(TEC_C='nan')))
        self.assertIsNotNone(gui.parse_measurement(data(T3_C='nan')))
        self.receive()
        path=next(p for p in Path(self.temp.name).glob('*.csv') if not p.stem.endswith('_commands'))
        with path.open() as f:row=next(csv.DictReader(f))
        self.assertEqual(tuple(row),gui.CSV_COLUMNS)
        self.assertEqual(row['T4_C'],'23.4')
        self.assertEqual(row['time_ms'],'1000')
        self.assertEqual(row['setpoint_C'],'nan')
        self.assertEqual(self.w.sensor_temperatures['rod4_C'][-1],23.4)
    def test_diagnostics_and_bounds(self):
        for mode,ua,ub in [('Raw PWM sine',40,10),('Heating-only PWM sine',40,10)]:
            vals=[gui.diagnostic_pwm(mode,t,100,ua,ub) for t in (0,25,50,75)]
            self.assertAlmostEqual(vals[1],50 if mode.startswith('Heating') else 10)
            self.assertAlmostEqual(vals[3],30 if mode.startswith('Heating') else -10)
        with self.assertRaises(ValueError):gui.diagnostic_pwm('Heating-only PWM sine',0,100,10,20)
        with self.assertRaises(ValueError):gui.diagnostic_pwm('Raw PWM sine',0,99,0,10)
        self.receive()
        self.w.control_mode.setCurrentText('Raw PWM sine');self.w.start_waveform()
        self.receive(time_ms='2000');self.receive(time_ms='27000')
        fields=self.port.sent[-1].strip().split(',')
        self.assertEqual(fields[0:3],['FRAME','RUNNING','RAW_SINE'])
        self.assertEqual(float(fields[11]),10) # no factor r in diagnostic PWM
        self.assertEqual(fields[10],'nan')
        self.w.stop_output()
        self.assertEqual(self.port.sent[-1].split(',')[1],'STOPPED')
        self.assertEqual(self.port.sent[-1].split(',')[11],'0')
    def test_feedback_compensation_and_antiwindup(self):
        self.receive();self.w.control_mode.setCurrentText('PI');self.w.output_stopped=False
        self.w.latest_temperature=30;self.w.setpoint=20;self.w.proportional_gain=10
        self.w.integral_gain=1;self.w.update_feedback_control(1)
        self.assertEqual(self.w.integral_error,0) # cooling-scaled candidate saturates
        self.assertAlmostEqual(self.w.requested_output,-100*self.w.cooling_ratio())
        self.w.latest_temperature=20;self.w.setpoint=30;self.w.update_feedback_control(0)
        self.assertEqual(self.w.requested_output,100)
    def test_safety_and_echoed_setpoint(self):
        self.receive(run_state='RUNNING',control_mode='PI',setpoint_C='40',signed_pwm=12)
        self.assertEqual(self.w.setpoints[-1],40) # wire echo, not the next host setpoint
        self.w.output_stopped=False
        self.receive(time_ms='2000',T1_C='56')
        self.assertTrue(self.w.output_stopped)
        self.assertEqual(self.port.sent[-1].split(',')[11],'0')
        self.receive(time_ms='3000',safety='SHUTDOWN',run_state='SAFETY',TEC_C='nan')
        self.assertTrue(self.w.output_stopped)

    def test_stalled_write_is_inhibited_without_retry(self):
        self.receive()
        with patch.object(self.port, 'write', side_effect=gui.serial.SerialTimeoutException('USB stalled')) as write:
            self.w.send_control_command(0, 'HEAT')
            self.assertTrue(self.w.serial_failed)
            self.assertTrue(self.w.output_stopped)
            self.w.send_control_command(10, 'HEAT')
            self.assertEqual(write.call_count, 1)
        self.assertIn('Turn TEC power off', self.w.wave_status.text())

    def test_usb_read_error_keeps_window_responsive(self):
        self.port.incoming=data().encode()
        with patch.object(self.port, 'read', side_effect=OSError('USB disconnected')):
            self.w.read_measurements()
        self.assertTrue(self.w.serial_failed)
        self.assertTrue(self.w.output_stopped)
        self.app.processEvents()

if __name__=='__main__':unittest.main()

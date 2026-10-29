import json
import subprocess
import unittest
from unittest.mock import Mock, patch
from smartpark.sensor import TermuxSensor, SensorError
from smartpark.processing import process_window
from smartpark.protocol import valid_control, valid_telemetry
from smartpark.config import Settings


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.sensor=TermuxSensor('Acc','Gyro')

    @patch('smartpark.sensor.subprocess.run')
    def test_sequential_physical_parsing_and_timing(self,run):
        run.side_effect=[Mock(stdout=json.dumps({'Acc':{'values':[0,0,9.81]}})),
                         Mock(stdout=json.dumps({'Gyro':{'values':[1,0,0]}}))]
        sample=self.sensor.read();self.assertEqual(sample['gyro'],[1,0,0]);self.assertEqual(run.call_count,2)
        self.assertGreaterEqual(sample['read_seconds'],0)

    @patch('smartpark.sensor.subprocess.run')
    def test_combined_uses_one_call(self,run):
        run.return_value=Mock(stdout=json.dumps({'Acc':{'values':[0,0,9.81]},'Gyro':{'values':[1,0,0]}}))
        s=TermuxSensor('Acc','Gyro','combined');self.assertEqual(s.read()['gyro'],[1,0,0])
        self.assertEqual(run.call_count,1);self.assertEqual(run.call_args.args[0][2],'Acc,Gyro')

    @patch('smartpark.sensor.subprocess.run')
    def test_empty_malformed_missing_and_nonfinite(self,run):
        for raw in ['', 'not json', '[]', '{}', '{"Acc":{"values":[1,2]}}', '{"Acc":{"values":[1,2,NaN]}}']:
            with self.subTest(raw=raw):
                run.return_value=Mock(stdout=raw)
                with self.assertRaises(SensorError):self.sensor.read()

    @patch('smartpark.sensor.subprocess.run')
    def test_timeout_nonzero_and_missing_command(self,run):
        for error in [subprocess.TimeoutExpired('termux-sensor',12),subprocess.CalledProcessError(1,'termux-sensor'),FileNotFoundError()]:
            run.side_effect=error
            with self.assertRaises(SensorError):self.sensor.read()

    def test_no_fuzzy_sensor_name_fallback(self):
        with self.assertRaises(SensorError):self.sensor.values({'Acc Uncalibrated':{'values':[1,2,3]}},'Acc')

    def test_gap_rejects_unreliable_rotation_without_clamping(self):
        samples=[{'t':t,'acc':[0,0,9.81],'gyro':[2,0,0]} for t in (0,20)]
        self.assertEqual(process_window(samples)['rotation_rad'],40)
        with self.assertRaises(ValueError):process_window(samples,max_gap=3)

    def test_nan_config_rejected(self):
        with self.assertRaises(ValueError):Settings(interval=float('nan'))

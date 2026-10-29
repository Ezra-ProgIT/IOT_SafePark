import unittest
from smartpark.classifier import MovementClassifier
from smartpark.config import Settings
from smartpark.sensor import SimulatedSensor
from smartpark.processing import process_window

class ClassifierTests(unittest.TestCase):
    def test_sustained(self):
        c=MovementClassifier(.7,.25,2)
        self.assertFalse(c.evaluate(.1,.01).alert)
        self.assertFalse(c.evaluate(1,.01).alert)
        self.assertTrue(c.evaluate(1,.01).alert)
        self.assertEqual(c.evaluate(1,.3).status,'HIGH_RISK_MOVEMENT')
        self.assertFalse(c.evaluate(.1,.01).alert)
    def test_rotation_only(self):
        c=MovementClassifier(); self.assertEqual(c.evaluate(.01,.4).status,'ORIENTATION_CHANGE')
    def test_reset(self):
        c=MovementClassifier();c.evaluate(2,2);c.reset();self.assertEqual(c.consecutive,0)
    def test_topic(self):
        self.assertEqual(Settings(project='abcdefgh').topic('control'),'smartpark/abcdefgh/control')
    def test_sensor(self):
        sensor=SimulatedSensor();sensor.set_mode('high')
        samples=[sensor.read() for _ in range(5)]
        # This test checks scenario values, not OS clock resolution in a tight loop.
        for i, sample in enumerate(samples): sample['t'] = i * .4
        v=process_window(samples)
        self.assertGreater(v['motion_rms'],0)
    def test_bad_project(self):
        with self.assertRaises(ValueError): Settings(project='bad')

    def test_exact_boundaries(self):
        c=MovementClassifier()
        self.assertEqual(c.evaluate(.7,0).status,'VIBRATION_DETECTED')
        self.assertEqual(c.evaluate(0,.25).status,'ORIENTATION_CHANGE')
        self.assertEqual(c.evaluate(.7,.25).status,'HIGH_RISK_MOVEMENT')
        self.assertEqual(c.evaluate(.6999,.2499).status,'NORMAL')

    def test_invalid_classification_does_not_advance_window(self):
        c=MovementClassifier()
        for rms,rot in [(float('nan'),0),(0,float('inf')),(-1,0)]:
            with self.assertRaises(ValueError):c.evaluate(rms,rot)
        self.assertEqual(c.consecutive,0)

import math
import unittest
from smartpark.processing import motion_rms, accumulated_rotation, process_window

class ProcessingTests(unittest.TestCase):
    def test_stationary(self):
        self.assertAlmostEqual(motion_rms([[0,0,9.81]]*5), 0)
    def test_rms(self):
        self.assertAlmostEqual(motion_rms([[0,0,10.81],[0,0,8.81]]),1)
    def test_rotation(self):
        self.assertAlmostEqual(accumulated_rotation([(0,[1,0,0]),(.5,[2,0,0]),(1,[2,0,0])]),2)
    def test_invalid(self):
        with self.assertRaises(ValueError): motion_rms([])
        with self.assertRaises(ValueError): accumulated_rotation([(1,[0,0,0]),(.5,[0,0,0])])
    def test_window(self):
        result=process_window([{'t':0,'acc':[0,0,9.81],'gyro':[0,0,0]},{'t':1,'acc':[0,0,9.81],'gyro':[1,0,0]}])
        self.assertEqual(result['sample_count'],2)
        self.assertEqual(result['rotation_rad'],1)

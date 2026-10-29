"""Persistent reader and real local subprocess tests with SYNTHETIC fixture data."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from smartpark.sensor import SensorError, TermuxSensor
from smartpark.sensor_stream import JSONFrames


class FramingTests(unittest.TestCase):
    def test_pretty_concatenated_json_split_at_every_character(self):
        docs = [{'sensor{}"name': {'values': [1, 2, 3]}}, {'Gyro': {'values': [4, 5, 6]}}]
        raw = '\n'.join(json.dumps(d, indent=2) for d in docs)
        frames = JSONFrames()
        actual = []
        for char in raw:
            actual.extend(frames.feed(char))
        self.assertEqual(actual, docs)

    def test_partial_frame_is_not_emitted(self):
        parser = JSONFrames()
        self.assertEqual(parser.feed('{"Acc":'), [])
        self.assertEqual(parser.feed('{"values":[1,2,3]}}'), [{'Acc': {'values': [1, 2, 3]}}])

    def test_bad_json_plain_errors_and_oversize_frames(self):
        for raw in ('ERROR: no sensors', '{"bad": }'):
            with self.subTest(raw=raw), self.assertRaises(SensorError):
                JSONFrames().feed(raw)
        with self.assertRaises(SensorError):
            JSONFrames(limit=20).feed('{"large":"' + 'x'*30)


class StreamProcessTests(unittest.TestCase):
    def setUp(self):
        self.mode = 'normal'
        self.startup = .05
        self.launches = []
        real_popen = subprocess.Popen
        fixture = str(Path(__file__).with_name('fixture_termux_stream.py'))

        def launch(command, **kwargs):
            self.launches.append(command)
            return real_popen([sys.executable, '-u', fixture, '--mode', self.mode,
                               '--startup', str(self.startup)], **kwargs)

        patcher = patch('smartpark.sensor_stream.subprocess.Popen', side_effect=launch)
        patcher.start()
        self.addCleanup(patcher.stop)
        cleanup = patch('smartpark.sensor_stream.SensorStream._cleanup_api')
        self.api_cleanup = cleanup.start()
        self.addCleanup(cleanup.stop)

    def source(self):
        src = TermuxSensor('Acc', 'Gyro', 'stream', stream_timeout=2.5)
        self.addCleanup(src.close)
        return src

    def wait_latest(self, src):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            with src.stream.condition:
                if src.stream.latest is not None:
                    return src.stream.latest.copy()
            time.sleep(.005)
        self.fail('Fixture stream did not produce its first frame')

    def test_many_reads_reuse_one_process_and_skip_old_queued_frames(self):
        src = self.source()
        first = src.read()
        deadline = time.monotonic()+1
        while src.stream.sequence < first['stream_sequence']+3 and time.monotonic() < deadline:
            time.sleep(.01)
        second = src.read()
        self.assertGreaterEqual(second['stream_sequence'], first['stream_sequence']+3)
        self.assertGreater(second['t'], first['t'])
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0], ['termux-sensor', '-s', 'Acc,Gyro', '-d', '100'])
        self.assertNotIn('-n', self.launches[0])

    def test_consumer_does_not_restamp_delayed_sample(self):
        self.mode = 'stall'
        src = self.source()
        src.stream._start()
        original = self.wait_latest(src)
        time.sleep(.1)
        sample = src.read()
        self.assertEqual(sample['t'], original['t'])
        self.assertGreaterEqual(sample['frame_age_seconds'], .09)
        self.assertEqual(sample['timestamp_basis'], 'termux_stdout_receipt')

    def test_same_frame_cannot_be_replayed_when_producer_stalls(self):
        self.mode = 'stall'
        src = self.source()
        src.read()
        src.stream.timeout = .15
        with self.assertRaisesRegex(SensorError, 'Timeout stream'):
            src.read()
        self.assertIsNone(src.stream.process)
        self.api_cleanup.assert_called_once()

    def test_cached_stale_frame_is_rejected(self):
        self.mode = 'stall'
        src = self.source()
        src.stream._start()
        self.wait_latest(src)
        with src.stream.condition:
            src.stream.latest['t'] -= 3
        src.stream.timeout = .15
        with self.assertRaises(SensorError):
            src.read()

    def test_discard_pending_requires_a_new_frame(self):
        src = self.source()
        src.read()
        cutoff = src.stream.sequence
        src.discard_pending()
        sample = src.read()
        self.assertGreater(sample['stream_sequence'], cutoff)

    def test_missing_sensor_does_not_invent_a_vector(self):
        self.mode = 'missing'
        src = self.source()
        src.stream.timeout = .7
        with self.assertRaisesRegex(SensorError, 'kedua nama sensor'):
            src.read()

    def test_incomplete_startup_frames_can_recover(self):
        self.mode = 'warmup'
        src = self.source()
        self.assertEqual(src.read()['gyro'], [1, 0, 0])

    def test_malformed_and_nonfinite_frames_fail_without_simulation(self):
        for mode in ('malformed', 'nan'):
            with self.subTest(mode=mode):
                self.mode = mode
                src = self.source()
                with self.assertRaises(SensorError):
                    src.read()

    def test_eof_can_recover_on_next_read_with_new_process(self):
        self.mode = 'eof'
        src = self.source()
        with self.assertRaisesRegex(SensorError, 'EOF'):
            src.read()
        self.mode = 'normal'
        self.assertEqual(src.read()['gyro'], [1, 0, 0])
        self.assertEqual(len(self.launches), 2)

    def test_close_stops_process_and_reader_and_is_idempotent(self):
        src = self.source()
        src.read()
        process, worker = src.stream.process, src.stream.worker
        src.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(worker.is_alive())
        src.close()
        self.api_cleanup.assert_called_once()

    def test_close_wakes_a_consumer_waiting_for_new_data(self):
        self.mode = 'stall'
        src = self.source()
        src.read()
        errors = []
        waiting = threading.Event()

        def consumer():
            waiting.set()
            try:
                src.read()
            except SensorError as exc:
                errors.append(str(exc))

        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()
        waiting.wait(1)
        src.close()
        consumer_thread.join(2)
        self.assertFalse(consumer_thread.is_alive())
        self.assertTrue(errors)

    def test_invalid_stream_parameters(self):
        with self.assertRaises(ValueError):
            TermuxSensor('Acc', 'Gyro', 'stream', stream_delay_ms=0)

    def test_real_subprocess_with_3586ms_startup_can_publish_a_valid_window(self):
        from smartpark.config import Settings
        from smartpark.edge_a import SensorNode
        self.startup = 3.586
        src = TermuxSensor('Acc', 'Gyro', 'stream', stream_timeout=8)
        self.addCleanup(src.close)

        class LocalOnlyLink:
            def __init__(self, *args):self.documents=[]
            def publish(self, topic, doc):self.documents.append((topic,doc));return True

        with patch('smartpark.edge_a.MQTTLink',LocalOnlyLink):
            node = SensorNode(Settings(interval=.5),src)
        node.armed = True
        result = node.collect_window()
        self.assertIsNotNone(result)
        self.assertGreater(result['acquisition_seconds'],3.5)
        self.assertLess(result['window_seconds'],result['acquisition_seconds']-3)
        self.assertLess(result['max_sample_gap'],3)
        self.assertAlmostEqual(result['rotation_rad'],result['window_seconds'],places=3)
        self.assertEqual(len(self.launches),1)
        self.assertEqual(len(node.link.documents),1)

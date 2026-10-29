"""MQTT delivery tests, with transport mocked and clocks/payloads explicit."""
import queue
import time
import unittest
from unittest.mock import Mock, patch
from smartpark.config import Settings
from smartpark.edge_a import SensorNode
from smartpark.edge_b import ReceiverNode
from smartpark.alarm import Alarm
from smartpark.sensor import SimulatedSensor


class FakeLink:
    def __init__(self, settings, role, subscriptions, handler):
        self.settings=settings; self.events=[]; self.on_connection=None
    def publish(self, suffix, doc):
        self.events.append((suffix, doc.copy()))
        return True


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.pa=patch('smartpark.edge_a.MQTTLink',FakeLink); self.pa.start(); self.addCleanup(self.pa.stop)
        self.pb=patch('smartpark.edge_b.MQTTLink',FakeLink); self.pb.start(); self.addCleanup(self.pb.stop)
        self.s=Settings(project='test_smartpark',minimum_alert_windows=2,cooldown=6)
        self.a=SensorNode(self.s,SimulatedSensor()); self.alarm=Alarm(); self.alarm.trigger=Mock()
        self.b=ReceiverNode(self.s,self.alarm)
        self.clock=time.time()-1

    def control(self, cmd='ARM', request='request1'):
        self.clock+=.01
        doc={'device':'LAPTOP','command':cmd,'request_id':request,'timestamp':self.clock}
        self.b.on_message(self.s.topic('control'),doc)
        self.a.on_message(self.s.topic('control'),doc)
        ack=[d for t,d in self.a.link.events if t=='ack'][-1]
        self.b.on_message(self.s.topic('ack'),ack)
        return doc,ack

    def telemetry(self, seq=1, **changes):
        doc={'device':'HP_A','session':self.a.session,'sequence':seq,'motion_rms':1.5,
             'rotation_rad':.4,'simulated':True,'timestamp':time.time(),
             'state_revision':self.a.revision,'monitoring':True}
        doc.update(changes); self.b.on_message(self.s.topic('sensor/data'),doc)
        return doc

    def decisions(self):
        return [d for t,d in self.b.link.events if t=='status' and 'consecutive' in d]

    def test_dashboard_control_sync_ack_classification_and_alarm(self):
        _,ack=self.control(); self.assertTrue(self.a.armed); self.assertTrue(self.b.armed)
        self.assertEqual([d for t,d in self.b.link.events if t=='ack'][-1]['request_id'],ack['request_id'])
        self.telemetry(1);self.telemetry(2)
        self.assertFalse(self.decisions()[0]['alert']);self.assertTrue(self.decisions()[1]['alert'])
        event=self.b.jobs.get_nowait(); self.b.process_alarm(event);self.alarm.trigger.assert_called_once()

    def test_menu_waits_for_authority_not_local_flag(self):
        self.b.command('ARM');self.assertFalse(self.b.armed)

    def test_b_measures_matching_ping_round_trip_with_monotonic_clock(self):
        with patch('smartpark.edge_b.time.monotonic',return_value=100):
            self.b.command('PING')
        command=self.b.link.events[-1][1]
        self.a.on_message(self.s.topic('control'),command)
        ack=[doc for suffix,doc in self.a.link.events if suffix=='ack'][-1]
        with patch('smartpark.edge_b.time.monotonic',return_value=100.45):
            self.b.on_message(self.s.topic('ack'),ack)
        self.assertAlmostEqual(self.b.last_control_rtt_ms,450)
        self.assertEqual(self.b.pending_requests,{})
        self.assertEqual(self.b.link.events[-1][1]['last_control_rtt_ms'],450)

    def test_duplicate_out_of_order_invalid_and_stale_telemetry(self):
        self.control();self.telemetry(1);self.telemetry(1);self.telemetry(0)
        self.telemetry(2,motion_rms=float('nan'));self.telemetry(2,timestamp=time.time()-60)
        self.telemetry(2,simulated=None);self.telemetry(True)
        self.assertEqual(len(self.decisions()),1)

    def test_gap_resets_sustained_windows(self):
        self.control();self.telemetry(1);self.telemetry(3)
        self.assertFalse(self.decisions()[-1]['alert']);self.assertEqual(self.decisions()[-1]['consecutive'],1)

    def test_cooldown(self):
        self.control();self.telemetry(1);self.telemetry(2);self.telemetry(3)
        self.assertEqual([d['alert'] for d in self.decisions()],[False,True,False])

    def test_disarm_cancels_queued_alarm(self):
        self.control();self.telemetry(1);self.telemetry(2);event=self.b.jobs.get_nowait()
        # Current actual sender timestamp, later than generated ACK.
        self.clock=time.time();self.control('DISARM','off');self.b.process_alarm(event)
        self.assertFalse(self.b.armed);self.assertFalse(self.a.armed);self.alarm.trigger.assert_not_called()
        self.assertEqual(self.b.link.events[-1][1]['alarm_result'],'cancelled')

    def test_alarm_failure_is_reported(self):
        self.control();self.telemetry(1);self.telemetry(2)
        self.alarm.trigger.side_effect=OSError('no termux')
        self.b.process_alarm(self.b.jobs.get_nowait());self.assertEqual(self.b.link.events[-1][1]['alarm_result'],'failed')

    def test_duplicate_control_cannot_rearm_after_newer_disarm(self):
        doc,_=self.control();self.clock=time.time();self.control('DISARM','off')
        self.a.on_message(self.s.topic('control'),doc)
        self.assertFalse(self.a.armed)

    def test_old_command_and_old_ack_cannot_roll_back(self):
        _,old=self.control();self.clock=time.time();self.control('DISARM','off')
        self.b.on_message(self.s.topic('ack'),old);self.assertFalse(self.b.armed)
        doc={'device':'LAPTOP','command':'ARM','request_id':'older','timestamp':self.clock-.5}
        self.a.on_message(self.s.topic('control'),doc);self.assertFalse(self.a.armed)
        self.assertEqual(self.a.link.events[-2][1]['result'],'rejected')

    def test_old_ping_cannot_rearm_during_pending_disarm(self):
        _,old=self.control()
        doc={'device':'LAPTOP','command':'DISARM','request_id':'pending','timestamp':time.time()+.01}
        self.b.on_message(self.s.topic('control'),doc)
        self.b.on_message(self.s.topic('ack'),old);self.assertFalse(self.b.armed)

    def test_reconnect_requires_new_authority_ack(self):
        self.control();self.b.connection_changed(False);self.assertFalse(self.b.synced)
        self.telemetry(1);self.assertEqual(self.decisions(),[])
        self.b.connection_changed(True);self.assertFalse(self.b.armed)
        self.assertTrue(any(t=='control' and d['command']=='PING' for t,d in self.b.link.events))

    def test_sensor_error_and_stale_source_are_not_normal(self):
        self.control();self.telemetry(1,motion_rms=0,rotation_rad=0)
        self.b.last_source_at=time.monotonic()-20;self.b.last_source_timestamp=time.time()-20;self.b.publish_state()
        self.assertEqual(self.b.link.events[-1][1]['status'],'DEVICE_OFFLINE')
        self.b.on_message(self.s.topic('status'),{'device':'HP_A','status':'SENSOR_ERROR','timestamp':time.time()})
        self.b.publish_state();self.assertEqual(self.b.link.events[-1][1]['status'],'SENSOR_ERROR')

    def test_sampling_abort_on_disarm_rearm(self):
        self.a.armed=True
        def read():
            self.a.revision+=2
            return {'t':1,'acc':[0,0,9.81],'gyro':[0,0,0]}
        self.a.source.read=read
        with patch.object(self.a.stopped,'wait'):
            self.assertIsNone(self.a.collect_window())
        self.assertFalse(any(t=='sensor/data' for t,d in self.a.link.events))

    def test_stream_startup_wait_is_outside_the_gyro_integration_window(self):
        clock = [100.0]

        class WarmupStream:
            read_mode = 'stream'
            calls = 0
            def read(self):
                wait = 3.586 if self.calls == 0 else 0
                clock[0] += wait
                self.calls += 1
                return {'t':clock[0], 'acc':[0,0,9.81], 'gyro':[1,0,0], 'read_seconds':wait}

        self.a.source = WarmupStream()
        self.a.armed = True
        def advance(seconds):clock[0] += seconds
        with patch('smartpark.edge_a.time.monotonic',side_effect=lambda:clock[0]), \
                patch.object(self.a.stopped,'wait',side_effect=advance):
            result = self.a.collect_window()
        self.assertAlmostEqual(result['window_seconds'],1.6)
        self.assertAlmostEqual(result['rotation_rad'],1.6)
        self.assertAlmostEqual(result['acquisition_seconds'],5.586)
        self.assertLess(result['max_sample_gap'],3)

    def test_delivery_latency_counts_in_source_age(self):
        self.control();self.telemetry(1,timestamp=time.time()-10)
        self.assertGreaterEqual(self.decisions()[-1]['source_age_seconds'],10)
        self.b.publish_state()
        self.assertGreaterEqual(self.b.link.events[-1][1]['source_age_seconds'],10)

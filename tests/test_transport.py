import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from smartpark.mqttlink import MQTTLink
from smartpark.config import Settings
from smartpark.protocol import AuthorityState, valid_control, valid_telemetry

class MQTTTests(unittest.TestCase):
    def test_paho_client_tls_and_qos_without_network(self):
        import paho.mqtt.client as mqtt
        with patch.object(mqtt,'Client') as factory:
            link=MQTTLink(Settings(),'A',['control'],Mock());client=factory.return_value
            client.subscribe.return_value=(mqtt.MQTT_ERR_SUCCESS,1)
            client.tls_set.assert_called_once();client.will_set.assert_called_once()
            link.on_connection=Mock();link._on_connect(client,None,None,SimpleNamespace(is_failure=False),None)
            client.subscribe.assert_called_once_with(Settings().topic('control'),qos=1)
            link.on_connection.assert_not_called()
            link._on_subscribe(client,None,1,[SimpleNamespace(is_failure=False)],None)
            link.on_connection.assert_called_once_with(True)
            client.publish.return_value.rc=mqtt.MQTT_ERR_SUCCESS
            self.assertTrue(link.publish('ack',{'device':'HP_A'}));self.assertFalse(client.publish.call_args.kwargs['retain'])
            link._on_disconnect(client,None,None,0,None);self.assertFalse(link.publish('ack',{}))

    def test_payload_shape_retained_size_and_unicode(self):
        link=object.__new__(MQTTLink);link.handler=Mock()
        for payload,retain in [(b'[]',False),(b'bad json',False),(b'\xff',False),(b'{}',True),(b' '*17000,False)]:
            link._on_message(None,None,SimpleNamespace(topic='x',payload=payload,retain=retain))
        link.handler.assert_not_called()
        link._on_message(None,None,SimpleNamespace(topic='x',payload=b'{"ok":true}',retain=False))
        link.handler.assert_called_once_with('x',{'ok':True})

    def test_old_session_cannot_return_after_restart(self):
        state=AuthorityState();stamp=time.time()
        def ack(session,ts):return {'device':'HP_A','result':'accepted','monitoring':False,'session':session,'state_revision':0,'timestamp':ts}
        self.assertTrue(state.apply(ack('old',stamp)));self.assertTrue(state.apply(ack('new',stamp+.1)))
        self.assertFalse(state.apply(ack('old',stamp+.2)))

    def test_expired_and_malformed_controls(self):
        base={'device':'LAPTOP','command':'ARM','request_id':'r','timestamp':time.time()}
        self.assertTrue(valid_control(base))
        for change in [{'timestamp':time.time()-20},{'request_id':None},{'command':'REBOOT'},{'timestamp':float('nan')}]:
            self.assertFalse(valid_control({**base,**change}))

    def make_link(self,role='A',subscriptions=None):
        import paho.mqtt.client as mqtt
        factory=patch.object(mqtt,'Client');mock_factory=factory.start();self.addCleanup(factory.stop)
        link=MQTTLink(Settings(),role,subscriptions or [],Mock())
        link.client.subscribe.return_value=(mqtt.MQTT_ERR_SUCCESS,1)
        return link

    def test_retained_presence_immediate_on_connect_without_arm_or_sensor(self):
        for role in ('A','B'):
            link=self.make_link(role)
            link._on_connect(link.client,None,None,SimpleNamespace(is_failure=False),None)
            args=link.client.publish.call_args
            self.assertEqual(args.args[0],Settings().topic('presence/HP_'+role))
            self.assertTrue(args.kwargs['retain'])
            payload=json.loads(args.args[1]);self.assertTrue(payload['online'])
            self.assertEqual(payload['core_version'],'2.1-presence')
            self.assertEqual(payload['sequence'],1)

    def test_will_has_device_specific_topic_and_session(self):
        link=self.make_link('B');call=link.client.will_set.call_args
        self.assertEqual(call.args[0],Settings().topic('presence/HP_B'))
        self.assertTrue(call.kwargs['retain'])
        payload=json.loads(call.args[1]);self.assertFalse(payload['online'])
        self.assertEqual(payload['presence_session'],link.presence_session)

    def test_heartbeat_runs_independently_of_node(self):
        link=self.make_link();link.publish_presence=Mock()
        link.stopped=Mock();link.stopped.wait.side_effect=[False,True]
        link._heartbeat();link.publish_presence.assert_called_once_with()

    def test_callback_waits_for_all_subacks(self):
        link=self.make_link('B',['ack','sensor/data'])
        link.client.subscribe.side_effect=[(0,1),(0,2)];link.on_connection=Mock()
        link._on_connect(link.client,None,None,SimpleNamespace(is_failure=False),None)
        link._on_subscribe(link.client,None,1,[],None);link.on_connection.assert_not_called()
        link._on_subscribe(link.client,None,2,[],None);link.on_connection.assert_called_once_with(True)
        link._on_subscribe(link.client,None,2,[],None);self.assertEqual(link.on_connection.call_count,1)

    def test_reconnect_republishes_presence_immediately(self):
        link=self.make_link()
        for _ in range(2):link._on_connect(link.client,None,None,SimpleNamespace(is_failure=False),None)
        payload=json.loads(link.client.publish.call_args.args[1]);self.assertEqual(payload['sequence'],2)

    def test_graceful_stop_publishes_offline_before_disconnect(self):
        link=self.make_link();link.connected=True
        link.stop()
        payload=json.loads(link.client.publish.call_args.args[1]);self.assertFalse(payload['online'])
        link.client.publish.return_value.wait_for_publish.assert_called_once_with(timeout=2)
        link.client.disconnect.assert_called_once()

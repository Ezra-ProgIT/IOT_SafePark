"""MQTT over TLS, reconnect and subscription restoration; Paho MQTT 2.x."""
import json
import logging
import ssl
import uuid
import threading
import time

CORE_VERSION = '2.1-presence'
PRESENCE_INTERVAL = 3

class MQTTLink:
    def __init__(self, settings, role, subscriptions, handler):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError('Instal dependensi: python -m pip install -r requirements.txt') from exc
        self.mqtt = mqtt
        self.settings = settings
        self.connected = False
        self.handler = handler
        self.on_connection = None
        self.subscriptions = subscriptions
        self.device = 'HP_' + role
        self.presence_session = uuid.uuid4().hex
        self.started_at = time.time()
        self.presence_sequence = 0
        self.presence_lock = threading.Lock()
        self.stopped = threading.Event()
        self.heartbeat_thread = None
        self.pending_subscriptions = set()
        self.subscriptions_ready = False
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f'smartpark_{role}_{uuid.uuid4().hex[:10]}')
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.reconnect_delay_set(min_delay=1, max_delay=15)
        self.client.max_queued_messages_set(100)
        # Separate retained topics prevent A and B overwriting each other's presence.
        self.client.will_set(settings.topic('presence/'+self.device), json.dumps({
            **self.presence_document(False), 'reason': 'connection_lost'}), qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.on_connect_fail = lambda c, u: logging.warning('MQTT gagal tersambung; mencoba ulang')

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.connected = not reason_code.is_failure
        if not self.connected:
            logging.error('MQTT ditolak: %s', reason_code)
            return
        self.pending_subscriptions.clear()
        self.subscriptions_ready = False
        logging.info('MQTT CONNECTED %s core=%s ke %s:%s', self.device, CORE_VERSION,
                     self.settings.host, self.settings.port)
        self.publish_presence()
        for suffix in self.subscriptions:
            result, mid = client.subscribe(self.settings.topic(suffix), qos=1)
            if result != self.mqtt.MQTT_ERR_SUCCESS:
                logging.error('Subscribe gagal: %s', suffix)
                self.publish_presence(False)
                self.connected = False
                client.disconnect()
                return
            self.pending_subscriptions.add(mid)
        if not self.pending_subscriptions:
            self._subscriptions_complete()

    def _subscriptions_complete(self):
        if not self.subscriptions_ready:
            self.subscriptions_ready = True
            if self.on_connection:
                self.on_connection(True)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if mid not in self.pending_subscriptions:
            return
        if any(code.is_failure for code in reason_codes):
            logging.error('Broker menolak subscription %s', mid)
            self.publish_presence(False)
            self.connected = False
            client.disconnect()
            return
        self.pending_subscriptions.discard(mid)
        if not self.pending_subscriptions:
            self._subscriptions_complete()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        self.subscriptions_ready = False
        self.pending_subscriptions.clear()
        if self.on_connection:
            self.on_connection(False)
        logging.warning('MQTT terputus: %s', reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            if msg.retain or len(msg.payload) > 16384:
                return
            document = json.loads(msg.payload.decode('utf-8'))
            if not isinstance(document, dict):
                raise ValueError('Expected object')
            self.handler(msg.topic, document)
        except (ValueError, UnicodeDecodeError, KeyError, TypeError) as exc:
            logging.warning('Payload MQTT diabaikan: %s', exc)

    def start(self):
        self.stopped.clear()
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=45)
        self.client.loop_start()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.heartbeat_thread.start()

    def presence_document(self, online):
        return {'device': self.device, 'online': online, 'core_version': CORE_VERSION,
                'presence_session': self.presence_session, 'started_at': self.started_at,
                'sequence': self.presence_sequence, 'timestamp': time.time()}

    def publish_presence(self, online=True):
        if not self.connected:
            return None
        with self.presence_lock:
            self.presence_sequence += 1
            return self.client.publish(self.settings.topic('presence/'+self.device),
                json.dumps(self.presence_document(online)), qos=1, retain=True)

    def _heartbeat(self):
        # Independent from sensor reads, ARM, classification and the receiver's input menu.
        while not self.stopped.wait(PRESENCE_INTERVAL):
            self.publish_presence()

    def publish(self, suffix, data):
        if not self.connected:
            return False
        info = self.client.publish(self.settings.topic(suffix), json.dumps(data, allow_nan=False), qos=1, retain=False)
        return info.rc == self.mqtt.MQTT_ERR_SUCCESS

    def stop(self):
        self.stopped.set()
        info = self.publish_presence(False)
        if info is not None:
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError) as exc:
                logging.debug('Offline presence belum terkirim: %s', exc)
        self.client.disconnect()
        self.client.loop_stop()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)

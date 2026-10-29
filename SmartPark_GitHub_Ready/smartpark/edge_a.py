"""HP A sensor publisher; authority for monitoring state."""
import argparse
import logging
import threading
import time
import uuid
from .config import Settings
from .mqttlink import MQTTLink
from .processing import process_window
from .sensor import SimulatedSensor, TermuxSensor, SensorError
from .protocol import BoundedCache, valid_control

LOG = logging.getLogger(__name__)


class SensorNode:
    def __init__(self, settings, source):
        self.settings, self.source = settings, source
        self.armed = False
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex
        self.seq = self.revision = 0
        self.last_control = 0
        self.requests = BoundedCache()
        self.sensor_error = None
        self.link = MQTTLink(settings, 'A', ['control'], self.on_message)
        self.link.on_connection = lambda connected: self.publish_state() if connected else None
        self.stopped = threading.Event()

    def publish_state(self):
        with self.lock:
            diagnostics = getattr(self.source, 'diagnostics', None)
            self.link.publish('status', {'device': 'HP_A', 'session': self.session,
                'state_revision': self.revision, 'online': True, 'monitoring': self.armed,
                'status': 'SENSOR_ERROR' if self.sensor_error else ('ARMED' if self.armed else 'DISARMED'),
                'error': self.sensor_error, 'timestamp': time.time(),
                **(diagnostics() if callable(diagnostics) else {})})

    def on_message(self, topic, doc):
        if topic != self.settings.topic('control') or not valid_control(doc):
            return
        with self.lock:
            request = doc['request_id']
            signature = (doc['device'], doc['command'], doc['timestamp'])
            cached = self.requests.get(request)
            if cached:
                if cached[0] == signature:
                    self.link.publish('ack', cached[1])
                return
            cmd = doc['command']
            result, reason = 'accepted', None
            if cmd != 'PING' and doc['timestamp'] <= self.last_control:
                result, reason = 'rejected', 'Perintah lebih lama dari kontrol terakhir'
            elif cmd != 'PING':
                self.armed = cmd == 'ARM'
                self.revision += 1  # also cancels an in-flight pre-command window
                self.last_control = doc['timestamp']
                self.sensor_error = None
            ack = {'device': 'HP_A', 'command': cmd, 'request_id': request,
                'monitoring': self.armed, 'session': self.session, 'state_revision': self.revision,
                'result': result, 'reason': reason, 'timestamp': time.time()}
            self.requests.remember(request, (signature, ack))
            self.link.publish('ack', ack)
            self.publish_state()
        LOG.info('PERINTAH %s -> monitoring=%s (%s)', cmd, self.armed, result)

    def collect_window(self):
        with self.lock:
            revision = self.revision
        samples = []
        start = time.monotonic()
        origin = start
        discard = getattr(self.source, 'discard_pending', None)
        if callable(discard):
            discard()
        for i in range(5):
            with self.lock:
                if not self.armed or self.revision != revision:
                    return None
            sample = self.source.read()
            samples.append(sample)
            # Stream startup can take seconds. It precedes the measured sample window.
            if i == 0 and getattr(self.source, 'read_mode', None) == 'stream':
                origin = sample['t']
            LOG.info('SAMPLE %d read=%.3fs age=%.3fs mode=%s', i+1,
                sample.get('read_seconds', 0), sample.get('frame_age_seconds', 0),
                getattr(self.source, 'read_mode', 'simulated'))
            # Do not add a full sleep after a slow blocking read.
            delay = origin + (i+1)*self.settings.interval/5 - time.monotonic()
            if delay > 0:
                self.stopped.wait(delay)
        metrics = process_window(samples, self.settings.max_sample_gap)
        metrics['acquisition_seconds'] = round(time.monotonic()-start, 4)
        with self.lock:
            if not self.armed or self.revision != revision:
                return None
            self.seq += 1
            self.sensor_error = None
            document = {'device': 'HP_A', 'session': self.session, 'sequence': self.seq,
                'state_revision': self.revision, 'monitoring': True,
                'timestamp': time.time(), 'simulated': isinstance(self.source, SimulatedSensor),
                'adapter_mode': getattr(self.source, 'read_mode', 'simulated'),
                'timestamp_basis': samples[-1].get('timestamp_basis', 'python_read_completion'),
                **metrics}
            ok = self.link.publish('sensor/data', document)
        LOG.info('DATA #%d RMS %.4f ROT %.4f window=%.3fs acquire=%.3fs MQTT=%s', self.seq,
            metrics['motion_rms'], metrics['rotation_rad'], metrics['window_seconds'],
            metrics['acquisition_seconds'], 'QUEUED' if ok else 'OFFLINE')
        return document

    def heartbeat(self):
        while not self.stopped.wait(5):
            self.publish_state()

    def close_source(self):
        close = getattr(self.source, 'close', None)
        if callable(close):
            close()

    def run(self):
        self.link.start()
        worker = threading.Thread(target=self.heartbeat, daemon=True)
        worker.start()
        failures = 0
        LOG.info('HP A siap; menunggu ARM dari HP B/dashboard')
        try:
            while not self.stopped.is_set():
                if not self.armed:
                    self.close_source()
                    self.stopped.wait(.2)
                    continue
                try:
                    attempt_revision = self.revision
                    self.collect_window()
                    failures = 0
                except (SensorError, ValueError) as exc:
                    with self.lock:
                        cancelled = not self.armed or attempt_revision != self.revision
                    if cancelled:
                        self.close_source()
                        continue
                    failures += 1
                    self.sensor_error = str(exc)
                    LOG.error('SENSOR_ERROR: %s (window dibuang, retry=%d)', exc, failures)
                    self.publish_state()
                    self.close_source()
                    self.stopped.wait(min(5, failures))
        except KeyboardInterrupt:
            LOG.info('HP A dihentikan')
        finally:
            self.stopped.set()
            self.close_source()
            self.link.stop()
            worker.join(1)


def main():
    parser = argparse.ArgumentParser(description='SmartPark Edge A')
    parser.add_argument('--mode', choices=('simulated', 'termux'), default='simulated')
    parser.add_argument('--scenario', choices=('normal', 'minor', 'suspicious', 'high'), default='high')
    parser.add_argument('--acc-name')
    parser.add_argument('--gyro-name')
    parser.add_argument('--sensor-read-mode', choices=('sequential', 'combined', 'stream'), default='sequential')
    parser.add_argument('--sensor-delay-ms', type=int, default=100, help='Interval stdout streaming Termux, default 100ms')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    settings = Settings()
    if args.mode == 'termux':
        if not args.acc_name or not args.gyro_name:
            parser.error('--mode termux perlu --acc-name dan --gyro-name dari termux-sensor -l')
        source = TermuxSensor(args.acc_name, args.gyro_name, args.sensor_read_mode,
                              stream_delay_ms=args.sensor_delay_ms)
        LOG.info('Sensor fisik: mode=%s; timestamp adalah waktu penerimaan Python', args.sensor_read_mode)
    else:
        source = SimulatedSensor()
        source.set_mode(args.scenario)
        LOG.warning('MODE SIMULASI: bukan data sensor fisik')
    SensorNode(settings, source).run()


if __name__ == '__main__':
    main()

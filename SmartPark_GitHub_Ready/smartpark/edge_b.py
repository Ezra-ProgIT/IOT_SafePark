"""HP B follows HP A ACK state; classification and non-blocking alarm worker."""
import argparse
import logging
import queue
import subprocess
import threading
import time
import uuid
from .alarm import Alarm
from .classifier import MovementClassifier
from .config import Settings
from .mqttlink import MQTTLink
from .protocol import AuthorityState, BoundedCache, fresh, valid_control, valid_telemetry

LOG = logging.getLogger(__name__)


class ReceiverNode:
    def __init__(self, settings, alarm):
        self.settings, self.alarm = settings, alarm
        self.armed = False
        self.authority = AuthorityState()
        self.synced = False
        self.last_alarm = -float('inf')
        self.last_source_at = 0
        self.last_source_timestamp = 0
        self.last_seq = BoundedCache()
        self.classifier = MovementClassifier(settings.rms_threshold, settings.rotation_threshold,
            settings.minimum_alert_windows)
        self.lock = threading.RLock()
        self.last_decision = None
        self.source_error = False
        self.pending_control = None
        self.pending_requests = BoundedCache()
        self.last_control_rtt_ms = None
        self.jobs = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.link = MQTTLink(settings, 'B', ['sensor/data', 'ack', 'control', 'status'], self.on_message)
        self.link.on_connection = self.connection_changed

    def connection_changed(self, connected):
        with self.lock:
            self.armed = False
            self.synced = False
            self.last_decision = None
            self.classifier.reset()
        if connected:
            self.command('PING')  # reconcile with authority, never auto-ARM locally
            self.publish_state()

    def publish_state(self):
        with self.lock:
            age = max(0, time.time()-self.last_source_timestamp) if self.last_source_at else None
            status = 'UNKNOWN' if not self.synced else 'DISARMED' if not self.armed else 'UNKNOWN'
            if self.armed and self.synced:
                if self.source_error:
                    status = 'SENSOR_ERROR'
                elif age is None or age > 15:
                    status = 'DEVICE_OFFLINE'
                elif self.last_decision:
                    status = self.last_decision['status']
            self.link.publish('status', {'device': 'HP_B', 'online': True, 'status': status,
                'armed': self.armed if self.synced else None, 'alert': False,
                'authority_session': self.authority.session, 'state_revision': self.authority.revision,
                'source_age_seconds': round(age, 2) if age is not None else None,
                'last_control_rtt_ms': self.last_control_rtt_ms,
                'alarm_mode': self.alarm.mode, 'timestamp': time.time()})

    def on_message(self, topic, doc):
        with self.lock:
            if topic == self.settings.topic('control'):
                # Stop pending detection immediately on a fresh DISARM, while awaiting authority ACK.
                if (valid_control(doc) and doc['command'] in ('ARM', 'DISARM')
                        and doc['timestamp'] > self.authority.timestamp
                        and (not self.pending_control or doc['timestamp'] > self.pending_control['timestamp'])):
                    self.pending_control = doc
                    self.armed = False
                    self.synced = False
                    self.classifier.reset()
                return
            if topic == self.settings.topic('ack'):
                if self.pending_control:
                    if doc.get('device') != 'HP_A' or doc.get('request_id') != self.pending_control['request_id']:
                        # A later PING can recover a lost control ACK after the control's validity expires.
                        if (time.time()-self.pending_control['timestamp'] <= 15
                                or doc.get('command') != 'PING'):
                            return
                if not self.authority.apply(doc):
                    return
                self.pending_control = None
                sent = self.pending_requests.pop(doc.get('request_id'), None)
                if sent and sent[0] == doc.get('command'):
                    self.last_control_rtt_ms = round((time.monotonic()-sent[1])*1000, 1)
                    LOG.info('CONTROL_ACK %s request=%s RTT=%.1fms', doc['command'],
                             doc['request_id'], self.last_control_rtt_ms)
                changed = (not self.synced or self.armed != self.authority.armed
                    or getattr(self, 'applied_revision', None) != (self.authority.session, self.authority.revision))
                self.armed, self.synced = self.authority.armed, True
                if changed:
                    self.classifier.reset()
                    self.last_decision = None
                    self.last_source_at = 0
                    self.source_error = False
                self.applied_revision = (self.authority.session, self.authority.revision)
                self.link.publish('ack', {'device': 'HP_B', 'command': doc.get('command'),
                    'request_id': doc.get('request_id'), 'monitoring': self.armed,
                    'authority_session': self.authority.session, 'state_revision': self.authority.revision,
                    'result': 'accepted', 'timestamp': time.time()})
                self.publish_state()
                LOG.info('ACK HP A: %s monitoring=%s revision=%s', doc.get('command'), self.armed, self.authority.revision)
                return
            if topic == self.settings.topic('status'):
                if doc.get('device') == 'HP_A' and (doc.get('online') is False or
                        (fresh(doc) and doc.get('status') == 'SENSOR_ERROR')):
                    self.source_error = True
                    self.classifier.reset()
                    self.last_decision = None
                    self.publish_state()
                return
            if topic != self.settings.topic('sensor/data') or not valid_telemetry(doc) or not fresh(doc, 15):
                return
            if (not self.armed or not self.synced or doc['session'] != self.authority.session
                    or doc.get('state_revision') != self.authority.revision or doc.get('monitoring') is not True):
                return
            seq, session = doc['sequence'], doc['session']
            if seq <= self.last_seq.get(session, 0) or doc['timestamp'] < self.last_source_timestamp:
                return
            now = time.monotonic()
            if seq != self.last_seq.get(session, seq-1)+1 or (self.last_source_at and now-self.last_source_at > 15):
                self.classifier.reset()
            decision = self.classifier.evaluate(doc['motion_rms'], doc['rotation_rad'])
            self.last_seq.remember(session, seq)
            self.last_source_at, self.last_source_timestamp = now, doc['timestamp']
            self.source_error = False
            should_alarm = decision.alert and now-self.last_alarm >= self.settings.cooldown
            event = {'device': 'HP_B', 'status': decision.status, 'armed': True,
                'alert': should_alarm, 'consecutive': decision.consecutive,
                'motion_rms': doc['motion_rms'], 'rotation_rad': doc['rotation_rad'],
                'source_simulated': doc['simulated'], 'source_device': 'HP_A',
                'source_session': session, 'source_sequence': seq,
                'source_timestamp': doc['timestamp'], 'source_age_seconds': max(0,time.time()-doc['timestamp']),
                'authority_session': session, 'state_revision': self.authority.revision,
                'alarm_mode': self.alarm.mode, 'alarm_result': 'queued' if should_alarm else 'idle',
                'event_id': f'{session}:{seq}', 'timestamp': time.time()}
            self.last_decision = event
            if should_alarm:
                try:
                    self.jobs.put_nowait(event.copy())
                    self.last_alarm = now
                except queue.Full:
                    event['alarm_result'] = 'busy'
            self.link.publish('status', event)
            LOG.info('HP A #%d %s RMS=%.4f ROT=%.4f WIN=%d ALERT=%s', seq,
                decision.status, doc['motion_rms'], doc['rotation_rad'], decision.consecutive, should_alarm)

    def process_alarm(self, event):
        with self.lock:
            valid = (self.synced and self.armed and self.authority.session == event['source_session']
                and self.authority.revision == event['state_revision'] and fresh(event, 15))
        result = 'cancelled'
        if valid:
            try:
                self.alarm.trigger(event['status'])
                result = 'command_completed' if self.alarm.mode == 'termux' else 'simulated'
            except (OSError, subprocess.SubprocessError) as exc:
                LOG.error('Gagal getar: %s', exc)
                result = 'failed'
        event.update(alarm_result=result, alarm_update=True, timestamp=time.time())
        self.link.publish('status', event)

    def alarm_worker(self):
        while not self.stopped.is_set():
            try:
                event = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            self.process_alarm(event)

    def command(self, cmd):
        if cmd not in ('ARM', 'DISARM', 'PING'):
            return False
        request_id = uuid.uuid4().hex
        with self.lock:
            self.pending_requests.remember(request_id, (cmd, time.monotonic()))
        ok = self.link.publish('control', {'device': 'HP_B', 'command': cmd,
            'request_id': request_id, 'timestamp': time.time()})
        if not ok:
            with self.lock:
                self.pending_requests.pop(request_id, None)
        LOG.info('CONTROL %s: %s; tunggu ACK HP A', cmd, 'QUEUED' if ok else 'OFFLINE')
        return ok

    def heartbeat(self):
        while not self.stopped.wait(5):
            self.publish_state()
            self.command('PING')

    def run(self):
        self.link.start()
        workers = [threading.Thread(target=fn, daemon=True) for fn in (self.heartbeat, self.alarm_worker)]
        for worker in workers:
            worker.start()
        try:
            while True:
                print('\n[HP B] 1 ARM | 2 DISARM | 3 PING | 4 KELUAR')
                choice = input('Pilih: ').strip()
                if choice in ('1', '2', '3'):
                    self.command({'1': 'ARM', '2': 'DISARM', '3': 'PING'}[choice])
                elif choice == '4':
                    self.command('DISARM')
                    time.sleep(.5)
                    break
        except (KeyboardInterrupt, EOFError):
            LOG.info('HP B dihentikan')
        finally:
            self.stopped.set()
            self.link.stop()
            for worker in workers:
                worker.join(1)


def main():
    parser = argparse.ArgumentParser(description='SmartPark Edge B')
    parser.add_argument('--alarm', choices=('simulated', 'termux'), default='simulated')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    if args.alarm == 'simulated':
        LOG.warning('MODE SIMULASI ALARM: tidak ada getar fisik')
    ReceiverNode(Settings(), Alarm(args.alarm)).run()


if __name__ == '__main__':
    main()

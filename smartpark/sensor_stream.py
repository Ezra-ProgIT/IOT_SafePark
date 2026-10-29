"""Persistent Termux stdout reader. Times refer to frame receipt, not Android events."""
import json
import logging
import os
import signal
import subprocess
import threading
import time
from .sensor import SensorError

LOG = logging.getLogger(__name__)
ADAPTER_VERSION = '2.1.2-stream'


class JSONFrames:
    """Frame concatenated/pretty JSON objects with a bounded incremental buffer."""
    def __init__(self, limit=65536):
        self.limit = limit
        self.parts = []
        self.depth = 0
        self.quoted = False
        self.escaped = False

    def feed(self, chunk):
        documents = []
        for char in chunk:
            if not self.parts:
                if char.isspace():
                    continue
                if char != '{':
                    raise SensorError('Stream Termux bukan objek JSON; periksa Termux:API/nama sensor')
            self.parts.append(char)
            if len(self.parts) > self.limit:
                raise SensorError('Frame sensor melebihi batas 64 KiB')
            if self.quoted:
                if self.escaped:
                    self.escaped = False
                elif char == '\\':
                    self.escaped = True
                elif char == '"':
                    self.quoted = False
            elif char == '"':
                self.quoted = True
            elif char == '{':
                self.depth += 1
            elif char == '}':
                self.depth -= 1
                if self.depth == 0:
                    try:
                        documents.append(json.loads(''.join(self.parts)))
                    except ValueError as exc:
                        raise SensorError('JSON stream sensor tidak valid') from exc
                    self.parts.clear()
        return documents


class SensorStream:
    """One process per active acquisition, latest fresh frame only, no sample replay."""
    def __init__(self, acc_name, gyro_name, parse_values, delay_ms=100, timeout=12):
        if type(delay_ms) is not int or not 50 <= delay_ms <= 2000:
            raise ValueError('Delay stream harus 50–2000 ms')
        if not 0 < timeout <= 60:
            raise ValueError('Timeout stream harus 0–60 detik')
        self.acc_name, self.gyro_name = acc_name, gyro_name
        self.parse_values = parse_values
        self.delay_ms, self.timeout = delay_ms, timeout
        self.condition = threading.Condition()
        self.lifecycle = threading.RLock()
        self.process = None
        self.worker = None
        self.stop_event = threading.Event()
        self.latest = None
        self.sequence = self.delivered = 0
        self.error = None
        self.last_issue = None
        self.last_read_seconds = None

    def _start(self):
        with self.lifecycle:
            if self.process is not None:
                return
            with self.condition:
                self.latest = None
                self.sequence = self.delivered = 0
                self.error = self.last_issue = None
                self.stop_event = threading.Event()
            command = ['termux-sensor', '-s', self.acc_name+','+self.gyro_name,
                       '-d', str(self.delay_ms)]
            try:
                self.process = subprocess.Popen(command, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    text=True, encoding='utf-8', bufsize=1,
                    start_new_session=(os.name == 'posix'))
            except OSError as exc:
                raise SensorError(f'Gagal memulai stream Termux: {exc}') from exc
            LOG.info('ADAPTER %s: satu proses kontinu, delay=%dms; menunggu frame awal',
                     ADAPTER_VERSION, self.delay_ms)
            self.worker = threading.Thread(target=self._read_frames,
                args=(self.process, self.stop_event), daemon=True)
            self.worker.start()

    def _read_frames(self, process, stop):
        frames = JSONFrames()
        failure = None
        try:
            while not stop.is_set():
                chunk = process.stdout.readline(4096)
                if not chunk:
                    raise SensorError('Stream termux-sensor berhenti/EOF')
                for document in frames.feed(chunk):
                    if stop.is_set():
                        return
                    if self.acc_name not in document or self.gyro_name not in document:
                        with self.condition:
                            self.latest = None
                            self.last_issue = 'Frame belum memuat kedua nama sensor yang dipilih'
                        continue
                    acc = self.parse_values(document, self.acc_name)
                    gyro = self.parse_values(document, self.gyro_name)
                    # Timestamp as soon as the complete frame reaches stdout, never at consumer read.
                    sample = {'t': time.monotonic(), 'acc': acc, 'gyro': gyro,
                              'timestamp_basis': 'termux_stdout_receipt'}
                    with self.condition:
                        if stop.is_set():
                            return
                        self.sequence += 1
                        sample['stream_sequence'] = self.sequence
                        self.latest = sample
                        self.last_issue = None
                        self.condition.notify_all()
        except (SensorError, OSError, UnicodeError, ValueError, TypeError) as exc:
            failure = str(exc)
        finally:
            with self.condition:
                if not stop.is_set():
                    self.error = failure or 'Stream sensor berhenti'
                    self.latest = None
                self.condition.notify_all()

    def discard_pending(self):
        with self.condition:
            self.delivered = self.sequence
            self.latest = None

    def read(self):
        started = time.monotonic()
        try:
            self._start()
            deadline = started+self.timeout
            with self.condition:
                while True:
                    if self.stop_event.is_set():
                        raise SensorError('Stream sensor dihentikan')
                    if self.error:
                        raise SensorError(self.error)
                    if self.latest is not None and self.sequence > self.delivered:
                        age = time.monotonic()-self.latest['t']
                        if age <= max(1.0, 3*self.delay_ms/1000):
                            self.delivered = self.sequence
                            sample = self.latest.copy()
                            self.last_read_seconds = time.monotonic()-started
                            sample['read_seconds'] = self.last_read_seconds
                            sample['frame_age_seconds'] = max(0, age)
                            return sample
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        reason = self.last_issue or 'tidak ada frame baru yang valid'
                        raise SensorError(f'Timeout stream {self.timeout}s: {reason}')
                    self.condition.wait(remaining)
        except SensorError:
            self.close()
            raise

    def diagnostics(self):
        with self.condition:
            return {'adapter_mode': 'stream', 'adapter_version': ADAPTER_VERSION,
                    'stream_delay_ms': self.delay_ms, 'stream_frames_seen': self.sequence,
                    'sensor_last_read_seconds': self.last_read_seconds,
                    'sensor_frame_age_seconds': max(0,time.monotonic()-self.latest['t']) if self.latest else None,
                    'timestamp_basis': 'termux_stdout_receipt'}

    def _cleanup_api(self):
        # Release the Android service/listeners BEFORE terminating the CLI pipe.
        # Run one acquisition process on this handset; Termux's sensor service is shared.
        try:
            subprocess.run(['termux-sensor', '-c'], text=True, capture_output=True,
                           timeout=3, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.warning('Cleanup Termux sensor gagal: %s', exc)

    def close(self):
        with self.lifecycle:
            process, worker = self.process, self.worker
            self.stop_event.set()
            with self.condition:
                self.latest = None
                self.condition.notify_all()
            if process is None:
                return
            self._cleanup_api()
            self._terminate(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._terminate(process, force=True)
                    process.wait(timeout=2)
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=1)
                if worker.is_alive():
                    self._terminate(process, force=True)
                    worker.join(timeout=1)
            # Never block closing a pipe while another thread is stuck inside readline.
            if process.stdout and (worker is None or not worker.is_alive()):
                process.stdout.close()
            self.process = self.worker = None

    @staticmethod
    def _terminate(process, force=False):
        try:
            if os.name == 'posix':
                # The CLI and its termux-api child belong to our dedicated session.
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif process.poll() is None:
                process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass

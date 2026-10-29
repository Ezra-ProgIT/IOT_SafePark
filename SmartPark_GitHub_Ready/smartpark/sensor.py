"""Sample source abstraction: simulator now; Android via termux-sensor later."""
import json
import math
import random
import subprocess
import time
from .processing import _triplet


class SensorError(RuntimeError):
    """Physical acquisition failed; never replaced with simulated values."""

class SimulatedSensor:
    def __init__(self, seed=42):
        self.random = random.Random(seed)
        self.step = 0
        self.mode = 'normal'

    def set_mode(self, mode):
        if mode not in ('normal', 'minor', 'suspicious', 'high'):
            raise ValueError(f'Unknown mode: {mode}')
        self.mode = mode

    def read(self):
        """A sample every call; synthetic values, not hardware measurements."""
        self.step += 1
        t = time.monotonic()
        if self.mode == 'normal':
            noise, spin = 0.04, 0.006
        elif self.mode == 'minor':
            noise, spin = 0.14, 0.03
        elif self.mode == 'suspicious':
            noise, spin = 1.35, 0.035
        else:
            noise, spin = 1.65, 0.65
        wave = math.sin(self.step * 1.75)
        acc = [noise*wave, noise*0.55, 9.81 + noise*wave]
        acc = [v + self.random.uniform(-noise*.1, noise*.1) for v in acc]
        gyro = [spin, spin*.25, spin*.4]
        return {'t': t, 'acc': acc, 'gyro': gyro}

class TermuxSensor:
    """Optional Android adapter; requires both Termux and Termux:API.

    Sensor data schema and names vary by device; verify `termux-sensor -l`.
    """
    def __init__(self, acc_name, gyro_name, read_mode='sequential', stream_delay_ms=100, stream_timeout=12):
        if not acc_name.strip() or not gyro_name.strip() or acc_name == gyro_name:
            raise ValueError('Dua nama sensor berbeda diperlukan')
        if read_mode not in ('sequential', 'combined', 'stream'):
            raise ValueError('Mode baca sensor tidak valid')
        self.acc_name = acc_name
        self.gyro_name = gyro_name
        self.read_mode = read_mode
        self.stream = None
        if read_mode == 'stream':
            from .sensor_stream import SensorStream
            self.stream = SensorStream(acc_name, gyro_name, self.values,
                                       stream_delay_ms, stream_timeout)

    def _read(self, sensor):
        try:
            raw = subprocess.run(['termux-sensor', '-s', sensor, '-n', '1'],
                                 text=True, capture_output=True, timeout=12, check=True)
            if not raw.stdout.strip():
                raise ValueError('Output termux-sensor kosong')
            doc = json.loads(raw.stdout)
            if not isinstance(doc, dict):
                raise ValueError('Output sensor harus objek JSON')
            return doc
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise SensorError(f'Gagal membaca {sensor!r}: {exc}') from exc

    @staticmethod
    def values(doc, name):
        # Exact match avoids silently selecting an uncalibrated sensor.
        try:
            return _triplet(doc[name]['values'][:3])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise SensorError(f'Sensor {name!r} hilang/invalid; cek termux-sensor -l') from exc

    def read(self):
        if self.stream is not None:
            return self.stream.read()
        start = time.monotonic()
        if self.read_mode == 'combined':
            doc = self._read(self.acc_name + ',' + self.gyro_name)
            acc, gyro = self.values(doc, self.acc_name), self.values(doc, self.gyro_name)
        else:
            acc = self.values(self._read(self.acc_name), self.acc_name)
            gyro = self.values(self._read(self.gyro_name), self.gyro_name)
        end = time.monotonic()
        return {'t': end, 'acc': acc, 'gyro': gyro, 'read_seconds': end-start}

    def discard_pending(self):
        if self.stream is not None:
            self.stream.discard_pending()

    def close(self):
        if self.stream is not None:
            self.stream.close()

    def diagnostics(self):
        return self.stream.diagnostics() if self.stream is not None else {'adapter_mode': self.read_mode}

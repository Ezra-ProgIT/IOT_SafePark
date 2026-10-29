import os
import re
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class Settings:
    project: str = os.getenv('SMARTPARK_PROJECT', 'smartpark_demo_2026')
    host: str = os.getenv('SMARTPARK_HOST', 'broker.hivemq.com')
    port: int = int(os.getenv('SMARTPARK_PORT', '8883'))
    rms_threshold: float = float(os.getenv('SMARTPARK_RMS_THRESHOLD', '0.7'))
    rotation_threshold: float = float(os.getenv('SMARTPARK_ROT_THRESHOLD', '0.25'))
    interval: float = float(os.getenv('SMARTPARK_INTERVAL', '2'))
    minimum_alert_windows: int = int(os.getenv('SMARTPARK_MIN_WINDOWS', '2'))
    cooldown: float = float(os.getenv('SMARTPARK_COOLDOWN', '6'))
    max_sample_gap: float = float(os.getenv('SMARTPARK_MAX_SAMPLE_GAP', '3'))

    def __post_init__(self):
        if not all(isfinite(x) for x in (self.interval, self.cooldown, self.rms_threshold, self.rotation_threshold, self.max_sample_gap)):
            raise ValueError('Konfigurasi numerik harus finite')
        if self.max_sample_gap <= 0:
            raise ValueError('MAX_SAMPLE_GAP harus positif')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,48}', self.project):
            raise ValueError('SMARTPARK_PROJECT harus 8-48 karakter huruf/angka/_/-')
        if self.interval <= 0 or self.minimum_alert_windows < 1 or self.cooldown < 0:
            raise ValueError('Interval, minimum windows, dan cooldown tidak valid')
        if self.rms_threshold <= 0 or self.rotation_threshold <= 0:
            raise ValueError('Threshold harus lebih dari 0')

    def topic(self, suffix: str) -> str:
        return f'smartpark/{self.project}/{suffix}'

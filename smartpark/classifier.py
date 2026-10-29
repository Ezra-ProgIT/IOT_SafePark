"""Rule-based classifications; sustained activity is needed for alerts."""
from collections import deque
from dataclasses import dataclass
from math import isfinite

@dataclass
class Decision:
    status: str
    alert: bool
    consecutive: int

class MovementClassifier:
    def __init__(self, rms_threshold=0.7, rotation_threshold=0.25, min_windows=2):
        self.rms_threshold = rms_threshold
        self.rotation_threshold = rotation_threshold
        self.min_windows = min_windows
        self.consecutive = 0

    def reset(self):
        self.consecutive = 0

    def evaluate(self, rms, rotation):
        if not (isfinite(rms) and isfinite(rotation)) or rms < 0 or rotation < 0:
            raise ValueError('Telemetri invalid')
        a = rms >= self.rms_threshold
        r = rotation >= self.rotation_threshold
        if a and r:
            status = 'HIGH_RISK_MOVEMENT'
        elif a:
            status = 'VIBRATION_DETECTED'
        elif r:
            status = 'ORIENTATION_CHANGE'
        else:
            status = 'NORMAL'
        self.consecutive = self.consecutive + 1 if (a or r) else 0
        return Decision(status=status, alert=self.consecutive >= self.min_windows, consecutive=self.consecutive)

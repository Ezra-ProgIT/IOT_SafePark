"""Pure sensor mathematics, testable without Termux."""
from math import sqrt, isfinite

GRAVITY = 9.81

def _triplet(values):
    if len(values) != 3 or any(not isfinite(float(v)) for v in values):
        raise ValueError('Sampel sensor memerlukan tiga angka finite')
    return [float(v) for v in values]

def motion_rms(samples):
    """RMS of (acceleration magnitude minus gravitational magnitude), m/s²."""
    if not samples:
        raise ValueError('Sampel akselerometer kosong')
    errors = []
    for values in samples:
        x, y, z = _triplet(values)
        errors.append(sqrt(x*x + y*y + z*z) - GRAVITY)
    return sqrt(sum(e*e for e in errors)/len(errors))

def accumulated_rotation(samples, max_gap=None):
    """Integral of angular-speed magnitude, rad. samples: (monotonic_time, (gx,gy,gz)) in rad/s."""
    if len(samples) < 2:
        raise ValueError('Minimal dua sampel gyroscope')
    total = 0.0
    previous_t = None
    for t, vec in samples:
        t = float(t)
        x, y, z = _triplet(vec)
        if not isfinite(t) or (previous_t is not None and t <= previous_t):
            raise ValueError('Timestamp harus finite dan meningkat')
        if previous_t is not None:
            if max_gap is not None and t - previous_t > max_gap:
                raise ValueError(f'Jeda sensor {t-previous_t:.3f}s > {max_gap:.3f}s; integral rotasi tidak andal')
            total += sqrt(x*x + y*y + z*z) * (t-previous_t)
        previous_t = t
    return total

def process_window(samples, max_gap=None):
    """Input list [{t,acc:[...],gyro:[...]}], output RMS and rotation."""
    if len(samples) < 2:
        raise ValueError('Butuh >=2 sampel')
    return {
        'motion_rms': round(motion_rms([s['acc'] for s in samples]), 4),
        'rotation_rad': round(accumulated_rotation([(s['t'],s['gyro']) for s in samples], max_gap), 4),
        'sample_count': len(samples),
        'window_seconds': round(samples[-1]['t'] - samples[0]['t'], 4),
        'max_sample_gap': round(max(b['t']-a['t'] for a,b in zip(samples, samples[1:])), 4),
    }

"""Alert backend: laptop stdout vs Android Termux:API vibration."""
import logging
import subprocess

class Alarm:
    def __init__(self, mode='simulated'):
        if mode not in ('simulated', 'termux'):
            raise ValueError('Unsupported alarm mode')
        self.mode = mode

    def trigger(self, reason):
        if self.mode == 'simulated':
            logging.warning('[SIMULATED ALARM] %s -- tidak ada getaran fisik', reason)
        else:
            subprocess.run(['termux-vibrate', '-d', '700'], timeout=8, check=True)
            logging.warning('[TERMUX VIBRATE] %s', reason)

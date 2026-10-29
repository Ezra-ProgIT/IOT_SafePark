"""Validation shared by edge nodes. Public MQTT is not authenticated."""
import math
import time
from collections import OrderedDict


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def fresh(doc, ttl=45, now=None):
    now = time.time() if now is None else now
    return number(doc.get('timestamp')) and -5 <= now - doc['timestamp'] <= ttl


def valid_control(doc):
    return (doc.get('device') in ('LAPTOP', 'HP_B')
            and doc.get('command') in ('ARM', 'DISARM', 'PING')
            and isinstance(doc.get('request_id'), str)
            and 1 <= len(doc['request_id']) <= 128 and fresh(doc, 15))


def valid_telemetry(doc):
    return (doc.get('device') == 'HP_A' and fresh(doc)
            and isinstance(doc.get('session'), str) and 1 <= len(doc['session']) <= 128
            and type(doc.get('sequence')) is int and doc['sequence'] > 0
            and type(doc.get('simulated')) is bool
            and all(number(doc.get(k)) and doc[k] >= 0 for k in ('motion_rms', 'rotation_rad')))


class BoundedCache(OrderedDict):
    def remember(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > 256:
            self.popitem(last=False)


class AuthorityState:
    """Only HP A ACKs establish state. Reject duplicate/older revisions and sessions."""
    def __init__(self):
        self.session = None
        self.revision = -1
        self.timestamp = 0
        self.armed = None
        self.retired = BoundedCache()

    def apply(self, doc):
        session, revision = doc.get('session'), doc.get('state_revision')
        if (doc.get('device') != 'HP_A' or doc.get('result') != 'accepted'
                or not fresh(doc, 15) or not isinstance(session, str) or not session
                or type(revision) is not int or revision < 0
                or type(doc.get('monitoring')) is not bool):
            return False
        if session != self.session:
            if session in self.retired or doc['timestamp'] < self.timestamp:
                return False
            if self.session:
                self.retired.remember(self.session, True)
        elif revision < self.revision or doc['timestamp'] < self.timestamp:
            return False
        self.session, self.revision = session, revision
        self.armed, self.timestamp = doc['monitoring'], doc['timestamp']
        return True

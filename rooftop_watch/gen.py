"""Seeded synthetic fleet of dealership rooftops. Nothing here is real data.

Time is an integer number of minutes since a Monday 00:00 UTC, so minute // 60 % 168
is the hour of the week and minute // 1440 % 7 is the weekday (0 = Monday).
"""
import hashlib
import math
import random
from dataclasses import dataclass

MIN_HOUR = 60
MIN_DAY = 1440
MIN_WEEK = 10080

# Local opening hours by weekday: (first open hour, hour it closes). Sunday is closed.
OPEN_HOURS = {0: (7, 18), 1: (7, 18), 2: (7, 18), 3: (7, 18), 4: (7, 18), 5: (8, 14)}
DAY_FACTOR = {0: 1.1, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.5}
DAY_NOISE = 0.25          # sigma of the day-to-day volume multiplier (log scale)
CLOSED_DAY_PROB = 1 / 60  # a rooftop that is closed on a day it would normally open
LATE_UPLOAD_PROB = 0.05   # the dealer-side uploader was offline for a while
DMS_SYNC_MAX_MIN = 240    # the repair order reaches the platform up to this late


@dataclass(frozen=True)
class Rooftop:
    rooftop_id: int
    utc_offset_h: int
    daily_mean: float


def poisson(rng, lam):
    """Knuth's method for small means, a rounded normal for large ones."""
    if lam <= 0:
        return 0
    if lam < 30:
        limit = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))


def make_rooftops(seed, n):
    """n rooftops whose mean daily volume is about 50, the posting's 4,000 a day over 80."""
    rng = random.Random(f"fleet:{seed}")
    rooftops = []
    for rooftop_id in range(1, n + 1):
        offset = rng.choice([-8, -7, -6, -5])
        mean = min(200.0, max(8.0, rng.lognormvariate(math.log(40), 0.6)))
        rooftops.append(Rooftop(rooftop_id, offset, mean))
    return rooftops


def hour_shares(first, last):
    """Share of a day's repairs that close in each open hour; the first two hours run busier."""
    weights = {h: (1.6 if h < first + 2 else 1.0) for h in range(first, last)}
    total = sum(weights.values())
    return {h: w / total for h, w in weights.items()}


def day_events(rng, rooftop, day):
    """UTC minutes at which repairs close on local day `day` (local day 0 is a Monday)."""
    weekday = day % 7
    closed_today = rng.random() < CLOSED_DAY_PROB
    if weekday not in OPEN_HOURS or closed_today:
        return []
    first, last = OPEN_HOURS[weekday]
    noise = rng.lognormvariate(-DAY_NOISE * DAY_NOISE / 2, DAY_NOISE)
    mean = rooftop.daily_mean * DAY_FACTOR[weekday] * noise
    local_midnight = day * MIN_DAY - rooftop.utc_offset_h * MIN_HOUR
    out = []
    for hour, share in hour_shares(first, last).items():
        base = local_midnight + hour * MIN_HOUR
        out.extend(base + rng.randrange(MIN_HOUR) for _ in range(poisson(rng, mean * share)))
    out.sort()
    return out


def rooftop_events(seed, rooftop, days):
    """(sequence, closed_min, uploaded_min, synced_min) for every repair of one rooftop.

    Each rooftop draws from its own generator, so a rooftop is the same in a fleet of 10 and a fleet of 1000.
    """
    rng = random.Random(f"{seed}:{rooftop.rooftop_id}")
    events = []
    for day in range(days):
        for closed in day_events(rng, rooftop, day):
            if rng.random() < LATE_UPLOAD_PROB:
                lag = rng.randint(60, 300)
            else:
                lag = rng.randint(1, 12)
            events.append((len(events) + 1, closed, closed + lag, closed + rng.randint(0, DMS_SYNC_MAX_MIN)))
    return events


def ro_id(rooftop_id, seq):
    return f"RO-{rooftop_id:04d}-{seq:06d}"


def rec_id(rooftop_id, seq):
    return f"REC-{rooftop_id:04d}-{seq:06d}"


# Faults injected into the repair-order and recording feeds. Each repair draws once, so the kinds
# are exclusive. The code is what a check should report; HIDDEN_WRONG_AUDIO is a recording of a
# different repair that is intact in every way a check can see, and no check is meant to catch it.
FAULT_RATES = (
    ("MISSING_RECORDING", 0.03),
    ("ORPHAN_RECORDING", 0.02),
    ("DUPLICATE_CONTENT", 0.01),
    ("EMPTY_FILE", 0.005),
    ("TRUNCATED_FILE", 0.015),
    ("THIN_TRANSCRIPT", 0.02),
    ("HIDDEN_WRONG_AUDIO", 0.01),
)
BYTES_PER_S = 16000       # a 128 kbit/s audio track
QUIET_SHARE = 0.10        # repairs that are mostly tool noise, so the transcript is short and correct
MEDIAN_WPM = 110.0        # words a minute when the technician talks the repair through
QUIET_WPM = 45.0


def pick_fault(rng):
    u = rng.random()
    for code, rate in FAULT_RATES:
        if u < rate:
            return code
        u -= rate
    return None


def rooftop_records(seed, rooftop, events):
    """Turn one rooftop's events into repair-order rows, recording rows and a fault manifest.

    Returns (repair_orders, recordings, faults). A fault row is (code, ro_id, rec_id, rooftop_id)
    with the key the check should name filled in and the other one None.
    """
    rid = rooftop.rooftop_id
    rng = random.Random(f"{seed}:{rid}:rec")
    ros, recs, faults = [], [], []
    earlier = []  # (sha256, ro_id, uploaded_min) of recordings already written for this rooftop
    for seq, closed, uploaded, synced in events:
        ro, rec = ro_id(rid, seq), rec_id(rid, seq)
        duration = int(min(3600.0, max(30.0, rng.lognormvariate(math.log(420), 0.5))))
        nbytes = int(duration * BYTES_PER_S * rng.uniform(0.95, 1.05))
        if rng.random() < QUIET_SHARE:
            wpm = rng.lognormvariate(math.log(QUIET_WPM), 0.5)
        else:
            wpm = max(40.0, rng.gauss(MEDIAN_WPM, 15.0))
        words = round(duration / 60 * wpm)
        sha = hashlib.sha256(f"{seed}:{rid}:{seq}".encode()).hexdigest()
        code = pick_fault(rng)
        # A copy is uploaded after the file it copies, so only earlier uploads can be its source.
        sources = [e for e in earlier if e[2] < uploaded]
        if code == "DUPLICATE_CONTENT" and not sources:
            code = None
        keep_ro, keep_rec = True, True
        if code == "MISSING_RECORDING":
            keep_rec = False
            faults.append((code, ro, None, rid))
        elif code == "ORPHAN_RECORDING":
            keep_ro = False
            faults.append((code, None, rec, rid))
        elif code == "DUPLICATE_CONTENT":
            sha = rng.choice(sources)[0]
            faults.append((code, None, rec, rid))
        elif code == "EMPTY_FILE":
            nbytes = 0
            faults.append((code, None, rec, rid))
        elif code == "TRUNCATED_FILE":
            share = rng.uniform(0.05, 0.4)
            nbytes, words = int(nbytes * share), int(words * share)
            faults.append((code, None, rec, rid))
        elif code == "THIN_TRANSCRIPT":
            words = int(words * rng.uniform(0.0, 0.08))
            faults.append((code, None, rec, rid))
        elif code == "HIDDEN_WRONG_AUDIO":
            faults.append((code, None, rec, rid))
        if keep_ro:
            ros.append((ro, rid, closed, synced))
        if keep_rec:
            recs.append((rec, ro, rid, uploaded, nbytes, duration, sha, words))
            earlier.append((sha, ro, uploaded))
    return ros, recs, faults

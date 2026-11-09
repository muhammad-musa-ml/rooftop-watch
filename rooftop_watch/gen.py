"""Seeded synthetic fleet of dealership rooftops. Nothing here is real data.

Time is an integer number of minutes since a Monday 00:00 UTC, so minute // 60 % 168
is the hour of the week and minute // 1440 % 7 is the weekday (0 = Monday).
"""
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

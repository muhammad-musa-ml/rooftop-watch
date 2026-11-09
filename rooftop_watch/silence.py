"""Quiet-feed detection: has a rooftop gone silent for longer than its own weekly pattern explains?

A rooftop's usual uploads per hour of the week are learned from the four weeks before its last upload.
It is called quiet when the uploads expected since its last one add up to at least `k`. A closed
night or Sunday adds almost nothing, so it takes much longer to reach `k` than a busy morning does.
"""
from dataclasses import dataclass

MIN_WEEK = 10080
HOURS_PER_WEEK = 168
PROFILE_WEEKS = 4
K = 8.0               # expected uploads missed before a rooftop is called quiet
MIN_BASELINE = 50     # uploads a rooftop needs in its profile window before it is judged at all


class Profile:
    """Expected uploads per hour of the week, with a running total so any interval costs one subtraction."""

    def __init__(self, hourly):
        if len(hourly) != HOURS_PER_WEEK:
            raise ValueError("a profile needs one rate for each of the 168 hours of the week")
        self.hourly = list(hourly)
        self.cum = [0.0]
        for rate in self.hourly:
            self.cum.append(self.cum[-1] + rate)
        self.total = self.cum[-1]

    def at(self, minute):
        """Uploads expected between minute 0 and `minute`."""
        week, rest = divmod(minute, MIN_WEEK)
        hour, part = divmod(rest, 60)
        return week * self.total + self.cum[hour] + self.hourly[hour] * part / 60

    def expected(self, start, end):
        return self.at(end) - self.at(start)


def profile_from_times(times, weeks=PROFILE_WEEKS):
    """Profile from upload minutes that cover exactly `weeks` whole weeks, so each hour-bin is seen `weeks` times."""
    counts = [0] * HOURS_PER_WEEK
    for t in times:
        counts[(t // 60) % HOURS_PER_WEEK] += 1
    return Profile([c / weeks for c in counts]), len(times)


def is_quiet(profile, last_upload, now, k=K):
    return profile.expected(last_upload, now) >= k


@dataclass(frozen=True)
class Quiet:
    rooftop_id: int
    last_upload_min: int
    expected_missed: float


def quiet_rooftops(conn, now_min, k=K, weeks=PROFILE_WEEKS, profile_end=None):
    """Rooftops quiet as of `now_min`, worst first.

    The profile window ends at each rooftop's last upload, so an outage never lowers its own baseline.
    Pass `profile_end` to anchor every window at one minute instead (used to replay a fixed clean history).
    """
    span = weeks * MIN_WEEK
    last = dict(conn.execute(
        "SELECT rooftop_id, MAX(uploaded_min) FROM recordings WHERE uploaded_min <= ? GROUP BY rooftop_id", (now_min,)
    ))
    counts = {}
    for rooftop_id, end in last.items():
        end = end if profile_end is None else profile_end
        rows = conn.execute(
            """SELECT uploaded_min FROM recordings WHERE rooftop_id = ? AND uploaded_min >= ? AND uploaded_min < ?""",
            (rooftop_id, end - span, end),
        )
        counts[rooftop_id] = [m for (m,) in rows]
    out = []
    for rooftop_id, times in counts.items():
        if len(times) < MIN_BASELINE:
            continue
        profile, _ = profile_from_times(times, weeks)
        missed = profile.expected(last[rooftop_id], now_min)
        if missed >= k:
            out.append(Quiet(rooftop_id, last[rooftop_id], missed))
    return sorted(out, key=lambda q: -q.expected_missed)

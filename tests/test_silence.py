import random

import pytest

from rooftop_watch import db
from rooftop_watch.silence import MIN_WEEK, Profile, is_quiet, profile_from_times, quiet_rooftops

HOUR = 60


def weekday_profile(per_hour=4.0):
    """Open 9:00 to 17:00 Monday to Friday (UTC), nothing else."""
    hourly = [per_hour if (h // 24) < 5 and 9 <= h % 24 < 17 else 0.0 for h in range(168)]
    return Profile(hourly)


def test_a_profile_needs_168_rates():
    with pytest.raises(ValueError):
        Profile([1.0] * 24)


def test_expected_uploads_add_up_across_any_split_and_week_boundary():
    rng = random.Random(3)
    profile = Profile([rng.random() * 5 for _ in range(168)])
    for _ in range(200):
        a = rng.randrange(0, 4 * MIN_WEEK)
        b = a + rng.randrange(1, 3 * MIN_WEEK)
        c = b + rng.randrange(1, 3 * MIN_WEEK)
        assert profile.expected(a, c) == pytest.approx(profile.expected(a, b) + profile.expected(b, c))
    assert profile.at(0) == 0.0
    assert profile.expected(0, MIN_WEEK) == pytest.approx(profile.total)


def test_a_constant_rate_gives_rate_times_hours():
    profile = Profile([2.0] * 168)
    assert profile.expected(100, 100 + 90) == pytest.approx(3.0)
    assert profile.expected(MIN_WEEK - 30, MIN_WEEK + 30) == pytest.approx(2.0)


def test_profile_is_recovered_from_four_weeks_of_uploads():
    times = [w * MIN_WEEK + h * HOUR + 7 for w in range(4) for h in range(168) if h % 24 == 10]
    profile, n = profile_from_times(times)
    assert n == 28
    assert [i for i, r in enumerate(profile.hourly) if r] == [h for h in range(168) if h % 24 == 10]
    assert all(r == 1.0 for r in profile.hourly if r)


def test_a_closed_weekend_is_not_quiet_but_a_missed_morning_is():
    profile = weekday_profile()
    friday_close = 4 * 1440 + 17 * HOUR
    monday_early = 7 * 1440 + 6 * HOUR
    assert profile.expected(friday_close, monday_early) == 0.0
    assert not is_quiet(profile, friday_close, monday_early, k=8.0)
    tuesday_morning = 1440 + 10 * HOUR
    assert is_quiet(profile, tuesday_morning, tuesday_morning + 3 * HOUR, k=8.0)    # 12 expected uploads missed
    assert not is_quiet(profile, tuesday_morning, tuesday_morning + 1 * HOUR, k=8.0)  # 4 expected


def conn_with_uploads(per_rooftop):
    conn = db.create(":memory:")
    n = 0
    for rooftop_id, times in per_rooftop.items():
        conn.execute("INSERT INTO rooftops VALUES (?, 0, 0)", (rooftop_id,))
        for t in times:
            n += 1
            conn.execute("INSERT INTO recordings VALUES (?, ?, ?, ?, 1, 1, ?, 1)", (f"R{n}", f"RO{n}", rooftop_id, t, f"s{n}"))
    conn.commit()
    return conn


def hourly_uploads(start, end):
    return list(range(start, end, HOUR))


def test_quiet_rooftops_flags_the_one_that_stopped_and_ranks_by_missed_uploads():
    five_weeks = 5 * MIN_WEEK
    now = five_weeks
    conn = conn_with_uploads({
        1: hourly_uploads(0, now),                    # still uploading every hour
        2: hourly_uploads(0, now - 11 * HOUR),        # last upload 12 hours ago
        3: hourly_uploads(0, now - 29 * HOUR),        # last upload 30 hours ago
        4: hourly_uploads(now - 3 * HOUR, now),       # too new to have a baseline
    })
    got = quiet_rooftops(conn, now, k=8.0)
    assert [q.rooftop_id for q in got] == [3, 2]
    assert got[1].expected_missed == pytest.approx(12.0)
    assert got[0].expected_missed == pytest.approx(30.0)
    assert got[1].last_upload_min == now - 12 * HOUR


def test_a_rooftop_with_fewer_uploads_than_the_baseline_needs_is_not_judged(monkeypatch):
    now = 5 * MIN_WEEK
    conn = conn_with_uploads({2: hourly_uploads(0, now - 11 * HOUR)})
    assert [q.rooftop_id for q in quiet_rooftops(conn, now, k=8.0)] == [2]
    monkeypatch.setattr("rooftop_watch.silence.MIN_BASELINE", 10_000)
    assert quiet_rooftops(conn, now, k=8.0) == []


def test_an_outage_does_not_lower_its_own_baseline_unless_the_window_is_forced_to_end_at_now():
    now = 6 * MIN_WEEK
    conn = conn_with_uploads({1: hourly_uploads(0, now - 47 * HOUR)})  # last upload exactly 48 hours ago
    anchored = quiet_rooftops(conn, now, k=1.0)
    assert anchored[0].expected_missed == pytest.approx(48.0)
    contaminated = quiet_rooftops(conn, now, k=1.0, profile_end=now)
    # Window ends at now: the first of those 48 hour-bins has 4 weeks behind it (the upload at its start
    # is in the window), the other 47 have 3. 1 + 47 * 0.75.
    assert contaminated[0].expected_missed == pytest.approx(36.25)


def test_an_upload_after_now_is_not_the_last_upload():
    now = 6 * MIN_WEEK
    conn = conn_with_uploads({1: hourly_uploads(0, now - 47 * HOUR) + [now + 5 * HOUR]})
    found = quiet_rooftops(conn, now, k=1.0)
    assert [q.rooftop_id for q in found] == [1]
    assert found[0].expected_missed == pytest.approx(48.0)


def test_a_rooftop_is_quiet_when_the_expected_uploads_reach_k_exactly():
    now = 6 * MIN_WEEK
    conn = conn_with_uploads({1: hourly_uploads(0, now - 11 * HOUR)})   # last upload 12 hours ago
    assert [q.rooftop_id for q in quiet_rooftops(conn, now, k=12.0)] == [1]
    assert quiet_rooftops(conn, now, k=12.0001) == []


def test_a_rooftop_with_no_uploads_before_now_is_ignored():
    conn = conn_with_uploads({1: hourly_uploads(10 * MIN_WEEK, 11 * MIN_WEEK)})
    assert quiet_rooftops(conn, MIN_WEEK, k=1.0) == []

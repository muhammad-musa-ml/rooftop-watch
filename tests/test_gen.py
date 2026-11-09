from rooftop_watch import db, gen


def local_parts(rooftop, minute):
    local = minute + rooftop.utc_offset_h * gen.MIN_HOUR
    return (local // gen.MIN_DAY) % 7, (local // gen.MIN_HOUR) % 24


def test_same_seed_gives_same_events():
    rooftops = gen.make_rooftops(3, 5)
    a = [gen.rooftop_events(3, r, 14) for r in rooftops]
    b = [gen.rooftop_events(3, r, 14) for r in gen.make_rooftops(3, 5)]
    assert a == b
    other = [gen.rooftop_events(4, r, 14) for r in gen.make_rooftops(4, 5)]
    assert a != other


def test_a_rooftop_is_the_same_in_a_small_and_a_large_fleet():
    small = gen.make_rooftops(9, 10)
    large = gen.make_rooftops(9, 80)
    assert small == large[:10]
    assert gen.rooftop_events(9, small[3], 21) == gen.rooftop_events(9, large[3], 21)


def test_repairs_close_only_in_local_opening_hours_and_never_on_sunday():
    rooftops = gen.make_rooftops(5, 20)
    seen = 0
    for rooftop in rooftops:
        for _, closed, uploaded, synced in gen.rooftop_events(5, rooftop, 28):
            weekday, hour = local_parts(rooftop, closed)
            assert weekday in gen.OPEN_HOURS, "closed on a Sunday"
            first, last = gen.OPEN_HOURS[weekday]
            assert first <= hour < last
            assert closed < uploaded and closed <= synced <= closed + gen.DMS_SYNC_MAX_MIN
            seen += 1
    assert seen > 5000


def test_volume_follows_the_stated_daily_mean():
    rooftops = gen.make_rooftops(2, 40)
    weeks = 8
    got = sum(len(gen.rooftop_events(2, r, 7 * weeks)) for r in rooftops)
    per_week = sum(gen.DAY_FACTOR.values())
    want = sum(r.daily_mean for r in rooftops) * per_week * weeks * (1 - gen.CLOSED_DAY_PROB)
    assert abs(got - want) / want < 0.03


def test_loader_writes_one_recording_per_repair_order():
    conn = db.create(":memory:")
    rooftops = gen.make_rooftops(1, 6)
    db.load_fleet(conn, 1, rooftops, 14)
    ros = conn.execute("SELECT COUNT(*) FROM repair_orders").fetchone()[0]
    recs = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    assert ros == recs > 0
    orphans = conn.execute(
        "SELECT COUNT(*) FROM recordings r LEFT JOIN repair_orders o ON o.ro_id = r.ro_id WHERE o.ro_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    assert conn.execute("SELECT COUNT(*) FROM rooftops").fetchone()[0] == 6

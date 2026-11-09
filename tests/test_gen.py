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


def loaded(seed, rooftops, days):
    conn = db.create(":memory:")
    db.load_fleet(conn, seed, gen.make_rooftops(seed, rooftops), days)
    return conn


def keys(conn, code, column):
    return {r[0] for r in conn.execute(f"SELECT {column} FROM faults WHERE code = ?", (code,))}


def test_every_manifest_row_describes_a_real_defect_in_the_tables():
    conn = loaded(4, 30, 28)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    for ro in keys(conn, "MISSING_RECORDING", "ro_id"):
        assert q("SELECT 1 FROM repair_orders WHERE ro_id = ?", ro)
        assert not q("SELECT 1 FROM recordings WHERE ro_id = ?", ro)
    for rec in keys(conn, "ORPHAN_RECORDING", "rec_id"):
        assert not q("SELECT 1 FROM repair_orders o JOIN recordings r ON r.ro_id = o.ro_id WHERE r.rec_id = ?", rec)
        assert q("SELECT 1 FROM recordings WHERE rec_id = ?", rec)
    for rec in keys(conn, "DUPLICATE_CONTENT", "rec_id"):
        sha, ro, uploaded = q("SELECT sha256, ro_id, uploaded_min FROM recordings WHERE rec_id = ?", rec)[0]
        assert q("SELECT 1 FROM recordings WHERE sha256 = ? AND ro_id <> ? AND uploaded_min < ?", sha, ro, uploaded)
    for rec in keys(conn, "EMPTY_FILE", "rec_id"):
        assert q("SELECT bytes FROM recordings WHERE rec_id = ?", rec)[0][0] == 0
    for rec in keys(conn, "TRUNCATED_FILE", "rec_id"):
        nbytes, duration = q("SELECT bytes, duration_s FROM recordings WHERE rec_id = ?", rec)[0]
        assert 0 < nbytes < 0.4 * 1.05 * duration * gen.BYTES_PER_S
    for rec in keys(conn, "THIN_TRANSCRIPT", "rec_id"):
        words, duration = q("SELECT transcript_words, duration_s FROM recordings WHERE rec_id = ?", rec)[0]
        assert words / (duration / 60) < 0.08 * 1.5 * gen.MEDIAN_WPM


def test_fault_counts_follow_the_stated_rates():
    conn = loaded(6, 60, 56)
    total = conn.execute("SELECT COUNT(*) FROM faults").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM repair_orders").fetchone()[0] + len(keys(conn, "ORPHAN_RECORDING", "rec_id"))
    for code, rate in gen.FAULT_RATES:
        n = conn.execute("SELECT COUNT(*) FROM faults WHERE code = ?", (code,)).fetchone()[0]
        assert abs(n - rate * events) < 5 * (rate * events) ** 0.5 + 3, code
    assert total > 0


def test_loader_writes_every_table():
    conn = loaded(1, 6, 14)
    assert conn.execute("SELECT COUNT(*) FROM rooftops").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM repair_orders").fetchone()[0] > 100
    assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] > 100
    assert conn.execute("SELECT COUNT(*) FROM faults").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM outages").fetchone()[0] == 0   # outages are only written when asked for

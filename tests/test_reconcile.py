from rooftop_watch import db
from rooftop_watch.reconcile import GRACE_MIN, reconcile

DURATION = 600
GOOD_BYTES = DURATION * 16000
GOOD_WORDS = 110 * DURATION // 60


def fresh():
    conn = db.create(":memory:")
    # A baseline of 25 ordinary recordings for rooftop 1, long closed and uploaded.
    for i in range(25):
        add(conn, f"base{i}", closed=100 + i, uploaded=110 + i, synced=105 + i, sha=f"base-sha-{i}")
    return conn


def add(conn, name, rooftop=1, closed=1000, uploaded=1005, synced=1010, ro=True, rec=True,
        nbytes=GOOD_BYTES, duration=DURATION, words=GOOD_WORDS, sha=None, ro_name=None):
    ro_id = ro_name or f"RO-{name}"
    if ro:
        conn.execute("INSERT INTO repair_orders VALUES (?, ?, ?, ?)", (ro_id, rooftop, closed, synced))
    if rec:
        conn.execute("INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (f"REC-{name}", ro_id, rooftop, uploaded, nbytes, duration, sha or f"sha-{name}", words))


def run(conn, now, **kw):
    return {(f.code, f.key) for f in reconcile(conn, now, **kw)}


def test_clean_records_give_no_findings():
    conn = fresh()
    add(conn, "a")
    assert run(conn, 5000) == set()


def test_repair_order_without_recording_is_missing_only_after_the_grace_window():
    conn = fresh()
    add(conn, "a", rec=False, closed=1000, synced=1010)
    assert run(conn, 1000 + GRACE_MIN - 1) == set()
    assert run(conn, 1000 + GRACE_MIN) == {("MISSING_RECORDING", "RO-a")}
    assert run(conn, 1000 + GRACE_MIN, grace_min=0) == {("MISSING_RECORDING", "RO-a")}
    # A recording that arrives later than `now` does not count as arrived at `now`.
    add(conn, "b", closed=1000, uploaded=1300, synced=1010)
    assert ("MISSING_RECORDING", "RO-b") in run(conn, 1200, grace_min=0)
    assert ("MISSING_RECORDING", "RO-b") not in run(conn, 1400, grace_min=0)


def test_repair_order_not_yet_synced_is_not_judged():
    conn = fresh()
    add(conn, "a", rec=False, closed=1000, synced=2000)
    assert run(conn, 1000 + GRACE_MIN + 10) == set()
    assert run(conn, 2000 + GRACE_MIN) == {("MISSING_RECORDING", "RO-a")}


def test_recording_for_an_unknown_repair_order_is_an_orphan_after_the_grace_window():
    conn = fresh()
    add(conn, "a", ro=False, uploaded=1005)
    assert run(conn, 1005 + GRACE_MIN - 1) == set()
    assert run(conn, 1005 + GRACE_MIN) == {("ORPHAN_RECORDING", "REC-a")}
    # The repair order shows up in the platform before `now`: no longer an orphan.
    add(conn, "b", rec=False, closed=900, synced=1100, ro_name="RO-a")
    assert ("ORPHAN_RECORDING", "REC-a") not in run(conn, 1005 + GRACE_MIN)


def test_same_content_on_two_repair_orders_flags_only_the_later_upload():
    conn = fresh()
    add(conn, "a", uploaded=1005, sha="same")
    add(conn, "b", closed=1100, uploaded=1105, synced=1110, sha="same")
    assert run(conn, 5000) == {("DUPLICATE_CONTENT", "REC-b")}


def test_a_retried_upload_for_one_repair_order_is_not_a_duplicate():
    conn = fresh()
    add(conn, "a", sha="same")
    conn.execute("INSERT INTO recordings VALUES ('REC-a-retry', 'RO-a', 1, 1020, ?, ?, 'same', ?)",
                 (GOOD_BYTES, DURATION, GOOD_WORDS))
    assert run(conn, 5000) == set()


def test_empty_short_and_thin_files_get_their_own_code():
    conn = fresh()
    add(conn, "empty", nbytes=0)
    add(conn, "short", nbytes=GOOD_BYTES // 5)
    add(conn, "thin", words=GOOD_WORDS // 20)
    add(conn, "fine")
    assert run(conn, 5000) == {
        ("EMPTY_FILE", "REC-empty"),
        ("TRUNCATED_FILE", "REC-short"),
        ("THIN_TRANSCRIPT", "REC-thin"),
    }


def test_one_code_per_recording_with_file_problems_taking_precedence():
    conn = fresh()
    add(conn, "both", nbytes=GOOD_BYTES // 5, words=GOOD_WORDS // 20)
    assert run(conn, 5000) == {("TRUNCATED_FILE", "REC-both")}


def test_a_very_short_recording_is_not_judged_on_its_transcript():
    conn = fresh()
    add(conn, "tiny", nbytes=30 * 16000, duration=30, words=0)
    assert run(conn, 5000) == set()


def test_thresholds_move_what_is_flagged():
    conn = fresh()
    add(conn, "borderline", words=int(GOOD_WORDS * 0.3))
    assert run(conn, 5000) == set()
    assert run(conn, 5000, thin_fraction=0.4) == {("THIN_TRANSCRIPT", "REC-borderline")}


def test_a_rooftop_with_few_recordings_is_judged_against_the_fleet_median():
    conn = fresh()
    add(conn, "new1", rooftop=2, nbytes=GOOD_BYTES // 5)
    add(conn, "new2", rooftop=2)
    assert run(conn, 5000) == {("TRUNCATED_FILE", "REC-new1")}


def test_window_start_excludes_older_items():
    conn = fresh()
    add(conn, "old", rec=False, closed=200, synced=210)
    add(conn, "recent", rec=False, closed=2000, synced=2010)
    assert run(conn, 9000, since_min=1000) == {("MISSING_RECORDING", "RO-recent")}

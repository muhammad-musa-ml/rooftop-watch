from rooftop_watch import db, gen
from rooftop_watch.reconcile import Finding, reconcile
from rooftop_watch.score import score_reconcile


def conn_with_faults(rows):
    conn = db.create(":memory:")
    conn.executemany("INSERT INTO faults VALUES (?, ?, ?, ?)", rows)
    return conn


def test_caught_missed_and_extra_are_counted_per_code():
    conn = conn_with_faults([
        ("MISSING_RECORDING", "RO-1", None, 1),
        ("MISSING_RECORDING", "RO-2", None, 1),
        ("EMPTY_FILE", None, "REC-9", 1),
        ("HIDDEN_WRONG_AUDIO", None, "REC-7", 1),
        ("HIDDEN_WRONG_AUDIO", None, "REC-8", 1),
    ])
    findings = [
        Finding("MISSING_RECORDING", "RO-1", 1),
        Finding("MISSING_RECORDING", "RO-3", 1),       # never injected
        Finding("THIN_TRANSCRIPT", "REC-7", 1),        # a hidden recording reported for another reason
    ]
    rows = score_reconcile(conn, findings)
    assert rows["MISSING_RECORDING"] == {"injected": 2, "caught": 1, "missed": 1, "extra": 1}
    assert rows["EMPTY_FILE"] == {"injected": 1, "caught": 0, "missed": 1, "extra": 0}
    assert rows["THIN_TRANSCRIPT"] == {"injected": 0, "caught": 0, "missed": 0, "extra": 1}
    assert rows["HIDDEN_WRONG_AUDIO"] == {"injected": 2, "incidental": 1}


def test_a_key_only_counts_under_the_code_it_was_injected_with():
    conn = conn_with_faults([("EMPTY_FILE", None, "REC-9", 1)])
    rows = score_reconcile(conn, [Finding("TRUNCATED_FILE", "REC-9", 1)])
    assert rows["EMPTY_FILE"]["caught"] == 0
    assert rows["TRUNCATED_FILE"]["extra"] == 1


def test_structural_faults_in_a_generated_fleet_are_all_found_with_nothing_extra():
    conn = db.create(":memory:")
    db.load_fleet(conn, 11, gen.make_rooftops(11, 30), 28)
    rows = score_reconcile(conn, reconcile(conn, 30 * gen.MIN_DAY))
    for code in ("MISSING_RECORDING", "ORPHAN_RECORDING", "DUPLICATE_CONTENT", "EMPTY_FILE", "TRUNCATED_FILE"):
        assert rows[code]["injected"] > 50, code
        assert rows[code]["missed"] == 0 and rows[code]["extra"] == 0, (code, rows[code])
    # A few injected thin transcripts sit just above the 0.10 line; the five-seed table shows 1 to 7 of about 1600.
    assert rows["THIN_TRANSCRIPT"]["missed"] <= 0.02 * rows["THIN_TRANSCRIPT"]["injected"]
    assert rows["HIDDEN_WRONG_AUDIO"]["injected"] > 50


def test_without_a_grace_window_in_flight_items_are_reported_as_defects():
    conn = db.create(":memory:")
    db.load_fleet(conn, 11, gen.make_rooftops(11, 30), 28)
    midway = 14 * gen.MIN_DAY + 16 * gen.MIN_HOUR  # a Monday, mid-morning in every U.S. time zone
    with_grace = score_reconcile(conn, reconcile(conn, midway))
    no_grace = score_reconcile(conn, reconcile(conn, midway, grace_min=0))
    assert with_grace["MISSING_RECORDING"]["extra"] == 0 and with_grace["ORPHAN_RECORDING"]["extra"] == 0
    # Observed on this fleet and snapshot: 2 and 200. A recording usually lands before its repair
    # order syncs, so the in-flight cost shows up mostly as orphans.
    assert no_grace["MISSING_RECORDING"]["extra"] > 0
    assert no_grace["ORPHAN_RECORDING"]["extra"] > 100

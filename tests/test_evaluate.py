import pytest

from rooftop_watch import db, evaluate, gen
from rooftop_watch.silence import quiet_rooftops

CLEAN = list(range(0, 700, 60))                         # an upload every hour, 0 to 660
KEPT = [u for u in CLEAN if not 300 <= u < 500]         # uploads 300 to 480 were lost
OUTAGE = [(250, 540, 300)]                              # (start, end, first missed upload)
CHECKS = list(range(0, 700, 15))


def gap(minutes):
    return lambda last, now: now - last >= minutes


def test_a_gap_rule_detects_the_outage_and_raises_no_false_alert():
    delays, false_scheduled, false_skipped, alerts = evaluate.replay(gap(90), KEPT, CLEAN, CHECKS, OUTAGE)
    assert delays == [30]       # the outage makes it fire at 330; the first missed upload was at 300
    assert (false_scheduled, false_skipped, alerts) == (0, 0, 0)


def test_a_rule_that_fires_between_every_pair_of_uploads_pays_in_false_alerts():
    delays, false_scheduled, false_skipped, alerts = evaluate.replay(gap(30), KEPT, CLEAN, CHECKS, OUTAGE)
    assert delays == [0]
    # On the fleet with no outage it fires from 30 minutes after every upload: 30, 90, ... 690 is 12 alerts.
    assert (false_scheduled, false_skipped, alerts) == (12, 0, 12)


def test_an_alert_on_a_skipped_day_is_counted_apart():
    _, false_scheduled, false_skipped, _ = evaluate.replay(gap(30), KEPT, CLEAN, CHECKS, OUTAGE, closed_windows=[(60, 130)])
    assert (false_scheduled, false_skipped) == (11, 1)    # only the alert that starts at 90 is inside the window


def test_a_rule_that_is_always_on_gets_no_credit_and_no_false_alert():
    always = lambda last, now: True
    delays, false_scheduled, false_skipped, alerts = evaluate.replay(always, KEPT, CLEAN, CHECKS, OUTAGE)
    assert delays == [None]     # it fires with and without the outage, so the outage did not make it fire
    assert alerts == 0          # on at the first check: it began before the window


def test_an_alert_already_on_at_the_first_check_is_not_counted_but_the_next_one_is():
    clean = [0, 600]
    checks = list(range(60, 700, 15))
    _, _, _, alerts = evaluate.replay(gap(30), clean, clean, checks, [])
    assert alerts == 1          # on from 30 (before the first check at 60), off at 600, on again from 630


def test_a_rule_that_never_fires_misses_the_outage_and_a_closed_outage_is_not_judged():
    never = lambda last, now: False
    assert evaluate.replay(never, KEPT, CLEAN, CHECKS, OUTAGE)[0] == [None]
    assert evaluate.replay(never, KEPT, CLEAN, CHECKS, [(250, 540, None)])[0] == []


def test_a_rule_that_fires_only_after_the_outage_ends_has_not_detected_it():
    late = lambda last, now: now >= 560
    assert evaluate.replay(late, KEPT, CLEAN, CHECKS, OUTAGE)[0] == [None]


def test_unsorted_uploads_are_refused_because_the_pointer_would_stall_on_a_late_one():
    with pytest.raises(ValueError):
        evaluate.replay(gap(30), [0, 100, 50, 200], CLEAN, CHECKS, OUTAGE)
    with pytest.raises(ValueError):
        evaluate.replay(gap(30), KEPT, [0, 100, 50, 200], CHECKS, OUTAGE)


def test_upload_times_put_a_late_upload_after_the_later_repairs_it_overtook():
    rooftop = gen.make_rooftops(1, 1)[0]
    events = gen.rooftop_events(1, rooftop, 28)
    raw = [e[2] for e in events]
    assert raw != sorted(raw)              # the premise: some late upload overtakes a later repair's
    assert gen.upload_times(events) == sorted(raw)


def test_spread_reports_median_and_range_and_skips_blanks():
    assert evaluate.spread([1, 3, 2]) == "2 (1-3)"
    assert evaluate.spread(["0.5", "", "1.25"], 2) == "0.88 (0.50-1.25)"
    assert evaluate.spread(["", ""]) == "n/a"


def test_by_length_counts_detected_over_judged_per_rule_and_length():
    rows = [
        {"rule": "a", "outage_hours": "4", "delay_min": "10"},
        {"rule": "a", "outage_hours": "4", "delay_min": ""},
        {"rule": "a", "outage_hours": "8", "delay_min": "5"},
        {"rule": "b", "outage_hours": "4", "delay_min": ""},
        {"rule": "b", "outage_hours": "8", "delay_min": ""},
    ]
    assert evaluate.by_length(rows, ["a", "b"]) == [
        "| rule | 4 h | 8 h |", "| --- | --- | --- |", "| a | 1 of 2 | 1 of 1 |", "| b | 0 of 1 | 0 of 1 |"]


def test_format_tune_lists_each_k_and_the_chosen_values():
    result = {"k": 20.0, "k_table": [(12.0, 2.4, 90, 110), (20.0, 0.1, 78, 110)],
              "thin_fraction": 0.1, "thin_f1": {0.05: 0.7962, 0.1: 0.9949}}
    assert evaluate.format_tune(result) == [
        "k: false alerts per 100 rooftop-weeks on scheduled days (pooled over the seeds), outages detected, outages",
        "  k=12: 2.40, 90, 110", "  k=20: 0.10, 78, 110",
        "smallest k at or under 2: 20.0",
        "thin-transcript share of median: F1", "  0.05: 0.7962", "  0.1: 0.9949", "best share: 0.1"]


def test_nearest_rank_percentile_takes_the_value_at_the_rank_not_below_it():
    assert evaluate.nearest_rank(list(range(1, 11)), 0.9) == 9
    assert evaluate.nearest_rank(list(range(1, 24)), 0.9) == 21      # n = 23: rank 21, not the floor index 19
    assert evaluate.nearest_rank([5], 0.9) == 5


def test_the_replay_rule_fires_exactly_where_the_database_check_reports_a_rooftop_quiet():
    seed, rooftops = 5, gen.make_rooftops(5, 6)
    t0 = evaluate.HISTORY_DAYS * gen.MIN_DAY
    t1 = (evaluate.HISTORY_DAYS + evaluate.TEST_DAYS) * gen.MIN_DAY
    conn = db.create(":memory:")
    db.load_fleet(conn, seed, rooftops, evaluate.HISTORY_DAYS + evaluate.TEST_DAYS, outage_range=(t0, t1))
    series = {r.rooftop_id: evaluate.rooftop_series(seed, r) for r in rooftops}
    assert any(details for _, _, details, _, _ in series.values()), "the fleet must have an outage to mean anything"
    checks = list(range(t0 + 7 * 60, t1, 9 * 60))
    firing = 0
    for check in checks:
        from_db = {q.rooftop_id for q in quiet_rooftops(conn, check, k=12.0, profile_end=t0)}
        from_replay = set()
        for rooftop_id, (_, kept, _, _, profile) in series.items():
            fire = evaluate.rules(profile)["quiet k=12"]
            if evaluate.states_at(fire, kept, [check])[0]:
                from_replay.add(rooftop_id)
        assert from_db == from_replay, check
        firing += len(from_db)
    assert firing > 0, "the comparison never saw a rooftop quiet"


def test_one_small_run_writes_every_file_and_the_quiet_rule_beats_a_six_hour_gap_on_false_alerts(tmp_path):
    evaluate.run(tmp_path, seeds=(7,), rooftops=12)
    for name in ("silence-outages.csv", "silence-false-alerts.csv", "reconcile.csv", "thin-sweep.csv", "grace.csv", "summary.md"):
        assert (tmp_path / name).stat().st_size > 0
    false_rows = evaluate.read_csv(tmp_path / "silence-false-alerts.csv")
    assert {r["rule"] for r in false_rows} == {f"quiet k={k:g}" for k in evaluate.K_VALUES} | {f"gap {h}h" for h in evaluate.GAP_HOURS}
    assert evaluate.false_rate(false_rows, "quiet k=12") < evaluate.false_rate(false_rows, "gap 6h") / 10
    outages = evaluate.read_csv(tmp_path / "silence-outages.csv")
    assert any(r["rule"] == "quiet k=12" and r["delay_min"] != "" for r in outages), "quiet k=12 detected nothing"
    summary = (tmp_path / "summary.md").read_text()
    assert "| quiet k=12 |" in summary
    block = summary.split("### Detected by outage length")[1].split("## Reconcile")[0]
    assert "| quiet k=12 | " in block and "| gap 24h | " in block and " of " in block

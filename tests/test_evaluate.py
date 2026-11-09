import pytest

from rooftop_watch import evaluate, gen

KEPT = [0, 60, 120, 180, 240, 540, 600, 660]          # uploads 300 to 480 were lost
OUTAGE = [(250, 540, 300)]                             # (start, end, first missed upload)
CHECKS = list(range(0, 700, 15))


def gap(minutes):
    return lambda last, now: now - last >= minutes


def test_a_gap_rule_detects_the_outage_without_a_false_episode():
    delays, false_open, false_closed, episodes = evaluate.replay(gap(90), KEPT, CHECKS, OUTAGE, 700)
    assert delays == [30]       # first fires at 330, the 300 upload was the first missed
    assert (false_open, false_closed, episodes) == (0, 0, 1)


def test_a_rule_that_fires_between_every_pair_of_uploads_is_detected_instantly_and_pays_in_false_episodes():
    delays, false_open, false_closed, episodes = evaluate.replay(gap(30), KEPT, CHECKS, OUTAGE, 700)
    assert delays == [0]
    # Episodes start at 30, 90, 150, 210 (before the outage), 270 (inside it), then 570, 630, 690.
    assert episodes == 8 and false_open == 7 and false_closed == 0


def test_an_episode_on_a_skipped_day_is_counted_apart_from_open_day_episodes():
    delays, false_open, false_closed, _ = evaluate.replay(gap(30), KEPT, CHECKS, OUTAGE, 700, closed_windows=[(60, 130)])
    assert false_closed == 1 and false_open == 6   # only the episode that starts at 90 falls in the window


def test_a_rule_that_never_fires_misses_the_outage_and_a_closed_outage_is_not_judged():
    never = lambda last, now: False
    assert evaluate.replay(never, KEPT, CHECKS, OUTAGE, 700)[0] == [None]
    assert evaluate.replay(never, KEPT, CHECKS, [(250, 540, None)], 700)[0] == []


def test_a_rule_that_fires_only_after_the_outage_ends_has_not_detected_it():
    late = lambda last, now: now >= 560
    assert evaluate.replay(late, KEPT, CHECKS, OUTAGE, 700)[0] == [None]


def test_unsorted_uploads_are_refused_because_the_pointer_would_stall_on_a_late_one():
    with pytest.raises(ValueError):
        evaluate.replay(gap(30), [0, 100, 50, 200], CHECKS, OUTAGE, 700)


def test_upload_times_put_a_late_upload_after_the_later_repairs_it_overtook():
    rooftop = gen.make_rooftops(1, 1)[0]
    events = gen.rooftop_events(1, rooftop, 28)
    raw = [e[2] for e in events]
    assert raw != sorted(raw)              # the premise: some late upload overtakes a later repair's
    assert gen.upload_times(events) == sorted(raw)


def test_spread_reports_median_and_range_and_skips_blanks():
    assert evaluate.spread([1, 3, 2], 0) == "2 (1-3)"
    assert evaluate.spread(["0.5", "", "1.25"], 2) == "0.88 (0.5-1.25)"
    assert evaluate.spread(["", ""], 2) == "n/a"


def test_one_small_run_writes_every_file_and_the_quiet_rule_beats_a_six_hour_gap_on_false_alerts(tmp_path):
    evaluate.run(tmp_path, seeds=(7,), rooftops=12)
    for name in ("silence.csv", "reconcile.csv", "thin-sweep.csv", "grace.csv", "summary.md"):
        assert (tmp_path / name).stat().st_size > 0
    rows = {r["rule"]: r for r in evaluate.read_csv(tmp_path / "silence.csv")}
    assert set(rows) == {f"quiet k={k:g}" for k in evaluate.K_VALUES} | {f"gap {h}h" for h in evaluate.GAP_HOURS}
    assert float(rows["quiet k=12"]["false_open_per_100_rooftop_weeks"]) < float(rows["gap 6h"]["false_open_per_100_rooftop_weeks"]) / 10
    assert "| quiet k=12 |" in (tmp_path / "summary.md").read_text()

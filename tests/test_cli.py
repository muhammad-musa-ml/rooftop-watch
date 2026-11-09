import sqlite3

import pytest

from rooftop_watch import __main__ as cli
from rooftop_watch import db, gen


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    assert cli.main(["demo", "--seed", "3", "--rooftops", "30", "--days", "56", "--out", str(out)]) == 0
    return str(out / "fleet.db")


def test_demo_refuses_to_overwrite_an_existing_database(demo, tmp_path):
    out = str(tmp_path)
    cli.main(["demo", "--rooftops", "3", "--days", "28", "--out", out])
    with pytest.raises(SystemExit):
        cli.main(["demo", "--rooftops", "3", "--days", "28", "--out", out])


def test_demo_loses_the_recordings_inside_each_outage_and_keeps_the_repair_orders(demo):
    conn = sqlite3.connect(demo)
    rows = conn.execute("SELECT rooftop_id, start_min, end_min FROM outages").fetchall()
    assert rows
    for rooftop, start, end in rows:
        assert conn.execute("SELECT COUNT(*) FROM recordings WHERE rooftop_id = ? AND uploaded_min >= ? AND uploaded_min < ?",
                            (rooftop, start, end)).fetchone()[0] == 0
    # The DMS side kept sending: some repair orders closed inside an outage have no recording.
    lost = conn.execute(
        """SELECT COUNT(*) FROM outages w JOIN repair_orders o ON o.rooftop_id = w.rooftop_id
           AND o.closed_min >= w.start_min AND o.closed_min < w.end_min
           WHERE NOT EXISTS (SELECT 1 FROM recordings r WHERE r.ro_id = o.ro_id)"""
    ).fetchone()[0]
    assert lost > 0


def test_quiet_finds_a_rooftop_at_the_end_of_a_long_outage_and_exits_1(demo, capsys):
    conn = sqlite3.connect(demo)
    long_ones = conn.execute(
        "SELECT rooftop_id, end_min FROM outages WHERE first_missed_min IS NOT NULL AND end_min - start_min >= 24 * 60"
    ).fetchall()
    assert long_ones
    flagged = 0
    for rooftop, end in long_ones:
        code = cli.main(["quiet", demo, "--now", str(end - 1), "--limit", "100"])
        text = capsys.readouterr().out
        if f"rooftop {rooftop}:" in text:
            flagged += 1
            assert code == 1
    assert flagged >= len(long_ones) // 2


def test_quiet_exits_0_when_nothing_is_quiet(tmp_path, capsys):
    path = str(tmp_path / "fleet.db")
    conn = db.create(path)
    db.load_fleet(conn, 2, gen.make_rooftops(2, 5), 56)
    conn.close()
    # Monday 06:00 UTC after day 28: Sunday evening in every U.S. time zone, and every rooftop is closed.
    assert cli.main(["quiet", path, "--now", str(28 * gen.MIN_DAY + 6 * gen.MIN_HOUR)]) == 0
    assert "0 rooftop(s)" in capsys.readouterr().out


def test_reconcile_lists_counts_for_every_code_and_exits_1_on_findings(demo, capsys):
    code = cli.main(["reconcile", demo, "--limit", "3"])
    text = capsys.readouterr().out
    assert code == 1
    for name in ("MISSING_RECORDING", "ORPHAN_RECORDING", "DUPLICATE_CONTENT", "EMPTY_FILE", "TRUNCATED_FILE", "THIN_TRANSCRIPT"):
        assert name in text


def test_a_missing_database_is_an_error():
    with pytest.raises(SystemExit):
        cli.main(["quiet", "no-such-file.db"])


def test_fmt_names_the_weekday_and_clock():
    assert cli.fmt(0) == "day 0 Mon 00:00 UTC"
    assert cli.fmt(3 * 1440 + 14 * 60 + 5) == "day 3 Thu 14:05 UTC"

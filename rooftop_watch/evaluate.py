"""Replay and score the checks over several seeds, then write results/ (csv files and summary.md).

Silence: 80 rooftops, 28 days of clean history to learn each profile, then 28 test days. Every rule is
checked every 15 minutes, twice: on the fleet with outages injected, to see whether an outage makes the
rule fire, and on the same fleet with no outages, to count the alerts nothing explains. Reconcile: 28
days of repairs with faults injected, judged at day 31. Thresholds are chosen on separate seeds.
"""
import csv
import math
import statistics
from bisect import bisect_left
from pathlib import Path

from . import db, gen
from .gen import MIN_DAY, MIN_HOUR, MIN_WEEK
from .reconcile import reconcile
from .score import score_reconcile
from .silence import PROFILE_WEEKS, profile_from_times

ROOFTOPS = 80
HISTORY_DAYS = 28
TEST_DAYS = 28
STEP_MIN = 15
SEEDS = (1, 2, 3, 4, 5)
TUNE_SEEDS = (101, 102, 103)
K_VALUES = (3.0, 5.0, 8.0, 12.0, 20.0)
GAP_HOURS = (6, 12, 24)
THIN_FRACTIONS = (0.05, 0.10, 0.15, 0.25, 0.35)
MAX_FALSE_PER_100 = 2.0   # tuning rule for k: the smallest k at or under this many false alerts per 100 rooftop-weeks on scheduled days


def rules(profile):
    """name -> function(last_upload, now) that is True while the rule is firing."""
    out = {}
    for k in K_VALUES:
        out[f"quiet k={k:g}"] = lambda last, now, k=k: profile.expected(last, now) >= k
    for hours in GAP_HOURS:
        out[f"gap {hours}h"] = lambda last, now, hours=hours: now - last >= hours * MIN_HOUR
    return out


def states_at(fire, uploads, checks):
    """Whether the rule is firing at each check time. `uploads` must be in time order: the pointer below
    stops at the first upload later than the check time, so an unsorted list would leave it on a stale one."""
    if any(b < a for a, b in zip(uploads, uploads[1:])):
        raise ValueError("uploads must be in time order")
    states, j = [], -1
    for t in checks:
        while j + 1 < len(uploads) and uploads[j + 1] <= t:
            j += 1
        states.append(j >= 0 and fire(uploads[j], t))
    return states


def replay(fire, kept, clean, checks, details, closed_windows=()):
    """Delays, false alerts and all alerts for one rule on one rooftop.

    `kept` is the rooftop's uploads with the outages removed, `clean` the same uploads with none removed.
    An outage is detected at the first check inside it, on or after its first missed upload, where the rule
    fires on `kept` and does not fire on `clean`: a rule that was already firing for ordinary reasons, such
    as a closed night, gets no credit. The delay is the minutes from that first missed upload.
    A false alert is an alert that starts on `clean`, where nothing is wrong. An alert already on at the
    first check began before the window and is not counted. It is a skipped-day alert when it starts inside
    a window in which the rooftop skipped a day it normally opens, otherwise a scheduled-day alert.
    Returns (delays, scheduled-day false alerts, skipped-day false alerts, all alerts on clean).
    """
    on_kept = states_at(fire, kept, checks)
    on_clean = states_at(fire, clean, checks)
    starts = [checks[i] for i in range(1, len(on_clean)) if on_clean[i] and not on_clean[i - 1]]
    false_skipped = sum(1 for s in starts if any(a <= s < b for a, b in closed_windows))
    delays = []
    for start, stop, first_missed in details:
        if first_missed is None:
            continue
        hit = next((checks[i] for i in range(bisect_left(checks, first_missed), len(checks))
                    if checks[i] < stop and on_kept[i] and not on_clean[i]), None)
        delays.append(None if hit is None else hit - first_missed)
    return delays, len(starts) - false_skipped, false_skipped, len(starts)


def rooftop_series(seed, rooftop):
    """The data one rooftop's replay needs: clean uploads, uploads with outages removed, the outages as
    (start, end, first missed upload), the windows of skipped days, and the profile learned from clean history.

    Uploads are the recordings the database would hold, so a repair whose recording is missing is not an upload.
    """
    t0 = HISTORY_DAYS * MIN_DAY
    t1 = (HISTORY_DAYS + TEST_DAYS) * MIN_DAY
    events, closed_windows = gen.rooftop_days(seed, rooftop, HISTORY_DAYS + TEST_DAYS)
    _, recs, _ = gen.rooftop_records(seed, rooftop, events)
    clean = sorted(rec[3] for rec in recs)
    kept, details = gen.apply_outages(clean, gen.make_outages(seed, rooftop.rooftop_id, t0, t1))
    profile, _ = profile_from_times([u for u in clean if t0 - PROFILE_WEEKS * MIN_WEEK <= u < t0])
    return clean, kept, details, closed_windows, profile


def silence_seed(seed, rooftops=ROOFTOPS):
    """(outage rows, false-alert rows) for one seed: one outage row per outage and rule, one false-alert row per rule."""
    t0 = HISTORY_DAYS * MIN_DAY
    t1 = (HISTORY_DAYS + TEST_DAYS) * MIN_DAY
    checks = list(range(t0, t1, STEP_MIN))
    outage_rows, totals = [], {}
    for rooftop in gen.make_rooftops(seed, rooftops):
        clean, kept, details, closed_windows, profile = rooftop_series(seed, rooftop)
        judged = [d for d in details if d[2] is not None]
        for name, fire in rules(profile).items():
            delays, false_open, false_closed, _ = replay(fire, kept, clean, checks, details, closed_windows)
            for (start, stop, _), delay in zip(judged, delays):
                outage_rows.append({"seed": seed, "rooftop_id": rooftop.rooftop_id, "rule": name,
                                    "outage_hours": (stop - start) // MIN_HOUR,
                                    "delay_min": "" if delay is None else delay})
            acc = totals.setdefault(name, [0, 0])
            acc[0] += false_open
            acc[1] += false_closed
    false_rows = [{"seed": seed, "rule": name, "rooftop_weeks": rooftops * TEST_DAYS // 7,
                   "false_scheduled_day": o, "false_skipped_day": c} for name, (o, c) in totals.items()]
    return outage_rows, false_rows


def reconcile_seed(seed, rooftops=ROOFTOPS):
    conn = db.create(":memory:")
    db.load_fleet(conn, seed, gen.make_rooftops(seed, rooftops), HISTORY_DAYS)
    end = (HISTORY_DAYS + 3) * MIN_DAY
    by_code = score_reconcile(conn, reconcile(conn, end))
    sweep = []
    for fraction in THIN_FRACTIONS:
        row = score_reconcile(conn, reconcile(conn, end, thin_fraction=fraction))["THIN_TRANSCRIPT"]
        sweep.append({"seed": seed, "thin_fraction": fraction, **row})
    midway = 14 * MIN_DAY + 16 * MIN_HOUR
    grace = []
    for grace_min in (360, 0):
        row = score_reconcile(conn, reconcile(conn, midway, grace_min=grace_min))
        grace.append({"seed": seed, "grace_min": grace_min,
                      "missing_extra": row["MISSING_RECORDING"]["extra"],
                      "orphan_extra": row["ORPHAN_RECORDING"]["extra"]})
    return by_code, sweep, grace


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def thin_f1(rows):
    tp = sum(r["caught"] for r in rows)
    fp = sum(r["extra"] for r in rows)
    fn = sum(r["missed"] for r in rows)
    return 2 * tp / (2 * tp + fp + fn)


def false_rate(false_rows, rule, column="false_scheduled_day"):
    """False alerts per 100 rooftop-weeks for one rule, pooled over the seeds in `false_rows`."""
    rows = [r for r in false_rows if r["rule"] == rule]
    return 100 * sum(int(r[column]) for r in rows) / sum(int(r["rooftop_weeks"]) for r in rows)


def by_length(outages, rule_names):
    """Table rows of outages detected out of outages judged, per rule and outage length."""
    hours = sorted({int(r["outage_hours"]) for r in outages})
    rows = ["| rule | " + " | ".join(f"{h} h" for h in hours) + " |", "| --- |" + " --- |" * len(hours)]
    for rule in rule_names:
        mine = [r for r in outages if r["rule"] == rule]
        cells = []
        for h in hours:
            judged = [r for r in mine if int(r["outage_hours"]) == h]
            cells.append("{} of {}".format(sum(1 for r in judged if r["delay_min"] != ""), len(judged)))
        rows.append(f"| {rule} | " + " | ".join(cells) + " |")
    return rows


def format_tune(result):
    """The lines that `tune` prints and writes to tune.txt."""
    lines = ["k: false alerts per 100 rooftop-weeks on scheduled days (pooled over the seeds), outages detected, outages"]
    for k, false, detected, total in result["k_table"]:
        lines.append(f"  k={k:g}: {false:.2f}, {detected}, {total}")
    lines.append(f"smallest k at or under {MAX_FALSE_PER_100:g}: {result['k']}")
    lines.append("thin-transcript share of median: F1")
    for fraction, f1 in result["thin_f1"].items():
        lines.append(f"  {fraction}: {f1:.4f}")
    lines.append(f"best share: {result['thin_fraction']}")
    return lines


def nearest_rank(values, share):
    ordered = sorted(values)
    return ordered[math.ceil(share * len(ordered)) - 1]


def tune(seeds=TUNE_SEEDS, rooftops=ROOFTOPS):
    """Pick k and the thin-transcript fraction on seeds that the reported runs never use."""
    outage_rows, false_rows = [], []
    for seed in seeds:
        o, f = silence_seed(seed, rooftops)
        outage_rows += o
        false_rows += f
    chosen_k, table = None, []
    for k in K_VALUES:
        rule = f"quiet k={k:g}"
        false = false_rate(false_rows, rule)
        mine = [r for r in outage_rows if r["rule"] == rule]
        table.append((k, false, sum(1 for r in mine if r["delay_min"] != ""), len(mine)))
        if chosen_k is None and false <= MAX_FALSE_PER_100:
            chosen_k = k
    sweeps = [row for seed in seeds for row in reconcile_seed(seed, rooftops)[1]]
    f1 = {f: thin_f1([r for r in sweeps if r["thin_fraction"] == f]) for f in THIN_FRACTIONS}
    return {"k": chosen_k, "k_table": table, "thin_fraction": max(f1, key=f1.get), "thin_f1": f1}


def spread(values, digits=0):
    """'median (min-max)' of a list of numbers written to `digits` decimals; blanks are skipped."""
    values = sorted(float(v) for v in values if v != "")
    if not values:
        return "n/a"
    return f"{statistics.median(values):.{digits}f} ({values[0]:.{digits}f}-{values[-1]:.{digits}f})"


def summarize(out_dir, rooftops=ROOFTOPS):
    """Build summary.md from the csv files. The README's tables are copied from it and from scale.md."""
    out = Path(out_dir)
    outages = read_csv(out / "silence-outages.csv")
    false_rows = read_csv(out / "silence-false-alerts.csv")
    recon = read_csv(out / "reconcile.csv")
    sweep = read_csv(out / "thin-sweep.csv")
    grace = read_csv(out / "grace.csv")
    seeds = sorted({int(r["seed"]) for r in false_rows})
    rule_names = list(dict.fromkeys(r["rule"] for r in false_rows))
    per_seed = {s: sum(1 for r in outages if int(r["seed"]) == s and r["rule"] == rule_names[0]) for s in seeds}
    lines = [f"# Results, seeds {', '.join(map(str, seeds))}", "",
             f"Fleets of {rooftops} rooftops, synthetic data. Counts are pooled over the {len(seeds)} seeds unless a cell says "
             "median and range over seeds.", "",
             "## Quiet feeds", "",
             f"{HISTORY_DAYS} clean days to learn each profile, then {TEST_DAYS} days with outages of 4, 8, 24 or 48 hours "
             f"(0, 1 or 2 per rooftop); each rule is checked every {STEP_MIN} minutes. An outage is detected when, between the "
             "first upload that did not arrive and the end of the outage, the rule fires with the outage and would not fire "
             "without it; the delay runs from that first missed upload. A false alert is an alert that starts on the same "
             "fleet with no outage, split by whether the rooftop was open as scheduled or had skipped a day it normally opens. "
             "Outages with nothing to miss (the rooftop was closed for all of it) are left out.", "",
             "| rule | outages detected | detected, by seed: median (range) | median delay (h) | 90th percentile delay (h) "
             "| false alerts per 100 rooftop-weeks, scheduled days | false alerts per 100 rooftop-weeks, skipped days |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for rule in rule_names:
        mine = [r for r in outages if r["rule"] == rule]
        found = [int(r["delay_min"]) for r in mine if r["delay_min"] != ""]
        shares = [100 * sum(1 for r in mine if int(r["seed"]) == s and r["delay_min"] != "") / per_seed[s]
                  for s in seeds if per_seed[s]]
        false_cells = "{:.1f} | {:.1f}".format(false_rate(false_rows, rule), false_rate(false_rows, rule, "false_skipped_day"))
        if not found:
            lines.append(f"| {rule} | 0 of {len(mine)} | n/a | n/a | n/a | {false_cells} |")
            continue
        lines.append("| {} | {} of {} ({:.0f}%) | {}% | {:.1f} | {:.1f} | {} |".format(
            rule, len(found), len(mine), 100 * len(found) / len(mine), spread(shares),
            statistics.median(found) / 60, nearest_rank(found, 0.9) / 60, false_cells))
    lines += ["", f"Outages judged: {min(per_seed.values())} to {max(per_seed.values())} per seed.", "",
              "### Detected by outage length", "",
              "Outages detected out of outages judged, pooled over the seeds.", ""]
    lines += by_length(outages, rule_names)
    lines += ["",
              "## Reconcile", "",
              f"Judged at day {HISTORY_DAYS + 3}, three days after the {HISTORY_DAYS}-day span. Totals over the {len(seeds)} seeds. "
              "'Missed' is an injected fault no finding with that code names; 'extra' is a finding for something that was not "
              "injected.", "",
              "| code | injected | caught | missed | extra |", "| --- | --- | --- | --- | --- |"]
    for code in dict.fromkeys(r["code"] for r in recon):
        mine = [r for r in recon if r["code"] == code]
        if code == "HIDDEN_WRONG_AUDIO":
            lines.append("| {} | {} | not checked | not checked | {} reported by another check |".format(
                code, sum(int(r["injected"]) for r in mine), sum(int(r["incidental"]) for r in mine)))
            continue
        lines.append("| {} | {} | {} | {} | {} |".format(
            code, *(sum(int(r[col]) for r in mine) for col in ("injected", "caught", "missed", "extra"))))
    lines += ["", "### Thin-transcript threshold", "",
              "Words a minute below this share of the rooftop's median counts as thin. The default is the value picked on the "
              "tuning seeds.", "",
              "| share of median | injected | caught | extra |", "| --- | --- | --- | --- |"]
    for fraction in dict.fromkeys(r["thin_fraction"] for r in sweep):
        mine = [r for r in sweep if r["thin_fraction"] == fraction]
        lines.append("| {} | {} | {} | {} |".format(
            fraction, *(sum(int(r[col]) for r in mine) for col in ("injected", "caught", "extra"))))
    lines += ["", "### Grace window", "",
              "One Monday-morning snapshot (day 14, 16:00 UTC) of each fleet, judged with and without a grace window. Every "
              "finding here is in flight, not defective; cells are the median and range over seeds.", "",
              "| grace (minutes) | missing recordings reported | orphan recordings reported |", "| --- | --- | --- |"]
    for minutes in dict.fromkeys(r["grace_min"] for r in grace):
        mine = [r for r in grace if r["grace_min"] == minutes]
        lines.append("| {} | {} | {} |".format(
            minutes, spread([r["missing_extra"] for r in mine]), spread([r["orphan_extra"] for r in mine])))
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text, newline="\n")
    return text


def run(out_dir, seeds=SEEDS, rooftops=ROOFTOPS):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    outage_rows, false_rows = [], []
    for seed in seeds:
        o, f = silence_seed(seed, rooftops)
        outage_rows += o
        false_rows += f
    write_csv(out / "silence-outages.csv", outage_rows)
    write_csv(out / "silence-false-alerts.csv", false_rows)
    recon, sweep, grace = [], [], []
    for seed in seeds:
        by_code, s, g = reconcile_seed(seed, rooftops)
        for code, vals in by_code.items():
            blank = {"injected": "", "caught": "", "missed": "", "extra": "", "incidental": ""}
            recon.append({"seed": seed, "code": code, **{**blank, **vals}})
        sweep += s
        grace += g
    write_csv(out / "reconcile.csv", recon)
    write_csv(out / "thin-sweep.csv", sweep)
    write_csv(out / "grace.csv", grace)
    summarize(out, rooftops)

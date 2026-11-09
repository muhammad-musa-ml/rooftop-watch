"""Replay and score the checks over several seeds, then write results/ (csv files and summary.md).

Silence: 80 rooftops, 28 days of clean history to learn each profile, then 28 test days in which
outages are injected. Every rule is checked every 15 minutes. Reconcile: 28 days of repairs with
faults injected, judged three days after the last one. Thresholds are chosen on separate seeds.
"""
import csv
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
MAX_FALSE_PER_100 = 2.0   # tuning rule for k: the smallest k at or under this many open-day false alerts per 100 rooftop-weeks


def rules(profile):
    """name -> function(last_upload, now) that is True while the rule is firing."""
    out = {}
    for k in K_VALUES:
        out[f"quiet k={k:g}"] = lambda last, now, k=k: profile.expected(last, now) >= k
    for hours in GAP_HOURS:
        out[f"gap {hours}h"] = lambda last, now, hours=hours: now - last >= hours * MIN_HOUR
    return out


def replay(fire, kept, checks, details, end, closed_windows=()):
    """Delays, false episodes and all episodes for one rule.

    A delay is minutes from the first missed upload to the first check that fires inside the outage, None
    if none does. A false episode starts outside every outage; it is counted as a closed-day episode when it
    starts inside a window in which the rooftop skipped a day it would normally open, otherwise as an
    open-day episode. Returns (delays, false open-day, false closed-day, all episodes).

    `kept` must be in time order: the pointer below stops at the first upload later than the check time.
    """
    if any(b < a for a, b in zip(kept, kept[1:])):
        raise ValueError("uploads must be in time order")
    states, j = [], -1
    for t in checks:
        while j + 1 < len(kept) and kept[j + 1] <= t:
            j += 1
        states.append(fire(kept[j], t))
    starts = [checks[i] for i, on in enumerate(states) if on and (i == 0 or not states[i - 1])]
    windows = []
    for start, stop, _ in details:
        later = [u for u in kept if u >= stop]
        windows.append((start, later[0] if later else end))
    outside = [s for s in starts if not any(a <= s <= b for a, b in windows)]
    false_closed = sum(1 for s in outside if any(a <= s < b for a, b in closed_windows))
    false_open = len(outside) - false_closed
    delays = []
    for start, stop, first_missed in details:
        if first_missed is None:
            continue
        hit = next((checks[i] for i in range(bisect_left(checks, first_missed), len(checks))
                    if checks[i] < stop and states[i]), None)
        delays.append(None if hit is None else hit - first_missed)
    return delays, false_open, false_closed, len(starts)


def silence_seed(seed, rooftops=ROOFTOPS):
    t0 = HISTORY_DAYS * MIN_DAY
    t1 = (HISTORY_DAYS + TEST_DAYS) * MIN_DAY
    checks = list(range(t0, t1, STEP_MIN))
    per_rule = {}
    empty = 0
    for rooftop in gen.make_rooftops(seed, rooftops):
        events, closed_windows = gen.rooftop_days(seed, rooftop, HISTORY_DAYS + TEST_DAYS)
        uploads = gen.upload_times(events)
        profile, _ = profile_from_times([u for u in uploads if t0 - PROFILE_WEEKS * MIN_WEEK <= u < t0])
        kept, details = gen.apply_outages(uploads, gen.make_outages(seed, rooftop.rooftop_id, t0, t1))
        empty += sum(1 for d in details if d[2] is None)
        for name, fire in rules(profile).items():
            delays, false_open, false_closed, _ = replay(fire, kept, checks, details, t1, closed_windows)
            acc = per_rule.setdefault(name, {"delays": [], "open": 0, "closed": 0})
            acc["delays"] += delays
            acc["open"] += false_open
            acc["closed"] += false_closed
    rooftop_weeks = rooftops * TEST_DAYS / 7
    rows = []
    for name, acc in per_rule.items():
        found = sorted(d for d in acc["delays"] if d is not None)
        rows.append({
            "seed": seed, "rule": name, "outages": len(acc["delays"]), "empty_outages": empty,
            "detected": len(found),
            "median_delay_h": round(statistics.median(found) / 60, 2) if found else "",
            "p90_delay_h": round(found[int(0.9 * (len(found) - 1))] / 60, 2) if found else "",
            "false_open_day": acc["open"],
            "false_closed_day": acc["closed"],
            "false_open_per_100_rooftop_weeks": round(100 * acc["open"] / rooftop_weeks, 2),
            "false_closed_per_100_rooftop_weeks": round(100 * acc["closed"] / rooftop_weeks, 2),
        })
    return rows


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


def thin_f1(rows):
    tp = sum(r["caught"] for r in rows)
    fp = sum(r["extra"] for r in rows)
    fn = sum(r["missed"] for r in rows)
    return 2 * tp / (2 * tp + fp + fn)


def tune(seeds=TUNE_SEEDS, rooftops=ROOFTOPS):
    """Pick k and the thin-transcript fraction on seeds that the reported runs never use."""
    silence = [row for seed in seeds for row in silence_seed(seed, rooftops)]
    chosen_k = None
    table = []
    for k in K_VALUES:
        rows = [r for r in silence if r["rule"] == f"quiet k={k:g}"]
        false = statistics.median(r["false_open_per_100_rooftop_weeks"] for r in rows)
        table.append((k, false, sum(r["detected"] for r in rows), sum(r["outages"] for r in rows)))
        if chosen_k is None and false <= MAX_FALSE_PER_100:
            chosen_k = k
    sweeps = [row for seed in seeds for row in reconcile_seed(seed, rooftops)[1]]
    f1 = {f: thin_f1([r for r in sweeps if r["thin_fraction"] == f]) for f in THIN_FRACTIONS}
    chosen_fraction = max(f1, key=f1.get)
    return {"k": chosen_k, "k_table": table, "thin_fraction": chosen_fraction, "thin_f1": f1}


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def spread(values, digits=2):
    """'median (min-max)' over seeds, each number written to at most `digits` decimals."""
    values = sorted(float(v) for v in values if v != "")
    if not values:
        return "n/a"
    fmt = lambda v: f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:.0f}"
    return f"{fmt(statistics.median(values))} ({fmt(values[0])}-{fmt(values[-1])})"


def summarize(out_dir, rooftops=ROOFTOPS):
    """Build summary.md from the csv files. Every number in the README comes from this file."""
    out = Path(out_dir)
    silence = read_csv(out / "silence.csv")
    recon = read_csv(out / "reconcile.csv")
    sweep = read_csv(out / "thin-sweep.csv")
    grace = read_csv(out / "grace.csv")
    seeds = sorted({int(r["seed"]) for r in silence})
    lines = [f"# Results, seeds {', '.join(map(str, seeds))}", "",
             f"Each cell is the median over {len(seeds)} seeds with the minimum and maximum in brackets. "
             f"Fleet of {rooftops} rooftops, synthetic data.", "",
             "## Quiet feeds", "",
             f"{HISTORY_DAYS} clean days to learn each profile, then {TEST_DAYS} days with outages of 4, 8, 24 or 48 hours "
             f"(0, 1 or 2 per rooftop); a rule is checked every {STEP_MIN} minutes. 'Detected' is the share of outages in which "
             "the rule fires before the outage ends; the delay runs from the first upload that did not arrive. "
             "A false alert is a firing that starts outside every outage, split by whether the rooftop was open or had "
             "skipped a day it normally opens. Outages with no upload to miss are left out.", "",
             "| rule | detected | median delay (h) | 90th percentile delay (h) | false alerts, open days, per 100 rooftop-weeks | false alerts, skipped days, per 100 rooftop-weeks |",
             "| --- | --- | --- | --- | --- | --- |"]
    for rule in dict.fromkeys(r["rule"] for r in silence):
        rows = [r for r in silence if r["rule"] == rule]
        share = [100 * int(r["detected"]) / int(r["outages"]) for r in rows]
        lines.append("| {} | {}% | {} | {} | {} | {} |".format(
            rule, spread(share, 0),
            spread([r["median_delay_h"] for r in rows], 2),
            spread([r["p90_delay_h"] for r in rows], 2),
            spread([r["false_open_per_100_rooftop_weeks"] for r in rows], 2),
            spread([r["false_closed_per_100_rooftop_weeks"] for r in rows], 2)))
    outages = sorted(int(r["outages"]) for r in silence if r["rule"] == silence[0]["rule"])
    empty = sorted(int(r["empty_outages"]) for r in silence if r["rule"] == silence[0]["rule"])
    lines += ["", f"Outages judged per seed: {outages[0]}-{outages[-1]}; outages with nothing to miss, left out: {empty[0]}-{empty[-1]}.", "",
              "## Reconcile", "",
              "Judged three days after the last repair. 'Missed' is an injected fault no finding names; 'extra' is a finding "
              "for something that was not injected.", "",
              "| code | injected | caught | missed | extra |", "| --- | --- | --- | --- | --- |"]
    for code in dict.fromkeys(r["code"] for r in recon):
        rows = [r for r in recon if r["code"] == code]
        if code == "HIDDEN_WRONG_AUDIO":
            lines.append("| {} | {} | not checked | not checked | {} reported by another check |".format(
                code, spread([r["injected"] for r in rows], 0), spread([r["incidental"] for r in rows], 0)))
            continue
        lines.append("| {} | {} | {} | {} | {} |".format(
            code, *(spread([r[col] for r in rows], 0) for col in ("injected", "caught", "missed", "extra"))))
    lines += ["", "### Thin-transcript threshold", "",
              "Words a minute below this share of the rooftop's median counts as thin. The default is the value picked on the tuning seeds.", "",
              "| share of median | injected | caught | extra |", "| --- | --- | --- | --- |"]
    for fraction in dict.fromkeys(r["thin_fraction"] for r in sweep):
        rows = [r for r in sweep if r["thin_fraction"] == fraction]
        lines.append("| {} | {} | {} | {} |".format(
            fraction, *(spread([r[col] for r in rows], 0) for col in ("injected", "caught", "extra"))))
    lines += ["", "### Grace window", "",
              "One Monday-morning snapshot (day 14, 16:00 UTC) of the same fleet, judged with and without a grace window. "
              "Everything reported here is in flight, not defective.", "",
              "| grace (minutes) | missing recordings reported | orphan recordings reported |", "| --- | --- | --- |"]
    for minutes in dict.fromkeys(r["grace_min"] for r in grace):
        rows = [r for r in grace if r["grace_min"] == minutes]
        lines.append("| {} | {} | {} |".format(
            minutes, spread([r["missing_extra"] for r in rows], 0), spread([r["orphan_extra"] for r in rows], 0)))
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text, newline="\n")
    return text


def run(out_dir, seeds=SEEDS, rooftops=ROOFTOPS):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    silence = [row for seed in seeds for row in silence_seed(seed, rooftops)]
    write_csv(out / "silence.csv", silence)
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

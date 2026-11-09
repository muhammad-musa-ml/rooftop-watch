"""Command line: demo, quiet, reconcile, evaluate, tune, scale."""
import argparse
import os
import sqlite3
import sys
from collections import Counter

from . import db, evaluate, gen, reconcile as rec, scale, silence

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def fmt(minute):
    day, rest = divmod(minute, gen.MIN_DAY)
    return f"day {day} {DAYS[day % 7]} {rest // 60:02d}:{rest % 60:02d} UTC"


def fail(message):
    """Exit 2, so a job that gates on exit 1 ("found something") can tell a failure from a finding."""
    print(message, file=sys.stderr)
    sys.exit(2)


def open_db(path):
    if not os.path.exists(path):
        fail(f"no such database: {path}")
    return sqlite3.connect(path)


def end_of_data(conn):
    return conn.execute("SELECT MAX(uploaded_min) FROM recordings").fetchone()[0] + 1


def cmd_demo(args):
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "fleet.db")
    if os.path.exists(path):
        fail(f"{path} exists; remove it first")
    conn = db.create(path)
    half = args.days // 2
    db.load_fleet(conn, args.seed, gen.make_rooftops(args.seed, args.rooftops), args.days,
                  outage_range=(half * gen.MIN_DAY, args.days * gen.MIN_DAY))
    n_rec = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    n_ro = conn.execute("SELECT COUNT(*) FROM repair_orders").fetchone()[0]
    print(f"wrote {path}: {args.rooftops} rooftops, {n_ro} repair orders, {n_rec} recordings, {args.days} days")
    print(f"the first {half} days are clean history; outages are injected in days {half} to {args.days}")
    print("outages with something to miss (rooftop, start, end, first missed upload):")
    rows = conn.execute("SELECT rooftop_id, start_min, end_min, first_missed_min FROM outages "
                        "WHERE first_missed_min IS NOT NULL ORDER BY first_missed_min LIMIT 5").fetchall()
    for rooftop, start, end, first in rows:
        print(f"  rooftop {rooftop}: {fmt(start)} to {fmt(end)}, first missed {fmt(first)} (minute {first})")
    return 0


def cmd_quiet(args):
    conn = open_db(args.db)
    now = args.now if args.now is not None else end_of_data(conn)
    found = silence.quiet_rooftops(conn, now, k=args.k)
    print(f"quiet as of {fmt(now)} (k={args.k:g}): {len(found)} rooftop(s)")
    for q in found[:args.limit]:
        print(f"  rooftop {q.rooftop_id}: last upload {fmt(q.last_upload_min)}, about {q.expected_missed:.1f} uploads expected since")
    return 1 if found else 0


def cmd_reconcile(args):
    conn = open_db(args.db)
    now = args.now if args.now is not None else end_of_data(conn) + args.grace
    findings = rec.reconcile(conn, now, since_min=args.since, grace_min=args.grace)
    counts = Counter(f.code for f in findings)
    print(f"reconcile as of {fmt(now)}, grace {args.grace} minutes: {len(findings)} finding(s)")
    for code in rec.CODES:
        print(f"  {code}: {counts.get(code, 0)}")
    for f in findings[:args.limit]:
        print(f"    {f.code} {f.key} (rooftop {f.rooftop_id})")
    return 1 if findings else 0


def cmd_evaluate(args):
    evaluate.run(args.out, seeds=tuple(args.seeds), rooftops=args.rooftops)
    print(f"wrote {args.out}/summary.md and the csv files it is built from")
    return 0


def cmd_tune(args):
    result = evaluate.tune()
    print("k: false alerts per 100 rooftop-weeks on scheduled days (pooled over the seeds), outages detected, outages")
    for k, false, detected, total in result["k_table"]:
        print(f"  k={k:g}: {false:.2f}, {detected}, {total}")
    print(f"smallest k at or under {evaluate.MAX_FALSE_PER_100:g}: {result['k']}")
    print("thin-transcript share of median: F1")
    for fraction, f1 in result["thin_f1"].items():
        print(f"  {fraction}: {f1:.4f}")
    print(f"best share: {result['thin_fraction']}")
    return 0


def cmd_scale(args):
    scale.run(args.out, args.rooftops, repeats=args.repeats)
    print(f"wrote {args.out}/scale.csv and {args.out}/scale.md")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rooftop-watch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="write a synthetic fleet with outages to <out>/fleet.db")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--rooftops", type=int, default=80)
    p.add_argument("--days", type=int, default=56)
    p.add_argument("--out", default="demo")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("quiet", help="rooftops quiet for longer than their own weekly pattern explains; exits 1 if any")
    p.add_argument("db")
    p.add_argument("--now", type=int, help="minute since a Monday 00:00 UTC; default is just after the last upload")
    p.add_argument("--k", type=float, default=silence.K)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_quiet)

    p = sub.add_parser("reconcile", help="repair orders and recordings that do not line up; exits 1 if any")
    p.add_argument("db")
    p.add_argument("--now", type=int, help="default is the last upload plus the grace window")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--grace", type=int, default=rec.GRACE_MIN)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_reconcile)

    p = sub.add_parser("evaluate", help="score both checks over several seeds and write results")
    p.add_argument("--out", default="results")
    p.add_argument("--seeds", type=int, nargs="+", default=list(evaluate.SEEDS))
    p.add_argument("--rooftops", type=int, default=evaluate.ROOFTOPS)
    p.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("tune", help="choose k and the thin-transcript share on the tuning seeds")
    p.set_defaults(fn=cmd_tune)

    p = sub.add_parser("scale", help="time both checks at several fleet sizes")
    p.add_argument("--out", default="results")
    p.add_argument("--rooftops", type=int, nargs="+", default=[80, 1000])
    p.add_argument("--repeats", type=int, default=5)
    p.set_defaults(fn=cmd_scale)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

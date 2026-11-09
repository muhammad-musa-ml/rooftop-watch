"""Time both checks as the fleet grows. Loading is not timed; only the checks are."""
import csv
import statistics
import tempfile
import time
from pathlib import Path

from . import db, gen
from .reconcile import reconcile
from .silence import quiet_rooftops

DAYS = 28
WINDOW_DAYS = 7  # reconcile looks at the last week


def run(out_dir, fleet_sizes=(80, 1000), repeats=5, seed=1):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for size in fleet_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.create(str(Path(tmp) / "fleet.db"))
            db.load_fleet(conn, seed, gen.make_rooftops(seed, size), DAYS)
            n_rec = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
            end = DAYS * gen.MIN_DAY
            for repeat in range(1, repeats + 1):
                t = time.perf_counter()
                quiet = quiet_rooftops(conn, end)
                quiet_s = time.perf_counter() - t
                t = time.perf_counter()
                findings = reconcile(conn, end + 3 * gen.MIN_DAY, since_min=end - WINDOW_DAYS * gen.MIN_DAY)
                reconcile_s = time.perf_counter() - t
                rows.append({"rooftops": size, "recordings": n_rec, "repeat": repeat,
                             "quiet_s": round(quiet_s, 3), "reconcile_s": round(reconcile_s, 3),
                             "quiet_found": len(quiet), "reconcile_found": len(findings)})
            conn.close()
    with open(out / "scale.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Scale", "",
             f"One check of each kind over {DAYS} days of data, {WINDOW_DAYS} days for the reconcile window; "
             f"median over {repeats} runs with the range in brackets. Python's sqlite3 on one laptop, "
             "synthetic data, loading not included.", "",
             "| rooftops | recordings | quiet check (s) | reconcile (s) |", "| --- | --- | --- | --- |"]
    for size in fleet_sizes:
        mine = [r for r in rows if r["rooftops"] == size]
        cell = lambda col: "{} ({}-{})".format(statistics.median(r[col] for r in mine),
                                              min(r[col] for r in mine), max(r[col] for r in mine))
        lines.append(f"| {size} | {mine[0]['recordings']} | {cell('quiet_s')} | {cell('reconcile_s')} |")
    (out / "scale.md").write_text("\n".join(lines) + "\n", newline="\n")
    return rows

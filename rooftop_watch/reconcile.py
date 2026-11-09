"""Reconcile closed repair orders against the recordings that arrived, and sanity-check each recording.

Everything is read "as of" a minute `now`: a repair order counts once its synced_min has passed and a
recording once its uploaded_min has passed. A repair order or recording is only judged after `grace_min`,
so a late upload or a slow DMS sync is not reported as missing.
"""
import statistics
from collections import defaultdict
from dataclasses import dataclass

GRACE_MIN = 360           # longer than the 300-minute late upload and the 240-minute sync lag
SHORT_FRACTION = 0.5      # bytes per second under this share of the rooftop's median is a short file
THIN_FRACTION = 0.10      # words per minute under this share of the rooftop's median is a thin transcript; picked on tuning seeds
MIN_THIN_DURATION_S = 60  # shorter recordings carry too few words to judge
MIN_BASELINE = 20         # recordings a rooftop needs before its own median is used

CODES = (
    "MISSING_RECORDING",
    "ORPHAN_RECORDING",
    "DUPLICATE_CONTENT",
    "EMPTY_FILE",
    "TRUNCATED_FILE",
    "THIN_TRANSCRIPT",
)


@dataclass(frozen=True)
class Finding:
    code: str
    key: str          # the repair order for MISSING_RECORDING, the recording for every other code
    rooftop_id: int


def reconcile(conn, now_min, since_min=0, grace_min=GRACE_MIN,
              short_fraction=SHORT_FRACTION, thin_fraction=THIN_FRACTION):
    """Findings as of `now_min` for repair orders and recordings from `since_min` on.

    The joins look at the whole tables, so an item near the start of the window is not
    reported for a partner row that sits just before it. Duplicate groups only use recordings
    inside the window.
    """
    findings = []
    findings += _missing(conn, now_min, since_min, grace_min)
    findings += _orphans(conn, now_min, since_min, grace_min)
    findings += _duplicates(conn, now_min, since_min)
    findings += _file_checks(conn, now_min, since_min, short_fraction, thin_fraction)
    return findings


def _missing(conn, now, since, grace):
    rows = conn.execute(
        """SELECT o.ro_id, o.rooftop_id FROM repair_orders o
           WHERE o.synced_min <= :now AND o.closed_min >= :since AND o.closed_min <= :now - :grace
             AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.ro_id = o.ro_id AND r.uploaded_min <= :now)""",
        {"now": now, "since": since, "grace": grace},
    )
    return [Finding("MISSING_RECORDING", ro, rooftop) for ro, rooftop in rows]


def _orphans(conn, now, since, grace):
    rows = conn.execute(
        """SELECT r.rec_id, r.rooftop_id FROM recordings r
           WHERE r.uploaded_min >= :since AND r.uploaded_min <= :now - :grace
             AND NOT EXISTS (SELECT 1 FROM repair_orders o WHERE o.ro_id = r.ro_id AND o.synced_min <= :now)""",
        {"now": now, "since": since, "grace": grace},
    )
    return [Finding("ORPHAN_RECORDING", rec, rooftop) for rec, rooftop in rows]


def _duplicates(conn, now, since):
    """One content hash on recordings of two or more repair orders: keep the first upload, flag the rest."""
    rows = conn.execute(
        """SELECT r.sha256, r.rec_id, r.rooftop_id FROM recordings r
           WHERE r.uploaded_min >= :since AND r.uploaded_min <= :now
             AND r.sha256 IN (SELECT sha256 FROM recordings
                              WHERE uploaded_min >= :since AND uploaded_min <= :now
                              GROUP BY sha256 HAVING COUNT(DISTINCT ro_id) > 1)
           ORDER BY r.sha256, r.uploaded_min, r.rec_id""",
        {"now": now, "since": since},
    )
    findings, current = [], None
    for sha, rec, rooftop in rows:
        if sha != current:
            current = sha
            continue
        findings.append(Finding("DUPLICATE_CONTENT", rec, rooftop))
    return findings


def _file_checks(conn, now, since, short_fraction, thin_fraction):
    """An empty file, a file far smaller than its length implies, or a transcript far shorter than usual."""
    rows = conn.execute(
        """SELECT rec_id, rooftop_id, bytes, duration_s, transcript_words FROM recordings
           WHERE uploaded_min >= ? AND uploaded_min <= ?""",
        (since, now),
    ).fetchall()
    bps_by_rooftop, wpm_by_rooftop = defaultdict(list), defaultdict(list)
    for _, rooftop, nbytes, duration, words in rows:
        bps_by_rooftop[rooftop].append(nbytes / duration)
        wpm_by_rooftop[rooftop].append(words / (duration / 60))
    all_bps = [v for vals in bps_by_rooftop.values() for v in vals]
    all_wpm = [v for vals in wpm_by_rooftop.values() for v in vals]
    fleet_bps = statistics.median(all_bps) if all_bps else 0.0
    fleet_wpm = statistics.median(all_wpm) if all_wpm else 0.0
    bps_median = {r: statistics.median(v) if len(v) >= MIN_BASELINE else fleet_bps for r, v in bps_by_rooftop.items()}
    wpm_median = {r: statistics.median(v) if len(v) >= MIN_BASELINE else fleet_wpm for r, v in wpm_by_rooftop.items()}
    findings = []
    for rec, rooftop, nbytes, duration, words in rows:
        if nbytes == 0:
            findings.append(Finding("EMPTY_FILE", rec, rooftop))
        elif nbytes / duration < short_fraction * bps_median[rooftop]:
            findings.append(Finding("TRUNCATED_FILE", rec, rooftop))
        elif duration >= MIN_THIN_DURATION_S and words / (duration / 60) < thin_fraction * wpm_median[rooftop]:
            findings.append(Finding("THIN_TRANSCRIPT", rec, rooftop))
    return findings

"""SQLite schema and loader for a generated fleet."""
import sqlite3

from . import gen

SCHEMA = """
CREATE TABLE rooftops (
    rooftop_id INTEGER PRIMARY KEY,
    utc_offset_h INTEGER NOT NULL,
    daily_mean REAL NOT NULL
);
CREATE TABLE repair_orders (
    ro_id TEXT PRIMARY KEY,
    rooftop_id INTEGER NOT NULL,
    closed_min INTEGER NOT NULL,
    synced_min INTEGER NOT NULL
);
CREATE TABLE recordings (
    rec_id TEXT PRIMARY KEY,
    ro_id TEXT NOT NULL,
    rooftop_id INTEGER NOT NULL,
    uploaded_min INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    duration_s INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    transcript_words INTEGER NOT NULL
);
CREATE TABLE faults (
    code TEXT NOT NULL,
    ro_id TEXT,
    rec_id TEXT,
    rooftop_id INTEGER NOT NULL
);
CREATE TABLE outages (
    rooftop_id INTEGER NOT NULL,
    start_min INTEGER NOT NULL,
    end_min INTEGER NOT NULL,
    first_missed_min INTEGER
);
"""

INDEXES = """
CREATE INDEX recordings_rooftop_time ON recordings (rooftop_id, uploaded_min);
CREATE INDEX recordings_ro ON recordings (ro_id);
CREATE INDEX recordings_sha ON recordings (sha256);
CREATE INDEX repair_orders_rooftop_time ON repair_orders (rooftop_id, closed_min);
"""


def create(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def load_fleet(conn, seed, rooftops, days, outage_range=None):
    """Write every rooftop's repairs, recordings and injected faults, then build the indexes.

    With `outage_range=(start_min, end_min)` each rooftop may also lose every upload for a few hours inside
    that range. The repair orders still arrive, so the lost recordings show up as missing in `reconcile`;
    they are not in the fault manifest, which only lists the injected data faults.
    """
    for rooftop in rooftops:
        events = gen.rooftop_events(seed, rooftop, days)
        ros, recs, faults = gen.rooftop_records(seed, rooftop, events)
        if outage_range:
            outages = gen.make_outages(seed, rooftop.rooftop_id, *outage_range)
            lost = {rec[0] for rec in recs if any(s <= rec[3] < e for s, e in outages)}
            _, details = gen.apply_outages(sorted(rec[3] for rec in recs), outages)
            conn.executemany("INSERT INTO outages VALUES (?, ?, ?, ?)",
                             [(rooftop.rooftop_id, s, e, first) for s, e, first in details])
            recs = [rec for rec in recs if rec[0] not in lost]
            faults = [f for f in faults if f[2] not in lost]
        conn.execute("INSERT INTO rooftops VALUES (?, ?, ?)", (rooftop.rooftop_id, rooftop.utc_offset_h, rooftop.daily_mean))
        conn.executemany("INSERT INTO repair_orders VALUES (?, ?, ?, ?)", ros)
        conn.executemany("INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", recs)
        conn.executemany("INSERT INTO faults VALUES (?, ?, ?, ?)", faults)
    conn.executescript(INDEXES)
    conn.commit()

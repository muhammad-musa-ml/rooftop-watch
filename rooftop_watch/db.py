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


def load_fleet(conn, seed, rooftops, days):
    """Write every rooftop's repairs, recordings and injected faults, then build the indexes."""
    for rooftop in rooftops:
        events = gen.rooftop_events(seed, rooftop, days)
        ros, recs, faults = gen.rooftop_records(seed, rooftop, events)
        conn.execute("INSERT INTO rooftops VALUES (?, ?, ?)", (rooftop.rooftop_id, rooftop.utc_offset_h, rooftop.daily_mean))
        conn.executemany("INSERT INTO repair_orders VALUES (?, ?, ?, ?)", ros)
        conn.executemany("INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", recs)
        conn.executemany("INSERT INTO faults VALUES (?, ?, ?, ?)", faults)
    conn.executescript(INDEXES)
    conn.commit()

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
    uploaded_min INTEGER NOT NULL
);
"""

INDEXES = """
CREATE INDEX recordings_rooftop_time ON recordings (rooftop_id, uploaded_min);
CREATE INDEX recordings_ro ON recordings (ro_id);
CREATE INDEX repair_orders_rooftop_time ON repair_orders (rooftop_id, closed_min);
"""


def create(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def load_fleet(conn, seed, rooftops, days):
    """Write every rooftop's repairs and recordings, then build the indexes."""
    for rooftop in rooftops:
        events = gen.rooftop_events(seed, rooftop, days)
        rid = rooftop.rooftop_id
        conn.execute("INSERT INTO rooftops VALUES (?, ?, ?)", (rid, rooftop.utc_offset_h, rooftop.daily_mean))
        conn.executemany(
            "INSERT INTO repair_orders VALUES (?, ?, ?, ?)",
            [(gen.ro_id(rid, s), rid, closed, synced) for s, closed, _, synced in events],
        )
        conn.executemany(
            "INSERT INTO recordings VALUES (?, ?, ?, ?)",
            [(gen.rec_id(rid, s), gen.ro_id(rid, s), rid, up) for s, _, up, _ in events],
        )
    conn.executescript(INDEXES)
    conn.commit()

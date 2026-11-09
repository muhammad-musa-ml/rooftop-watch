"""Score reconcile findings against the fault manifest the generator wrote."""
from .reconcile import CODES


def manifest(conn):
    """{code: set of keys}, the key being the repair order for MISSING_RECORDING and the recording otherwise."""
    out = {}
    for code, ro, rec in conn.execute("SELECT code, ro_id, rec_id FROM faults"):
        out.setdefault(code, set()).add(ro if code == "MISSING_RECORDING" else rec)
    return out


def score_reconcile(conn, findings):
    """Per code: injected, caught, missed, extra (reported but never injected).

    The hidden fault gets injected and incidental: how many of those recordings a check reported
    for some other reason, which is the rate at which clean recordings are reported too.
    """
    truth = manifest(conn)
    found = {}
    for f in findings:
        found.setdefault(f.code, set()).add(f.key)
    rows = {}
    for code in CODES:
        injected, reported = truth.get(code, set()), found.get(code, set())
        rows[code] = {
            "injected": len(injected),
            "caught": len(injected & reported),
            "missed": len(injected - reported),
            "extra": len(reported - injected),
        }
    hidden = truth.get("HIDDEN_WRONG_AUDIO", set())
    reported_any = {f.key for f in findings}
    rows["HIDDEN_WRONG_AUDIO"] = {"injected": len(hidden), "incidental": len(hidden & reported_any)}
    return rows

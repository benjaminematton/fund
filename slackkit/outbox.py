"""events outbox: SQLite truth -> Slack projection (contracts §2, §5.3).
DB write and Slack post are decoupled; a crash between post and mark may
duplicate a Slack message — acceptable; never retry into a second DB write."""

from __future__ import annotations

import json
import sqlite3

from .render import render


def append_event(conn: sqlite3.Connection, kind: str, payload: dict,
                 now_iso: str) -> int:
    cur = conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), now_iso))
    conn.commit()
    return cur.lastrowid


def drain(conn: sqlite3.Connection, slack, now_iso: str) -> int:
    """Post every unposted event. Returns the count of events genuinely
    posted to Slack (via slack.post()) — not the count of rows marked
    drained. A row whose render raises is dead-lettered (marked posted,
    never retried, not counted) and a projection_error event describing
    it is appended and drained in the same call, so a bad event dead-letters
    itself instead of jamming the queue (MVF review C2)."""
    posted = 0
    while True:
        rows = conn.execute(
            "SELECT id, kind, payload FROM events WHERE posted_at IS NULL"
            " ORDER BY id").fetchall()
        if not rows:
            break
        for row in rows:
            try:
                channel, text = render(row["kind"], json.loads(row["payload"]))
                slack.post(channel, text)
            except Exception:
                conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                             (now_iso, row["id"]))
                conn.commit()
                if row["kind"] != "projection_error":
                    append_event(conn, "projection_error",
                                 {"event_id": row["id"], "kind": row["kind"]},
                                 now_iso)
                continue
            conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                         (now_iso, row["id"]))
            conn.commit()
            posted += 1
    return posted

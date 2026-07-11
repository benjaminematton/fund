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
    posted = 0
    rows = conn.execute(
        "SELECT id, kind, payload FROM events WHERE posted_at IS NULL"
        " ORDER BY id").fetchall()
    for row in rows:
        channel, text = render(row["kind"], json.loads(row["payload"]))
        slack.post(channel, text)
        conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                     (now_iso, row["id"]))
        conn.commit()
        posted += 1
    return posted

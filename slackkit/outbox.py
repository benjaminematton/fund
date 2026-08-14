"""events outbox: SQLite truth -> Slack projection (contracts §2, §5.3).
DB write and Slack post are decoupled; a crash between post and mark, or a
post that raises after Slack accepted it, may duplicate a Slack message —
acceptable (a duplicate projection is recoverable, a lost one is not); never
retry into a second DB write."""

from __future__ import annotations

import json
import logging
import sqlite3

from .render import render

log = logging.getLogger(__name__)


def append_event(conn: sqlite3.Connection, kind: str, payload: dict,
                 now_iso: str) -> int:
    cur = conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), now_iso))
    conn.commit()
    return cur.lastrowid


def drain(conn: sqlite3.Connection, slack, now_iso: str) -> int:
    """Post unposted events, oldest first. Returns the count of events
    genuinely posted to Slack (via slack.post()) during THIS call — not the
    count of rows marked drained, and not a promise that the queue is now
    empty. A short count means work is left for the next drain; the audit's
    global "undrained outbox events" check is what surfaces a queue that
    never clears.

    Two failure modes, two outcomes:

    * render() raises -> PERMANENT (an unknown kind, a malformed payload; it
      will raise identically forever). The row is dead-lettered — marked
      posted, never retried, not counted — and a projection_error event
      describing it is appended and drained in the same call, so a bad event
      dead-letters itself instead of jamming the queue (MVF review C2).

    * slack.post() raises -> TRANSIENT (an outage, a rate limit, a token not
      fixed yet). The row is left UNPOSTED and the drain STOPS, so a later
      event can never be posted ahead of an earlier one. Everything retries
      on the next drain — which daily.py runs after every stage — instead of
      the day's whole Slack projection being discarded and recoverable only
      by reading the DB.

    Terminates: each pass either returns early or marks every row it fetched,
    and the only rows a pass can add are the projection_errors it appended,
    which never append further projection_errors."""
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
            except Exception as exc:
                conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                             (now_iso, row["id"]))
                conn.commit()
                log.error("outbox: dead-lettered event %s (kind %s) — %s: %s",
                          row["id"], row["kind"], type(exc).__name__, exc)
                if row["kind"] != "projection_error":
                    append_event(conn, "projection_error",
                                 {"event_id": row["id"], "kind": row["kind"]},
                                 now_iso)
                continue
            try:
                slack.post(channel, text)
            except Exception as exc:
                log.error("outbox: slack.post failed on event %s (kind %s) —"
                          " %s: %s; drain stopped, this and every later event"
                          " stay queued and retry on the next drain",
                          row["id"], row["kind"], type(exc).__name__, exc)
                return posted
            conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                         (now_iso, row["id"]))
            conn.commit()
            posted += 1
    return posted

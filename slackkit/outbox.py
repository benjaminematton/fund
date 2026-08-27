"""Events outbox: SQLite truth -> Slack projection (contracts §2, §5.3).
DB write and Slack post are decoupled. A crash between post and mark, or a
post that raises after Slack accepted it, might duplicate a Slack message.
A duplicate projection is recoverable and a lost one is not, so duplicates
are acceptable. Never retry into a second DB write."""

from __future__ import annotations

import json
import logging
import sqlite3

from .port import PermanentPostError
from .redact import redact
from .render import render

log = logging.getLogger(__name__)


def _insert(conn: sqlite3.Connection, kind: str, payload: dict,
            now_iso: str) -> int:
    cur = conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), now_iso))
    conn.commit()
    return cur.lastrowid


def append_event(conn: sqlite3.Connection, kind: str, payload: dict,
                 now_iso: str) -> int:
    return _insert(conn, kind, payload, now_iso)


def append_alert(conn: sqlite3.Connection, code: str, text: str, *,
                 now_iso: str, ticker: str | None = None,
                 clears: bool = False, **payload) -> int:
    """Append an alert carrying a stable machine identity.

    `code` is what scripts/file_alert_issues.py keys a GitHub issue on, so it
    must be identical across runs: never interpolate a ticker, order id,
    quantity or exception type into it. `ticker` is the only permitted
    per-entity key, and only where fixing one position would not fix another.

    Deliberately validates nothing. This runs on the alert path, often inside
    an `except`, and a raise here would turn "something needs review" into a
    dead trading day (invariant 4). scripts/check_alert_codes.py enforces the
    code's shape statically instead.

    `text` — and only `text` — is redacted here rather than at the call sites:
    the three scripts/run_day.py sites interpolate a raw exception, and the
    stored row feeds BOTH egresses, Slack via drain() and GitHub via
    scripts/file_alert_issues.py. `**payload` is stored as given;
    orchestrator/preconditions.py:78 deliberately keeps a full uncapped
    exception there, and no egress reads payload extras today. redact()
    neither raises nor runs long, so this cannot cost an alert.
    """
    body: dict = {"text": redact(text), "code": code, **payload}
    if ticker is not None:
        body["ticker"] = ticker
    if clears:
        body["clears"] = True
    return _insert(conn, "alert", body, now_iso)


def _dead_letter(conn: sqlite3.Connection, row, now_iso: str,
                 exc: Exception) -> None:
    """Mark one undeliverable row posted, never retried, not counted, and
    append a projection_error naming it so scripts/audit_day.py fails the
    day (its dead-letter check counts projection_error rows). A
    projection_error that itself dead-letters appends nothing — that is what
    stops the loop when the dead channel is #risk."""
    conn.execute("UPDATE events SET posted_at = ? WHERE id = ?",
                 (now_iso, row["id"]))
    conn.commit()
    log.error("outbox: dead-lettered event %s (kind %s) — %s: %s",
              row["id"], row["kind"], type(exc).__name__, exc)
    if row["kind"] != "projection_error":
        append_event(conn, "projection_error",
                     {"event_id": row["id"], "kind": row["kind"]}, now_iso)


def drain(conn: sqlite3.Connection, slack, now_iso: str) -> int:
    """Post unposted events, oldest first. Returns the count of events
    genuinely posted to Slack (through slack.post()) during THIS call — not the
    count of rows marked drained, and not a promise that the queue is now
    empty. A short count means work is left for the next drain; the audit's
    global "undrained outbox events" check is what surfaces a queue that
    never clears.

    The two failure modes have two outcomes:

    * render() raises -> PERMANENT (an unknown kind, a malformed payload; it
      raises identically forever). The row is dead-lettered — marked
      posted, never retried, not counted — and a projection_error event
      describing it is appended and drained in the same call, so a bad event
      dead-letters itself instead of jamming the queue (MVF review C2).

    * slack.post() raises PermanentPostError -> PERMANENT (the bot is not in
      that channel, the channel is archived or gone, the token is invalid; see
      slackkit.real.PERMANENT_ERRORS). Retrying cannot help, so the row is
      dead-lettered exactly like a render failure and the drain CONTINUES to
      the next event. Ordering only has to hold WITHIN a channel, and a dead
      channel delivers nothing to order — stopping the global queue on it
      would silence every healthy channel too (day one: invited to 4 of 5).

    * slack.post() raises anything else -> TRANSIENT (an outage, a rate
      limit, a token not fixed yet). The row is left UNPOSTED and the drain
      STOPS, so a later event can never be posted ahead of an earlier one.
      Everything retries on the next drain — which daily.py runs after every
      stage — instead of the day's whole Slack projection being discarded and
      recoverable only by reading the DB.

    Terminates: each pass either returns early or marks every row it fetched,
    and the only rows a pass can add are the projection_error rows it appended,
    which never append further projection_error rows."""
    posted = 0
    while True:
        rows = conn.execute(
            "SELECT id, kind, payload FROM events WHERE posted_at IS NULL"
            " ORDER BY id").fetchall()
        if not rows:
            break
        for row in rows:
            try:
                post = render(row["kind"], json.loads(row["payload"]))
            except Exception as exc:
                _dead_letter(conn, row, now_iso, exc)
                continue
            try:
                slack.post(post.channel, post.text, blocks=post.blocks,
                           username=post.username,
                           icon_emoji=post.icon_emoji)
            except PermanentPostError as exc:
                _dead_letter(conn, row, now_iso, exc)
                continue
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

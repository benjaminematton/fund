import pytest

from slackkit.fake import FakeSlack
from slackkit.outbox import append_event, drain
from slackkit.render import render

NOW = "2026-07-06T15:30:00+00:00"

FILL = {"ticker": "NVDA", "side": "buy", "filled_qty": 67,
        "filled_avg_price": 180.14,
        "ticket_id": "a3f90000-0000-4000-8000-000000000001"}


def test_render_fill_matches_contracts_s8():
    channel, text = render("fill", FILL)
    assert channel == "#trade-log"
    assert text == "🧾 NVDA buy 67@180.14 (ticket a3f90000)"


def test_render_unknown_kind_raises():
    with pytest.raises(ValueError):
        render("mystery", {})


def test_fake_slack_records_posts_per_channel():
    s = FakeSlack()
    ts1 = s.post("#trade-log", "hello")
    ts2 = s.post("#trade-log", "again", thread_ts=ts1)
    assert [p["text"] for p in s.posts["#trade-log"]] == ["hello", "again"]
    assert s.posts["#trade-log"][1]["thread_ts"] == ts1
    assert ts1 != ts2


def test_outbox_drain_posts_once_and_marks(fund_db):
    slack = FakeSlack()
    append_event(fund_db, "fill", FILL, NOW)
    assert drain(fund_db, slack, NOW) == 1
    assert len(slack.posts["#trade-log"]) == 1
    # second drain: nothing unposted — Slack is a projection, never re-written
    assert drain(fund_db, slack, NOW) == 0
    assert len(slack.posts["#trade-log"]) == 1
    row = fund_db.execute("SELECT posted_at FROM events").fetchone()
    assert row["posted_at"] == NOW


from pathlib import Path
from slackkit.render import RENDERERS


def test_new_event_kinds_render():
    assert render("signal", {"agent": "analyst", "ticker": "NVDA",
        "direction": "bullish", "confidence": 72, "summary": "s"})[0] == "#research"
    assert render("decision", {"ticker": "NVDA", "action": "buy", "qty": 80,
        "thesis": "t"})[0] == "#trading-floor"
    assert render("gate_approved", {"ticket_id": "a3f90000-x", "side": "buy",
        "ticker": "NVDA", "max_qty": 67, "expires_hhmm": "16:00"}) == (
        "#risk", "✅ TICKET a3f90000 buy NVDA ≤67 expires 16:00")
    assert render("gate_rejected", {"ticker": "NVDA", "side": "buy",
        "reason": "gate_error"}) == ("#risk", "⛔ NVDA buy — gate_error")
    assert render("alert", {"text": "x"})[0] == "#risk"
    assert render("digest", {"text": "x"})[0] == "#pnl"
    assert render("projection_error", {"event_id": 3, "kind": "bogus"})[0] == "#risk"

def test_drain_dead_letters_bad_event_and_continues(fund_db, sim_clock):
    from orchestrator.clock import iso
    from slackkit.fake import FakeSlack
    now = iso(sim_clock.now())
    append_event(fund_db, "bogus_kind", {"x": 1}, now)
    append_event(fund_db, "alert", {"text": "after"}, now)
    slack = FakeSlack()
    posted = drain(fund_db, slack, now)
    # queue is not jammed: the good event posted, the bad one dead-lettered
    assert [p["text"] for p in slack.posts["#risk"] if "after" in p["text"]]
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"] == 0
    # and a projection_error event was appended AND posted
    assert any("projection_error" in p["text"] or "bogus_kind" in p["text"]
               for p in slack.posts["#risk"])
    # returned count reflects only events genuinely posted to Slack: the
    # "after" alert plus the projection_error itself (both posted) — the
    # bogus_kind row is dead-lettered and must NOT be counted
    actual_posts = sum(len(v) for v in slack.posts.values())
    assert actual_posts == 2
    assert posted == actual_posts

def test_every_written_kind_has_a_renderer():
    """Static guard: every append_event kind literal in the codebase renders.

    AST-based (not regex): finds every append_event call, requires its kind
    argument to be a string literal (2nd positional or kind= kwarg), and
    fails loudly on anything else — a variable, f-string, etc. would
    silently escape a regex-based scan and defeat the guard.
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    kinds = set()
    for py in root.rglob("*.py"):
        if ".venv" in py.parts or "tests" in py.parts:
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name != "append_event":
                continue
            kind_arg = None
            if len(node.args) >= 2:
                kind_arg = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "kind":
                        kind_arg = kw.value
            loc = f"{py.relative_to(root)}:{node.lineno}"
            assert kind_arg is not None, (
                f"{loc}: append_event call has no kind (2nd positional or"
                f" kind=) argument")
            assert isinstance(kind_arg, ast.Constant) and isinstance(kind_arg.value, str), (
                f"{loc}: append_event kinds must be string literals")
            kinds.add(kind_arg.value)
    missing = kinds - set(RENDERERS)
    assert not missing, f"event kinds without renderer: {missing}"

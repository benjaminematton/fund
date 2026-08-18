from pathlib import Path

import pytest

from slackkit.fake import FakeSlack
from slackkit.outbox import append_event, drain
from slackkit.render import render

NOW = "2026-07-06T15:30:00+00:00"

FILL = {"ticker": "NVDA", "side": "buy", "filled_qty": 67,
        "filled_avg_price": 180.14,
        "ticket_id": "a3f90000-0000-4000-8000-000000000001"}

SIGNAL = {"agent": "analyst", "ticker": "NVDA", "direction": "bullish",
          "confidence": 72, "summary": "upgraded to Buy on datacenter demand"}

DECISION = {"ticker": "NVDA", "action": "buy", "qty": 80,
            "thesis": "datacenter demand is not priced in"}

GATE_OK = {"ticket_id": "a3f90000-0000-4000-8000-000000000001", "side": "buy",
           "ticker": "NVDA", "max_qty": 67, "expires_hhmm": "16:00"}

GATE_NO = {"ticker": "NVDA", "side": "buy", "reason": "no_headroom"}


def test_render_fill_matches_contracts_s8():
    channel, text = render("fill", FILL)
    assert channel == "#trade-log"
    assert text == ("*Execution Trader* · 🧾 bought *67 NVDA* at *$180.14*"
                    " — $12,069.38\nTicket `a3f90000`")


def test_a_sell_fill_says_sold_not_sell():
    text = render("fill", {**FILL, "side": "sell"})[1]
    assert "sold *67 NVDA*" in text


def test_signal_names_the_seat_and_quotes_the_summary():
    channel, text = render("signal", SIGNAL)
    assert channel == "#research"
    assert text == ("*Research Analyst* · *NVDA* · bullish, conviction 72/100\n"
                    "> upgraded to Buy on datacenter demand")


def test_an_unmapped_seat_falls_back_to_its_raw_name():
    text = render("signal", {**SIGNAL, "agent": "macro"})[1]
    assert text.startswith("*macro* · ")


def test_decision_reads_as_an_instruction_not_a_verdict_line():
    channel, text = render("decision", DECISION)
    assert channel == "#trading-floor"
    assert text == ("*Portfolio Manager* · *NVDA* — buy 80 shares\n"
                    "> datacenter demand is not priced in")


def test_a_hold_decision_does_not_claim_a_share_count():
    text = render("decision", {**DECISION, "action": "hold", "qty": 0})[1]
    assert "*NVDA* — hold\n" in text
    assert "0 shares" not in text


def test_gate_approval_spells_out_the_cap_and_labels_the_ticket():
    channel, text = render("gate_approved", GATE_OK)
    assert channel == "#risk"
    assert text == ("*Risk Gate* · ✅ *buy NVDA* approved for up to *67 shares*\n"
                    "Ticket `a3f90000` · expires 16:00 ET")


def test_gate_rejection_explains_the_reason_code_in_english():
    channel, text = render("gate_rejected", GATE_NO)
    assert channel == "#risk"
    assert text == ("*Risk Gate* · ⛔ *buy NVDA* blocked\n"
                    "> Sector exposure is already at its cap — no room for"
                    " another share. (`no_headroom`)")


def test_an_unglossed_reason_code_still_renders_rather_than_raising():
    """A reason code minted after this map was written must degrade to the
    bare code, not take the whole projection down (invariant 4)."""
    text = render("gate_rejected", {**GATE_NO, "reason": "brand_new"})[1]
    assert text.endswith("> (`brand_new`)")


def test_every_gate_reason_code_has_an_english_gloss():
    """Static guard: every Rejected("<code>") literal in the deterministic
    gate reaches Slack as English, not as a bare identifier."""
    import ast

    from slackkit.render import REASONS

    root = Path(__file__).resolve().parents[1]
    codes = {"zero_qty"}   # minted in daily.py, not via Rejected()
    for py in (root / "gate").rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(), filename=str(py))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Rejected" and node.args):
                arg = node.args[0]
                assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                    f"{py.relative_to(root)}:{node.lineno}: Rejected() reasons"
                    f" must be string literals so this guard can see them")
                codes.add(arg.value)
    assert codes <= set(REASONS), f"gate reasons with no gloss: {codes - set(REASONS)}"


def test_alert_is_labelled_so_it_is_not_mistaken_for_a_gate_post():
    assert render("alert", {"text": "boom"}) == ("#risk", "⚠️ *Alert* · boom")


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


from slackkit.render import RENDERERS


def test_new_event_kinds_render():
    """Channel routing per contracts §8. The message bodies have their own
    tests above; this one guards only where each kind lands."""
    assert render("signal", SIGNAL)[0] == "#research"
    assert render("decision", DECISION)[0] == "#trading-floor"
    assert render("gate_approved", GATE_OK)[0] == "#risk"
    assert render("gate_rejected", GATE_NO)[0] == "#risk"
    assert render("alert", {"text": "x"})[0] == "#risk"
    assert render("digest", {"text": "x"})[0] == "#pnl"
    # distinct kind from 'digest', same channel: run_close's
    # already-posted guard matches on kind='digest', so reusing it for the
    # post-close P&L would make a re-fired close think its own digest had
    # already gone out.
    assert render("pnl", {"text": "x"})[0] == "#pnl"
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

class _FlakySlack:
    """Slack that fails its first `failures` post() calls, then works — a
    token that has not been fixed yet, a 503, a network blip."""

    def __init__(self, failures: int = 1):
        self.remaining = failures
        self.posts: list[tuple[str, str]] = []

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        if self.remaining:
            self.remaining -= 1
            raise RuntimeError("slack outage")
        self.posts.append((channel, text))
        return f"ts-{len(self.posts)}"


def _queue(conn) -> tuple[int, int]:
    unposted = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]
    dead = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'projection_error'"
        ).fetchone()["c"]
    return unposted, dead


def test_a_transient_post_failure_retries_instead_of_dead_lettering(fund_db):
    """drain() used to catch render() AND slack.post() in one handler, so a
    Slack outage marked every event posted and discarded the day's whole
    projection forever. A post failure is transient: the events stay
    unposted, nothing is dead-lettered, and the next drain delivers them."""
    slack = _FlakySlack(failures=1)
    append_event(fund_db, "fill", FILL, NOW)
    append_event(fund_db, "alert", {"text": "after"}, NOW)

    assert drain(fund_db, slack, NOW) == 0        # stopped on the first post
    assert slack.posts == []                      # ordering: nothing jumped it
    assert _queue(fund_db) == (2, 0)              # both still queued, none dead

    assert drain(fund_db, slack, NOW) == 2
    assert [text for _, text in slack.posts] == [
        render("fill", FILL)[1], render("alert", {"text": "after"})[1]]
    assert _queue(fund_db) == (0, 0)


def test_a_post_failure_stops_the_drain_so_ordering_is_preserved(fund_db):
    """A later event must never be posted before an earlier one."""
    slack = _FlakySlack(failures=1)
    for i in range(4):
        append_event(fund_db, "alert", {"text": f"e{i}"}, NOW)
    assert drain(fund_db, slack, NOW) == 0
    assert slack.posts == []
    assert drain(fund_db, slack, NOW) == 4
    assert [text for _, text in slack.posts] == [
        render("alert", {"text": f"e{i}"})[1] for i in range(4)]


def test_a_render_error_still_dead_letters_while_slack_is_healthy(fund_db):
    """Only RENDER errors are permanent. The dead-letter path is unchanged:
    the row is marked posted, a projection_error names it, and the queue is
    not jammed."""
    slack = FakeSlack()
    append_event(fund_db, "bogus_kind", {"x": 1}, NOW)
    append_event(fund_db, "alert", {"text": "after"}, NOW)
    assert drain(fund_db, slack, NOW) == 2        # the alert + the projection_error
    assert _queue(fund_db) == (0, 1)


def test_a_dead_letter_survives_a_transient_post_failure(fund_db):
    """The two paths compose: the unrenderable row dead-letters once, and the
    projection_error it appends is delivered by the retrying drain — never
    dead-lettered a second time by the outage."""
    slack = _FlakySlack(failures=1)
    append_event(fund_db, "bogus_kind", {"x": 1}, NOW)
    append_event(fund_db, "alert", {"text": "after"}, NOW)

    assert drain(fund_db, slack, NOW) == 0
    assert _queue(fund_db) == (2, 1)     # bogus row dead-lettered, 2 left to post
    assert drain(fund_db, slack, NOW) == 2
    assert _queue(fund_db) == (0, 1)


def test_a_permanent_post_error_dead_letters_only_that_events_channel(fund_db):
    """Day-one shape: the bot was invited to 4 of 5 channels, so #pnl posts
    raise not_in_channel — PERMANENT, it will raise identically forever. That
    one event dead-letters and the drain CONTINUES: ordering only has to hold
    within a channel, so a dead channel must not stop #trade-log fills or
    #risk alerts."""
    slack = FakeSlack(permanent_failures={"#pnl"})
    append_event(fund_db, "digest", {"text": "day 1 pnl"}, NOW)
    append_event(fund_db, "fill", FILL, NOW)
    append_event(fund_db, "alert", {"text": "risk breach"}, NOW)

    posted = drain(fund_db, slack, NOW)

    assert "#pnl" not in slack.posts                     # never delivered...
    assert _queue(fund_db) == (0, 1)                     # ...and dead-lettered
    # the healthy channels are untouched
    assert [p["text"] for p in slack.posts["#trade-log"]] == [render("fill", FILL)[1]]
    assert any("risk breach" in p["text"] for p in slack.posts["#risk"])
    # the dead letter is visible: a projection_error naming it was posted
    assert any("projection error" in p["text"] for p in slack.posts["#risk"])
    assert posted == sum(len(v) for v in slack.posts.values())


def test_a_permanently_dead_channel_does_not_loop_on_its_own_projection_error(fund_db):
    """If the dead channel IS #risk, the projection_error routed there also
    fails permanently. It must dead-letter without appending another one."""
    slack = FakeSlack(permanent_failures={"#risk"})
    append_event(fund_db, "alert", {"text": "risk breach"}, NOW)
    append_event(fund_db, "fill", FILL, NOW)

    posted = drain(fund_db, slack, NOW)

    assert posted == 1                                   # only the fill landed
    assert [p["text"] for p in slack.posts["#trade-log"]] == [render("fill", FILL)[1]]
    unposted, dead = _queue(fund_db)
    assert unposted == 0                                 # queue is not jammed
    assert dead == 1                                     # exactly one, no loop


def test_real_slack_raises_permanent_post_error_on_permanent_codes():
    from slack_sdk.errors import SlackApiError

    from slackkit.port import PermanentPostError
    from slackkit.real import RealSlack

    for code in ("not_in_channel", "channel_not_found", "invalid_auth",
                 "is_archived"):
        slack = RealSlack("xoxb-not-a-real-token")

        def _raise(**kwargs):
            raise SlackApiError("boom", {"ok": False, "error": code})

        slack._client.chat_postMessage = _raise
        with pytest.raises(PermanentPostError) as exc:
            slack.post("#pnl", "hi")
        assert code in str(exc.value)


def test_real_slack_leaves_every_other_slack_error_transient():
    from slack_sdk.errors import SlackApiError

    from slackkit.port import PermanentPostError
    from slackkit.real import RealSlack

    slack = RealSlack("xoxb-not-a-real-token")

    def _raise(**kwargs):
        raise SlackApiError("boom", {"ok": False, "error": "ratelimited"})

    slack._client.chat_postMessage = _raise
    with pytest.raises(SlackApiError) as exc:
        slack.post("#pnl", "hi")
    assert not isinstance(exc.value, PermanentPostError)


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

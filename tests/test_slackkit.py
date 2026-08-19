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
    channel, text = render("fill", FILL)[:2]
    assert channel == "#trade-log"
    assert text == ("*Dash (Execution)* · 🧾 bought *67 NVDA* at *$180.14*"
                    " — $12,069.38\nTicket `a3f90000`")


def test_a_sell_fill_says_sold_not_sell():
    text = render("fill", {**FILL, "side": "sell"})[1]
    assert "sold *67 NVDA*" in text


def test_signal_names_the_seat_and_quotes_the_summary():
    channel, text = render("signal", SIGNAL)[:2]
    assert channel == "#research"
    assert text == ("*Nora (Analyst)* · *NVDA* · bullish, conviction 72/100\n"
                    "> upgraded to Buy on datacenter demand")


def test_an_unmapped_seat_falls_back_to_its_raw_name():
    text = render("signal", {**SIGNAL, "agent": "macro"})[1]
    assert text.startswith("*macro* · ")


def test_decision_reads_as_an_instruction_not_a_verdict_line():
    channel, text = render("decision", DECISION)[:2]
    assert channel == "#trading-floor"
    assert text == ("*Vic (PM)* · *NVDA* — buy 80 shares\n"
                    "> datacenter demand is not priced in")


def test_a_hold_decision_does_not_claim_a_share_count():
    text = render("decision", {**DECISION, "action": "hold", "qty": 0})[1]
    assert "*NVDA* — hold\n" in text
    assert "0 shares" not in text


def test_gate_approval_spells_out_the_cap_and_labels_the_ticket():
    channel, text = render("gate_approved", GATE_OK)[:2]
    assert channel == "#risk"
    assert text == ("*Risk Gate* · ✅ *buy NVDA* approved for up to *67 shares*\n"
                    "Ticket `a3f90000` · expires 16:00 ET")


def test_gate_rejection_explains_the_reason_code_in_english():
    channel, text = render("gate_rejected", GATE_NO)[:2]
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
    assert render("alert", {"text": "boom"})[:2] == ("#risk", "⚠️ *Alert* · boom")


FALLBACK = {"seat": "analyst", "configured": "claude-haiku-4-5-20251001",
            "served": ["claude-sonnet-5"]}


def test_a_model_fallback_names_both_models_and_what_it_costs_the_reader():
    """agents/runtime.py appends this when model_usage names a model the seat
    was not configured to run. The post has to say what the divergence MEANS —
    the day's rows now name a model that did not serve them — or a reader
    files it as trivia."""
    channel, text = render("model_fallback_used", FALLBACK)[:2]
    assert channel == "#risk"
    assert "claude-sonnet-5" in text and "claude-haiku-4-5-20251001" in text
    assert "Nora (Analyst)" in text


# --- Block Kit (tier 2) -----------------------------------------------------

DIGEST = {"text": "2026-07-06 close\ndecisions: NVDA buy 80 (executed)\n"
                  "fills: NVDA buy 66@180.14\nest. inference cost $0.03",
          "run_date": "2026-07-06", "cost_usd": 0.03,
          "decisions": [{"ticker": "NVDA", "action": "buy", "qty": 80,
                         "status": "executed"}],
          "fills": [{"symbol": "NVDA", "side": "buy", "filled_qty": 66,
                     "filled_avg_price": 180.14, "partial": False}]}

PNL = {"text": "2026-07-06 close · P&L +$412.00 (+0.41%) · SPY +0.18% ·"
               " alpha +0.23% · equity $100,412.00",
       "run_date": "2026-07-06", "equity": 100412.0, "pnl_usd": 412.0,
       "pnl_pct": 0.0041, "spy_pct": 0.0018, "alpha": 0.0023}

BLOCK_KINDS = [("signal", SIGNAL), ("decision", DECISION),
               ("gate_approved", GATE_OK), ("gate_rejected", GATE_NO),
               ("fill", FILL), ("alert", {"text": "boom"}),
               ("digest", DIGEST), ("pnl", PNL),
               ("model_fallback_used", FALLBACK)]


@pytest.mark.parametrize("kind,payload", BLOCK_KINDS)
def test_a_post_with_blocks_still_carries_text(kind, payload):
    """A blocks-only message shows as BLANK in push notifications and to
    screen readers. text is the fallback Slack renders there, so it must stay
    populated for every kind that gains blocks."""
    post = render(kind, payload)
    assert post.blocks, f"{kind} gained no blocks"
    assert post.text.strip(), f"{kind} has blocks but no text fallback"


@pytest.mark.parametrize("kind,payload", BLOCK_KINDS)
def test_blocks_are_well_formed_block_kit(kind, payload):
    """Structural guard: Slack rejects the whole message on a malformed
    block, and that rejection is permanent — the event would be lost from the
    projection forever. Cheaper to catch here."""
    for block in render(kind, payload).blocks:
        assert block["type"] in {"section", "context", "divider"}, block
        if block["type"] == "section":
            assert set(block) <= {"type", "text", "fields"}
            if "text" in block:
                assert block["text"]["type"] == "mrkdwn"
                assert block["text"]["text"].strip()
            for field in block.get("fields", []):
                assert field["type"] == "mrkdwn" and field["text"].strip()
            assert len(block.get("fields", [])) <= 10
        if block["type"] == "context":
            assert 1 <= len(block["elements"]) <= 10
            for el in block["elements"]:
                assert el["type"] == "mrkdwn" and el["text"].strip()


@pytest.mark.parametrize("kind,payload", BLOCK_KINDS)
def test_no_block_text_exceeds_slacks_limit(kind, payload):
    """Slack caps section text at 3000 chars and rejects the message with
    msg_blocks_too_long above it — permanent, so the post would dead-letter.
    A 4000-char PM thesis is not a hypothetical."""
    big = {k: ("x" * 4000 if isinstance(v, str) and k in
               ("summary", "thesis", "text") else v) for k, v in payload.items()}
    for block in render(kind, big).blocks:
        for chunk in ([block["text"]] if "text" in block else []) + \
                     block.get("fields", []) + block.get("elements", []):
            assert len(chunk["text"]) <= 3000, f"{kind}: {len(chunk['text'])} chars"


def test_fill_blocks_state_price_and_notional_as_fields():
    blocks = render("fill", FILL).blocks
    fields = [f["text"] for b in blocks for f in b.get("fields", [])]
    assert any("$180.14" in f for f in fields)
    assert any("$12,069.38" in f for f in fields)
    context = [e["text"] for b in blocks if b["type"] == "context"
               for e in b["elements"]]
    assert any("Dash (Execution)" in c and "a3f90000" in c for c in context)


def test_gate_approval_blocks_state_the_cap_and_the_expiry_as_fields():
    blocks = render("gate_approved", GATE_OK).blocks
    fields = [f["text"] for b in blocks for f in b.get("fields", [])]
    assert any("67 shares" in f for f in fields)
    assert any("16:00 ET" in f for f in fields)


def test_signal_blocks_quote_the_summary_and_credit_the_seat():
    blocks = render("signal", SIGNAL).blocks
    assert any("> upgraded to Buy on datacenter demand" in b["text"]["text"]
               for b in blocks if b["type"] == "section")
    assert any("Nora (Analyst)" in e["text"] for b in blocks
               if b["type"] == "context" for e in b["elements"])


def test_projection_error_posts_as_plain_text_with_no_blocks():
    """Already prose. None means the port posts text only."""
    assert render("projection_error", {"event_id": 3, "kind": "bogus"}).blocks is None


@pytest.mark.parametrize("kind", ["digest", "pnl"])
def test_a_digest_row_written_before_block_kit_still_renders(kind):
    """Rows with only the flat `text` already exist in the production DB.
    They must keep posting as text rather than raising on a missing field —
    a dead-lettered digest is a lost day of evidence (HANDOFF-LIVE §5)."""
    post = render(kind, {"text": "2026-07-06 close", "run_date": "2026-07-06"})
    assert post.blocks is None
    assert post.text == "2026-07-06 close"


def test_digest_blocks_count_the_day_and_flag_the_cost_as_an_estimate():
    blocks = render("digest", DIGEST).blocks
    fields = [f["text"] for b in blocks for f in b.get("fields", [])]
    assert any("*Decisions*\n1" in f for f in fields)
    assert any("*Fills*\n1" in f for f in fields)
    # total_cost_usd is a client-side estimate — always labeled est. (CLAUDE.md)
    assert any("Est. cost" in f and "$0.03" in f for f in fields)


def test_digest_blocks_list_the_decisions_and_the_fills():
    text = " ".join(b["text"]["text"] for b in render("digest", DIGEST).blocks
                    if "text" in b)
    assert "NVDA buy 80 (executed)" in text
    assert "NVDA buy 66@180.14" in text


def test_a_partial_fill_stays_marked_in_the_digest_blocks():
    """`fills: none` beside real shares was a live bug; `(partial)` must keep
    meaning something in the block layout too."""
    payload = {**DIGEST, "fills": [{**DIGEST["fills"][0], "partial": True}]}
    text = " ".join(b["text"]["text"] for b in render("digest", payload).blocks
                    if "text" in b)
    assert "(partial)" in text


def test_a_hold_only_day_still_renders_a_digest():
    payload = {**DIGEST, "decisions": [], "fills": []}
    blocks = render("digest", payload).blocks
    fields = [f["text"] for b in blocks for f in b.get("fields", [])]
    assert any("*Decisions*\n0" in f for f in fields)
    assert all(b["type"] in {"section", "context"} for b in blocks)


def test_pnl_blocks_carry_every_figure_with_an_explicit_sign():
    """A losing day and a winning one must not differ by a character someone
    can miss while skimming (orchestrator/pnl.format_line)."""
    fields = [f["text"] for b in render("pnl", PNL).blocks
              for f in b.get("fields", [])]
    joined = " ".join(fields)
    assert "+$412.00" in joined and "+0.41%" in joined
    assert "+0.18%" in joined and "+0.23%" in joined
    assert "$100,412.00" in joined


def test_a_losing_day_renders_a_minus_not_a_bare_number():
    losing = {**PNL, "pnl_usd": -412.0, "pnl_pct": -0.0041, "alpha": -0.0023}
    fields = [f["text"] for b in render("pnl", losing).blocks
              for f in b.get("fields", [])]
    joined = " ".join(fields)
    assert "-$412.00" in joined and "-0.41%" in joined and "-0.23%" in joined


# --- seat identity (tier 3) -------------------------------------------------

def test_a_signal_posts_under_the_analysts_own_name_and_face():
    """The point of the feature: who is speaking is readable from the message
    header, before a word of the body is read."""
    post = render("signal", SIGNAL)
    assert post.username == "Nora (Analyst)"
    assert post.icon_emoji == "🔎"


def test_a_decision_posts_under_the_seat_its_payload_names():
    post = render("decision", {**DECISION, "seat": "pm"})
    assert post.username == "Vic (PM)"
    assert post.icon_emoji == "🎯"


def test_a_decision_from_another_seat_is_not_attributed_to_the_pm():
    """Guards the hardcode this replaced: the renderer must read the seat,
    not assume it."""
    post = render("decision", {**DECISION, "seat": "quant"})
    assert post.username == "Kai (Quant)"


def test_a_decision_row_written_before_seats_still_credits_the_pm():
    """Rows already in the production DB carry no `seat`. Only the PM could
    have written them, so they keep posting as the PM rather than raising."""
    assert render("decision", DECISION).username == "Vic (PM)"


def test_an_unmapped_seat_gets_its_raw_name_and_no_face():
    """A new seat must not take the projection down, and must not borrow
    another seat's face."""
    post = render("signal", {**SIGNAL, "agent": "macro"})
    assert post.username == "macro"
    assert post.icon_emoji is None


ALL_KINDS = BLOCK_KINDS + [("projection_error", {"event_id": 3,
                                                 "kind": "bogus"})]
SPEAKS = {"signal", "decision"}


@pytest.mark.parametrize("kind,payload", ALL_KINDS)
def test_only_the_kinds_with_a_model_behind_them_have_a_face(kind, payload):
    """Both directions, so a kind added later is caught either way it errs.
    Invariant 3 keeps the gate free of LLM code; this keeps machinery free of
    an LLM's face — a reader must be able to tell a model's words from code
    that cannot be argued with."""
    post = render(kind, payload)
    assert (post.username is not None) is (kind in SPEAKS), kind
    assert (post.icon_emoji is not None) is (kind in SPEAKS), kind


def test_the_outbox_hands_the_persona_to_the_port(fund_db):
    slack = FakeSlack()
    append_event(fund_db, "signal", SIGNAL, NOW)
    assert drain(fund_db, slack, NOW) == 1
    post = slack.posts["#research"][0]
    assert post["username"] == "Nora (Analyst)"
    assert post["icon_emoji"] == "🔎"


def test_outbox_hands_blocks_to_the_port(fund_db):
    slack = FakeSlack()
    append_event(fund_db, "fill", FILL, NOW)
    assert drain(fund_db, slack, NOW) == 1
    post = slack.posts["#trade-log"][0]
    assert post["blocks"] == render("fill", FILL).blocks
    assert post["text"] == render("fill", FILL).text


def test_outbox_posts_prose_kinds_with_no_blocks(fund_db):
    slack = FakeSlack()
    append_event(fund_db, "digest", {"text": "day 1"}, NOW)
    assert drain(fund_db, slack, NOW) == 1
    assert slack.posts["#pnl"][0]["blocks"] is None


def test_real_slack_treats_a_malformed_block_payload_as_permanent():
    """Slack rejects the whole message and will reject it identically on
    every retry, so it must dead-letter rather than stop the drain forever."""
    from slack_sdk.errors import SlackApiError

    from slackkit.port import PermanentPostError
    from slackkit.real import RealSlack

    for code in ("invalid_blocks", "invalid_blocks_format",
                 "msg_blocks_too_long"):
        slack = RealSlack("xoxb-not-a-real-token")

        def _raise(**kwargs):
            raise SlackApiError("boom", {"ok": False, "error": code})

        slack._client.chat_postMessage = _raise
        with pytest.raises(PermanentPostError) as exc:
            slack.post("#risk", "hi", blocks=[{"type": "divider"}])
        assert code in str(exc.value)


def test_real_slack_sends_blocks_and_omits_them_when_absent():
    from slackkit.real import RealSlack

    slack = RealSlack("xoxb-not-a-real-token")
    sent = []

    def _capture(**kwargs):
        sent.append(kwargs)
        return {"ts": "1.0"}

    slack._client.chat_postMessage = _capture
    slack.post("#risk", "hi", blocks=[{"type": "divider"}])
    slack.post("#risk", "plain")
    assert sent[0]["blocks"] == [{"type": "divider"}]
    assert sent[0]["text"] == "hi"          # fallback always goes with them
    assert "blocks" not in sent[1] or sent[1]["blocks"] is None


def test_real_slack_treats_a_refused_persona_as_permanent():
    """A token that may not set `username` is refused identically on every
    retry until a human changes the app's scopes — permanent by the same
    definition as invalid_auth. Left transient it would stop the drain on the
    day's FIRST signal, queueing every gate post, fill and digest behind it;
    dead-lettering costs one post and reddens the day through the audit's
    projection_error check."""
    from slack_sdk.errors import SlackApiError

    from slackkit.port import PermanentPostError
    from slackkit.real import RealSlack

    for code in ("missing_scope", "not_allowed_token_type"):
        slack = RealSlack("xoxb-not-a-real-token")

        def _raise(**kwargs):
            raise SlackApiError("boom", {"ok": False, "error": code})

        slack._client.chat_postMessage = _raise
        with pytest.raises(PermanentPostError) as exc:
            slack.post("#research", "hi", username="Nora (Analyst)",
                       icon_emoji="🔎")
        assert code in str(exc.value)


def test_real_slack_sends_the_persona_and_omits_it_when_absent():
    from slackkit.real import RealSlack

    slack = RealSlack("xoxb-not-a-real-token")
    sent = []

    def _capture(**kwargs):
        sent.append(kwargs)
        return {"ts": "1.0"}

    slack._client.chat_postMessage = _capture
    slack.post("#research", "hi", username="Nora (Analyst)", icon_emoji="🔎")
    slack.post("#risk", "gate")
    assert sent[0]["username"] == "Nora (Analyst)"
    assert sent[0]["icon_emoji"] == "🔎"
    # machinery must not send the keys at all: Slack validates an explicit
    # null rather than ignoring it
    assert "username" not in sent[1] and "icon_emoji" not in sent[1]


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

    def post(self, channel: str, text: str, thread_ts: str | None = None,
             blocks: list[dict] | None = None, username: str | None = None,
             icon_emoji: str | None = None) -> str:
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

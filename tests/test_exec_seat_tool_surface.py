"""Regression guard for every seat's tool surface + settings isolation.

The exec seat places real (paper) orders; analyst and pm are read-only. Every
seat's entire capability surface must be a diff: exactly the two MCP tool
globs, nothing from the claude_code preset (no Bash/Write/Edit/Task/Agent/
Workflow/Web/Cron), and no on-disk settings or CLAUDE.md feeding its context
or permission surface. Read-only seats additionally must never be able to
reach `mcp__alpaca__place_*` (invariant 2).

`tools` governs AVAILABILITY; `allowed_tools`/`disallowed_tools` only govern
APPROVAL. Leaving `tools` unset inherits the full coding-agent surface — which
lets a seat route around the gate/recorder/default-HOLD entirely (Read .env ->
curl broker). This test pins the levers that close that hole so a harness
change or a reverted config cannot silently regrow shell access, or silently
grant a read-only seat the `trading` toolset.

These assertions intentionally FAIL against the pre-fix config (tools=None,
setting_sources=["project"]). That failure is the point: it is the instrument
that measures the fix.
"""

from datetime import datetime, timezone

import pytest

from agents.seats import build_seat_options, load_seat_config
from orchestrator.clock import SimClock

BANNED_BUILTINS = (
    "Bash", "Write", "Edit", "NotebookEdit", "Task", "Agent", "Workflow",
    "WebFetch", "WebSearch", "ScheduleWakeup", "CronCreate", "CronDelete",
    "Read", "Skill", "ToolSearch",
)

SEATS = ("exec", "analyst", "news", "pm", "critic")

# NEITHER reflect NOR quant is in SEATS, for the same reason: each one's tool
# surface is legitimately narrower than the five, and folding either into that
# tuple would force an edit to test_tools_are_exactly_the_two_mcp_globs —
# weakening the assertion that protects the five to accommodate a sixth. Every
# OTHER pin applies to both unchanged, and a seat escaping THOSE is the real
# risk, so they run over this tuple instead.
#
# They are also absent from the read-only parametrizations below, which assert
# a threaded ALPACA_TOOLSETS on seats that carry the alpaca glob. Neither of
# these two carries it, so the stronger statement — that the broker is not
# reachable at all — is made once per seat, directly, below.
#
# quant is NOT free: nothing forces a new seat into this file. These tuples are
# hand-maintained, so a seat added to SEAT_CAPS escapes every pin here until
# someone adds it. #198 added it.
ALL_SEATS = SEATS + ("reflect", "quant")


def _cfg(seat: str) -> dict:
    return load_seat_config(f"agents/config/{seat}.yaml")


def _opts(seat: str, tmp_path):
    cfg = _cfg(seat)
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    return build_seat_options(cfg, tmp_path / "fund.sqlite", clock)


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_tools_is_explicit_not_the_full_preset(seat, tmp_path):
    # None => the CLI applies the claude_code preset (Bash/Write/Task/...).
    # The seat MUST pass an explicit allow-array.
    assert _opts(seat, tmp_path).tools is not None


@pytest.mark.parametrize("seat", SEATS)
def test_tools_are_exactly_the_two_mcp_globs(seat, tmp_path):
    assert _opts(seat, tmp_path).tools == ["mcp__fund__*", "mcp__alpaca__*"]


def test_the_alpaca_glob_is_safe_only_because_the_gate_denies_what_it_admits():
    """The assertion above pins a WILDCARD, and for a while that was the whole
    check — which is how a seat holding `alpaca_toolsets: trading` passed a
    test named for its tool surface while `cancel_all_orders` and
    `close_all_positions` were reachable with no ticket and no `orders` row.

    `mcp__alpaca__*` is the right shape: enumerating every read verb would
    break the exec turn the first time the toolset grew a getter. What makes it
    SAFE is that the PreToolUse gate allowlists what the glob admits. This
    pins the two together, so loosening either one alone reddens here.

    Deliberately asserted at the policy level rather than by driving the hook:
    this file is about what the SEAT can reach, and tests/test_runtime_hooks.py
    owns the hook's behaviour. All this claims is that the two agree."""
    from agents.runtime import _broker_verb_policy

    for verb in ("cancel_order_by_id", "cancel_all_orders", "close_position",
                 "close_all_positions"):
        assert _broker_verb_policy(f"mcp__alpaca__{verb}") == "deny", verb
    # ...and the glob must still admit the surface the seat genuinely needs
    assert _broker_verb_policy("mcp__alpaca__place_stock_order") == "gated"
    assert _broker_verb_policy("mcp__alpaca__get_account_info") == "allow"


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_no_builtin_tool_is_available_to_the_seat(seat, tmp_path):
    tools = _opts(seat, tmp_path).tools or []
    leaked = [t for t in tools if t in BANNED_BUILTINS]
    assert leaked == [], f"seat can call built-in tools: {leaked}"


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_setting_sources_empty_no_claude_md_or_project_settings(seat, tmp_path):
    # setting_sources=[] => --setting-sources= (nothing). No CLAUDE.md, no
    # project/local settings.json feeding context or the permission allow-list.
    assert _opts(seat, tmp_path).setting_sources == []


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_permission_mode_is_dont_ask(seat, tmp_path):
    assert _opts(seat, tmp_path).permission_mode == "dontAsk"


@pytest.mark.parametrize("seat", ["analyst", "news", "pm", "critic"])
def test_read_only_seats_cannot_trade(seat, tmp_path):
    opts = _opts(seat, tmp_path)
    assert "trading" not in _cfg(seat)["alpaca_toolsets"]
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    # The yaml value is inert unless it's actually threaded into the built
    # options — this is what the alpaca-mcp-server subprocess reads to decide
    # which tools to REGISTER at all (the only load-bearing lock for this
    # seat's `mcp__alpaca__*` glob).
    env = opts.mcp_servers["alpaca"]["env"]
    assert env["ALPACA_TOOLSETS"] == _cfg(seat)["alpaca_toolsets"]
    assert "trading" not in env["ALPACA_TOOLSETS"]


def test_only_exec_has_trading_toolset(tmp_path):
    assert "trading" in _cfg("exec")["alpaca_toolsets"]
    env = _opts("exec", tmp_path).mcp_servers["alpaca"]["env"]
    assert env["ALPACA_TOOLSETS"] == _cfg("exec")["alpaca_toolsets"]
    assert "trading" in env["ALPACA_TOOLSETS"]


@pytest.mark.parametrize("seat", ["analyst", "news", "pm", "critic"])
def test_read_only_seats_carry_no_order_hooks(seat, tmp_path):
    # Only the trading seat may carry the PreToolUse order gate / PostToolUse
    # recorder (CLAUDE.md: hooks attach only to a seat that trades).
    assert _opts(seat, tmp_path).hooks in (None, {})


def test_exec_carries_both_order_hooks(tmp_path):
    hooks = _opts("exec", tmp_path).hooks
    assert hooks and "PreToolUse" in hooks and "PostToolUse" in hooks


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_the_seat_yaml_budget_cap_is_threaded_into_the_options(seat, tmp_path):
    """max_budget_usd is the only hard stop on a runaway turn. A yaml value
    that is not actually threaded into the built options is inert — the SDK
    would apply no cap at all, and the first evidence would be the bill.

    The caps are BACKSTOPS, not the expectation: they sum to $2.25 worst case
    against an expected spend under $0.50/day (README "Cost"). What bounds the
    expectation is the watchlist size and the per-seat max_turns, not these."""
    cap = _cfg(seat)["max_budget_usd"]
    assert isinstance(cap, (int, float)) and cap > 0
    assert _opts(seat, tmp_path).max_budget_usd == cap


def test_the_reflect_seat_cannot_reach_the_broker_at_all(tmp_path):
    """Stricter than every other seat, deliberately. The read-only seats need
    prices; a reflection turn reads nothing — its facts are computed inside
    its one tool before the seat ever sees them.

    Omitting the alpaca glob from `tools` is what makes the broker
    UNAVAILABLE. The alternative — carrying the glob with a narrow
    ALPACA_TOOLSETS — would rest on what that env value means to
    alpaca-mcp-server@2.2.1, a third-party behaviour no offline test can
    check, and would resolve that unknown toward granting a toolset. This
    assertion depends on no such fact.
    """
    cfg = _cfg("reflect")
    options = _opts("reflect", tmp_path)
    assert options.tools == ["mcp__fund__*"]
    assert "mcp__alpaca__*" not in options.tools
    assert "trading" not in cfg["alpaca_toolsets"]
    assert options.hooks in (None, {})


def test_the_quant_seat_cannot_reach_the_broker_at_all(tmp_path):
    """Same posture as reflect, and for the same reason (#198): the seat has
    ONE cap, it is a write, and it has no read tool of any kind — it is handed
    its subject in the prompt and asks nothing. A seat with nothing to ask a
    broker does not carry the broker glob.

    Omitting `mcp__alpaca__*` from `tools` is what makes it UNAVAILABLE. The
    alternative — carrying the glob with a narrow ALPACA_TOOLSETS — would rest
    on what that env value means to alpaca-mcp-server@2.2.1, a third-party
    behaviour no offline test can check, and would resolve that unknown toward
    granting a toolset."""
    cfg = _cfg("quant")
    options = _opts("quant", tmp_path)
    assert options.tools == ["mcp__fund__*"]
    assert "mcp__alpaca__*" not in options.tools
    assert "trading" not in cfg["alpaca_toolsets"]
    assert options.hooks in (None, {})


def _clock():
    return SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))


G1_TOOLS = ["mcp__fund__get_spec_brief", "mcp__fund__submit_spec_critique"]


def test_a_per_turn_tools_override_narrows_the_seat_for_one_turn(tmp_path):
    """The nightly G1 turn (issue #169) needs the Critic down to two tools for
    ONE turn, without editing agents/config/critic.yaml — the standing config
    is what specs/design.md's seat table describes and what the parametrized
    assertions above pin. #170's Phase 6 per-KIND tool surfaces reuse this
    same parameter rather than building their own."""
    opts = build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite",
                              _clock(), tools=G1_TOOLS)
    assert opts.tools == G1_TOOLS
    assert "mcp__alpaca__*" not in opts.tools
    assert "mcp__fund__*" not in opts.tools


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_the_override_is_absent_by_default_and_changes_no_seat(seat, tmp_path):
    """Additive by construction: every existing call site omits the kwarg."""
    assert _opts(seat, tmp_path).tools == _cfg(seat)["tools"]


@pytest.mark.parametrize("seat,refusal", [("critic", "invariant 2"),
                                          ("reflect", "may only NARROW")])
def test_a_per_turn_override_cannot_grant_an_order_tool(seat, refusal,
                                                        tmp_path):
    """PARAMETRIZED OVER critic, not only reflect — this is the review defect
    that made the first version of this guard decorative. agents/config/
    critic.yaml carries tools: ["mcp__fund__*", "mcp__alpaca__*"], so a check
    against the seat's standing GLOBS accepts mcp__alpaca__place_stock_order
    for the very seat this lane narrows. It rejected only for reflect, whose
    standing tools are ["mcp__fund__*"] — and reflect was the only seat the
    test covered.

    THE TWO SEATS ARE REFUSED FOR DIFFERENT REASONS, and the parametrization
    carries the message each one genuinely raises rather than a substring both
    happen to share. Asserting one message for both is what a first revision of
    this test did, and it shipped RED: agents/config/reflect.yaml:28 is
    `tools: ["mcp__fund__*"]` with no alpaca glob at all, so for reflect the
    name never reaches the invariant-2 branch — it is refused one step earlier,
    as a name the seat is not served. Loosening the pattern until both passed
    would have been the assertion that goes green under any refusal, which is
    exactly the decorative-guard defect this test exists to close.

      critic   IS served the alpaca glob, so the name is admitted by the
               standing surface and refused by the invariant-2 rule keyed off
               alpaca_toolsets — the branch that had to be added.
      reflect  is not served the alpaca surface at all, so the name is refused
               as ungranted. Still a refusal, and still worth pinning, but it
               proves nothing about invariant 2.

    Invariant 2 therefore holds for every seat without `trading` in
    alpaca_toolsets, which is every seat but exec — by the critic case."""
    with pytest.raises(ValueError, match=refusal):
        build_seat_options(_cfg(seat), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__alpaca__place_stock_order"])


def test_a_per_turn_override_cannot_name_a_fund_tool_the_seat_is_not_served(
        tmp_path):
    """SEAT_CAPS is the fund server's registration, so a name outside it does
    not exist for this seat. Checking against it makes the guard bite for the
    Critic, where the yaml glob `mcp__fund__*` admits everything."""
    with pytest.raises(ValueError, match="may only NARROW"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__fund__get_spec_brief",
                                  "mcp__fund__submit_decision"])


def test_a_per_turn_override_cannot_smuggle_in_a_builtin(tmp_path):
    """The whole point of `tools` is that the claude_code preset never reaches
    a seat. An override is not a second door into it."""
    with pytest.raises(ValueError, match="may only NARROW"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__fund__get_spec_brief", "Bash"])


@pytest.mark.parametrize("pattern", [
    "mcp__alpaca__*",
    "mcp__alpaca__*place*",
    "mcp__alpaca__pla?e_stock_order",
    "mcp__alpaca__[pq]lace_stock_order",
    "mcp__fund__*",
    "mcp__fund__submit_*",
])
def test_a_per_turn_override_must_name_concrete_tools_never_a_glob(pattern,
                                                                   tmp_path):
    """AN OVERRIDE THAT IS A GLOB RE-WIDENS THE SURFACE, which is the same
    defect class the served-surface check exists to close, one level up.

    `tools` entries in this repo really are globs — agents/seats.py:92 passes
    cfg["tools"] straight to the SDK and every agents/config/*.yaml uses
    wildcards. So an override is not a list of names the SDK looks up; it is a
    list of PATTERNS the SDK expands. Before this rule, all four of these were
    ACCEPTED for the Critic: ['mcp__alpaca__*'], ['mcp__alpaca__*place*'],
    ['mcp__alpaca__pla?e_stock_order'] and ['mcp__alpaca__[pq]lace_...'] — the
    invariant-2 rule is a LITERAL fnmatchcase(name, "mcp__alpaca__place_*")
    test, and a wildcard walks straight past a literal test.

    Nothing escalates today: critic's alpaca_toolsets is `stock-data` and its
    disallowed_tools denies mcp__alpaca__place_*, so no order tool is actually
    served to the seat whatever this list says. This closes an OVERCLAIM, not a
    live hole — but the claim is made in three places, so the code has to be
    the thing that is true.

    Refusing the metacharacter, rather than trying to prove a glob is a subset,
    is deliberate: subset-of-a-glob is undecidable against an external server
    whose tool names this repo never enumerates, and a per-TURN narrowing has
    no legitimate need for a pattern — it names the two tools the turn uses."""
    with pytest.raises(ValueError, match="must name CONCRETE tools"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=[pattern])


def test_the_exec_seat_may_still_be_narrowed_to_its_own_order_tool(tmp_path):
    """The trading rule is keyed off alpaca_toolsets, the same field the order
    hooks key off (agents/seats.py:118) — not off a seat-name allow-list. The
    one seat that legitimately carries `trading` is not blocked from a future
    per-turn narrowing that includes its order tool."""
    opts = build_seat_options(_cfg("exec"), tmp_path / "fund.sqlite", _clock(),
                              tools=["mcp__alpaca__place_stock_order"])
    assert opts.tools == ["mcp__alpaca__place_stock_order"]


def test_an_empty_override_is_refused_not_read_as_no_narrowing(tmp_path):
    """[] and None are different intents and must not collapse. A seat with no
    tools cannot complete any turn; running one and paying for it is worse
    than refusing to build it."""
    with pytest.raises(ValueError, match="empty per-turn surface"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=[])


def test_a_narrowed_turn_keeps_every_other_guard(tmp_path):
    """A narrowing must not become the place a second guard is quietly
    dropped: the deny-list belt, the absence of order hooks, the empty
    setting_sources and the budget cap are all unchanged."""
    opts = build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite",
                              _clock(), tools=G1_TOOLS)
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    assert opts.hooks in (None, {})
    assert opts.setting_sources == []
    assert opts.permission_mode == "dontAsk"
    assert opts.max_budget_usd == _cfg("critic")["max_budget_usd"]

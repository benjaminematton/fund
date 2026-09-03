"""Unit tests for devcheck's pure checks.

Every check is a pure function of a Snapshot, so each test states a whole
world and asserts one verdict. Each check gets a negative control: a world
where it MUST fire. A check whose negative control also passes is not a
check — three tests in this repo passed on 2026-08-21 because they always
passed, and two were caught by luck.
"""

from __future__ import annotations

from devcheck.evaluate import evaluate
from devcheck.model import OrderRow, Position, ServiceResult, Snapshot


def _snap(**over) -> Snapshot:
    """A wholly healthy world. Each test darkens exactly one field."""
    base = dict(
        droplet_env={"ALPACA_PAPER_TRADE": "true"},
        seat_trading_toolsets={"exec": True, "pm": False, "analyst": False, "news": False},
        orders=[],
        tickets={},
        events_unposted=0,
        broker_fill_count=0,
        checkpoints=[],
        journals_written=set(),
        seats_participating=set(),
        alert_codes=[],
        alert_date="2026-09-02",
        positions=[],
        open_orders=[],
        due_unresolved=[],
        droplet_head="abc1234",
        origin_master="abc1234",
        commits_behind=0,
        services={},
        suppressed=frozenset(),
        tracked_checks=frozenset(),
    )
    base.update(over)
    return Snapshot(**base)


def test_paper_trading_true_is_ok():
    findings = evaluate(_snap())
    paper = [f for f in findings if f.check == "paper_trading"]
    assert len(paper) == 1
    assert paper[0].severity == "ok"


def test_paper_trading_false_alerts():
    """Negative control for invariant 1 — the most important line in CLAUDE.md."""
    findings = evaluate(_snap(droplet_env={"ALPACA_PAPER_TRADE": "false"}))
    paper = [f for f in findings if f.check == "paper_trading"]
    assert paper[0].severity == "alert"
    assert "invariant 1" in paper[0].detail


def test_paper_trading_missing_alerts():
    """Absent is not the same as false, and both must alert."""
    findings = evaluate(_snap(droplet_env={}))
    paper = [f for f in findings if f.check == "paper_trading"]
    assert paper[0].severity == "alert"


def _only(findings, check):
    matches = [f for f in findings if f.check == check]
    assert len(matches) == 1, f"expected exactly one {check}, got {matches}"
    return matches[0]


def test_trading_toolset_exec_only_is_ok():
    assert _only(evaluate(_snap()), "trading_toolset").severity == "ok"


def test_trading_toolset_second_seat_alerts():
    """Negative control for invariant 2."""
    f = _only(evaluate(_snap(seat_trading_toolsets={"exec": True, "pm": True})), "trading_toolset")
    assert f.severity == "alert"
    assert "pm" in f.detail


def test_trading_toolset_exec_missing_alerts():
    """Exec losing `trading` is silent otherwise: the day just never fills."""
    f = _only(evaluate(_snap(seat_trading_toolsets={"exec": False})), "trading_toolset")
    assert f.severity == "alert"


def test_order_idempotency_ok_when_every_coid_is_a_ticket():
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"})
    assert _only(evaluate(s), "order_idempotency").severity == "ok"


def test_order_idempotency_alerts_on_unknown_coid():
    """Negative control for invariant 5 — a coid that is not a ticket id
    means an order the gate did not authorise, or a minted retry id."""
    s = _snap(orders=[OrderRow("free-form", "NVDA")], tickets={"t-1": "NVDA"})
    f = _only(evaluate(s), "order_idempotency")
    assert f.severity == "alert"
    assert "free-form" in f.detail


def test_outbox_ok_when_drained():
    assert _only(evaluate(_snap()), "outbox").severity == "ok"


def test_outbox_alerts_on_backlog():
    """Negative control for invariant 6 — Slack is the projection, and an
    undrained outbox means it is silently stale."""
    f = _only(evaluate(_snap(events_unposted=3)), "outbox")
    assert f.severity == "alert"
    assert "3" in f.detail


def test_db_broker_agreement_ok_when_counts_match():
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"}, broker_fill_count=1)
    assert _only(evaluate(s), "db_broker_agreement").severity == "ok"


def test_db_broker_agreement_alerts_when_broker_saw_more():
    """Negative control for invariant 6 — SQLite is the source of truth, so
    the broker having seen fills the DB has no row for is a divergence."""
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"}, broker_fill_count=3)
    f = _only(evaluate(s), "db_broker_agreement")
    assert f.severity == "alert"
    assert "1" in f.detail and "3" in f.detail


def test_degradations_ok_when_none():
    assert _only(evaluate(_snap()), "degradations").severity == "ok"


def test_degradations_warn_on_gate_error():
    """Invariant 4 says a gate_error resolves to HOLD, which is correct
    behaviour — so this warns, it does not alert. The day was not wrong;
    it was degraded, and a degraded day that nobody sees becomes normal."""
    f = _only(evaluate(_snap(alert_codes=["gate_error"])), "degradations")
    assert f.severity == "warn"
    assert "gate_error" in f.detail


def test_degradations_warn_on_pm_timeout():
    f = _only(evaluate(_snap(alert_codes=["pm_timeout"])), "degradations")
    assert f.severity == "warn"


def test_checkpoints_ok_when_all_done():
    s = _snap(checkpoints=[("2026-08-21", "research", "done"), ("2026-08-21", "gate", "done")])
    assert _only(evaluate(s), "checkpoints").severity == "ok"


def test_checkpoints_alert_on_unfinished_stage():
    """Negative control — Phase 2 acceptance requires every checkpoint done."""
    s = _snap(checkpoints=[("2026-08-21", "research", "done"), ("2026-08-21", "gate", "running")])
    f = _only(evaluate(s), "checkpoints")
    assert f.severity == "alert"
    assert "gate" in f.detail


def test_journals_ok_when_every_participant_wrote():
    s = _snap(seats_participating={"pm", "analyst"}, journals_written={"pm", "analyst"})
    assert _only(evaluate(s), "journals").severity == "ok"


def test_journals_warn_when_a_participant_did_not_write():
    """Phase 2 acceptance: after a day each participating seat has a journal
    entry. Memory is load-bearing in this phase, so a silent seat matters."""
    s = _snap(seats_participating={"pm", "analyst"}, journals_written={"pm"})
    f = _only(evaluate(s), "journals")
    assert f.severity == "warn"
    assert "analyst" in f.detail


def test_reflection_ok_when_nothing_is_due():
    """2026-08-21: resolutions empty, nothing past horizon. Correct, silent."""
    assert _only(evaluate(_snap(due_unresolved=[])), "reflection").severity == "ok"


def test_reflection_alerts_when_something_is_due_and_unresolved():
    """2026-08-24: same empty table, decisions now past horizon. Dead job.

    This pair is the point of the check — identical `resolutions` content,
    opposite verdicts, distinguished only by whether anything is due."""
    f = _only(evaluate(_snap(due_unresolved=[1, 2])), "reflection")
    assert f.severity == "alert"
    assert "2" in f.detail


def test_coverage_ok_when_fully_covered():
    s = _snap(positions=[Position("NVDA", qty=40, covering_qty=40)])
    assert _only(evaluate(s), "position_coverage").severity == "ok"


def test_coverage_alerts_on_a_naked_position():
    """2026-08-21's actual state: 40 shares, zero open orders, no stop."""
    s = _snap(positions=[Position("NVDA", qty=40, covering_qty=0)])
    f = _only(evaluate(s), "position_coverage")
    assert f.severity == "alert"
    assert "NVDA" in f.detail and "0" in f.detail and "40" in f.detail


def test_coverage_alerts_on_partial_cover():
    """Aggregate protection: N shares covered by one or more stops. Partial
    cover is exposure, not protection."""
    s = _snap(positions=[Position("NVDA", qty=80, covering_qty=40)])
    f = _only(evaluate(s), "position_coverage")
    assert f.severity == "alert"


def test_coverage_ok_with_no_positions():
    """Flat is not exposed. The check must not fire on an empty book."""
    assert _only(evaluate(_snap(positions=[])), "position_coverage").severity == "ok"


def test_deploy_state_ok_when_level():
    assert _only(evaluate(_snap()), "deploy_state").severity == "ok"


def test_deploy_state_warns_when_behind():
    """Behind is normal and worth seeing — the box is not running the code
    the suite just went green against."""
    s = _snap(droplet_head="aaa1111", origin_master="bbb2222", commits_behind=22)
    f = _only(evaluate(s), "deploy_state")
    assert f.severity == "warn"
    assert "22" in f.detail


def test_services_ok_when_all_succeeded():
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "success", "2026-08-21T09:35")})
    assert _only(evaluate(s), "services").severity == "ok"


def test_services_alert_on_failure():
    """2026-08-21: fund-daily.service exited 1 at 09:38 and sat 8h."""
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "exit-code", "2026-08-21T09:38")})
    f = _only(evaluate(s), "services")
    assert f.severity == "alert"
    assert "fund-daily" in f.detail


def test_services_alert_when_droplet_unreachable():
    """Spec §4: droplet unreachable renders as a finding; other checks still
    run. Absence of data is never rendered as health."""
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "unreachable", "")})
    f = _only(evaluate(s), "services")
    assert f.severity == "alert"
    assert "unreachable" in f.detail


def test_db_broker_agreement_warns_when_the_broker_could_not_be_read():
    """Absence is never rendered as health (spec §4).

    AlpacaSource exposes no fill history, so this count can genuinely be
    unread. Defaulting an unread count to len(orders) would print 🟢 for a
    comparison nobody performed — the exact "signal that changes meaning
    without changing appearance" shape this package exists to remove.
    """
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"}, broker_fill_count=None)
    f = _only(evaluate(s), "db_broker_agreement")
    assert f.severity == "warn"
    assert "not read" in f.detail


def test_coverage_alerts_when_the_book_could_not_be_read():
    """The false green this check is most dangerous to have.

    An unreachable broker yields no positions, and "0 position(s), every
    share covered" is indistinguishable from a genuinely flat book. Spec §4:
    a failed broker call renders as a finding, and position state is never
    inferred. On 2026-08-21 an unknown exposure sat eight hours.
    """
    f = _only(evaluate(_snap(positions=None)), "position_coverage")
    assert f.severity == "alert"
    assert "not read" in f.detail


def test_issue_coverage_ok_when_alert_is_tracked():
    s = _snap(
        positions=[Position("NVDA", qty=40, covering_qty=0)],   # raises an alert
        tracked_checks=frozenset({"position_coverage"}),
    )
    assert _only(evaluate(s), "issue_coverage").severity == "ok"


def test_issue_coverage_alerts_when_an_alert_is_untracked():
    """Negative control — the finding exists and nothing will remember it."""
    s = _snap(
        positions=[Position("NVDA", qty=40, covering_qty=0)],
        tracked_checks=frozenset(),
    )
    f = _only(evaluate(s), "issue_coverage")
    assert f.severity == "alert"
    assert "position_coverage" in f.detail
    assert "gh issue create" in f.detail


def test_issue_coverage_ignores_warn_and_ok():
    """Only alerts nag. A warn that nagged daily would train the reader to
    skip the report, which is the failure suppression exists to prevent."""
    s = _snap(commits_behind=22, tracked_checks=frozenset())   # deploy_state warns
    assert _only(evaluate(s), "issue_coverage").severity == "ok"


def test_issue_coverage_does_not_report_itself():
    """Without this it alerts about its own alert, forever."""
    s = _snap(positions=[Position("NVDA", qty=1, covering_qty=0)], tracked_checks=frozenset())
    f = _only(evaluate(s), "issue_coverage")
    assert "issue_coverage" not in f.detail


def test_issue_coverage_does_not_nag_about_a_suppressed_check():
    """Suppression means "known noise in this repo". Demanding a GitHub issue
    for known noise re-creates the nagging suppression exists to remove.

    issue_coverage runs inside evaluate(), before apply_suppression() has
    downgraded anything, so it must consult the snapshot's suppression set
    itself rather than the severity it happens to see.
    """
    s = _snap(
        positions=[Position("NVDA", qty=40, covering_qty=0)],   # raises an alert
        suppressed=frozenset({"position_coverage"}),
        tracked_checks=frozenset(),
    )
    assert _only(evaluate(s), "issue_coverage").severity == "ok"


def test_unreadable_database_alerts_once_and_marks_its_checks_unknown():
    """A failed DB read must not render as five green rows.

    Found live: the script queried a path that existed as a 0-byte file, so
    every query returned no rows with exit 0 and `order_idempotency`,
    `outbox`, `checkpoints`, `journals` and `reflection` all printed 🟢. One
    root cause alerts; the checks it starved say they were never checked,
    rather than claiming health nobody measured.
    """
    findings = evaluate(_snap(db_read_ok=False))
    assert _only(findings, "database").severity == "alert"
    for check in ("order_idempotency", "outbox", "checkpoints", "journals", "reflection"):
        f = _only(findings, check)
        assert f.severity == "warn", f"{check} claimed {f.severity} off an unread database"
        assert "not read" in f.detail


def test_readable_database_is_ok_and_leaves_its_checks_alone():
    """Negative control: the same checks keep their real verdicts."""
    findings = evaluate(_snap())
    assert _only(findings, "database").severity == "ok"
    assert _only(findings, "outbox").severity == "ok"


def test_unread_book_names_why_it_could_not_be_read():
    """A red row whose cause is "no credentials in this shell" and one whose
    cause is "the broker is down" must not look identical.

    Without the distinction every local run shows a red top row for a local
    setup reason, and a reader learns to skip the most important check in the
    report inside a week — the same trained blindness suppression exists for.
    """
    f = _only(evaluate(_snap(positions=None, broker_error="no ALPACA_API_KEY in this shell")),
              "position_coverage")
    assert f.severity == "alert"
    assert "no ALPACA_API_KEY in this shell" in f.detail


# --- alert codes on a red `services`, and the degradations input --------------
# Regression cover for 2026-08-31 → 09-02: the Alpaca MCP server stopped
# importing, every seat defaulted to HOLD, and `services` had ALREADY been red
# for nine days on #141. The new failure produced an identical line and was
# attributed to #141 in writing. These pin the difference being visible.

def _units(**results):
    from devcheck.model import ServiceResult
    return {u: ServiceResult(u, r, "Wed 2026-09-02 09:36:51 EDT")
            for u, r in results.items()}


def test_a_red_service_names_which_alerts_fired():
    f = _only(evaluate(_snap(services=_units(**{"fund-daily": "exit-code"}),
                             alert_codes=["seat_turn_failed"] * 3 + ["pm_timeout"],
                             alert_date="2026-09-02")), "services")
    assert f.severity == "alert"
    assert "seat_turn_failed x3" in f.detail, f.detail
    assert "pm_timeout" in f.detail and "pm_timeout x" not in f.detail  # count only when >1
    assert "2026-09-02" in f.detail


def test_two_reds_with_different_codes_do_not_read_alike():
    """The masking failure itself: #141's red and the outage's red must differ."""
    known = _only(evaluate(_snap(services=_units(**{"fund-daily": "exit-code"}),
                                 alert_codes=["accounting_shortfall"])), "services")
    novel = _only(evaluate(_snap(services=_units(**{"fund-daily": "exit-code"}),
                                 alert_codes=["seat_turn_failed"])), "services")
    assert known.detail != novel.detail
    assert "accounting_shortfall" in known.detail
    assert "seat_turn_failed" in novel.detail


def test_unread_alert_codes_are_never_rendered_as_no_alerts():
    """`None` is 'could not read'. An empty list is 'nothing alerted'. Rendering
    the first as the second is how absence becomes health."""
    unread = _only(evaluate(_snap(services=_units(**{"fund-daily": "exit-code"}),
                                  alert_codes=None)), "services")
    empty = _only(evaluate(_snap(services=_units(**{"fund-daily": "exit-code"}),
                                 alert_codes=[], alert_date="2026-09-02")), "services")
    assert "UNREAD" in unread.detail
    assert "no alert rows" in empty.detail
    assert unread.detail != empty.detail


def test_degradations_is_unknown_rather_than_clean_when_codes_are_unread():
    f = _only(evaluate(_snap(alert_codes=None)), "degradations")
    assert f.severity == "warn"
    assert "unknown" in f.detail


def test_degradations_fires_on_the_codes_the_fund_actually_emits():
    """The 2026-09-02 defect: this field was fed event `kind`s (alert/digest/
    pnl), which can never equal a degradation code, so `degradations` was green
    on every day the fund had ever run. These are real codes from that day."""
    real = ["seat_turn_failed"] * 12 + ["pm_timeout"] * 3 + ["audit_failed",
                                                            "reflect_turn_wrote_nothing"]
    f = _only(evaluate(_snap(alert_codes=real)), "degradations")
    assert f.severity == "warn", "pm_timeout fired 3x that day; this must not read clean"
    assert "pm_timeout" in f.detail

    kinds = ["alert", "digest", "pnl", "scorecard"]        # what it used to receive
    stale = _only(evaluate(_snap(alert_codes=kinds)), "degradations")
    assert stale.severity == "ok", "event kinds must not look like degradations either"

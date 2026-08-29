"""ops/*.service — properties whose loss changes trading behavior."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAILY = (ROOT / "ops" / "fund-daily.service").read_text()
PNL = (ROOT / "ops" / "fund-pnl.service").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()
OPS_README = (ROOT / "ops" / "README.md").read_text()


def _exec_starts(unit: str) -> list[str]:
    return [line.split("=", 1)[1].strip()
            for line in unit.splitlines() if line.startswith("ExecStart=")]


def test_heartbeat_cannot_fail_the_trading_day():
    """The `-` prefix is the whole safety argument. Without it a network blip on
    a monitoring ping marks the unit failed AFTER a successful day — converting
    an observability addition into a trading-day failure, and firing OnFailure
    for a day that actually completed."""
    line = next(l for l in DAILY.splitlines() if l.startswith("ExecStopPost="))
    assert line.startswith("ExecStopPost=-"), f"lost the fail-safe prefix: {line}"


def test_heartbeat_runs_after_the_day_not_before():
    """Never ExecStartPre: pinging before the run would report liveness for a
    day that then failed."""
    assert "ExecStopPost=-/usr/bin/curl" in DAILY
    assert "ExecStartPre=/usr/bin/curl" not in DAILY


def test_heartbeat_fires_whether_the_day_passed_or_failed():
    """ExecStopPost, not ExecStartPost. ExecStartPost runs ONLY on success, so a
    failed run sent no ping and the watchdog fired — but OnFailure had already
    alerted on that same run. Silence therefore meant "the box is dead" OR "the
    run failed", ambiguous exactly where the watchdog is the only thing that can
    speak. Firing unconditionally makes silence mean only "it never ran"."""
    assert "ExecStartPost=" not in DAILY, "ExecStartPost only fires on success"


def test_heartbeat_carries_the_exit_status():
    """The ping must say whether the day passed. Healthchecks.io reads a
    /<exit-status> suffix — 0 is a success, nonzero is a failure — so an
    unconditional ping does not launder a failed day into a healthy one."""
    line = next(l for l in DAILY.splitlines() if l.startswith("ExecStopPost="))
    assert "${HC_PING_URL}/${EXIT_STATUS}" in line, line


def test_no_restart_directive():
    """Invariant 4: default is HOLD. A failed day waits for a human. Checks
    directive lines only — the file's own comment reads `# NO Restart=`."""
    directives = [l for l in DAILY.splitlines() if l and not l.startswith("#")]
    assert not any(l.startswith("Restart=") for l in directives), directives


def test_the_nightly_unit_runs_its_four_legs_in_the_committed_order():
    """Type=oneshot runs ExecStart lines IN ORDER and stops at the first one
    that exits nonzero, so this order is behaviour, not formatting. Until
    2026-08-28 no test read this file at all.

      close_pnl, resolve_day  first: arithmetic, no LLM budget, and nothing is
                                     reflectable until resolutions exist
      reflect_day             third: PERISHABLE. reflect_day's _DUE_WHERE
                                     bounds on resolved_at within
                                     REFLECT_LOOKBACK_DAYS=7 and _AGED_OUT_WHERE
                                     alerts on rows that fell below the window
                                     and will NEVER be written. A reflection
                                     lost for seven nights is lost for good
      critic_g1                last: IMPERISHABLE and cheap to lose.
                                     state/specs.py:specs_awaiting_critique
                                     selects on `c.spec_id IS NULL` with NO
                                     date bound, so a skipped spec is
                                     re-selected every future night, forever.
                                     It also spends LLM budget and needs
                                     ANTHROPIC_API_KEY/SLACK_BOT_TOKEN — the
                                     property this file's own comment already
                                     gives as the reason reflect went last, now
                                     true of two legs

    Losing the window is not silent either way: OnFailure=fund-alert@%n.service
    fires on an overrun, a nonzero exit, or the guillotine.
    """
    assert [Path(cmd.split()[-1]).name for cmd in _exec_starts(PNL)] == [
        "close_pnl.py", "resolve_day.py", "reflect_day.py", "critic_g1.py"]


def test_the_nightly_unit_still_bounds_and_alerts_itself():
    assert "Type=oneshot" in PNL
    assert "OnFailure=fund-alert@%n.service" in PNL
    assert "TimeoutStartSec=30min" in PNL


def test_no_restart_directive_on_the_nightly_unit():
    """Invariant 4: a failed night waits for a human. Directive lines only —
    the file's comments discuss Restart= without setting it."""
    directives = [l for l in PNL.splitlines() if l and not l.startswith("#")]
    assert not any(l.startswith("Restart=") for l in directives), directives


def test_the_g1_leg_is_run_from_the_deployed_venv_like_its_siblings():
    """A bare `python3` would pick the host interpreter, not /opt/fund/.venv,
    and the seat would import nothing."""
    g1 = next(c for c in _exec_starts(PNL) if c.endswith("critic_g1.py"))
    assert g1.startswith("/opt/fund/.venv/bin/python3 ")
    assert g1.endswith("/opt/fund/scripts/critic_g1.py")


def test_the_critic_ship_gate_is_recorded_as_a_runnable_precondition():
    """scripts/critic_gate.py "decides whether the G1 gate ships, and the
    holdout it reads can only be spent once" — and until this lane it was
    invoked by NOTHING: not CI, not the Makefile, not systemd. An orphaned gate
    is how the stop-leg class of incident happens, in eval form. The CEO ruled
    it a recorded precondition for the Critic's first LIVE run: a make target
    plus an ops checklist line. It is deliberately NOT a `make test`
    prerequisite — it grades real recorded LLM trials."""
    assert "critic-gate:" in MAKEFILE
    assert "scripts/critic_gate.py" in MAKEFILE
    assert "make critic-gate" in OPS_README
    assert "critic_g1.py" in OPS_README        # the leg the units table lists


def test_the_g1_leg_has_a_by_hand_target_like_every_other_nightly_leg():
    """close-pnl, resolve and reflect each have one; an operator re-running a
    missed night by hand must not have to remember a path."""
    assert "critic-g1:" in MAKEFILE
    assert "scripts/critic_g1.py" in MAKEFILE

"""ops/*.service — properties whose loss changes trading behavior."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAILY = (ROOT / "ops" / "fund-daily.service").read_text()


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

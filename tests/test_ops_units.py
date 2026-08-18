"""ops/*.service — properties whose loss changes trading behavior."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAILY = (ROOT / "ops" / "fund-daily.service").read_text()


def test_heartbeat_cannot_fail_the_trading_day():
    """The `-` prefix is the whole safety argument. Without it a network blip on
    a monitoring ping marks the unit failed AFTER a successful day — converting
    an observability addition into a trading-day failure, and firing OnFailure
    for a day that actually completed."""
    line = next(l for l in DAILY.splitlines() if l.startswith("ExecStartPost="))
    assert line.startswith("ExecStartPost=-"), f"lost the fail-safe prefix: {line}"


def test_heartbeat_runs_after_the_day_not_before():
    """ExecStartPost, not ExecStartPre: a ping must mean the day COMPLETED. Pinging
    before the run would report liveness for a day that then failed."""
    assert "ExecStartPost=-/usr/bin/curl" in DAILY
    assert "ExecStartPre=/usr/bin/curl" not in DAILY


def test_no_restart_directive():
    """Invariant 4: default is HOLD. A failed day waits for a human. Checks
    directive lines only — the file's own comment reads `# NO Restart=`."""
    directives = [l for l in DAILY.splitlines() if l and not l.startswith("#")]
    assert not any(l.startswith("Restart=") for l in directives), directives

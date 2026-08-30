"""Offline tests for the hand-run spec-registration job's decision seams (#198).

main() IS DRIVEN HERE. It was not, for the two commits in which it did not
exist, and this docstring said so; it exists now. The seams under it are still
tested directly — register_and_log's counting and alerting, _guarded's
exit-code pass-through, the narrowed turn surface — and the final section
drives main() itself through every exit code it can return, the way
tests/test_critic_g1_job.py's "main()'s own exit codes" section (:621) drives
that job's. That section exists in the sibling because an earlier draft claimed
critic_g1 "returns 0 from every failure path from connect() onward" and, in the
file's own words, "It was not pinned." The exit code is THIS job's only report
— there is no OnFailure= unit behind it — so it is the last thing that may go
untested.

THE JOB IS A PRODUCER, which is why it looks different from its siblings. Every
other nightly job drains a queue and can compute how much of its OWN work is
outstanding; this one has none to read — there is no ideas table and no
strategies table in state/schema.sql — so "did the turn work?" is a
strategy_specs row COUNT either side, not a selector re-read. It does report
the DOWNSTREAM G1 queue either side, through the canonical
state.specs.specs_awaiting_critique selector, because that is the thing the
operator wants to know changed.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.tools.fund_server import handle_submit_strategy_spec
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state.db import connect
from tests.synthetic import spec_payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "register_spec.py"

# An arbitrary attended moment — this job is hand-run, not scheduled.
RUN_AT = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("register_spec", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


register_spec = _load()


def _register(conn, **overrides) -> str:
    """Register a spec exactly the way a real turn does — through the handler,
    from the seat that actually holds the cap. Never a raw INSERT: a fixture
    that can write a row the handler would refuse is a fixture that tests
    nothing."""
    result = handle_submit_strategy_spec(
        conn, seat="quant", args=spec_payload(**overrides), now_iso=iso(RUN_AT))
    assert result["ok"], result
    return result["spec_id"]


def _alert_texts(conn) -> list[str]:
    return [r["payload"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def _undrained(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


def test_a_turn_that_registers_a_spec_is_counted_and_drained(db):
    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: _register(db))

    assert counts == {"registered": 1, "failed": 0}
    assert _alert_texts(db) == []
    assert _undrained(db) == 0


def test_the_queue_depth_comes_from_the_canonical_selector(db):
    """The number the operator is told is the number the 16:35 critic_g1 leg
    will act on, or it is worse than saying nothing. Derived from
    state.specs.specs_awaiting_critique — a second copy of the predicate here
    is how the job and the tool come to disagree about what "pending" means,
    which is the reason scripts/critic_g1.py:233-238 gives for its own
    PENDING_REPORT_LIMIT.

    The selector's default is limit=1 (state/specs.py:48-49), so a DEPTH needs
    an explicit limit argument. Asserted against three, which the default
    would have reported as one."""
    from state.specs import specs_awaiting_critique

    assert register_spec.queue_depth(db) == 0
    for budget in (24, 25, 26):
        _register(db, search_budget=budget)

    assert register_spec.queue_depth(db) == 3
    assert len(specs_awaiting_critique(db)) == 1        # the default, for contrast


def test_a_turn_that_writes_nothing_alerts_and_registers_nothing(db):
    """The likeliest real failure, and nothing else catches it:
    run_day.make_turn's own run() catches every exception and returns
    normally, so a seat that never calls submit_strategy_spec — or calls it
    and gives up on {"ok": false} — raises nothing here either."""
    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    assert counts == {"registered": 0, "failed": 1}
    assert db.execute("SELECT count(*) c FROM strategy_specs"
                      ).fetchone()["c"] == 0
    assert any("register_spec_wrote_nothing" in t for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_the_wrote_nothing_alert_names_all_four_causes_and_the_queue(db):
    """FOUR causes, not three. charters/quant.md's Mission sanctions "this
    family is tapped out, I am not proposing" as a legitimate output, and this
    job counts a no-write turn as FAILED — so the seat doing the right thing
    and the seat going dark produce the identical alert. The operator can only
    tell them apart if the alert says so; an alert that lists three causes
    when there are four teaches the reader to distrust it.

    The queue depth is in the text for the same reason: "nothing registered"
    and "nothing registered, and there are already 2 specs waiting for G1" are
    different operator problems."""
    _register(db, search_budget=24)          # something already pending

    register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    text = next(t for t in _alert_texts(db)
                if "register_spec_wrote_nothing" in t)
    assert "declined" in text                 # the sanctioned fourth cause
    assert "never called" in text
    assert "refused" in text
    assert "duplicate" in text or "already on the books" in text
    assert "G1 queue 1 -> 1." in text


def test_a_queue_at_the_report_limit_renders_saturated_not_exact(db, monkeypatch):
    """scripts/register_spec.py:112-118 and queue_depth's own docstring both
    promise 'N+' once the canonical selector saturates at QUEUE_REPORT_LIMIT —
    but every render site used to interpolate the raw int, so a queue at or
    past the limit printed a number that reads as exact when it is a floor.

    queue_depth is monkeypatched rather than registering 50 real specs: the
    behaviour under test is what the render sites do with a saturated count,
    not whether the selector itself caps correctly (that is
    test_the_queue_depth_comes_from_the_canonical_selector's job)."""
    monkeypatch.setattr(register_spec, "queue_depth",
                        lambda conn: register_spec.QUEUE_REPORT_LIMIT)

    register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    text = next(t for t in _alert_texts(db)
                if "register_spec_wrote_nothing" in t)
    assert f"G1 queue {register_spec.QUEUE_REPORT_LIMIT}+ ->" \
        f" {register_spec.QUEUE_REPORT_LIMIT}+." in text


def test_a_re_registration_counts_as_wrote_nothing(db):
    """A duplicate is honest and it is also NOT a new spec: the content hash
    collides, INSERT OR IGNORE writes no row, and the outbox gets no event
    (agents/tools/fund_server.py:290-309). The count either side does not
    move, so the operator is told the run produced nothing — which is true,
    and is the only answer that does not overstate what happened."""
    _register(db)

    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: _register(db))

    assert counts == {"registered": 0, "failed": 1}
    assert any("register_spec_wrote_nothing" in t for t in _alert_texts(db))


def test_a_turn_that_raises_alerts_and_writes_nothing(db):
    """Defence in depth — not reachable through run_day.make_turn today, which
    swallows everything. Costs nothing to keep, and the alternative is a
    traceback out of a hand-run command with no Slack record."""
    def _boom():
        raise RuntimeError("sdk exploded")

    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), _boom)

    assert counts == {"registered": 0, "failed": 1}
    assert any("register_spec_turn_failed" in t and "sdk exploded" in t
               and "G1 queue" in t
               for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_the_job_never_registers_a_spec_of_its_own(db):
    """Invariant 7: structured data reaches state only through the seat's tool
    call. A job that could seed a spec on a failed turn would be manufacturing
    the fund's research record."""
    register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    assert db.execute("SELECT count(*) c FROM strategy_specs"
                      ).fetchone()["c"] == 0


def test_a_failure_inside_the_body_is_alerted_and_exits_nonzero(db):
    """No systemd unit stands behind this job (CEO ruling B1: no fifth leg),
    so unlike critic_g1 there is no OnFailure= to carry a failure out of the
    box. The drained alert and the nonzero exit are the whole report."""
    def _body():
        raise RuntimeError("db went away")

    rc = register_spec._guarded(db, FakeSlack(), SimClock(RUN_AT), _body)

    assert rc == 1
    assert any("register_spec_failed" in t and "db went away" in t
               for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_a_clean_run_returns_the_bodys_own_code(db):
    """A NONZERO sentinel, deliberately — tests/test_critic_g1_job.py:612-618's
    shape. `lambda: 0` asserted against 0 cannot tell pass-through from a
    swallow: it is the assertion that goes green under either implementation.
    It matters more here than there, because this job's _body returns 1 for a
    turn that wrote nothing (ruling 16) and a _guarded that quietly returned 0
    would report a failed registration as a success at the shell."""
    assert register_spec._guarded(db, FakeSlack(), SimClock(RUN_AT),
                                  lambda: 7) == 7
    assert _alert_texts(db) == []


def test_the_turn_surface_is_exactly_the_one_cap_the_seat_holds(db, tmp_path):
    """DRIVEN THROUGH build_seat_options, not compared against a constant.

    The earlier version of this test asserted
    `REGISTER_TOOLS == [f"mcp__fund__{c}" for c in sorted(SEAT_CAPS["quant"])]`
    — two constants, derived from each other, green on first run and green
    under any build_seat_options. What actually has to hold is that the
    narrowing SURVIVES the builder: build_seat_options refuses a per-turn list
    naming anything the seat's yaml does not already grant, and it refuses a
    glob, so a REGISTER_TOOLS the seat cannot carry fails at turn time — on a
    host, in front of a human waiting on a $0.75 turn.

    The standing guards are re-asserted on the narrowed options for the reason
    tests/test_exec_seat_tool_surface.py:343-353 gives: a narrowing must not
    become the place a second guard is quietly dropped."""
    from agents.seats import build_seat_options, load_seat_config
    from agents.tools.fund_server import SEAT_CAPS

    cfg = load_seat_config(register_spec.SEAT_CONFIG)
    opts = build_seat_options(cfg, tmp_path / "fund.sqlite",
                              SimClock(RUN_AT),
                              tools=register_spec.REGISTER_TOOLS)

    assert opts.tools == register_spec.REGISTER_TOOLS
    # ...and the surface really is the seat's whole cap set, so this narrowing
    # cannot silently drop a capability the seat needs.
    assert opts.tools == [f"mcp__fund__{cap}"
                          for cap in sorted(SEAT_CAPS["quant"])]
    assert "mcp__alpaca__*" not in opts.tools
    assert "mcp__fund__*" not in opts.tools

    # Every other guard, unchanged by the narrowing.
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    assert opts.hooks in (None, {})
    assert opts.setting_sources == []
    assert opts.permission_mode == "dontAsk"
    assert opts.max_budget_usd == cfg["max_budget_usd"]


# --- the turn factory -------------------------------------------------------

def test_the_turn_is_built_with_the_narrowed_surface(db, monkeypatch):
    """tests/test_critic_g1_job.py:453-470's shape. REGISTER_TOOLS is inert
    unless _make_run_turn actually passes it, and it is inert SILENTLY:
    without `tools=`, agents/seats.py's _turn_tools returns cfg["tools"]
    verbatim, so the turn would run on quant.yaml's ["mcp__fund__*"] glob and
    nothing would fail — the seat holds one cap today, so the widening only
    becomes visible the day it holds two."""
    seen = {}

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen.update(kwargs, seat=seat, run_date=run_date)
        return lambda: None

    monkeypatch.setattr(register_spec.run_day, "make_turn", _fake_make_turn)

    run_turn = register_spec._make_run_turn(
        "quant", {}, ":memory:", SimClock(RUN_AT), db, "2026-08-30")
    run_turn()

    assert seen["seat"] == "quant"
    assert seen["tools"] == register_spec.REGISTER_TOOLS


def test_the_turn_takes_no_argument(db, monkeypatch):
    """register_and_log drives a ZERO-argument callable — every sibling's takes
    a job dict because every sibling drains a queue and has a row to hand its
    turn. This job has no queue, so a factory that produced a one-argument
    run_turn would raise TypeError inside register_and_log's try, be counted
    as "the turn raised", and alert register_spec_turn_failed on a run where
    nothing was wrong with the seat."""
    monkeypatch.setattr(register_spec.run_day, "make_turn",
                        lambda *a, **k: (lambda: None))

    run_turn = register_spec._make_run_turn(
        "quant", {}, ":memory:", SimClock(RUN_AT), db, "2026-08-30")

    run_turn()          # no argument — a TypeError here IS the failure


def test_the_prompt_is_a_constant_that_carries_no_per_run_value(db,
                                                                monkeypatch):
    """🔏 OQ-1, ruled: the seat composes the spec itself and NOTHING per-run
    enters the prompt. The operative rule is not "no identifiers" but "nothing
    a replay cannot reconstruct from state" — scripts/reflect_day.py:366-367
    does embed job['frame'] in prompt prose, and tests/test_reflect_job.py:241
    pins that it does, which is fine there because the frame comes from a DB
    row a replay re-reads. A hypothesis typed at a shell exists nowhere but
    that shell, so this job opens no per-run channel into its prompt at all.
    Modelled on critic_g1.G1_PROMPT, which names no spec for the same reason.

    Two invocations under different clocks and different run dates, one
    identical prompt — critic_g1's test_the_prompt_carries_no_per_run_value
    (tests/test_critic_g1_job.py:506-524) uses two different queue heads for
    exactly this."""
    seen = []

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen.append(prompt)
        return lambda: None

    monkeypatch.setattr(register_spec.run_day, "make_turn", _fake_make_turn)

    register_spec._make_run_turn("quant", {}, ":memory:", SimClock(RUN_AT), db,
                                 "2026-08-30")()
    register_spec._make_run_turn("quant", {}, ":memory:",
                                 SimClock(datetime(2026, 9, 2, 14, 0,
                                                   tzinfo=timezone.utc)),
                                 db, "2026-09-02")()

    assert set(seen) == {register_spec.REGISTER_PROMPT}
    assert "2026-08-30" not in register_spec.REGISTER_PROMPT
    assert "2026-09-02" not in register_spec.REGISTER_PROMPT
    assert "{" not in register_spec.REGISTER_PROMPT      # no format slot


def test_the_prompt_sanctions_the_decline_its_own_alert_names(db):
    """register_and_log's register_spec_wrote_nothing alert tells the operator
    that "the seat correctly declined to propose" is one of FOUR causes and is
    not a fault, citing charters/quant.md's Mission. That is only true if the
    turn was allowed to decline. A prompt that said "end by calling
    submit_strategy_spec exactly once" and stopped would make the decline a
    disobeyed instruction rather than a sanctioned output, and the alert would
    be teaching the operator a cause that cannot occur."""
    assert "submit_strategy_spec" in register_spec.REGISTER_PROMPT
    assert "declin" in register_spec.REGISTER_PROMPT


# --- main()'s own exit codes ------------------------------------------------
#
# 0 means A SPEC WAS REGISTERED, and nothing else returns it. critic_g1's _body
# discards critique_and_log's counts and ends `return 0` whatever the night did
# (scripts/critic_g1.py:552-558); copying that shape here would make `make
# register-spec` exit 0 on a seat that never called the tool, which is the
# failure this job's module docstring promises it does not have.

class _FakeLock:
    """run_day.acquire_lock returns an OPEN FILE HANDLE, and main() closes the
    run_day one immediately — it asks whether a trading day is running, it does
    not claim the day's lock. A bare object() cannot stand in for that: it has
    no .close(), so the test would fail on the release rather than on the exit
    code under test."""

    def __init__(self, name: str):
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _fake_main_env(monkeypatch, db, tmp_path, *, held=()):
    """critic_g1's main()-test monkeypatch set (tests/test_critic_g1_job.py:
    629-646), one helper because six tests need it identically.

    `held` names the lock FILES some other process holds: acquire_lock returns
    None for those (contention is a None RETURN, not an exception —
    scripts/run_day.py:142-163) and a closeable handle for the rest. Per-path
    rather than one object shared by both calls, because the two refusals have
    to be exercised SEPARATELY: a single None trips run_day's check first every
    time, so the register_spec-lock test would pass without ever reaching the
    branch it is named for.

    Returns the (name, handle) pairs acquire_lock handed out, in order."""
    handed: list[tuple[str, _FakeLock | None]] = []

    def _acquire(path):
        handle = None if path.name in held else _FakeLock(path.name)
        handed.append((path.name, handle))
        return handle

    monkeypatch.setattr(register_spec.run_day, "paper_guard", lambda env: None)
    monkeypatch.setattr(register_spec.run_day, "require_env",
                        lambda names, env: {n: "x" for n in names}
                        | {"FUND_DB": str(tmp_path / "fund.sqlite")})
    monkeypatch.setattr(register_spec.run_day, "acquire_lock", _acquire)
    monkeypatch.setattr(register_spec, "connect", lambda p: db)
    monkeypatch.setattr(register_spec, "_build_slack",
                        lambda env, environ: FakeSlack())
    return handed


def test_main_exits_zero_only_when_a_spec_was_registered(db, tmp_path,
                                                         monkeypatch):
    """The whole exit-code contract in one assertion: 0 MEANS a spec exists
    that did not exist before."""
    _fake_main_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(register_spec, "_make_run_turn",
                        lambda *a, **k: (lambda: _register(db)))

    assert register_spec.main([]) == 0
    assert _alert_texts(db) == []
    assert db.execute("SELECT count(*) c FROM strategy_specs"
                      ).fetchone()["c"] == 1


def test_main_exits_one_when_the_turn_wrote_nothing(db, tmp_path, monkeypatch):
    """The bug the contract exists to prevent, driven end to end. No exception
    is raised anywhere — run_day.make_turn's run() swallows everything — so
    ONLY the count either side of the turn can produce this 1."""
    _fake_main_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(register_spec, "_make_run_turn",
                        lambda *a, **k: (lambda: None))

    assert register_spec.main([]) == 1
    assert any("register_spec_wrote_nothing" in t for t in _alert_texts(db))


def test_main_exits_one_when_the_guarded_body_fails(db, tmp_path, monkeypatch):
    """The end-to-end code, not just _guarded's. Everything main() builds is
    faked except the decision under test: what integer reaches sys.exit."""
    _fake_main_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(register_spec, "register_and_log",
                        lambda *a, **k: (_ for _ in ()).throw(
                            sqlite3.OperationalError("database is locked")))

    assert register_spec.main([]) == 1
    assert "register_spec_failed" in _alert_texts(db)[0]


def test_main_exits_two_when_another_register_spec_holds_the_lock(
        db, tmp_path, monkeypatch):
    """NOT 0 and NOT 1. critic_g1 returns 0 here because contention on a
    systemd leg resolves itself and must not page. This one is typed by a
    human waiting to know whether the fund has a new spec, and 0 would tell
    them it has. Not 1 either, because nothing failed.

    run_day's lock is FREE in this test, so this really is the second check
    refusing — and its handle must have been released, or merely asking the
    question would hold a trading day out of its own window."""
    handed = _fake_main_env(monkeypatch, db, tmp_path,
                            held=(register_spec.LOCK_NAME,))
    ran = []
    monkeypatch.setattr(register_spec, "connect", lambda p: ran.append(p) or db)

    assert register_spec.main([]) == 2
    assert [n for n, _ in handed] == [register_spec.run_day.LOCK_NAME,
                                      register_spec.LOCK_NAME]
    assert handed[0][1].closed is True       # run_day's lock, released
    assert ran == []                         # it never even opened the DB


def test_main_exits_two_when_run_day_holds_its_lock(db, tmp_path, monkeypatch):
    """The cross-job refusal, and it is not tidiness: slackkit/outbox.py:118-144
    drain() SELECTs every unposted row and then marks and commits one row at a
    time, so two concurrent drainers each fetch the same set and post it twice.
    Invariant 6 routes outbound delivery through the outbox precisely so a
    crash or retry can neither lose nor duplicate a post.

    Same exit code as the other lock, because the operator's next action is
    identical — but this check runs FIRST and this job's own lock is never even
    requested, so a refusal here cannot leave a register_spec.lock stamped by a
    run that did nothing."""
    handed = _fake_main_env(monkeypatch, db, tmp_path,
                            held=(register_spec.run_day.LOCK_NAME,))
    ran = []
    monkeypatch.setattr(register_spec, "connect", lambda p: ran.append(p) or db)

    assert register_spec.main([]) == 2
    assert [n for n, _ in handed] == [register_spec.run_day.LOCK_NAME]
    assert ran == []


def test_main_releases_run_days_lock_before_running_the_turn(db, tmp_path,
                                                            monkeypatch):
    """main() ASKS whether a trading day is running; it does not claim the
    day's lock for the length of its own run. Holding it would let a hand-run
    at 16:30 keep the 16:35 legs out of their window — the exact hazard
    scripts/register_spec.py's LOCK_NAME comment says a separate lock exists to
    avoid.

    This is a REDUCTION of the double-drain hazard, not an elimination: run_day
    can still start between the check and the turn. Pinned so nobody "fixes"
    the release and closes that race by re-introducing the worse one."""
    handed = _fake_main_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(register_spec, "_make_run_turn",
                        lambda *a, **k: (lambda: _register(db)))

    assert register_spec.main([]) == 0
    day_lock = next(h for n, h in handed
                    if n == register_spec.run_day.LOCK_NAME)
    own_lock = next(h for n, h in handed if n == register_spec.LOCK_NAME)
    assert day_lock.closed is True
    assert own_lock.closed is False          # ours outlives the run


def test_a_bad_seat_config_fails_loudly_rather_than_passing_silently(
        db, tmp_path, monkeypatch):
    """load_seat_config reads agents/config/quant.yaml. It is INSIDE _guarded,
    so it alerts with a code and exits 1 — tests/test_critic_g1_job.py:681-707's
    shape, and the same defect that test was written for."""
    _fake_main_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(register_spec, "load_seat_config",
                        lambda p: (_ for _ in ()).throw(
                            FileNotFoundError("agents/config/quant.yaml")))

    assert register_spec.main([]) == 1
    assert "register_spec_failed" in _alert_texts(db)[0]
    assert "FileNotFoundError" in _alert_texts(db)[0]

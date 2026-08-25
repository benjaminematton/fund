"""The filer's dedupe, against the REAL alert texts from 2026-08-21 and
2026-08-24. Synthetic text would not prove the key survives interpolation,
which is the entire defect this script exists to fix."""
import importlib.util, json, sqlite3, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "file_alert_issues.py"

NVDA_0821 = ("NVDA 80 was ticketed with a stop at 215.0 but the broker covers"
             " only 40 of 80 shares — the position is exposed and no code path"
             " will protect it; place or restore a stop manually")
NVDA_0824 = ("NVDA 40 was ticketed with a stop at 215.0 but the broker has NO"
             " live protective order — the position is exposed and no code path"
             " will protect it; place or restore a stop manually")


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("file_alert_issues", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: Filing is a frozen dataclass under `from __future__
    # import annotations`, so Python 3.14's dataclass field resolution looks
    # the module up in sys.modules by name. Without this line the module
    # loads but every dataclass() call inside it raises AttributeError on
    # sys.modules.get(cls.__module__).__dict__ — a loader gap the brief's
    # audit_day-style _load() never hit because audit_day.py has no dataclass.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeTracker:
    """Records every lookup; answers from a fixed open set."""
    def __init__(self, open_issues=None):
        self.open_issues = open_issues or {}      # labels tuple -> issue number
        self.lookups = []

    def open_issue(self, labels):
        self.lookups.append(labels)
        return self.open_issues.get(tuple(labels))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.sqlite"


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT,"
                 " payload TEXT, created_at TEXT, posted_at TEXT)")
    return conn


def _alert(conn, created_at, **payload):
    conn.execute("INSERT INTO events (kind, payload, created_at) VALUES"
                 " ('alert', ?, ?)", (json.dumps(payload), created_at))
    conn.commit()


def test_the_same_condition_with_different_text_files_once(db):
    """THE defect. Both texts are verbatim from the production DBs."""
    _alert(db, "2026-08-21T13:38:02+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0821)
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    filings, _ = _load().plan_filings(db, "2026-08-21", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:unprotected_position", "ticker:NVDA")
    assert NVDA_0821 in filings[0].body and NVDA_0824 in filings[0].body


def test_negative_control_two_codes_file_two_issues(db):
    """If this passed with one issue, the test above would prove nothing."""
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    _alert(db, "2026-08-24T13:37:54+00:00", code="accounting_shortfall",
           ticker="NVDA", text="NVDA: recorded 80, broker holds 40")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert [f.action for f in filings] == ["create", "create"]


def test_pm_timeout_on_three_tickers_is_one_issue(db):
    for t in ("AAPL", "MSFT", "NVDA"):
        _alert(db, "2026-08-18T13:36:11+00:00", code="pm_timeout",
               text=f"pm_timeout {t} — defaulted to hold")
    filings, _ = _load().plan_filings(db, "2026-08-18", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:pm_timeout",)


def test_two_tickers_of_one_code_are_two_issues(db):
    for t in ("NVDA", "MSFT"):
        _alert(db, "2026-08-24T13:00:00+00:00", code="unprotected_position",
               ticker=t, text=f"{t} exposed")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert sorted(f.labels[1] for f in filings) == ["ticker:MSFT", "ticker:NVDA"]


def test_an_already_open_issue_files_nothing(db):
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    tracker = FakeTracker({("alert:unprotected_position", "ticker:NVDA"): 41})
    filings, _ = _load().plan_filings(db, "2026-08-24", tracker)
    assert [f.action for f in filings] == ["skip"]
    assert filings[0].issue == 41


def test_a_closed_issue_does_not_suppress_a_recurrence(db):
    """Only OPEN issues match, so a recurrence gets a fresh issue."""
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker({}))
    assert [f.action for f in filings] == ["create"]


def test_a_clearing_alert_comments_and_never_closes(db):
    _alert(db, "2026-08-25T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    tracker = FakeTracker({("alert:accounting_shortfall", "ticker:NVDA"): 42})
    filings, _ = _load().plan_filings(db, "2026-08-25", tracker)
    assert [f.action for f in filings] == ["comment"]
    assert filings[0].issue == 42
    assert all(f.action != "close" for f in filings)


def test_a_clearing_alert_with_no_open_issue_does_nothing(db):
    _alert(db, "2026-08-25T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    filings, _ = _load().plan_filings(db, "2026-08-25", FakeTracker())
    assert filings == []


def test_a_condition_that_fired_and_cleared_in_one_window_still_files(db):
    """The symptom cleared; the code defect did not."""
    _alert(db, "2026-08-24T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", text="NVDA: recorded 80, broker holds 40")
    _alert(db, "2026-08-24T15:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert [f.action for f in filings] == ["create"]


def test_the_audit_rollup_never_files(db):
    _alert(db, "2026-08-24T13:38:01+00:00", code="audit_failed",
           audit_report=True, text="audit 2026-08-24 FAILED: alert events raised: 2")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert filings == []


def test_a_payload_with_no_code_is_reported_not_guessed(db):
    _alert(db, "2026-08-24T13:00:00+00:00", text="something old and codeless")
    _alert(db, "2026-08-24T13:01:00+00:00", code="pm_timeout", text="pm_timeout AAPL")
    filings, malformed = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert len(malformed) == 1 and "codeless" in malformed[0]
    assert [f.action for f in filings] == ["create"]      # the rest still process


def test_alerts_before_the_since_date_are_ignored(db):
    _alert(db, "2026-08-20T13:00:00+00:00", code="pm_timeout", text="old")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert filings == []


def test_a_changing_order_id_does_not_file_a_new_issue_each_day(db):
    """ticket_open_after_exec embeds a fresh order id every run. Keyed on the
    id it would file one issue per trading day, forever — the single worst
    outcome available here."""
    _alert(db, "2026-08-20T13:38:23+00:00", code="ticket_open_after_exec",
           text="ticket c0a9ae97 open after exec turn — no order")
    _alert(db, "2026-08-24T13:38:23+00:00", code="ticket_open_after_exec",
           text="ticket 7f31b204 open after exec turn — no order")
    filings, _ = _load().plan_filings(db, "2026-08-20", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:ticket_open_after_exec",)

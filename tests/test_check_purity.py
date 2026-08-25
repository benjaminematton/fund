"""The purity lint's own negative controls (issue #43).

`scripts/check_purity.py` enforces CLAUDE.md invariant 3, but it matches on the
literal source text of a call's base and only ever looks at `ast.Import` /
`ast.ImportFrom` nodes. A file can therefore import `anthropic` and read the
wall clock while the lint reports `clean`. Each entry in EVASION_CASES below is
a snippet that the lint must flag and today does not.

The two tables that follow are the other half: DETECTED_CASES are the
violations the lint already catches and must keep catching, and CLEAN_CASES are
correct code — above all `self._clock.now()`, the injected-Clock pattern the
invariant exists to *require*. A rewrite that resolves bindings instead of
source text will over-fire without them.

SLACKKIT_INIT_VIOLATIONS covers the third boundary: `slackkit` goes in neither
list, and instead `slackkit/__init__.py` must stay import-free — the mechanism
that lets orchestrator import `slackkit.outbox` without pulling in `slack_sdk`.

Zero dependencies: plain no-arg `def test_*` functions and stdlib `tempfile`,
so it runs under pytest (what CI and `make test` invoke) and under the
`tests/run_tests.py` fallback runner alike. Scratch trees are built under
`tempfile`; nothing is ever written into the real packages.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_purity.py"


def _load():
    """A fresh copy of the lint. Never shared — _lint_exit_code rebinds ROOT."""
    spec = importlib.util.spec_from_file_location("check_purity_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _errors_for(source: str) -> list[str]:
    """check_file() against a scratch file holding `source`."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.py"
        path.write_text(dedent(source))
        return _load().check_file(path)


def _lint_exit_code_for_tree(files: dict[str, str]) -> int:
    """main() against a scratch ROOT built from {relative path: source}."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, source in files.items():
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(source))
        lint = _load()
        lint.ROOT = Path(tmp)
        return lint.main()


def _lint_exit_code(pkg: str, source: str) -> int:
    """main() against a scratch ROOT whose only content is `pkg/sample.py`."""
    return _lint_exit_code_for_tree({f"{pkg}/sample.py": source})


# --- (case name, source) tables -------------------------------------------

# Dynamic imports: an ast.Call, never an ast.Import, so the import branch
# never sees them.
DYNAMIC_IMPORT_CASES = [
    ("importlib.import_module with a concatenated name", """
        import importlib

        def sdk():
            return importlib.import_module("claude" + "_agent_sdk")
    """),
    ("__import__ builtin", """
        def sdk():
            return __import__("anthropic")
    """),
]

# Aliased bindings: FORBIDDEN_CALLS keys on the base's literal source text
# (check_purity.py:47-48), so renaming the binding walks straight past it.
ALIASED_BINDING_CASES = [
    ("import time as _t, then _t.sleep(1)", """
        import time as _t

        def pause():
            _t.sleep(1)
    """),
    ("from datetime import datetime as _dt, then _dt.now()", """
        from datetime import datetime as _dt

        def stamp():
            return _dt.now()
    """),
    ("from time import sleep, then a bare sleep(1)", """
        from time import sleep

        def pause():
            sleep(1)
    """),
]

# Clock/blocking calls absent from FORBIDDEN_CALLS entirely.
UNLISTED_CLOCK_CALL_CASES = [
    ("time.time()", """
        import time

        def stamp():
            return time.time()
    """),
    ("time.monotonic()", """
        import time

        def stamp():
            return time.monotonic()
    """),
    ("time.perf_counter()", """
        import time

        def stamp():
            return time.perf_counter()
    """),
    ("asyncio.sleep()", """
        import asyncio

        async def pause():
            await asyncio.sleep(0.1)
    """),
]

EVASION_CASES = DYNAMIC_IMPORT_CASES + ALIASED_BINDING_CASES + UNLISTED_CLOCK_CALL_CASES

# Already caught today. These must survive the rewrite.
DETECTED_CASES = [
    ("plain import anthropic", """
        import anthropic
    """),
    ("from claude_agent_sdk import query", """
        from claude_agent_sdk import query
    """),
    ("import agents.runtime", """
        import agents.runtime
    """),
    ("plain datetime.now()", """
        from datetime import datetime

        def stamp():
            return datetime.now()
    """),
    ("plain time.sleep(1)", """
        import time

        def pause():
            time.sleep(1)
    """),
]

# Correct code. Flagging any of these would make the lint forbid the very
# pattern invariant 3 mandates.
CLEAN_CASES = [
    ("self._clock.now() — the injected Clock", """
        class Stage:
            def __init__(self, clock):
                self._clock = clock

            def stamp(self):
                return self._clock.now()
    """),
    ("clock.now() on an injected parameter", """
        def stamp(clock):
            return clock.now()
    """),
    ("parameter named `time` shadowing the module", """
        def elapsed(time):
            # `time` is a Timer passed in by the caller, not the stdlib module.
            return time.perf_counter()
    """),
    ("local variable named `datetime` shadowing the class", """
        def label(rows):
            datetime = Formatter(rows)
            return datetime.now()
    """),
    ("from datetime import timedelta (market/source_alpaca.py:6)", """
        from datetime import timedelta

        def window():
            return timedelta(days=1)
    """),
    (".now() on an unrelated object", """
        def price(feed):
            # The feed's own API; nothing to do with the wall clock.
            return feed.now()
    """),
]

CLEAN_FILE = """
    from __future__ import annotations


    def net(gross: float, fees: float) -> float:
        return gross - fees
"""

# --- slackkit: the empty __init__.py is a load-bearing boundary -------------
#
# `slackkit` belongs in NEITHER PURE_PACKAGES nor FORBIDDEN_IMPORTS — both
# redden legitimate code. What makes `orchestrator/daily.py:24`'s
# `from slackkit.outbox import ...` safe is that `slackkit/__init__.py` is
# empty, so importing a submodule pulls in no `slack_sdk`. `slackkit/real.py`
# says it "must stay empty"; today nothing enforces that. These are the
# enforcement. Trees are {relative path: source}, run through main().

SLACKKIT_INIT_VIOLATIONS = [
    ("__init__.py re-exports the real port", {
        "slackkit/__init__.py": "from .real import RealSlack\n",
        "slackkit/real.py": "import slack_sdk\n",
    }),
    ("__init__.py imports slack_sdk directly", {
        "slackkit/__init__.py": "import slack_sdk\n",
    }),
    ("__init__.py imports anything at all, even stdlib", {
        # Not about *what* is imported: an __init__ that executes imports is a
        # package that can grow one, which is how the boundary erodes.
        "slackkit/__init__.py": "import os\n",
    }),
]

SLACKKIT_CLEAN_TREES = [
    ("empty __init__.py, real.py holds the SDK", {
        "slackkit/__init__.py": "",
        "slackkit/real.py": """
            import slack_sdk


            class RealSlack:
                def __init__(self, token):
                    self._client = slack_sdk.WebClient(token=token)
        """,
    }),
    ("empty __init__.py, outbox.py is plain stdlib", {
        "slackkit/__init__.py": "",
        "slackkit/outbox.py": """
            import json


            def append_event(path, event):
                return json.dumps(event)
        """,
    }),
]


# --- assertions ------------------------------------------------------------

def _assert_all_flagged(cases, label):
    missed = [name for name, src in cases if not _errors_for(src)]
    assert not missed, (
        f"{label}: purity lint reported clean for {len(missed)} of {len(cases)} "
        f"case(s) it must flag: " + "; ".join(missed))


def _assert_trees_rejected(cases, label):
    passed = [name for name, tree in cases if _lint_exit_code_for_tree(tree) == 0]
    assert not passed, (
        f"{label}: purity lint exited 0 for {len(passed)} of {len(cases)} "
        f"tree(s) it must reject: " + "; ".join(passed))


def _assert_trees_accepted(cases, label):
    rejected = [name for name, tree in cases if _lint_exit_code_for_tree(tree) != 0]
    assert not rejected, (
        f"{label}: purity lint exited non-zero for {len(rejected)} of "
        f"{len(cases)} legitimate tree(s): " + "; ".join(rejected))


def _assert_none_flagged(cases, label):
    fired = [(name, errs) for name, src in cases if (errs := _errors_for(src))]
    assert not fired, (
        f"{label}: purity lint false-positived on {len(fired)} of {len(cases)} "
        f"case(s): " + "; ".join(f"{name} -> {errs}" for name, errs in fired))


# --- tests -----------------------------------------------------------------

def test_dynamic_imports_are_flagged():
    _assert_all_flagged(DYNAMIC_IMPORT_CASES, "dynamic import")


def test_aliased_bindings_are_flagged():
    _assert_all_flagged(ALIASED_BINDING_CASES, "aliased binding")


def test_unlisted_clock_calls_are_flagged():
    _assert_all_flagged(UNLISTED_CLOCK_CALL_CASES, "unlisted clock call")


def test_known_violations_stay_flagged():
    _assert_all_flagged(DETECTED_CASES, "regression")


def test_correct_patterns_are_not_flagged():
    _assert_none_flagged(CLEAN_CASES, "false positive")


def test_clean_file_yields_no_errors():
    errors = _errors_for(CLEAN_FILE)
    assert errors == [], f"clean file produced errors: {errors}"


def test_an_evasion_exits_nonzero():
    """The issue's headline repro: gate/evade.py, `clean`, exit 0."""
    for name, source in EVASION_CASES:
        code = _lint_exit_code("gate", source)
        assert code != 0, f"gate/ evasion '{name}' left the lint exit 0"


def test_market_is_linted():
    """market/ computes the gate's own inputs; it is currently unlinted."""
    assert (ROOT / "market").is_dir(), "market/ is gone — retarget this test"
    code = _lint_exit_code("market", "import anthropic\n")
    assert code != 0, "a forbidden import in market/ left the lint exit 0"


def test_slackkit_init_must_stay_import_free():
    _assert_trees_rejected(SLACKKIT_INIT_VIOLATIONS, "slackkit/__init__.py")


def test_slackkit_submodules_may_hold_the_sdk():
    """The boundary, not a ban: real.py is *supposed* to import slack_sdk.
    A check that lints all of slackkit/ instead of just __init__.py fails
    here."""
    _assert_trees_accepted(SLACKKIT_CLEAN_TREES, "slackkit submodule")


def test_the_real_slackkit_init_is_empty():
    """The premise of the two tests above, verified rather than assumed."""
    init = ROOT / "slackkit" / "__init__.py"
    assert init.is_file(), f"{init} is gone — retarget these tests"
    assert init.read_text().strip() == "", (
        f"{init} is no longer empty, which breaks the boundary "
        f"slackkit/real.py:1-3 documents:\n{init.read_text()}")


def test_the_real_tree_stays_clean():
    """No fix may turn the repo red — every pure package is clean today."""
    proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"purity lint failed on the real tree:\n{proc.stdout}{proc.stderr}")

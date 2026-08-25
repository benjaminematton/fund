"""The purity lint's own negative controls (issue #43).

`scripts/check_purity.py` enforces CLAUDE.md invariant 3. A lint whose negative
control also passes is not a lint, so every table here is one half of a pair:
something the lint must catch, and the legitimate code next door it must not.

Round one covered the source-spelling evasions (aliased bindings, dynamic
imports, unlisted clock spellings, `market/`). Round two adds four groups:

* MASTER_REGRESSION_CASES — violations master flagged that the binding rewrite
  stopped flagging. A lint that gets looser is worse than one that never moved.
* COLLISION_CASES — an import binding colliding with a rebinding *in the same
  scope* is a violation, not a shadow. Popping the name on any rebinding makes
  `import time` + `if False: time = None` a working, silent bypass.
* CALLABLE_CAPTURE_CASES — only a call on an attribute chain was matched, so
  `_sleep = time.sleep` defeated the check while reading as careful seam code.
* SLACKKIT_* — option (d): lint `slackkit/` as a pure package with `real.py`
  excluded, and forbid pure packages from importing `slackkit.real`. That last
  rule needs *dotted-prefix* matching: `FORBIDDEN_IMPORTS` is compared against
  `name.split(".")[0]`, so the string "slackkit.real" added to it would match
  nothing, forever. The ablation below is what proves the matcher changed.

CLEAN_CASES is the load-bearing half — above all `self._clock.now()`, the
injected-Clock pattern the invariant exists to *require*. A lint that flags
correct code is the failure mode that gets a lint deleted.

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

# --- round two -------------------------------------------------------------

# Violations MASTER flags that the binding rewrite stopped flagging. Each one
# is a place where the scope model pops a name that Python does not actually
# rebind, or fails to bind one it does.
MASTER_REGRESSION_CASES = [
    ("class-body binding leaking into a method scope", """
        from datetime import date


        class Row:
            # An ordinary pydantic/dataclass field. Class scope is NOT visible
            # inside methods, so `date` below is still the stdlib import —
            # state/models.py:8 already does `from datetime import date`.
            date: date = None

            def stamp(self):
                return date.today()
    """),
    ("star-import binds the literal name '*'", """
        from datetime import *


        def stamp():
            return datetime.now()
    """),
    ("comprehension target folded into the parent scope", """
        import time

        # A comprehension target is invisible outside the comprehension, so
        # folding it into module scope buys nothing and silences the file.
        _UNUSED = [None for time in ()]


        def pause():
            time.sleep(1)
    """),
]

# An import binding rebound in the SAME scope is incoherent code, not a shadow.
# The current pop is position- and condition-independent, so each of these
# silences the whole scope while looking like a no-op.
COLLISION_CASES = [
    ("`if False: time = None` at module scope", """
        import time

        if False:
            time = None


        def pause():
            time.sleep(1)
    """),
    ("`time = time` self-assignment on the last line", """
        import time


        def pause():
            time.sleep(1)


        time = time
    """),
    ("`except Exception as time:` at module scope", """
        import time

        try:
            pass
        except Exception as time:
            pass


        def pause():
            time.sleep(1)
    """),
    ("`with open(...) as time:` at module scope", """
        import time

        with open("/dev/null") as time:
            pass


        def pause():
            time.sleep(1)
    """),
    ("walrus `if (time := None):` at module scope", """
        import time

        if (time := None):
            pass


        def pause():
            time.sleep(1)
    """),
    ("same-scope rebind placed after the use", """
        import time

        time.sleep(1)
        time = None
    """),
]

# Same clock, one spelling away from the listed one.
CLOCK_SPELLING_CASES = [
    ("datetime.today() — date.today is listed, datetime.today is not", """
        from datetime import datetime


        def stamp():
            return datetime.today()
    """),
    ("time.time_ns()", """
        import time


        def stamp():
            return time.time_ns()
    """),
    ("time.monotonic_ns()", """
        import time


        def stamp():
            return time.monotonic_ns()
    """),
    ("time.perf_counter_ns()", """
        import time


        def stamp():
            return time.perf_counter_ns()
    """),
    ("time.process_time()", """
        import time


        def stamp():
            return time.process_time()
    """),
    ("time.localtime()", """
        import time


        def stamp():
            return time.localtime()
    """),
    ("time.gmtime()", """
        import time


        def stamp():
            return time.gmtime()
    """),
]

# Rebindings of the dynamic-import builtins. Master misses these too — they are
# still-open evasions, not regressions.
DYNAMIC_IMPORT_SPELLING_CASES = [
    ("from builtins import __import__", """
        from builtins import __import__


        def sdk():
            return __import__("anthropic")
    """),
    ("_imp = __import__, then _imp(...)", """
        _imp = __import__


        def sdk():
            return _imp("anthropic")
    """),
]

# Capturing the callable instead of calling it. These read as conscientious
# Clock-protocol code while being exactly the wall-clock dependency invariant 3
# bans — the reviewers rated this the most realistic gap of the set.
CALLABLE_CAPTURE_CASES = [
    ("module-level seam alias `_sleep = time.sleep`", """
        import time

        _sleep = time.sleep
    """),
    ("dispatch table holding datetime.utcnow", """
        from datetime import datetime

        _FIELDS = {"ts": datetime.utcnow}
    """),
    ("adapter class whose attributes are the clock functions", """
        import time
        from datetime import datetime


        class SystemClock:
            now = datetime.now
            sleep = time.sleep
    """),
    ("default-argument fallback `_fallback=time.monotonic`", """
        import time


        class Runner:
            def __init__(self, clock=None, _fallback=time.monotonic):
                self._clock = clock
                self._fallback = _fallback
    """),
    ("intermediate module alias `_clock_mod = time`", """
        import time

        _clock_mod = time


        def pause():
            _clock_mod.sleep(1)
    """),
    ("getattr(time, \"sleep\")() — house style at market/source_alpaca.py:33", """
        import time


        def pause():
            getattr(time, "sleep")()
    """),
]

EVASION_CASES = (DYNAMIC_IMPORT_CASES + ALIASED_BINDING_CASES
                 + UNLISTED_CLOCK_CALL_CASES + MASTER_REGRESSION_CASES
                 + COLLISION_CASES + CLOCK_SPELLING_CASES
                 + DYNAMIC_IMPORT_SPELLING_CASES + CALLABLE_CAPTURE_CASES)

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
    # --- round two ---
    # The hinge of the collision rule: a shadow is legitimate precisely when
    # the name is bound by something other than an import. The two cases above
    # (`def elapsed(time)`, `datetime = Formatter(rows)`) are green because
    # their files never import the shadowed name at all. This one is green for
    # the harder reason — the file DOES import `time`, and the parameter is a
    # different scope, which Python resolves to the parameter. Delete the
    # scope/shadow logic and this case goes red, which is the point of it.
    ("module-level `import time` + a parameter named `time` (different scope)", """
        import time


        def elapsed(time):
            # The caller's Timer. Python binds this to the parameter inside
            # this scope; the module-level import is not visible here.
            return time.perf_counter()
    """),
    ("match/case capture pattern is a binding", """
        import time


        def handle(event):
            match event:
                case {"clock": time}:
                    # `time` is the captured dict value, not the module.
                    return time.monotonic()
            return None
    """),
    # Comprehension scoping, guarded from the other side. The violation table
    # has `[None for time in ()]` silencing a module, which the collision rule
    # also happens to catch — so these are what actually pin the comprehension
    # branch of _SCOPE_NODES. A comprehension target is scoped to the
    # comprehension in Python 3: it rebinds nothing outside, so reusing an
    # imported name is legal and must not read as a same-scope collision.
    # The name is genuinely imported in the file — that is the hinge.
    ("comprehension target reusing an imported name — list", """
        import json

        _KEYS = [json for json in ("a", "b")]
    """),
    ("comprehension target reusing an imported name — set", """
        import json

        _KEYS = {json for json in ("a", "b")}
    """),
    ("comprehension target reusing an imported name — dict", """
        import json

        _KEYS = {json: 1 for json in ("a", "b")}
    """),
    ("comprehension target reusing an imported name — generator", """
        import json

        _KEYS = tuple(json for json in ("a", "b"))
    """),
    # Same shape as the comprehension pair above, for two more blocks that the
    # violation tables were catching by a different route and so left unpinned.
    ("`except ... as time` binds the caught exception, not the module", """
        import time


        def deadline(fn):
            try:
                return fn()
            except TimeoutError as time:
                # The caught exception carries its own .time; the module is
                # shadowed inside this handler. A nested scope, so not a
                # collision — and not the stdlib clock either.
                return time.time
    """),
    ("a name bound to a local must not fall through to a star-import", """
        from time import *


        def run(sleep):
            # `sleep` is the injected callable, not the one the star import
            # would have supplied. Resolving to nothing must mean nothing —
            # never "guess the star module".
            sleep(1)
    """),
]

CLEAN_FILE = """
    from __future__ import annotations


    def net(gross: float, fees: float) -> float:
        return gross - fees
"""

# --- slackkit, option (d) ---------------------------------------------------
#
# Lint `slackkit/` as a pure package with `real.py` EXCLUDED, and forbid pure
# packages from importing `slackkit.real`. Keep the import-free `__init__.py`
# check. Trees are {relative path: source}, run through main().
#
# The half that is easy to get wrong: `FORBIDDEN_IMPORTS` is matched on the
# ROOT module only — `alias.name.split(".")[0]` and
# `(node.module or "").split(".")[0]`. Putting the string "slackkit.real" in
# that tuple can never match, because `root` is "slackkit". It would sit in the
# source looking like a rule and catch nothing, forever. So what is required is
# dotted-prefix semantics, and SLACKKIT_ABLATION_CLEAN is what proves the
# matcher actually moved rather than the constant.

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

SLACKKIT_REAL_VIOLATIONS = [
    ("pure package does `from slackkit.real import RealSlack`", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.real import RealSlack\n",
    }),
    ("pure package does `import slackkit.real`", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "import slackkit.real\n",
    }),
    ("dotted prefix reaches subpackages: slackkit.real.helpers", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.real.helpers import retry\n",
    }),
    ("`from slackkit import real` — the module is the package, not the name", {
        # The other three spellings all put "slackkit.real" in `node.module`,
        # so they never reach the per-alias check. This one is the natural way
        # to write it and is the only case that exercises that branch.
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit import real\n",
    }),
    ("wall clock parked in slackkit/outbox.py", {
        # The better half of (d): outbox/render/fake/port are orchestrator's
        # live dependency and are completely unlinted today, so a clock read
        # here breaks sim-day determinism silently.
        "slackkit/__init__.py": "",
        "slackkit/outbox.py": """
            import time


            def append_event(path, event):
                return time.time()
        """,
    }),
    ("slack_sdk imported by slackkit/outbox.py", {
        "slackkit/__init__.py": "",
        "slackkit/outbox.py": "import slack_sdk\n",
    }),
]

# The ablation. Without it, "the matcher was changed" is only the word of
# whoever changed it — which is exactly what `PURITY LINT: clean, exit 0` said
# the last two times.
SLACKKIT_ABLATION_CLEAN = [
    ("pure package does `from slackkit.outbox import append_alert`", {
        # Real code: orchestrator/{daily,preconditions,protection,reconcile}.py.
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.outbox import append_alert\n",
    }),
    ("slackkit/real.py holds the SDK — excluded from the pure scan", {
        "slackkit/__init__.py": "",
        "slackkit/real.py": """
            import slack_sdk


            class RealSlack:
                def __init__(self, token):
                    self._client = slack_sdk.WebClient(token=token)
        """,
    }),
    ("`slackkit.realtime` is not `slackkit.real` — dotted, not string, prefix", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.realtime import stream\n",
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


def test_master_regressions_stay_flagged():
    """Master flagged all three. A binding rewrite that loses them has made
    the lint looser than the thing it replaced."""
    _assert_all_flagged(MASTER_REGRESSION_CASES, "regression vs master")


def test_import_colliding_with_a_rebinding_is_flagged():
    """Popping a name on any rebinding, anywhere in the scope, regardless of
    whether the branch can execute, is a one-line silencer for a whole file."""
    _assert_all_flagged(COLLISION_CASES, "same-scope collision")


def test_clock_spellings_are_flagged():
    _assert_all_flagged(CLOCK_SPELLING_CASES, "clock spelling")


def test_dynamic_import_spellings_are_flagged():
    _assert_all_flagged(DYNAMIC_IMPORT_SPELLING_CASES, "dynamic import spelling")


def test_captured_callables_are_flagged():
    """Capturing the function instead of calling it. Reads as careful seam
    code; is the banned dependency."""
    _assert_all_flagged(CALLABLE_CAPTURE_CASES, "callable capture")


def test_slackkit_init_must_stay_import_free():
    _assert_trees_rejected(SLACKKIT_INIT_VIOLATIONS, "slackkit/__init__.py")


def test_slackkit_real_is_out_of_bounds_for_pure_packages():
    _assert_trees_rejected(SLACKKIT_REAL_VIOLATIONS, "slackkit.real")


def test_slackkit_ablation():
    """Required, not optional. Every case here is legitimate code that the
    `slackkit.real` rule must leave alone — the outbox import four orchestrator
    modules really make, real.py's SDK, and the dotted-vs-string prefix. A rule
    that reddens these is worse than the gap it closes; a rule that reddens
    nothing at all is the defect this whole lane is named after."""
    _assert_trees_accepted(SLACKKIT_ABLATION_CLEAN, "slackkit ablation")


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

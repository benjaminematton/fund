"""The purity lint's own negative controls (issue #43).

`scripts/check_purity.py` enforces CLAUDE.md invariant 3. A lint whose negative
control also passes is not a lint, so every table here is one half of a pair:
something the lint must catch, and the legitimate code next door it must not.

Round one covered the source-spelling evasions (aliased bindings, dynamic
imports, unlisted clock spellings, `market/`). Round two adds four groups:

* MASTER_REGRESSION_CASES — violations master flagged that the binding rewrite
  stopped flagging. A lint that gets looser is worse than one that never moved.
* BINDING_STANDS_CASES — where a rebinding cannot hide the import from a use
  site the binding STANDS rather than being popped, which is what lets the
  reference check resolve `time.sleep` through `import time` + `if False: time
  = None`. The collision REPORT that once lived here was deleted; the
  discriminator that decides where the binding stands, and the history of every
  case that moved, are documented on that table.
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

HOW EVERY EVASION IN THIS BRANCH WAS FOUND, and the one thing to know before
adding a case. Six evasions shipped past a green suite. Not one was found by
reasoning about a case; every one was found by CROSSING TWO AXES nobody had
crossed — enclosing-shadow x declaration-kind, nested-scope-kind x where-the-
read-sits, walrus x alias. The reason is structural:

    EVERY CASE FIXES ONE VALUE OF EVERY AXIS IT DOES NOT NAME.

A case pinning one instance of a rule reads exactly like a case pinning the
rule, and an ablation cannot tell them apart either, because deleting the block
fails both. So the axes a table names are a CLAIM about what it covers, and an
axis list that overstates coverage is the same defect as an unfalsifiable
guard — of which this file has now retired four (`numpy`, the old `nonlocal`,
a dead walrus pin, and a `global` case that could not catch the shape beside
it). Before adding a case, ask what it holds fixed, not what it varies.

Oracles are subject to the same rule. Prefer an EXACT probe — swap the callable
for a recorder and count calls — over timing. A 0.06s threshold once scored a
one-off `import asyncio` as an evasion; a noisy oracle is an overstated axis
list wearing a number.

Zero dependencies: plain no-arg `def test_*` functions and stdlib `tempfile`,
so it runs under pytest (what CI and `make test` invoke) and under the
`tests/run_tests.py` fallback runner alike. Scratch trees are built under
`tempfile`; nothing is ever written into the real packages.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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


def _lint_run_tree(files: dict[str, str]) -> tuple[int, str]:
    """main() against a scratch ROOT built from {relative path: source},
    returning (exit code, everything it printed). The output is what lets a
    tree case assert WHICH rule fired, not merely that the exit was non-zero."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, source in files.items():
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(source))
        lint = _load()
        lint.ROOT = Path(tmp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lint.main()
        return code, buf.getvalue()


def _lint_exit_code_for_tree(files: dict[str, str]) -> int:
    return _lint_run_tree(files)[0]


# --- rule fragments: deliberate contract, do not loosen ---------------------
#
# Every violation case below is (name, source, RULE) and asserts that the
# named rule is the one that fired — not merely that *something* did.
#
# This is not belt-and-braces. `except Exception as time:` stayed red for a
# whole round with the collision rule deleted, because the reference check
# flagged `time.sleep` instead and a truthiness assertion cannot tell the two
# apart. The test reported a plausible verdict while measuring something other
# than what it claimed, which is the defect in issue #43's own title arriving
# inside the suite meant to close it. A bare `assert errors` scores a deleted
# rule as covered.
#
# The fragments are deliberately the MINIMUM that identifies a rule, so an
# implementer stays free to reword any message without breaking a test. Do not
# extend them toward the full text (that freezes prose), and do not simplify
# them back to a truthiness check (that reopens the hole).

RULE_FORBIDDEN_IMPORT = "forbidden import"
RULE_DYNAMIC_IMPORT = "dynamic import"
RULE_CLOCK_REF = "wall-clock/sleep reference"
RULE_SLACKKIT_INIT = "must stay import-free"


# --- (case name, source, expected rule) tables -----------------------------

# Dynamic imports: an ast.Call, never an ast.Import, so the import branch
# never sees them.
DYNAMIC_IMPORT_CASES = [
    ("importlib.import_module with a concatenated name", """
        import importlib

        def sdk():
            return importlib.import_module("claude" + "_agent_sdk")
    """, RULE_DYNAMIC_IMPORT),
    ("__import__ builtin", """
        def sdk():
            return __import__("anthropic")
    """, RULE_DYNAMIC_IMPORT),
]

# Aliased bindings: FORBIDDEN_CALLS keys on the base's literal source text
# (check_purity.py:47-48), so renaming the binding walks straight past it.
ALIASED_BINDING_CASES = [
    ("import time as _t, then _t.sleep(1)", """
        import time as _t

        def pause():
            _t.sleep(1)
    """, RULE_CLOCK_REF),
    ("from datetime import datetime as _dt, then _dt.now()", """
        from datetime import datetime as _dt

        def stamp():
            return _dt.now()
    """, RULE_CLOCK_REF),
    ("from time import sleep, then a bare sleep(1)", """
        from time import sleep

        def pause():
            sleep(1)
    """, RULE_CLOCK_REF),
    # Twin of "star-import (name bound to a parameter)" in CLEAN_CASES: same
    # file, but nothing binds `sleep`, so the star module's own prefix is the
    # only thing that can resolve it. WATCHED_BASES cannot reach a leaf
    # function on its own — it holds `time`, not `time.sleep` — so this is the
    # case that pins the star-module prefix guess.
    ("star-import (name unbound)", """
        from time import *


        def pause():
            sleep(1)
    """, RULE_CLOCK_REF),
]

# Clock/blocking calls absent from FORBIDDEN_CALLS entirely.
UNLISTED_CLOCK_CALL_CASES = [
    ("time.time()", """
        import time

        def stamp():
            return time.time()
    """, RULE_CLOCK_REF),
    ("time.monotonic()", """
        import time

        def stamp():
            return time.monotonic()
    """, RULE_CLOCK_REF),
    ("time.perf_counter()", """
        import time

        def stamp():
            return time.perf_counter()
    """, RULE_CLOCK_REF),
    ("asyncio.sleep()", """
        import asyncio

        async def pause():
            await asyncio.sleep(0.1)
    """, RULE_CLOCK_REF),
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
    """, RULE_CLOCK_REF),
    ("star-import binds the literal name '*'", """
        from datetime import *


        def stamp():
            return datetime.now()
    """, RULE_CLOCK_REF),
    ("comprehension target folded into the parent scope", """
        import time

        # A comprehension target is invisible outside the comprehension, so
        # folding it into module scope buys nothing and silences the file.
        _UNUSED = [None for time in ()]


        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    # D5. Master flags this by source text; the branch launders it through the
    # star import. Resolvable WITHIN the file — a star import means the name
    # could have come from anywhere, so an otherwise-unbound `datetime.now`
    # under one must stay a candidate. No cross-module following required.
    ("star-import laundering of datetime.now()", """
        from state.helpers import *


        def stamp():
            return datetime.now()
    """, RULE_CLOCK_REF),
]

# WHAT THIS TABLE PINS: the binding STANDS. Not that a collision is reported —
# that report was deleted (see below). Where a rebinding cannot hide the import
# from a use site, the import binding is left in place instead of being popped,
# and that is what lets the REFERENCE check still resolve `time.sleep` through
# the rebind. Every case here is a violation caught by the reference check;
# delete binding-stands and they all go clean, which is the whole point.
#
# THE COLLISION REPORT WAS DELETED — ruled and approved 2026-08-25 after final
# review. Ablating the report (keeping binding-stands) over the whole corpus of
# 104 unique snippets lost ZERO purity detections; independently reproduced
# here, across every violation table, lost 0. Six violations disappeared and
# every one of them SHOULD have:
#
#   * four were false positives on class-body fields that never use the name —
#     `@dataclass date: str`, `NamedTuple time: str`, an Enum member `time`, a
#     method named `date`. Losing those IS the fix; they are guards in
#     CLEAN_CASES now.
#   * two were not purity violations at all: `class Stamp(datetime.datetime):
#     datetime = None` and `class Bar(BaseModel): date: date`. In both the use
#     reaches the import but the use is NOT a forbidden target — no clock is
#     read. A use that cannot reach a forbidden target is not a purity concern.
#     Both are now clean cases; see the note on each.
#
# What the report uniquely caught was shadowing HYGIENE, not purity, which is
# not this lint's job. What is load-bearing, and stays, is binding-stands.
#
# THE DISCRIMINATOR STAYS — it now decides where the BINDING stands rather than
# where a report fires. One question decides every shadowing case in this file:
#
#     Can the rebinding make a use site resolve to the import?
#
# The rows fall out of the question; the question cannot be re-derived from the
# rows, which is why it is written here and not as a list of scope kinds.
#
#   module scope ................................. YES, rebind and use share
#       one namespace. Verified: with `import time` above it, a use placed
#       before an `except ... as time` / `with ... as time` / `case {...: time}`
#       returns a real 145616.61 timestamp, and afterwards the name is the
#       caught object (or deleted, for except-as).
#   class body ................................... YES, LOAD_NAME with a global
#       fallback: `a = time.time()` on the line ABOVE `time = None` is real.
#   defaults, decorators, annotations, base-class
#       lists, a genexp's outermost iterable ..... YES, evaluated in the
#       ENCLOSING scope, before the shadow exists.
#   function body ................................ NO. Binding a name ANYWHERE
#       in a function makes it local for the whole body, so a use before the
#       binding raises UnboundLocalError and never reaches the module. This
#       covers parameters, `except ... as`, and `match`/`case` captures alike —
#       all three verified by execution.
#   comprehension `for` target ................... NO, Python 3 scopes it to
#       the comprehension and it does not leak.
#   comprehension WALRUS target .................. DEPENDS, and the `for` row
#       above does not carry over. PEP 572 binds a walrus target in the
#       CONTAINING scope, so it leaks out of the comprehension and lands under
#       whichever row governs that scope: private inside a function body,
#       reachable at module scope.
#
#       AND — a use INSIDE the same comprehension also resolves to the
#       containing scope, so if the name is import-bound there the use reaches
#       the real module. `[(time := time.sleep(0.30)) for _ in range(1)]`
#       blocks for a third of a second and leaves `time` as None. The name must
#       therefore NOT be popped inside the comprehension.
#
#       CORRECTION: this row previously ended "at module scope it is reachable
#       and the reference check catches any real clock read regardless." That
#       was mine and it was false — the reference check did NOT catch the
#       self-use shape, which is how six evasions survived a round. It is the
#       kind of claim this file exists to disprove, so it is recorded rather
#       than quietly replaced.
#
# Rareness and suspiciousness are NOT the test; reachability is. A rule that
# flags code which cannot produce the harm is the failure mode that gets lints
# deleted, which this lane has now established twice.
#
# ASSERTION HISTORY — inverted, then reverted in two steps. Every move ruled
# and approved by the coordinator, 2026-08-25. Recorded so the next reader
# files this as a decision rather than churn and does not re-litigate it:
#
#   1. The parameter / except-as / match-capture cases asserted CLEAN, and were
#      INVERTED to assert a VIOLATION under a rule of "a collision at EVERY
#      scope". That was a TIGHTENING — a stricter claim about what the lint
#      must catch — never a weakening to make a failing test pass, which
#      CLAUDE.md forbids. No expected value was edited to match an
#      implementation's output; the encoded rule changed.
#   2. The rule was revised to the discriminator above and the PARAMETER case
#      was reverted to CLEAN. The revert was deliberately partial.
#   3. The two remaining cases were then reverted as well, once execution
#      showed the justification for keeping them was wrong: an `except ... as`
#      target and a `match` capture are exactly as private as a parameter, so
#      the discriminator answers NO for all three. A carve-out for "conditional
#      or partial bindings are suspicious" was offered and explicitly rejected.
#      The table that held them (INVERTED_SHADOW_CASES) was retired rather than
#      left as a husk; this comment is the durable artefact, not that table.
#   4. The collision REPORT was deleted outright and every case here was
#      re-measured against an ablated copy rather than reassigned by
#      assumption. EIGHT stayed violations under the reference check and their
#      rule moved from the deleted RULE_COLLISION to RULE_CLOCK_REF; `global`
#      stayed red for a different reason (see its case); two moved to
#      CLEAN_CASES. RULE_COLLISION was removed because nothing asserts it.
#
# The background to move 1 stands: the shadow rule was deleted because it
# protected nothing. Monkeypatching `_bound_names` to return set() left every
# injected-Clock shape clean, orchestrator/clock.py included, because the real
# mechanism is that `self` is unresolvable. A sweep of all 45 pure-package
# files found ZERO parameters or locals named time/date/datetime/asyncio/
# importlib. It cost ~10 looser-than-master rows and enabled a regression.
#
# Each module-scope entry below pairs with a function-body twin in CLEAN_CASES
# under the same name. Same shape, different scope, opposite verdict — that
# pairing IS the rule, so keep the names matched if you touch either table.
#
# WARNING ABOUT THE `global` BLOCK — read before adding or pruning a case there.
# It has produced THREE separate evasions, and each one was invisible to the
# case written for the one before it. Measured, not recalled: reverting the fix
# for evasion N leaves the case for evasion N-1 red, every time.
#
#     evasion          the case that existed             pinned it?
#     enclosing shadow  (none)                            -
#     no assignment     global + assignment               NO, stayed red
#     local import      global +/- assignment (both)      NO, both stayed red
#
# So a case here that "already covers global" almost certainly covers one
# INSTANCE of the rule. That is the general trap: a case pinning one instance
# reads exactly like a case pinning the rule, and an ablation cannot separate
# them either, because deleting the block fails both. The only defence is
# another case that varies the axis the first one silently fixed — which is
# also why the axes a table names are a CLAIM about what it covers, and an axis
# list that overstates coverage is the same defect as an unfalsifiable guard.
BINDING_STANDS_CASES = [
    ("`if False: time = None` at module scope", """
        import time

        if False:
            time = None


        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    ("`time = time` self-assignment on the last line", """
        import time


        def pause():
            time.sleep(1)


        time = time
    """, RULE_CLOCK_REF),
    ("except-as (module scope)", """
        import time

        try:
            pass
        except Exception as time:
            pass


        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    ("with-as (module scope)", """
        import time

        with open("/dev/null") as time:
            pass


        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    # Added when the function-body twin moved to CLEAN_CASES, so the pair is
    # complete on both sides. Verified at module scope: the use above returns a
    # real timestamp and `time` afterwards is the captured value.
    # `global` defeats the function-body exemption: the declared binding is the
    # MODULE's, so the privacy guarantee the discriminator's function-body row
    # rests on does not apply — the read below reaches the real module and
    # returns a live timestamp rather than raising UnboundLocalError.
    #
    # With the collision report deleted, the fix is in the BINDING logic, not in
    # a report: a name declared `global` must not shadow, so the import binding
    # stands and the reference check sees `time.time`. Measured against an
    # ablated copy rather than assumed — it is clean both with and without the
    # report, which is what shows the report was never what caught it.
    #
    # Twin of "nonlocal (function body)" in CLEAN_CASES, which cannot reach an
    # import at all. Low reachability: no `global` or `nonlocal` statement
    # exists anywhere in this repo today. It is written because the
    # discriminator's own comment is false for it, not because anyone is about
    # to write it.
    ("global (function body declaring a module binding)", """
        import time


        def capture():
            global time
            grabbed = time.time
            time = None
            return grabbed
    """, RULE_CLOCK_REF),
    # Twin of the case above, and the pair states the rule that was never
    # written down: `global` means the MODULE's binding, regardless of what any
    # enclosing scope bound. Above there is no enclosing shadow; here `outer`
    # binds `time = None` and `global` in `inner` reaches straight past it to
    # the module anyway.
    #
    # Resolution used to follow the ENCLOSING scope, so the shadow silenced the
    # check and this executed a real clock read while the lint said clean. It is
    # a pin, not a red case — it passes today and must keep passing.
    # Verified: inner() blocks 0.304s (executor controlled — the same shape
    # without the sleep reports 0.001s). Deleting the `module_bindings`
    # threading on a copy turns this case clean, so it pins that block and not
    # something adjacent.
    ("global reaches the module past an enclosing shadow (WITH assignment)", """
        import time


        def outer():
            time = None

            def inner():
                global time
                v = time.sleep(0.30)
                time = None
                return v

            return inner
    """, RULE_CLOCK_REF),
    # THIS CASE EXISTS BECAUSE THE ONE ABOVE PINNED THE INSTANCE, NOT THE CLASS.
    # It is not duplication — deleting it re-opens a live evasion.
    #
    # The case above carries `time = None` inside `inner`, and that assignment
    # is load-bearing for the wrong reason: the `declared_global` branch sits
    # inside `for name in rebound`, so a function that declares `global time`
    # and never ASSIGNS `time` never enters `rebound` and the module binding is
    # never consulted. Delete that one line from the case above and it goes
    # clean. So the case asserted "`global` reaches the module when the function
    # also assigns the name" while reading like "`global` reaches the module" —
    # which is why fixing the third evasion left this fourth one, one line away,
    # untouched.
    #
    # The general shape of that mistake is worth more than this case: A CASE
    # THAT PINS ONE INSTANCE OF A RULE READS EXACTLY LIKE A CASE THAT PINS THE
    # RULE, and an ablation cannot separate them either, because deleting the
    # block fails both. The only defence is a second case that varies the axis
    # the first one silently fixed.
    #
    # Verified: inner() blocks 0.304s; the same shape with the `global` line
    # removed raises AttributeError, proving the `global` is what reaches the
    # module; the same shape with no sleep reports 0.001s (executor control).
    ("global reaches the module past an enclosing shadow (NO assignment)", """
        import time


        def outer():
            time = None

            def inner():
                global time
                return time.sleep(0.30)

            return inner
    """, RULE_CLOCK_REF),
    # THIRD `global` shape, and again neither case above could catch it. Under
    # `global x`, a function-local `import x` binds the MODULE's x to the module
    # object, so this scope's own import must win over whatever module scope
    # held. The fix for the previous evasion overwrote that binding
    # unconditionally and clobbered the import.
    #
    # Deletion-check (standing rule): revert the `not in imported` guard on a
    # copy and THIS case goes clean while BOTH cases above stay red —
    #     global + function-local import   FLAG -> clean   (this case)
    #     global, WITH assignment          FLAG -> FLAG    (cannot pin it)
    #     global, NO assignment            FLAG -> FLAG    (cannot pin it)
    # so the two existing cases are not merely redundant here, they are blind.
    #
    # Verified: probe() blocks 0.302s; the same shape without the inner
    # `import time` raises NameError, proving the inner import is the
    # precondition; the same shape with no sleep reports 0.001s (executor
    # control). Passes today — a pin, not a red case.
    ("global + a function-local import of the same name", """
        def outer():
            import time
            time = None

            def probe():
                global time
                import time
                return time.sleep(0.30)

            return probe
    """, RULE_CLOCK_REF),
    # PIN for the nested-scope stop in _walrus_targets, whose docstring asserts
    # that nested function and class scopes are not crossed. Found by generating
    # across four axes (nested-scope kind x what the walrus binds x where the
    # read sits x containing scope): of 24 snippets only this shape and its
    # near-twin distinguish the guard, and none of the three shapes written by
    # hand did. The walrus belongs to the LAMBDA, so it must not leak out and
    # pop the comprehension's `time`; remove the stop and the genuine
    # `time.monotonic()` beside it goes clean. Verified: go() returns a real
    # float off the module clock. Passes today — that is the point.
    ("walrus inside a lambda inside a comprehension does not leak out", """
        import time


        def go():
            return [((lambda: (time := 1))(), time.monotonic()) for _ in range(1)]
    """, RULE_CLOCK_REF),
    # `nonlocal` onto an enclosing function's IMPORT. Twin of "nonlocal onto a
    # NON-import enclosing local" in CLEAN_CASES, and the difference between
    # them is the whole rule: an `import` inside a function is a function-local
    # binding whose value IS the module, so `nonlocal` reaches it. The earlier
    # claim that `nonlocal` "can never reach an import" was wrong, and the case
    # written on it has been corrected rather than deleted.
    # Executed: inner() blocks 0.305s on a real wall clock.
    ("nonlocal onto an enclosing function's import", """
        def outer():
            import time

            def inner():
                nonlocal time
                v = time.sleep(0.30)
                time = None
                return v

            return inner
    """, RULE_CLOCK_REF),
    # --- walrus used INSIDE its own comprehension --------------------------
    # PEP 572 gives the walrus target to the CONTAINING scope, so a use inside
    # the comprehension resolves there — to the import. The lint also binds the
    # target inside the comprehension's own scope, which pops the name exactly
    # where the use is read, so the use resolves to nothing and the clock read
    # goes clean. Every one of these executes and blocks ~0.30s on a real wall
    # clock; the module-scope variants leave `time` set to None afterwards.
    #
    # This was previously documented in the lint as harmless because "both paths
    # bind to unresolvable, so no verdict differs". That is falsified by every
    # case below and is the fourth "harmless by construction" claim in this
    # change to turn out false. Measure such claims; do not accept them.
    ("walrus used inside its own listcomp", """
        import time

        _U = [(time := time.sleep(0.30)) for _ in range(1)]
    """, RULE_CLOCK_REF),
    ("walrus used inside its own setcomp", """
        import time

        _U = {(time := time.sleep(0.30)) for _ in range(1)}
    """, RULE_CLOCK_REF),
    ("walrus used inside its own dictcomp", """
        import time

        _U = {i: (time := time.sleep(0.30)) for i in range(1)}
    """, RULE_CLOCK_REF),
    ("walrus used inside its own genexp", """
        import time

        _U = tuple((time := time.sleep(0.30)) for _ in range(1))
    """, RULE_CLOCK_REF),
    ("walrus used inside a comprehension's `if` clause", """
        import time

        _U = [x for x in range(1) if (time := time.sleep(0.30)) is None]
    """, RULE_CLOCK_REF),
    ("walrus used inside its own comprehension, in function scope", """
        def go():
            import time
            return [(time := time.sleep(0.30)) for _ in range(1)]
    """, RULE_CLOCK_REF),
    # REMOVED: a case here claimed to pin the _walrus_targets nested-scope stop
    # with the read in a separate `def pause()` at module scope. Measured, it
    # flags identically with and without the guard (binding-stands keeps the
    # module binding either way), so it pinned nothing and its comment was
    # false. The real pin is above, in the entry that puts the read INSIDE the
    # comprehension. Left as a note because a fourth unfalsifiable guard is
    # worth recording, not silently deleting.
    ("match-capture (module scope)", """
        import time

        _NOW = time.monotonic()

        match {"clock": 1}:
            case {"clock": time}:
                pass
    """, RULE_CLOCK_REF),
    ("walrus `if (time := None):` at module scope", """
        import time

        if (time := None):
            pass


        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    ("same-scope rebind placed after the use", """
        import time

        time.sleep(1)
        time = None
    """, RULE_CLOCK_REF),
]

# Same clock, one spelling away from the listed one.
CLOCK_SPELLING_CASES = [
    ("datetime.today() — date.today is listed, datetime.today is not", """
        from datetime import datetime


        def stamp():
            return datetime.today()
    """, RULE_CLOCK_REF),
    ("time.time_ns()", """
        import time


        def stamp():
            return time.time_ns()
    """, RULE_CLOCK_REF),
    ("time.monotonic_ns()", """
        import time


        def stamp():
            return time.monotonic_ns()
    """, RULE_CLOCK_REF),
    ("time.perf_counter_ns()", """
        import time


        def stamp():
            return time.perf_counter_ns()
    """, RULE_CLOCK_REF),
    ("time.process_time()", """
        import time


        def stamp():
            return time.process_time()
    """, RULE_CLOCK_REF),
    ("time.localtime()", """
        import time


        def stamp():
            return time.localtime()
    """, RULE_CLOCK_REF),
    ("time.gmtime()", """
        import time


        def stamp():
            return time.gmtime()
    """, RULE_CLOCK_REF),
    # --- round three: the docstring says "the time module's clock and sleep
    # functions"; these are all of that and all currently clean. The three
    # no-arg formatters read the wall clock; their with-argument forms are
    # pure and are guarded in CLEAN_CASES.
    ("time.clock_gettime()", """
        import time


        def stamp():
            return time.clock_gettime(0)
    """, RULE_CLOCK_REF),
    ("time.clock_gettime_ns()", """
        import time


        def stamp():
            return time.clock_gettime_ns(0)
    """, RULE_CLOCK_REF),
    ("time.thread_time()", """
        import time


        def stamp():
            return time.thread_time()
    """, RULE_CLOCK_REF),
    ("time.thread_time_ns()", """
        import time


        def stamp():
            return time.thread_time_ns()
    """, RULE_CLOCK_REF),
    ("time.strftime() with no time tuple reads localtime", """
        import time


        def stamp():
            return time.strftime("%Y-%m-%d")
    """, RULE_CLOCK_REF),
    ("time.asctime() with no argument", """
        import time


        def stamp():
            return time.asctime()
    """, RULE_CLOCK_REF),
    ("time.ctime() with no argument", """
        import time


        def stamp():
            return time.ctime()
    """, RULE_CLOCK_REF),
    # pandas is a real dependency of fundbt/. Verified 2026-08-25: the real
    # tree contains ZERO Timestamp.now() call sites, so adding this reddens
    # nothing that exists.
    ("pd.Timestamp.now()", """
        import pandas as pd


        def stamp():
            return pd.Timestamp.now()
    """, RULE_CLOCK_REF),
    ("from pandas import Timestamp, then Timestamp.now()", """
        from pandas import Timestamp


        def stamp():
            return Timestamp.now()
    """, RULE_CLOCK_REF),
]

# Rebindings of the dynamic-import builtins. Master misses these too — they are
# still-open evasions, not regressions.
DYNAMIC_IMPORT_SPELLING_CASES = [
    ("from builtins import __import__", """
        from builtins import __import__


        def sdk():
            return __import__("anthropic")
    """, RULE_DYNAMIC_IMPORT),
    ("_imp = __import__, then _imp(...)", """
        _imp = __import__


        def sdk():
            return _imp("anthropic")
    """, RULE_DYNAMIC_IMPORT),
]

# Capturing the callable instead of calling it. These read as conscientious
# Clock-protocol code while being exactly the wall-clock dependency invariant 3
# bans — the reviewers rated this the most realistic gap of the set.
CALLABLE_CAPTURE_CASES = [
    ("module-level seam alias `_sleep = time.sleep`", """
        import time

        _sleep = time.sleep
    """, RULE_CLOCK_REF),
    ("dispatch table holding datetime.utcnow", """
        from datetime import datetime

        _FIELDS = {"ts": datetime.utcnow}
    """, RULE_CLOCK_REF),
    ("adapter class whose attributes are the clock functions", """
        import time
        from datetime import datetime


        class SystemClock:
            now = datetime.now
            sleep = time.sleep
    """, RULE_CLOCK_REF),
    ("default-argument fallback `_fallback=time.monotonic`", """
        import time


        class Runner:
            def __init__(self, clock=None, _fallback=time.monotonic):
                self._clock = clock
                self._fallback = _fallback
    """, RULE_CLOCK_REF),
    ("intermediate module alias `_clock_mod = time`", """
        import time

        _clock_mod = time


        def pause():
            _clock_mod.sleep(1)
    """, RULE_CLOCK_REF),
    ("getattr(time, \"sleep\")() — house style at market/source_alpaca.py:33", """
        import time


        def pause():
            getattr(time, "sleep")()
    """, RULE_CLOCK_REF),
    # --- WALRUS x ALIAS: two families that had never been crossed ----------
    # Open since 11d7b54 — the whole branch — and not a regression from any
    # recent work. The alias pass has always been `ast.Assign`-only, and
    # `ast.NamedExpr` appears in the lint exactly once, inside _walrus_targets,
    # purely for SCOPING. So a walrus is registered as a BINDER — it pops or
    # stands the name — while the module it names is thrown away.
    #
    # Every walrus case elsewhere in this file binds its target to a VALUE
    # (`_F()`, `1`, `r`, `time.sleep(...)`); not one binds it to a module or
    # class ALIAS. Every alias case above binds with `=`; not one binds with
    # `:=`. Neither family is wrong and neither covers this, because the axis
    # that separates them was never varied.
    #
    # Verified by EXACT PROBE, not timing: `time.sleep` is swapped for a
    # recorder, so "the clock path ran" is a call count. All six below report
    # `sleep called 1x` while the lint reports clean. The plain-assign control
    # (`time = _src`) FLAGS, which is what identifies the walrus as the thing
    # doing the hiding. Timing was rejected as the oracle after a 0.06s
    # threshold scored a one-off `import asyncio` as an evasion — a noisy
    # oracle is the same defect class as an overstated axis list.
    ("walrus alias, function scope", """
        import time as _src


        def f():
            _w = (time := _src)
            return time.sleep(0.30)
    """, RULE_CLOCK_REF),
    ("walrus alias, module scope", """
        import time as _src

        _w = (time := _src)
        _R = time.sleep(0.30)
    """, RULE_CLOCK_REF),
    # Class scope resolves through `inherited` rather than `bindings` for its
    # children, so it is its own arm of the mechanism rather than a third
    # spelling of the two above.
    ("walrus alias, class scope", """
        import time as _src


        class C:
            _w = (time := _src)
            _R = time.sleep(0.30)
    """, RULE_CLOCK_REF),
    # Transitivity: the walrus alias must itself be resolvable as the SOURCE of
    # a second walrus alias. Master misses this one too, so it is a still-open
    # evasion rather than a regression.
    ("chained walrus alias", """
        import time as _src


        def f():
            _a = (_x := _src)
            _b = (_y := _x)
            return _y.sleep(0.30)
    """, RULE_CLOCK_REF),
    # DISTINCT MECHANISM, and the one an obvious fix would miss: here the
    # walrus sits in no `ast.Assign` at all. Extending the alias pass by
    # walking `Assign.value` for a NamedExpr fixes the three above and leaves
    # this red. The `if` and `while` condition shapes were measured and are the
    # same mechanism as this one — documented rather than duplicated, since
    # three spellings of one arm is volume, not coverage. Master misses it too:
    # its `_dotted` cannot take a NamedExpr as an attribute base.
    ("walrus alias in a return expression (no enclosing Assign)", """
        import time as _src


        def f():
            return (time := _src).sleep(0.30)
    """, RULE_CLOCK_REF),
    # A non-`time` target, to keep the family from being pinned to one module.
    # Proved by identity rather than a call recorder: f() returns a real
    # datetime.datetime instance, so the walrus really did bind the imported
    # class. Master flags this one.
    ("walrus alias, datetime target", """
        from datetime import datetime as _dt


        def f():
            _w = (datetime := _dt)
            return datetime.now()
    """, RULE_CLOCK_REF),
]

# --- round three ------------------------------------------------------------

# The "yes" rows of the discriminator above: positions evaluated in the
# ENCLOSING scope, where a later rebinding does not shadow the import, plus
# class bodies whose LOAD_NAME falls back to the global. The earlier ruling —
# "a function-local rebinding makes the name local for the whole body, so it
# can never silently resolve to the stdlib" — is true for function BODIES only.
# Each of these was confirmed by executing it.
#
# KNOWN SHAPE, ruled deliberate 2026-08-25 and NARROWED the same day in final
# review. Documented so it is met as a decision, not as a surprise CI failure.
#
# THE RULING, as it now stands. A class-body rebinding of a watched name is a
# collision ONLY WHERE A USE ACTUALLY REACHES THE IMPORT. This one is a true
# positive and must stay flagged:
#
#     from datetime import date
#     class Bar(BaseModel):
#         date: date            # <- annotation evaluates to the import, THEN rebinds
#
# THE NARROWING, and why it was needed. The rule originally fired on the mere
# EXISTENCE of an import/rebind pair, never checking that a use PRECEDES the
# rebinding — so it also reddened all of these, none of which use the name at
# all, making the error message ("a use above the rebinding still resolves to
# it") literally false:
#
#     @dataclass                      class Bar(NamedTuple):
#     class Bar:                          time: str
#         date: str
#     class Kind(Enum):               class Bar:
#         time = 1                        def date(self): ...
#
# A position where the code and the discriminator disagree is exactly what the
# discriminator exists to prevent, so these are fixed rather than documented.
# The ruling stands for the case that HAS a use; it never should have covered
# the case that has none.
#
# THE NUANCE THE FIX MUST NOT LOSE: an annotation IS a use. `date: date`
# evaluates the annotation — resolving to the import — and only then rebinds,
# on the SAME line. A naive "report only if a Load appears at a strictly lower
# lineno" would wrongly clear it. The two shapes are written below and in
# CLEAN_CASES as a matched pair so the boundary is pinned from both sides.
#
# state/models.py:8 is exactly that import and holds the models; there are zero
# uses of either shape today. Consistency with narrowing `numpy` out is
# unchanged: `date` IS a watched base and `numpy` is not. The remedy for a
# developer who hits the true positive is to rename the field, or to import the
# module (`import datetime`) rather than the name. Do not "fix" it by exempting
# class bodies: that would delete the class-body row of the discriminator,
# which four cases below depend on.
ENCLOSING_SCOPE_CASES = [
    ("class body: LOAD_NAME falls back to the global import", """
        import time


        class C:
            # C.clock() really returns the wall clock: the class-level
            # `time = None` below has not executed yet at this point.
            clock = staticmethod(time.time)
            time = None
    """, RULE_CLOCK_REF),
    # RULE CHANGED from the since-deleted RULE_COLLISION to RULE_CLOCK_REF
    # when the "every
    # scope" rule was revised: the parameter is private to the body, so the
    # collision rule no longer fires here. What makes this a violation is that
    # the DEFAULT is evaluated in the enclosing scope, so the parameter really
    # holds the imported class and `datetime.now()` resolves through it to
    # datetime.datetime.now. Catching it means propagating a default into the
    # parameter's binding — the alias-propagation family, not the collision one.
    ("default argument is evaluated in the enclosing scope", """
        from datetime import datetime


        def stamp(datetime=datetime):
            # The default IS the datetime class, so this reads the wall clock.
            return datetime.now()
    """, RULE_CLOCK_REF),
    ("class nested in a function", """
        import time


        def make():
            class C:
                clock = staticmethod(time.time)
                time = None
            return C
    """, RULE_CLOCK_REF),
    ("class nested in a class", """
        import time


        class Outer:
            class Inner:
                clock = staticmethod(time.time)
                time = None
    """, RULE_CLOCK_REF),
    ("annotation is evaluated in the enclosing scope", """
        import time


        class C:
            clock: time.time = None
            time = None
    """, RULE_CLOCK_REF),
    # --- the outer expression ALONE, with nothing else in the file ---------
    # The cases above pair an outer expression with a rebinding, so a rule that
    # only ever looked at the rebinding would still pass them. These four strip
    # that away: nothing is rebound, the function is never called, and in the
    # first three the name is never referenced in the body either.
    #
    # "Unused" is the whole point: none of these is reachable by looking at the
    # function body, so a scanner that only walks bodies misses all four.
    #
    # Defaults, decorators and base lists are EVALUATED in the enclosing scope
    # at definition time — `def f(x=time.sleep)` captures the real time.sleep
    # the moment the def executes, whether or not anything ever calls f.
    #
    # Annotations are the exception and the earlier wording here was wrong.
    # Every file in this suite carries `from __future__ import annotations`
    # (see the module docstring at the top), so annotations are stringified and
    # never evaluated at all; on 3.14 they are lazy regardless. The VERDICT is
    # unchanged — PEP 649's annotate function still closes over the enclosing
    # scope, so the name it would resolve is the enclosing one — but the
    # mechanism is deferred resolution, not evaluation at definition time, and
    # the previous claim that this was "confirmed by executing it" was false
    # for the annotation arm. Do not re-derive the old sentence.
    #
    # Each of these pins one arm of _outer_exprs; delete that arm and the case
    # goes silent.
    ("outer position alone: unused default argument", """
        import time


        def f(x=time.sleep):
            pass
    """, RULE_CLOCK_REF),
    ("outer position alone: decorator", """
        import time


        @time.sleep
        def f():
            pass
    """, RULE_CLOCK_REF),
    ("outer position alone: parameter annotation", """
        import time


        def f(x: time.sleep):
            pass
    """, RULE_CLOCK_REF),
    ("outer position alone: base-class list", """
        import time


        class C(time.sleep):
            pass
    """, RULE_CLOCK_REF),
]

EVASION_CASES = (DYNAMIC_IMPORT_CASES + ALIASED_BINDING_CASES
                 + UNLISTED_CLOCK_CALL_CASES + MASTER_REGRESSION_CASES
                 + BINDING_STANDS_CASES + CLOCK_SPELLING_CASES
                 + DYNAMIC_IMPORT_SPELLING_CASES + CALLABLE_CAPTURE_CASES
                 + ENCLOSING_SCOPE_CASES)

# Already caught today. These must survive the rewrite.
DETECTED_CASES = [
    ("plain import anthropic", """
        import anthropic
    """, RULE_FORBIDDEN_IMPORT),
    ("from claude_agent_sdk import query", """
        from claude_agent_sdk import query
    """, RULE_FORBIDDEN_IMPORT),
    ("import agents.runtime", """
        import agents.runtime
    """, RULE_FORBIDDEN_IMPORT),
    ("plain datetime.now()", """
        from datetime import datetime

        def stamp():
            return datetime.now()
    """, RULE_CLOCK_REF),
    ("plain time.sleep(1)", """
        import time

        def pause():
            time.sleep(1)
    """, RULE_CLOCK_REF),
    # --- round three: enclosing-scope shapes ALREADY caught. Measured, not
    # assumed — these three of the nine positions need no new work, and
    # locking them in stops the shadow-rule deletion from disturbing them.
    ("decorator in a class body, rebind after", """
        import time


        class C:
            @time.sleep
            def pause(self):
                pass

            time = None
    """, RULE_CLOCK_REF),
    ("comprehension's outermost iterable", """
        import time


        class C:
            vals = [x for x in time.time()]
            time = None
    """, RULE_CLOCK_REF),
    ("lambda default argument", """
        import time


        class C:
            f = lambda _t=time.time: _t()
            time = None
    """, RULE_CLOCK_REF),
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
    # --- the function-body row of the discriminator -----------------------
    # These three pin it, and each is the twin of an identically-named entry in
    # BINDING_STANDS_CASES that differs ONLY in scope. Same shape, module scope =
    # violation; same shape, function body = clean. That pairing IS the rule,
    # so keep the names matched if you touch either table.
    #
    # All three were inverted to violations under the superseded "collision at
    # every scope" rule and reverted here — the parameter first, then the other
    # two once execution showed an `except ... as` target and a `match` capture
    # are exactly as private as a parameter. History on BINDING_STANDS_CASES.
    #
    # Unlike the shadow cases further up, every file here DOES import the name,
    # which is what makes them pins rather than trivia: with no import there is
    # no binding to collide with and the case proves nothing.
    ("parameter (function body)", """
        import time


        def elapsed(time):
            # Python binds this to the parameter for the entire body; the
            # module-level import is unreachable from here.
            return time.perf_counter()
    """),
    ("except-as (function body)", """
        import time


        def deadline(fn):
            try:
                return fn()
            except TimeoutError as time:
                # Binding `time` anywhere in this function makes it local for
                # the WHOLE body — a use above would raise UnboundLocalError,
                # never reach the module. Verified by execution.
                return time.time
    """),
    ("match-capture (function body)", """
        import time


        def handle(event):
            match event:
                case {"clock": time}:
                    # Same as except-as: local for the whole body.
                    return time.monotonic()
            return None
    """),
    ("with-as (function body)", """
        import time


        def pause(ctx):
            with ctx as time:
                # Completes the matrix: every shape in BINDING_STANDS_CASES
                # function-body twin here. A gap in the pairing reads as an
                # unexamined shape.
                return time.monotonic()
    """),
    # CORRECTED 2026-08-25. This case previously read `import time` at module
    # scope with `time = None` in `outer`, and was justified as "`nonlocal` can
    # only bind an enclosing function's local, never an import, so it can never
    # reach the stdlib." The premise is true and the conclusion does not follow:
    # an `import` INSIDE a function creates a function-local binding whose value
    # IS the module, so `nonlocal` can reach one. That shape is now a violation
    # in BINDING_STANDS_CASES; this control keeps the genuinely-clean half so
    # the pair pins the distinction instead of deleting it.
    #
    # Here the enclosing local is NOT an import — it is a caller's Timer — so
    # nothing resolves to the stdlib and `.monotonic()` is that object's own API.
    ("nonlocal onto a NON-import enclosing local (function body)", """
        def outer():
            time = Timer()

            def inner():
                nonlocal time
                return time.monotonic()

            return inner
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
    # Comprehension scoping. A comprehension target is scoped to the
    # comprehension in Python 3: it rebinds nothing outside, so reusing an
    # imported name is legal. The `time` case below is the one that PINS the
    # comprehension branch of _SCOPE_NODES under round three's rules — `time`
    # is a watched base, so if the target folded into module scope the
    # narrowed collision rule would fire. The `json` cases cover the same
    # shape on an unwatched name.
    ("comprehension target reusing an imported WATCHED name (pins the block)", """
        import time

        _UNUSED = [None for time in ()]
    """),
    # Twin of the case above, and the asymmetry IS the content: a normal `for`
    # target does not leak out of the comprehension, but a WALRUS target binds
    # in the CONTAINING scope (PEP 572). So `time` here is a genuine local of
    # `elapsed`, the module import is unreachable from the whole body by the
    # function-body row of the discriminator, and `time.monotonic()` is not a
    # clock read at all.
    #
    # Executed to confirm the binding is real, not theoretical:
    # `elapsed(["NOT-THE-STDLIB"])` returns 'NOT-THE-STDLIB'. The discriminator's
    # comprehension row is written for the `for` target and does not carry over
    # to the walrus — do not collapse the two.
    ("walrus target in a comprehension binds in the CONTAINING scope", """
        import time


        def elapsed(rows):
            marks = [(time := r) for r in rows]
            return time.monotonic()
    """),
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
    # Twin of "star-import (name unbound)" in ALIASED_BINDING_CASES. Same file
    # shape, one difference: here the name IS bound, by a parameter. Bound ->
    # clean, unbound -> violation. Keep the pair named together.
    ("star-import (name bound to a parameter)", """
        from time import *


        def run(sleep):
            # `sleep` is the injected callable, not the one the star import
            # would have supplied. Resolving to nothing must mean nothing —
            # never "guess the star module".
            sleep(1)
    """),
    # --- round three: the narrowed collision rule -------------------------
    # The collision rule fires only when the rebound name's resolved target is
    # a forbidden-import root, or the base of a FORBIDDEN_CALLS pattern
    # (datetime.now, date.today, time.sleep, ...). NOT "came from a watched
    # module" — `from datetime import UTC` resolves to `datetime.UTC`, which
    # IS under `datetime`, so the loose wording reddens the first case below
    # while passing all the others. That case is this rule's ablation, the
    # same role `slackkit.realtime` plays for the dotted-prefix matcher.
    #
    # Every one of these is ordinary defensive Python whose rebound name can
    # reach nothing forbidden. The current error text claims the rebinding
    # "disables the purity check silently", which is false for all five — a
    # rule whose own message is false on most of its firings is over-firing
    # by construction.
    ("`from datetime import UTC` + fallback rebind (rule ablation)", """
        try:
            from datetime import UTC
        except ImportError:
            from datetime import timezone
            UTC = timezone.utc
    """),
    ("TYPE_CHECKING import with a runtime fallback", """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from decimal import Decimal
        else:
            Decimal = float
    """),
    ("`import logging` then `logging = logging.getLogger(__name__)`", """
        import logging

        logging = logging.getLogger(__name__)
    """),
    ("`from __future__ import annotations` + a variable named annotations", """
        from __future__ import annotations

        annotations = {"a": 1}
    """),
    # RE-POINTED from numpy to pandas. As written with `numpy` this guard
    # PASSED VACUOUSLY and pinned nothing: `numpy` is not in WATCHED_BASES
    # (verified — `"numpy" in WATCHED_BASES` is False), so no rule could ever
    # have fired on it and the case could not fail. `pandas` IS a watched base,
    # via pandas.Timestamp.now, and is a hard dependency of fundbt/ — so this
    # spelling is both the realistic one and the one that can actually fail.
    # A guard that cannot fail is the same defect as an unpinned block.
    ("optional dependency: `import pandas` / `pandas = None`", """
        try:
            import pandas
        except ImportError:
            pandas = None
    """),
    # --- round three: arity discriminates a clock read from a formatter ----
    # time.strftime/asctime/ctime read the wall clock ONLY in their no-arg
    # form. Given a time tuple or an epoch they are pure formatters, and
    # flagging those would forbid ordinary rendering code. This is the
    # ablation for the arity check: an implementation that adds the names to
    # FORBIDDEN_REFS without looking at the call reddens all three.
    ("time.strftime WITH a time tuple is a pure formatter", """
        import time


        def render(struct):
            return time.strftime("%Y-%m-%d", struct)
    """),
    ("time.asctime WITH a time tuple is a pure formatter", """
        import time


        def render(struct):
            return time.asctime(struct)
    """),
    ("time.ctime WITH an epoch argument is a pure formatter", """
        import time


        def render(secs):
            return time.ctime(secs)
    """),
    ("pd.Timestamp(value) constructs, it does not read the clock", """
        import pandas as pd


        def at(value):
            return pd.Timestamp(value)
    """),
    # gmtime and localtime belong to the same arity family as ctime/asctime —
    # they read the clock ONLY with no argument — but sat unconditionally in
    # the forbidden refs. The third case is the docstring's own justifying
    # example, which failed on itself: strftime with two args is correctly
    # pure, while the gmtime(0) feeding it was flagged, so the inconsistency
    # was visible in a single line. Twins of the bare `time.gmtime()` /
    # `time.localtime()` violations in CLOCK_SPELLING_CASES.
    ("time.gmtime(epoch) is a pure converter", """
        import time


        def stamp():
            return time.gmtime(0)
    """),
    ("time.localtime(epoch) is a pure converter", """
        import time


        def stamp(epoch):
            return time.localtime(epoch)
    """),
    ("time.strftime('%Y', time.gmtime(0)) — the docstring's own example", """
        import time


        def year():
            return time.strftime("%Y", time.gmtime(0))
    """),
    # --- round five: class-body fields that never USE the name -------------
    # Four shapes that reddened on the mere existence of an import/rebind pair.
    # None of them uses the name, so nothing resolves to the import and the
    # collision message is false. Twin of "class-body field annotated with the
    # import itself" in ENCLOSING_SCOPE_CASES, which DOES use it and stays a
    # violation — the annotation is the whole difference.
    ("class-body field with NO use (GUARD): @dataclass date: str", """
        from dataclasses import dataclass
        from datetime import date


        @dataclass
        class Bar:
            date: str
    """),
    ("class-body field with NO use (GUARD): NamedTuple time: str", """
        import time
        from typing import NamedTuple


        class Bar(NamedTuple):
            time: str
    """),
    ("class-body field with NO use (GUARD): Enum member named time", """
        import time
        from enum import Enum


        class Kind(Enum):
            time = 1
    """),
    ("class-body field with NO use (GUARD): method named date", """
        from datetime import date


        class Bar:
            def date(self):
                return None
    """),
    # Controls: these two were already clean and must stay so. The first is an
    # unwatched name, the second has no import to collide with — together they
    # keep the fix from being "stop checking class bodies".
    ("class-body control: run_date: str is an unwatched name", """
        from datetime import date


        class Bar:
            run_date: str
    """),
    ("class-body control: date: str with no import of `date`", """
        class Bar:
            date: str
    """),
    # --- a use that cannot reach a forbidden target is not a purity concern --
    # RECLASSIFIED from violation to clean, ruled 2026-08-25 when the collision
    # report was deleted. In both of these the use DOES reach the import — the
    # base list and the annotation are evaluated in the enclosing scope, before
    # the rebinding, exactly as the discriminator says. What the earlier
    # specification got wrong is the step after that: the thing reached is
    # `datetime.datetime` / `date`, which is not a forbidden target. No clock is
    # read, so there is no purity violation to report. The mechanism was right
    # and the consequence was wrong.
    #
    # The second was previously pinned as a violation on the reasoning "an
    # annotation is a use". It is ordinary pydantic. Meeting it as a corrected
    # decision is the point of this note — without it a future reader
    # rediscovers the argument and re-files it as a bug.
    ("base-class list reaches the import but not a forbidden target", """
        import datetime


        class Stamp(datetime.datetime):
            datetime = None
    """),
    ("ordinary pydantic: `date: date` (was mis-specified as a violation)", """
        from datetime import date


        class Bar(BaseModel):
            date: date
    """),
    # --- round four: two FALSE-POSITIVE GUARDS ----------------------------
    # Both of these read at a glance like violations — a name spelled `time`
    # with `.time()` called on it. They are not, and the lint reports 1 error
    # instead of 0 if either supporting block is removed. Named as guards on
    # purpose: the failure mode is a later reader "fixing" the lint to flag
    # them.
    #
    # GUARD 1. A sibling module named `time` is not the stdlib. An unresolved
    # relative import must bind the name to *nothing*, so it resolves to
    # nothing. Bind it to the bare alias instead and `time.time()` reads as
    # the stdlib clock, which is a false positive on ordinary package code.
    ("relative import of a sibling named `time` is not the stdlib (GUARD)", """
        from . import time


        def stamp():
            return time.time()
    """),
    # GUARD 2. Class scope is genuinely invisible to methods, so the method's
    # `time` is NOT the class's import — it is an unbound global. The same
    # rule that makes `class Row: date = None` fail to shadow a module-level
    # `from datetime import date` has to cut this way too, or the lint claims
    # a resolution Python does not perform.
    ("a class-body import is invisible to its own methods (GUARD)", """
        class C:
            import time

            def stamp(self):
                return time.time()
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
    }, RULE_SLACKKIT_INIT),
    ("__init__.py imports slack_sdk directly", {
        "slackkit/__init__.py": "import slack_sdk\n",
    }, RULE_SLACKKIT_INIT),
    ("__init__.py imports anything at all, even stdlib", {
        # Not about *what* is imported: an __init__ that executes imports is a
        # package that can grow one, which is how the boundary erodes.
        "slackkit/__init__.py": "import os\n",
    }, RULE_SLACKKIT_INIT),
]

SLACKKIT_REAL_VIOLATIONS = [
    ("pure package does `from slackkit.real import RealSlack`", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.real import RealSlack\n",
    }, RULE_FORBIDDEN_IMPORT),
    ("pure package does `import slackkit.real`", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "import slackkit.real\n",
    }, RULE_FORBIDDEN_IMPORT),
    ("dotted prefix reaches subpackages: slackkit.real.helpers", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit.real.helpers import retry\n",
    }, RULE_FORBIDDEN_IMPORT),
    ("`from slackkit import real` — the module is the package, not the name", {
        # The other three spellings all put "slackkit.real" in `node.module`,
        # so they never reach the per-alias check. This one is the natural way
        # to write it and is the only case that exercises that branch.
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": "from slackkit import real\n",
    }, RULE_FORBIDDEN_IMPORT),
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
    }, RULE_CLOCK_REF),
    ("slack_sdk imported by slackkit/outbox.py", {
        "slackkit/__init__.py": "",
        "slackkit/outbox.py": "import slack_sdk\n",
    }, RULE_FORBIDDEN_IMPORT),
    # D1 — prioritised. check_file skips `node.level != 0`, so a RELATIVE
    # import of .real is exempt. Every intra-package import in the real
    # slackkit/ is relative (outbox.py:13-14, fake.py:6, real.py:10), so the
    # exempted spelling is the only one a contributor would actually write.
    # A rule that catches the form nobody writes and misses the form everybody
    # writes has never been tested against the code it guards.
    ("D1: `from .real import RealSlack` inside slackkit/", {
        "slackkit/__init__.py": "",
        "slackkit/outbox.py": "from .real import RealSlack\n",
    }, RULE_FORBIDDEN_IMPORT),
    ("D2: `import slackkit` then slackkit.real.RealSlack()", {
        "slackkit/__init__.py": "",
        "orchestrator/daily.py": """
            import slackkit


            def post():
                return slackkit.real.RealSlack()
        """,
    }, RULE_FORBIDDEN_IMPORT),
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

# The relative-import guard again, this time through main() so the file sits in
# a REAL package and the import resolves to `state.time` rather than to
# nothing. The CLEAN_CASES twin covers the unresolved (package-unknown) half;
# this covers the resolved half, which is the spelling a contributor writes.
PACKAGE_RELATIVE_CLEAN_TREES = [
    ("sibling module named `time` imported relatively (GUARD)", {
        "state/__init__.py": "",
        "state/helpers.py": """
            from . import time


            def stamp():
                return time.time()
        """,
    }),
    ("class-body import inside a real package (GUARD)", {
        "state/__init__.py": "",
        "state/helpers.py": """
            class C:
                import time

                def stamp(self):
                    return time.time()
        """,
    }),
]

# --- deduplication: the ONLY count assertions in this file -----------------
#
# Every other assertion here is existence-based — "did rule X fire" — which is
# deliberate and must stay that way, because it lets an implementer reword or
# re-route a message without breaking a test. That design is structurally blind
# to one thing: the SAME violation reported twice.
#
# THREE blocks in the lint exist only to deduplicate — this said "two" until a
# third, the Store-context filter, was found unpinned by exactly the gap this
# comment was written to close. A comprehension's outermost iterable, a
# parameter's annotation, and an alias capture's Store target are each reachable
# by two paths through the scanner, and each block suppresses the second. Delete
# any of them and the lint
# stays correct about WHAT is wrong while printing it twice — so no existence
# check moves, the ablation scores the block as dead, and the next person
# removes it. The only symptom is doubled output that nobody traces back. That
# is the same unverified-purpose failure this lane has spent its length
# closing, with a cosmetic blast radius instead of a correctness one.
#
# SCOPE, deliberately narrow (ruled 2026-08-25): these minimal snippets assert
# DEDUPLICATION, not a general contract on error counts. Each is chosen to
# produce exactly one violation by exactly one rule, so the count is
# unambiguous. If a future rule legitimately fires twice on one of these
# snippets, the right fix is to update THESE CASES deliberately — not to loosen
# them back to existence checks, and not to freeze counts anywhere else in this
# file.
DEDUP_CASES = [
    ("comprehension's outermost iterable, reported once", """
        import time


        class C:
            vals = [x for x in time.time()]
    """, 1),
    ("parameter annotation, reported once", """
        import time


        def f(x: time.time = None):
            pass
    """, 1),
    # The third block: the Store-context filter. Without it the alias's Store
    # target is scanned too, resolves through alias propagation to time.sleep,
    # and the same violation is reported twice. Measured 1 -> 2. (A chained
    # `_a = _b = time.sleep` does NOT double, so it would not pin this.)
    ("alias capture, reported once", """
        import time

        _sleep = time.sleep
    """, 1),
]


# --- KNOWN FALSE POSITIVES: accepted wrong verdicts, not guards -------------
#
# READ THIS BEFORE ASSUMING ANYTHING HERE IS CORRECT BEHAVIOUR. Every case in
# this table asserts a verdict the lint currently gives and that we have ruled
# is WRONG. They are here so the cost is visible and so that the day the
# precondition becomes reachable, these are the cases that must change. They
# are deliberately NOT in CLEAN_CASES or in any violation table — a case that
# pins an accepted defect must not be mistakable for one that pins correct
# behaviour.
#
# KFP-1: the function-body import exception is not flow-sensitive. An `import
# time` in one branch binds `time` for the whole body, so a LATER, unrelated
# `time = clock` is read as still holding the module — and the lint flags the
# injected Clock, the one thing this lint exists to require. `master=clean,
# candidate=FLAG`. Two errors are reported: `time.time` is a true positive,
# `time.monotonic` is the false one.
#
# RULED: accept it; do NOT make the exception flow-sensitive. Not because the
# shape is contrived — that argument was rejected — but because the precondition
# cannot arrive silently. Checked against the tree, not reasoned: there are ZERO
# function-body imports of time/datetime/random/os across all six pure packages,
# and the real idiom here binds the RESULT (`now = clock.now()`), never a
# module-shadowing name. The false positive is unreachable without a
# function-body import of a forbidden module, and that import is itself an event
# the lint already flags or excepts.
#
# THE REMEDY, if it ever becomes reachable: make the function-body import
# exception flow-sensitive, so a binding established after the import supersedes
# it. Verified precondition — the identical function with the function-body
# import removed is clean, so the import is what triggers it.
KNOWN_FALSE_POSITIVE_CASES = [
    ("KFP-1: function-body import flags a later injected Clock", """
        def render(clock, fmt):
            if fmt == "epoch":
                import time
                return time.time()
            time = clock
            return time.monotonic()
    """, RULE_CLOCK_REF),
]


# --- assertions ------------------------------------------------------------

def _assert_all_flagged(cases, label):
    """Each case is (name, source, rule). Reports "not caught at all" and
    "caught by the wrong rule" separately — they are different bugs, and
    collapsing them is what let a deleted rule score as covered."""
    missed, wrong = [], []
    for name, src, rule in cases:
        errors = _errors_for(src)
        if not errors:
            missed.append(name)
        elif not any(rule in e for e in errors):
            wrong.append(f"{name} -> wanted {rule!r}, got {errors}")
    assert not (missed or wrong), "; ".join(filter(None, [
        (f"{label}: reported CLEAN for {len(missed)} of {len(cases)} case(s): "
         + "; ".join(missed)) if missed else "",
        (f"{label}: flagged by the WRONG RULE for {len(wrong)} of {len(cases)} "
         f"case(s) — the violation was caught, but not by the rule under test, "
         f"so deleting that rule would not turn this red: " + "; ".join(wrong))
        if wrong else "",
    ]))


def _assert_trees_rejected(cases, label):
    """Each case is (name, tree, rule). Same two-way split as above."""
    passed, wrong = [], []
    for name, tree, rule in cases:
        code, out = _lint_run_tree(tree)
        if code == 0:
            passed.append(name)
        elif rule not in out:
            wrong.append(f"{name} -> wanted {rule!r}, got: {out.strip()}")
    assert not (passed or wrong), "; ".join(filter(None, [
        (f"{label}: exited 0 for {len(passed)} of {len(cases)} tree(s): "
         + "; ".join(passed)) if passed else "",
        (f"{label}: flagged by the WRONG RULE for {len(wrong)} of {len(cases)} "
         f"tree(s): " + "; ".join(wrong)) if wrong else "",
    ]))


def _assert_trees_accepted(cases, label):
    rejected = [name for name, tree in cases if _lint_exit_code_for_tree(tree) != 0]
    assert not rejected, (
        f"{label}: purity lint exited non-zero for {len(rejected)} of "
        f"{len(cases)} legitimate tree(s): " + "; ".join(rejected))


def _assert_error_count(cases, label):
    """Exact error counts. Used ONLY by DEDUP_CASES — see the scope note there
    before adding a caller."""
    wrong = []
    for name, src, want in cases:
        errors = _errors_for(src)
        if len(errors) != want:
            wrong.append(f"{name} -> wanted {want}, got {len(errors)}: {errors}")
    assert not wrong, (
        f"{label}: {len(wrong)} of {len(cases)} case(s) reported the wrong "
        f"number of errors. The same violation reported twice is a dedup "
        f"block gone missing, not a new violation: " + "; ".join(wrong))


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
    for name, source, rule in EVASION_CASES:
        code, out = _lint_run_tree({"gate/sample.py": source})
        assert code != 0, f"gate/ evasion '{name}' left the lint exit 0"
        assert rule in out, (
            f"gate/ evasion '{name}' exited non-zero but not via {rule!r}: {out.strip()}")


def test_market_is_linted():
    """market/ computes the gate's own inputs; it is currently unlinted."""
    assert (ROOT / "market").is_dir(), "market/ is gone — retarget this test"
    code, out = _lint_run_tree({"market/sample.py": "import anthropic\n"})
    assert code != 0, "a forbidden import in market/ left the lint exit 0"
    assert RULE_FORBIDDEN_IMPORT in out, (
        f"market/ was scanned but not via {RULE_FORBIDDEN_IMPORT!r}: {out.strip()}")


def test_master_regressions_stay_flagged():
    """Master flagged all three. A binding rewrite that loses them has made
    the lint looser than the thing it replaced."""
    _assert_all_flagged(MASTER_REGRESSION_CASES, "regression vs master")


def test_a_rebound_import_still_resolves_for_the_reference_check():
    """Popping a name on any rebinding, anywhere in the scope, regardless of
    whether the branch can execute, is a one-line silencer for a whole file."""
    _assert_all_flagged(BINDING_STANDS_CASES, "binding stands")


def test_clock_spellings_are_flagged():
    _assert_all_flagged(CLOCK_SPELLING_CASES, "clock spelling")


def test_dynamic_import_spellings_are_flagged():
    _assert_all_flagged(DYNAMIC_IMPORT_SPELLING_CASES, "dynamic import spelling")


def test_captured_callables_are_flagged():
    """Capturing the function instead of calling it. Reads as careful seam
    code; is the banned dependency."""
    _assert_all_flagged(CALLABLE_CAPTURE_CASES, "callable capture")


def test_enclosing_scope_positions_are_flagged():
    """Class bodies, defaults, annotations and base lists are evaluated in the
    enclosing scope, so a later rebinding does not shadow the import."""
    _assert_all_flagged(ENCLOSING_SCOPE_CASES, "enclosing scope")


def test_slackkit_init_must_stay_import_free():
    _assert_trees_rejected(SLACKKIT_INIT_VIOLATIONS, "slackkit/__init__.py")


def test_slackkit_real_is_out_of_bounds_for_pure_packages():
    _assert_trees_rejected(SLACKKIT_REAL_VIOLATIONS, "slackkit.real")


def test_known_false_positives_still_fire():
    """NOT a correctness test. These are accepted WRONG verdicts, kept visible
    so their cost is legible and so the fix has a home if the precondition ever
    becomes reachable. If one of these starts passing, the lint improved —
    move the case, do not delete it. Read KNOWN_FALSE_POSITIVE_CASES first."""
    _assert_all_flagged(KNOWN_FALSE_POSITIVE_CASES, "known false positive")


def test_one_violation_is_reported_once():
    """Pins the two deduplication blocks, which no existence check can reach.
    Read the scope note on DEDUP_CASES before changing either number."""
    _assert_error_count(DEDUP_CASES, "deduplication")


def test_relative_and_class_body_imports_are_not_the_stdlib():
    """False-positive guards, through main() so package resolution is real.
    Both shapes look like violations and are not."""
    _assert_trees_accepted(PACKAGE_RELATIVE_CLEAN_TREES, "package-relative guard")


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

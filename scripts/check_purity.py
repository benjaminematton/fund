#!/usr/bin/env python3
"""Purity lint (CI-enforced; see CLAUDE.md invariant 3 and specs/design.md §4).

Checks over the no-LLM business-logic packages listed in PURE_PACKAGES:

  1. Forbidden imports: LLM/SDK/Slack code (claude_agent_sdk, anthropic,
     slack_bolt, slack_sdk), anything from agents/, and `slackkit.real`.
     Matching is dotted-prefix, not root-module: `slackkit.real.helpers` is
     forbidden, `slackkit.realtime` and `slackkit.outbox` are not.
  2. No dynamic imports: importlib.import_module() and __import__(). A pure
     package has to stay statically analyzable, and the argument may be
     computed ("claude" + "_agent_sdk"), so the call itself is the violation.
  3. No wall clock: datetime.now()/utcnow()/today(), date.today(), and the
     time module's clock and sleep functions — time is an injected Clock
     (design.md §4 Testability).
  4. slackkit/__init__.py stays import-free, so `import slackkit` executes
     nothing (slackkit/real.py:1-3). That file is NOT on its own sufficient to
     keep slack_sdk out of linted code — it never stops `from slackkit.real
     import RealSlack`. Rule 1's `slackkit.real` entry is what stops that, and
     `slackkit/` is itself linted as a pure package with real.py excluded,
     because real.py is the one file that is supposed to hold the SDK.

Matching is on *resolved bindings*, not source spellings, and on *references*,
not only calls:

  * `import time as _t; _t.sleep(1)` and `from time import sleep; sleep(1)`
    resolve to time.sleep and are caught.
  * `_sleep = time.sleep`, `{"ts": datetime.utcnow}` and `getattr(time,
    "sleep")` are caught without ever being called — capturing the callable is
    the same banned dependency as calling it.
  * A parameter or local named `time`/`datetime` shadows the module and is
    left alone, which is what keeps the injected `self._clock.now()` green.

Zero dependencies. Exit 1 on any violation. Directories that don't exist yet
(e.g. gate/ before Phase 2) are skipped — add code, inherit the check.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PURE_PACKAGES = ["gate", "stratgate", "fundbt", "calibration", "orchestrator",
                 "state", "market", "slackkit"]
# Paths (relative to ROOT, posix) inside a pure package that are exempt.
EXCLUDED_FILES = {"slackkit/real.py"}
# Matched as dotted prefixes: "agents" covers agents.runtime, and
# "slackkit.real" covers slackkit.real.helpers but not slackkit.realtime.
FORBIDDEN_IMPORTS = ("claude_agent_sdk", "anthropic", "slack_bolt", "slack_sdk",
                     "agents", "slackkit.real")
# Fully-qualified names that read the wall clock or block the thread. Flagged
# on *reference*, not just on call.
FORBIDDEN_REFS = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today",
    "datetime.date.today",
    "time.sleep", "time.time", "time.time_ns", "time.monotonic",
    "time.monotonic_ns", "time.perf_counter", "time.perf_counter_ns",
    "time.process_time", "time.localtime", "time.gmtime",
    "asyncio.sleep",
}
# Fully-qualified names that import at runtime, defeating this lint entirely.
DYNAMIC_IMPORT_REFS = {"importlib.import_module", "importlib.__import__",
                       "builtins.__import__", "__import__"}

# Nodes that open a new binding scope. Comprehensions are included: their
# targets are invisible outside them, so folding one into the parent would let
# `[None for time in ()]` silence a whole module.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _forbidden_import(name: str) -> str | None:
    """Dotted-prefix match against FORBIDDEN_IMPORTS.

    Boundary-aware on purpose: a raw `name.startswith("slackkit.real")` would
    also redden `slackkit.realtime`. Root-module comparison is the opposite
    error — it can never match a dotted entry at all, which would leave
    "slackkit.real" sitting in the tuple looking like a rule and matching
    nothing, forever.
    """
    for entry in FORBIDDEN_IMPORTS:
        if name == entry or name.startswith(entry + "."):
            return entry
    return None


def _dotted(node: ast.AST) -> str | None:
    """A Name/Attribute chain as "a.b.c"; anything else -> None.

    `getattr(x, "lit")` is folded in as a spelling of `x.lit`, so a string
    lookup is not a way around an attribute match.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)):
        base = _dotted(node.args[0])
        return f"{base}.{node.args[1].value}" if base else None
    return None


def _candidates(dotted: str, bindings: dict[str, str | None],
                stars: list[str]) -> list[str]:
    """Fully-qualified names `dotted` could refer to in this scope.

    A name present in `bindings` with value None is bound to something
    unresolvable (a parameter, a local, a relative import) — it resolves to
    nothing and must NOT fall through to the star-import guess.
    """
    head, _, rest = dotted.partition(".")
    if head in bindings:
        base = bindings[head]
        if base is None:
            return []
        return [f"{base}.{rest}" if rest else base]
    return [f"{module}.{dotted}" for module in stars]


def _import_bindings(node: ast.Import | ast.ImportFrom) -> dict[str, str | None]:
    """{local name: fully-qualified target}. None means "bound to something
    unresolvable" — a relative import — which must clear an inherited binding
    of that name rather than leave it standing."""
    out: dict[str, str | None] = {}
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                out[alias.asname] = alias.name
            else:
                root = alias.name.split(".")[0]
                out[root] = root
    elif node.level:  # `from .x import y` — not a resolvable absolute path
        for alias in node.names:
            out[alias.asname or alias.name] = None
    else:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue  # binds names, but not the literal name "*"
            name = alias.asname or alias.name
            out[name] = f"{module}.{alias.name}" if module else alias.name
    return out


def _bound_names(node: ast.AST) -> set[str]:
    """Names this node binds by means other than an import: assignment, walrus,
    parameter, def/class, `except ... as`, `with ... as`, comprehension or
    match/case capture."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, ast.arg):
        return {node.arg}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    return set()


def _scope_nodes(node: ast.AST):
    """Descendants of `node` belonging to its own scope. Nested scopes are
    yielded but not descended into; they are scanned separately."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPE_NODES):
            yield from _scope_nodes(child)


def _scan_scope(node: ast.AST, inherited: dict[str, str | None],
                inherited_stars: list[str], path: Path, errors: list[str]) -> None:
    own = list(_scope_nodes(node))
    bindings = dict(inherited)
    stars = list(inherited_stars)

    imported: dict[str, str | None] = {}
    for child in own:
        if isinstance(child, ast.ImportFrom) and any(a.name == "*" for a in child.names):
            if child.level == 0 and child.module:
                stars.append(child.module)
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            imported.update(_import_bindings(child))

    rebound: dict[str, int] = {}
    for child in own:
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            continue
        for name in _bound_names(child):
            rebound.setdefault(name, getattr(child, "lineno", 0))

    bindings.update(imported)

    # An import binding rebound in the SAME scope is incoherent code, and
    # popping the name instead would make `import time` + `if False: time =
    # None` a silent, condition-independent off switch for the whole scope. So
    # it is a loud error, not a shadow.
    #
    # Deliberately does not fire on a rebinding in a NESTED scope: a
    # function-local assignment makes the name local for that whole body, so a
    # use there resolves to the local object or raises UnboundLocalError —
    # never silently to the stdlib module. That exemption is not a hole in the
    # collision rule. Where the local object is itself a forbidden binding, the
    # reference check below catches it as a callable capture instead, which is
    # the family it belongs to: `_s = time.sleep`, or `dt = datetime` followed
    # by `dt.datetime.now()`, are both flagged there.
    #
    # What neither rule reaches is a capture whose source is not statically
    # resolvable — `datetime = some_free_name`, or a clock handed in as a
    # parameter. That is the ordinary limit of resolving names without running
    # the program, not a decision taken here; do not read it as a ruling.
    for name, lineno in sorted(rebound.items(), key=lambda item: item[1]):
        if name in imported:
            errors.append(f"{path}:{lineno}: '{name}' is imported and rebound in "
                          f"the same scope — an import binding may not be "
                          f"reassigned; it disables the purity check silently")
        else:
            bindings[name] = None

    # `_clock_mod = time` / `_sleep = time.sleep`: an alias is the thing it
    # aliases. Runs after the pass above so a real alias beats the None.
    for child in own:
        if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            dotted = _dotted(child.value)
            hits = _candidates(dotted, bindings, stars) if dotted else []
            if len(hits) == 1:
                bindings[targets[0].id] = hits[0]

    for child in own:
        if isinstance(child, (ast.Name, ast.Attribute)) and not isinstance(child.ctx, ast.Load):
            continue
        if not isinstance(child, (ast.Name, ast.Attribute, ast.Call)):
            continue
        dotted = _dotted(child)
        if not dotted:
            continue
        for target in _candidates(dotted, bindings, stars):
            if target in FORBIDDEN_REFS:
                errors.append(f"{path}:{child.lineno}: wall-clock/sleep reference "
                              f"'{dotted}' resolves to {target} — inject Clock instead")
            elif target in DYNAMIC_IMPORT_REFS:
                errors.append(f"{path}:{child.lineno}: dynamic import '{dotted}' — "
                              f"a pure package must be statically analyzable")

    # Class scope is not visible inside methods (`class Row: date = None` does
    # not shadow a module-level `from datetime import date` for `Row.stamp`),
    # so nested scopes inherit from the class's *enclosing* scope.
    child_bindings = inherited if isinstance(node, ast.ClassDef) else bindings
    child_stars = inherited_stars if isinstance(node, ast.ClassDef) else stars
    for child in own:
        if isinstance(child, _SCOPE_NODES):
            _scan_scope(child, child_bindings, child_stars, path, errors)


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_import(alias.name):
                    errors.append(f"{path}:{node.lineno}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if _forbidden_import(module):
                errors.append(f"{path}:{node.lineno}: forbidden import 'from {module}'")
            else:
                for alias in node.names:
                    if alias.name != "*" and _forbidden_import(f"{module}.{alias.name}"):
                        errors.append(f"{path}:{node.lineno}: forbidden import "
                                      f"'from {module} import {alias.name}'")
    _scan_scope(tree, {"__import__": "__import__"}, [], path, errors)
    return errors


def check_slackkit_init(root: Path) -> list[str]:
    """slackkit/__init__.py must execute no imports at all — not just no
    forbidden ones. An __init__ that imports is one that can grow a slack_sdk
    import, and every `import slackkit` would inherit it. This is a narrower
    claim than it looks: it does nothing about `from slackkit.real import X`,
    which FORBIDDEN_IMPORTS covers instead."""
    init = root / "slackkit" / "__init__.py"
    if not init.is_file():
        return []
    tree = ast.parse(init.read_text(), filename=str(init))
    return [f"{init}:{node.lineno}: slackkit/__init__.py must stay import-free "
            f"so `import slackkit` executes nothing (slackkit/real.py:1-3)"
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))]


def main() -> int:
    errors: list[str] = []
    checked = 0
    for pkg in PURE_PACKAGES:
        pkg_dir = ROOT / pkg
        if not pkg_dir.is_dir():
            continue
        for py in sorted(pkg_dir.rglob("*.py")):
            if py.relative_to(ROOT).as_posix() in EXCLUDED_FILES:
                continue
            checked += 1
            errors.extend(check_file(py))
    errors.extend(check_slackkit_init(ROOT))
    if errors:
        print(f"PURITY LINT: {len(errors)} violation(s) in {checked} file(s):")
        print("\n".join(errors))
        return 1
    print(f"PURITY LINT: clean ({checked} files across "
          f"{[p for p in PURE_PACKAGES if (ROOT / p).is_dir()]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

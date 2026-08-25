#!/usr/bin/env python3
"""Purity lint (CI-enforced; see CLAUDE.md invariant 3 and specs/design.md §4).

Three checks over the no-LLM business-logic packages:
  1. Forbidden imports: gate/, stratgate/, fundbt/, calibration/, orchestrator/,
     state/ and market/ must not import LLM/SDK/Slack code (claude_agent_sdk,
     anthropic, slack_bolt, slack_sdk) nor anything from agents/. Dynamic
     imports (importlib.import_module, __import__) are forbidden outright: a
     pure package has to stay statically analyzable, and the argument may be
     computed ("claude" + "_agent_sdk").
  2. No wall clock: datetime.now()/utcnow(), date.today(), time.sleep()/time()/
     monotonic()/perf_counter(), asyncio.sleep() — time is an injected Clock
     (design.md §4 Testability).
  3. slackkit/__init__.py stays import-free. slackkit is in neither list above:
     orchestrator legitimately imports slackkit.outbox, and slackkit/real.py
     legitimately holds slack_sdk. The empty __init__.py is the entire mechanism
     keeping the two apart, and slackkit/real.py:1-3 says so.

Calls are matched on the *binding a name resolves to*, not on its source
spelling, so `import time as _t; _t.sleep(1)` and `from time import sleep;
sleep(1)` are caught while a local or parameter named `time`/`datetime` — and
above all the injected `self._clock.now()` — are not.

Zero dependencies. Exit 1 on any violation. Directories that don't exist yet
(e.g. gate/ before Phase 2) are skipped — add code, inherit the check.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PURE_PACKAGES = ["gate", "stratgate", "fundbt", "calibration", "orchestrator",
                 "state", "market"]
FORBIDDEN_IMPORTS = ("claude_agent_sdk", "anthropic", "slack_bolt", "slack_sdk", "agents")
# Fully-qualified callables that read the wall clock or block the thread.
FORBIDDEN_CALL_TARGETS = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.date.today",
    "time.sleep", "time.time", "time.monotonic", "time.perf_counter",
    "asyncio.sleep",
}
# Fully-qualified callables that import at runtime, defeating this lint.
DYNAMIC_IMPORT_TARGETS = {"importlib.import_module", "importlib.__import__", "__import__"}

# Nodes that open a new binding scope; their bodies are scanned separately.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` as a Name/Attribute chain -> "a.b.c"; anything else -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _resolve(dotted: str, bindings: dict[str, str]) -> str | None:
    """Rewrite a dotted expression through the local name -> module bindings."""
    head, _, rest = dotted.partition(".")
    target = bindings.get(head)
    if target is None:
        return None
    return f"{target}.{rest}" if rest else target


def _import_bindings(node: ast.Import | ast.ImportFrom) -> dict[str, str | None]:
    """{local name: fully-qualified target}. None means "bound to something
    unresolvable" — a relative import — which must clear any inherited binding
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
            name = alias.asname or alias.name
            out[name] = f"{module}.{alias.name}" if module else alias.name
    return out


def _bound_names(node: ast.AST) -> set[str]:
    """Names this node binds by means other than an import: assignment,
    parameter, def/class, `except ... as`, comprehension or `with` target."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, ast.arg):
        return {node.arg}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    return set()


def _scope_nodes(node: ast.AST):
    """Descendants of `node` belonging to its own scope — nested defs, lambdas
    and classes are yielded but not descended into."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPE_NODES):
            yield from _scope_nodes(child)


def _scan_scope(node: ast.AST, inherited: dict[str, str], path: Path,
                errors: list[str]) -> None:
    own = list(_scope_nodes(node))
    bindings = dict(inherited)
    for child in own:
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            for name, target in _import_bindings(child).items():
                if target is None:
                    bindings.pop(name, None)
                else:
                    bindings[name] = target
    # A local binding of the same name shadows the module: `def elapsed(time)`
    # is a caller's Timer, not the stdlib.
    for child in own:
        if not isinstance(child, (ast.Import, ast.ImportFrom)):
            for name in _bound_names(child):
                bindings.pop(name, None)

    for child in own:
        if isinstance(child, ast.Call):
            dotted = _dotted(child.func)
            target = _resolve(dotted, bindings) if dotted else None
            if target in FORBIDDEN_CALL_TARGETS:
                errors.append(f"{path}:{child.lineno}: wall-clock/sleep call "
                              f"'{dotted}()' resolves to {target}() — "
                              f"inject Clock instead")
            elif target in DYNAMIC_IMPORT_TARGETS:
                errors.append(f"{path}:{child.lineno}: dynamic import "
                              f"'{dotted}()' — a pure package must be "
                              f"statically analyzable")
        elif isinstance(child, _SCOPE_NODES):
            _scan_scope(child, bindings, path, errors)


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    errors.append(f"{path}:{node.lineno}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level == 0 and root in FORBIDDEN_IMPORTS:
                errors.append(f"{path}:{node.lineno}: forbidden import 'from {node.module}'")
    _scan_scope(tree, {"__import__": "__import__"}, path, errors)
    return errors


def check_slackkit_init(root: Path) -> list[str]:
    """slackkit/__init__.py must execute no imports at all — not just no
    forbidden ones. An __init__ that imports is one that can grow a slack_sdk
    import, and every linted `from slackkit.outbox import ...` would inherit it.
    Deliberately does not lint the rest of slackkit/: real.py is *supposed* to
    hold the SDK."""
    init = root / "slackkit" / "__init__.py"
    if not init.is_file():
        return []
    tree = ast.parse(init.read_text(), filename=str(init))
    return [f"{init}:{node.lineno}: slackkit/__init__.py must stay import-free "
            f"— it is what lets linted code import slackkit.outbox without "
            f"pulling in slack_sdk (slackkit/real.py:1-3)"
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

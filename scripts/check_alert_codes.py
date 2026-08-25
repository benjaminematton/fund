#!/usr/bin/env python3
"""Alert-code lint (CI-enforced; see docs/agents/devops.md).

Every kind='alert' row must carry a stable `code`, because that code is what
scripts/file_alert_issues.py keys a GitHub issue on. A site that forgets one
still alerts and still reaches Slack, and is silently invisible to the filer
forever — absence reading as health, the failure shape this repo keeps hitting.

Two checks:
  1. No append_event(..., 'alert', ...) anywhere. Use append_alert().
  2. Every alert-raising call passes a string-literal code matching
     ^[a-z][a-z0-9_]*$. An f-string code is unbounded and would file an issue
     per run.

Zero dependencies. Exit 1 on any violation.

**The pass-through rule.** scripts/run_day.py keeps a thin `_alert(conn, clock,
code, text, **payload)` wrapper that logs and delegates, so its one
`append_alert(conn, code, ...)` call forwards a *variable* by construction.
Rather than exempt that file, the rule is general: a call is a forwarder, and
skipped, only when it passes the ENCLOSING function's own `code` parameter
through as the code argument. Declaring a `code` parameter is not enough — a
function that declares one and then passes something else is still checked,
so a wrapper cannot launder a dynamic code past the lint. `_alert` itself is
checked as an alert raiser, so its five call sites still owe a literal.

Known edge, documented rather than hidden: a NEW wrapper named something else
would have its own callers unchecked until its name is added to ALERT_FUNCS.
A second edge: a code passed by keyword (`append_alert(conn, code="x", ...)`)
finds no positional arg and is reported as a non-literal. That is a false
positive, and deliberately so — it fails CLOSED, at build time, the moment it
appears.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ["orchestrator", "agents", "scripts", "slackkit", "gate", "state",
            "market", "evals", "fundbt", "stratgate", "calibration", "ops"]
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALERT_FUNCS = ("append_alert", "_alert")


def _callee(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", None)


def _forwarded_calls(tree: ast.AST) -> set[int]:
    """Alert-raising calls that pass the enclosing function's own `code`
    parameter through, unchanged, as their code argument — a forwarder, not
    a dynamic code.

    Narrower than "any call inside a function declaring `code`": that
    exempted every call in such a function, including a second alert call
    built from an f-string that happens to sit next to the real forward.
    Only a call whose code-position argument (positional or `code=`
    keyword) is exactly a bare reference to the parameter is exempted."""
    forwarded: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in
                  fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
        if "code" not in params:
            continue
        for inner in ast.walk(fn):
            if not isinstance(inner, ast.Call):
                continue
            name = _callee(inner)
            if name not in ALERT_FUNCS:
                continue
            pos = 1 if name == "append_alert" else 2
            passed = inner.args[pos] if len(inner.args) > pos else None
            if passed is None:
                passed = next((kw.value for kw in inner.keywords
                              if kw.arg == "code"), None)
            if isinstance(passed, ast.Name) and passed.id == "code":
                forwarded.add(id(inner))
    return forwarded


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    forwarded = _forwarded_calls(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name == "append_event":
            kind = node.args[1] if len(node.args) > 1 else None
            if isinstance(kind, ast.Constant) and kind.value == "alert":
                errors.append(
                    f"{path}:{node.lineno}: append_event(..., 'alert', ...) —"
                    " use append_alert() so the alert carries a code")
        elif name in ALERT_FUNCS:
            if id(node) in forwarded:
                continue                   # a wrapper forwarding its own code
            # append_alert(conn, code, text, ...)  -> args[1]
            # _alert(conn, clock, code, text, ...) -> args[2]
            pos = 1 if name == "append_alert" else 2
            code = node.args[pos] if len(node.args) > pos else None
            if not isinstance(code, ast.Constant) or not isinstance(code.value, str):
                errors.append(
                    f"{path}:{node.lineno}: {name} code must be a string"
                    " literal, not an expression — a dynamic code files an"
                    " issue per run")
            elif not CODE_RE.match(code.value):
                errors.append(
                    f"{path}:{node.lineno}: alert code {code.value!r} is not a"
                    " bare lower_snake identifier")
    return errors


def main() -> int:
    errors: list[str] = []
    checked = 0
    for package in PACKAGES:
        root = ROOT / package
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            checked += 1
            errors.extend(check_file(path))
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        print(f"ALERT CODE LINT: {len(errors)} violation(s)", file=sys.stderr)
        return 1
    print(f"ALERT CODE LINT: clean ({checked} files across {len(PACKAGES)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

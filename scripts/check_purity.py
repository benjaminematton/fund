#!/usr/bin/env python3
"""Purity lint (CI-enforced; see CLAUDE.md invariant 3 and specs/design.md §4).

Two checks over the no-LLM business-logic packages:
  1. Forbidden imports: gate/, stratgate/, fundbt/, calibration/, orchestrator/,
     and state/ must not import LLM/SDK/Slack code (claude_agent_sdk, anthropic,
     slack_bolt, slack_sdk) nor anything from agents/.
  2. No wall clock: no datetime.now()/datetime.utcnow()/date.today()/time.sleep()
     in business logic — time is an injected Clock (design.md §4 Testability).

Zero dependencies. Exit 1 on any violation. Directories that don't exist yet
(e.g. gate/ before Phase 2) are skipped — add code, inherit the check.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PURE_PACKAGES = ["gate", "stratgate", "fundbt", "calibration", "orchestrator", "state"]
FORBIDDEN_IMPORTS = ("claude_agent_sdk", "anthropic", "slack_bolt", "slack_sdk", "agents")
# (module, attr) call patterns that read the wall clock / block the thread
FORBIDDEN_CALLS = {
    ("datetime", "now"), ("datetime", "utcnow"), ("date", "today"), ("time", "sleep"),
}


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
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else None)
            if base_name and (base_name, attr) in FORBIDDEN_CALLS:
                errors.append(f"{path}:{node.lineno}: wall-clock/sleep call "
                              f"'{base_name}.{attr}()' — inject Clock instead")
    return errors


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
    if errors:
        print(f"PURITY LINT: {len(errors)} violation(s) in {checked} file(s):")
        print("\n".join(errors))
        return 1
    print(f"PURITY LINT: clean ({checked} files across "
          f"{[p for p in PURE_PACKAGES if (ROOT / p).is_dir()]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

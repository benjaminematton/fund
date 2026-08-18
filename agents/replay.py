"""Replay mode (acceptance §0): feed recorded tool-call decisions through the
REAL hooks and REAL tool executors against a temp DB + FakeSlack/FakeAlpaca.
The LLM is the only thing replaced."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Callable


def load_recording(path: str | Path) -> list[dict]:
    lines = Path(path).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def replay_turn(decisions: list[dict], *, pre_hooks: list,
                      executor: Callable[[str, dict], object],
                      post_hooks: list) -> list[dict]:
    """Run recorded decisions through the real hooks, in order. Returns one
    outcome per decision: {"tool", "denied"} when a PreToolUse hook denied it,
    {"tool", "result"} otherwise."""
    outcomes: list[dict] = []
    for i, d in enumerate(decisions):
        input_data = {"tool_name": d["tool"], "tool_input": d["args"]}
        denied = None
        for hook in pre_hooks:
            out = await hook(input_data, f"replay-{i}", None)
            spec = (out or {}).get("hookSpecificOutput", {})
            if spec.get("permissionDecision") == "deny":
                denied = spec.get("permissionDecisionReason", "denied")
                break
        if denied is not None:
            outcomes.append({"tool": d["tool"], "denied": denied})
            continue
        result = executor(d["tool"], d["args"])
        if inspect.isawaitable(result):
            result = await result
        post_input = dict(input_data, tool_response=result)
        for hook in post_hooks:
            await hook(post_input, f"replay-{i}", None)
        outcomes.append({"tool": d["tool"], "result": result})
    return outcomes

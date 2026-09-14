"""Content-addressed identity (strategy-contracts.md §1). The ONLY hashing module."""

from __future__ import annotations

import hashlib
import json


def canonical_json(obj) -> str:
    """Sorted keys, no whitespace, shortest-repr floats (json default)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _h(prefix: str, payload: str) -> str:
    return prefix + hashlib.sha256(payload.encode()).hexdigest()[:16]


def spec_id(spec_fields: dict) -> str:
    return _h("spec_", canonical_json(spec_fields))


def config_hash(spec: str, params: dict) -> str:
    return _h("cfg_", canonical_json({"spec_id": spec, "params": params}))


def run_key(cfg_hash: str, data_snapshot_hash: str, engine_version: str, seed: int) -> str:
    return _h("run_", cfg_hash + data_snapshot_hash + engine_version + str(seed))


def work_id(kind: str, subject: str, dedupe_key: str) -> str:
    """worklist row identity (contracts.md §2). Same (kind, subject, dedupe_key)
    -> same id, so a second enqueue is a no-op rather than a duplicate."""
    return _h("wk_", canonical_json({"kind": kind, "subject": subject,
                                     "dedupe_key": dedupe_key}))

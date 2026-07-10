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

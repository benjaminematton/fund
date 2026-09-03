#!/usr/bin/env python3
"""Prove the Alpaca MCP server still IMPORTS at the spec the seats launch.

    make mcp-probe

WHY THIS EXISTS. On 2026-08-31 `alpaca-mcp-server@2.2.1` — an exact pin —
stopped working because `fastmcp` is NOT pinned by it (`fastmcp>=3.1.0`, no
upper bound). uv resolved fastmcp 4.0.2, which moved `fastmcp.tools.tool`;
`alpaca_mcp_server/security.py` imports it, so the server raised
ModuleNotFoundError at import. Every seat turn hit `required MCP server(s) not
connected after 30.0s: {'alpaca': 'failed'}` and defaulted to HOLD. The fund
placed no order for three days.

`make test` was green for all of it, and could not have been anything else: it
is offline by design and never launches this server. THIS is the check that
would have caught it, and it is deliberately NOT part of that suite — it needs
the network, and a network step inside `make test` would silently void an
offline guarantee several other things rest on.

WHY IT IMPORTS RATHER THAN STARTS THE SERVER. The obvious probe —
`uvx <spec> --transport stdio` — does not work without credentials, and was
written into the first draft of the design before being tested:

    $ uvx alpaca-mcp-server@2.2.1 --transport stdio    # BROKEN build
    Error: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.   exit=1
    $ uvx alpaca-mcp-server@2.3.1 --transport stdio    # WORKING build
    Error: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.   exit=1

The CLI validates credentials BEFORE importing `.server`, so broken and working
are byte-identical without them and the probe would have been green through the
whole outage. Importing the module directly skips the CLI's credential gate and
reaches the import that actually failed — which is why this runs
`python -c "import alpaca_mcp_server.server"` and not the entry point.

Demonstrated to discriminate, credential-free (this is the probe's manufactured
red, run before it was written):

    alpaca-mcp-server@2.2.1, unpinned  -> ModuleNotFoundError: fastmcp.tools.tool
    alpaca-mcp-server@2.3.1            -> IMPORT OK

WHAT IT DOES NOT PROVE. That the server runs, that its tools behave, or that the
broker surface has not moved — listing tools needs a live server and therefore
credentials. `make schema-pin` and `make surface-pin` remain the guards for
that, live and human-run. This proves the import, which is the failure that cost
three days.

The spec is read from agents/seats.py rather than restated here: a probe that
tests a launch nobody makes is the same defect in a new place.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The module whose import broke. Importing the PACKAGE is not enough —
# `alpaca_mcp_server/__init__.py` does not pull in `.server`, so a package
# import stays green on exactly the failure this exists to catch.
TARGET_MODULE = "alpaca_mcp_server.server"


def probe_argv() -> list[str]:
    """The uvx argv, derived from the same constant the seats launch with."""
    from agents.seats import ALPACA_MCP_SPEC

    argv = ["uvx"]
    try:                                    # present once Part 1 of the design lands
        from agents.seats import ALPACA_MCP_ARGS
    except ImportError:
        argv += ["--from", ALPACA_MCP_SPEC]
    else:
        # ALPACA_MCP_ARGS ends with the spec; --from takes its place so uvx runs
        # `python` from that package's environment instead of its entry point.
        argv += [a for a in ALPACA_MCP_ARGS if a != ALPACA_MCP_SPEC]
        argv += ["--from", ALPACA_MCP_SPEC]
    return argv + ["python", "-c", f"import {TARGET_MODULE}"]


def main() -> int:
    argv = probe_argv()
    print(" ".join(argv))
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        print("PROBE INCONCLUSIVE: uvx not on PATH — install uv", file=sys.stderr)
        return 2                            # not 1: nothing was proved either way
    except subprocess.TimeoutExpired:
        print("PROBE INCONCLUSIVE: uvx timed out after 300s", file=sys.stderr)
        return 2

    if done.returncode == 0:
        print(f"MCP IMPORT OK — {TARGET_MODULE} imports at the launched spec")
        return 0

    # Print the whole thing. The useful line is the ModuleNotFoundError, and
    # which module moved is the entire finding.
    print(f"MCP IMPORT FAILED (exit {done.returncode}) — the seats cannot start "
          "this server, and every seat turn will default to HOLD:", file=sys.stderr)
    sys.stderr.write(done.stdout)
    sys.stderr.write(done.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

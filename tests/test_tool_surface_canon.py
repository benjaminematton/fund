"""Contract test: the fund server's tool surface IS `specs/contracts.md` §4.

§4 is declared the single canonical enumeration of every tool the in-process
fund server serves. Nothing else compared the two, so a tool could be — and
`list_open_tickets` was — shipped with no canonical entry at all: it ran in
production for a phase while the file that is supposed to describe the whole
agent→state seam did not mention it.

The table is PARSED, never restated. A tool list typed out here would be a
third source of truth, and the failure mode of a third source of truth is that
it agrees with neither.

Three instruments, deliberately not one:

  * `_declared()` regexes `@tool("name"` out of `fund_server.py`'s source. It
    sees a tool that is registered but granted to NO seat — dead surface the
    served union below cannot see, because nothing serves it.
  * `_served()` builds a real server per seat and lists its tools through the
    MCP handler. It sees what a live seat can actually call, wrappers and all.
  * the §4 table's `seats` column, checked per tool against `_served()`.

The first two answer different questions and can disagree; asserting both
against the table is what makes "no tool may exist without a canonical entry"
mean the thing it says rather than "no GRANTED tool does".

Parsing is deferred out of the module body into `_canon()` so that a §4 table
this file cannot parse fails THIS file's tests instead of erroring collection
and taking the repo's suite down with it (same reasoning as
`test_schema_contract.py`, which parses §2's DDL).
"""

import pathlib
import re
import sqlite3

import pytest

from agents.tools.fund_server import SEAT_CAPS
# Imported, not re-implemented: `_handlers` carries the mcp 1.x/2.x shim for
# reaching the registered surface, and a second copy of it would rot the day
# the pin moves — which is precisely the day this test has to still work.
from tests.test_fund_tools import _handlers, _is_error, _server

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "contracts.md"

HEADER = ("tool", "seats", "schema", "status")

# One minimal payload per served tool, so the wrong-seat refusal is exercised
# through the real wrapper with an otherwise VALID call: an invalid payload
# would be refused for the wrong reason and the seat guard would never be
# reached. Asserted below to cover the canonical served set exactly, so a tool
# added to §4 cannot slip past the wrong-seat test by being forgotten here.
ARGS: dict[str, dict] = {
    "get_stage_brief": {},
    "get_spec_brief": {},
    "list_open_tickets": {},
    "submit_signal": {"ticker": "NVDA", "direction": "bullish",
                      "confidence": 72, "summary": "s"},
    "submit_decision": {"ticker": "NVDA", "action": "hold", "qty": 0,
                        "thesis": "t", "invalidation": "i"},
    "submit_spec_critique": {"spec_id": "spec-1", "verdict": "clear"},
    "submit_reflection": {"prose": "p"},
}


def _section_4() -> str:
    """The text of §4, from its heading to the next top-level heading."""
    text = CONTRACTS.read_text()
    start = text.find("\n## 4. ")
    if start < 0:
        raise ValueError(f"no '## 4. ' heading in {CONTRACTS}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def _canon(body: str | None = None) -> dict[str, dict]:
    """§4's tool table as {tool: {seats, schema, status}}.

    Every parse failure raises rather than returning a partial table: a table
    silently parsed as empty makes every equality below trivially assertable
    against an empty served set, which is the shape of a green test that has
    stopped testing. `body` is the §4 text, defaulting to the real file — it
    is a parameter so the parser's own failure modes can be driven directly,
    without a doctored copy of the spec on disk.
    """
    rows: dict[str, dict] = {}
    body = _section_4() if body is None else body
    # Located by its header, not by shape: §4 holds a second four-column table
    # (get_stage_brief's field/analyst/pm/source matrix), and a parser that
    # took any four columns read `field` as a tool.
    in_table = False
    for line in body.splitlines():
        line = line.strip()
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not in_table:
            in_table = tuple(cells) == HEADER
            continue
        if not line.startswith("|"):                         # table ended
            break
        if set(cells[0]) <= {"-", ":"}:                      # separator row
            continue
        if len(cells) != len(HEADER):
            raise ValueError(
                f"§4 tool table row has {len(cells)} cells, expected"
                f" {len(HEADER)}: {line!r}")
        tool = cells[0].strip("`")
        if not re.fullmatch(r"[a-z_]+", tool):
            raise ValueError(f"§4 tool table: {cells[0]!r} is not a tool name")
        seats = frozenset(s.strip().strip("`")
                          for s in cells[1].split(",") if s.strip())
        status = cells[3]
        if status != "served" and not status.startswith("not served"):
            raise ValueError(
                f"§4 row {tool!r}: status {status!r} is neither 'served' nor"
                " 'not served...' — an unrecognized status is a fail, never an"
                " interpretation")
        unknown = seats - set(SEAT_CAPS)
        if unknown:
            raise ValueError(
                f"§4 row {tool!r} names seats that do not exist:"
                f" {sorted(unknown)}")
        rows[tool] = {"seats": seats, "schema": cells[2], "status": status}
    if not rows:
        raise ValueError(f"no tool table parsed out of §4 in {CONTRACTS}")
    return rows


def _canon_served() -> set[str]:
    return {t for t, r in _canon().items() if r["status"] == "served"}


def _declared() -> set[str]:
    """Every `@tool("name"` in the fund server's source — including one no seat
    is granted, which no served-tool listing can see."""
    src = (ROOT / "agents" / "tools" / "fund_server.py").read_text()
    return set(re.findall(r'@tool\(\s*"([a-z_]+)"', src))


def _tool_names(conn, clock, seat) -> set[str]:
    import asyncio

    list_tools, _ = _handlers(_server(conn, clock, seat))
    return {t.name for t in asyncio.run(list_tools()).tools}


def _served(conn, clock) -> dict[str, set[str]]:
    """{tool: seats that can actually call it}, over every registered seat."""
    by_tool: dict[str, set[str]] = {}
    for seat in SEAT_CAPS:
        for name in _tool_names(conn, clock, seat):
            by_tool.setdefault(name, set()).add(seat)
    return by_tool


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%'")]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables}


def test_served_tool_set_is_exactly_the_section_4_table(fund_db, sim_clock):
    """The gap this test closes, in both directions. A tool served with no §4
    row is `list_open_tickets` before this commit — running in production,
    absent from the file that is supposed to enumerate the seam. A §4 row
    marked served with nothing behind it is the opposite failure: canon that
    describes a fund that does not exist."""
    assert set(_served(fund_db, sim_clock)) == _canon_served()


def test_every_registered_tool_has_a_canonical_entry(fund_db, sim_clock):
    """Registration, not grant. A tool added to `build_fund_server` but listed
    in no seat's caps is invisible to the served union above — it is dead
    surface today and one SEAT_CAPS line from live, with no canonical entry
    either way."""
    assert _declared() == _canon_served()


def test_each_tool_reaches_exactly_the_seats_the_table_names(fund_db,
                                                             sim_clock):
    """The `seats` column is canon, not commentary: it is the column that says
    which seat may write which state, and the prose it replaced had already
    drifted (it read 'analyst + PM only' for get_stage_brief while the news
    seat held it)."""
    served = _served(fund_db, sim_clock)
    for tool, row in _canon().items():
        # A row that is not served names the seats it WILL reach when it
        # ships; until then the only correct answer is nobody.
        want = set(row["seats"]) if row["status"] == "served" else set()
        assert served.get(tool, set()) == want, tool


def test_a_row_pointing_its_schema_elsewhere_points_somewhere_real():
    """§4 enumerates the G1 pair but leaves its schemas in strategy-contracts
    §3.4, which CLAUDE.md makes authoritative for them. A pointer is only worth
    more than a copy while it resolves."""
    for tool, row in _canon().items():
        if row["schema"] == "below":
            continue
        names = re.findall(r"[\w-]+\.md", row["schema"])
        assert names, f"{tool}: schema cell {row['schema']!r} names no file"
        for name in names:
            target = CONTRACTS.parent / name
            assert target.exists(), f"{tool}: {name} does not exist"
            assert tool in target.read_text(), f"{tool}: absent from {name}"


def test_a_row_claiming_its_schema_is_below_has_one_in_section_4():
    """'Each with a strict schema' — the half of §7's requirement a name-only
    enumeration would satisfy on paper. Two-sided: a schema block in §4 with no
    row is an entry that the enumeration does not enumerate."""
    below = {t for t, r in _canon().items() if r["schema"] == "below"}
    assert set(re.findall(r'@tool\(\s*"([a-z_]+)"', _section_4())) == below


def test_the_wrong_seat_payloads_cover_every_canonical_tool():
    """Guards the test below from the failure that would leave it green while
    testing less: a tool added to §4 and to the server, but not to ARGS, would
    simply not be checked for a seat guard."""
    assert set(ARGS) == _canon_served()


def _call(conn, clock, seat, tool, args):
    import asyncio

    _, call_tool = _handlers(_server(conn, clock, seat))
    return asyncio.run(call_tool(tool, args))


def test_a_wrong_seat_caller_gets_a_tool_error(fund_db, sim_clock):
    """Every (tool, seat-the-table-does-not-name) pair, through the real
    surface with no test seam at all: the call comes back an error and writes
    nothing. For a tool the seat was never granted the refusal comes from the
    MCP layer ("Tool 'x' not found"), which is a tool error the same way the
    handler's is — the seat is told its call did not land, which is the
    property that matters (a refusal reported as success would have the seat
    believe its turn landed and stop retrying).

    A not-served row is checked against EVERY seat: `submit_critique` is canon
    but Phase 3, so today no seat may call it.
    """
    for tool, row in _canon().items():
        granted = set(row["seats"]) if row["status"] == "served" else set()
        for seat in sorted(set(SEAT_CAPS) - granted):
            before = _row_counts(fund_db)
            result = _call(fund_db, sim_clock, seat, tool,
                           ARGS.get(tool, {}))
            assert _is_error(result), f"{tool} accepted a call from {seat}"
            assert _row_counts(fund_db) == before, f"{tool} wrote from {seat}"


# Which guard each handler puts BEHIND registration — a real asymmetry, named
# rather than smoothed over, because the two are reached in opposite ways.
# `list_open_tickets` compares the seat name directly (`seat != "exec"`), so it
# still refuses a cap table that has been widened by mistake but is unmoved by
# one that has been narrowed. Every other handler asks `_can(seat, cap)` — the
# same table registration derives from — so it is reached only by revoking the
# cap under a server already built. Neither technique fires on both, and a
# single-technique test would silently exempt whichever tool it does not fit.
SEAT_NAME_GUARDED = frozenset({"list_open_tickets"})


def test_the_handler_refuses_even_when_registration_is_wrong(fund_db,
                                                             sim_clock,
                                                             monkeypatch):
    """The layer under registration. Registration is derived from SEAT_CAPS
    (ADR-0002), so a mistaken edit there hands a seat a registered tool; what
    stops the write landing is the handler's own check. Inverting `if not
    result["ok"]` — or dropping the check — leaves the whole suite green apart
    from this."""
    from agents.tools import fund_server

    for tool, row in _canon().items():
        if row["status"] != "served":
            continue
        before = _row_counts(fund_db)
        if tool in SEAT_NAME_GUARDED:
            # Widen the table, then call from the seat it wrongly names.
            intruder = next(s for s in sorted(SEAT_CAPS)
                            if s not in row["seats"])
            monkeypatch.setitem(fund_server.SEAT_CAPS, intruder,
                                SEAT_CAPS[intruder] | {tool})
            result = _call(fund_db, sim_clock, intruder, tool, ARGS[tool])
            who = intruder
        else:
            # Build while the seat holds the cap so the tool is registered,
            # and only then revoke: revoking first unregisters it and the call
            # dies at the MCP layer without reaching the guard under test.
            import asyncio

            who = sorted(row["seats"])[0]
            _, call_tool = _handlers(_server(fund_db, sim_clock, who))
            monkeypatch.setitem(fund_server.SEAT_CAPS, who,
                                SEAT_CAPS[who] - {tool})
            result = asyncio.run(call_tool(tool, ARGS[tool]))
        assert _is_error(result), f"{tool} handler accepted {who}"
        assert _row_counts(fund_db) == before, f"{tool} wrote from {who}"
        monkeypatch.undo()


def test_the_canon_parser_rejects_what_it_cannot_read(fund_db, sim_clock):
    """The parser's own instrument. Every assertion above is an equality
    against a parsed table, so a parser that quietly dropped the rows it does
    not understand would turn a smuggled-in tool into a green suite. Both
    drops are driven here: an unreadable status, and a table it cannot find
    at all."""
    body = _section_4()
    assert "| not served — Phase 3 |" in body, "the fixture row moved"
    with pytest.raises(ValueError, match="'soon'"):
        _canon(body.replace("| not served — Phase 3 |", "| soon |"))
    with pytest.raises(ValueError, match="no tool table parsed"):
        _canon("## 4. Agent tool schemas\n\nprose, no table.\n")
    # And a seat that does not exist is a typo, not a new seat.
    with pytest.raises(ValueError, match="seats that do not exist"):
        _canon(body.replace("| `submit_decision` | `pm` |",
                            "| `submit_decision` | `pmm` |"))

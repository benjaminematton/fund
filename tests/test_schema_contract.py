"""Contract test: `state/schema.sql` must agree with the canonical spec DDL.

`specs/contracts.md` §2 and `specs/strategy-contracts.md` §2 are declared
canonical; `state/schema.sql` is the DDL that actually runs. Nothing else
compares them, so a column added to one side alone drifts silently.

Both sides are PARSED, never restated: a column list typed out here would be
a third source of truth. The comparison is structural (names, types, NOT
NULL, DEFAULT, CHECK, UNIQUE, PRIMARY KEY, REFERENCES), not textual, so
reformatting, `--` and `/* */` comments and `CREATE TABLE IF NOT EXISTS` are
all invisible to it. `PRAGMA table_info()` is not usable here: it does not
expose CHECK constraints, which is exactly where the allowed-value lists live.

Nothing carrying a `TABLE` keyword is skipped. Inside a `CREATE TABLE`, an
unknown column constraint, a trailing table option (`STRICT`, `WITHOUT
ROWID`) or a quoted identifier raises. So does a statement that is not a
plain `CREATE TABLE` but mentions `TABLE`: a `CREATE TEMP`/`CREATE VIRTUAL
TABLE` this comparator cannot represent, and — the route that actually got
exploited — a statement that has SWALLOWED a following `CREATE TABLE`
because the statement above it is missing its `;`. Only statements with no
`TABLE` in them at all (`CREATE INDEX`, `PRAGMA`, ...) are skipped, by
design. DDL outside a §2 sql fence is not seen.

The failure mode this file dies of quietly is under-extraction — a spec fence
that stops yielding tables leaves a green test comparing almost nothing — so
extraction is audited on its own in `test_spec_extraction_did_not_come_up_short`
and every table is pinned from both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "state" / "schema.sql"
# (spec file, §2 heading). The heading is followed by prose in at least one of
# them, so the DDL is located as "the sql fence inside §2", not "the next line".
SPEC_SECTIONS = (
    (ROOT / "specs" / "contracts.md", "## 2. SQLite DDL"),
    (ROOT / "specs" / "strategy-contracts.md", "## 2. DDL"),
)

# Spec §2 tables that deliberately have no `state/schema.sql` home. Reason per
# table is recorded in issue #50; it is not restated here. A table listed here
# is not compared, so removing it from the list is what binds it.
NO_SCHEMA_HOME = frozenset({
    "strategies", "trial_registry", "holdout_evaluations", "sleeves",
    "shadow_fills",
})

# Everything that can follow the type in a column definition.
_COLUMN_CONSTRAINTS = frozenset({
    "CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT",
    "COLLATE", "REFERENCES", "GENERATED", "AS",
})
_PUNCT = "(),;"


# --------------------------------------------------------------------------
# parsing


def _tokenize(sql: str) -> list[str]:
    """SQL text -> tokens. Drops `--` and `/* */` comments, keeps string
    literals whole.

    Both comment forms are dropped for the same reason: a comment is not part
    of the structure being compared, so it must be invisible. `/*` used to
    fall through to the operator branch and become a token, which pushed the
    `CREATE` of the following statement off the front and made the whole
    table vanish silently. The string-literal branch below still wins on `'`,
    so a `/*` inside a literal is never read as a comment.
    """
    toks: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
        elif sql.startswith("--", i):
            nl = sql.find("\n", i)
            i = n if nl < 0 else nl + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError(
                    f"unterminated /* block comment at offset {i}: everything"
                    " after it would be discarded, taking any CREATE TABLE"
                    " with it")
            i = end + 2
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise ValueError(f"unterminated string literal at offset {i}")
            toks.append(sql[i:j + 1])
            i = j + 1
        elif ch in _PUNCT:
            toks.append(ch)
            i += 1
        elif ch in '"`[]':
            raise ValueError(
                f"quoted identifier {ch!r} at offset {i}: the tokenizer would"
                " fold it into the neighbouring token, key the column under the"
                " quote character and then compare one column against another"
                " — use unquoted identifiers, or extend the tokenizer")
        elif ch.isalnum() or ch in "_.":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_."):
                j += 1
            toks.append(sql[i:j])
            i = j
        else:  # operator run: <=, >=, <>, =, *, ...
            j = i
            while j < n and not (sql[j].isspace() or sql[j] in _PUNCT
                                 or sql[j] == "'" or sql[j].isalnum()
                                 or sql[j] in "_."):
                j += 1
            toks.append(sql[i:j])
            i = j
    return toks


def _close(toks: list[str], open_at: int) -> int:
    """Index of the `)` matching the `(` at `open_at`."""
    depth = 0
    for i in range(open_at, len(toks)):
        if toks[i] == "(":
            depth += 1
        elif toks[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parentheses in DDL")


def _split_top(toks: list[str], sep: str) -> list[list[str]]:
    """Split on `sep` at paren depth 0, dropping empty parts."""
    parts, cur, depth = [], [], 0
    for t in toks:
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
        if t == sep and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    parts.append(cur)
    return [p for p in parts if p]


def _norm(toks: list[str]) -> str:
    """Canonical text for an expression: identifiers and keywords are
    case-insensitive in SQLite, string literals are not."""
    return " ".join(t if t.startswith("'") else t.upper() for t in toks)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    not_null: bool
    primary_key: bool
    unique: bool
    default: str | None
    checks: tuple[str, ...]
    references: str | None


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    constraints: tuple[str, ...]


def _parse_column(table: str, toks: list[str]) -> Column:
    name = toks[0]
    where = f"{table}.{name}"
    i, type_toks = 1, []
    while i < len(toks) and toks[i].upper() not in _COLUMN_CONSTRAINTS:
        if toks[i] == "(":
            end = _close(toks, i)
            type_toks += toks[i:end + 1]
            i = end + 1
        else:
            type_toks.append(toks[i])
            i += 1

    not_null = primary_key = unique = False
    default: str | None = None
    references: str | None = None
    checks: list[str] = []

    def nxt(k: int) -> str:
        if k >= len(toks):
            raise ValueError(f"{where}: definition ends mid-constraint")
        return toks[k]

    while i < len(toks):
        kw = toks[i].upper()
        if kw == "NOT" and nxt(i + 1).upper() == "NULL":
            not_null, i = True, i + 2
        elif kw == "NULL":
            i += 1
        elif kw == "PRIMARY" and nxt(i + 1).upper() == "KEY":
            primary_key, i = True, i + 2
            while i < len(toks) and toks[i].upper() in {"ASC", "DESC",
                                                        "AUTOINCREMENT"}:
                i += 1
        elif kw == "UNIQUE":
            unique, i = True, i + 1
        elif kw == "CHECK":
            if nxt(i + 1) != "(":
                raise ValueError(f"{where}: CHECK without a parenthesized expr")
            end = _close(toks, i + 1)
            checks.append(_norm(toks[i + 2:end]))
            i = end + 1
        elif kw == "DEFAULT":
            if nxt(i + 1) == "(":
                end = _close(toks, i + 1)
                default = _norm(toks[i + 1:end + 1])
                i = end + 1
            elif nxt(i + 1) in {"+", "-"}:
                default = _norm(toks[i + 1:i + 3])
                i += 3
            else:
                default = _norm([toks[i + 1]])
                i += 2
        elif kw == "REFERENCES":
            ref = [nxt(i + 1)]
            i += 2
            if i < len(toks) and toks[i] == "(":
                end = _close(toks, i)
                ref += toks[i:end + 1]
                i = end + 1
            references = _norm(ref)
        else:
            raise ValueError(
                f"{where}: unparsed token {toks[i]!r} in column definition"
                " — extend the parser rather than letting it skip a"
                " constraint")

    return Column(name=name.lower(), type=_norm(type_toks), not_null=not_null,
                  primary_key=primary_key, unique=unique, default=default,
                  checks=tuple(sorted(checks)), references=references)


def _is_table_constraint(item: list[str]) -> bool:
    """A table constraint, as opposed to a column named e.g. `check`."""
    head = [t.upper() for t in item[:2]]
    return (head[0] == "CONSTRAINT"
            or head[:2] in (["PRIMARY", "KEY"], ["FOREIGN", "KEY"])
            or (head[0] in ("UNIQUE", "CHECK") and len(item) > 1
                and item[1] == "("))


def _parse_tables(sql: str) -> dict[str, Table]:
    """Every CREATE TABLE in `sql`, keyed by table name. Statements with no
    `TABLE` keyword (CREATE INDEX / VIEW / TRIGGER, PRAGMA, ...) are ignored;
    every other shape raises rather than being skipped."""
    tables: dict[str, Table] = {}
    for stmt in _split_top(_tokenize(sql), ";"):
        upper = [t.upper() for t in stmt]
        if upper[:2] != ["CREATE", "TABLE"]:
            # The whole statement is searched, not just its first tokens. A
            # statement that CONTAINS `CREATE TABLE` without starting with one
            # has swallowed a real table — the statement above it is missing
            # its `;` — and used to be dropped here without a word.
            if any(upper[k:k + 2] == ["CREATE", "TABLE"]
                   for k in range(1, len(upper))):
                raise ValueError(
                    f"{' '.join(stmt[:6])!r}...: a CREATE TABLE is buried"
                    " inside this statement instead of starting it, so the"
                    " statement above it is missing its `;` — the swallowed"
                    " table would not be compared at all. Add the semicolon.")
            # CREATE INDEX / VIEW / TRIGGER are ignored on purpose. CREATE TEMP
            # TABLE and CREATE VIRTUAL TABLE are tables this comparator cannot
            # represent, and silently skipping one is how a table stops being
            # compared without anyone noticing.
            if "TABLE" in upper:
                raise ValueError(
                    f"{' '.join(stmt[:4])!r}: only plain CREATE TABLE is"
                    " compared — extend the parser rather than letting this"
                    " table be skipped")
            continue
        i = 2
        if [t.upper() for t in stmt[i:i + 3]] == ["IF", "NOT", "EXISTS"]:
            i += 3
        if i + 1 >= len(stmt):
            raise ValueError(f"{' '.join(stmt)!r}: truncated CREATE TABLE")
        name = stmt[i].lower()
        if stmt[i + 1] != "(":
            raise ValueError(f"{name}: CREATE TABLE without a column list")
        close = _close(stmt, i + 1)
        body = stmt[i + 2:close]
        if stmt[close + 1:]:
            # `) STRICT` and `) WITHOUT ROWID` change type enforcement and PK
            # semantics; neither is in `Table`, so neither can be compared.
            raise ValueError(
                f"{name}: unparsed table option"
                f" {' '.join(stmt[close + 1:])!r} after the column list — it"
                " changes table semantics and is not compared; extend the"
                " parser rather than letting it be skipped")

        columns, constraints = [], []
        for item in _split_top(body, ","):
            if _is_table_constraint(item):
                constraints.append(_norm(item))
            else:
                columns.append(_parse_column(name, item))
        if name in tables:
            raise ValueError(f"{name}: declared twice in the same source")
        tables[name] = Table(name, tuple(columns), tuple(sorted(constraints)))
    return tables


def _section_sql(path: Path, heading: str) -> str:
    """The one ```sql fence inside `path`'s `heading` section."""
    lines = path.read_text().splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        raise ValueError(f"{path.name}: heading {heading!r} not found") from None
    end = next((i for i in range(start, len(lines))
                if re.match(r"^##(?!#)\s", lines[i])), len(lines))

    fences = []
    i = start
    while i < end:
        if lines[i].strip() == "```sql":
            close = next((j for j in range(i + 1, end)
                          if lines[j].strip() == "```"), None)
            if close is None:
                raise ValueError(f"{path.name}: unclosed sql fence in {heading!r}")
            fences.append("\n".join(lines[i + 1:close]))
            i = close
        i += 1
    if len(fences) != 1:
        raise ValueError(
            f"{path.name}: expected 1 sql fence in {heading!r}, found"
            f" {len(fences)}")
    return fences[0]


# --------------------------------------------------------------------------
# the contract

SCHEMA_TABLES = _parse_tables(SCHEMA.read_text())
SPEC_TABLES: dict[str, Table] = {}
SPEC_SOURCE: dict[str, str] = {}
# label -> (raw fence text, names parsed out of it). Kept so the extraction can
# be audited as its own claim, not inferred from the comparison it feeds.
SPEC_EXTRACTION: dict[str, tuple[str, tuple[str, ...]]] = {}
for _path, _heading in SPEC_SECTIONS:
    _label = f"{_path.parent.name}/{_path.name} §2"
    _raw = _section_sql(_path, _heading)
    _parsed = _parse_tables(_raw)
    SPEC_EXTRACTION[_label] = (_raw, tuple(_parsed))
    for _name, _table in _parsed.items():
        if _name in SPEC_TABLES:
            raise ValueError(f"{_name}: declared in two spec files")
        SPEC_TABLES[_name] = _table
        SPEC_SOURCE[_name] = _label

BOUND = sorted(set(SPEC_TABLES) - NO_SCHEMA_HOME)


def _diff(name: str) -> list[str]:
    spec, got = SPEC_TABLES[name], SCHEMA_TABLES[name]
    src = SPEC_SOURCE[name]
    out: list[str] = []

    spec_cols = {c.name: c for c in spec.columns}
    got_cols = {c.name: c for c in got.columns}
    for missing in sorted(set(spec_cols) - set(got_cols)):
        out.append(f"{name}.{missing}: in {src}, absent from schema.sql")
    for extra in sorted(set(got_cols) - set(spec_cols)):
        out.append(f"{name}.{extra}: in schema.sql, absent from {src}")
    if set(spec_cols) == set(got_cols) and list(spec_cols) != list(got_cols):
        out.append(f"{name}: column order differs — {src}"
                   f" {list(spec_cols)}, schema.sql {list(got_cols)}")

    for col in (c for c in spec.columns if c.name in got_cols):
        mine = got_cols[col.name]
        for field in ("type", "not_null", "primary_key", "unique", "default",
                      "checks", "references"):
            want, have = getattr(col, field), getattr(mine, field)
            if want != have:
                out.append(f"{name}.{col.name}: {field} — {src} {want!r},"
                           f" schema.sql {have!r}")

    for c in sorted(set(spec.constraints) - set(got.constraints)):
        out.append(f"{name}: table constraint in {src}, absent from"
                   f" schema.sql — {c}")
    for c in sorted(set(got.constraints) - set(spec.constraints)):
        out.append(f"{name}: table constraint in schema.sql, absent from"
                   f" {src} — {c}")
    return out


# Independent of the parser: raw-text CREATE ... TABLE lines in a fence. Used
# only as a LOWER bound on how many statements the text declares — it is
# `^`-anchored per line, so it undercounts a `CREATE` and `TABLE` split across
# two lines, and it counts two statements sharing one line as one. The
# comparison below is therefore one-sided and a reformat can never make it
# fire. Those same blind spots are why this floor must never be the only thing
# standing between a dropped table and a green run: an under-count here gives
# a silent parser skip exactly enough slack to hide in. See
# `test_spec_extraction_did_not_come_up_short` for what that cost once.
_CREATE_TABLE_LINE = re.compile(r"(?im)^[ \t]*CREATE\b[^;\n]*\bTABLE\b")


def test_parsers_found_the_ddl():
    """Both sides parsed to something, and no table parsed to zero columns.

    A floor, not a coverage check: it says nothing about how MUCH of either
    source was extracted. That job belongs to
    `test_spec_extraction_did_not_come_up_short` and to the two existence
    tests, which pin every known table from both directions.
    """
    assert SCHEMA_TABLES, "no CREATE TABLE parsed from state/schema.sql"
    assert BOUND, "no CREATE TABLE parsed from the spec §2 sections"
    for src, tables in (("state/schema.sql", SCHEMA_TABLES),
                        ("spec §2", SPEC_TABLES)):
        empty = [t for t, tbl in tables.items() if not tbl.columns]
        assert not empty, f"{src}: parsed no columns for {empty}"


def test_spec_extraction_did_not_come_up_short():
    """Every spec §2 section still yields all the DDL its text contains.

    Under-extraction is silent by construction: tables that stop being
    extracted stop being compared, and a comparison of nothing passes. Two
    checks, both self-updating — no table name or count is written down here:
    a section that yields no tables at all fails, and a section that yields
    fewer tables than its raw text visibly declares fails.

    This guards the SIZE of the extraction, not its correctness. Which
    particular tables must survive is pinned by the two existence tests, from
    both directions.

    It is a backstop, and it is only safe to describe it as one now. Before
    the parser learned to raise on a swallowed statement, this floor was the
    SOLE guard on that route, and it was defeated by a two-part edit with no
    typo in it. Demonstrated against this repo by adding a `positions` table
    to `contracts.md` §2 that `schema.sql` lacks — the exact drift this file
    exists to catch:

        `--` comment before it        parsed=10 declared=10  -> failed (right)
        `/* */` comment before it     parsed=9  declared=10  -> failed HERE only
        `/* */` + `CREATE`/`TABLE`
          split across two lines      parsed=9  declared=9   -> 16 passed

    Both ingredients were legal: `/* */` is ordinary SQL, and the line split
    is a reformat this suite's own negative controls require to stay green.
    The fix was in the parser (block comments are now stripped, a buried
    `CREATE TABLE` now raises), not in this operator: `==` would not have
    caught the third row either, and it would fire on a legitimate reformat.
    """
    for label, (raw, names) in SPEC_EXTRACTION.items():
        assert names, (
            f"spec extraction came up short: parsed 0 CREATE TABLE statements"
            f" out of {label} — its sql fence is empty, or its DDL moved out of"
            " §2. Every table it used to declare has silently stopped being"
            " compared.")
        declared = len(_CREATE_TABLE_LINE.findall(raw))
        assert len(names) >= declared, (
            f"spec extraction came up short: {label} spells out {declared}"
            f" CREATE TABLE statements but only {len(names)} were parsed"
            f" ({', '.join(names)}) — the parser is dropping DDL the spec still"
            " contains, and every dropped table stopped being compared.")


def test_every_spec_table_is_declared_in_schema():
    """Also the schema side's under-extraction alarm, so it needs no floor of
    its own: a table that stops parsing out of `state/schema.sql` drops out of
    SCHEMA_TABLES and lands here. Verified by swallowing `signals` in
    schema.sql behind a `/* */` comment plus a `CREATE`/`TABLE` line split —
    this test failed with "signals ... absent from state/schema.sql" while the
    rest of the suite stayed green.
    """
    missing = [t for t in BOUND if t not in SCHEMA_TABLES]
    assert not missing, (
        "declared in a canonical spec §2 but absent from state/schema.sql: "
        + ", ".join(f"{t} ({SPEC_SOURCE[t]})" for t in missing)
        + " — add it to schema.sql, or to NO_SCHEMA_HOME with a reason in"
          " issue #50")


def test_every_schema_table_is_declared_in_a_spec():
    """The reverse direction: schema.sql may not grow a table the specs
    never declared, and a spec may not drop one schema.sql still runs."""
    undeclared = sorted(set(SCHEMA_TABLES) - set(SPEC_TABLES))
    assert not undeclared, (
        "in state/schema.sql but declared in no canonical spec §2: "
        + ", ".join(undeclared)
        + " — add the DDL to specs/contracts.md §2 or"
          " specs/strategy-contracts.md §2. NO_SCHEMA_HOME is the opposite"
          " direction (spec tables with no schema home) and is not an escape"
          " hatch for this one.")


def test_allowlisted_tables_still_have_no_schema_home():
    """Keeps NO_SCHEMA_HOME honest in both directions."""
    landed = sorted(NO_SCHEMA_HOME & set(SCHEMA_TABLES))
    assert not landed, (f"{landed} now exist in state/schema.sql — remove them"
                        " from NO_SCHEMA_HOME so they are compared")
    unknown = sorted(NO_SCHEMA_HOME - set(SPEC_TABLES))
    assert not unknown, (f"{unknown} are in NO_SCHEMA_HOME but no longer"
                         " declared in any spec §2 — drop the stale entries")


@pytest.mark.parametrize("table", BOUND)
def test_schema_matches_spec(table):
    """state/schema.sql and the canonical spec §2 declare the same structure."""
    if table not in SCHEMA_TABLES:
        pytest.skip("covered by test_every_spec_table_is_declared_in_schema")
    problems = _diff(table)
    assert not problems, (f"{table}: state/schema.sql has drifted from"
                          f" {SPEC_SOURCE[table]}\n  "
                          + "\n  ".join(problems))

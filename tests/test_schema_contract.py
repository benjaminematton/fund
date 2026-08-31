"""Contract test: `state/schema.sql` must agree with the canonical spec DDL.

`specs/contracts.md` §2 and `specs/strategy-contracts.md` §2 are declared
canonical; `state/schema.sql` is the DDL that actually runs. Nothing else
compares them, so a column added to one side alone drifts silently.

Both sides are PARSED, never restated: a column list typed out here would be
a third source of truth. Parsing is deliberately deferred out of the module
body into `_contract()`, so that DDL this file cannot parse fails THIS
file's tests instead of erroring pytest's collection and taking all ~1200
tests in the repo down with it.

What is deferred is the RAISE, not the READ. Both sources are read and
parsed AT IMPORT, by `_bound_or_sentinel()` inside the `parametrize`
decorator near the bottom of this file: after a bare
`import tests.test_schema_contract` with zero tests run,
`_contract.cache_info()` already reports `misses=1, currsize=1` and the
parametrize argvalues already hold their 11 table names (measured). Two
consequences to reason from:

  * Anything `_bound_or_sentinel()` lets through is a COLLECTION error, so
    it catches `Exception` and not `ValueError` — a spec file that has been
    renamed raises `FileNotFoundError`, which is not a `ValueError` and
    which halted collection repo-wide until it did.
  * `_contract()` is memoized by the time the first test runs, so a source
    edit made AFTER collection is invisible. Measured: flipping
    `usd_estimate REAL` to `INTEGER` in `state/schema.sql` from a
    `pytest_collection_finish` hook leaves every test in this file green
    (18 of 18) with that drift really in the tree. Only the opposite
    direction is guarded: `test_schema_matches_spec` fails if the DDL did
    not parse at collection but parses now. Editing a spec or the schema
    while pytest is running yields a stale pass; re-run the suite.

What the comparison actually compares, precisely. Table and column NAMES,
and the three booleans NOT NULL / PRIMARY KEY / UNIQUE, are structural:
case, whitespace, `--` and `/* */` comments, `CREATE TABLE IF NOT EXISTS`
and constraint-clause order are all invisible to them. But `type`,
`default`, `checks`, `references` and every table constraint are compared as
NORMALIZED TEXT — the tokens joined by single spaces, uppercased outside
string literals. Two sides that mean the same thing but spell it differently
therefore FAIL: `CHECK (qty >= 0)` vs `CHECK (0 <= qty)`, `<>` vs `!=`,
`DEFAULT 0` vs `DEFAULT 0.0`, `INT` vs `INTEGER`, and a renamed table-level
`CONSTRAINT` (a column-level one raises instead — `Column` has no field for
it). That is a deliberate trade, not an oversight: such a failure
prints both sides and is one rename from green, whereas an expression
comparator that called those pairs equal would be a fourth source of truth
about what the DDL means. `PRAGMA table_info()` is not usable here either:
it does not expose CHECK constraints, which is exactly where the
allowed-value lists live.

Nothing carrying a `TABLE` keyword is skipped. Inside a `CREATE TABLE`, an
unknown column constraint, a `PRIMARY KEY` modifier (`ASC`/`DESC`/
`AUTOINCREMENT`), a trailing table option (`STRICT`, `WITHOUT ROWID`), a
schema-qualified table name or a quoted identifier raises. So does a
statement that is not a plain `CREATE TABLE` but mentions `TABLE`: a `CREATE
TEMP`/`CREATE VIRTUAL TABLE` this comparator cannot represent. And a `TABLE`
keyword anywhere but position 1 of a `CREATE TABLE` raises wherever it sits,
including inside a column CHECK, a DEFAULT expression, a type's parens or a
table-constraint item: it means a second table statement has been swallowed,
by a missing `;` above or by a `(` left open across it, and would be
compared by nothing. Only statements with no `TABLE` keyword in them at all
(`CREATE INDEX`, `CREATE TRIGGER`, `PRAGMA`, ...) are skipped, by design.

DDL outside §2 is not seen. Inside §2, ALL THREE CommonMark code-block
forms are accounted for — backtick fences and tilde fences are READ,
4-space-indented blocks RAISE — because each renders as code to whoever
reads the spec and so is a plausible home for canonical DDL. Keying on the
backtick character alone
left `~~~sql` and indented DDL invisible to every guard here at once. A
fence whose info string is not `sql`/`sqlite` raises, and any block count
other than one per section raises; no form is passed over. `CREATE TABLE`
written into §2 PROSE is still not seen — prose renders as prose, so it is
not a plausible canonical-DDL home.

Each section's block is also executed against an in-memory SQLite by
`test_spec_ddl_executes`, off the same block discovery. Read that test's
docstring for the narrow class it closes; it is not cover for relaxing any
guard here.

The failure mode this file dies of quietly is under-extraction — a spec fence
that stops yielding tables leaves a green test comparing almost nothing — so
extraction is audited on its own in `test_spec_extraction_did_not_come_up_short`
and every table is pinned from both directions.
"""

from __future__ import annotations

import functools
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "state" / "schema.sql"
# (spec file, §2 heading). The heading is followed by prose in at least one of
# them, so the DDL is located as "the code block inside §2", not "the next line".
SPEC_SECTIONS = (
    (ROOT / "specs" / "contracts.md", "## 2. SQLite DDL"),
    (ROOT / "specs" / "strategy-contracts.md", "## 2. DDL"),
    # improvement.md keeps its DDL in §4, not §2 (issue #50's reasoning for a
    # per-file home carried over: contracts.md §7b points here).
    (ROOT / "specs" / "improvement.md", "## 4. DDL"),
)

# Spec §2 tables that deliberately have no `state/schema.sql` home. Reason per
# table is recorded in issue #50; it is not restated here. A table listed here
# is not compared, so removing it from the list is what binds it.
#
# `trial_registry` and `holdout_evaluations` came OFF this list on 2026-08-29
# under issue #172, which is #50's Group 2: their DDL moved out of
# fundbt/registry.py's standalone string into state/schema.sql, so both are now
# compared character-for-character against strategy-contracts.md §2.
#
# `strategies` came off on 2026-08-30 under issue #197, the first of #50's
# Group 1: its DDL now has a state/schema.sql home, so it too is compared
# character-for-character. The TABLE is what landed — there is no
# state/transition.py machine for it — but this list is about where the DDL
# lives, not about what reads it. `sleeves` and `shadow_fills` are the rest of
# Group 1 and are untouched: no DDL for those exists anywhere in the repo.
NO_SCHEMA_HOME = frozenset({
    "sleeves", "shadow_fills",
    # improvement.md §4, lanes not yet landed (specs/improvement.md §8): the
    # per-table reason is recorded in issue #50. `lessons` lands with lane (c),
    # `proposals` with lane (e); each removes its own entry.
    "lessons", "proposals",
})

# Everything that can follow the type in a column definition.
_COLUMN_CONSTRAINTS = frozenset({
    "CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT",
    "COLLATE", "REFERENCES", "GENERATED", "AS",
})
_PUNCT = "(),;"
# May follow `PRIMARY KEY` in a column definition. None of them is a field of
# `Column`, so each one raises rather than being dropped — see `_parse_column`.
_PK_MODIFIERS = frozenset({"ASC", "DESC", "AUTOINCREMENT"})
# Fence info strings inside a spec §2 that hold DDL, matched case-insensitively.
# Anything else fenced in §2 raises — see `_section_blocks`.
_DDL_FENCE_TAGS = frozenset({"sql", "sqlite"})
# A CommonMark fence line, backtick OR tilde. Up to 3 leading spaces still opens
# a fence; at 4 or more it is no longer a fence, and if a blank line precedes it
# it is an indented code block, which `_section_blocks` raises on.
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")


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
    """One column definition -> `Column`. Anything it cannot represent raises.

    Why column-level `PRIMARY KEY ASC/DESC/AUTOINCREMENT` RAISES here while
    the table-level spelling `PRIMARY KEY (a, b DESC)` is merely COMPARED, as
    normalized text, by `_is_table_constraint`. The asymmetry is measured, not
    stylistic. Inserting a row with no explicit id, SQLite 3.53.2:

        col-level  INTEGER PRIMARY KEY         stored id=1
        col-level  INTEGER PRIMARY KEY DESC    stored id=None   <-- the hazard
        col-level  INTEGER PRIMARY KEY ASC     stored id=1
        tbl-level  PRIMARY KEY (id)            stored id=1
        tbl-level  PRIMARY KEY (id DESC)       stored id=1      <-- NOT a hazard
        tbl-level  PRIMARY KEY (id, v)         stored id=None   (composite:
        tbl-level  PRIMARY KEY (id, v DESC)    stored id=None    never a rowid
                                                                 alias either way)

    `DESC` silently stops a column aliasing the rowid ONLY in the column-level
    position; in the table-level single-column form it is ignored and the alias
    survives, and a composite PK is never a rowid alias with or without it. So
    the column-level modifier is a real semantic difference that `Column` has
    no field to carry, hence the raise; the table-level one carries no
    equivalent hazard and normalized-text comparison is the right treatment.

    If anyone later unifies the two, UNIFY TOWARD COMPARING: give `Column` a
    field for the modifier so it is compared like every other clause. Do not
    unify toward raising on the table-level form — that punishes a construct
    measured to be benign.
    """
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
            if i < len(toks) and toks[i].upper() in _PK_MODIFIERS:
                raise ValueError(
                    f"{where}: PRIMARY KEY {toks[i].upper()} — `primary_key`"
                    " is compared but this modifier is not, and it is not"
                    " cosmetic. Measured against SQLite: `INTEGER PRIMARY KEY"
                    " DESC` is NOT a rowid alias, so every id inserted without"
                    " one comes back NULL; AUTOINCREMENT adds a"
                    " `sqlite_sequence` table and changes id reuse. (ASC alone"
                    " behaves like plain PRIMARY KEY, but it is still a"
                    " declared-scope difference nothing would compare.) Drop"
                    " it, or extend the parser rather than letting it skip a"
                    " constraint")
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
            # CREATE INDEX / VIEW / TRIGGER are ignored on purpose. CREATE TEMP
            # TABLE and CREATE VIRTUAL TABLE are tables this comparator cannot
            # represent, and silently skipping one is how a table stops being
            # compared without anyone noticing. A `CREATE TABLE` that does not
            # START its statement lands here too — see the twin check below.
            if "TABLE" in upper:
                raise ValueError(
                    f"{' '.join(stmt[:4])!r}: this statement carries a TABLE"
                    " keyword but is not a plain CREATE TABLE, so nothing"
                    " compares it. Either extend the parser rather than"
                    " letting the table be skipped (CREATE TEMP/VIRTUAL TABLE,"
                    " ALTER, DROP), or restore the `;` this statement is"
                    " missing — without it a following CREATE TABLE is"
                    " swallowed into this one.")
            continue
        i = 2
        if [t.upper() for t in stmt[i:i + 3]] == ["IF", "NOT", "EXISTS"]:
            i += 3
        if i + 1 >= len(stmt):
            raise ValueError(f"{' '.join(stmt)!r}: truncated CREATE TABLE")
        if "TABLE" in upper[2:]:
            # `TABLE` is reserved, so it cannot be a column name or a type: a
            # second one inside this statement's body is another table
            # statement swallowed by the `;` that should have ended this one.
            # The `(` doing the swallowing can be opened in any of the four
            # regions of a `CREATE TABLE` that hold free text — a column
            # CHECK, a DEFAULT (expr), a type's parens, and a whole
            # table-constraint item — and this check covers all four, plus the
            # plain missing-`;` case. The raw-text floor below cannot see any
            # of it once `CREATE` and `TABLE` sit on two lines.
            raise ValueError(
                f"{' '.join(stmt[:4])!r}...: a second TABLE statement is"
                " buried inside this CREATE TABLE's body, so it is compared by"
                " nothing and reported by nothing else. Either the `;` that"
                " should have ended the statement above is missing, or a `(`"
                " opened earlier in this statement is never closed and has"
                " swallowed it. Add the semicolon or balance the parentheses.")
        name = stmt[i].lower()
        if "." in name:
            # `.` is in the tokenizer's identifier charset and has to be, for
            # `0.20` and `tbl(col)` refs — so a schema prefix rides along into
            # the dict key and the table quietly stops matching its twin. Both
            # existence tests then fire, naming two innocent sides instead of
            # the cause.
            raise ValueError(
                f"{name}: schema-qualified table name — it would be keyed as"
                f" {name!r} and so compared against nothing on the other side."
                " Write the bare table name.")
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
        # The comparison holds table constraints in a set, so a duplicate
        # COLLAPSES: the second copy disappears into `_diff`'s set() and is
        # never compared. Nothing else catches that — SQLite ACCEPTS it
        # (measured: `CREATE TABLE t (a INT, UNIQUE(a), UNIQUE(a))` executes
        # clean), so `test_spec_ddl_executes` passes on it too. Raise instead.
        # Duplicate COLUMN names need no twin of this check: SQLite REJECTS
        # them ("duplicate column name: a"), which fails
        # `test_spec_ddl_executes` on the spec side and every test that opens a
        # DB through `state/db.py` on the schema side.
        dupe_cons = sorted({c for c in constraints if constraints.count(c) > 1})
        if dupe_cons:
            raise ValueError(
                f"{name}: identical table constraint(s) {dupe_cons} declared"
                " more than once — the comparison holds constraints in a set,"
                " so the duplicate would collapse and never be compared."
                " Remove it.")
        if name in tables:
            raise ValueError(f"{name}: declared twice in the same source")
        tables[name] = Table(name, tuple(columns), tuple(sorted(constraints)))
    return tables


def _section_blocks(path: Path, heading: str) -> list[str]:
    """Every code block inside `path`'s `heading` section, in document order.

    THE ONE code-block finder in this file. `_contract()` parses what it
    returns and `test_spec_ddl_executes` executes the same strings; a second
    finder would be a second source of truth about where the canonical DDL
    lives, and would drift precisely here.

    All three CommonMark code-block forms are recognised, because each one
    renders as code and so is a plausible home for canonical DDL:

      * backtick fences  ```sql ... ```   (any run of >= 3 backticks)
      * tilde fences     ~~~sql ... ~~~   (any run of >= 3 tildes)
      * 4-space (or tab) indented blocks  -> RAISE, see below

    An earlier version keyed on the literal "```sql", so a fence tagged
    ```` ```sqlite ````, ```` ```SQL ```` or nothing at all was not a fence to
    it at all; the round that fixed that keyed on the BACKTICK CHARACTER, which
    left `~~~sql` and indented DDL invisible to the tag check, to the
    one-block guard and to `_CREATE_TABLE_LINE` alike. Neither route needs
    malformed SQL — both are plausible typos, and both render as SQL to a
    human reading the spec.

    A fence's info string must be a `_DDL_FENCE_TAGS` tag or this raises; a
    fence closes on a line of >= as many of the SAME character with nothing
    after it, per CommonMark. An indented block — a non-blank line indented
    4+ spaces or a tab, after a blank line, which is CommonMark's rule and is
    what keeps this off wrapped prose — raises rather than being read:
    reading it would mean guessing whether the indentation is DDL or an
    example, and silently ignoring it is the failure this function exists to
    prevent.

    What it does NOT see: `CREATE TABLE` in §2 prose, and code blocks in a
    section other than `heading`. Neither renders as canonical §2 DDL.

    HOW BIG THE POLICED SURFACE IS. The section ends at the next `^## `, and
    the negative lookahead lets `###` SUBHEADINGS stay inside it. So the two
    guards above — every fence must be tagged sql/sqlite, every indented block
    raises — apply to far more than the DDL fence. Measured on this commit:
    `specs/contracts.md` §2 spans 161 lines, of which 39 (29 non-blank) come
    AFTER the closing fence, including the whole
    `### Attribution — charter_version and model_id` subsection;
    `specs/strategy-contracts.md` §2 spans 122 lines with nothing but a blank
    line after its closing fence. A ```text snippet or a 4-space-indented
    list continuation written into that Attribution prose takes all 7 of this
    file's source-reading tests down (measured, both forms) while the DDL
    itself is untouched. That is the
    price of the guards, and it is paid knowingly: narrowing the scan to stop
    at the fence would restore exactly the silence — DDL sitting in §2 that
    nothing reads — these guards exist to prevent.
    """
    lines = path.read_text().splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        raise ValueError(f"{path.name}: heading {heading!r} not found") from None
    end = next((i for i in range(start, len(lines))
                if re.match(r"^##(?!#)\s", lines[i])), len(lines))

    blocks: list[str] = []
    i, prev_blank = start, True
    while i < end:
        line = lines[i]
        opener = _FENCE_RE.match(line)
        if opener:
            char, width = opener["fence"][0], len(opener["fence"])
            tag = opener["info"].strip()
            if tag.lower() not in _DDL_FENCE_TAGS:
                raise ValueError(
                    f"{path.name}: code fence tagged {tag!r} inside"
                    f" {heading!r} — only {sorted(_DDL_FENCE_TAGS)} are read as"
                    " DDL, so anything in this fence is compared by nothing."
                    " Retag it, move it out of the section, or add its tag to"
                    " _DDL_FENCE_TAGS")
            close = None
            for j in range(i + 1, end):
                shut = _FENCE_RE.match(lines[j])
                if (shut and shut["fence"][0] == char
                        and len(shut["fence"]) >= width
                        and not shut["info"].strip()):
                    close = j
                    break
            if close is None:
                raise ValueError(
                    f"{path.name}: unclosed {char * width}{tag} fence in"
                    f" {heading!r}")
            blocks.append("\n".join(lines[i + 1:close]))
            i, prev_blank = close + 1, False
            continue
        if prev_blank and line.strip() and (line.startswith("    ")
                                            or line.startswith("\t")):
            raise ValueError(
                f"{path.name}: indented code block at line {i + 1} of"
                f" {heading!r} ({line.strip()[:40]!r}...) — it renders as code"
                " but this file will not read it as DDL, so anything in it is"
                " compared by nothing. Put the DDL in a ```sql fence, or"
                " unindent the prose.")
        prev_blank = not line.strip()
        i += 1
    return blocks


def _section_ddl(path: Path, heading: str) -> str:
    """The one DDL block inside `path`'s `heading` section."""
    blocks = _section_blocks(path, heading)
    if len(blocks) != 1:
        raise ValueError(
            f"{path.name}: expected 1 DDL code block in {heading!r}, found"
            f" {len(blocks)}")
    return blocks[0]


# --------------------------------------------------------------------------
# the contract

@functools.cache
def _spec_ddl_blocks() -> tuple[tuple[str, str], ...]:
    """`(label, DDL text)` for each spec §2, off the one block finder.

    Both consumers of the spec DDL start here: `_contract()` parses these
    strings, `test_spec_ddl_executes` executes them. Deliberately independent
    of the PARSER, so that a construct the parser cannot represent does not
    also make the smoke test report a syntax problem the SQL does not have.
    """
    return tuple((f"{path.parent.name}/{path.name}"
                  f" §{heading.split('.')[0].removeprefix('## ')}",
                  _section_ddl(path, heading))
                 for path, heading in SPEC_SECTIONS)


@dataclass(frozen=True)
class _Contract:
    schema_tables: dict[str, Table]
    spec_tables: dict[str, Table]
    spec_source: dict[str, str]
    # label -> (raw block text, names parsed out of it). Kept so the extraction
    # can be audited as its own claim, not inferred from the comparison it feeds.
    spec_extraction: dict[str, tuple[str, tuple[str, ...]]]
    bound: tuple[str, ...]


@functools.cache
def _contract() -> _Contract:
    """Read and parse both sides. Raises on anything unreadable or unparseable
    — `ValueError` from the parser, but also whatever `Path.read_text()`
    throws.

    THE RAISE REACHES THE CALLER FROM INSIDE A TEST, NEVER FROM THE MODULE
    BODY — that placement is the whole point of this function, not an accident
    of style. (The CALL still happens at import: `_bound_or_sentinel()` makes
    it, and catches everything, so what the module body never does is raise.)
    Every `raise ValueError` above is deliberate and loud, but when the parse
    ran in the module body those raises fired during pytest's COLLECTION:
    `make test` reported `Interrupted: 1 error during collection`, ran ZERO
    of the repo's ~1200 tests, and told whoever hit it to extend a parser.
    An ordinary edit introducing no drift at all was enough to do it —
    re-measured on TEN, all ten still raising here.
    Six are DDL applied correctly to BOTH sides:
    `ON DELETE CASCADE`, `COLLATE NOCASE`, a COLUMN-level named `CONSTRAINT`,
    `GENERATED ALWAYS AS`, `) STRICT`, and a quoted identifier. Four are
    markdown edits to a spec file that leave the DDL untouched: a ```text
    fence in §2, an untagged fence, a second ```sql fence, and retitling the
    §2 heading. (A TABLE-level named `CONSTRAINT` parses fine — it lands in
    `Table.constraints` as normalized text; that one does not reproduce.)
    Since `CLAUDE.md` requires `make test` green before every commit, that
    made one unparsed token a repo-wide stop-work
    whose cheapest fix is deleting this file. Deferred here, the identical
    raise is confined to this file, and which of its tests go red depends on
    where the failure is (both measured on this commit):

        ON DELETE CASCADE (a PARSER limit)   6 of this file's tests fail;
                                             `test_spec_ddl_executes` passes,
                                             since it is valid SQL
        an untagged fence (BLOCK DISCOVERY)  7 fail — the smoke test has no
                                             block to run and goes down too

    Either way every test outside this file still runs and still passes, and
    the suite runs 10 fewer tests than a green run, because the 11 per-table
    cases below collapse to one sentinel when there is no parsed table list to
    parametrize over.

    The messages are unchanged and must stay loud: the defect was WHERE they
    fired, never THAT they fired. `functools.cache` memoizes only success —
    Python does not cache exceptions — so on a parse failure every test here
    that reads the sources re-runs the parse and fails with the same full
    message.
    """
    schema_tables = _parse_tables(SCHEMA.read_text())
    spec_tables: dict[str, Table] = {}
    spec_source: dict[str, str] = {}
    spec_extraction: dict[str, tuple[str, tuple[str, ...]]] = {}
    for label, raw in _spec_ddl_blocks():
        parsed = _parse_tables(raw)
        spec_extraction[label] = (raw, tuple(parsed))
        for name, table in parsed.items():
            if name in spec_tables:
                raise ValueError(f"{name}: declared in two spec files")
            spec_tables[name] = table
            spec_source[name] = label
    return _Contract(
        schema_tables=schema_tables, spec_tables=spec_tables,
        spec_source=spec_source, spec_extraction=spec_extraction,
        bound=tuple(sorted(set(spec_tables) - NO_SCHEMA_HOME)))


# `parametrize` is evaluated at import, so it may not be allowed to raise
# either. On a parse failure it yields this single id instead; the test body
# then calls `_contract()`, re-raises, and reports the real message as an
# ordinary failure of this file.
_PARSE_FAILED = "<spec/schema DDL did not parse>"


def _bound_or_sentinel() -> list[str]:
    """The bound table names, or the sentinel if reading or parsing blew up.

    Catches `Exception`, and the width is the point: "collection must never
    die" is a property of THIS call site, not of the parser's exception
    vocabulary. A `ValueError`-only guard let `FileNotFoundError` through, so
    renaming or moving a spec file — `_section_blocks` calls
    `Path.read_text()` — errored collection and ran ZERO of the repo's ~1200
    tests. The raise sites above tell contributors to "extend the parser"; the
    next one to reach for `KeyError` or `IndexError` must not silently reopen
    that hole. Nothing is swallowed: every test here that reads the sources
    calls `_contract()`, which re-raises with the full message as an ordinary
    failure of this file.
    """
    try:
        return list(_contract().bound)
    except Exception:
        return [_PARSE_FAILED]


def _diff(con: _Contract, name: str) -> list[str]:
    spec, got = con.spec_tables[name], con.schema_tables[name]
    src = con.spec_source[name]
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


# Independent of the parser: raw-text CREATE ... TABLE lines in a block, used
# only as a LOWER bound on how many statements the text declares. It is a
# heuristic over text and it is blind in both directions:
#
#   * it UNDERCOUNTS — `^`-anchored per line, so a `CREATE` and `TABLE` split
#     across two lines is missed, and two statements sharing one line count as
#     one. The `>=` comparison below makes those blind spots silent, never
#     spurious.
#   * it OVERCOUNTS any `CREATE ...` line whose text before the first `;`
#     contains the word TABLE. Comment text used to count, so a
#     `CREATE INDEX ... ON trial_registry(family)` line ending in the comment
#     `-- one row per table`, with the `;` on the next line, failed the floor
#     with nothing drifted at all (measured; the same comment placed AFTER the
#     `;` was silent). `_SQL_COMMENT` strips comment text before the count for
#     that reason. A TABLE inside a string literal on such a line would still
#     overcount; neither spec has one.
#
# The under-count is why this floor must never be the only thing standing
# between a dropped table and a green run: it gives a silent parser skip
# exactly enough slack to hide in. See
# `test_spec_extraction_did_not_come_up_short` for what that cost once.
_CREATE_TABLE_LINE = re.compile(r"(?im)^[ \t]*CREATE\b[^;\n]*\bTABLE\b")
# `--` to end of line and `/* */` across lines. Stripping inside a string
# literal would be wrong, but it can only LOWER the count, which is the safe
# direction for a floor compared with `>=`.
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def test_parsers_found_the_ddl():
    """At least one table is left for `test_schema_matches_spec` to run on.

    One assert, and it guards the parametrize list itself. `bound` IS that
    list. If it empties — the spec side parsing to nothing, or
    `NO_SCHEMA_HOME` grown until it covers every spec table — `parametrize`
    gets empty argvalues and pytest turns 11 comparisons into ONE SKIP,
    "got empty parameter set" (measured). That is the quietest failure this
    file has, so it is made loud here.

    Two asserts that stood here were removed as subsumed, both re-measured:
    `assert con.schema_tables` (with `state/schema.sql` emptied,
    `test_every_spec_table_is_declared_in_schema` fires naming all 11 tables
    and how to fix it) and a loop over zero-column tables (a zero-column table
    needs `CREATE TABLE t ()`, which SQLite rejects — "near ')': syntax error"
    — so `test_spec_ddl_executes` has the spec side and `state/db.py` the
    schema side).

    Also the first place a parse failure lands, now that the raise reaches a
    test body instead of the module body: it surfaces here with its own
    message instead of at collection time.
    """
    assert _contract().bound, (
        "no CREATE TABLE parsed from the spec §2 sections")


def test_a_non_valueerror_still_yields_the_sentinel():
    """`_bound_or_sentinel()` survives ANY exception, not just `ValueError`.

    It runs at import, inside `parametrize`, so anything it lets through is a
    collection error and ZERO of the repo's ~1200 tests run.
    `FileNotFoundError` (a renamed spec file, raised by `Path.read_text()`) is
    the measured instance; `KeyError` stands for whatever a future parser
    guard raises. Patched here rather than by moving a real file: this
    worktree shares a checkout, and a test that dirties the tree is worse than
    the bug.
    """
    module = sys.modules[__name__]
    for exc in (FileNotFoundError(2, "No such file or directory", str(SCHEMA)),
                KeyError("heading")):
        with pytest.MonkeyPatch.context() as mp:
            def boom(_exc=exc):
                raise _exc
            mp.setattr(module, "_contract", boom)
            assert _bound_or_sentinel() == [_PARSE_FAILED], (
                f"{type(exc).__name__} escaped _bound_or_sentinel() — at"
                " import time that is"
                " `Interrupted: 1 error during collection`")


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

    It is a backstop. On ONE route — a `/* */` comment, which the tokenizer
    did not strip until `da59f50` — it was for a while the only thing
    failing, and a two-part edit with no typo in it walked past it. Measured
    at `caa35d5` by adding a `positions` table to `contracts.md` §2 that
    `schema.sql` lacks — the exact drift this file exists to catch:

        `--` comment before it        parsed=10 declared=10  -> failed (right)
        `/* */` comment before it     parsed=9  declared=10  -> failed HERE only
        `/* */` + `CREATE`/`TABLE`
          split across two lines      parsed=9  declared=9   -> 16 passed

    Both ingredients were legal: `/* */` is ordinary SQL, and the line split
    is a reformat this suite's own negative controls require to stay green.

    `da59f50` also read that finding across to the missing-`;` route, and
    that part does NOT reproduce — it is corrected rather than left standing.
    Deleting the `;` from `strategy-contracts.md`'s second `CREATE INDEX` at
    `caa35d5` failed BOTH this test and
    `test_allowlisted_tables_still_have_no_schema_home`: dropping a `;` does
    not change how many lines START with `CREATE ... TABLE`, so declared held
    at 7 while parsed fell to 6 (`assert 6 >= 7`). That route needed the line
    split as well. The parser raise is still the right fix — this floor is
    blind to both ingredients — but it did not close a route that was open.

    The fix was in the parser (block comments are stripped, a buried
    `CREATE TABLE` raises), not in this operator: `==` would not have caught
    the third row either, and it would fire on a legitimate reformat.
    """
    for label, (raw, names) in _contract().spec_extraction.items():
        assert names, (
            f"spec extraction came up short: parsed 0 CREATE TABLE statements"
            f" out of {label} — its DDL block is empty, or its DDL moved out of"
            " §2. Every table it used to declare has silently stopped being"
            " compared.")
        declared = len(_CREATE_TABLE_LINE.findall(_SQL_COMMENT.sub("", raw)))
        assert len(names) >= declared, (
            f"spec extraction came up short: {label} spells out {declared}"
            f" CREATE TABLE statements but only {len(names)} were parsed"
            f" ({', '.join(names)}) — the parser is dropping DDL the spec still"
            " contains, and every dropped table stopped being compared.")


def test_every_spec_table_is_declared_in_schema():
    """Also the schema side's under-extraction alarm, so it needs no floor of
    its own: a table that stops parsing out of `state/schema.sql` drops out of
    the parsed schema tables and lands here. Verified by swallowing `signals`
    in schema.sql behind a `/* */` comment plus a `CREATE`/`TABLE` line split —
    this test failed with "signals ... absent from state/schema.sql" while the
    rest of the suite stayed green.
    """
    con = _contract()
    missing = [t for t in con.bound if t not in con.schema_tables]
    assert not missing, (
        "declared in a canonical spec §2 but absent from state/schema.sql: "
        + ", ".join(f"{t} ({con.spec_source[t]})" for t in missing)
        + " — add it to schema.sql, or to NO_SCHEMA_HOME with a reason in"
          " issue #50")


def test_every_schema_table_is_declared_in_a_spec():
    """The reverse direction: schema.sql may not grow a table the specs
    never declared, and a spec may not drop one schema.sql still runs."""
    con = _contract()
    undeclared = sorted(set(con.schema_tables) - set(con.spec_tables))
    assert not undeclared, (
        "in state/schema.sql but declared in no canonical spec §2: "
        + ", ".join(undeclared)
        + " — add the DDL to specs/contracts.md §2 or"
          " specs/strategy-contracts.md §2. NO_SCHEMA_HOME is the opposite"
          " direction (spec tables with no schema home) and is not an escape"
          " hatch for this one.")


def test_allowlisted_tables_still_have_no_schema_home():
    """Keeps NO_SCHEMA_HOME honest in both directions."""
    con = _contract()
    landed = sorted(NO_SCHEMA_HOME & set(con.schema_tables))
    assert not landed, (f"{landed} now exist in state/schema.sql — remove them"
                        " from NO_SCHEMA_HOME so they are compared")
    unknown = sorted(NO_SCHEMA_HOME - set(con.spec_tables))
    assert not unknown, (f"{unknown} are in NO_SCHEMA_HOME but no longer"
                         " declared in any spec §2 — drop the stale entries")


def test_spec_ddl_executes():
    """Each spec §2 DDL block is valid SQL: SQLite accepts it as written.

    Runs on the SAME strings `_contract()` parses: both come from
    `_spec_ddl_blocks()`, which is the sole consumer of `_section_blocks`, the
    sole code-block finder in this file. A second finder here would be a
    second source of truth about where the canonical DDL lives and would drift
    exactly where the backtick-only finder already drifted once. It does NOT
    go through the parser, so a construct the parser cannot represent — which
    is a separate, loud failure of the tests above — is not also reported here
    as a syntax problem the SQL does not have.

    SCOPE. What it uniquely closes is spec-side SQL that SQLite rejects but
    that the PARSER above does not: DDL the parser skips by design, or
    normalizes until the defect disappears. Four cases measured on this commit,
    each of them the ONLY failing test in the whole suite:

      * a missing `;` after the FIRST `CREATE INDEX` in
        `strategy-contracts.md` §2 — the two indexes merge into one statement,
        which carries no `TABLE` token, so `_parse_tables` skips it by design
        and neither index is looked at;
      * `CREATE INDEX ... ON trial_ledger(spec_id)`, naming a table that does
        not exist — valid syntax, unresolvable, and again no `TABLE` token;
      * a TRAILING COMMA before the `)` of a spec `CREATE TABLE` — `_split_top`
        drops the empty part, so the malformed statement parses to a
        byte-identical `Table` and is invisible to `_diff` by construction;
      * a broken `CREATE VIEW live_sleeves AS SELEKT * FROM sleeves` — a
        statement shape the parser never inspects.

    Note where it is LEAST unique: malformed SQL identical on BOTH sides is
    not this test's class at all. `state/db.py:connect()` runs `executescript`
    over `state/schema.sql` whenever a DB is missing a table — which every
    `tmp_path` fixture in the suite is — so that case is repo-wide wreckage
    long before it reaches here. Measured, with one column duplicated in
    `state/schema.sql`: 61 failed and 473 errors across the suite.

    What it does NOT reach, and may never be cited as covering:

      * SEMANTIC hazards. `INTEGER PRIMARY KEY DESC` is valid SQL, executes
        without complaint, and silently nulls every id (measured — see
        `_parse_column`). Every parser guard that raises on a construct it
        cannot COMPARE is unaffected by this test; none may be relaxed
        because "the SQL executes".
      * Block-discovery failures. DDL this file never finds is DDL this test
        never runs. `_section_blocks` is what stands there.
      * Drift. Executing cleanly says nothing about whether the two sides
        agree; that is `test_schema_matches_spec`.

    Each block gets its own connection, and foreign keys are not resolved:
    SQLite accepts a REFERENCES naming a table that does not exist (measured,
    even with `PRAGMA foreign_keys=ON`), so a block that came to reference a
    table declared in the other spec file would still execute. Neither block
    does so today.
    """
    for label, raw in _spec_ddl_blocks():
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(raw)
        except sqlite3.Error as exc:
            raise AssertionError(
                f"{label}: SQLite rejected the canonical DDL — {exc}. The spec"
                " block is not valid SQL, so it cannot be what `state/`"
                " actually runs; fix the DDL in the spec.") from None
        finally:
            conn.close()


def test_schema_sql_survives_a_second_executescript():
    """Every statement in state/schema.sql must be idempotent — not just the
    CREATE TABLEs.

    state/db.py's connect() re-runs the WHOLE file whenever ANY ONE expected
    table is missing (the `_TABLES <= have` guard), which is the mechanism by
    which a table added here reaches an existing database with no migration
    (pinned from the other side by tests/test_state.py:199). That mechanism
    runs every OTHER statement in the file a second time as well.

    The route this closes was opened by issue #172, which added this file's
    first CREATE INDEX statements: `CREATE INDEX idx_trials_family` without
    IF NOT EXISTS raises "index idx_trials_family already exists" on the second
    pass. The symptom would be connect() failing for every live database the
    moment some LATER lane adds an unrelated table — a failure with no visible
    connection to the index that caused it.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA.read_text())
        conn.executescript(SCHEMA.read_text())
    finally:
        conn.close()


@pytest.mark.parametrize("table", _bound_or_sentinel())
def test_schema_matches_spec(table):
    """state/schema.sql and the canonical spec §2 declare the same structure."""
    con = _contract()  # re-raises a parse failure as a failure of THIS test
    assert table != _PARSE_FAILED, (
        "the DDL failed to parse at collection but parses now — a source file"
        " changed mid-run. Re-run the suite; do not read this as a pass.")
    if table not in con.schema_tables:
        pytest.skip("covered by test_every_spec_table_is_declared_in_schema")
    problems = _diff(con, table)
    assert not problems, (f"{table}: state/schema.sql has drifted from"
                          f" {con.spec_source[table]}\n  "
                          + "\n  ".join(problems))

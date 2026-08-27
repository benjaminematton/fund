#!/usr/bin/env python3
"""Purity lint (CI-enforced; see CLAUDE.md invariant 3 and specs/design.md §4).

Checks over the no-LLM business-logic packages listed in PURE_PACKAGES:

  1. Forbidden imports: LLM/SDK/Slack code (claude_agent_sdk, anthropic,
     slack_bolt, slack_sdk), anything from agents/, and `slackkit.real`.
     Matching is dotted-prefix, not root-module: `slackkit.real.helpers` is
     forbidden, `slackkit.realtime` and `slackkit.outbox` are not. Relative
     imports are resolved against the containing package, because every
     intra-package import in slackkit/ is written relative — a rule that only
     understood absolute spellings would guard the form nobody writes.
     A forbidden module reached by attribute (`import slackkit` then
     `slackkit.real.RealSlack()`) is the same violation. It is reported once
     per attribute prefix that lands inside the forbidden module, so
     `slackkit.real.a.b.c` yields four lines naming one root cause.
  2. No dynamic imports: importlib.import_module() and __import__(). A pure
     package has to stay statically analyzable, and the argument may be
     computed ("claude" + "_agent_sdk"), so the call itself is the violation.
  3. No wall clock: datetime.now()/utcnow()/today(), date.today(),
     pandas.Timestamp.now()/today(), asyncio.sleep(), and the time module's
     sleep, counters and clock readers. strftime/asctime/ctime/gmtime/localtime
     read the clock only when called with too few arguments to render a
     supplied value; given one they are pure converters and are left alone
     (see ARITY_PURE_FROM).
  4. slackkit/__init__.py stays import-free, so `import slackkit` executes
     nothing (slackkit/real.py:1-3). That file is NOT on its own sufficient to
     keep slack_sdk out of linted code — it never stops `from slackkit.real
     import RealSlack`. Rule 1's `slackkit.real` entry is what stops that, and
     `slackkit/` is itself linted as a pure package with real.py excluded,
     because real.py is the one file that is supposed to hold the SDK.

Matching is on *resolved bindings*, not source spellings, and on *references*,
not only calls:

  * `import time as _t; _t.sleep(1)` and `from time import sleep; sleep(1)`
    resolve to time.sleep and are caught.
  * `_sleep = time.sleep`, `{"ts": datetime.utcnow}` and `getattr(time,
    "sleep")` are caught without ever being called — capturing the callable is
    the same banned dependency as calling it.
  * The injected `self._clock.now()` stays green because `self` resolves to
    nothing, not because of any shadowing rule. The converse does NOT hold, so
    do not restate this as "resolving to no import is a pass": under a star
    import an unbound name is deliberately still matched against every watched
    base, which is how re-export laundering is caught (see _candidates).

Zero dependencies. Exit 1 on any violation. A package directory that does not
exist is skipped, so a package can be added to the list before it is written.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PURE_PACKAGES = ["gate", "stratgate", "fundbt", "calibration", "orchestrator",
                 "state", "market", "slackkit", "devcheck"]
# Paths (relative to ROOT, posix) inside a pure package that are exempt.
EXCLUDED_FILES = {"slackkit/real.py"}
# Matched as dotted prefixes: "agents" covers agents.runtime, and
# "slackkit.real" covers slackkit.real.helpers but not slackkit.realtime.
FORBIDDEN_IMPORTS = ("claude_agent_sdk", "anthropic", "slack_bolt", "slack_sdk",
                     "agents", "slackkit.real")
# Fully-qualified names that read the wall clock or block the thread. Flagged
# on *reference*, not just on call.
FORBIDDEN_REFS = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today",
    "datetime.date.today",
    "time.sleep", "time.time", "time.time_ns", "time.monotonic",
    "time.monotonic_ns", "time.perf_counter", "time.perf_counter_ns",
    "time.process_time", "time.process_time_ns", "time.thread_time",
    "time.thread_time_ns", "time.clock_gettime", "time.clock_gettime_ns",
    "asyncio.sleep",
    "pandas.Timestamp.now", "pandas.Timestamp.today", "pandas.Timestamp.utcnow",
}
# Names that read the wall clock ONLY when called with too few arguments to
# render or convert a supplied value: `time.ctime()` is now, `time.ctime(secs)`
# is a formatter; `time.gmtime()` is now, `time.gmtime(epoch)` is a converter.
# The value is the argument count at which the call becomes pure. Bare
# references to these are deliberately not flagged — the spelling alone does
# not say which form is meant.
#
# gmtime and localtime belong here and sat in FORBIDDEN_REFS unconditionally
# until the docstring's own example failed on itself: in
# `time.strftime("%Y", time.gmtime(0))` the outer call was correctly pure while
# the inner one it was feeding got flagged.
ARITY_PURE_FROM = {"time.strftime": 2, "time.asctime": 1, "time.ctime": 1,
                   "time.gmtime": 1, "time.localtime": 1}
# Fully-qualified names that import at runtime, defeating this lint entirely.
DYNAMIC_IMPORT_REFS = {"importlib.import_module", "importlib.__import__",
                       "builtins.__import__", "__import__"}

# Nodes that open a new binding scope.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Scopes where a rebinding does NOT hide the import from a use site, so the
# import binding STANDS rather than being popped. See the discriminator on
# _scan_scope.
_BINDING_STANDS_SCOPES = (ast.Module, ast.ClassDef)


def _watched_bases() -> set[str]:
    """Every dotted name a forbidden reference hangs off: `datetime`,
    `datetime.datetime`, `time`, `pandas.Timestamp`, ... Used to resolve a name
    a star import could have supplied from anywhere."""
    bases: set[str] = set()
    for ref in FORBIDDEN_REFS | DYNAMIC_IMPORT_REFS | set(ARITY_PURE_FROM):
        parts = ref.split(".")
        for i in range(1, len(parts)):
            bases.add(".".join(parts[:i]))
    return bases


WATCHED_BASES = _watched_bases()


def _forbidden_import(name: str) -> str | None:
    """Dotted-prefix match against FORBIDDEN_IMPORTS.

    Boundary-aware on purpose: a raw `name.startswith("slackkit.real")` would
    also redden `slackkit.realtime`. Root-module comparison is the opposite
    error — it can never match a dotted entry at all, which would leave
    "slackkit.real" sitting in the tuple looking like a rule and matching
    nothing, forever.
    """
    for entry in FORBIDDEN_IMPORTS:
        if name == entry or name.startswith(entry + "."):
            return entry
    return None


def _dotted(node: ast.AST) -> str | None:
    """A Name/Attribute chain as "a.b.c"; anything else -> None.

    `getattr(x, "lit")` is folded in as a spelling of `x.lit`, so a string
    lookup is not a way around an attribute match.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)):
        base = _dotted(node.args[0])
        return f"{base}.{node.args[1].value}" if base else None
    if isinstance(node, ast.NamedExpr):
        # A walrus EVALUATES to its value, so `(time := _src).sleep` is
        # `_src.sleep`. Without this the return-expression shape resolves to
        # nothing, because that walrus sits in no ast.Assign for the alias pass
        # below to find. Deletion-check: remove these two lines on a copy and
        # "walrus alias in a return expression" goes clean while the function,
        # module, class and chained shapes stay red — the alias pass covers
        # those, and only this arm covers a walrus used as an attribute base.
        return _dotted(node.value)
    return None


def _candidates(dotted: str, bindings: dict[str, str | None],
                stars: list[str]) -> list[str]:
    """Fully-qualified names `dotted` could refer to in this scope.

    A name present in `bindings` with value None is bound to something
    unresolvable (a parameter, a local, an unresolved relative import) — it
    resolves to nothing and must NOT fall through to a star-import guess.

    Under a star import the head could have been re-exported from anywhere, so
    two guesses are added: the star module itself, and any watched base whose
    last segment matches the head. The second is what catches laundering —
    `from state.helpers import *` followed by `datetime.now()` — without
    following imports across modules, which stays out of scope (issue #69).
    """
    head, _, rest = dotted.partition(".")
    if head in bindings:
        base = bindings[head]
        if base is None:
            return []
        return [f"{base}.{rest}" if rest else base]
    if not stars:
        return []
    out = [f"{module}.{dotted}" for module in stars]
    for base in WATCHED_BASES:
        if base.rsplit(".", 1)[-1] == head:
            out.append(f"{base}.{rest}" if rest else base)
    return out


def _resolve_relative(node: ast.ImportFrom, package: tuple[str, ...]) -> str | None:
    """`from .real import X` inside slackkit/ -> "slackkit.real".

    Without this, the only spelling the slackkit.real ban would catch is the
    absolute one, which no file in slackkit/ uses.
    """
    if node.level == 0:
        return node.module
    if node.level > len(package):
        return None
    base = package[:len(package) - node.level + 1]
    return ".".join([*base, *([node.module] if node.module else [])])


def _import_bindings(node: ast.Import | ast.ImportFrom,
                     package: tuple[str, ...]) -> dict[str, str | None]:
    """{local name: fully-qualified target}. None means "bound to something
    unresolvable", which must clear an inherited binding of that name rather
    than leave it standing."""
    out: dict[str, str | None] = {}
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                out[alias.asname] = alias.name
            else:
                root = alias.name.split(".")[0]
                out[root] = root
        return out
    module = _resolve_relative(node, package)
    for alias in node.names:
        if alias.name == "*":
            # No observable behaviour: "*" is not an identifier, so it can
            # never come back out of _dotted or _bound_names, and no test can
            # pin this branch. Differential-tested across 46 real files and 56
            # snippets with zero diffs. It is kept because the next reader of
            # _import_bindings would otherwise have to re-derive that, and
            # deleted it would read as an oversight rather than a decision.
            continue
        name = alias.asname or alias.name
        out[name] = f"{module}.{alias.name}" if module else None
    return out


def _bound_names(node: ast.AST) -> set[str]:
    """Names this node binds by means other than an import: assignment, walrus,
    parameter, def/class, `except ... as`, `with ... as`, comprehension or
    match/case capture."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, ast.arg):
        return {node.arg}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    return set()


def _outer_exprs(node: ast.AST):
    """Sub-expressions of a scope node that Python evaluates in the ENCLOSING
    scope, before the new scope's bindings exist. Scanning these inside the
    child is how `def stamp(datetime=datetime)` — which really does return the
    wall clock — reads as clean."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        yield from getattr(node, "decorator_list", [])
        yield from args.defaults
        yield from [d for d in args.kw_defaults if d is not None]
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                    args.vararg, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                yield arg.annotation
        if getattr(node, "returns", None) is not None:
            yield node.returns
    elif isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        yield from [kw.value for kw in node.keywords]
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        if node.generators:
            yield node.generators[0].iter


def _inner_children(node: ast.AST) -> list[ast.AST]:
    """Children of a scope node evaluated INSIDE its own scope."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        params = [a for a in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                              args.vararg, args.kwarg] if a is not None]
        body = node.body if isinstance(node.body, list) else [node.body]
        return [*params, *body]
    if isinstance(node, ast.ClassDef):
        return list(node.body)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        parts: list[ast.AST] = ([node.key, node.value]
                                if isinstance(node, ast.DictComp) else [node.elt])
        for index, gen in enumerate(node.generators):
            parts.append(gen.target)
            if index:  # only the OUTERMOST iterable is evaluated outside
                parts.append(gen.iter)
            parts.extend(gen.ifs)
        return parts
    return list(ast.iter_child_nodes(node))


def _walk_in_scope(node: ast.AST):
    yield node
    if isinstance(node, ast.arg):
        return  # its annotation belongs to the enclosing scope
    if isinstance(node, _SCOPE_NODES):
        for expr in _outer_exprs(node):
            yield from _walk_in_scope(expr)
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_in_scope(child)


def _scope_nodes(node: ast.AST):
    for child in _inner_children(node):
        yield from _walk_in_scope(child)


def _walrus_targets(node: ast.AST):
    """Names a walrus binds in the scope CONTAINING `node`.

    PEP 572 gives an assignment expression inside a comprehension to the
    containing scope rather than the comprehension's, so `[(time := r) for r
    in rows]` really does leave a local named `time` behind. Nested function
    and class scopes are not crossed — a walrus inside one belongs to it.

    The caller also uses this to SUPPRESS the target inside the comprehension's
    own scope. Both halves are required: a use inside the comprehension
    resolves to the containing scope too, so popping the name locally would
    hide `[(time := time.sleep(0.30)) for _ in range(1)]`, which blocks for a
    third of a second on a real clock.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
            yield child.target.id
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
            yield from _walrus_targets(child)


def _param_defaults(node: ast.AST):
    """(parameter, default expression) pairs. The default is evaluated in the
    enclosing scope, so the parameter really holds whatever it resolves to."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if args.defaults:
        yield from zip(positional[len(positional) - len(args.defaults):], args.defaults)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            yield arg, default


def _scan_scope(node: ast.AST, inherited: dict[str, str | None],
                inherited_stars: list[str], path: Path, errors: list[str],
                package: tuple[str, ...],
                module_bindings: dict[str, str | None] | None = None) -> None:
    own = list(_scope_nodes(node))
    bindings = dict(inherited)
    stars = list(inherited_stars)

    imported: dict[str, str | None] = {}
    for child in own:
        if isinstance(child, ast.ImportFrom) and any(a.name == "*" for a in child.names):
            module = _resolve_relative(child, package)
            if module:
                stars.append(module)
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            imported.update(_import_bindings(child, package))
    bindings.update(imported)

    if module_bindings is None:  # this IS the module scope
        module_bindings = bindings

    rebound: set[str] = set()
    declared_global: set[str] = set()
    declared_nonlocal: set[str] = set()
    for child in own:
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(child, ast.Global):
            declared_global.update(child.names)
        if isinstance(child, ast.Nonlocal):
            declared_nonlocal.update(child.names)
        rebound.update(_bound_names(child))
        # PEP 572: a walrus target inside a comprehension binds in the
        # CONTAINING scope, so it leaks back out to here. A plain `for` target
        # does not, which is why the two cannot share a row.
        if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp)):
            rebound.update(_walrus_targets(child))

    # THE DISCRIMINATOR: can the rebinding make a use site resolve to the
    # import? Not "is it rare" and not "is it suspicious" — reachability.
    #
    #   module scope ....... YES. Rebind and use share one namespace, and a use
    #                        above the rebind reaches the real module.
    #   class body ......... YES. LOAD_NAME falls back to the global, so
    #                        `a = time.time()` above `time = None` is real.
    #   enclosing-scope
    #     positions ........ YES, handled by _outer_exprs: defaults,
    #                        decorators, annotations, base lists and a
    #                        comprehension's outermost iterable are evaluated
    #                        before the new scope's bindings exist.
    #   function body ...... NO, UNLESS the same scope also imports the name.
    #                        Binding a name anywhere in a function makes it
    #                        local for the whole body, so a use before the
    #                        binding raises UnboundLocalError rather than
    #                        reaching the module — parameters, `except ... as`
    #                        and `match`/`case` captures are alike here. But an
    #                        `import` in that same body IS a binding, and it
    #                        assigns the module to that local, so a use between
    #                        the import and the rebinding reads the real thing.
    #   comprehension
    #     `for` target ..... NO. Python 3 scopes it to the comprehension.
    #   comprehension
    #     WALRUS target .... Two separate effects, and neither follows from the
    #                        `for` row. PEP 572 binds the target in the
    #                        CONTAINING scope, so (a) it leaks OUT and lands
    #                        under whichever row governs that scope, and (b) a
    #                        use INSIDE the comprehension resolves out there
    #                        too — so the name must NOT be popped locally.
    #                        Missing (b) hid six executing clock reads.
    #   `global x` ......... YES. The declared binding is the MODULE's, so the
    #                        privacy the function-body row rests on does not
    #                        hold: a read before the rebinding returns a live
    #                        value instead of raising UnboundLocalError.
    #   `nonlocal x` ....... YES, onto the ENCLOSING FUNCTION's binding. This
    #                        row previously read "unaffected — nonlocal can
    #                        only bind an enclosing function's local, never an
    #                        import". The premise is true and the conclusion
    #                        does not follow: `import time` inside a function
    #                        is a function-local binding whose value IS the
    #                        module, so nonlocal reaches it. Executed: the
    #                        inner function blocks 0.305s. Where the enclosing
    #                        local is not an import there is nothing to reach
    #                        and it stays clean, which is the committed twin.
    #
    # The enclosing-scope row covers two different mechanisms. Defaults,
    # decorators and base lists are EVALUATED at definition time, before the
    # new scope's bindings exist. Annotations are not evaluated at all here —
    # this file carries `from __future__ import annotations`, and on 3.14 they
    # are lazy regardless — but the verdict is the same, because PEP 649's
    # annotate function still closes over the enclosing scope. Deferred
    # resolution, not evaluation; do not restate the annotation arm as
    # something confirmed by executing it.
    #
    # There is deliberately NO report attached to any of this. A collision
    # report lived here and was deleted after ablation showed it caught no
    # purity violation the reference check did not already catch, while
    # false-positiving on class-body fields that never use the name. What is
    # load-bearing is only that the binding STANDS, so the reference check can
    # still resolve through the rebind. Do not reintroduce a report, a warning
    # or a flag.
    # A walrus target belongs to the CONTAINING scope, so inside the
    # comprehension itself it must not be treated as a local rebinding — a use
    # there reads the containing scope's binding, import included.
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        rebound -= set(_walrus_targets(node))

    stands = isinstance(node, _BINDING_STANDS_SCOPES)
    # `| declared_global` and not just `rebound`: a `global x` DECLARATION
    # rebinds resolution on its own, with or without an assignment anywhere in
    # the body. Iterating `rebound` alone made the assignment load-bearing, so
    # `global time` + a bare `time.sleep(0.30)` kept the enclosing scope's
    # shadow and read the real clock while the lint said clean. Deletion-check:
    # revert to `for name in rebound:` on a copy and the assignment-free case
    # in BINDING_STANDS_CASES goes clean while its with-assignment twin stays
    # red — which is exactly why one case could not pin both.
    for name in rebound | declared_global:
        if name in declared_nonlocal:
            continue  # the enclosing function's binding, which `inherited` holds
        if name in declared_global:
            # `import x` in a scope that declared `global x` binds the MODULE's
            # x to the module object, so this scope's own import wins over
            # whatever the module scope held. Overwriting unconditionally
            # clobbered it: `global time` + a function-local `import time` read
            # 0.306s of real clock while the lint said clean. Deletion-check:
            # drop the `not in imported` guard on a copy and the two
            # A3=global/A4=import rows in the generated gate go clean again.
            if name not in imported:
                bindings[name] = module_bindings.get(name)
            continue
        # `name in imported` is the function-body exception: an import in this
        # same body binds the module to that local, so a use between the two
        # reaches it.
        if (not stands and name not in imported) or name not in bindings:
            bindings[name] = None

    # `_clock_mod = time` / `_sleep = time.sleep`: an alias is the thing it
    # aliases. Runs after the pass above so a real alias beats the None.
    for child in own:
        if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            dotted = _dotted(child.value)
            hits = _candidates(dotted, bindings, stars) if dotted else []
            if len(hits) == 1:
                bindings[targets[0].id] = hits[0]
        # `(time := _src)` aliases exactly as `time = _src` does. NamedExpr was
        # known to this file only as a SCOPING event (_walrus_targets), so a
        # walrus registered as a binder — popping or standing the name — while
        # the module it named was discarded. Not restricted to walruses inside
        # an Assign: `own` carries them from any statement, which is what makes
        # the `if`/`while`-condition spellings work as well.
        # Deletion-check: drop this branch on a copy and the function, module,
        # class and chained walrus-alias cases go clean, while the
        # return-expression case stays red on the _dotted arm above.
        elif isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
            dotted = _dotted(child.value)
            hits = _candidates(dotted, bindings, stars) if dotted else []
            if len(hits) == 1:
                bindings[child.target.id] = hits[0]

    # A default is evaluated outside, so the parameter holds what it resolved
    # to there — `def stamp(datetime=datetime)` really does read the clock.
    for arg, default in _param_defaults(node):
        dotted = _dotted(default)
        hits = _candidates(dotted, inherited, inherited_stars) if dotted else []
        if len(hits) == 1:
            bindings[arg.arg] = hits[0]

    for child in own:
        if isinstance(child, ast.Call):
            dotted = _dotted(child.func)
            for target in _candidates(dotted, bindings, stars) if dotted else []:
                if target in ARITY_PURE_FROM and (
                        len(child.args) + len(child.keywords) < ARITY_PURE_FROM[target]):
                    errors.append(f"{path}:{child.lineno}: wall-clock/sleep reference "
                                  f"'{dotted}()' resolves to {target}() with no value "
                                  f"to render, which reads the clock — inject Clock")
        if isinstance(child, (ast.Name, ast.Attribute)) and not isinstance(child.ctx, ast.Load):
            continue
        if not isinstance(child, (ast.Name, ast.Attribute, ast.Call)):
            continue
        dotted = _dotted(child)
        if not dotted:
            continue
        for target in _candidates(dotted, bindings, stars):
            if target in FORBIDDEN_REFS:
                errors.append(f"{path}:{child.lineno}: wall-clock/sleep reference "
                              f"'{dotted}' resolves to {target} — inject Clock instead")
            elif target in DYNAMIC_IMPORT_REFS:
                errors.append(f"{path}:{child.lineno}: dynamic import '{dotted}' — "
                              f"a pure package must be statically analyzable")
            elif _forbidden_import(target):
                errors.append(f"{path}:{child.lineno}: forbidden import reached by "
                              f"attribute — '{dotted}' resolves to {target}")

    # Class scope is not visible inside methods (`class Row: date = None` does
    # not shadow a module-level `from datetime import date` for `Row.stamp`),
    # so nested scopes inherit from the class's *enclosing* scope.
    child_bindings = inherited if isinstance(node, ast.ClassDef) else bindings
    child_stars = inherited_stars if isinstance(node, ast.ClassDef) else stars
    for child in own:
        if isinstance(child, _SCOPE_NODES):
            _scan_scope(child, child_bindings, child_stars, path, errors, package,
                        module_bindings)


def check_file(path: Path, package: tuple[str, ...] = ()) -> list[str]:
    """`package` is the dotted package the file sits in, used to resolve
    relative imports. Empty means "unknown", which leaves them unresolved."""
    errors: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_import(alias.name):
                    errors.append(f"{path}:{node.lineno}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_relative(node, package)
            if not module:
                continue
            if _forbidden_import(module):
                errors.append(f"{path}:{node.lineno}: forbidden import 'from {module}'")
            else:
                for alias in node.names:
                    if alias.name != "*" and _forbidden_import(f"{module}.{alias.name}"):
                        errors.append(f"{path}:{node.lineno}: forbidden import "
                                      f"'from {module} import {alias.name}'")
    _scan_scope(tree, {"__import__": "__import__"}, [], path, errors, package)
    return errors


def check_slackkit_init(root: Path) -> list[str]:
    """slackkit/__init__.py must execute no imports at all — not just no
    forbidden ones. An __init__ that imports is one that can grow a slack_sdk
    import, and every `import slackkit` would inherit it. This is a narrower
    claim than it looks: it does nothing about `from slackkit.real import X`,
    which FORBIDDEN_IMPORTS covers instead."""
    init = root / "slackkit" / "__init__.py"
    if not init.is_file():
        return []
    tree = ast.parse(init.read_text(), filename=str(init))
    return [f"{init}:{node.lineno}: slackkit/__init__.py must stay import-free "
            f"so `import slackkit` executes nothing (slackkit/real.py:1-3)"
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
            relative = py.relative_to(ROOT)
            if relative.as_posix() in EXCLUDED_FILES:
                continue
            checked += 1
            errors.extend(check_file(py, relative.parts[:-1]))
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

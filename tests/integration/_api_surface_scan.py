"""Scrape #1 of the API-surface drift test: statically scans
AICopilot/handlers/*.py for every ``doc.addObject("Type::String", ...)`` /
``body.newObject("Type::String", ...)`` call site, and for each one, the
distinct properties the handler code actually sets on the result.

This produces the *scope table* — which (type, property) pairs are worth
drift-checking at all — derived mechanically from our own source, not by
guessing or hand-curating. It is always computed fresh from the current
handler source (see test_api_surface.py), never itself checked in; only
the golden snapshot of *expected* FreeCAD-side values is.

Branch-aware: a variable name reused across mutually-exclusive
``if body: ... else: ...`` branches (the fillet/chamfer/thickness/
revolution Body-vs-standalone pattern) must not have both branches'
properties collapsed onto whichever type was assigned last — each branch
gets its own copy of the variable->type bindings.
"""

import ast
import glob
import os
from typing import Dict, Set

HANDLERS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "AICopilot", "handlers"
)


def _type_id_from_call(node: ast.AST) -> str | None:
    """Return the literal "Type::String" argument of an addObject/newObject
    call, or None if this call isn't one of those (or the type isn't a
    literal string, e.g. it's built dynamically — out of scope for static
    scanning, dropped-with-reason rather than silently guessed at)."""
    if not isinstance(node, ast.Call):
        return None
    func_name = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if func_name not in ("addObject", "newObject") or not node.args:
        return None
    arg0 = node.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str) and "::" in arg0.value:
        return arg0.value
    return None


def _record_type_call(type_id: str, type_properties: Dict[str, Set[str]],
                       type_call_sites: Dict[str, int]) -> None:
    """Register a Type::String call site with zero properties (yet) -- the
    same bookkeeping needed whether the call is bound to a variable
    (Assign), handed straight back (Return), or fire-and-forget (a bare
    expression statement)."""
    type_call_sites[type_id] = type_call_sites.get(type_id, 0) + 1
    type_properties.setdefault(type_id, set())


def _walk_stmts(stmts, bindings: Dict[str, str], type_properties: Dict[str, Set[str]],
                 type_call_sites: Dict[str, int]) -> None:
    """Process a statement list with its own copy of var->type bindings, so
    mutually-exclusive branches (if/else, try/except) don't leak into each
    other. `bindings` is copied on entry, never mutated in the caller's view."""
    bindings = dict(bindings)
    for stmt in stmts:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            type_id = _type_id_from_call(stmt.value)
            if type_id:
                _record_type_call(type_id, type_properties, type_call_sites)
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = type_id
                continue

        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    varname = target.value.id
                    if varname in bindings:
                        type_properties[bindings[varname]].add(target.attr)
            continue

        # `return doc.addObject(...)` or a bare fire-and-forget
        # `doc.addObject(...)` statement: the type_id itself is still worth
        # recording (with zero properties, since nothing captures a name to
        # hang a later `.Prop = ...` off of) -- neither has a nested block
        # to recurse into, so both just record-and-continue.
        if isinstance(stmt, (ast.Return, ast.Expr)) and stmt.value is not None:
            type_id = _type_id_from_call(stmt.value)
            if type_id:
                _record_type_call(type_id, type_properties, type_call_sites)
            continue

        if isinstance(stmt, ast.If):
            _walk_stmts(stmt.body, bindings, type_properties, type_call_sites)
            _walk_stmts(stmt.orelse, bindings, type_properties, type_call_sites)
        elif isinstance(stmt, (ast.For, ast.While)):
            _walk_stmts(stmt.body, bindings, type_properties, type_call_sites)
            _walk_stmts(stmt.orelse, bindings, type_properties, type_call_sites)
        elif isinstance(stmt, ast.Try):
            _walk_stmts(stmt.body, bindings, type_properties, type_call_sites)
            for handler in stmt.handlers:
                _walk_stmts(handler.body, bindings, type_properties, type_call_sites)
            _walk_stmts(stmt.orelse, bindings, type_properties, type_call_sites)
            _walk_stmts(stmt.finalbody, bindings, type_properties, type_call_sites)
        elif isinstance(stmt, ast.With):
            _walk_stmts(stmt.body, bindings, type_properties, type_call_sites)
        # Nested defs are walked separately when the outer scan reaches them
        # top-level (ast.walk still finds them); their own bindings must not
        # inherit the enclosing function's, so we deliberately don't recurse
        # into FunctionDef/AsyncFunctionDef/ClassDef bodies here.


def scan_type_properties(handlers_dir: str = HANDLERS_DIR) -> Dict[str, Set[str]]:
    """Return {type_id: {property_names actually set by handler code}} for
    every literal "Type::String" passed to addObject/newObject across
    handlers_dir. Types with zero properties set (pure containers like
    PartDesign::Body, populated via .Group/.addObject rather than scalar
    properties) appear with an empty set, not omitted."""
    type_properties: Dict[str, Set[str]] = {}
    type_call_sites: Dict[str, int] = {}

    for path in sorted(glob.glob(os.path.join(handlers_dir, "*.py"))):
        with open(path) as f:
            src = f.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk_stmts(node.body, {}, type_properties, type_call_sites)

    return type_properties


if __name__ == "__main__":
    import json
    table = scan_type_properties()
    print(f"{len(table)} distinct Type::String identifiers, "
          f"{sum(len(v) for v in table.values())} total (type, property) pairs\n")
    print(json.dumps({k: sorted(v) for k, v in sorted(table.items())}, indent=2))

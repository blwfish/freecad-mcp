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
gets its own copy of the variable->type bindings. Where BOTH branches of an
if/else bind the same variable to the *same* type (e.g. create_lcs's
``lcs = doc.addObject(...)`` / ``lcs = container.newObject(...)``, identical
type on both sides), that binding is safe to propagate back to the
enclosing scope for statements *after* the if/else -- exactly one branch
always runs, so the variable is unconditionally bound to that type by the
time execution reaches those later statements. This was previously a real
blind spot (full-review 2026-07-24 finding #02): create_lcs's MapMode/
AttachmentSupport/AttachmentOffset assignments happen after the if/else,
so with no propagation the scanner recorded zero properties for
Part::LocalCoordinateSystem at all, confirmed via a live scan_type_properties()
run against the real repo.
"""

import ast
import glob
import os
from typing import Dict, Set, Tuple

HANDLERS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "AICopilot", "handlers"
)

# self.<method>(...) call patterns whose return type this scanner can't
# infer generally (that would require real interprocedural analysis, out of
# scope for a lightweight AST scan) but is worth naming explicitly for this
# one well-understood, file-local case: assembly_ops.py's
# `joint, err = self._resolve_joint(joint_name, doc)` always returns an
# App::FeaturePython carrying a JointObject Python proxy (the same type
# create_joint/ground_part create directly elsewhere in the file) -- without
# this, set_joint_limits's entire property surface (LengthMin/LengthMax/
# AngleMin/AngleMax/Enable*) was invisible to the scanner because `joint`
# never came from a literal addObject/newObject call in that function body
# (full-review 2026-07-24 finding #14).
_HELPER_RETURN_TYPES = {
    "_resolve_joint": "App::FeaturePython",
}

# Draft.make_*() factory functions don't take a Type::String argument at
# all (unlike addObject/newObject), so their return type can't be read off
# the call site syntactically -- these were completely invisible to the
# scanner (full-review 2026-07-24 finding #16, deferred at the time for
# lack of live verification). Confirmed 2026-07-25 against a real spawned
# FreeCAD instance (FreeCADCmd, FC-clone release build):
#   Draft.make_clone(obj).TypeId        -> "Part::FeaturePython"
#   Draft.make_ortho_array(...).TypeId  -> "Part::FeaturePython"
#   Draft.make_polar_array(...).TypeId  -> "Part::FeaturePython"
#   Draft.make_path_array(...).TypeId   -> "Part::FeaturePython"
#   Draft.make_point_array(...).TypeId  -> "Part::FeaturePython"
#   Draft.make_text([...]).TypeId       -> "App::FeaturePython"
# Distinct from assembly_ops.py's "App::FeaturePython" (Joint/GroundedJoint)
# -- same type string, but that's a real FreeCAD fact (Draft.make_text and
# JointObject both happen to sit on the generic App::FeaturePython shell),
# not a collision to work around; their respective properties simply share
# one scope-table bucket, same as any other type used by two call sites.
#
# doc.copyObject(...) is NOT included here: unlike these, its return type
# is genuinely data-dependent (it copies whatever type the argument already
# is) and no amount of static analysis can determine that reliably. All 4
# real call sites in this codebase (partdesign_ops.py:541,592;
# transforms.py:113,147) pass an object obtained via self.get_object(...) --
# a runtime document lookup, not a locally-created binding -- confirmed by
# reading the source, so there's no local binding to propagate through
# either. Left un-scanned, deliberately, not by oversight.
_DRAFT_FACTORY_TYPES = {
    "make_clone": "Part::FeaturePython",
    "make_ortho_array": "Part::FeaturePython",
    "make_polar_array": "Part::FeaturePython",
    "make_path_array": "Part::FeaturePython",
    "make_point_array": "Part::FeaturePython",
    "make_text": "App::FeaturePython",
}


def _type_id_from_call(node: ast.AST) -> str | None:
    """Return the literal "Type::String" argument of an addObject/newObject
    call, or None if this call isn't one of those (or the type isn't a
    literal string, e.g. it's built dynamically — out of scope for static
    scanning, dropped-with-reason rather than silently guessed at).

    Kept as a standalone single-result function (rather than folded into
    _type_ids_from_call below) because tests/unit/test_api_surface_scan.py
    imports and calls it directly with this exact signature."""
    if not isinstance(node, ast.Call):
        return None
    func_name = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if func_name not in ("addObject", "newObject") or not node.args:
        return None
    arg0 = node.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str) and "::" in arg0.value:
        return arg0.value
    return None


def _literal_type_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and "::" in node.value:
        return node.value
    return None


def _ternary_literal_type_strs(node: ast.AST) -> Tuple[str, ...]:
    """If node is ``"A" if cond else "B"`` with both sides literal
    '::'-containing strings, return (A, B); else ()."""
    if isinstance(node, ast.IfExp):
        a = _literal_type_str(node.body)
        b = _literal_type_str(node.orelse)
        if a and b:
            return (a, b)
    return ()


def _type_ids_from_call(node: ast.AST, type_string_vars: Dict[str, Tuple[str, ...]]) -> Tuple[str, ...]:
    """Extends _type_id_from_call to also resolve addObject/newObject calls
    whose type argument is a variable bound (in this same function scope)
    to a ternary of literal Type::Strings, e.g. add_component's
    ``link_type = "Assembly::AssemblyLink" if is_sub_assembly else
    "App::Link"; assembly.newObject(link_type, ...)``. Both branches are
    recorded as possible call sites rather than silently dropping the whole
    site because the type argument isn't a bare literal (full-review
    2026-07-24 finding #13 -- "Assembly::AssemblyLink" was previously
    invisible to this scanner entirely, only reachable via a comment grep).

    Returns a tuple (possibly empty, possibly length >1) rather than an
    Optional[str] -- callers must loop over the result."""
    single = _type_id_from_call(node)
    if single:
        return (single,)
    if not isinstance(node, ast.Call):
        return ()
    if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Draft" and node.func.attr in _DRAFT_FACTORY_TYPES):
        return (_DRAFT_FACTORY_TYPES[node.func.attr],)
    func_name = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if func_name not in ("addObject", "newObject") or not node.args:
        return ()
    arg0 = node.args[0]
    if isinstance(arg0, ast.Name) and arg0.id in type_string_vars:
        return type_string_vars[arg0.id]
    return ()


def _base_var_and_property(target: ast.AST):
    """For an assignment target that's an attribute chain (``obj.Prop = x``,
    or ``obj.Prop.SubField = x``), return (base_var_name, top_level_property)
    -- e.g. ``obj.Placement.Base`` -> ("obj", "Placement"). The top-level
    FreeCAD property is what ``_describe_properties``'s
    ``getTypeIdOfProperty()`` actually checks; a nested field like
    ``.Base``/``.Rotation`` isn't a property in its own right.

    Returns (None, None) if target isn't an attribute chain rooted in a
    plain Name (e.g. a subscript or call result -- out of scope for this
    static scan). Previously only single-level ``obj.Prop = x`` was
    recognized at all -- ``obj.Placement.Base = value`` (the standard way
    every primitives/transforms/draft/cam handler in this repo sets
    Placement) was silently invisible (full-review 2026-07-24 finding #03,
    confirmed via grep: ~35 live call sites across 10 handler files)."""
    if not isinstance(target, ast.Attribute):
        return None, None
    chain = []
    node = target
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None, None
    return node.id, chain[-1]


def _record_type_call(type_id: str, type_properties: Dict[str, Set[str]],
                       type_call_sites: Dict[str, int]) -> None:
    """Register a Type::String call site with zero properties (yet) -- the
    same bookkeeping needed whether the call is bound to a variable
    (Assign), handed straight back (Return), or fire-and-forget (a bare
    expression statement)."""
    type_call_sites[type_id] = type_call_sites.get(type_id, 0) + 1
    type_properties.setdefault(type_id, set())


def _walk_stmts(stmts, bindings: Dict[str, Tuple[str, ...]],
                 type_string_vars: Dict[str, Tuple[str, ...]],
                 type_properties: Dict[str, Set[str]],
                 type_call_sites: Dict[str, int]):
    """Process a statement list with its own copy of var->type(s) bindings,
    so mutually-exclusive branches (if/else, try/except) don't leak into
    each other. `bindings`/`type_string_vars` are copied on entry, never
    mutated in the caller's view directly -- but the RETURNED dicts reflect
    this statement list's cumulative effect, which the ast.If handling
    below uses to safely propagate a binding both branches agree on back
    into the enclosing scope.

    Returns (bindings, type_string_vars) as they stand after processing
    every statement in `stmts` in order."""
    bindings = dict(bindings)
    type_string_vars = dict(type_string_vars)
    for stmt in stmts:
        if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr in _HELPER_RETURN_TYPES):
            type_id = _HELPER_RETURN_TYPES[stmt.value.func.attr]
            for target in stmt.targets:
                if isinstance(target, ast.Tuple) and target.elts and isinstance(target.elts[0], ast.Name):
                    bindings[target.elts[0].id] = (type_id,)
                elif isinstance(target, ast.Name):
                    bindings[target.id] = (type_id,)
            continue

        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            type_ids = _type_ids_from_call(stmt.value, type_string_vars)
            if type_ids:
                for type_id in type_ids:
                    _record_type_call(type_id, type_properties, type_call_sites)
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = type_ids
                continue

        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.IfExp):
            ternary_strs = _ternary_literal_type_strs(stmt.value)
            if ternary_strs:
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        type_string_vars[target.id] = ternary_strs
                continue

        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                varname, prop = _base_var_and_property(target)
                if varname and varname in bindings:
                    for type_id in bindings[varname]:
                        type_properties[type_id].add(prop)
            continue

        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            # setattr(var, "PropName", value) -- a call, not an Assign, so
            # invisible to the branch above. 2 confirmed live call sites in
            # assembly_ops.py's set_joint_offset (full-review 2026-07-24
            # finding #14).
            if (isinstance(call.func, ast.Name) and call.func.id == "setattr"
                    and len(call.args) >= 2
                    and isinstance(call.args[0], ast.Name)
                    and isinstance(call.args[1], ast.Constant)
                    and isinstance(call.args[1].value, str)):
                varname = call.args[0].id
                prop = call.args[1].value
                if varname in bindings:
                    for type_id in bindings[varname]:
                        type_properties[type_id].add(prop)
                continue

        # `return doc.addObject(...)` or a bare fire-and-forget
        # `doc.addObject(...)` statement: the type_id itself is still worth
        # recording (with zero properties, since nothing captures a name to
        # hang a later `.Prop = ...` off of) -- neither has a nested block
        # to recurse into, so both just record-and-continue.
        if isinstance(stmt, (ast.Return, ast.Expr)) and stmt.value is not None:
            type_ids = _type_ids_from_call(stmt.value, type_string_vars)
            for type_id in type_ids:
                _record_type_call(type_id, type_properties, type_call_sites)
            continue

        if isinstance(stmt, ast.If):
            body_bindings, body_tsv = _walk_stmts(
                stmt.body, bindings, type_string_vars, type_properties, type_call_sites)
            else_bindings, else_tsv = _walk_stmts(
                stmt.orelse, bindings, type_string_vars, type_properties, type_call_sites)
            # Merge back into THIS scope only where both branches agree on
            # the exact same type(s) for a name -- exactly one of the two
            # branches always runs, so that binding holds unconditionally
            # for statements after the if/else. A name bound differently
            # (or only in one branch, e.g. no `else:` at all) stays
            # branch-local, preserving the original mutually-exclusive-
            # branch isolation this function exists for.
            for varname, type_ids in body_bindings.items():
                if varname in else_bindings and else_bindings[varname] == type_ids:
                    bindings[varname] = type_ids
            for varname, strs in body_tsv.items():
                if varname in else_tsv and else_tsv[varname] == strs:
                    type_string_vars[varname] = strs
        elif isinstance(stmt, (ast.For, ast.While)):
            # A loop body may run zero times -- unlike if/else, there's no
            # guarantee either branch's bindings hold afterward, so these
            # stay branch-local (no merge-back).
            _walk_stmts(stmt.body, bindings, type_string_vars, type_properties, type_call_sites)
            _walk_stmts(stmt.orelse, bindings, type_string_vars, type_properties, type_call_sites)
        elif isinstance(stmt, ast.Try):
            # Whether a handler runs (and which one) is data-dependent --
            # stays branch-local, same reasoning as For/While.
            _walk_stmts(stmt.body, bindings, type_string_vars, type_properties, type_call_sites)
            for handler in stmt.handlers:
                _walk_stmts(handler.body, bindings, type_string_vars, type_properties, type_call_sites)
            _walk_stmts(stmt.orelse, bindings, type_string_vars, type_properties, type_call_sites)
            _walk_stmts(stmt.finalbody, bindings, type_string_vars, type_properties, type_call_sites)
        elif isinstance(stmt, ast.With):
            # Unlike If/For/Try, a `with` block always executes its body
            # exactly once -- no branching at all, so its bindings can
            # propagate directly, not just when two branches agree.
            bindings, type_string_vars = _walk_stmts(
                stmt.body, bindings, type_string_vars, type_properties, type_call_sites)
        # Nested defs are walked separately when the outer scan reaches them
        # top-level (ast.walk still finds them); their own bindings must not
        # inherit the enclosing function's, so we deliberately don't recurse
        # into FunctionDef/AsyncFunctionDef/ClassDef bodies here.

    return bindings, type_string_vars


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
                _walk_stmts(node.body, {}, {}, type_properties, type_call_sites)

    return type_properties


if __name__ == "__main__":
    import json
    table = scan_type_properties()
    print(f"{len(table)} distinct Type::String identifiers, "
          f"{sum(len(v) for v in table.values())} total (type, property) pairs\n")
    print(json.dumps({k: sorted(v) for k, v in sorted(table.items())}, indent=2))

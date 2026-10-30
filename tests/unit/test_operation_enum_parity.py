"""Operation-enum parity for every MCP tool that takes an `operation` argument.

Background (full-review 2026-09-23, finding freecad-mcp-20260923-6c7a#22): the
bridge (freecad_mcp_server.py) declares each tool's valid operations in an
`inputSchema` enum, and the FreeCAD-side handler decides separately which
operations it will actually run. Only assembly_operations / varset_operations
(and, after a live drift, spreadsheet_operations) had a test tying the two
together; the other 16 tools did not. An operation added to one side only
either never reaches an MCP client (schema-missing) or fails at call time with
a generic "Unknown ... operation" (handler-missing).

The handler side takes four shapes, and this test reads each from the real
source with `ast` rather than a hand-typed copy of the list:

  * generic dispatch  -- freecad_mcp_handler.py's `generic_dispatch_map` names
                         the handler, handler_registry.py names its class, and
                         the class's `_ALLOWED_OPERATIONS` is the set
                         (cam_operations, cam_tools, cam_tool_controllers,
                         draft, mesh, spreadsheet, measurement, spatial_query,
                         macro, api_introspection, geometric_verification,
                         fixture, assembly, varset);
  * dict dispatch     -- the literal operation->method dict inside
                         _dispatch_partdesign / _dispatch_sketch /
                         _dispatch_view_control (the dict is rebuilt on every
                         call, so ONE missing method breaks every operation of
                         that tool, not just its own);
  * if/elif chain     -- _dispatch_part_operations, whose methods are named by
                         a template (f"create_{operation}") or a fixed name;
  * bridge-side       -- get_usage_guidance, answered in usage_guidance.query.

Three checks, each reported per tool (subTest) so one run shows every drift:
the schema enum equals the handler-side set; every handler-side operation
resolves to a callable method on its handler class; and no tool with an
`operation` enum exists that this file does not cover. The last one is what
keeps the next new tool from becoming the next unprotected boundary.
"""

import ast
import importlib
import os
import unittest

from tests.unit._freecad_mocks import reset_mocks  # noqa: F401 -- puts AICopilot/ on sys.path

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
SERVER_PY = os.path.join(REPO_ROOT, 'freecad_mcp_server.py')
HANDLER_PY = os.path.join(REPO_ROOT, 'AICopilot', 'freecad_mcp_handler.py')
GUIDANCE_PY = os.path.join(REPO_ROOT, 'usage_guidance.py')

# Tools whose handler side is a literal dict inside one dispatch method:
# tool -> (method name, names of the local dict variables holding operation->method).
DICT_DISPATCHED = {
    'partdesign_operations': ('_dispatch_partdesign', ('operation_map',)),
    'sketch_operations': ('_dispatch_sketch', ('operation_map',)),
    'view_control': ('_dispatch_view_control', ('gui_ops', 'safe_ops')),
}
CHAIN_DISPATCHED = {'part_operations': '_dispatch_part_operations'}
BRIDGE_ANSWERED = {'get_usage_guidance': ('query', GUIDANCE_PY)}


def _parse(path: str) -> ast.Module:
    with open(path, encoding='utf-8') as f:
        return ast.parse(f.read(), filename=path)


def _only(nodes: list, what: str):
    """Exactly one match, or fail naming what was looked for: a refactor that
    renames or duplicates a dispatch construct must not leave this test
    quietly reading nothing."""
    assert len(nodes) == 1, f"expected exactly one {what}, found {len(nodes)}"
    return nodes[0]


def _function(tree: ast.Module, name: str):
    return _only([n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name],
                 f"function {name}()")


def _assigned_dict(scope, var: str) -> ast.Dict:
    return _only([n.value for n in ast.walk(scope)
                  if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                  and any(isinstance(t, ast.Name) and t.id == var for t in n.targets)],
                 f"dict literal assigned to `{var}`")


def _str(node, what: str) -> str:
    assert isinstance(node, ast.Constant) and isinstance(node.value, str), f"{what} is not a string literal"
    return node.value


def _self_attr(node):
    """`self.a` -> 'a'; anything else -> None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'self':
        return node.attr
    return None


def _self_method(node):
    """`self.a.m` -> ('a', 'm'); anything else -> None."""
    if isinstance(node, ast.Attribute):
        owner = _self_attr(node.value)
        if owner:
            return owner, node.attr
    return None


def _tool_operation_enums(tree: ast.Module) -> dict:
    """{tool name: operation enum list} for every types.Tool(...) whose schema
    has an `operation` property with an enum. The schemas are pure literals;
    one that stops being literal fails here, by name, instead of being skipped."""
    enums = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'Tool' and isinstance(node.func.value, ast.Name) and node.func.value.id == 'types'):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        if 'name' not in kw or 'inputSchema' not in kw:
            continue
        name = _str(kw['name'], 'a types.Tool name')
        try:
            schema = ast.literal_eval(kw['inputSchema'])
        except ValueError as e:
            raise AssertionError(f"{name}: inputSchema is no longer a pure literal ({e}); this test cannot read it") from e
        op = schema.get('properties', {}).get('operation')
        if isinstance(op, dict) and 'enum' in op:
            enums[name] = op['enum']
    return enums


def _compared_to_operation(scope) -> set:
    """Every string constant that a bare `operation` name is compared against
    with `==` or `in (...)` anywhere inside `scope`."""
    found = set()
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == 'operation'):
            continue
        for op_node, comp in zip(node.ops, node.comparators, strict=True):
            if isinstance(op_node, ast.Eq):
                found.add(_str(comp, "`operation ==` operand"))
            elif isinstance(op_node, ast.In) and isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                found.update(_str(e, "`operation in` element") for e in comp.elts)
    return found


def _resolve_chain_value(expr, op: str):
    """The (handler attr, method) a branch of _dispatch_part_operations picks for `op`."""
    direct = _self_method(expr)
    if direct:
        return direct
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == 'getattr'
            and len(expr.args) == 3 and _self_attr(expr.args[0])):
        name_expr = expr.args[1]
        if isinstance(name_expr, ast.Name) and name_expr.id == 'operation':   # getattr(self.part_ops, operation, None)
            return _self_attr(expr.args[0]), op
        if isinstance(name_expr, ast.Constant):
            return _self_attr(expr.args[0]), name_expr.value
        if isinstance(name_expr, ast.JoinedStr):   # f"create_{operation}" -> create_<op>
            parts = []
            for piece in name_expr.values:
                if isinstance(piece, ast.Constant):
                    parts.append(piece.value)
                elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name) and piece.value.id == 'operation':
                    parts.append(op)
                else:
                    raise AssertionError("part_operations: an f-string method name uses something other than `operation`")
            return _self_attr(expr.args[0]), ''.join(parts)
    raise AssertionError("part_operations: a branch assigns its method in a shape this test does not understand "
                         "(expected self.a.m or getattr(self.a, <name or f-string>, None))")


def _chain_operations(func) -> dict:
    """{operation: (handler attr, method)} from _dispatch_part_operations' if/elif chain."""
    # The chain is the top-level `if` that tests `operation`; the trailing
    # `if not method:` guard is a second top-level `if` and is not part of it.
    node = _only([n for n in func.body if isinstance(n, ast.If) and _compared_to_operation(n.test)],
                 "top-level if/elif chain testing `operation` in _dispatch_part_operations")
    ops = {}
    while node is not None:
        names = sorted(_compared_to_operation(node.test))
        assert names, "part_operations: an if/elif branch tests something other than `operation ==` / `operation in (...)`"
        branch = _only(node.body, "statement in a part_operations branch")
        assert isinstance(branch, ast.Assign), "part_operations: a branch does more than assign `method`"
        for name in names:
            ops[name] = _resolve_chain_value(branch.value, name)
        node = node.orelse[0] if node.orelse and isinstance(node.orelse[0], ast.If) else None
    return ops


def _handler_class(attr: str):
    """Class for a handler attribute, by the registry's own table. Imported
    here, not at the top: AICopilot/ is only on sys.path once the mocks
    module above has run, and an import-sorting pass must not reorder that."""
    registry = importlib.import_module('handler_registry')
    module = importlib.import_module(f'handlers.{attr}')
    return getattr(module, registry._HANDLER_CLASS_NAMES[attr])


def _handler_side(handler_tree: ast.Module) -> dict:
    """{tool: {operation: (handler attr, method) or None}} -- None where the
    handler side has no per-operation method to resolve (bridge-answered)."""
    side = {}

    generic = _assigned_dict(handler_tree, 'generic_dispatch_map')
    for key, value in zip(generic.keys, generic.values, strict=True):
        tool = _str(key, 'generic_dispatch_map key')
        attr = _self_attr(value)
        assert attr, f"generic_dispatch_map[{tool!r}] is not `self.<handler>`"
        side[tool] = {op: (attr, op) for op in _handler_class(attr)._ALLOWED_OPERATIONS}

    for tool, (method_name, variables) in DICT_DISPATCHED.items():
        func = _function(handler_tree, method_name)
        ops = {}
        for var in variables:
            table = _assigned_dict(func, var)
            for key, value in zip(table.keys, table.values, strict=True):
                resolved = _self_method(value)
                assert resolved, f"{method_name}: {var}[{ast.unparse(key)}] is not `self.<handler>.<method>`"
                ops[_str(key, f'{method_name} {var} key')] = resolved
        side[tool] = ops

    for tool, method_name in CHAIN_DISPATCHED.items():
        side[tool] = _chain_operations(_function(handler_tree, method_name))

    for tool, (func_name, path) in BRIDGE_ANSWERED.items():
        side[tool] = dict.fromkeys(_compared_to_operation(_function(_parse(path), func_name)))

    return side


class TestOperationEnumParity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = _tool_operation_enums(_parse(SERVER_PY))
        cls.handler = _handler_side(_parse(HANDLER_PY))

    def test_every_tool_with_an_operation_enum_is_covered(self):
        """A new tool with an `operation` enum must be given a handler-side
        source here (or in generic_dispatch_map) before it can ship."""
        self.assertEqual(
            set(self.schema) - set(self.handler), set(),
            "tool(s) with an `operation` enum in freecad_mcp_server.py that have no handler-side source in this test: "
            "add them to generic_dispatch_map, or to DICT_DISPATCHED / CHAIN_DISPATCHED / BRIDGE_ANSWERED here")
        self.assertEqual(
            set(self.handler) - set(self.schema), set(),
            "handler-side dispatch for tool(s) whose schema has no `operation` enum (renamed, removed, or the enum was dropped)")

    def test_schema_enum_matches_handler_side(self):
        for tool in sorted(set(self.schema) & set(self.handler)):
            with self.subTest(tool=tool):
                schema_ops = self.schema[tool]
                self.assertEqual(len(schema_ops), len(set(schema_ops)), f"{tool}: duplicate entry in the schema's operation enum")
                handler_ops = set(self.handler[tool])
                self.assertEqual(
                    (sorted(set(schema_ops) - handler_ops), sorted(handler_ops - set(schema_ops))), ([], []),
                    f"{tool}: (in schema but not in handler, in handler but not in schema) -- an operation on one side "
                    f"only is unreachable via MCP or fails at call time")

    def test_every_handler_operation_resolves_to_a_callable_method(self):
        for tool in sorted(self.handler):
            with self.subTest(tool=tool):
                missing = []
                for op, target in sorted(self.handler[tool].items()):
                    if target is None:
                        continue
                    attr, method = target
                    if not callable(getattr(_handler_class(attr), method, None)):
                        missing.append(f"{op} -> {attr}.{method}")
                self.assertEqual(missing, [], f"{tool}: operation(s) whose handler method does not exist on the handler class")


if __name__ == "__main__":
    unittest.main()

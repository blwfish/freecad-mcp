"""Verify every MCP tool registered in the bridge has a dispatch path
in the handler.

Background: in March 2026 (commit 7ad1498) ``measurement_operations``
was a registered MCP tool in ``freecad_mcp_server.py`` but missing from
``freecad_mcp_handler.py``'s ``generic_dispatch_map``. Calls dead-lettered
silently — the handler returned ``{"error": "Unknown tool: ..."}`` and
the bug went unnoticed until someone tried to use the tool.

This test parses both files and asserts every server-side Tool() name
either appears as a routable key in the handler, or is on the
explicit allow-list of bridge-only tools.

It is a static-analysis test — no FreeCAD or handler instantiation
required, runs in <50 ms.
"""

import os
import re
import unittest


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
SERVER_PY = os.path.join(REPO_ROOT, 'freecad_mcp_server.py')
HANDLER_PY = os.path.join(REPO_ROOT, 'AICopilot', 'freecad_mcp_handler.py')


# Tools the bridge handles directly without routing to the FreeCAD-side
# handler. Each is implemented as a function/branch inside
# freecad_mcp_server.py and never reaches _execute_tool_inner.
BRIDGE_ONLY_TOOLS = frozenset({
    "check_freecad_connection",
    "test_echo",
    "manage_connection",
    "continue_selection",
    "spawn_freecad_instance",
    "list_freecad_instances",
    "select_freecad_instance",
    "stop_freecad_instance",
})


def _read(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def extract_server_tool_names(src: str) -> set:
    """Pull every ``types.Tool(name="...")`` registration from server.py.

    Looks for ``types.Tool(`` blocks followed (within ~3 lines) by
    ``name="..."``.  Matches the structure used in
    ``handle_list_tools()``.
    """
    names = set()
    for m in re.finditer(r'types\.Tool\(\s*\n?\s*name="([a-zA-Z_][a-zA-Z0-9_]*)"', src):
        names.add(m.group(1))
    return names


def extract_handler_routable_names(src: str) -> set:
    """Pull every tool name the handler can dispatch to.

    Sources:
      * keys in ``direct_map = { ... }``
      * keys in ``async_boolean_map = { ... }``
      * keys in ``generic_dispatch_map = { ... }``
      * literal string compared against ``tool_name`` in if-branches
    """
    names = set()

    # Map keys: "name": self.handler.method
    map_block_re = re.compile(
        r'(direct_map|async_boolean_map|generic_dispatch_map)\s*=\s*\{([^}]*)\}',
        re.DOTALL,
    )
    for block_match in map_block_re.finditer(src):
        body = block_match.group(2)
        for m in re.finditer(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', body):
            names.add(m.group(1))

    # `if tool_name == "..."` branches
    for m in re.finditer(r'tool_name\s*==\s*"([a-zA-Z_][a-zA-Z0-9_]*)"', src):
        names.add(m.group(1))

    return names


class TestDispatchCompleteness(unittest.TestCase):
    """Fail fast if any registered MCP tool lacks a handler dispatch."""

    @classmethod
    def setUpClass(cls):
        cls.server_src = _read(SERVER_PY)
        cls.handler_src = _read(HANDLER_PY)
        cls.server_tools = extract_server_tool_names(cls.server_src)
        cls.handler_tools = extract_handler_routable_names(cls.handler_src)

    def test_server_has_tools(self):
        """Sanity: the parser must find a reasonable number of tools."""
        self.assertGreaterEqual(
            len(self.server_tools), 25,
            f"Expected at least 25 MCP tools registered, found "
            f"{len(self.server_tools)}: {sorted(self.server_tools)}"
        )

    def test_handler_has_routes(self):
        """Sanity: the parser must find handler routes."""
        self.assertGreaterEqual(
            len(self.handler_tools), 15,
            f"Expected at least 15 handler routes, found "
            f"{len(self.handler_tools)}: {sorted(self.handler_tools)}"
        )

    def test_every_server_tool_is_routed_or_bridge_only(self):
        """Each MCP tool must either route in the handler or be on the
        bridge-only allow-list. Catches the 7ad1498 dead-letter bug."""
        unrouted = (self.server_tools
                    - self.handler_tools
                    - BRIDGE_ONLY_TOOLS)
        self.assertEqual(
            unrouted, set(),
            f"\nMCP tool(s) registered in freecad_mcp_server.py but with "
            f"no dispatch path in freecad_mcp_handler.py:\n  "
            + "\n  ".join(sorted(unrouted))
            + "\n\nFix: add the tool name to direct_map, generic_dispatch_map, "
              "or an explicit `tool_name ==` branch in _execute_tool_inner. "
              "If the tool is intentionally bridge-only (no FreeCAD call), "
              "add it to BRIDGE_ONLY_TOOLS in this test."
        )

    def test_no_orphan_bridge_only_entries(self):
        """Items on the BRIDGE_ONLY_TOOLS list should still exist as MCP tools.

        If a tool was deleted from the bridge but still appears here, the
        allow-list is masking a real regression.
        """
        orphans = BRIDGE_ONLY_TOOLS - self.server_tools
        self.assertEqual(
            orphans, set(),
            f"\nBRIDGE_ONLY_TOOLS lists names not found as MCP tools:\n  "
            + "\n  ".join(sorted(orphans))
            + "\n\nEither the bridge tool was renamed/removed (update this "
              "list), or our parser missed it (broaden extract_server_tool_names)."
        )

    def test_known_dead_letter_bug_would_be_caught(self):
        """Regression: the measurement_operations dead-letter bug must be
        caught by this test if it recurs.

        Verify the test mechanism by checking that 'measurement_operations'
        is currently routable. If it stops being routable without being
        added to BRIDGE_ONLY_TOOLS, test_every_server_tool_is_routed_or_bridge_only
        will catch it — this just asserts the canary.
        """
        self.assertIn(
            "measurement_operations", self.server_tools,
            "measurement_operations no longer registered as an MCP tool — "
            "test data has shifted."
        )
        self.assertIn(
            "measurement_operations", self.handler_tools,
            "measurement_operations is registered as an MCP tool but has "
            "no handler dispatch — the 7ad1498 bug has recurred."
        )


if __name__ == '__main__':
    unittest.main()

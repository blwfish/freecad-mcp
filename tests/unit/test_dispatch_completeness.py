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
#
# NOTE: continue_selection was removed from this list in the C1/C2 selection-
# subsystem fix — it now routes through the smart-dispatcher elif name in
# [...] list and reaches a real _execute_tool_inner branch
# (tool_name == "continue_selection"), so it must not be exempted here.
# test_echo stays bridge-only for the client-facing MCP tool (the bridge
# answers it directly with no FreeCAD round-trip); a *different*, unregistered
# use of the same string as a raw internal socket command (restart_freecad's
# readiness poll) is handled by a tool_name == "test_echo" branch in the
# handler, invisible to this MCP-tool-name-based static analysis.
BRIDGE_ONLY_TOOLS = frozenset({
    "check_freecad_connection",
    "test_echo",
    "manage_connection",
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


def extract_bridge_dispatched_names(src: str) -> set:
    """Pull every tool name the BRIDGE's handle_call_tool actually dispatches.

    The bridge decides what to do with each tool in an if/elif chain on
    ``name`` *before* anything reaches the FreeCAD-side handler. A tool can be
    registered in handle_list_tools AND routable in the handler yet still be
    unreachable, because this chain has no branch for it and it falls through
    to the ``Unknown tool`` else. That is exactly how geometric_verification,
    fixture_operations, run_inspector and get_last_traceback shipped dead in
    PR #21 — extract_handler_routable_names found them (the handler routes
    them), so the handler-side completeness test passed while the tools were
    unreachable. Sources:
      * ``name == "x"`` / ``elif name == "x"`` comparisons
      * names inside an ``elif name in ["a", "b", ...]`` list (possibly
        spanning several lines)
    """
    names = set()
    for m in re.finditer(r'\bname\s*==\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']', src):
        names.add(m.group(1))
    for block in re.finditer(r'\bname\s+in\s*\[(.*?)\]', src, re.DOTALL):
        for m in re.finditer(r'["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']', block.group(1)):
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
        cls.bridge_dispatched = extract_bridge_dispatched_names(cls.server_src)

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

    def test_every_server_tool_is_dispatched_by_bridge(self):
        """Each registered MCP tool must have a branch in the BRIDGE's
        handle_call_tool dispatch, not just a route in the handler.

        Regression for PR #21: geometric_verification, fixture_operations,
        run_inspector and get_last_traceback were registered and handler-
        routable, but the bridge's if/elif chain had no branch for them, so
        every call fell through to 'Unknown tool'. The handler-side test above
        could not catch this — only the bridge dispatch can.
        """
        undispatched = (self.server_tools
                        - self.bridge_dispatched)
        self.assertEqual(
            undispatched, set(),
            f"\nMCP tool(s) advertised in handle_list_tools() with no branch in "
            f"the bridge's handle_call_tool dispatch (they return 'Unknown "
            f"tool'):\n  " + "\n  ".join(sorted(undispatched))
            + "\n\nFix: add the name to the smart-dispatcher `elif name in [...]` "
              "list (for socket-routed tools) or give it its own `elif name == "
              "...` branch in freecad_mcp_server.py."
        )

    def test_previously_dead_tools_now_dispatched(self):
        """Canary: the four tools that were unreachable in PR #21 must stay
        dispatched by the bridge."""
        for tool in ("geometric_verification", "fixture_operations",
                     "run_inspector", "get_last_traceback"):
            self.assertIn(
                tool, self.bridge_dispatched,
                f"{tool} is advertised but not dispatched by the bridge — the "
                f"PR #21 dead-tool regression has recurred."
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

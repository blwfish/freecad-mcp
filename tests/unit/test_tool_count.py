"""Verify bridge tool count stays in sync with TOOLS.md.

Adding a tool requires exactly two things: add it to freecad_mcp_server.py
and add a row to TOOLS.md. This test catches you if you do one without
the other. No hardcoded counts to maintain.
"""

import os
import re


BRIDGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py"
)


def _bridge_tool_count():
    with open(BRIDGE_PATH) as f:
        return f.read().count("types.Tool(")


def _tools_md_row_count():
    tools_path = os.path.join(os.path.dirname(BRIDGE_PATH), "TOOLS.md")
    with open(tools_path) as f:
        content = f.read()
    # Count rows that start a tool entry: "| `toolname`"
    return len(re.findall(r"^\| `\w+`", content, re.MULTILINE))


def test_tools_md_matches_bridge():
    """TOOLS.md must have one row per tool defined in the bridge."""
    bridge = _bridge_tool_count()
    docs = _tools_md_row_count()
    assert bridge == docs, (
        f"Bridge defines {bridge} tools but TOOLS.md has {docs} rows. "
        f"Add a TOOLS.md entry for every new tool (or remove the stale row)."
    )

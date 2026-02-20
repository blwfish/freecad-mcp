"""Verify bridge tool count stays consistent with documentation.

If this test fails, you added or removed a tool in working_bridge.py.
Update the count here AND in README.md and CLAUDE.md.
"""

import os
import re


BRIDGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "working_bridge.py"
)

EXPECTED_TOOL_COUNT = 14


def test_bridge_tool_count():
    """Bridge should define exactly the expected number of MCP tools."""
    with open(BRIDGE_PATH) as f:
        content = f.read()
    actual = content.count("types.Tool(")
    assert actual == EXPECTED_TOOL_COUNT, (
        f"Expected {EXPECTED_TOOL_COUNT} tools in working_bridge.py, "
        f"found {actual}. Update this test, README.md, and CLAUDE.md."
    )


def test_readme_tool_count():
    """README.md tool count should match the bridge."""
    readme_path = os.path.join(os.path.dirname(BRIDGE_PATH), "README.md")
    with open(readme_path) as f:
        content = f.read()
    m = re.search(r"(\d+) tools for", content)
    assert m, "README.md should contain '<N> tools for' pattern"
    readme_count = int(m.group(1))
    assert readme_count == EXPECTED_TOOL_COUNT, (
        f"README.md says {readme_count} tools but expected {EXPECTED_TOOL_COUNT}"
    )


def test_claude_md_tool_count():
    """CLAUDE.md tool count should match the bridge."""
    claude_path = os.path.join(os.path.dirname(BRIDGE_PATH), "CLAUDE.md")
    with open(claude_path) as f:
        content = f.read()
    m = re.search(r"(\d+) MCP tools", content)
    assert m, "CLAUDE.md should contain '<N> MCP tools' pattern"
    claude_count = int(m.group(1))
    assert claude_count == EXPECTED_TOOL_COUNT, (
        f"CLAUDE.md says {claude_count} tools but expected {EXPECTED_TOOL_COUNT}"
    )

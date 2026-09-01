"""Verify bridge tool count stays in sync with TOOLS.md, AGENT-INSTALL.md,
and CLAUDE.md.

Adding a tool requires exactly two things: add it to freecad_mcp_server.py
and add a row to TOOLS.md. This test catches you if you do one without
the other. No hardcoded counts to maintain.

AGENT-INSTALL.md and CLAUDE.md separately hand-state the tool count in
prose (e.g. "providing 38 tools", "38 MCP tools") and, in
AGENT-INSTALL.md's case, break it into a Dispatchers table + a
Single-Purpose Tools bullet list with their own hand-counted headings.
These are additional hand-duplicated copies of the same number with no
prior automated cross-check -- confirmed stale in practice: AGENT-INSTALL.md
said "36 tools" / "14 Dispatchers" for weeks after assembly_operations and
varset_operations shipped, bringing the real count to 38 dispatchers=16.
"""

import os
import re


BRIDGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py"
)
AGENT_INSTALL_PATH = os.path.join(
    os.path.dirname(BRIDGE_PATH), "AGENT-INSTALL.md"
)
CLAUDE_MD_PATH = os.path.join(os.path.dirname(BRIDGE_PATH), "CLAUDE.md")


def _bridge_tool_count():
    with open(BRIDGE_PATH) as f:
        return f.read().count("types.Tool(")


def _tools_md_row_count():
    tools_path = os.path.join(os.path.dirname(BRIDGE_PATH), "TOOLS.md")
    with open(tools_path) as f:
        content = f.read()
    # Count rows that start a tool entry: "| `toolname`"
    return len(re.findall(r"^\| `\w+`", content, re.MULTILINE))


def _agent_install_text():
    with open(AGENT_INSTALL_PATH) as f:
        return f.read()


def _agent_install_prose_tool_counts():
    """Every "N tools" claim in AGENT-INSTALL.md's free-text prose (the
    Tool Overview table/list breakdown is checked separately, below --
    it isn't phrased as "N tools")."""
    counts = [int(n) for n in re.findall(r"(\d+) tools\b", _agent_install_text())]
    assert counts, "no 'N tools' claim found in AGENT-INSTALL.md -- did its wording change?"
    return counts


def _agent_install_dispatcher_section():
    text = _agent_install_text()
    start = text.index("Dispatchers**")
    end = text.index("Single-Purpose Tools:**", start)
    return text[start:end]


def _agent_install_single_purpose_section():
    text = _agent_install_text()
    start = text.index("Single-Purpose Tools:**")
    end = text.index("### Critical Rules", start)
    return text[start:end]


def _agent_install_dispatcher_heading_count():
    m = re.search(r"\*\*(\d+) Dispatchers\*\*", _agent_install_text())
    assert m, "no '**N Dispatchers**' heading found in AGENT-INSTALL.md"
    return int(m.group(1))


def _agent_install_dispatcher_row_count():
    # Same row shape as TOOLS.md's "| `toolname`" convention, but scoped to
    # just this table -- the Environment Variables table further down also
    # starts its rows with a backtick (e.g. "| `FREECAD_MCP_SOCKET`"), so
    # matching this pattern over the whole file would overcount.
    return len(re.findall(r"^\| `\w+`", _agent_install_dispatcher_section(), re.MULTILINE))


def _agent_install_single_purpose_heading_count():
    m = re.search(r"\*\*(\d+) Single-Purpose Tools:\*\*", _agent_install_text())
    assert m, "no '**N Single-Purpose Tools:**' heading found in AGENT-INSTALL.md"
    return int(m.group(1))


def _agent_install_single_purpose_name_count():
    # Each bullet is "- `name` [/ `name2` ...] — description". Only the
    # segment before the em dash is a name listing -- a description can
    # itself mention another tool in backticks (e.g. api_introspection's
    # "use before `execute_python`"), which is prose, not a second entry,
    # so splitting on " — " first and counting backtick-names only in the
    # head is required to not double-count those cross-references.
    count = 0
    for line in _agent_install_single_purpose_section().splitlines():
        line = line.strip()
        if not line.startswith("- `"):
            continue
        head = line.split(" — ", 1)[0]
        count += len(re.findall(r"`\w+`", head))
    return count


def _claude_md_tool_counts():
    with open(CLAUDE_MD_PATH) as f:
        content = f.read()
    counts = [int(n) for n in re.findall(r"(\d+) MCP tools", content)]
    assert counts, "no 'N MCP tools' claim found in CLAUDE.md -- did its wording change?"
    return counts


def test_tools_md_matches_bridge():
    """TOOLS.md must have one row per tool defined in the bridge."""
    bridge = _bridge_tool_count()
    docs = _tools_md_row_count()
    assert bridge == docs, (
        f"Bridge defines {bridge} tools but TOOLS.md has {docs} rows. "
        f"Add a TOOLS.md entry for every new tool (or remove the stale row)."
    )


def test_agent_install_prose_tool_counts_match_bridge():
    bridge = _bridge_tool_count()
    for claimed in _agent_install_prose_tool_counts():
        assert claimed == bridge, (
            f"AGENT-INSTALL.md claims {claimed} tools, but the bridge "
            f"defines {bridge}. Update AGENT-INSTALL.md's tool-count prose."
        )


def test_agent_install_dispatcher_table_matches_its_own_heading():
    heading = _agent_install_dispatcher_heading_count()
    rows = _agent_install_dispatcher_row_count()
    assert heading == rows, (
        f"AGENT-INSTALL.md's '**{heading} Dispatchers**' heading doesn't "
        f"match its table, which has {rows} rows. Update whichever is stale."
    )


def test_agent_install_single_purpose_list_matches_its_own_heading():
    heading = _agent_install_single_purpose_heading_count()
    names = _agent_install_single_purpose_name_count()
    assert heading == names, (
        f"AGENT-INSTALL.md's '**{heading} Single-Purpose Tools:**' heading "
        f"doesn't match its bullet list, which names {names} tools. "
        f"Update whichever is stale."
    )


def test_agent_install_tool_overview_totals_match_bridge():
    """The Dispatchers table + Single-Purpose Tools list together must
    account for every tool the bridge defines -- this is the check that
    actually caught the real staleness: assembly_operations and
    varset_operations shipped as new dispatchers without being added to
    this table, so the file's own internal counts (14 + 22 = 36) stayed
    self-consistent while silently falling behind the bridge's real 38."""
    bridge = _bridge_tool_count()
    dispatchers = _agent_install_dispatcher_row_count()
    single_purpose = _agent_install_single_purpose_name_count()
    assert dispatchers + single_purpose == bridge, (
        f"AGENT-INSTALL.md's Tool Overview lists {dispatchers} dispatchers + "
        f"{single_purpose} single-purpose tools = {dispatchers + single_purpose}, "
        f"but the bridge defines {bridge} tools. Add the missing tool(s) to "
        f"the Dispatchers table or Single-Purpose Tools list (and bump that "
        f"section's heading count)."
    )


def test_claude_md_tool_counts_match_bridge():
    bridge = _bridge_tool_count()
    for claimed in _claude_md_tool_counts():
        assert claimed == bridge, (
            f"CLAUDE.md claims {claimed} MCP tools, but the bridge defines "
            f"{bridge}. Update CLAUDE.md's tool-count mentions."
        )

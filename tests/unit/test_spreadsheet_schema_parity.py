"""Verify the spreadsheet_operations MCP tool schema's operation enum stays
in sync with SpreadsheetOpsHandler._ALLOWED_OPERATIONS.

Background (full-review 2026-09-23, Interface Contract Check finding #21):
freecad_mcp_server.py's spreadsheet_operations schema enum only listed 8 of
the 12 operations SpreadsheetOpsHandler._ALLOWED_OPERATIONS actually permits
-- bind_property, list_aliases, import_csv, and export_csv were live,
implemented, and reachable via generic dispatch, but invisible to any MCP
client relying on the schema to discover valid operation values. No parity
test existed to catch this (unlike assembly_operations/varset_operations,
which each got one after a similar incident). This is that test, following
the same static-analysis pattern as test_varset_schema_parity.py.
"""

import ast
import os
import unittest

from tests.unit._freecad_mocks import reset_mocks  # noqa: F401 -- ensures mock_FreeCAD is installed
from handlers.spreadsheet_ops import SpreadsheetOpsHandler


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
SERVER_PY = os.path.join(REPO_ROOT, 'freecad_mcp_server.py')


def _read(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _extract_enum_after(src: str, tool_name: str, description_substring: str) -> list:
    """Find the first `"enum": [...]` list appearing shortly after a
    property whose "description" contains description_substring, inside the
    named tool's inputSchema. Parses the actual Python list literal via
    ast.literal_eval on the matched text (not a naive string-list regex) so
    trailing commas, formatting, and quote style don't matter."""
    tool_start = src.index(f'name="{tool_name}"')
    tool_end = src.index('types.Tool(', tool_start + 1) if 'types.Tool(' in src[tool_start + 1:] else len(src)
    block = src[tool_start:tool_end]

    marker = block.index(description_substring)
    enum_start = block.index('"enum":', marker)
    list_start = block.index('[', enum_start)
    depth = 0
    i = list_start
    while i < len(block):
        if block[i] == '[':
            depth += 1
        elif block[i] == ']':
            depth -= 1
            if depth == 0:
                break
        i += 1
    list_text = block[list_start:i + 1]
    return ast.literal_eval(list_text)


class TestSpreadsheetEnumParity(unittest.TestCase):
    """Fail fast if the bridge schema's copy of the operation enum drifts
    from the FreeCAD-side handler's real constant."""

    @classmethod
    def setUpClass(cls):
        cls.server_src = _read(SERVER_PY)

    def test_operation_enum_matches_allowed_operations(self):
        schema_operations = _extract_enum_after(
            self.server_src, 'spreadsheet_operations', '"description": "Spreadsheet operation to perform"'
        )
        self.assertEqual(
            set(schema_operations), set(SpreadsheetOpsHandler._ALLOWED_OPERATIONS),
            "freecad_mcp_server.py's spreadsheet_operations operation enum has "
            "drifted from SpreadsheetOpsHandler._ALLOWED_OPERATIONS -- an operation "
            "added to one without the other either becomes unreachable via MCP "
            "(schema-missing) or only fails at call time with a generic error "
            "(handler-missing)."
        )
        self.assertEqual(len(schema_operations), len(set(schema_operations)),
                          "Duplicate entry in the schema's operation enum")


if __name__ == "__main__":
    unittest.main()

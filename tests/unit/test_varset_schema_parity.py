"""Verify the varset_operations MCP tool schema's operation enum stays in
sync with VarSetOpsHandler._ALLOWED_OPERATIONS.

Background (full-review 2026-08-31, finding #04): freecad_mcp_server.py (the
bridge process) hand-duplicates this list because it can't import
AICopilot/handlers/varset_ops.py directly -- that module `import FreeCAD`s at
module scope, and the bridge runs as a separate process with no FreeCAD
available. The two lists agree today only by manual diligence; nothing
would catch one being updated without the other. This is a static-analysis
test in the same spirit as test_assembly_schema_parity.py -- no FreeCAD
needed for the schema side (plain regex over the source text); the handler
side imports the real module with FreeCAD mocked, so this checks the ACTUAL
constant, not a second hand-transcribed copy of it.
"""

import ast
import os
import unittest

from tests.unit._freecad_mocks import reset_mocks  # noqa: F401 -- ensures mock_FreeCAD is installed
from handlers.varset_ops import VarSetOpsHandler


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
SERVER_PY = os.path.join(REPO_ROOT, 'freecad_mcp_server.py')


def _read(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _extract_enum_after(src: str, description_substring: str) -> list:
    """Find the first `"enum": [...]` list appearing shortly after a
    property whose "description" contains description_substring, inside the
    varset_operations tool's inputSchema.

    Parses the actual Python list literal via ast.literal_eval on the
    matched text (not a naive string-list regex) so trailing commas,
    formatting, and quote style don't matter -- only the real values do.
    """
    # Narrow to the varset_operations Tool( ... ) block first, so a
    # same-named property in another tool's schema can't be matched instead.
    tool_start = src.index('name="varset_operations"')
    tool_end = src.index('types.Tool(', tool_start + 1) if 'types.Tool(' in src[tool_start + 1:] else len(src)
    block = src[tool_start:tool_end]

    marker = block.index(description_substring)
    enum_start = block.index('"enum":', marker)
    list_start = block.index('[', enum_start)
    # Balance brackets to find the matching close, so a nested structure
    # (there isn't one here, but don't assume) can't truncate early.
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


class TestVarSetEnumParity(unittest.TestCase):
    """Fail fast if the bridge schema's copy of the operation enum drifts
    from the FreeCAD-side handler's real constant."""

    @classmethod
    def setUpClass(cls):
        cls.server_src = _read(SERVER_PY)

    def test_operation_enum_matches_allowed_operations(self):
        schema_operations = _extract_enum_after(self.server_src, '"description": "VarSet operation to perform"')
        self.assertEqual(
            set(schema_operations), set(VarSetOpsHandler._ALLOWED_OPERATIONS),
            "freecad_mcp_server.py's varset_operations operation enum has drifted "
            "from VarSetOpsHandler._ALLOWED_OPERATIONS -- a method added to one "
            "without the other either becomes unreachable via MCP (schema-missing) or "
            "only fails at call time with a generic error (handler-missing)."
        )
        # No duplicates on either side -- a duplicate entry wouldn't be
        # caught by the set-equality check above.
        self.assertEqual(len(schema_operations), len(set(schema_operations)),
                          "Duplicate entry in the schema's operation enum")


if __name__ == "__main__":
    unittest.main()

"""Verify the assembly_operations MCP tool schema's operation/joint_type
enums stay in sync with AssemblyOpsHandler's _ALLOWED_OPERATIONS/_JOINT_TYPES.

Background (full-review 2026-07-24, findings #01/#11/#28): freecad_mcp_server.py
(the bridge process) hand-duplicates both lists because it can't import
AICopilot/handlers/assembly_ops.py directly -- that module `import FreeCAD`s at
module scope, and the bridge runs as a separate process with no FreeCAD
available, often deployed to an entirely different directory than the
AICopilot/ handler tree (FREECAD_MCP_MODULE_DIR is independently configurable).
A true shared-constants module across that process boundary was judged out of
scope for this fix pass; this static-analysis parity test is the mechanism
that makes future drift LOUD instead of silent.

_JOINT_TYPES' order is load-bearing -- create_joint calls
`_JOINT_TYPES.index(joint_type)` and feeds the result as a positional index
into `JointObject.Joint(joint, type_index)`. Mutation-tested 2026-07-24:
swapping two entries left the full 100+-test suite green, because
test_assembly_ops.py's own expected-index computation reads from the same
(mutated) list it's checking -- this test is the only thing that would catch
a reorder, since it compares against the schema's independently-typed copy.

This is a static-analysis test in the same spirit as
test_dispatch_completeness.py -- no FreeCAD needed for the schema side (plain
regex over the source text); the handler side imports the real module with
FreeCAD mocked, the same way test_assembly_ops.py already does, so this
checks the ACTUAL constants, not a second hand-transcribed copy of them.
"""

import ast
import os
import re
import unittest

from tests.unit._freecad_mocks import reset_mocks  # noqa: F401 -- ensures mock_FreeCAD is installed
from handlers.assembly_ops import _JOINT_TYPES
from handlers.assembly_ops import AssemblyOpsHandler


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
SERVER_PY = os.path.join(REPO_ROOT, 'freecad_mcp_server.py')


def _read(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _extract_enum_after(src: str, description_substring: str) -> list:
    """Find the first `"enum": [...]` list appearing shortly after a
    property whose "description" contains description_substring, inside the
    assembly_operations tool's inputSchema.

    Parses the actual Python list literal via ast.literal_eval on the
    matched text (not a naive string-list regex) so trailing commas,
    formatting, and quote style don't matter -- only the real values do.
    """
    # Narrow to the assembly_operations Tool( ... ) block first, so a
    # same-named property in another tool's schema can't be matched instead.
    tool_start = src.index('name="assembly_operations"')
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


class TestAssemblyEnumParity(unittest.TestCase):
    """Fail fast if the bridge schema's copy of either enum drifts from the
    FreeCAD-side handler's real constant."""

    @classmethod
    def setUpClass(cls):
        cls.server_src = _read(SERVER_PY)

    def test_joint_type_enum_matches_and_order_is_preserved(self):
        """Order-sensitive -- _JOINT_TYPES.index(joint_type) is positional,
        so a reorder in either copy silently binds the wrong joint type."""
        schema_joint_types = _extract_enum_after(self.server_src, '"description": "Joint type (create_joint)"')
        self.assertEqual(
            schema_joint_types, list(_JOINT_TYPES),
            "freecad_mcp_server.py's assembly_operations joint_type enum has drifted "
            "from AICopilot/handlers/assembly_ops.py's _JOINT_TYPES. This list's ORDER "
            "is load-bearing (positional index into JointObject.Joint) -- update both "
            "copies together."
        )

    def test_operation_enum_matches_allowed_operations(self):
        schema_operations = _extract_enum_after(self.server_src, '"description": "Assembly operation to perform"')
        self.assertEqual(
            set(schema_operations), set(AssemblyOpsHandler._ALLOWED_OPERATIONS),
            "freecad_mcp_server.py's assembly_operations operation enum has drifted "
            "from AssemblyOpsHandler._ALLOWED_OPERATIONS -- a method added to one "
            "without the other either becomes unreachable via MCP (schema-missing) or "
            "only fails at call time with a generic error (handler-missing)."
        )
        # No duplicates on either side -- a duplicate entry wouldn't be
        # caught by the set-equality check above.
        self.assertEqual(len(schema_operations), len(set(schema_operations)),
                          "Duplicate entry in the schema's operation enum")

    def test_joint_type_enum_has_no_duplicates(self):
        self.assertEqual(len(_JOINT_TYPES), len(set(_JOINT_TYPES)),
                          "Duplicate entry in _JOINT_TYPES")


if __name__ == "__main__":
    unittest.main()

"""
Transform integration tests — move, rotate, copy, array.

All operations route through part_operations dispatcher.
"""

import json
import time
import pytest
from ._geom_helpers import assert_op_succeeded, _result_text
from .test_e2e_workflows import send_command


def _placement_base(doc_name: str, obj_name: str) -> tuple:
    """Return (x, y, z) of the object's Placement.Base via execute_python."""
    code = (
        f"import json\n"
        f"obj = FreeCAD.getDocument('{doc_name}').getObject('{obj_name}')\n"
        f"if obj is None:\n"
        f"    found = FreeCAD.getDocument('{doc_name}').getObjectsByLabel('{obj_name}')\n"
        f"    obj = found[0] if found else None\n"
        f"p = obj.Placement.Base\n"
        f"json.dumps([float(p.x), float(p.y), float(p.z)])\n"
    )
    raw = send_command("execute_python", {"code": code})
    text = _result_text(raw).strip()
    if text.startswith("Result: "):
        text = text[len("Result: "):].strip()
    return tuple(json.loads(text))


@pytest.fixture
def clean_document():
    doc_name = f"Transforms_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })
    yield doc_name
    try:
        send_command("execute_python", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


@pytest.fixture
def box_in_document(clean_document):
    """Create a single box for transform testing."""
    send_command("part_operations", {
        "operation": "box",
        "length": 10, "width": 10, "height": 10,
        "name": "TBox",
    })
    return clean_document


class TestMove:
    def test_move_relative(self, box_in_document):
        """Default relative move adds offset to current Placement.Base."""
        result = send_command("part_operations", {
            "operation": "move",
            "object_name": "TBox",
            "x": 10, "y": 5, "z": 0,
        })
        assert_op_succeeded(result, "move relative")
        # Box was at (0,0,0) initially; relative move puts it at (10,5,0)
        x, y, z = _placement_base(box_in_document, "TBox")
        assert (x, y, z) == (10.0, 5.0, 0.0), \
            f"Expected (10,5,0), got ({x},{y},{z})"

    def test_move_absolute(self, box_in_document):
        """relative=False sets Placement.Base directly (commit 4247599)."""
        result = send_command("part_operations", {
            "operation": "move",
            "object_name": "TBox",
            "x": 50, "y": 50, "z": 50,
            "relative": False,
        })
        assert_op_succeeded(result, "move absolute")
        x, y, z = _placement_base(box_in_document, "TBox")
        assert (x, y, z) == (50.0, 50.0, 50.0), \
            f"Expected (50,50,50), got ({x},{y},{z})"

    def test_move_missing_object(self, clean_document):
        result = send_command("part_operations", {
            "operation": "move",
            "object_name": "NoSuchThing",
            "x": 10, "y": 0, "z": 0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "not found" in result_str.lower() or "error" in result_str.lower()


class TestRotate:
    def test_rotate_around_z(self, box_in_document):
        result = send_command("part_operations", {
            "operation": "rotate",
            "object_name": "TBox",
            "angle": 45,
            "axis": "z",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_rotate_around_x(self, box_in_document):
        result = send_command("part_operations", {
            "operation": "rotate",
            "object_name": "TBox",
            "angle": 90,
            "axis": "x",
        })
        result_str = str(result)
        assert "Unknown" not in result_str


class TestCopyAndArray:
    def test_copy_with_offset(self, box_in_document):
        result = send_command("part_operations", {
            "operation": "copy",
            "object_name": "TBox",
            "x": 20, "y": 0, "z": 0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_array_linear(self, box_in_document):
        result = send_command("part_operations", {
            "operation": "array",
            "object_name": "TBox",
            "count": 4,
            "interval_x": 15,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

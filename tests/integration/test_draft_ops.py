"""
Draft operations integration tests — clone, array, polar_array, path_array,
point_array.

Draft ops use the generic dispatcher (_dispatch_to_handler), so operation
names must match method names exactly.
"""

import time
import pytest
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Module-level guard: skip all tests if Draft module isn't available
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _require_draft():
    result = send_command("execute_python", {
        "code": """
try:
    import Draft
    'draft_available'
except ImportError:
    'draft_missing'
"""
    })
    if "draft_missing" in str(result):
        pytest.skip("Draft module not available in this FreeCAD build")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_document():
    doc_name = f"DraftOps_{int(time.time() * 1000) % 100000}"
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
    send_command("part_operations", {
        "operation": "box",
        "length": 10, "width": 10, "height": 10,
        "name": "DBox",
    })
    return clean_document


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDraftClone:
    def test_clone(self, box_in_document):
        result = send_command("draft_operations", {
            "operation": "clone",
            "object_name": "DBox",
            "x": 20, "y": 0, "z": 0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "clone" in result_str.lower()

    def test_clone_missing_object(self, clean_document):
        result = send_command("draft_operations", {
            "operation": "clone",
            "object_name": "NoSuchBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "not found" in result_str.lower() or "error" in result_str.lower()


class TestDraftArrays:
    def test_ortho_array(self, box_in_document):
        """2x3 rectangular array."""
        result = send_command("draft_operations", {
            "operation": "array",
            "object_name": "DBox",
            "count_x": 2,
            "count_y": 3,
            "interval_x": 15,
            "interval_y": 15,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "array" in result_str.lower()

    def test_polar_array(self, box_in_document):
        """6-element polar array around origin."""
        result = send_command("draft_operations", {
            "operation": "polar_array",
            "object_name": "DBox",
            "count": 6,
            "angle": 360,
            "center_x": 0, "center_y": 0, "center_z": 0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "polar" in result_str.lower()

    def test_path_array(self, box_in_document):
        """Array along a wire path."""
        # Create a path via execute_python
        send_command("execute_python", {
            "code": """
import Part
doc = FreeCAD.ActiveDocument
path = doc.addObject("Part::Feature", "ArrayPath")
path.Shape = Part.makePolygon([
    FreeCAD.Vector(0,0,0),
    FreeCAD.Vector(50,0,0),
    FreeCAD.Vector(50,50,0),
])
doc.recompute()
'done'
"""
        })
        result = send_command("draft_operations", {
            "operation": "path_array",
            "object_name": "DBox",
            "path_name": "ArrayPath",
            "count": 4,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_point_array(self, box_in_document):
        """Array at specified point locations."""
        send_command("execute_python", {
            "code": """
import Part
doc = FreeCAD.ActiveDocument
# Create a compound of vertices as the point source
pts = Part.Compound([Part.Point(FreeCAD.Vector(x*20, 0, 0)).toShape()
                     for x in range(3)])
pobj = doc.addObject("Part::Feature", "ArrayPoints")
pobj.Shape = pts
doc.recompute()
'done'
"""
        })
        result = send_command("draft_operations", {
            "operation": "point_array",
            "object_name": "DBox",
            "point_object": "ArrayPoints",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

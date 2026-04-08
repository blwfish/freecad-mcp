"""
Part workbench integration tests — extrude, revolve, mirror, scale, section,
loft, sweep, compound, check_geometry.

All operations route through part_operations dispatcher.
"""

import time
import pytest
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_document():
    """Create a fresh document and clean up after the test."""
    doc_name = f"PartOps_{int(time.time() * 1000) % 100000}"
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
def sketch_on_xy(clean_document):
    """Create a closed rectangular sketch on XY plane."""
    send_command("sketch_operations", {
        "operation": "create_sketch",
        "plane": "XY",
        "name": "RectSketch",
    })
    send_command("sketch_operations", {
        "operation": "add_rectangle",
        "sketch_name": "RectSketch",
        "x": 0, "y": 0, "width": 20, "height": 15,
    })
    send_command("sketch_operations", {
        "operation": "close_sketch",
        "sketch_name": "RectSketch",
    })
    return clean_document


# ---------------------------------------------------------------------------
# Tests: Part Extrude
# ---------------------------------------------------------------------------

class TestPartExtrude:
    def test_extrude_sketch_z(self, sketch_on_xy):
        """Extrude a rectangular sketch in Z direction."""
        result = send_command("part_operations", {
            "operation": "extrude",
            "profile_sketch": "RectSketch",
            "height": 10,
            "direction": "z",
        })
        result_str = str(result)
        assert "Unknown" not in result_str, f"Dispatch failed: {result}"
        assert "error" not in result_str.lower() or "Extru" in result_str

    def test_extrude_sketch_x(self, sketch_on_xy):
        """Extrude in X direction."""
        result = send_command("part_operations", {
            "operation": "extrude",
            "profile_sketch": "RectSketch",
            "height": 10,
            "direction": "x",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_extrude_missing_sketch(self, clean_document):
        """Extrude with nonexistent sketch should give error, not crash."""
        result = send_command("part_operations", {
            "operation": "extrude",
            "profile_sketch": "NoSuchSketch",
            "height": 10,
        })
        result_str = str(result)
        assert "Unknown" not in result_str  # dispatch worked
        assert "not found" in result_str.lower() or "error" in result_str.lower()


# ---------------------------------------------------------------------------
# Tests: Revolve
# ---------------------------------------------------------------------------

class TestPartRevolve:
    def test_revolve_sketch(self, sketch_on_xy):
        """Revolve a sketch around an axis."""
        result = send_command("part_operations", {
            "operation": "revolve",
            "profile_sketch": "RectSketch",
            "axis": "y",
            "angle": 360,
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Mirror, Scale, Section
# ---------------------------------------------------------------------------

class TestPartMirrorScaleSection:
    def test_mirror_box(self, clean_document):
        """Mirror a box across a plane."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "MirrorBox",
        })
        result = send_command("part_operations", {
            "operation": "mirror",
            "object_name": "MirrorBox",
            "plane": "YZ",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_scale_box(self, clean_document):
        """Scale a box by a factor."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "ScaleBox",
        })
        result = send_command("part_operations", {
            "operation": "scale",
            "object_name": "ScaleBox",
            "factor": 2.0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_section_box(self, clean_document):
        """Create a section through a box."""
        send_command("part_operations", {
            "operation": "box",
            "length": 20, "width": 20, "height": 20,
            "name": "SectionBox",
        })
        result = send_command("part_operations", {
            "operation": "section",
            "object_name": "SectionBox",
            "plane": "XY",
            "offset": 10,
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Loft and Sweep
# ---------------------------------------------------------------------------

class TestPartLoftSweep:
    def test_loft_two_sketches(self, clean_document):
        """Loft between two sketches at different heights."""
        # Create two sketches at different Z heights via execute_python
        send_command("execute_python", {
            "code": """
import Part
doc = FreeCAD.ActiveDocument

# Bottom profile — circle at Z=0
s1 = doc.addObject("Sketcher::SketchObject", "LoftBottom")
s1.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 10))

# Top profile — smaller circle at Z=30
s2 = doc.addObject("Sketcher::SketchObject", "LoftTop")
s2.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,30), FreeCAD.Rotation())
s2.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 5))

doc.recompute()
'done'
"""
        })
        result = send_command("part_operations", {
            "operation": "loft",
            "profiles": ["LoftBottom", "LoftTop"],
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_sweep_along_path(self, clean_document):
        """Sweep a profile along a path."""
        send_command("execute_python", {
            "code": """
import Part
doc = FreeCAD.ActiveDocument

# Profile — small circle on XY
s1 = doc.addObject("Sketcher::SketchObject", "SweepProfile")
s1.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 3))

# Path — line from origin to (0,0,40)
path = doc.addObject("Part::Feature", "SweepPath")
path.Shape = Part.makeLine(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,40))

doc.recompute()
'done'
"""
        })
        result = send_command("part_operations", {
            "operation": "sweep",
            "profile": "SweepProfile",
            "path": "SweepPath",
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Compound and Check Geometry
# ---------------------------------------------------------------------------

class TestPartCompoundAndCheck:
    def test_compound_two_boxes(self, clean_document):
        """Create a compound of two boxes."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "CBox1",
        })
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "x": 20, "name": "CBox2",
        })
        result = send_command("part_operations", {
            "operation": "compound",
            "objects": ["CBox1", "CBox2"],
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_check_geometry(self, clean_document):
        """Check geometry of a box (should be valid)."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "CheckBox",
        })
        result = send_command("part_operations", {
            "operation": "check_geometry",
            "object_name": "CheckBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

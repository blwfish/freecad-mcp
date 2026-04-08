"""
PartDesign integration tests — revolution, loft, sweep, shell, thickness,
mirror, linear_pattern, polar_pattern.

Pad, pocket, and datum are already tested in test_e2e_workflows.py.
"""

import time
import pytest
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_document():
    doc_name = f"PDOps_{int(time.time() * 1000) % 100000}"
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
def body_with_pad(clean_document):
    """Create a PartDesign Body with a padded rectangular sketch (20x15x10)."""
    send_command("execute_python", {
        "code": """
import Part
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'PadSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'

sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(20,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,0,0), FreeCAD.Vector(20,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,15,0), FreeCAD.Vector(0,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,15,0), FreeCAD.Vector(0,0,0)))
# Close the profile
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))

doc.recompute()
'done'
"""
    })
    # Pad via the MCP tool
    result = send_command("partdesign_operations", {
        "operation": "pad",
        "sketch_name": "PadSketch",
        "length": 10,
    })
    return clean_document


# ---------------------------------------------------------------------------
# Tests: Revolution
# ---------------------------------------------------------------------------

class TestRevolution:
    def test_revolution_full(self, clean_document):
        """Revolve an L-shaped profile around the Y axis."""
        send_command("execute_python", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'RevSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sketch.MapMode = 'FlatFace'

# L-shaped profile offset from Y axis (must not cross the revolution axis)
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,0,0), FreeCAD.Vector(15,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,0,0), FreeCAD.Vector(15,20,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,20,0), FreeCAD.Vector(5,20,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,20,0), FreeCAD.Vector(5,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))

doc.recompute()
'done'
"""
        })
        result = send_command("partdesign_operations", {
            "operation": "revolution",
            "sketch_name": "RevSketch",
            "axis": "Y",
            "angle": 360,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_revolution_partial(self, clean_document):
        """Revolve 180 degrees."""
        send_command("execute_python", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'Rev180Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sketch.MapMode = 'FlatFace'

sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,0,0), FreeCAD.Vector(15,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,0,0), FreeCAD.Vector(15,10,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,10,0), FreeCAD.Vector(5,10,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,10,0), FreeCAD.Vector(5,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))

doc.recompute()
'done'
"""
        })
        result = send_command("partdesign_operations", {
            "operation": "revolution",
            "sketch_name": "Rev180Sketch",
            "axis": "Y",
            "angle": 180,
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Shell and Thickness
# ---------------------------------------------------------------------------

class TestShellThickness:
    def test_shell(self, body_with_pad):
        """Shell a padded body (hollow out with wall thickness)."""
        result = send_command("partdesign_operations", {
            "operation": "shell",
            "object_name": "Body",
            "thickness": 1.0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_thickness(self, body_with_pad):
        """Apply thickness to a padded body."""
        result = send_command("partdesign_operations", {
            "operation": "thickness",
            "object_name": "Body",
            "thickness": 2.0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Patterns
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_mirror(self, body_with_pad):
        """Mirror the pad feature."""
        result = send_command("partdesign_operations", {
            "operation": "mirror",
            "feature_name": "Pad",
            "plane": "YZ",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_linear_pattern(self, body_with_pad):
        """Linear pattern of the pad (3 instances)."""
        result = send_command("partdesign_operations", {
            "operation": "linear_pattern",
            "feature_name": "Pad",
            "direction": "x",
            "length": 40,
            "occurrences": 3,
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_polar_pattern(self, body_with_pad):
        """Polar pattern of the pad (4 instances over 360°)."""
        result = send_command("partdesign_operations", {
            "operation": "polar_pattern",
            "feature_name": "Pad",
            "axis": "z",
            "angle": 360,
            "occurrences": 4,
        })
        result_str = str(result)
        assert "Unknown" not in result_str


# ---------------------------------------------------------------------------
# Tests: Unknown operation
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_unknown_operation(self, clean_document):
        result = send_command("partdesign_operations", {
            "operation": "nonexistent_op",
        })
        result_str = str(result)
        assert "Unknown" in result_str or "error" in result_str.lower()

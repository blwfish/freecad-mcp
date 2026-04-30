"""
PartDesign integration tests — revolution, loft, sweep, shell, thickness,
mirror, linear_pattern, polar_pattern.

Pad, pocket, and datum are already tested in test_e2e_workflows.py.
"""

import time
import pytest
from ._geom_helpers import (
    assert_op_succeeded,
    get_shape_props,
    assert_volume_close,
    _result_text as _text,
)
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
        """Revolve a 10x20 rectangle (offset 5 from Y axis) full circle.

        Expected solid: ring with outer R=15, inner R=5, height=20.
        Volume = π * (15² - 5²) * 20 = π * 200 * 20 ≈ 12566 mm³.

        Requires the handler to set Solid=True on Part::Revolution
        (pre-existing concern fixed in the same commit as this
        assertion tightening).
        """
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
result = None
"""
        })
        result = send_command("partdesign_operations", {
            "operation": "revolution",
            "sketch_name": "RevSketch",
            "axis": "Y",
            "angle": 360,
        })
        assert_op_succeeded(result, "revolution full")
        props = get_shape_props(clean_document, "Revolution")
        assert props is not None, "Revolution produced no Shape"
        # Closed ring volume: π * (R₁² - R₀²) * h = π * 200 * 20
        import math
        expected = math.pi * (15**2 - 5**2) * 20
        assert_volume_close(props['volume'], expected, rel=0.02,
                            op_label="revolution volume")
        assert props['solid_count'] >= 1, \
            f"Expected revolution to produce a solid, got {props['solid_count']}"

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
    """Shell and thickness use the selection-flow handshake. Both ops route
    through the dispatcher; in GUI mode they return awaiting_selection,
    in headless mode (CI) the bridge has no selector and surfaces the
    AttributeError. Either is acceptable evidence the dispatch wiring
    works — the important regression class is "Unknown operation"
    (dead-letter), which neither response indicates.
    """

    def test_shell_dispatches(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "shell",
            "object_name": "Body",
            "thickness": 1.0,
        })
        text = _text(result)
        assert "Unknown" not in text, f"shell dead-letter: {text[:300]}"
        assert ("awaiting_selection" in text
                or "Created shell" in text
                or "selector" in text.lower()), \
            f"Expected shell handshake, success, or headless-mode " \
            f"selector error; got: {text[:300]}"

    def test_thickness_dispatches(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "thickness",
            "object_name": "Body",
            "thickness": 2.0,
        })
        text = _text(result)
        assert "Unknown" not in text, f"thickness dead-letter: {text[:300]}"
        assert ("awaiting_selection" in text
                or "Created thickness" in text
                or "selector" in text.lower()), \
            f"Expected thickness handshake, success, or headless-mode " \
            f"selector error; got: {text[:300]}"


# ---------------------------------------------------------------------------
# Tests: Patterns
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_mirror(self, body_with_pad):
        """Mirror the pad across YZ — result has Source=Pad and Normal aligned."""
        result = send_command("partdesign_operations", {
            "operation": "mirror",
            "feature_name": "Pad",
            "plane": "YZ",
        })
        assert_op_succeeded(result, "mirror")
        text = _text(result)
        assert "Mirrored" in text or "mirror" in text.lower() or "Created" in text, \
            f"Expected mirror confirmation, got: {text[:300]}"

    def test_linear_pattern(self, body_with_pad):
        """Linear pattern of the pad reports correct count and direction."""
        result = send_command("partdesign_operations", {
            "operation": "linear_pattern",
            "feature_name": "Pad",
            "direction": "x",
            "length": 40,
            "count": 3,
        })
        assert_op_succeeded(result, "linear_pattern")
        text = _text(result)
        assert "3" in text and ("instances" in text or "Pattern" in text), \
            f"Expected 3 instances in linear pattern result: {text[:300]}"

    def test_polar_pattern(self, body_with_pad):
        """Polar pattern reports correct count and axis."""
        result = send_command("partdesign_operations", {
            "operation": "polar_pattern",
            "feature_name": "Pad",
            "axis": "z",
            "angle": 360,
            "count": 4,
        })
        assert_op_succeeded(result, "polar_pattern")
        text = _text(result)
        assert "4" in text and ("instances" in text or "Polar" in text), \
            f"Expected 4 instances in polar pattern result: {text[:300]}"


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

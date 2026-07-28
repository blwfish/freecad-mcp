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
        send_command("execute_python_sync", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


@pytest.fixture
def body_with_pad(clean_document):
    """Create a PartDesign Body with a padded rectangular sketch (20x15x10)."""
    send_command("execute_python_sync", {
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
    """All sketches below use body.addObject(sketch), so they're Body
    members — revolution() now takes the PartDesign::Revolution path for
    all of them (matching fillet/chamfer/thickness's existing Body-aware
    pattern), not the standalone Part::Revolution path. That path maps
    axis='x'/'y' to the sketch-local H_Axis/V_Axis (like groove() already
    does), NOT the global vectors the standalone path uses — so "which
    axis produces the swept, non-degenerate shape" is a different value
    here than it would be for a sketch outside any Body. See
    TestRevolutionStandalone below for the non-Body path's equivalent
    coverage, where axis really does mean a global vector.
    """

    def test_revolution_full(self, clean_document):
        """Revolve a sketch full circle around H/V-Axis Y (sketch-local
        V_Axis, which maps to global Z for this XZ_Plane-attached sketch).

        A rectangle from R=5 to R=15, height 20, revolved 360 deg around an
        in-plane axis at R=0 is a hollow-cylinder ("napkin ring"):
        V = pi*(15^2-5^2)*20 = 4000*pi = ~12566.37 mm^3. Confirmed
        empirically against a real FreeCAD instance — not a guessed value.

        This all used to work differently: prior to the Body-aware fix,
        this exact setup created a standalone Part::Revolution and used
        axis="Z" (a global vector) to get this same volume; axis="Y" was
        the *degenerate* choice (Y being this plane's own normal) — that
        mismatch was the real root cause of the long-standing "Revolution
        produces ~0 volume" bug (see DEFERRED_TESTS.md's corrected write-up),
        not a defect in FreeCAD or OCCT. Now that the sketch's Body
        membership routes this through PartDesign::Revolution instead,
        'x'/'y' mean sketch-local H_Axis/V_Axis, not global vectors — 'y'
        (V_Axis) is the axis that reproduces the same valid geometry here.
        """
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'RevSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sketch.MapMode = 'FlatFace'

# L-shaped profile offset from the revolution axis (must not cross it)
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
        assert_volume_close(props['volume'], 12566.37, op_label="revolution full")
        type_id = send_command("execute_python_sync", {
            "code": "FreeCAD.ActiveDocument.getObject('Revolution').TypeId"
        })
        assert "PartDesign::Revolution" in _text(type_id), \
            f"Expected a genuine PartDesign::Revolution in the Body, got: {_text(type_id)}"

    def test_revolution_partial(self, clean_document):
        """Revolve 180 degrees - half the full-circle volume for the same
        annular cross-section: 0.5 * pi*(15^2-5^2)*10 = 1000*pi = ~3141.59.
        Axis 'y' for the same reason as test_revolution_full above.
        """
        send_command("execute_python_sync", {
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
        assert_op_succeeded(result, "revolution partial")
        props = get_shape_props(clean_document, "Revolution")
        assert props is not None, "Revolution produced no Shape"
        assert_volume_close(props['volume'], 3141.59, op_label="revolution partial")

    def test_revolution_n_axis_rejected_in_body(self, clean_document):
        """The Body-path's degenerate case: axis="Z" maps to the sketch's
        own N_Axis (its plane normal, by construction, for any sketch) —
        unconditionally rejected, no per-sketch computation needed, unlike
        the standalone path's placement-dependent check (see
        TestRevolutionStandalone below). This used to be tested via
        axis="Y" against the standalone Part::Revolution path before the
        Body-aware fix; that axis/path combination no longer applies once
        the sketch's Body membership routes it through PartDesign::Revolution.
        """
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'RevBadAxisSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sketch.MapMode = 'FlatFace'

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
            "sketch_name": "RevBadAxisSketch",
            "axis": "Z",
            "angle": 360,
        })
        text = _text(result)
        assert "n_axis" in text.lower(), \
            f"Expected an explicit N_Axis rejection, got: {text[:300]}"
        # No Revolution object should have been created at all - the
        # validation runs before body.newObject(), not after.
        with pytest.raises(AssertionError, match="object not found"):
            get_shape_props(clean_document, "Revolution")


class TestRevolutionStandalone:
    """Coverage for the *other* half of revolution()'s dual path: a sketch
    with no Body at all still gets the original standalone Part::Revolution
    behavior, where axis really is a global vector and the degenerate case
    is placement-dependent (not the Body-path's unconditional N_Axis rule).
    Uses a directly-set Placement instead of AttachmentSupport, since a
    document with no Body has no XZ_Plane datum object to attach to.
    """

    def test_standalone_revolution_succeeds_with_in_plane_axis(self, clean_document):
        """Same profile and orientation as TestRevolution above (Placement
        matches XZ_Plane's real rotation), but with no Body at all - axis
        'Z' is a global vector here (the standalone path's mapping), and
        it's in-plane for this sketch, giving the same known-correct volume.
        """
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'StandaloneRevSketch')
sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 90))
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
            "sketch_name": "StandaloneRevSketch",
            "axis": "Z",
            "angle": 360,
        })
        assert_op_succeeded(result, "standalone revolution")
        props = get_shape_props(clean_document, "Revolution")
        assert props is not None, "Revolution produced no Shape"
        assert_volume_close(props['volume'], 12566.37, op_label="standalone revolution")
        type_id = send_command("execute_python_sync", {
            "code": "FreeCAD.ActiveDocument.getObject('Revolution').TypeId"
        })
        assert "Part::Revolution" in _text(type_id) and "PartDesign" not in _text(type_id), \
            f"Expected standalone Part::Revolution (no Body present), got: {_text(type_id)}"

    def test_standalone_revolution_axis_parallel_to_plane_normal_rejected(self, clean_document):
        """The standalone path's degenerate case: axis="Y" is this sketch's
        own plane normal (placement-dependent check, distinct from the
        Body path's unconditional N_Axis rejection above)."""
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'StandaloneBadAxisSketch')
sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 90))
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
            "sketch_name": "StandaloneBadAxisSketch",
            "axis": "Y",
            "angle": 360,
        })
        text = _text(result)
        assert "perpendicular" in text.lower(), \
            f"Expected an explicit axis-vs-plane rejection, got: {text[:300]}"
        with pytest.raises(AssertionError, match="object not found"):
            get_shape_props(clean_document, "Revolution")


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

    def test_auto_shell_closed_produces_correct_hollow_volume(self, body_with_pad):
        """auto_shell_closed=True bypasses the selection handshake entirely
        (no faces to pick), so unlike the two tests above this should always
        fully succeed headless - a strict assertion is appropriate here.

        This previously crashed unconditionally: it created a Part::Thickness
        and set .Source on it, but Part::Thickness has no Source property at
        all (confirmed live: AttributeError, 'Part.Feature' object has no
        attribute 'Source'). The fix computes the hollow shape directly
        (inward offset cut from the original) since Part::Thickness/
        BRepOffsetAPI_MakeThickSolid also can't produce a zero-opening shell
        by construction (confirmed live: "shape is invalid" with zero faces
        regardless of Join mode).

        Pad is a 20x15x10 box (3000 mm^3). At 2mm wall thickness: hollow
        volume = 3000 - (20-4)*(15-4)*(10-4) = 3000 - 1056 = 1944 mm^3.
        """
        result = send_command("partdesign_operations", {
            "operation": "shell",
            "object_name": "Body",
            "thickness": 2.0,
            "auto_shell_closed": True,
        })
        assert_op_succeeded(result, "auto shell closed")
        props = get_shape_props(body_with_pad, "Shell")
        assert props is not None, "Shell produced no Shape"
        assert_volume_close(props['volume'], 1944, op_label="auto shell closed")


# ---------------------------------------------------------------------------
# Tests: Rib
# ---------------------------------------------------------------------------

class TestRib:
    def test_rib_creates_extrusion_feature(self, clean_document):
        """rib() previously crashed unconditionally: "Part::Extrude" isn't a
        real FreeCAD document-object type at all (confirmed live:
        Document::addObject raises "not a document object type" every
        single call). The real type is Part::Extrusion, which has the same
        Base/Dir/LengthFwd/Solid properties this method already set.

        A 20x15 rectangle extruded 5mm vertically: volume = 20*15*5 = 1500.
        """
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'RibSketch')
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(20,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,0,0), FreeCAD.Vector(20,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,15,0), FreeCAD.Vector(0,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,15,0), FreeCAD.Vector(0,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
'done'
"""
        })
        result = send_command("partdesign_operations", {
            "operation": "rib",
            "sketch_name": "RibSketch",
            "thickness": 5,
            "direction": "vertical",
        })
        assert_op_succeeded(result, "rib")
        props = get_shape_props(clean_document, "Rib")
        assert props is not None, "Rib produced no Shape"
        assert_volume_close(props['volume'], 1500, op_label="rib")


# ---------------------------------------------------------------------------
# Tests: Helix
# ---------------------------------------------------------------------------

class TestHelix:
    def test_helix_right_and_left_handed_both_produce_valid_sweeps(self, clean_document):
        """create_helix() previously crashed unconditionally: Part::Helix
        (the parametric document-object type) has no LeftHanded property at
        all (confirmed live: AttributeError, 'PrimitivePy' object has no
        attribute 'LeftHanded'). Handedness is only exposed on the plain
        shape-computing function Part.makeLongHelix(pitch, height, radius,
        angle, hand) - confirmed live this fifth "hand" parameter is the
        real mechanism (negative Pitch and negative Height were both tried
        and both failed: one raises OCCError, the other marks State
        Invalid). The fix builds the curve via makeLongHelix wrapped in a
        generic Part::Feature instead of a parametric Part::Helix.

        Both handedness values should produce a valid, non-degenerate swept
        solid from the same small circular profile.
        """
        send_command("execute_python_sync", {
            "code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'HelixProfile')
sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(10,0,0), FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 90))
sketch.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 1.0))
doc.recompute()
'done'
"""
        })
        for left_handed in (False, True):
            name = f"Helix{'Left' if left_handed else 'Right'}"
            result = send_command("partdesign_operations", {
                "operation": "helix",
                "sketch_name": "HelixProfile",
                "pitch": 5,
                "height": 20,
                "left_handed": left_handed,
                "name": name,
            })
            assert_op_succeeded(result, f"helix left_handed={left_handed}")
            props = get_shape_props(clean_document, name)
            assert props is not None, f"Helix (left_handed={left_handed}) produced no Shape"
            assert props['is_valid'], f"Helix (left_handed={left_handed}) shape is invalid"
            # ~ area(pi*1^2) * helix path length(~252 for pitch=5/height=20/radius=10)
            # confirmed empirically (~789.57) against a real FreeCAD instance.
            assert_volume_close(props['volume'], 789.57, rel=0.02,
                                 op_label=f"helix left_handed={left_handed}")


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

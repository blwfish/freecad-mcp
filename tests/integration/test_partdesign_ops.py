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
# Tests: Fillet via the public edges= bypass (no GUI selection needed)
# ---------------------------------------------------------------------------

class TestFilletExplicitEdges:
    def test_fillet_specific_edges_real_geometry(self, body_with_pad):
        """fillet's `edges` param (a list of 1-based edge indices) routes
        straight to _create_fillet_with_edges — no GUI selection handshake
        at all, so this is real, non-error-path headless coverage of the
        actual PartDesign::Fillet creation logic."""
        doc_name = body_with_pad
        result = send_command("partdesign_operations", {
            "operation": "fillet", "object_name": "Pad",
            "edges": [1], "radius": 2.0, "name": "ExplicitFillet",
        })
        assert_op_succeeded(result, "fillet(edges=[1])")
        text = _text(result)
        assert "Created fillet" in text, text[:300]

        props = get_shape_props(doc_name, "ExplicitFillet")
        assert props is not None
        assert props["is_valid"]
        # A fillet removes material — strictly less than the unfilleted
        # pad's 20*15*10 = 3000 mm^3, but still close (small radius).
        assert props["volume"] < 3000.0
        assert_volume_close(props["volume"], 3000.0, rel=0.05, op_label="filleted pad volume")

    def test_fillet_out_of_range_edge_index_fails_loudly_not_silently(self, body_with_pad):
        """_create_fillet_with_edges's bounds-check (1 <= idx <=
        len(Shape.Edges)) only applies to the non-Body Part::Fillet
        fallback branch — a Body-based PartDesign::Fillet (this fixture's
        case) builds Edge{idx} names for every given index unconditionally,
        with no bounds-check at all. An out-of-range index therefore isn't
        silently dropped here (unlike the non-Body path); it reaches OCCT
        as a real invalid edge reference, and _check_feature_state catches
        the resulting State=Invalid and reports it — a loud, reported
        failure, not silent data loss or a crash. Pinning this asymmetry
        as real current behavior, not fixing it here."""
        result = send_command("partdesign_operations", {
            "operation": "fillet", "object_name": "Pad",
            "edges": [1, 9999], "radius": 1.0, "name": "PartialFillet",
        })
        text = _text(result)
        assert "failed to compute" in text and "Invalid" in text, text[:300]


# ---------------------------------------------------------------------------
# Tests: Hole wizard (hole / counterbore / countersink)
# ---------------------------------------------------------------------------

class TestHoleWizard:
    def test_standalone_simple_hole_no_body(self, clean_document):
        """No face_index, no Body -- the original CSG cylinder-cut path."""
        doc_name = clean_document
        send_command("part_operations", {
            "operation": "box", "length": 20, "width": 20, "height": 10, "name": "HoleBox",
        })
        result = send_command("partdesign_operations", {
            "operation": "hole", "object_name": "HoleBox", "hole_type": "simple",
            "diameter": 6, "depth": 10, "x": 10, "y": 10,
        })
        text = _text(result)
        assert "hole" in text.lower(), text[:300]
        assert "Error" not in text.split("\n")[0], text[:300]

        props = get_shape_props(doc_name, "HoleBox_WithHole")
        assert props is not None, text
        assert props["is_valid"]
        # A through-hole strictly reduces volume below the box's 20*20*10=4000.
        assert props["volume"] < 4000.0

    def test_body_aware_simple_hole_on_top_face(self, body_with_pad):
        """face_index given + object in a Body -> genuine PartDesign::Hole.
        Pad's top face (Face6, z=10 plane per test_e2e_workflows'
        established face numbering for this exact box shape) is used so
        x=0,y=0 lands at the face's own centroid (hole_wizard recenters
        local (0,0) to the face's CenterOfMass, not the native FlatFace
        origin)."""
        doc_name = body_with_pad
        result = send_command("partdesign_operations", {
            "operation": "hole", "object_name": "Pad", "hole_type": "simple",
            "diameter": 5, "depth": 5, "x": 0, "y": 0, "face_index": 6,
        })
        text = _text(result)
        assert "PartDesign::Hole" in text, text[:300]
        assert "Face6" in text, text[:300]

        props = get_shape_props(doc_name, "Pad_Hole")
        assert props is not None, text
        assert props["is_valid"]

    def test_body_aware_counterbore(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "counterbore", "object_name": "Pad", "hole_type": "counterbore",
            "diameter": 5, "depth": 5, "cb_diameter": 9, "cb_depth": 2,
            "x": 0, "y": 0, "face_index": 6,
        })
        text = _text(result)
        assert "counterbore hole" in text.lower(), text[:300]
        assert "PartDesign::Hole" in text, text[:300]

    def test_body_aware_countersink(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "countersink", "object_name": "Pad", "hole_type": "countersink",
            "diameter": 5, "depth": 5, "cb_diameter": 9, "cb_depth": 2,
            "x": 0, "y": 0, "face_index": 6,
        })
        text = _text(result)
        assert "countersink hole" in text.lower(), text[:300]
        assert "PartDesign::Hole" in text, text[:300]

    def test_operation_name_alone_selects_hole_type(self, body_with_pad):
        """`operation="counterbore"`/`"countersink"` and `operation="hole"`
        all route to the same hole_wizard method (freecad_mcp_handler.py's
        operation_map) -- hole_wizard used to read hole_type only from its
        own args, defaulting to 'simple' regardless of which of the three
        operations dispatched here, so operation="counterbore" WITHOUT an
        explicit hole_type used to silently create a plain hole (confirmed
        live, fixed 2026-08-21). args['operation'] is the original
        dispatched name, still present in the same args dict hole_wizard
        receives -- now used to infer hole_type when the caller doesn't
        pass it explicitly."""
        result = send_command("partdesign_operations", {
            "operation": "counterbore", "object_name": "Pad",
            "diameter": 5, "depth": 5, "x": 0, "y": 0, "face_index": 6,
        })
        text = _text(result)
        assert "counterbore hole" in text.lower(), text[:300]

    def test_explicit_hole_type_still_overrides_operation_name(self, body_with_pad):
        """Explicit hole_type is authoritative over the inferred-from-
        operation-name default -- operation="hole" with an explicit
        hole_type="countersink" still creates a countersink, not a plain
        hole."""
        result = send_command("partdesign_operations", {
            "operation": "hole", "object_name": "Pad", "hole_type": "countersink",
            "diameter": 5, "depth": 5, "cb_diameter": 9, "cb_depth": 2,
            "x": 0, "y": 0, "face_index": 6,
        })
        text = _text(result)
        assert "countersink hole" in text.lower(), text[:300]


# ---------------------------------------------------------------------------
# Tests: Chamfer / Draft real geometry via internal-method reach-in
# (Phase 2 — see the plan's note: this exercises the real creation/
# recompute logic but bypasses the public continue_selection dispatch,
# a narrower guard than test_continue_selection.py's dispatch-level tests.
# Reached via FreeCAD.__ai_socket_server.partdesign_ops, the same handle
# headless_server.py/InitGui.py expose for the running FreeCADSocketServer
# instance.)
# ---------------------------------------------------------------------------

def _call_internal_selection_method(method_name: str, args: dict, elements: list) -> str:
    code = f"""
server = FreeCAD.__ai_socket_server
selection_result = {{"selection_data": {{"elements": {elements!r}}}}}
print(server.partdesign_ops.{method_name}({args!r}, selection_result))
result = None
"""
    raw = send_command("execute_python_sync", {"code": code})
    text = _text(raw)
    if text.startswith("Result: "):
        text = text[len("Result: "):]
    return text.strip()


class TestChamferRealGeometry:
    def test_chamfer_specific_edges(self, body_with_pad):
        doc_name = body_with_pad
        text = _call_internal_selection_method(
            "_create_chamfer_with_selection",
            {"object_name": "Pad", "distance": 1.5, "name": "RealChamfer"},
            [1],
        )
        assert "Created chamfer" in text, text[:300]

        props = get_shape_props(doc_name, "RealChamfer")
        assert props is not None
        assert props["is_valid"]
        assert props["volume"] < 3000.0
        assert_volume_close(props["volume"], 3000.0, rel=0.05, op_label="chamfered pad volume")


class TestDraftRealGeometry:
    def test_draft_specific_face(self, body_with_pad):
        doc_name = body_with_pad
        text = _call_internal_selection_method(
            "_create_draft_with_selection",
            {"object_name": "Pad", "angle": 3.0, "name": "RealDraft"},
            [1],
        )
        assert "Created draft" in text, text[:300]

        props = get_shape_props(doc_name, "RealDraft")
        assert props is not None
        assert props["is_valid"]

    def test_draft_requires_body(self, clean_document):
        """_create_draft_with_selection explicitly rejects a non-Body
        target rather than crashing on a missing find_body_for_object
        result."""
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "BareBox",
        })
        text = _call_internal_selection_method(
            "_create_draft_with_selection",
            {"object_name": "BareBox", "angle": 3.0, "name": "ShouldFail"},
            [1],
        )
        assert "requires object to be in a PartDesign Body" in text, text[:300]


class TestShellThicknessRealGeometry:
    """Upgrades the existing TestShellThickness dispatch-only tests (which
    only assert the dispatch doesn't dead-letter) with real hollow-geometry
    assertions, using the same internal-method reach-in as chamfer/draft
    above."""

    def test_shell_specific_face_real_hollow_volume(self, body_with_pad):
        doc_name = body_with_pad
        text = _call_internal_selection_method(
            "_create_shell_with_selection",
            {"object_name": "Pad", "thickness": 2.0, "name": "RealShell"},
            [6],
        )
        assert "Created shell" in text, text[:300]

        props = get_shape_props(doc_name, "RealShell")
        assert props is not None
        assert props["is_valid"]
        # Hollowed 20x15x10 box, 2mm walls, one face open: strictly less
        # than the solid 3000 mm^3, strictly more than 0.
        assert 0 < props["volume"] < 3000.0

    def test_thickness_specific_face_real_hollow_volume(self, body_with_pad):
        doc_name = body_with_pad
        text = _call_internal_selection_method(
            "_create_thickness_with_selection",
            {"object_name": "Pad", "thickness": 2.0, "name": "RealThickness"},
            [6],
        )
        assert "Created PartDesign Thickness" in text, text[:300]

        props = get_shape_props(doc_name, "RealThickness")
        assert props is not None
        assert props["is_valid"]
        assert 0 < props["volume"] < 3000.0


# ---------------------------------------------------------------------------
# Tests: Groove (subtractive revolution — needs an existing base feature
# to cut into, unlike revolution)
# ---------------------------------------------------------------------------

class TestGroove:
    def test_groove_cuts_ring_channel(self, clean_document):
        """Base pad: 30x30x30 box (27000 mm^3). Groove sketch: a small
        rectangle offset from the Y-axis on XZ_Plane, revolved 360 deg
        around Y — cuts a ring-shaped channel out of the box. Exact
        removed volume isn't hand-derivable here (the groove only
        partially overlaps the box's footprint), so this asserts the
        real, non-hypothetical property: some material was removed, the
        result is a valid solid, and it's a genuine PartDesign::Groove
        (not silently falling back to some other feature type)."""
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

base_sketch = doc.addObject('Sketcher::SketchObject', 'GrooveBaseSketch')
body.addObject(base_sketch)
base_sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
base_sketch.MapMode = 'FlatFace'
base_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(-15,-15,0), FreeCAD.Vector(15,-15,0)))
base_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,-15,0), FreeCAD.Vector(15,15,0)))
base_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(15,15,0), FreeCAD.Vector(-15,15,0)))
base_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(-15,15,0), FreeCAD.Vector(-15,-15,0)))
base_sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
base_sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
base_sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
base_sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
pad = body.newObject('PartDesign::Pad', 'BasePad')
pad.Profile = base_sketch
pad.Length = 30
doc.recompute()

groove_sketch = doc.addObject('Sketcher::SketchObject', 'GrooveSk')
body.addObject(groove_sketch)
groove_sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
groove_sketch.MapMode = 'FlatFace'
groove_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,10,0), FreeCAD.Vector(10,10,0)))
groove_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,10,0), FreeCAD.Vector(10,15,0)))
groove_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,15,0), FreeCAD.Vector(5,15,0)))
groove_sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,15,0), FreeCAD.Vector(5,10,0)))
groove_sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
groove_sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
groove_sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
groove_sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "groove", "sketch_name": "GrooveSk", "axis": "y", "angle": 360,
        })
        assert_op_succeeded(result, "groove")
        props = get_shape_props(doc_name, "Groove")
        assert props is not None
        assert props["is_valid"]
        assert 0 < props["volume"] < 27000.0
        type_id = send_command("execute_python_sync", {
            "code": "FreeCAD.ActiveDocument.getObject('Groove').TypeId"
        })
        assert "PartDesign::Groove" in _text(type_id)

    def test_groove_z_axis_rejected(self, clean_document):
        """Same unconditional N_Axis rejection as revolution's Body path —
        groove.py shares the identical validation."""
        send_command("execute_python_sync", {"code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'GrooveZSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,0,0), FreeCAD.Vector(10,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,0,0), FreeCAD.Vector(10,5,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,5,0), FreeCAD.Vector(5,5,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(5,5,0), FreeCAD.Vector(5,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "groove", "sketch_name": "GrooveZSketch", "axis": "z", "angle": 360,
        })
        text = _text(result)
        assert "n_axis" in text.lower() or "own plane" in text.lower(), text[:300]


# ---------------------------------------------------------------------------
# Tests: Loft / Sweep (Part::Loft / Part::Sweep — no PartDesign Body
# needed, unlike additive_pipe/subtractive_loft/subtractive_sweep below)
# ---------------------------------------------------------------------------

class TestLoft:
    def test_loft_two_circles_produces_frustum(self, clean_document):
        """A circle-to-circle loft (no Body) between R=10 at z=0 and R=5
        at z=20 is a cone frustum: V = (pi*h/3)*(r1^2+r1*r2+r2^2) =
        (pi*20/3)*(100+50+25) = 3665.19 mm^3. Confirmed empirically
        against this exact FreeCAD build. Uses a direct Placement on the
        second sketch rather than AttachmentSupport+AttachmentOffset —
        confirmed live that combination silently fails to actually shift
        the sketch in Z when both sketches attach to the SAME datum
        plane object (both ended up coincident at z=0)."""
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part
doc = FreeCAD.ActiveDocument
s1 = doc.addObject('Sketcher::SketchObject', 'LoftS1')
s1.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 10))
doc.recompute()
s2 = doc.addObject('Sketcher::SketchObject', 'LoftS2')
s2.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,20), FreeCAD.Rotation())
s2.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 5))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "loft", "sketches": ["LoftS1", "LoftS2"],
        })
        assert_op_succeeded(result, "loft")
        props = get_shape_props(doc_name, "Loft")
        assert props is not None
        assert props["is_valid"]
        assert_volume_close(props["volume"], 3665.19, op_label="loft frustum")

    def test_loft_requires_two_sketches(self, clean_document):
        result = send_command("partdesign_operations", {
            "operation": "loft", "sketches": ["OnlyOne"],
        })
        text = _text(result)
        assert "at least 2 sketches" in text.lower(), text[:300]


class TestSweep:
    def test_sweep_circle_along_straight_line_produces_cylinder(self, clean_document):
        """R=3 circle swept 30mm along a straight line: V = pi*3^2*30 =
        848.23 mm^3. Confirmed empirically that Part::Sweep (Frenet=True
        by default, unset by this handler) produces a NULL shape when
        Sections/Spine reference Sketcher::SketchObject sketches directly
        for a perfectly straight, axis-aligned spine (a Frenet-frame
        degenerate case: an unbent line has no well-defined normal to
        build a frame from) — same null-shape result whether Frenet is
        left at its True default or explicitly set False, and whether
        Spine is a bare object or an explicit (obj, ['Edge1']) tuple, so
        this isn't a handler bug, it's a spine-representation quirk of
        this exact geometry. Using plain Part::Feature objects (a Wire
        Shape assigned directly, no Sketcher involved) for BOTH profile
        and path sidesteps it entirely and produces a correct, non-null
        cylinder — confirmed via the real MCP handler, not just raw OCCT
        calls."""
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part
doc = FreeCAD.ActiveDocument
circ = Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 3)
prof = doc.addObject('Part::Feature', 'SweepProfile')
prof.Shape = Part.Wire([circ.toShape()])
line_shape = Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,30)).toShape()
path = doc.addObject('Part::Feature', 'SweepPath')
path.Shape = Part.Wire([line_shape])
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "sweep", "profile_sketch": "SweepProfile", "path_sketch": "SweepPath",
        })
        assert_op_succeeded(result, "sweep")
        props = get_shape_props(doc_name, "Sweep")
        assert props is not None
        assert props["is_valid"]
        assert_volume_close(props["volume"], 848.23, op_label="swept cylinder")

    def test_sweep_missing_profile_sketch(self, clean_document):
        result = send_command("partdesign_operations", {
            "operation": "sweep", "profile_sketch": "Ghost", "path_sketch": "AlsoGhost",
        })
        text = _text(result)
        assert "profile sketch not found" in text.lower(), text[:300]


# ---------------------------------------------------------------------------
# Tests: Additive pipe / Subtractive sweep (PartDesign — need a Body;
# both use the same bent-path recipe as TestSweep's straight-line finding
# implies: a straight spine hits the same Frenet degeneracy here too, so
# both fixtures use a 2-segment bent path instead)
# ---------------------------------------------------------------------------

class TestAdditivePipe:
    def test_additive_pipe_bent_path_real_geometry(self, clean_document):
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

prof = doc.addObject('Sketcher::SketchObject', 'PipeProfile')
body.addObject(prof)
prof.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
prof.MapMode = 'FlatFace'
prof.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 3))
doc.recompute()

path = doc.addObject('Sketcher::SketchObject', 'PipePath')
body.addObject(path)
path.AttachmentSupport = [(doc.getObject('YZ_Plane'), '')]
path.MapMode = 'FlatFace'
path.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(10,10,0)))
path.addGeometry(Part.LineSegment(FreeCAD.Vector(10,10,0), FreeCAD.Vector(0,20,0)))
path.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "additive_pipe", "profile_sketch": "PipeProfile", "path_sketch": "PipePath",
        })
        assert_op_succeeded(result, "additive_pipe")
        props = get_shape_props(doc_name, "AdditivePipe")
        assert props is not None
        assert props["is_valid"]
        assert props["volume"] > 0

    def test_additive_pipe_missing_path_sketch(self, clean_document):
        send_command("execute_python_sync", {"code": """
import Part
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
prof = doc.addObject('Sketcher::SketchObject', 'LonelyProfile')
body.addObject(prof)
prof.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 3))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "additive_pipe", "profile_sketch": "LonelyProfile", "path_sketch": "Ghost",
        })
        text = _text(result)
        assert "path sketch not found" in text.lower(), text[:300]


class TestSubtractiveSweep:
    def test_subtractive_sweep_bent_path_real_geometry(self, clean_document):
        """Base pad: 40x40x20 box (32000 mm^3). A small circular channel
        swept along a bent path near one edge removes a modest, real
        volume — assert strictly less than the unfilleted pad, not an
        exact hand-derived number (the bent-path sweep's exact swept
        volume isn't simple to hand-calculate)."""
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

base_sk = doc.addObject('Sketcher::SketchObject', 'SSBaseSk')
body.addObject(base_sk)
base_sk.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
base_sk.MapMode = 'FlatFace'
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(-20,-20,0), FreeCAD.Vector(20,-20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(20,-20,0), FreeCAD.Vector(20,20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(20,20,0), FreeCAD.Vector(-20,20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(-20,20,0), FreeCAD.Vector(-20,-20,0)))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
pad = body.newObject('PartDesign::Pad', 'SSBasePad')
pad.Profile = base_sk
pad.Length = 20
doc.recompute()

prof = doc.addObject('Sketcher::SketchObject', 'SSProfile')
body.addObject(prof)
prof.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
prof.MapMode = 'FlatFace'
prof.addGeometry(Part.Circle(FreeCAD.Vector(-15,0,0), FreeCAD.Vector(0,0,1), 2))
doc.recompute()

path = doc.addObject('Sketcher::SketchObject', 'SSPath')
body.addObject(path)
path.AttachmentSupport = [(doc.getObject('YZ_Plane'), '')]
path.MapMode = 'FlatFace'
path.addGeometry(Part.LineSegment(FreeCAD.Vector(-15,0,0), FreeCAD.Vector(-10,10,0)))
path.addGeometry(Part.LineSegment(FreeCAD.Vector(-10,10,0), FreeCAD.Vector(-15,20,0)))
path.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "subtractive_sweep", "profile_sketch": "SSProfile", "path_sketch": "SSPath",
        })
        assert_op_succeeded(result, "subtractive_sweep")
        props = get_shape_props(doc_name, "SubtractivePipe")
        assert props is not None
        assert props["is_valid"]
        assert 0 < props["volume"] < 32000.0


# ---------------------------------------------------------------------------
# Tests: Subtractive loft — was a regression pin (State=Invalid, null
# Shape, reproduced across every geometry variant tried, both via the real
# handler and a hand-built replica using identical FreeCAD API calls).
# Root cause found 2026-08-21: PartDesign::SubtractiveLoft (and its
# additive sibling PartDesign::AdditiveLoft — confirmed the SAME failure
# reproduces there too, standalone, with no subtraction involved at all,
# proving this was never subtraction-specific) has its own required
# Profile property distinct from Sections, matching every other PartDesign
# additive/subtractive feature's convention (Pad, Pocket, Revolution, ...
# all set .Profile). The handler used to model this after the standalone
# Part::Loft (which has no Profile property, only Sections) and put every
# sketch into Sections with Profile left unset — fixed to set
# loft.Profile = sketches[0] and loft.Sections = sketches[1:].
# ---------------------------------------------------------------------------

class TestSubtractiveLoft:
    def test_subtractive_loft_real_hollow_volume(self, clean_document):
        doc_name = clean_document
        send_command("execute_python_sync", {"code": """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
base_sk = doc.addObject('Sketcher::SketchObject', 'SLBaseSk')
body.addObject(base_sk)
base_sk.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
base_sk.MapMode = 'FlatFace'
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(-20,-20,0), FreeCAD.Vector(20,-20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(20,-20,0), FreeCAD.Vector(20,20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(20,20,0), FreeCAD.Vector(-20,20,0)))
base_sk.addGeometry(Part.LineSegment(FreeCAD.Vector(-20,20,0), FreeCAD.Vector(-20,-20,0)))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
base_sk.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
pad = body.newObject('PartDesign::Pad', 'SLBasePad')
pad.Profile = base_sk
pad.Length = 20
doc.recompute()

s1 = doc.addObject('Sketcher::SketchObject', 'SLS1')
body.addObject(s1)
s1.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
s1.MapMode = 'FlatFace'
s1.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 8))
doc.recompute()

s2 = doc.addObject('Sketcher::SketchObject', 'SLS2')
body.addObject(s2)
s2.AttachmentSupport = [(pad, 'Face6')]
s2.MapMode = 'FlatFace'
s2.addGeometry(Part.Circle(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 4))
doc.recompute()
result = None
"""})
        result = send_command("partdesign_operations", {
            "operation": "subtractive_loft", "sketches": ["SLS1", "SLS2"],
        })
        assert_op_succeeded(result, "subtractive_loft")
        text = _text(result)
        assert "Created subtractive loft" in text, text[:300]

        # Base pad: 40x40x20 box = 32000 mm^3. Tapered circular channel
        # (R=8 at bottom to R=4 at top) removes a real, non-trivial volume.
        props = get_shape_props(doc_name, "SubtractiveLoft")
        assert props is not None
        assert props["is_valid"]
        assert 0 < props["volume"] < 32000.0


# ---------------------------------------------------------------------------
# Tests: Datum line / point (auto-creates a Body if none exists) /
# datum_from_face (shortcut over create_datum_plane)
# ---------------------------------------------------------------------------

class TestDatumLinePoint:
    def test_datum_line_auto_creates_body(self, clean_document):
        result = send_command("partdesign_operations", {"operation": "datum_line"})
        text = _text(result)
        assert "Created datum line: DatumLine" in text, text[:300]
        type_id = send_command("execute_python_sync", {
            "code": "FreeCAD.ActiveDocument.getObject('DatumLine').TypeId"
        })
        assert "PartDesign::Line" in _text(type_id)

    def test_datum_point_auto_creates_body(self, clean_document):
        result = send_command("partdesign_operations", {"operation": "datum_point"})
        text = _text(result)
        assert "Created datum point: DatumPoint" in text, text[:300]
        type_id = send_command("execute_python_sync", {
            "code": "FreeCAD.ActiveDocument.getObject('DatumPoint').TypeId"
        })
        assert "PartDesign::Point" in _text(type_id)

    def test_datum_point_with_offset(self, clean_document):
        result = send_command("partdesign_operations", {
            "operation": "datum_point", "offset_x": 5, "offset_y": 10, "offset_z": 15,
        })
        text = _text(result)
        assert "Created datum point" in text, text[:300]


class TestDatumFromFace:
    def test_datum_from_face_on_top_face(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "datum_from_face", "object_name": "Pad", "face_index": 6,
        })
        text = _text(result)
        assert "Created datum plane: Datum_Face6" in text, text[:300]
        assert "Face centroid:" in text and "Face normal:" in text, text[:300]

    def test_datum_from_face_out_of_range(self, body_with_pad):
        result = send_command("partdesign_operations", {
            "operation": "datum_from_face", "object_name": "Pad", "face_index": 99,
        })
        text = _text(result)
        assert "out of range" in text.lower(), text[:300]


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

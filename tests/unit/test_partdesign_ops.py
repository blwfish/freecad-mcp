"""Unit tests for PartDesignOpsHandler.

Coverage focus: parametric design operations that are the highest-stakes
in any FreeCAD workflow. The handler is 1,440 LOC with ~25 operations
and previously had zero unit tests (7% statement coverage). This file
adds ~30 tests covering pad/pocket, fillet/chamfer (including the
selection-flow handshake), hole_wizard, patterns, mirror, revolution/
groove, loft/sweep, shell/thickness/draft (selection flow), and
datum_from_face.

Selection-flow coverage: fillet, chamfer, hole, draft, shell, thickness
all return ``{"status": "awaiting_selection", ...}`` JSON when invoked
without explicit edges/faces. Tests verify the handshake structure
plus the non-selection auto/explicit-edges paths.
"""

import json
import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Part,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    make_sketch,
    make_body,
    assert_error_contains,
    assert_success_contains,
    assert_awaiting_selection,
    _Vec,
    _Placement,
)

from handlers.partdesign_ops import PartDesignOpsHandler


def _make_next_addobject_invalid(doc, type_id):
    """Make the next doc.addObject(type_id, ...) call return an object with
    State=['Invalid'], preserving make_mock_doc's normal _add_object setup
    (appends to doc.Objects, sets Shape defaults) for every other call.
    Used to test the post-recompute Invalid-state check added to every
    PartDesign feature-creating method."""
    original = doc.addObject.side_effect

    def _wrapped(t, name=None):
        obj = original(t, name)
        if t == type_id:
            obj.State = ['Invalid']
        return obj

    doc.addObject.side_effect = _wrapped


def _make_next_newobject_invalid(body, type_id):
    """Same as above, but for body.newObject (PartDesign-native features).
    make_body's newObject is a plain return_value MagicMock (not a
    side_effect factory), so this just flags the shared return object —
    fine as long as the test only creates one feature of this type_id."""
    body.newObject.return_value.State = ['Invalid']


# ---------------------------------------------------------------------------
# Pad / pocket
# ---------------------------------------------------------------------------

class TestPadSketch(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.pad_sketch({'sketch_name': 'S', 'length': 10})
        assert_error_contains(self, result, "no active document")

    def test_missing_sketch(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.pad_sketch({'sketch_name': 'Ghost'})
        assert_error_contains(self, result, "ghost", "not found")

    def test_pad_creates_feature_in_body(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.pad_sketch({
            'sketch_name': 'S', 'length': 25, 'name': 'MyPad',
        })

        # Body.newObject was called with the right TypeId and requested name
        body.newObject.assert_called_with("PartDesign::Pad", "MyPad")
        # Pad properties (Profile, Length) were assigned
        pad = body.newObject.return_value
        self.assertEqual(pad.Profile, sketch)
        self.assertEqual(pad.Length, 25)
        assert_success_contains(self, result, "S", "25", "Body")

    def test_pad_reversed_flag_passed_through(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.pad_sketch({
            'sketch_name': 'S', 'length': 10, 'reversed': True,
        })

        # Pad object should have Reversed=True
        pad = body.newObject.return_value
        self.assertTrue(pad.Reversed)
        assert_success_contains(self, result, "reversed")

    def test_pad_invalid_state_diagnoses_open_wires(self):
        """If the pad's State contains 'Invalid', the handler must call the
        wire-diagnosis helper and surface the result instead of falsely
        reporting success."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        # Pre-set the body.newObject return so we can inject Invalid state
        invalid_pad = MagicMock()
        invalid_pad.Name = "Pad001"
        invalid_pad.State = ['Invalid']
        body.newObject = MagicMock(return_value=invalid_pad)

        result = self.handler.pad_sketch({'sketch_name': 'S', 'length': 10})

        assert_error_contains(self, result, "failed to compute")


class TestPocket(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_pocket_needs_body(self):
        """Sketch outside a Body cannot be pocketed."""
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])  # No Body in doc
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.pocket({'sketch_name': 'S', 'length': 5})

        assert_error_contains(self, result, "must be in a partdesign body")

    def test_pocket_creates_feature_in_body(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.pocket({
            'sketch_name': 'S', 'length': 8, 'name': 'Hole',
        })

        body.newObject.assert_called_with("PartDesign::Pocket", "Hole")
        pocket = body.newObject.return_value
        self.assertEqual(pocket.Profile, sketch)
        self.assertEqual(pocket.Length, 8)
        assert_success_contains(self, result, "S", "8")

    def test_pocket_invalid_state_diagnoses_open_wires(self):
        """If the pocket's State contains 'Invalid', the handler must call
        the wire-diagnosis helper and surface the result instead of falsely
        reporting success. pad_sketch's identical check has a test
        (test_pad_invalid_state_diagnoses_open_wires); this one didn't —
        confirmed as a surviving mutant during the full review (disabling
        this check entirely left all TestPocket tests passing)."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        invalid_pocket = MagicMock()
        invalid_pocket.Name = "Pocket001"
        invalid_pocket.State = ['Invalid']
        body.newObject = MagicMock(return_value=invalid_pocket)

        result = self.handler.pocket({'sketch_name': 'S', 'length': 10})

        assert_error_contains(self, result, "failed to compute")

    def test_pocket_accepts_depth_alias(self):
        """`depth` and `length` are interchangeable for pocket."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.pocket({'sketch_name': 'S', 'depth': 12})

        pocket = body.newObject.return_value
        self.assertEqual(pocket.Length, 12)


# ---------------------------------------------------------------------------
# Fillet — selection flow + explicit edges + auto
# ---------------------------------------------------------------------------

class TestFilletEdges(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_no_args_returns_awaiting_selection(self):
        """Without edges or auto_select_all, fillet asks the user to pick."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 2.0,
        })

        op_id = assert_awaiting_selection(self, result)
        self.assertEqual(op_id, "op_test_001")
        # Selector got the request with the right tool name
        self.handler.selector.request_selection.assert_called_once()
        kwargs = self.handler.selector.request_selection.call_args.kwargs
        self.assertEqual(kwargs.get("tool_name"), "fillet_edges")
        self.assertEqual(kwargs.get("selection_type"), "edges")
        self.assertEqual(kwargs.get("radius"), 2.0)

    def test_explicit_edges_creates_part_fillet_when_no_body(self):
        """Object outside a Body gets a Part::Fillet (legacy fallback)."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 1.5, 'edges': [1, 2, 3],
        })

        # Part::Fillet was created (Body fallback path)
        doc.addObject.assert_called_with("Part::Fillet", "Fillet")
        fillet = doc.Objects[-1]
        self.assertEqual(fillet.Base, box)
        self.assertEqual(fillet.Edges, [(1, 1.5, 1.5), (2, 1.5, 1.5), (3, 1.5, 1.5)])
        assert_success_contains(self, result, "3 edges", "1.5")

    def test_explicit_edges_rejects_out_of_range_index_when_no_body(self):
        """Out-of-range indices used to be silently dropped here (edge_list
        built via `if 1 <= idx <= n_edges`), creating a fillet on fewer
        edges than requested with no indication any were skipped -- the
        Body path has no such filter at all, so it fails loudly via
        _check_feature_state instead. Fixed 2026-08-21 to reject up front
        here too, matching that same fail-loud contract, instead of
        silently under-filleting."""
        box = make_box_object("B")  # default 12 edges
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 1.5, 'edges': [1, 99],
        })

        assert_error_contains(self, result, "99", "valid range")
        # No object should have been created at all -- validated before
        # doc.addObject(), not after a partial fillet already exists.
        doc.addObject.assert_not_called()

    def test_explicit_edges_creates_partdesign_fillet_in_body(self):
        """Object inside a Body gets a PartDesign::Fillet."""
        box = make_box_object("B")
        body = make_body("Body", tip=box, group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 2.0, 'edges': [1, 4],
        })

        body.newObject.assert_called_with("PartDesign::Fillet", "Fillet")
        pd_fillet = body.newObject.return_value
        self.assertEqual(pd_fillet.Radius, 2.0)
        self.assertEqual(pd_fillet.Base, (box, ['Edge1', 'Edge4']))
        assert_success_contains(self, result, "2 edges")

    def test_auto_select_all_fillets_every_edge(self):
        box = make_box_object("B")  # default 12 edges
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 1.0, 'auto_select_all': True,
        })

        doc.addObject.assert_called_with("Part::Fillet", "Fillet")
        fillet = doc.Objects[-1]
        # 12 edges total, all in fillet.Edges
        self.assertEqual(len(fillet.Edges), 12)
        assert_success_contains(self, result, "all 12")

    def test_continue_selection_processes_edges(self):
        """When _continue_selection is set, fillet builds from completed picks."""
        box = make_box_object("B")
        body = make_body("Body", tip=box, group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        # Selector returns the user's picks
        self.handler.selector.complete_selection.return_value = {
            "selection_data": {"elements": [1, 5, 9]},
        }

        result = self.handler.fillet_edges({
            'object_name': 'B', 'radius': 3.0,
            '_continue_selection': True,
            '_operation_id': 'op_test_001',
        })

        body.newObject.assert_called_with("PartDesign::Fillet", "Fillet")
        pd_fillet = body.newObject.return_value
        self.assertEqual(pd_fillet.Base, (box, ['Edge1', 'Edge5', 'Edge9']))
        assert_success_contains(self, result, "3 selected edges", "3.0")


# ---------------------------------------------------------------------------
# Chamfer — selection flow
# ---------------------------------------------------------------------------

class TestChamferEdges(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_no_args_returns_awaiting_selection(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.chamfer_edges({
            'object_name': 'B', 'distance': 1.5,
        })
        op_id = assert_awaiting_selection(self, result)
        kwargs = self.handler.selector.request_selection.call_args.kwargs
        self.assertEqual(kwargs.get("tool_name"), "chamfer_edges")
        self.assertEqual(kwargs.get("distance"), 1.5)

    def test_auto_select_all(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.chamfer_edges({
            'object_name': 'B', 'distance': 0.5, 'auto_select_all': True,
        })
        doc.addObject.assert_called_with("Part::Chamfer", "Chamfer")
        assert_success_contains(self, result, "all 12")

    def test_auto_select_all_no_edges_errors(self):
        """A zero-edge object must get a clear message, not a false 'all 0 edges'
        success or an AttributeError (mirrors _create_fillet_auto)."""
        box = make_box_object("B")
        box.Shape.Edges = []
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.chamfer_edges({
            'object_name': 'B', 'distance': 0.5, 'auto_select_all': True,
        })
        self.assertIn("no edges to chamfer", result)
        doc.addObject.assert_not_called()


# ---------------------------------------------------------------------------
# Hole wizard
# ---------------------------------------------------------------------------

class TestHoleWizard(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_simple_hole_creates_cylinder_and_cut(self):
        box = make_box_object("Plate")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Plate', 'hole_type': 'simple',
            'diameter': 6, 'depth': 10, 'x': 5, 'y': 5,
        })

        # Cylinder + Cut were added; counterbore ones were not
        type_ids_added = [c.args[0] for c in doc.addObject.call_args_list]
        self.assertIn("Part::Cylinder", type_ids_added)
        self.assertIn("Part::Cut", type_ids_added)
        # Simple hole does NOT create Counterbore or Cone
        self.assertNotIn("Part::Cone", type_ids_added)
        assert_success_contains(self, result, "simple", "6mm", "Plate")

    def test_counterbore_creates_two_cylinders_plus_fuse_plus_cut(self):
        box = make_box_object("Plate")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Plate', 'hole_type': 'counterbore',
            'diameter': 6, 'depth': 20,
            'cb_diameter': 12, 'cb_depth': 4,
        })

        type_ids = [c.args[0] for c in doc.addObject.call_args_list]
        # Through-hole cylinder, counterbore cylinder, fuse, cut
        self.assertEqual(type_ids.count("Part::Cylinder"), 2)
        self.assertEqual(type_ids.count("Part::Fuse"), 1)
        self.assertEqual(type_ids.count("Part::Cut"), 1)
        assert_success_contains(self, result, "counterbore")

    def test_countersink_creates_cylinder_plus_cone(self):
        box = make_box_object("Plate")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Plate', 'hole_type': 'countersink',
            'diameter': 6, 'depth': 15,
        })

        type_ids = [c.args[0] for c in doc.addObject.call_args_list]
        self.assertIn("Part::Cone", type_ids)
        self.assertIn("Part::Cylinder", type_ids)
        assert_success_contains(self, result, "countersink")

    def test_creates_partdesign_hole_in_body_via_face_index(self):
        """When face_index is given and the object is in a Body,
        hole_wizard should create a genuine PartDesign::Hole (with an
        auto-generated circle sketch attached to that face) instead of
        the raw CSG cylinder-and-boolean-cut approach. Confirmed live via
        a real FreeCAD instance -- including that FlatFace attachment's
        native local origin is NOT the face's centroid (it's the
        underlying surface's own parametric origin), so the sketch's
        AttachmentOffset must be computed and set explicitly to recenter
        local (0,0) at the face center."""
        box = make_box_object("Pad")
        face = box.Shape.Faces[5]  # face_index=6 (1-based)
        face.CenterOfMass = _Vec(5, 5, 10)

        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        hole_sketch = MagicMock()
        hole_sketch.Placement = _Placement()  # identity: Base=(0,0,0), Rotation=identity
        hole = MagicMock()
        hole.State = []
        body.newObject.side_effect = [hole_sketch, hole]

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'face_index': 6, 'hole_type': 'simple',
            'diameter': 6, 'depth': 8, 'x': 2, 'y': 1,
        })

        body.newObject.assert_any_call("Sketcher::SketchObject", "Pad_HoleSketch")
        body.newObject.assert_any_call("PartDesign::Hole", "Pad_Hole")
        self.assertEqual(hole_sketch.AttachmentSupport, [(box, 'Face6')])
        self.assertEqual(hole_sketch.MapMode, 'FlatFace')
        # AttachmentOffset recenters local (0,0) to the face's centroid --
        # identity placement means the offset equals the centroid itself.
        self.assertEqual(hole_sketch.AttachmentOffset.Base, _Vec(5, 5, 10))
        self.assertEqual(hole.Profile, hole_sketch)
        self.assertEqual(hole.Diameter, 6)
        self.assertEqual(hole.DepthType, "Dimension")
        self.assertEqual(hole.Depth, 8)
        self.assertEqual(hole.DrillPoint, 'Flat')
        self.assertEqual(hole.HoleCutType, 'None')
        assert_success_contains(self, result, "simple", "6mm", "Face6", "Pad", "PartDesign::Hole", "Body")

    def test_creates_partdesign_hole_counterbore_in_body(self):
        box = make_box_object("Pad")
        box.Shape.Faces[5].CenterOfMass = _Vec(5, 5, 10)
        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        hole_sketch = MagicMock()
        hole_sketch.Placement = _Placement()
        hole = MagicMock()
        hole.State = []
        body.newObject.side_effect = [hole_sketch, hole]

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'face_index': 6, 'hole_type': 'counterbore',
            'diameter': 6, 'depth': 8, 'cb_diameter': 12, 'cb_depth': 3,
        })

        self.assertEqual(hole.HoleCutType, 'Counterbore')
        self.assertEqual(hole.HoleCutDiameter, 12)
        self.assertEqual(hole.HoleCutDepth, 3)
        assert_success_contains(self, result, "counterbore")

    def test_creates_partdesign_hole_countersink_derives_angle_in_body(self):
        """PartDesign::Hole parameterizes a countersink by diameter +
        included angle, not diameter + depth like the old CSG cone did --
        the angle must be derived from cb_diameter/diameter/cb_depth.
        Confirmed live: cb_diameter=12, diameter=6, cb_depth=3 -> 90
        degrees exactly."""
        box = make_box_object("Pad")
        box.Shape.Faces[5].CenterOfMass = _Vec(5, 5, 10)
        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        hole_sketch = MagicMock()
        hole_sketch.Placement = _Placement()
        hole = MagicMock()
        hole.State = []
        body.newObject.side_effect = [hole_sketch, hole]

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'face_index': 6, 'hole_type': 'countersink',
            'diameter': 6, 'depth': 8, 'cb_diameter': 12, 'cb_depth': 3,
        })

        self.assertEqual(hole.HoleCutType, 'Countersink')
        self.assertEqual(hole.HoleCutDiameter, 12)
        self.assertAlmostEqual(hole.HoleCutCountersinkAngle, 90.0)
        assert_success_contains(self, result, "countersink")

    def test_invalid_hole_type_rejected_in_body(self):
        box = make_box_object("Pad")
        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'face_index': 6, 'hole_type': 'square',
        })

        assert_error_contains(self, result, "invalid hole_type", "square")
        body.newObject.assert_not_called()

    def test_face_index_out_of_range_in_body(self):
        box = make_box_object("Pad")
        # _make_shape default: 6 faces
        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'face_index': 99, 'hole_type': 'simple',
        })

        assert_error_contains(self, result, "out of range", "6 faces")
        body.newObject.assert_not_called()

    def test_no_face_index_uses_standalone_csg_even_when_object_is_in_body(self):
        """face_index is what opts into the Body-aware path -- a Body-
        resident object with no face_index given must still get the old
        CSG cylinder-and-cut behavior, not silently switch mechanisms."""
        box = make_box_object("Pad")
        body = make_body("Body", group=[box])
        doc = make_mock_doc([body, box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.hole_wizard({
            'object_name': 'Pad', 'hole_type': 'simple', 'diameter': 6, 'depth': 10,
        })

        body.newObject.assert_not_called()
        type_ids_added = [c.args[0] for c in doc.addObject.call_args_list]
        self.assertIn("Part::Cylinder", type_ids_added)
        assert_success_contains(self, result, "simple", "6mm", "Pad")


# ---------------------------------------------------------------------------
# Patterns + Mirror
# ---------------------------------------------------------------------------

class TestLinearPattern(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_creates_count_minus_1_copies(self):
        feat = make_part_object("F")
        feat.Label = "F"
        # _Vec.add is real on the helpers, no mock needed.
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'F', 'direction': 'x',
            'count': 4, 'spacing': 10,
        })

        # 3 copies created (count=4 means original + 3 clones)
        self.assertEqual(doc.copyObject.call_count, 3)
        assert_success_contains(self, result, "4 instances", "x", "10mm")

    def test_invalid_direction_errors_instead_of_stacking_copies(self):
        """An unrecognized direction used to leave the offset vector at
        (0,0,0), silently stacking every copy exactly on the original
        (coincident geometry — the OCCT Boolean crash pathology) while
        still reporting success. Must error instead."""
        feat = make_part_object("F")
        feat.Label = "F"
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'F', 'direction': 'q',
            'count': 4, 'spacing': 10,
        })

        assert_error_contains(self, result, "invalid direction", "q")
        doc.copyObject.assert_not_called()

    def test_count_zero_rejected(self):
        feat = make_part_object("F")
        feat.Label = "F"
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'F', 'direction': 'x', 'count': 0, 'spacing': 10,
        })

        assert_error_contains(self, result, "count")
        doc.copyObject.assert_not_called()

    def test_count_one_accepted_as_boundary(self):
        """count=1 means 'just the original, no copies' — the >= 1 floor's
        accept side, not the reject side."""
        feat = make_part_object("F")
        feat.Label = "F"
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'F', 'direction': 'x', 'count': 1, 'spacing': 10,
        })

        doc.copyObject.assert_not_called()
        assert_success_contains(self, result, "1 instances")

    def test_creates_partdesign_linear_pattern_in_body(self):
        """When the feature is in a Body, linear_pattern should behave
        like mirror_feature/create_helix already do: create a genuine
        PartDesign::LinearPattern instead of copying the feature by hand.
        Confirmed live: Length is the total span from first to last
        occurrence (spacing * (count - 1)), not the per-step spacing
        value directly, and Body.Tip needed an explicit assignment
        (PartDesign::LinearPattern doesn't auto-update it)."""
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'Pad', 'direction': 'x', 'count': 4, 'spacing': 10,
        })

        body.newObject.assert_called_with("PartDesign::LinearPattern", "LinearPattern")
        doc.copyObject.assert_not_called()
        linpat = body.newObject.return_value
        self.assertEqual(linpat.Originals, [feat])
        x_axis = body.Origin.OriginFeatures[0]
        self.assertEqual(linpat.Direction, (x_axis, ['']))
        self.assertEqual(linpat.Length, 30)
        self.assertEqual(linpat.Occurrences, 4)
        self.assertEqual(body.Tip, linpat)
        assert_success_contains(self, result, "4 instances", "x", "10mm", "PartDesign::LinearPattern", "Body")

    def test_invalid_direction_rejected_in_body(self):
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'Pad', 'direction': 'q', 'count': 4, 'spacing': 10,
        })

        assert_error_contains(self, result, "invalid direction", "q")
        body.newObject.assert_not_called()

    def test_count_zero_rejected_in_body_before_creating_anything(self):
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.linear_pattern({
            'feature_name': 'Pad', 'direction': 'x', 'count': 0, 'spacing': 10,
        })

        assert_error_contains(self, result, "count")
        body.newObject.assert_not_called()


class TestPolarPattern(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_creates_count_minus_1_rotated_copies(self):
        feat = make_part_object("F")
        feat.Label = "F"
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.polar_pattern({
            'feature_name': 'F', 'axis': 'z',
            'angle': 360, 'count': 6,
        })

        self.assertEqual(doc.copyObject.call_count, 5)  # 6 - 1
        assert_success_contains(self, result, "6 instances", "Z", "360")

    def test_count_zero_rejected(self):
        """count<1 must be rejected before the `angle / count` division —
        count=0 would otherwise raise ZeroDivisionError (swallowed as a
        contextless generic error) and create no copies."""
        feat = make_part_object("F")
        feat.Label = "F"
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.polar_pattern({
            'feature_name': 'F', 'axis': 'z', 'angle': 360, 'count': 0,
        })

        self.assertIn("count", result)
        self.assertEqual(doc.copyObject.call_count, 0)

    def test_creates_partdesign_polar_pattern_in_body(self):
        """When the feature is in a Body, polar_pattern should behave
        like linear_pattern/mirror_feature/create_helix already do:
        create a genuine PartDesign::PolarPattern instead of copying the
        feature by hand. Confirmed live: volume scaled exactly by count
        for a full-circle pattern, and Body.Tip needed an explicit
        assignment (PartDesign::PolarPattern doesn't auto-update it)."""
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.polar_pattern({
            'feature_name': 'Pad', 'axis': 'z', 'angle': 360, 'count': 6,
        })

        body.newObject.assert_called_with("PartDesign::PolarPattern", "PolarPattern")
        doc.copyObject.assert_not_called()
        polpat = body.newObject.return_value
        self.assertEqual(polpat.Originals, [feat])
        z_axis = body.Origin.OriginFeatures[2]
        self.assertEqual(polpat.Axis, (z_axis, ['']))
        self.assertEqual(polpat.Angle, 360)
        self.assertEqual(polpat.Occurrences, 6)
        self.assertEqual(body.Tip, polpat)
        assert_success_contains(self, result, "6 instances", "Z", "360", "PartDesign::PolarPattern", "Body")

    def test_invalid_axis_rejected_in_body(self):
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.polar_pattern({
            'feature_name': 'Pad', 'axis': 'q', 'angle': 360, 'count': 4,
        })

        assert_error_contains(self, result, "invalid axis", "q")
        body.newObject.assert_not_called()

    def test_count_zero_rejected_in_body_before_creating_anything(self):
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.polar_pattern({
            'feature_name': 'Pad', 'axis': 'z', 'angle': 360, 'count': 0,
        })

        self.assertIn("count", result)
        body.newObject.assert_not_called()


class TestMirrorFeature(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_mirror_yz_plane(self):
        feat = make_part_object("F")
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_feature({
            'feature_name': 'F', 'plane': 'YZ', 'name': 'M',
        })

        doc.addObject.assert_called_with("Part::Mirroring", "M")
        mirror = doc.Objects[-1]
        self.assertEqual(mirror.Source, feat)
        self.assertEqual(mirror.Normal, (1, 0, 0))
        assert_success_contains(self, result, "M", "F", "YZ")

    def test_invalid_state_errors_instead_of_false_success(self):
        """Only pad_sketch/pocket checked post-recompute State before this
        fix; mirror_feature (and ~15 other feature-creating methods) could
        silently report success for a broken feature."""
        feat = make_part_object("F")
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Mirroring")

        result = self.handler.mirror_feature({'feature_name': 'F', 'plane': 'YZ'})

        assert_error_contains(self, result, "failed to compute")

    def test_invalid_plane_rejected_instead_of_silent_default(self):
        """An unrecognized plane used to leave Normal/Base entirely unset
        on an already-created Part::Mirroring while still reporting
        "Created mirror: ... across {plane} plane" as success."""
        feat = make_part_object("F")
        doc = make_mock_doc([feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_feature({'feature_name': 'F', 'plane': 'QQ'})

        assert_error_contains(self, result, "invalid plane", "qq")
        doc.addObject.assert_not_called()

    def test_creates_partdesign_mirrored_in_body(self):
        """When the feature is in a Body, mirror_feature should behave
        like its siblings revolution()/groove()/create_helix() already
        do: create a genuine PartDesign::Mirrored instead of standalone
        Part::Mirroring. Confirmed working via a live FreeCAD instance --
        volume exactly doubled and Body.Tip correctly ended up pointing
        at the new Mirrored feature (which required an explicit
        body.Tip = mirror assignment; PartDesign::Mirrored doesn't
        auto-update Tip the way Pad/Pocket/Hole/AdditiveHelix do)."""
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_feature({
            'feature_name': 'Pad', 'plane': 'YZ', 'name': 'M',
        })

        body.newObject.assert_called_with("PartDesign::Mirrored", "M")
        doc.addObject.assert_not_called()
        mirror = body.newObject.return_value
        self.assertEqual(mirror.Originals, [feat])
        yz_plane = body.Origin.OriginFeatures[5]
        self.assertEqual(mirror.MirrorPlane, (yz_plane, ['']))
        self.assertEqual(body.Tip, mirror)
        assert_success_contains(self, result, "M", "Pad", "YZ", "PartDesign::Mirrored", "Body")

    def test_invalid_plane_rejected_in_body(self):
        feat = make_part_object("Pad")
        body = make_body("Body", group=[feat])
        doc = make_mock_doc([body, feat])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_feature({'feature_name': 'Pad', 'plane': 'QQ'})

        assert_error_contains(self, result, "invalid plane", "qq")
        body.newObject.assert_not_called()
        doc.addObject.assert_not_called()


# ---------------------------------------------------------------------------
# Revolution / groove
# ---------------------------------------------------------------------------

class TestRevolution(unittest.TestCase):
    """make_sketch()'s default Placement is identity (no rotation), so its
    plane normal is global Z — meaning axis='z' is the *degenerate* choice
    for these mock sketches (revolving around the plane's own normal), while
    'x'/'y' are valid (in-plane). This mirrors a real sketch attached to
    FreeCAD's default XY_Plane, where Z genuinely is the plane's normal.
    """
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_full_revolution(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({
            'sketch_name': 'S', 'angle': 360, 'axis': 'y',
        })

        doc.addObject.assert_called_with("Part::Revolution", "Revolution")
        rev = doc.Objects[-1]
        self.assertEqual(rev.Source, sketch)
        self.assertEqual(rev.Angle, 360)
        self.assertEqual(rev.Axis, (0, 1, 0))
        self.assertIs(rev.Solid, True)
        assert_success_contains(self, result, "S", "360", "Y")

    def test_invalid_state_errors_instead_of_false_success(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Revolution")

        result = self.handler.revolution({'sketch_name': 'S', 'angle': 360, 'axis': 'x'})

        assert_error_contains(self, result, "failed to compute")

    def test_partial_revolution_around_x(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.revolution({
            'sketch_name': 'S', 'angle': 90, 'axis': 'x',
        })

        rev = doc.Objects[-1]
        self.assertEqual(rev.Angle, 90)
        self.assertEqual(rev.Axis, (1, 0, 0))

    def test_invalid_axis_rejected_instead_of_silent_default(self):
        """An unrecognized axis used to silently fall through to Z while
        the success message still echoed the requested axis string."""
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({'sketch_name': 'S', 'axis': 'q'})

        assert_error_contains(self, result, "invalid axis", "q")
        doc.addObject.assert_not_called()

    def test_axis_parallel_to_sketch_normal_rejected(self):
        """Revolving a planar profile around its own plane's normal sweeps
        zero volume by construction - confirmed empirically against a real
        FreeCAD instance (identical near-zero volume from the handler, from
        Part::Revolution with Solid explicitly set, and from raw OCCT
        Face.revolve() bypassing FreeCAD entirely). Must be rejected before
        creating anything, not silently produce a degenerate 'success'."""
        sketch = make_sketch("S")  # identity Placement -> normal is global Z
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({'sketch_name': 'S', 'axis': 'z'})

        assert_error_contains(self, result, "perpendicular")
        doc.addObject.assert_not_called()

    def test_default_axis_is_also_rejected_for_default_placement_sketch(self):
        """axis defaults to 'z' when omitted - for a sketch on the (very
        common) default/identity placement, that default is *also* the
        degenerate choice. No silent default-axis fallback here either."""
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({'sketch_name': 'S'})

        assert_error_contains(self, result, "perpendicular")
        doc.addObject.assert_not_called()

    def test_revolution_creates_partdesign_revolution_in_body(self):
        """When the sketch is in a Body, revolution() should behave like
        its siblings fillet/chamfer/thickness already do: create a real
        PartDesign::Revolution (chains onto the Body's feature tree, gains
        Midplane/Reversed/UpToFace) instead of standalone Part::Revolution.
        Confirmed working via a live FreeCAD instance before this was
        implemented - PartDesign::Revolution computes the identical correct
        volume and correctly becomes the Body's tip feature."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({
            'sketch_name': 'S', 'angle': 270, 'axis': 'x',
        })

        body.newObject.assert_called_with("PartDesign::Revolution", "Revolution")
        doc.addObject.assert_not_called()
        rev = body.newObject.return_value
        self.assertEqual(rev.Profile, sketch)
        self.assertEqual(rev.ReferenceAxis, (sketch, ['H_Axis']))
        self.assertEqual(rev.Angle, 270)
        assert_success_contains(self, result, "S", "270", "X", "PartDesign::Revolution", "Body")

    def test_revolution_n_axis_rejected_in_body_as_always_degenerate(self):
        """N_Axis is the sketch's own plane normal by construction, for
        every sketch unconditionally - unlike the standalone path's
        placement-dependent check, this needs no per-sketch computation,
        mirroring groove()'s identical rejection."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({'sketch_name': 'S', 'axis': 'z'})

        assert_error_contains(self, result, "n_axis")
        body.newObject.assert_not_called()
        doc.addObject.assert_not_called()

    def test_revolution_invalid_axis_rejected_in_body(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({'sketch_name': 'S', 'axis': 'q'})

        assert_error_contains(self, result, "invalid axis", "q")
        body.newObject.assert_not_called()
        doc.addObject.assert_not_called()


class TestGroove(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_groove_needs_body(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.groove({'sketch_name': 'S'})
        assert_error_contains(self, result, "must be in a partdesign body")

    def test_groove_creates_partdesign_groove_in_body(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.groove({
            'sketch_name': 'S', 'angle': 180, 'axis': 'x',
        })

        body.newObject.assert_called_with("PartDesign::Groove", "Groove")
        assert_success_contains(self, result, "S", "180", "X")

    def test_default_axis_is_x_not_the_always_degenerate_z(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.groove({'sketch_name': 'S', 'angle': 90})

        body.newObject.assert_called_with("PartDesign::Groove", "Groove")
        assert_success_contains(self, result, "S", "90", "X")

    def test_invalid_axis_rejected_instead_of_silent_default(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.groove({'sketch_name': 'S', 'axis': 'q'})

        assert_error_contains(self, result, "invalid axis", "q")
        body.newObject.assert_not_called()

    def test_n_axis_rejected_as_always_degenerate(self):
        """N_Axis is the sketch's own plane normal by construction, for
        every sketch unconditionally (unlike revolution()'s global axis
        choice, which depends on the sketch's actual placement) - so 'z'
        here always sweeps zero volume, with no exception, and is rejected
        outright rather than validated against placement at runtime."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.groove({'sketch_name': 'S', 'axis': 'z'})

        assert_error_contains(self, result, "n_axis")
        body.newObject.assert_not_called()


# ---------------------------------------------------------------------------
# Loft / sweep
# ---------------------------------------------------------------------------

class TestLoftProfiles(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_needs_at_least_two_sketches(self):
        doc = make_mock_doc([make_sketch("Only")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.loft_profiles({'sketches': ['Only']})
        assert_error_contains(self, result, "at least 2")

    def test_creates_part_loft_with_sections(self):
        s1, s2 = make_sketch("S1"), make_sketch("S2")
        doc = make_mock_doc([s1, s2])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.loft_profiles({
            'sketches': ['S1', 'S2'], 'name': 'L',
        })

        doc.addObject.assert_called_with("Part::Loft", "L")
        loft = doc.Objects[-1]
        self.assertEqual(list(loft.Sections), [s1, s2])
        assert_success_contains(self, result, "L", "2 profiles")


class TestSweepPath(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_missing_profile_errors(self):
        path = make_sketch("Path")
        doc = make_mock_doc([path])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.sweep_path({
            'profile_sketch': 'NoSuchProfile', 'path_sketch': 'Path',
        })
        assert_error_contains(self, result, "profile", "not found")

    def test_creates_part_sweep_with_profile_and_spine(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.sweep_path({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })

        doc.addObject.assert_called_with("Part::Sweep", "Sweep")
        sweep = doc.Objects[-1]
        self.assertEqual(list(sweep.Sections), [profile])
        self.assertEqual(sweep.Spine, path)


# ---------------------------------------------------------------------------
# additive_pipe / subtractive_loft / subtractive_sweep / create_helix /
# create_rib — previously zero test coverage (H15 finding).
# ---------------------------------------------------------------------------

class TestAdditivePipe(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_requires_body(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.additive_pipe({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })
        assert_error_contains(self, result, "partdesign body")

    def test_creates_additive_pipe_in_body(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        body = make_body("Body", group=[profile, path])
        doc = make_mock_doc([body, profile, path])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.additive_pipe({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })

        body.newObject.assert_called_with("PartDesign::AdditivePipe", "AdditivePipe")
        pipe = body.newObject.return_value
        self.assertEqual(pipe.Profile, profile)
        self.assertEqual(pipe.Spine, path)
        assert_success_contains(self, result, "P", "Path")

    def test_invalid_state_errors_instead_of_false_success(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        body = make_body("Body", group=[profile, path])
        doc = make_mock_doc([body, profile, path])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_newobject_invalid(body, "PartDesign::AdditivePipe")

        result = self.handler.additive_pipe({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })

        assert_error_contains(self, result, "failed to compute")


class TestSubtractiveLoft(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_needs_at_least_two_sketches(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.subtractive_loft({'sketches': ['S1']})
        assert_error_contains(self, result, "at least 2")

    def test_requires_body(self):
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        doc = make_mock_doc([s1, s2])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.subtractive_loft({'sketches': ['S1', 'S2']})
        assert_error_contains(self, result, "partdesign body")

    def test_creates_subtractive_loft_in_body(self):
        """PartDesign::SubtractiveLoft has its own required Profile
        property distinct from Sections (like every other PartDesign
        additive/subtractive feature) -- unlike the standalone Part::Loft
        this method used to be modeled after. Profile must get the first
        sketch and Sections only the rest; putting every sketch
        (including the first) into Sections with Profile left unset
        left the feature permanently State=Invalid with a null Shape
        (confirmed live 2026-08-21, fixed same day)."""
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        body = make_body("Body", group=[s1, s2])
        doc = make_mock_doc([body, s1, s2])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.subtractive_loft({'sketches': ['S1', 'S2']})

        body.newObject.assert_called_with("PartDesign::SubtractiveLoft", "SubtractiveLoft")
        loft = body.newObject.return_value
        self.assertEqual(loft.Profile, s1)
        self.assertEqual(list(loft.Sections), [s2])
        assert_success_contains(self, result, "2 profiles")

    def test_three_sketches_first_is_profile_rest_are_sections(self):
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        s3 = make_sketch("S3")
        body = make_body("Body", group=[s1, s2, s3])
        doc = make_mock_doc([body, s1, s2, s3])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.subtractive_loft({'sketches': ['S1', 'S2', 'S3']})

        loft = body.newObject.return_value
        self.assertEqual(loft.Profile, s1)
        self.assertEqual(list(loft.Sections), [s2, s3])

    def test_invalid_state_errors_instead_of_false_success(self):
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        body = make_body("Body", group=[s1, s2])
        doc = make_mock_doc([body, s1, s2])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_newobject_invalid(body, "PartDesign::SubtractiveLoft")

        result = self.handler.subtractive_loft({'sketches': ['S1', 'S2']})

        assert_error_contains(self, result, "failed to compute")


class TestSubtractiveSweep(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_requires_body(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.subtractive_sweep({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })
        assert_error_contains(self, result, "partdesign body")

    def test_creates_subtractive_pipe_in_body(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        body = make_body("Body", group=[profile, path])
        doc = make_mock_doc([body, profile, path])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.subtractive_sweep({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })

        body.newObject.assert_called_with("PartDesign::SubtractivePipe", "SubtractivePipe")
        pipe = body.newObject.return_value
        self.assertEqual(pipe.Profile, profile)
        self.assertEqual(pipe.Spine, path)
        assert_success_contains(self, result, "P", "Path")

    def test_invalid_state_errors_instead_of_false_success(self):
        profile = make_sketch("P")
        path = make_sketch("Path")
        body = make_body("Body", group=[profile, path])
        doc = make_mock_doc([body, profile, path])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_newobject_invalid(body, "PartDesign::SubtractivePipe")

        result = self.handler.subtractive_sweep({
            'profile_sketch': 'P', 'path_sketch': 'Path',
        })

        assert_error_contains(self, result, "failed to compute")


class TestCreateHelix(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_creates_helix_sweep_from_pitch_and_height(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({
            'sketch_name': 'S', 'pitch': 2, 'height': 10,
        })

        doc.addObject.assert_any_call("Part::Sweep", "Helix")
        assert_success_contains(self, result, "S", "pitch=2", "height=10", "turns=5.0")

    def test_turns_drives_height_when_given(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({
            'sketch_name': 'S', 'pitch': 2, 'turns': 3,
        })

        assert_success_contains(self, result, "height=6", "turns=3")

    def test_invalid_state_errors_instead_of_false_success(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Sweep")

        result = self.handler.create_helix({'sketch_name': 'S', 'pitch': 2, 'height': 10})

        assert_error_contains(self, result, "failed to compute")

    def test_creates_partdesign_additive_helix_in_body_turns_driven(self):
        """When the sketch is in a Body, create_helix should behave like
        its siblings revolution()/groove() already do: create a genuine
        PartDesign::AdditiveHelix instead of the standalone Part::Sweep
        path. Confirmed working via a live FreeCAD instance -- unlike
        Part::Helix, PartDesign::AdditiveHelix does have a LeftHanded
        property, and Mode must be set to 'pitch-turns-angle' before Turns
        takes effect (setting Turns while Mode is still the default
        'pitch-height-angle' silently no-ops)."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({
            'sketch_name': 'S', 'pitch': 5, 'turns': 3, 'axis': 'x',
        })

        body.newObject.assert_called_with("PartDesign::AdditiveHelix", "Helix")
        doc.addObject.assert_not_called()
        helix = body.newObject.return_value
        self.assertEqual(helix.Profile, sketch)
        self.assertEqual(helix.ReferenceAxis, (sketch, ['H_Axis']))
        self.assertEqual(helix.Mode, 'pitch-turns-angle')
        self.assertEqual(helix.Pitch, 5)
        self.assertEqual(helix.Turns, 3)
        assert_success_contains(self, result, "S", "X-axis", "pitch=5", "PartDesign::AdditiveHelix", "Body")

    def test_creates_partdesign_additive_helix_in_body_height_driven_left_handed(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({
            'sketch_name': 'S', 'pitch': 4, 'height': 20, 'axis': 'y', 'left_handed': True,
        })

        helix = body.newObject.return_value
        self.assertEqual(helix.ReferenceAxis, (sketch, ['V_Axis']))
        self.assertEqual(helix.Mode, 'pitch-height-angle')
        self.assertEqual(helix.Height, 20)
        self.assertTrue(helix.LeftHanded)
        assert_success_contains(self, result, "Y-axis")

    def test_n_axis_rejected_in_body_as_always_degenerate(self):
        """N_Axis (default axis) is the sketch's own plane normal by
        construction, for every sketch unconditionally -- mirroring
        revolution()/groove()'s identical rejection."""
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({'sketch_name': 'S', 'pitch': 2, 'height': 10})

        assert_error_contains(self, result, "n_axis")
        body.newObject.assert_not_called()
        doc.addObject.assert_not_called()

    def test_invalid_axis_rejected_in_body(self):
        sketch = make_sketch("S")
        body = make_body("Body", group=[sketch])
        doc = make_mock_doc([body, sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_helix({'sketch_name': 'S', 'pitch': 2, 'height': 10, 'axis': 'q'})

        assert_error_contains(self, result, "invalid axis", "q")
        body.newObject.assert_not_called()
        doc.addObject.assert_not_called()


class TestCreateRib(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_creates_rib_with_default_normal_direction(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_rib({'sketch_name': 'S', 'thickness': 5})

        doc.addObject.assert_called_with("Part::Extrusion", "Rib")
        rib = doc.Objects[-1]
        self.assertEqual(rib.Dir, (0, 1, 0))
        self.assertEqual(rib.LengthFwd, 5)
        assert_success_contains(self, result, "S", "5mm", "normal")

    def test_horizontal_direction(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.create_rib({'sketch_name': 'S', 'direction': 'horizontal'})

        rib = doc.Objects[-1]
        self.assertEqual(rib.Dir, (1, 0, 0))

    def test_invalid_state_errors_instead_of_false_success(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Extrusion")

        result = self.handler.create_rib({'sketch_name': 'S'})

        assert_error_contains(self, result, "failed to compute")


# ---------------------------------------------------------------------------
# Selection-flow ops: shell, thickness, draft
# ---------------------------------------------------------------------------

class TestShellSolid(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_default_returns_awaiting_selection(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.shell_solid({
            'object_name': 'B', 'thickness': 2,
        })
        assert_awaiting_selection(self, result)
        kwargs = self.handler.selector.request_selection.call_args.kwargs
        self.assertEqual(kwargs.get("tool_name"), "shell_solid")
        self.assertEqual(kwargs.get("selection_type"), "faces")

    def test_continue_selection_creates_thickness_with_faces(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.selector.complete_selection.return_value = {
            "selection_data": {"elements": [5]},
        }

        result = self.handler.shell_solid({
            'object_name': 'B', 'thickness': 2,
            '_continue_selection': True,
            '_operation_id': 'op_test_001',
        })

        doc.addObject.assert_called_with("Part::Thickness", "Shell")
        shell = doc.Objects[-1]
        self.assertEqual(shell.Value, 2)
        # Part::Thickness has no Source property at all (confirmed live:
        # AttributeError on assignment, unconditionally) - the base object
        # reference is carried entirely by .Faces's own (object, subelement)
        # tuple below, not a separate .Source assignment. A MagicMock
        # auto-vivifies any attribute access, so there's no meaningful way
        # to assert the code *doesn't* set .Source against this mock beyond
        # what py_compile/the live-instance verification already confirmed;
        # the real regression protection here is that .Faces below is the
        # only reference actually used.
        # Part::Thickness.Faces is a LinkSubList: (object, ("Face5",)) with the
        # 1-based FaceN name — not a raw 0-based int index.
        self.assertEqual(shell.Faces, (box, ("Face5",)))
        assert_success_contains(self, result, "2mm", "1 face")

    def test_auto_shell_closed_uses_offset_and_cut_not_thickness_source(self):
        """auto_shell_closed=True previously created a Part::Thickness and
        set .Source on it - Part::Thickness has no Source property at all
        (confirmed live: unconditional AttributeError), and separately,
        BRepOffsetAPI_MakeThickSolid (what Part::Thickness wraps) genuinely
        can't produce a zero-opening shell regardless of property names
        (confirmed live: "shape is invalid" with zero faces, any Join mode).
        The fix computes the hollow shape directly (inward offset, cut from
        the original) and wraps it in a plain Part::Feature."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.shell_solid({
            'object_name': 'B', 'thickness': 2, 'auto_shell_closed': True,
        })

        doc.addObject.assert_called_with("Part::Feature", "Shell")
        shell = doc.Objects[-1]
        box.Shape.makeOffsetShape.assert_called_once_with(-2, 1e-3, fill=False)
        box.Shape.cut.assert_called_once_with(box.Shape.makeOffsetShape.return_value)
        self.assertEqual(shell.Shape, box.Shape.cut.return_value)
        assert_success_contains(self, result, "2mm", "no opening")


class TestAddThickness(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_default_returns_awaiting_selection(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_thickness({
            'object_name': 'B', 'thickness': 1.5,
        })
        assert_awaiting_selection(self, result)


class TestDraftFaces(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_default_returns_awaiting_selection(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.draft_faces({
            'object_name': 'B', 'angle': 5,
        })
        assert_awaiting_selection(self, result)
        kwargs = self.handler.selector.request_selection.call_args.kwargs
        self.assertEqual(kwargs.get("tool_name"), "draft_faces")
        self.assertEqual(kwargs.get("angle"), 5)

    def test_object_without_faces_errors(self):
        obj = make_part_object("X")
        obj.Shape.Faces = []  # No faces
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.draft_faces({'object_name': 'X', 'angle': 5})
        assert_error_contains(self, result, "no faces")


# ---------------------------------------------------------------------------
# Datum from face
# ---------------------------------------------------------------------------

class TestDatumFromFace(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_face_index_out_of_range(self):
        box = make_box_object("B")
        # _make_shape default: 6 faces
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.datum_from_face({
            'object_name': 'B', 'face_index': 99,
        })
        assert_error_contains(self, result, "out of range", "6 faces")

    def test_object_without_shape_errors(self):
        obj = MagicMock()
        obj.Name = "X"
        obj.Label = "X"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.datum_from_face({
            'object_name': 'X', 'face_index': 1,
        })
        assert_error_contains(self, result, "no shape")

    def test_default_name_uses_face_index(self):
        """Default datum name follows Datum_FaceN pattern from the face index."""
        box = make_box_object("B")
        # Set up face[2] (index 3) with a normalAt and CenterOfMass
        face = box.Shape.Faces[2]
        face.normalAt = MagicMock(return_value=MagicMock(x=0, y=0, z=1))
        face.CenterOfMass = MagicMock(x=5, y=5, z=10)
        face.Area = 100.0

        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        # create_datum_plane is delegated to — patch it to confirm the args
        with unittest.mock.patch.object(
                self.handler, 'create_datum_plane',
                return_value="Created datum: Datum_Face3 on Face3 of B") as cdp:
            result = self.handler.datum_from_face({
                'object_name': 'B', 'face_index': 3,
            })

        cdp.assert_called_once()
        kwargs = cdp.call_args.args[0]
        self.assertEqual(kwargs['name'], 'Datum_Face3')
        self.assertEqual(kwargs['map_mode'], 'FlatFace')
        self.assertEqual(kwargs['reference'], 'Face3')
        self.assertEqual(kwargs['reference_object'], 'B')
        # Face geometry info is appended
        self.assertIn("Face centroid", result)
        self.assertIn("Face normal", result)
        self.assertIn("Face area: 100.00", result)


if __name__ == '__main__':
    unittest.main()

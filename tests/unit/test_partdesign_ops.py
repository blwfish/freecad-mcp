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
)

from handlers.partdesign_ops import PartDesignOpsHandler


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


# ---------------------------------------------------------------------------
# Revolution / groove
# ---------------------------------------------------------------------------

class TestRevolution(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartDesignOpsHandler)

    def test_full_revolution(self):
        sketch = make_sketch("S")
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolution({
            'sketch_name': 'S', 'angle': 360, 'axis': 'z',
        })

        doc.addObject.assert_called_with("Part::Revolution", "Revolution")
        rev = doc.Objects[-1]
        self.assertEqual(rev.Source, sketch)
        self.assertEqual(rev.Angle, 360)
        self.assertEqual(rev.Axis, (0, 0, 1))
        assert_success_contains(self, result, "S", "360", "Z")

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
            'sketch_name': 'S', 'angle': 180, 'axis': 'z',
        })

        body.newObject.assert_called_with("PartDesign::Groove", "Groove")
        assert_success_contains(self, result, "S", "180", "Z")


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
        self.assertEqual(shell.Source, box)
        # face_idx 5 → faces tuple has 4 (0-based)
        self.assertEqual(shell.Faces, (4,))
        assert_success_contains(self, result, "2mm", "1 face")


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

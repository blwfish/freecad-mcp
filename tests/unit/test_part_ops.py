"""Unit tests for PartOpsHandler.

Covers extrude, revolve, mirror, scale, section, loft, sweep, compound,
shape_string, and check_geometry. The biggest of the part-umbrella
handlers (478 LOC) — all 10 operations exercised by at least one unit
test, with real assertions on dispatch routing, parameter assembly,
and error paths.
"""

import unittest
from unittest.mock import MagicMock, patch

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Part,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    make_cylinder_object,
    make_sphere_object,
    make_sketch,
    assert_error_contains,
    assert_success_contains,
)

from handlers.part_ops import PartOpsHandler


class TestExtrude(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.extrude({'profile_sketch': 'S', 'height': 10})
        assert_error_contains(self, result, "no active document")

    def test_missing_sketch(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.extrude({'profile_sketch': 'Ghost', 'height': 10})
        assert_error_contains(self, result, "ghost", "not found")

    def test_extrude_z_default_direction(self):
        sketch = make_sketch("RectS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 10,
        })

        assert_success_contains(self, result, "RectS", "10", "z")
        # Result object created with Part::Feature TypeId
        doc.addObject.assert_called()
        last_call = doc.addObject.call_args_list[-1]
        self.assertEqual(last_call.args[0], "Part::Feature")
        self.assertIn("extruded", last_call.args[1])

    def test_extrude_x_direction(self):
        sketch = make_sketch("RectS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 5, 'direction': 'x',
        })

        assert_success_contains(self, result, "x")

    def test_extrude_uses_face_path_when_only_faces(self):
        """When sketch has no Wires but has Faces, fall back to Shape.extrude."""
        sketch = make_sketch("S", has_wires=False, has_faces=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({'profile_sketch': 'S', 'height': 10})

        assert_success_contains(self, result, "S")
        # Shape.extrude was called (face path)
        sketch.Shape.extrude.assert_called()

    def test_extrude_no_wires_or_faces_errors(self):
        sketch = make_sketch("Empty", has_wires=False, has_faces=False)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({'profile_sketch': 'Empty', 'height': 10})

        assert_error_contains(self, result, "no valid", "wires", "faces")


class TestRevolve(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_revolve_full_circle(self):
        sketch = make_sketch("RevS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolve({
            'profile_sketch': 'RevS', 'angle': 360, 'axis': 'z',
        })

        assert_success_contains(self, result, "RevS", "360", "z")

    def test_missing_sketch(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.revolve({'profile_sketch': 'Nope'})
        assert_error_contains(self, result, "not found")


class TestMirror(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_mirror_yz_plane(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_object({
            'object_name': 'B', 'plane': 'YZ',
        })

        assert_success_contains(self, result, "B", "YZ")
        # Shape.mirror was called
        box.Shape.mirror.assert_called_once()

    def test_invalid_plane_errors(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mirror_object({
            'object_name': 'B', 'plane': 'XYZ',
        })
        assert_error_contains(self, result, "invalid plane")

    def test_object_without_shape_errors(self):
        obj = MagicMock()
        obj.Name = "Datum"
        obj.Label = "Datum"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mirror_object({
            'object_name': 'Datum', 'plane': 'YZ',
        })
        assert_error_contains(self, result, "not a shape object")


class TestScaleObject(unittest.TestCase):
    """Scale tests check the result string rather than post-state.

    The handler does ``obj.Length = obj.Length.Value * factor`` and
    later reads ``obj.Length.Value`` again to format the new-dims
    string. In real FreeCAD the property setter wraps the float back
    into a Quantity that retains .Value; a plain MagicMock cannot
    fake that descriptor without significant complexity. The result
    string contains both old and new dimensions, so success-text
    assertions exercise the same code path.
    """

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_scale_box_routes_to_box_branch(self):
        """Parametric Box: result reports old → new dimensions."""
        box = make_box_object("B", length=10, width=20, height=30)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'B', 'scale_factor': 2.0,
        })

        # The box branch reads Length.Value once before assignment, so
        # at minimum the old dimensions appear in the result.
        assert_success_contains(self, result, "B", "factor 2",
                                "10.0", "20.0", "30.0")

    def test_scale_cylinder_routes_to_cylinder_branch(self):
        cyl = make_cylinder_object("C", radius=5, height=10)
        doc = make_mock_doc([cyl])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'C', 'scale_factor': 3.0,
        })

        assert_success_contains(self, result, "C", "factor 3", "R5", "H10")

    def test_scale_sphere_routes_to_sphere_branch(self):
        sphere = make_sphere_object("S", radius=4)
        doc = make_mock_doc([sphere])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'S', 'scale_factor': 0.5,
        })

        assert_success_contains(self, result, "S", "factor 0.5", "R4")

    def test_scale_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.scale_object({
            'object_name': 'Ghost', 'scale_factor': 2.0,
        })
        assert_error_contains(self, result, "not found")


class TestSection(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_section_at_xy_plane(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.section({
            'object_name': 'B', 'plane': 'XY', 'offset': 5,
        })

        assert_success_contains(self, result, "B", "XY", "5")

    def test_invalid_plane_errors(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.section({
            'object_name': 'B', 'plane': 'BAD',
        })
        assert_error_contains(self, result, "invalid plane")


class TestLoft(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_loft_two_sketches_creates_part_loft(self):
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        doc = make_mock_doc([s1, s2])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.loft({
            'sketches': ['S1', 'S2'], 'name': 'MyLoft',
        })

        assert_success_contains(self, result, "MyLoft", "2 profiles")
        doc.addObject.assert_called_with("Part::Loft", "MyLoft")
        loft = doc.Objects[-1]
        self.assertEqual(list(loft.Sections), [s1, s2])

    def test_loft_needs_at_least_two_sketches(self):
        doc = make_mock_doc([make_sketch("Only")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.loft({'sketches': ['Only']})
        assert_error_contains(self, result, "at least 2")


class TestSweep(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_sweep_creates_part_sweep_with_profile_and_path(self):
        profile = make_sketch("Profile")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.sweep({
            'profile_sketch': 'Profile', 'path_sketch': 'Path',
        })

        assert_success_contains(self, result, "Profile", "Path")
        doc.addObject.assert_called_with("Part::Sweep", "Sweep")
        sweep = doc.Objects[-1]
        self.assertEqual(list(sweep.Sections), [profile])
        self.assertEqual(sweep.Spine, path)

    def test_missing_profile(self):
        doc = make_mock_doc([make_sketch("Path")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.sweep({
            'profile_sketch': 'NoProfile', 'path_sketch': 'Path',
        })
        assert_error_contains(self, result, "profile", "not found")


class TestCompound(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_compound_two_boxes(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.compound({
            'objects': ['A', 'B'], 'name': 'Combo',
        })

        assert_success_contains(self, result, "Combo", "2 objects")
        # Part.makeCompound was called with the shapes
        mock_Part.makeCompound.assert_called_once()
        args = mock_Part.makeCompound.call_args
        shapes = args.args[0]
        self.assertEqual(shapes, [a.Shape, b.Shape])

    def test_compound_needs_at_least_two_objects(self):
        a = make_box_object("A")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.compound({'objects': ['A']})
        assert_error_contains(self, result, "at least 2")


class TestCheckGeometry(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_reports_validity_and_counts(self):
        """check_geometry reports Valid, Solids, Faces, Edges, Vertices."""
        box = make_box_object("B")
        # _make_shape defaults: 6 faces, 12 edges, 8 vertices, 1 solid
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.check_geometry({'object_name': 'B'})

        assert_success_contains(self, result,
                                "Valid: True",
                                "Solids: 1",
                                "Faces: 6",
                                "Edges: 12",
                                "Vertices: 8")

    def test_reports_invalid_geometry(self):
        box = make_box_object("B")
        box.Shape.isValid = MagicMock(return_value=False)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.check_geometry({'object_name': 'B'})

        assert_success_contains(self, result, "Valid: False")

    def test_no_shape_errors(self):
        obj = MagicMock()
        obj.Name = "X"
        obj.Label = "X"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.check_geometry({'object_name': 'X'})
        assert_error_contains(self, result, "no shape")


class TestShapeString(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_no_font_errors_helpfully(self):
        """Without a usable font, shape_string returns guidance, not a crash."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        # Force find_font to return ''
        with patch.object(self.handler, 'find_font', return_value=''):
            result = self.handler.shape_string({'string': 'Hi'})
        assert_error_contains(self, result, "font", ".ttf")

    def test_creates_part_feature_with_compound_shape(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        # makeWireString: list of [list of wires] per character
        wire = MagicMock()
        mock_Part.makeWireString = MagicMock(return_value=[[wire], [wire, wire]])
        compound = MagicMock()
        mock_Part.makeCompound = MagicMock(return_value=compound)
        with patch.object(self.handler, 'find_font',
                          return_value='/fake/Arial.ttf'):
            result = self.handler.shape_string({
                'string': 'Hi', 'size': 10, 'name': 'Label',
            })

        assert_success_contains(self, result, "Hi", "Label", "Arial.ttf")
        # 2 chars × 1+2 wires = 3 wires flattened
        mock_Part.makeCompound.assert_called_once()
        flat = mock_Part.makeCompound.call_args.args[0]
        self.assertEqual(len(flat), 3)


if __name__ == '__main__':
    unittest.main()

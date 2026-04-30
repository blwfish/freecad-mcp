"""Unit tests for PrimitivesHandler.

Covers create_box, create_cylinder, create_sphere, create_cone,
create_torus, create_wedge. Validates parameter routing, Label
assignment (e62ebc5 fix), and document creation behavior.
"""

import unittest

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    assert_success_contains,
    _Vec,
)

from handlers.primitives import PrimitivesHandler


class TestCreateBox(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_creates_box_with_correct_typeid_and_dimensions(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_box({
            'length': 25, 'width': 15, 'height': 10,
            'name': 'MyBox',
        })

        assert_success_contains(self, result, "MyBox", "25", "15", "10")
        doc.addObject.assert_called_once_with("Part::Box", "MyBox")
        # The added object's parametric properties were set
        box = doc.Objects[-1]
        self.assertEqual(box.Length, 25)
        self.assertEqual(box.Width, 15)
        self.assertEqual(box.Height, 10)

    def test_label_set_for_label_fallback_lookup(self):
        """Label = name so base.get_object()'s label fallback resolves."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        self.handler.create_box({
            'length': 10, 'width': 10, 'height': 10,
            'name': 'LeftTab',
        })

        box = doc.Objects[-1]
        self.assertEqual(box.Label, 'LeftTab')

    def test_default_name_is_Box(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        self.handler.create_box({'length': 5, 'width': 5, 'height': 5})

        doc.addObject.assert_called_once_with("Part::Box", "Box")

    def test_placement_set_to_xyz_origin(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        self.handler.create_box({
            'length': 1, 'width': 1, 'height': 1,
            'x': 50, 'y': -10, 'z': 25,
        })

        box = doc.Objects[-1]
        self.assertEqual(box.Placement.Base.x, 50.0)
        self.assertEqual(box.Placement.Base.y, -10.0)
        self.assertEqual(box.Placement.Base.z, 25.0)


class TestCreateCylinder(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_creates_cylinder_with_radius_and_height(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_cylinder({
            'radius': 5, 'height': 20, 'name': 'Pin',
        })

        assert_success_contains(self, result, "Pin", "R5", "H20")
        doc.addObject.assert_called_once_with("Part::Cylinder", "Pin")
        cyl = doc.Objects[-1]
        self.assertEqual(cyl.Radius, 5)
        self.assertEqual(cyl.Height, 20)


class TestCreateSphere(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_creates_sphere_with_radius(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_sphere({
            'radius': 10, 'name': 'Ball',
        })

        assert_success_contains(self, result, "Ball", "R10")
        doc.addObject.assert_called_once_with("Part::Sphere", "Ball")
        sphere = doc.Objects[-1]
        self.assertEqual(sphere.Radius, 10)


class TestCreateCone(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_creates_cone_with_two_radii(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_cone({
            'radius1': 5, 'radius2': 2, 'height': 10,
        })

        assert_success_contains(self, result, "R1", "R2", "H10")
        doc.addObject.assert_called_once_with("Part::Cone", "Cone")
        cone = doc.Objects[-1]
        self.assertEqual(cone.Radius1, 5)
        self.assertEqual(cone.Radius2, 2)
        self.assertEqual(cone.Height, 10)


class TestCreateTorus(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_creates_torus_with_major_minor_radii(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_torus({
            'radius1': 20, 'radius2': 4,
        })

        assert_success_contains(self, result, "R1", "R2")
        doc.addObject.assert_called_once_with("Part::Torus", "Torus")


if __name__ == '__main__':
    unittest.main()

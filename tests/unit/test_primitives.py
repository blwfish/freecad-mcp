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


# ---------------------------------------------------------------------------
# Degenerate-dimension tests
#
# All existing primitives tests use round positive numbers
# (length=25/15/10, radius=5/10).  None cover what happens when the
# user (or an LLM-generated tool call) passes zero, negative, or
# missing dimensions.  The handlers don't validate — they pass the
# values straight through to Part::Box / Part::Cylinder / etc.  That
# means:
#
#   - create_box(length=0, width=0, height=0) returns "Created box:
#     ...(0x0x0mm)" with no error.  Downstream OCCT will produce a
#     null shape that crashes any subsequent Boolean operation.
#   - create_cylinder(radius=-5) propagates a negative radius into a
#     Part::Cylinder, which FreeCAD silently clamps or produces an
#     invalid shape from.
#   - Missing 'length' falls back to default=10.  An LLM that mis-spells
#     the key gets a 10mm default it didn't ask for, with no warning.
#
# These tests pin the current behavior (no validation, returns success
# string) so a future change to add validation is intentional, not
# accidental.  They also serve as the "what does the consumer get to
# work with?" check — the response string is the *only* signal the
# MCP client receives about what was created.
# ---------------------------------------------------------------------------

class TestCreateBoxDegenerateDimensions(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_zero_dimensions_passed_through(self):
        """length=0/width=0/height=0 produces a zero-volume Part::Box
        with no error.  Downstream OCCT Booleans will fail; the caller
        gets a success string with "0x0x0mm" as the only signal."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_box({
            'length': 0, 'width': 0, 'height': 0,
        })

        # No validation — the response says "Created" not "Error".
        self.assertIn("Created box", result)
        self.assertIn("0x0x0", result)
        box = doc.Objects[-1]
        self.assertEqual(box.Length, 0)
        self.assertEqual(box.Width, 0)
        self.assertEqual(box.Height, 0)

    def test_negative_dimension_passed_through(self):
        """Negative length is propagated to Part::Box.  FreeCAD will
        either clamp or produce a degenerate shape — either way, the
        handler's return value reflects what the caller asked for, so
        a careful caller can detect the issue from the response string."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_box({
            'length': -10, 'width': 5, 'height': 5,
        })

        self.assertIn("Created box", result)
        self.assertIn("-10", result)
        self.assertEqual(doc.Objects[-1].Length, -10)

    def test_missing_dimension_uses_default(self):
        """Missing 'length' defaults to 10mm.  An LLM that omits a key
        (or mis-spells it as 'lenght') silently gets a default — the
        response string is the only place this is observable."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_box({'width': 5, 'height': 5})

        # Default length=10 from the handler
        self.assertEqual(doc.Objects[-1].Length, 10)
        # The response must echo the default value so the caller can
        # see what was actually used.
        self.assertIn("10x5x5", result)


class TestCreateCylinderDegenerateDimensions(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_zero_radius_passed_through(self):
        """radius=0 yields a degenerate cylinder.  No error from the
        handler."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_cylinder({'radius': 0, 'height': 10})

        self.assertIn("Created cylinder", result)
        self.assertEqual(doc.Objects[-1].Radius, 0)

    def test_zero_height_passed_through(self):
        """height=0 yields a flat disk (or null shape, depending on
        FreeCAD version).  Handler returns success."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_cylinder({'radius': 5, 'height': 0})

        self.assertIn("Created cylinder", result)
        self.assertEqual(doc.Objects[-1].Height, 0)


class TestCreateConeDegenerateDimensions(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)

    def test_equal_radii_makes_a_cylinder(self):
        """radius1 == radius2 is a valid degenerate case (it's a
        cylinder, not a cone).  Handler accepts it without complaint."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_cone({
            'radius1': 5, 'radius2': 5, 'height': 10,
        })

        self.assertIn("Created cone", result)
        cone = doc.Objects[-1]
        self.assertEqual(cone.Radius1, 5)
        self.assertEqual(cone.Radius2, 5)

    def test_radius2_default_is_zero(self):
        """The default radius2 is 0 — a pointed cone.  Catches the
        case where a refactor changes the default to something else
        and silently shifts the geometry of every cone call that
        relied on the default."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        self.handler.create_cone({'radius1': 5, 'height': 10})

        self.assertEqual(doc.Objects[-1].Radius2, 0)


class TestPrimitivesNoDocument(unittest.TestCase):
    """Primitives called with no active document must return an error string,
    not crash (issue #16: FreeCAD.newDocument() on wrong thread kills FreeCAD).
    """

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PrimitivesHandler)
        mock_FreeCAD.ActiveDocument = None

    def test_box_no_document_returns_error(self):
        result = self.handler.create_box({'length': 10, 'width': 10, 'height': 10})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_cylinder_no_document_returns_error(self):
        result = self.handler.create_cylinder({'radius': 5, 'height': 10})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_sphere_no_document_returns_error(self):
        result = self.handler.create_sphere({'radius': 5})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_cone_no_document_returns_error(self):
        result = self.handler.create_cone({'radius1': 5, 'height': 10})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_torus_no_document_returns_error(self):
        result = self.handler.create_torus({'radius1': 10, 'radius2': 3})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_wedge_no_document_returns_error(self):
        result = self.handler.create_wedge({})
        self.assertIn("Error", result)
        self.assertIn("create_document", result)

    def test_no_document_does_not_call_newDocument(self):
        """newDocument() must never be called from a primitive handler."""
        self.handler.create_box({'length': 10, 'width': 10, 'height': 10})
        mock_FreeCAD.newDocument.assert_not_called()


if __name__ == '__main__':
    unittest.main()

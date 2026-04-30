"""Unit tests for MeasurementOpsHandler.

Focus: list_faces, get_volume, get_surface_area, get_center_of_mass,
count_elements, measure_distance, get_mass_properties. The list_faces
op is the load-bearing one — PartDesign tests rely on its face indices
and normals being correct.

Known bug not fixed here: get_bounding_box is defined twice in
measurement_ops.py — the second definition (line 232) shadows the
first and actually implements check_solid. Tests here document the
shadowing rather than the lost operation. Flag this for follow-up.

Run with: python3 -m pytest tests/unit/test_measurement_ops.py -v
"""

import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    assert_error_contains,
    assert_success_contains,
)

from handlers.measurement_ops import MeasurementOpsHandler


def _attach_face_geometry(obj, faces_data):
    """Replace obj.Shape.Faces with mocks carrying real numeric attributes.

    faces_data: list of dicts {area, normal: (nx,ny,nz), centroid: (cx,cy,cz)}.
    """
    faces = []
    for f in faces_data:
        face = MagicMock()
        face.Area = f['area']
        nx, ny, nz = f['normal']
        face.normalAt = MagicMock(return_value=MagicMock(x=nx, y=ny, z=nz))
        cx, cy, cz = f['centroid']
        face.CenterOfMass = MagicMock(x=cx, y=cy, z=cz)
        faces.append(face)
    obj.Shape.Faces = faces


def _attach_box_face_geometry(obj, length=10.0, width=10.0, height=10.0):
    """Add 6 box faces with axis-aligned normals and predictable areas."""
    _attach_face_geometry(obj, [
        # +X face
        {'area': width * height, 'normal': (1, 0, 0),
         'centroid': (length, width / 2, height / 2)},
        # -X face
        {'area': width * height, 'normal': (-1, 0, 0),
         'centroid': (0, width / 2, height / 2)},
        # +Y face
        {'area': length * height, 'normal': (0, 1, 0),
         'centroid': (length / 2, width, height / 2)},
        # -Y face
        {'area': length * height, 'normal': (0, -1, 0),
         'centroid': (length / 2, 0, height / 2)},
        # +Z face
        {'area': length * width, 'normal': (0, 0, 1),
         'centroid': (length / 2, width / 2, height)},
        # -Z face
        {'area': length * width, 'normal': (0, 0, -1),
         'centroid': (length / 2, width / 2, 0)},
    ])


class TestListFaces(unittest.TestCase):
    """list_faces is critical infrastructure — PartDesign tests rely on it."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_faces({'object_name': 'Ghost'})
        assert_error_contains(self, result, "not found", "Ghost")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.list_faces({'object_name': 'Box'})
        assert_error_contains(self, result, "no active document")

    def test_object_without_shape(self):
        obj = MagicMock()
        obj.Name = "Datum"
        obj.Label = "Datum"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_faces({'object_name': 'Datum'})
        assert_error_contains(self, result, "shape")

    def test_lists_all_box_faces_with_normals_and_areas(self):
        """A 10x10x10 box reports 6 faces with axis-aligned normals."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_faces({'object_name': 'Box1'})

        assert_success_contains(self, result,
                                "6 total",
                                "Face1:", "Face2:", "Face3:",
                                "Face4:", "Face5:", "Face6:")
        # Each face line should have a normal vector printed
        self.assertIn("normal=", result)
        self.assertIn("centroid=", result)
        self.assertIn("area=", result)
        # Areas of all 6 faces of a 10x10x10 box are each 100 mm²
        self.assertIn("100.00", result)

    def test_face_normals_are_axis_aligned_for_box(self):
        """Each box face normal should be ±1 along one axis."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_faces({'object_name': 'Box1'})

        # The format string in the handler uses '+.2f' for components,
        # so each normal prints as e.g. "(+1.00, +0.00, +0.00)".
        # All 6 axis-aligned normals must appear in the output.
        for component in ("(+1.00, +0.00, +0.00)",
                          "(-1.00, +0.00, +0.00)",
                          "(+0.00, +1.00, +0.00)",
                          "(+0.00, -1.00, +0.00)",
                          "(+0.00, +0.00, +1.00)",
                          "(+0.00, +0.00, -1.00)"):
            self.assertIn(component, result,
                          f"Missing normal {component} from list_faces output")

    def test_face_index_is_1_based(self):
        """Handler emits Face1..FaceN, not Face0..FaceN-1.

        PartDesign's `datum_from_face` / face references in FreeCAD use
        1-based indexing — list_faces must agree."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_faces({'object_name': 'Box1'})

        self.assertIn("Face1:", result)
        self.assertIn("Face6:", result)
        self.assertNotIn("Face0:", result)

    def test_object_lookup_falls_back_to_label(self):
        """list_faces must use base.get_object()'s label fallback."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box)
        box.Label = "MyBox"
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        # Pass the Label, not the Name — fallback path
        result = self.handler.list_faces({'object_name': 'MyBox'})

        assert_success_contains(self, result, "6 total")

    def test_per_face_error_does_not_abort_listing(self):
        """If one face's normalAt() raises, the others should still print."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        # Sabotage face[2] — normalAt raises
        box.Shape.Faces[2].normalAt = MagicMock(
            side_effect=RuntimeError("synthetic"))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_faces({'object_name': 'Box1'})

        # Face3 should report the error inline
        self.assertIn("Face3: error reading face", result)
        # Other faces still listed
        self.assertIn("Face1:", result)
        self.assertIn("Face6:", result)


class TestGetVolume(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_volume({'object_name': 'Ghost'})
        assert_error_contains(self, result, "not found")

    def test_volume_reported_with_units(self):
        obj = make_part_object("Cube", volume=1000.0)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_volume({'object_name': 'Cube'})
        assert_success_contains(self, result, "1000", "mm")


class TestCountElements(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_box_counts(self):
        """A box-shaped mock reports 6 faces, 12 edges, 8 vertices."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        # Defaults from _make_shape: 6 faces, 12 edges, 8 vertices, 1 solid
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.count_elements({'object_name': 'Box1'})
        assert_success_contains(self, result,
                                "Faces: 6", "Edges: 12", "Vertices: 8",
                                "Solids: 1")


class TestMeasureDistance(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_missing_first_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.measure_distance({'object1': 'A', 'object2': 'B'})
        assert_error_contains(self, result, "not found", "A")

    def test_distance_between_two_boxes(self):
        a = make_part_object("A")
        a.Shape.CenterOfMass = MagicMock()
        a.Shape.CenterOfMass.distanceToPoint = MagicMock(return_value=42.5)
        b = make_part_object("B")
        b.Shape.CenterOfMass = MagicMock()
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.measure_distance({'object1': 'A', 'object2': 'B'})
        assert_success_contains(self, result, "42.5", "mm")


class TestGetSurfaceArea(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_box_surface_area_summed_over_faces(self):
        """Box surface area = sum of face areas."""
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_surface_area({'object_name': 'Box1'})
        # 6 faces × 100mm² = 600mm²
        assert_success_contains(self, result, "600", "mm")


class TestGetCenterOfMass(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_center_of_mass_coordinates_in_output(self):
        obj = make_part_object("X")
        obj.Shape.CenterOfMass = MagicMock(x=5.0, y=10.0, z=15.0)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_center_of_mass({'object_name': 'X'})
        assert_success_contains(self, result, "5.00", "10.00", "15.00")


class TestGetMassProperties(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(MeasurementOpsHandler)

    def test_volume_area_and_com_all_reported(self):
        box = make_box_object("Box1", 10.0, 10.0, 10.0)
        _attach_box_face_geometry(box, 10.0, 10.0, 10.0)
        box.Shape.CenterOfMass = MagicMock(x=5.0, y=5.0, z=5.0)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_mass_properties({'object_name': 'Box1'})
        # Volume from default _make_shape, area from sum of faces, COM from above
        assert_success_contains(self, result,
                                "Volume:", "Surface Area:", "Center of Mass:",
                                "600", "5.00")


if __name__ == '__main__':
    unittest.main()

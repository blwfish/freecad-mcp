"""Unit tests for SpatialOpsHandler.

Tests all 6 spatial query operations with mocked FreeCAD modules.
Run with: python3 -m pytest tests/unit/test_spatial_ops.py -v
"""

import os
import sys
import math
import types as py_types
import unittest
import pytest
from unittest.mock import Mock, MagicMock, patch

# ---------------------------------------------------------------------------
# FakeVector — needed because the handler does real math on Vector fields
# ---------------------------------------------------------------------------

class FakeVector:
    def __init__(self, x=0, y=0, z=0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
    def __repr__(self):
        return f"Vector({self.x}, {self.y}, {self.z})"


# ---------------------------------------------------------------------------
# Setup: ensure FreeCAD mock has what we need before importing handler
# ---------------------------------------------------------------------------

if 'FreeCAD' not in sys.modules:
    _fc_mod = MagicMock()
    _fc_mod.GuiUp = False
    _fc_mod.Console = MagicMock()
    sys.modules['FreeCAD'] = _fc_mod
    sys.modules['FreeCADGui'] = MagicMock()
    sys.modules['Part'] = MagicMock()

sys.modules['FreeCAD'].Vector = FakeVector

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

# Import the module itself so we can patch its FreeCAD reference
import handlers.spatial_ops as spatial_ops_module
from handlers.spatial_ops import SpatialOpsHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_handler():
    server = MagicMock()
    return SpatialOpsHandler(server, MagicMock(), MagicMock(return_value={}))


def make_mock_doc(objects=None):
    doc = MagicMock()
    doc.Name = "TestDoc"
    doc.Objects = objects or []

    def get_object(name):
        for o in doc.Objects:
            if o.Name == name:
                return o
        return None

    def get_objects_by_label(label):
        return [o for o in doc.Objects if o.Label == label]

    doc.getObject = get_object
    doc.getObjectsByLabel = get_objects_by_label
    return doc


def make_box(name="Box", xmin=0, ymin=0, zmin=0, xlen=10, ylen=10, zlen=10):
    """Create a mock Part object with Shape, BoundBox, and faces."""
    obj = MagicMock()
    obj.Name = name
    obj.Label = name

    shape = MagicMock()
    bb = MagicMock()
    bb.XMin = float(xmin); bb.XMax = float(xmin + xlen)
    bb.YMin = float(ymin); bb.YMax = float(ymin + ylen)
    bb.ZMin = float(zmin); bb.ZMax = float(zmin + zlen)
    bb.XLength = float(xlen); bb.YLength = float(ylen); bb.ZLength = float(zlen)
    shape.BoundBox = bb
    shape.Volume = float(xlen * ylen * zlen)
    shape.CenterOfMass = FakeVector(xmin + xlen/2, ymin + ylen/2, zmin + zlen/2)

    # Default: no intersection
    common_shape = MagicMock()
    common_shape.Volume = 0.0
    common_shape.BoundBox = MagicMock(XMin=0.0, XMax=0.0, YMin=0.0, YMax=0.0,
                                       ZMin=0.0, ZMax=0.0,
                                       XLength=0.0, YLength=0.0, ZLength=0.0)
    shape.common = MagicMock(return_value=common_shape)

    # Default: some distance
    shape.distToShape = MagicMock(return_value=(5.0, [(FakeVector(10, 5, 5), FakeVector(15, 5, 5))]))

    # Faces: 6 faces for a box, with normals
    faces = []
    face_defs = [
        (FakeVector(-1, 0, 0), FakeVector(xmin, ymin + ylen/2, zmin + zlen/2), float(xlen * zlen)),
        (FakeVector(1, 0, 0), FakeVector(xmin + xlen, ymin + ylen/2, zmin + zlen/2), float(ylen * zlen)),
        (FakeVector(0, -1, 0), FakeVector(xmin + xlen/2, ymin, zmin + zlen/2), float(xlen * zlen)),
        (FakeVector(0, 1, 0), FakeVector(xmin + xlen/2, ymin + ylen, zmin + zlen/2), float(xlen * zlen)),
        (FakeVector(0, 0, -1), FakeVector(xmin + xlen/2, ymin + ylen/2, zmin), float(xlen * ylen)),
        (FakeVector(0, 0, 1), FakeVector(xmin + xlen/2, ymin + ylen/2, zmin + zlen), float(xlen * ylen)),
    ]
    for normal, center, area in face_defs:
        face = MagicMock()
        face.normalAt = MagicMock(return_value=normal)
        face.CenterOfMass = center
        face.Area = area
        face.section = MagicMock(return_value=MagicMock(Edges=[], Wires=[]))
        faces.append(face)
    shape.Faces = faces
    shape.Solids = [MagicMock()]   # solid by default
    shape.ShapeType = "Solid"

    obj.Shape = shape
    return obj


def make_shell(name="Shell", xmin=0, ymin=0, zmin=0, xlen=10, ylen=10, zlen=10):
    """Like make_box but with no solids — simulates an open shell."""
    obj = make_box(name, xmin, ymin, zmin, xlen, ylen, zlen)
    obj.Shape.Solids = []
    obj.Shape.ShapeType = "Shell"
    return obj


def _make_fc_mock():
    """Create a fresh FreeCAD mock with Vector support."""
    fc = MagicMock()
    fc.GuiUp = False
    fc.Console = MagicMock()
    fc.Vector = FakeVector
    fc.ActiveDocument = None
    return fc


@pytest.fixture(autouse=True)
def _patch_fc():
    """Patch the handler module's FreeCAD reference so our mocks take effect."""
    fc = _make_fc_mock()
    with patch.object(spatial_ops_module, 'FreeCAD', fc):
        # Also patch the base module's FreeCAD (used by get_document, get_object)
        import handlers.base as base_module
        with patch.object(base_module, 'FreeCAD', fc):
            yield fc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInterferenceCheck(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_missing_objects(self):
        self.fc.ActiveDocument = make_mock_doc()
        result = self.handler.interference_check({'object1': 'A'})
        self.assertIn("required", result)

    def test_object_not_found(self):
        self.fc.ActiveDocument = make_mock_doc([])
        result = self.handler.interference_check({'object1': 'A', 'object2': 'B'})
        self.assertIn("not found", result)

    def test_no_intersection(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 20, 0, 0)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.interference_check({'object1': 'A', 'object2': 'B'})
        self.assertIn("Intersects: False", result)
        self.assertIn("clearance", result.lower())

    def test_with_intersection(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 5, 0, 0)

        common = MagicMock()
        common.Volume = 500.0
        common.BoundBox = MagicMock(XMin=5.0, XMax=10.0, YMin=0.0, YMax=10.0,
                                     ZMin=0.0, ZMax=10.0,
                                     XLength=5.0, YLength=10.0, ZLength=10.0)
        box1.Shape.common = MagicMock(return_value=common)

        self.fc.ActiveDocument = make_mock_doc([box1, box2])
        result = self.handler.interference_check({'object1': 'A', 'object2': 'B'})
        self.assertIn("Intersects: True", result)
        self.assertIn("500.0000", result)
        self.assertIn("Intersection size", result)

    def test_no_shape(self):
        obj = MagicMock()
        obj.Name = "NoShape"
        obj.Label = "NoShape"
        del obj.Shape
        box = make_box("B")
        self.fc.ActiveDocument = make_mock_doc([obj, box])
        result = self.handler.interference_check({'object1': 'NoShape', 'object2': 'B'})
        self.assertIn("no Shape", result)

    def test_non_solid_warning(self):
        shell = make_shell("A")
        box = make_box("B")
        self.fc.ActiveDocument = make_mock_doc([shell, box])
        result = self.handler.interference_check({'object1': 'A', 'object2': 'B'})
        self.assertIn("WARNING", result)
        self.assertIn("Shell", result)

    def test_solid_no_warning(self):
        box1 = make_box("A")
        box2 = make_box("B")
        self.fc.ActiveDocument = make_mock_doc([box1, box2])
        result = self.handler.interference_check({'object1': 'A', 'object2': 'B'})
        self.assertNotIn("WARNING", result)


class TestClearance(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_gap_reported(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 20, 0, 0)
        box1.Shape.distToShape = MagicMock(return_value=(
            10.0,
            [(FakeVector(10, 5, 5), FakeVector(20, 5, 5))]
        ))
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.clearance({'object1': 'A', 'object2': 'B'})
        self.assertIn("10.0000 mm", result)
        self.assertIn("10.0000 mm gap", result)
        self.assertIn("Dominant gap axis: X", result)

    def test_touching(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 10, 0, 0)
        box1.Shape.distToShape = MagicMock(return_value=(
            0.0,
            [(FakeVector(10, 5, 5), FakeVector(10, 5, 5))]
        ))
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.clearance({'object1': 'A', 'object2': 'B'})
        self.assertIn("TOUCHING", result)

    def test_multiple_point_pairs(self):
        box1 = make_box("A")
        box2 = make_box("B", 15, 0, 0)
        box1.Shape.distToShape = MagicMock(return_value=(
            5.0,
            [
                (FakeVector(10, 0, 0), FakeVector(15, 0, 0)),
                (FakeVector(10, 0, 10), FakeVector(15, 0, 10)),
                (FakeVector(10, 10, 0), FakeVector(15, 10, 0)),
                (FakeVector(10, 10, 10), FakeVector(15, 10, 10)),
                (FakeVector(10, 5, 5), FakeVector(15, 5, 5)),
            ]
        ))
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.clearance({'object1': 'A', 'object2': 'B'})
        self.assertIn("5 total", result)
        self.assertIn("... and 1 more", result)


class TestContainment(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_fully_contained(self):
        outer = make_box("Outer", 0, 0, 0, 50, 50, 50)
        inner = make_box("Inner", 10, 10, 10, 10, 10, 10)

        common = MagicMock()
        common.Volume = 1000.0
        inner.Shape.common = MagicMock(return_value=common)

        self.fc.ActiveDocument = make_mock_doc([inner, outer])
        result = self.handler.containment({'object1': 'Inner', 'object2': 'Outer'})
        self.assertIn("Bounding box contained: True", result)
        self.assertIn("Geometric containment: True", result)
        self.assertIn("No bounding-box overhang", result)

    def test_overhang(self):
        outer = make_box("Outer", 0, 0, 0, 20, 20, 20)
        inner = make_box("Inner", -5, 0, 0, 30, 10, 10)

        self.fc.ActiveDocument = make_mock_doc([inner, outer])
        result = self.handler.containment({'object1': 'Inner', 'object2': 'Outer'})
        self.assertIn("Bounding box contained: False", result)
        self.assertIn("X-: 5.0000", result)
        self.assertIn("X+: 5.0000", result)

    def test_bb_contained_but_geometry_protrudes(self):
        outer = make_box("Outer", 0, 0, 0, 50, 50, 50)
        inner = make_box("Inner", 10, 10, 10, 10, 10, 10)

        common = MagicMock()
        common.Volume = 800.0
        inner.Shape.common = MagicMock(return_value=common)

        self.fc.ActiveDocument = make_mock_doc([inner, outer])
        result = self.handler.containment({'object1': 'Inner', 'object2': 'Outer'})
        self.assertIn("Bounding box contained: True", result)
        self.assertIn("Geometric containment: False", result)
        self.assertIn("Protruding volume: 200.0000", result)


class TestFaceRelationship(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_missing_face_args(self):
        box1 = make_box("A")
        box2 = make_box("B", 15, 0, 0)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])
        result = self.handler.face_relationship({'object1': 'A', 'object2': 'B'})
        self.assertIn("face1 and face2 are required", result)

    def test_parallel_faces(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 15, 0, 0, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.face_relationship({
            'object1': 'A', 'object2': 'B',
            'face1': 'Face2', 'face2': 'Face1'
        })
        self.assertIn("Parallel: True", result)
        self.assertIn("Facing each other: True", result)

    def test_perpendicular_faces(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 15, 0, 0, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.face_relationship({
            'object1': 'A', 'object2': 'B',
            'face1': 'Face2', 'face2': 'Face6'
        })
        self.assertIn("90.00", result)
        self.assertIn("Parallel: False", result)

    def test_invalid_face_index(self):
        box1 = make_box("A")
        box2 = make_box("B", 15, 0, 0)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.face_relationship({
            'object1': 'A', 'object2': 'B',
            'face1': 'Face99', 'face2': 'Face1'
        })
        self.assertIn("Invalid face reference", result)

    def test_coplanar_faces(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 15, 0, 0, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.face_relationship({
            'object1': 'A', 'object2': 'B',
            'face1': 'Face6', 'face2': 'Face6'
        })
        self.assertIn("Parallel: True", result)
        self.assertIn("Coplanar: True", result)


class TestBatchInterference(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_fewer_than_two_objects(self):
        result = self.handler.batch_interference({'objects': ['A']})
        self.assertIn("at least 2", result)

    def test_object_not_found(self):
        self.fc.ActiveDocument = make_mock_doc([])
        result = self.handler.batch_interference({'objects': ['A', 'B']})
        self.assertIn("not found", result)

    def test_no_collisions(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 20, 0, 0)
        box3 = make_box("C", 40, 0, 0)

        box1.Shape.BoundBox.intersect = MagicMock(return_value=False)
        box2.Shape.BoundBox.intersect = MagicMock(return_value=False)
        box3.Shape.BoundBox.intersect = MagicMock(return_value=False)

        self.fc.ActiveDocument = make_mock_doc([box1, box2, box3])
        result = self.handler.batch_interference({'objects': ['A', 'B', 'C']})
        self.assertIn("3 objects, 3 pairs", result)
        self.assertIn("Collisions: 0", result)
        self.assertIn("Clear: 3", result)

    def test_some_collisions(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 5, 0, 0)
        box3 = make_box("C", 30, 0, 0)

        box1.Shape.BoundBox.intersect = MagicMock(side_effect=lambda bb: bb is box2.Shape.BoundBox)
        box2.Shape.BoundBox.intersect = MagicMock(side_effect=lambda bb: bb is box1.Shape.BoundBox)
        box3.Shape.BoundBox.intersect = MagicMock(return_value=False)

        common_ab = MagicMock()
        common_ab.Volume = 500.0
        box1.Shape.common = MagicMock(return_value=common_ab)

        self.fc.ActiveDocument = make_mock_doc([box1, box2, box3])
        result = self.handler.batch_interference({'objects': ['A', 'B', 'C']})
        self.assertIn("Collisions: 1", result)
        self.assertIn("A ↔ B: 500.0000", result)

    def test_non_solid_warning_in_batch(self):
        shell = make_shell("A")
        box = make_box("B")
        self.fc.ActiveDocument = make_mock_doc([shell, box])
        result = self.handler.batch_interference({'objects': ['A', 'B']})
        self.assertIn("WARNING", result)
        self.assertIn("Shell", result)

    def test_all_solids_no_warning_in_batch(self):
        box1 = make_box("A")
        box2 = make_box("B")
        self.fc.ActiveDocument = make_mock_doc([box1, box2])
        result = self.handler.batch_interference({'objects': ['A', 'B']})
        self.assertNotIn("WARNING", result)


class TestAlignmentCheck(unittest.TestCase):

    def setUp(self):
        self.handler = make_handler()
        self.fc = spatial_ops_module.FreeCAD

    def test_aligned_z(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 0, 0, 20, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.alignment_check({'object1': 'A', 'object2': 'B', 'axis': 'Z'})
        self.assertIn("ALIGNED", result)
        self.assertIn("Lateral offset (XY): 0.0000", result)

    def test_misaligned_z(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 5, 3, 20, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.alignment_check({'object1': 'A', 'object2': 'B', 'axis': 'Z'})
        self.assertIn("MISALIGNED", result)
        self.assertIn("5.83", result)

    def test_default_axis_is_z(self):
        box1 = make_box("A", 0, 0, 0)
        box2 = make_box("B", 0, 0, 20)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.alignment_check({'object1': 'A', 'object2': 'B'})
        self.assertIn("along Z axis", result)

    def test_invalid_axis(self):
        box1 = make_box("A")
        box2 = make_box("B", 20, 0, 0)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])
        result = self.handler.alignment_check({'object1': 'A', 'object2': 'B', 'axis': 'W'})
        self.assertIn("must be", result)

    def test_x_axis(self):
        box1 = make_box("A", 0, 0, 0, 10, 10, 10)
        box2 = make_box("B", 20, 0, 0, 10, 10, 10)
        self.fc.ActiveDocument = make_mock_doc([box1, box2])

        result = self.handler.alignment_check({'object1': 'A', 'object2': 'B', 'axis': 'X'})
        self.assertIn("along X axis", result)
        self.assertIn("Axial offset (X):", result)
        self.assertIn("Lateral offset (YZ): 0.0000", result)


class TestHelpers(unittest.TestCase):

    def test_fmt_vec(self):
        h = make_handler()
        result = h._fmt_vec(FakeVector(1.234, 5.678, 9.012))
        self.assertEqual(result, "(1.23, 5.68, 9.01)")

    def test_fmt_vec_custom_decimals(self):
        h = make_handler()
        result = h._fmt_vec(FakeVector(1.2, 3.4, 5.6), decimals=1)
        self.assertEqual(result, "(1.2, 3.4, 5.6)")

    def test_get_two_shapes_missing_names(self):
        h = make_handler()
        spatial_ops_module.FreeCAD.ActiveDocument = make_mock_doc()
        s1, s2, n1, err = h._get_two_shapes({})
        self.assertIsNone(s1)
        self.assertIn("required", err)

    def test_get_two_shapes_no_doc(self):
        h = make_handler()
        spatial_ops_module.FreeCAD.ActiveDocument = None
        s1, s2, n1, err = h._get_two_shapes({'object1': 'A', 'object2': 'B'})
        self.assertIsNone(s1)
        self.assertIn("No active document", err)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for SketchOpsHandler.

Covers create_sketch, close_sketch, add_line, add_circle, add_rectangle,
add_arc, add_polygon, add_constraint dispatch (Horizontal, Coincident,
Distance, DistanceX, Radius, Angle), and delete_constraint. Sketch_ops
is the largest unit-untested handler at 792 LOC and 13% statement
coverage.

The constraint-dispatch surface is the highest-stakes part — wrong
geometry/point indices or missing value handling silently produce a
sketch that solves but doesn't constrain what the user expected. Tests
verify the right Sketcher.Constraint variant gets built for each type.

Verify_sketch is exercised in test_open_wire_diagnosis.py — not
duplicated here.
"""

import math
import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Part,
    mock_Sketcher,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_sketch,
    make_body,
    assert_error_contains,
    assert_success_contains,
)

from handlers.sketch_ops import SketchOpsHandler


def _make_real_sketch_mock(name="Sketch"):
    """Sketch that survives addGeometry and addConstraint calls.

    addGeometry returns sequential geo ids; addConstraint returns
    sequential constraint indices. The handler-side recompute() does
    nothing on the doc mock, but ConstraintCount and GeometryCount
    track real call counts so close_sketch can report them.
    """
    s = make_sketch(name)
    geo_counter = [0]
    con_counter = [0]

    def add_geom(_):
        idx = geo_counter[0]
        geo_counter[0] += 1
        s.GeometryCount = geo_counter[0]
        return idx

    def add_constraint(_):
        idx = con_counter[0]
        con_counter[0] += 1
        s.ConstraintCount = con_counter[0]
        return idx

    s.addGeometry = MagicMock(side_effect=add_geom)
    s.addConstraint = MagicMock(side_effect=add_constraint)
    s.delConstraint = MagicMock()
    s.solve = MagicMock(return_value=0)  # default: fully constrained
    s.GeometryCount = 0
    s.ConstraintCount = 0
    s.FullyConstrained = True
    return s


# ---------------------------------------------------------------------------
# create_sketch
# ---------------------------------------------------------------------------

class TestCreateSketch(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_xy_plane_default(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_sketch({'name': 'S'})

        assert_success_contains(self, result, "S", "XY")
        doc.addObject.assert_called_once_with('Sketcher::SketchObject', 'S')

    def test_xz_plane(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_sketch({'plane': 'XZ', 'name': 'S'})
        assert_success_contains(self, result, "XZ")

    def test_yz_plane(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_sketch({'plane': 'YZ', 'name': 'S'})
        assert_success_contains(self, result, "YZ")

    def test_attaches_to_active_partdesign_body(self):
        body = make_body("Body")
        doc = make_mock_doc([body])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_sketch({'name': 'S'})

        assert_success_contains(self, result, "Body")
        # body.addObject was called with the new sketch
        body.addObject.assert_called_once()


# ---------------------------------------------------------------------------
# close_sketch
# ---------------------------------------------------------------------------

class TestCloseSketch(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_reports_geometry_and_constraint_counts(self):
        s = _make_real_sketch_mock("RectS")
        s.GeometryCount = 4
        s.ConstraintCount = 8
        s.FullyConstrained = True
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.close_sketch({'sketch_name': 'RectS'})

        assert_success_contains(self, result, "RectS", "4 geometries",
                                "8 constraints", "fully constrained: True")


# ---------------------------------------------------------------------------
# add_line / add_circle / add_rectangle / add_arc / add_polygon
# ---------------------------------------------------------------------------

class TestAddLine(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_adds_line_segment_and_returns_geo_id(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_line({
            'sketch_name': 'S', 'x1': 0, 'y1': 0, 'x2': 30, 'y2': 0,
        })

        assert_success_contains(self, result, "S", "(0,0)", "(30,0)",
                                "geo_id=0")
        # Part.LineSegment was called with vectors
        mock_Part.LineSegment.assert_called_once()


class TestAddCircle(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_adds_circle_and_three_constraints(self):
        """Circle adds geometry plus DistanceX, DistanceY, Radius constraints."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_circle({
            'sketch_name': 'S', 'x': 5, 'y': 10, 'radius': 3,
        })

        assert_success_contains(self, result, "S", "center (5,10)",
                                "radius 3", "geo_id=0")
        # 3 constraint calls: DistanceX, DistanceY, Radius
        self.assertEqual(s.addConstraint.call_count, 3)


class TestAddRectangle(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_constrained_rectangle_creates_4_lines_and_12_constraints(self):
        """4 lines + 4 coincident + 2 horizontal + 2 vertical + 2 position
        + 2 size = 12 constraints total."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_rectangle({
            'sketch_name': 'S', 'x': 0, 'y': 0,
            'width': 20, 'height': 15,
        })

        assert_success_contains(self, result, "20x15", "geo_ids=[0,1,2,3]")
        # 4 line segments (one per side)
        self.assertEqual(s.addGeometry.call_count, 4)
        # 12 constraints
        self.assertEqual(s.addConstraint.call_count, 12)

    def test_unconstrained_rectangle_skips_constraints(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_rectangle({
            'sketch_name': 'S', 'x': 0, 'y': 0,
            'width': 20, 'height': 15,
            'constrain': False,
        })

        self.assertEqual(s.addGeometry.call_count, 4)
        self.assertEqual(s.addConstraint.call_count, 0)


class TestAddArc(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_angles_converted_to_radians(self):
        """Handler must convert degrees → radians for Part.ArcOfCircle."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_arc({
            'sketch_name': 'S', 'center_x': 0, 'center_y': 0,
            'radius': 5, 'start_angle': 0, 'end_angle': 90,
        })

        assert_success_contains(self, result, "S", "0°", "90°", "R5")
        # ArcOfCircle was called with radians (90° = π/2)
        ac_call = mock_Part.ArcOfCircle.call_args
        # Args: (Circle, start_rad, end_rad)
        self.assertAlmostEqual(ac_call.args[1], 0.0, places=6)
        self.assertAlmostEqual(ac_call.args[2], math.pi / 2, places=6)


class TestAddPolygon(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_minimum_3_sides(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_polygon({
            'sketch_name': 'S', 'sides': 2, 'radius': 5,
        })
        assert_error_contains(self, result, "at least 3 sides")

    def test_hexagon_creates_6_segments(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_polygon({
            'sketch_name': 'S', 'sides': 6, 'radius': 10,
        })

        assert_success_contains(self, result, "S", "6")
        # 6 line segments for hexagon
        self.assertEqual(s.addGeometry.call_count, 6)


# ---------------------------------------------------------------------------
# add_constraint — dispatch table
# ---------------------------------------------------------------------------

class TestAddConstraint(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_unknown_constraint_type(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'WeirdType',
        })
        assert_error_contains(self, result, "unknown constraint type",
                              "weirdtype")

    def test_horizontal_takes_only_geo_id(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Horizontal',
            'geo_id1': 0,
        })

        assert_success_contains(self, result, "Horizontal")
        # Sketcher.Constraint('Horizontal', 0)
        sc_call = mock_Sketcher.Constraint.call_args
        self.assertEqual(sc_call.args, ('Horizontal', 0))

    def test_coincident_takes_two_points(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Coincident',
            'geo_id1': 0, 'pos_id1': 2,
            'geo_id2': 1, 'pos_id2': 1,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        self.assertEqual(sc_call.args, ('Coincident', 0, 2, 1, 1))

    def test_radius_requires_value(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Radius',
            'geo_id1': 0,
        })

        assert_error_contains(self, result, "radius constraint requires a value")

    def test_radius_with_value(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Radius',
            'geo_id1': 2, 'value': 7.5,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        self.assertEqual(sc_call.args, ('Radius', 2, 7.5))

    def test_distance_with_two_points_and_value(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Distance',
            'geo_id1': 0, 'pos_id1': 1,
            'geo_id2': 0, 'pos_id2': 2,
            'value': 25,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        self.assertEqual(sc_call.args, ('Distance', 0, 1, 0, 2, 25))

    def test_distance_x_short_form(self):
        """DistanceX with just geo_id1+pos_id1+value (no geo_id2)."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'DistanceX',
            'geo_id1': 0, 'pos_id1': 1, 'value': 10,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        # Short form: single point with value
        self.assertEqual(sc_call.args, ('DistanceX', 0, 1, 10))

    def test_angle_converted_to_radians(self):
        """Angle constraints take degrees from caller, convert to radians."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Angle',
            'geo_id1': 0, 'geo_id2': 1, 'value': 45,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        # 45° in radians ≈ 0.7854
        self.assertEqual(sc_call.args[0], 'Angle')
        self.assertAlmostEqual(sc_call.args[3], math.pi / 4, places=4)

    def test_fix_maps_to_lock_constraint(self):
        """Fix is renamed to Lock internally."""
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_constraint({
            'sketch_name': 'S', 'constraint_type': 'Fix',
            'geo_id1': 0, 'pos_id1': 1,
        })

        sc_call = mock_Sketcher.Constraint.call_args
        self.assertEqual(sc_call.args[0], 'Lock')


# ---------------------------------------------------------------------------
# delete_constraint
# ---------------------------------------------------------------------------

class TestDeleteConstraint(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SketchOpsHandler)

    def test_index_required(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.delete_constraint({
            'sketch_name': 'S',
        })
        assert_error_contains(self, result, "index is required")

    def test_calls_delConstraint_with_int_index(self):
        s = _make_real_sketch_mock("S")
        doc = make_mock_doc([s])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.delete_constraint({
            'sketch_name': 'S', 'index': 3,
        })

        s.delConstraint.assert_called_once_with(3)
        assert_success_contains(self, result, "Deleted constraint 3", "S")


if __name__ == '__main__':
    unittest.main()

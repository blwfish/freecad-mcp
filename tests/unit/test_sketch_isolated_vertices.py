"""Unit tests for isolated-vertex diagnosis in sketches.

A sketch whose Shape is Compound[Wire, Vertex] is valid for pad/pocket, but
Part -> Sweep only lists a sketch as a profile when its Shape is a single wire
(or only edges), so it is silently left out of the dialog.  Found live
2026-10-07: a closed gutter profile anchored to a *Defining* external vertex
(buildShape exports Defining external points into the Shape).  verify_sketch
said VALID, and health_check misreported the stray vertex as an open endpoint
at the nearest geometry's start point.

Covers BaseHandler._sketch_isolated_vertices, ._diagnose_isolated_vertices,
the open-vertex subtraction in ._diagnose_open_wires, and the new sections in
verify_sketch and _sketch_health_check.

Run with: python3 -m pytest tests/unit/test_sketch_isolated_vertices.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

if 'FreeCAD' not in sys.modules:
    _fc = MagicMock()
    _fc.GuiUp = False
    _fc.Console = MagicMock()
    sys.modules['FreeCAD'] = _fc
    sys.modules['FreeCADGui'] = MagicMock()
    sys.modules['Part'] = MagicMock()
    sys.modules['Sketcher'] = MagicMock()

sys.modules['FreeCAD'].ActiveDocument = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import handlers.base as base_module  # noqa: E402
import handlers.sketch_ops as sketch_ops_module  # noqa: E402
from handlers.base import BaseHandler  # noqa: E402
from handlers.sketch_ops import SketchOpsHandler  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _V:
    """Vector stand-in with .x/.y/.z (and a Length property like FreeCAD's)."""
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @property
    def Length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    def __sub__(self, o):
        return _V(self.x - o.x, self.y - o.y, self.z - o.z)


class _Placement:
    """Translation-only Placement: inverse().multVec(p) maps global -> local."""
    def __init__(self, dx=0.0, dy=0.0, dz=0.0, inverted=False):
        self.d, self.inverted = (dx, dy, dz), inverted

    def inverse(self):
        return _Placement(*self.d, inverted=True)

    def multVec(self, p):
        s = -1.0 if self.inverted else 1.0
        # inverse of "translate by d" is "subtract d": s=-1 when inverted
        return _V(p.x + s * self.d[0], p.y + s * self.d[1], p.z + s * self.d[2])


class _Child:
    def __init__(self, shape_type, point=None):
        self.ShapeType = shape_type
        if point is not None:
            self.Point = point


class _Shape:
    def __init__(self, children, wires=None):
        self._children = children
        self.Wires = wires if wires is not None else [w for w in children if w.ShapeType == 'Wire']

    def childShapes(self):
        return list(self._children)

    def __bool__(self):
        return True


class _PointGeo:           # Part.Point: has X/Y, no StartPoint
    def __init__(self, x, y):
        self.X, self.Y, self.Z = x, y, 0.0


class _LineGeo:
    def __init__(self, sx, sy, ex, ey):
        self.StartPoint, self.EndPoint = _V(sx, sy), _V(ex, ey)


class _ExtObj:
    def __init__(self, name, label, vertices):
        self.Name, self.Label = name, label
        self._v = vertices
        self.Shape = self

    def getElement(self, sub):
        return _Child('Vertex', _V(*self._v[sub]))


WIRE = _Child('Wire')


def _sketch(children, geometry=None, construction=None, external=None,
            placement=None, open_vertices=None, name="Gutter", label="gutter profile"):
    s = MagicMock()
    s.Name, s.Label = name, label
    s.TypeId = "Sketcher::SketchObject"
    geometry = geometry if geometry is not None else []
    s.Geometry = geometry
    s.GeometryCount = len(geometry)
    s.ConstraintCount = 0
    flags = construction or [False] * len(geometry)
    s.getConstruction = lambda i: flags[i]
    s.ExternalGeometry = external or []
    s.Placement = placement or _Placement()
    s.Shape = _Shape(children)
    s.solve.return_value = 0
    s.OpenVertices = open_vertices or []
    s.detectMissingPointOnPointConstraints.return_value = None
    s.MissingPointOnPointConstraints = []
    s.evaluateConstraints.return_value = True
    s.detectDegeneratedGeometries.return_value = 0
    s.detectMissingVerticalHorizontalConstraints.return_value = None
    s.MissingVerticalHorizontalConstraints = []
    s.detectMissingEqualityConstraints.return_value = None
    s.MissingLineEqualityConstraints = []
    s.MissingRadiusConstraints = []
    return s


def _handler():
    return BaseHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def _vtx(x, y, z=0.0):
    return _Child('Vertex', _V(x, y, z))


MASTER = _ExtObj("Sketch001", "Master XZ", {"Vertex16": (99.3228, 59.5586, 0.0)})


# ---------------------------------------------------------------------------
# _sketch_isolated_vertices
# ---------------------------------------------------------------------------

class TestIsolatedVertexCollection:
    def test_wire_plus_vertex_is_reported(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        assert _handler()._sketch_isolated_vertices(s) == [(1.0, 2.0)]

    def test_two_vertices_both_reported_in_order(self):
        s = _sketch([WIRE, _vtx(1, 2), _vtx(3, 4)])
        assert _handler()._sketch_isolated_vertices(s) == [(1.0, 2.0), (3.0, 4.0)]

    def test_pure_wire_reports_nothing(self):
        assert _handler()._sketch_isolated_vertices(_sketch([WIRE])) == []

    def test_all_edges_reports_nothing(self):
        s = _sketch([_Child('Edge')] * 4)
        assert _handler()._sketch_isolated_vertices(s) == []

    def test_points_only_sketch_is_not_a_mixed_shape(self):
        # Pins: a sketch that is ONLY points has nothing riding along.
        s = _sketch([_vtx(1, 2), _vtx(3, 4)])
        assert _handler()._sketch_isolated_vertices(s) == []

    def test_single_vertex_alone_is_not_mixed(self):
        assert _handler()._sketch_isolated_vertices(_sketch([_vtx(1, 2)])) == []

    def test_empty_shape_reports_nothing(self):
        assert _handler()._sketch_isolated_vertices(_sketch([])) == []

    def test_global_point_converted_to_sketch_local_frame(self):
        # Sketch translated by +100 in x: global (110, 20) is local (10, 20).
        s = _sketch([WIRE, _vtx(110, 20)], placement=_Placement(dx=100.0))
        assert _handler()._sketch_isolated_vertices(s) == [(10.0, 20.0)]

    def test_missing_placement_falls_back_to_global(self):
        s = _sketch([WIRE, _vtx(5, 6)])
        s.Placement = None            # .inverse() raises AttributeError
        assert _handler()._sketch_isolated_vertices(s) == [(5.0, 6.0)]

    def test_falsy_shape_reports_nothing(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        s.Shape = None
        assert _handler()._sketch_isolated_vertices(s) == []

    def test_childshapes_raising_reports_nothing_not_a_crash(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        s.Shape.childShapes = MagicMock(side_effect=RuntimeError("boom"))
        assert _handler()._sketch_isolated_vertices(s) == []

    def test_plain_magicmock_sketch_reports_nothing(self):
        # Existing health-check tests use bare MagicMock sketches; they must
        # not suddenly sprout an "isolated vertices" finding.
        assert _handler()._sketch_isolated_vertices(MagicMock()) == []


# ---------------------------------------------------------------------------
# _diagnose_isolated_vertices : origin attribution
# ---------------------------------------------------------------------------

class TestOriginAttribution:
    def test_clean_sketch_returns_empty_string(self):
        assert _handler()._diagnose_isolated_vertices(_sketch([WIRE])) == ""

    def test_defining_external_vertex_is_named_with_its_label(self):
        # The exact case from the live gutter-profile sketch.
        s = _sketch([WIRE, _vtx(99.3228, 59.5586)],
                    external=[(MASTER, ("Vertex16",))])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "external geometry Sketch001.Vertex16" in out
        assert "'Master XZ'" in out
        assert "Defining flag" in out
        assert "99.3228" in out and "59.5586" in out

    def test_external_fix_text_says_to_turn_defining_off(self):
        s = _sketch([WIRE, _vtx(99.3228, 59.5586)], external=[(MASTER, ("Vertex16",))])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "Turn the Defining flag off for external geometry Sketch001.Vertex16" in out

    def test_non_construction_point_geometry_is_named_with_fix(self):
        s = _sketch([WIRE, _vtx(15, 12)], geometry=[_LineGeo(0, 0, 1, 0), _PointGeo(15, 12)])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "point geometry geo_id=1" in out
        assert "toggleConstruction(1)" in out
        assert "Gutter.toggleConstruction" in out        # uses the sketch's own Name

    def test_construction_point_is_not_blamed(self):
        # A construction point is never exported, so it cannot be the origin.
        s = _sketch([WIRE, _vtx(15, 12)], geometry=[_PointGeo(15, 12)], construction=[True])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "origin not identified" in out
        assert "toggleConstruction" not in out

    def test_unknown_origin_is_stated_not_guessed(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "origin not identified" in out

    def test_external_edge_refs_are_ignored(self):
        ext = _ExtObj("Sk", "Sk", {"Vertex1": (1.0, 2.0, 0.0)})
        s = _sketch([WIRE, _vtx(1, 2)], external=[(ext, ("Edge3",))])
        assert "origin not identified" in _handler()._diagnose_isolated_vertices(s)

    @pytest.mark.parametrize("offset,matched", [
        (0.0, True),
        (0.9e-4, True),      # just inside the 1e-4 tolerance
        (1.1e-4, False),     # just outside
        (0.5, False),
    ])
    def test_match_tolerance_at_below_above(self, offset, matched):
        s = _sketch([WIRE, _vtx(10 + offset, 10)], geometry=[_PointGeo(10, 10)])
        out = _handler()._diagnose_isolated_vertices(s)
        assert ("point geometry geo_id=0" in out) is matched

    def test_both_axes_must_match(self):
        s = _sketch([WIRE, _vtx(10, 10)], geometry=[_PointGeo(10, 11)])
        assert "origin not identified" in _handler()._diagnose_isolated_vertices(s)

    def test_external_vertex_matched_in_sketch_local_xy(self):
        # Sketch translated by +100 in x; the external vertex is expressed in
        # global coordinates, the exported vertex likewise -- both map to local.
        ext = _ExtObj("M", "M", {"Vertex1": (110.0, 20.0, 0.0)})
        s = _sketch([WIRE, _vtx(110, 20)], external=[(ext, ("Vertex1",))],
                    placement=_Placement(dx=100.0))
        out = _handler()._diagnose_isolated_vertices(s)
        assert "external geometry M.Vertex1" in out
        assert "(10.0000, 20.0000)" in out

    def test_two_vertices_each_attributed_separately(self):
        s = _sketch([WIRE, _vtx(99.3228, 59.5586), _vtx(15, 12)],
                    geometry=[_PointGeo(15, 12)], external=[(MASTER, ("Vertex16",))])
        out = _handler()._diagnose_isolated_vertices(s)
        assert out.count("•") == 2
        assert "external geometry Sketch001.Vertex16" in out
        assert "point geometry geo_id=0" in out

    def test_explains_why_it_matters_for_sweep(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        out = _handler()._diagnose_isolated_vertices(s)
        assert "Part -> Sweep" in out and "pad/pocket still work" in out

    def test_unreadable_external_geometry_degrades_to_unknown_origin(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        bad = MagicMock()
        bad.Shape.getElement.side_effect = RuntimeError("stale ref")
        s.ExternalGeometry = [(bad, ("Vertex1",))]
        assert "origin not identified" in _handler()._diagnose_isolated_vertices(s)

    def test_none_external_geometry_is_tolerated(self):
        s = _sketch([WIRE, _vtx(1, 2)])
        s.ExternalGeometry = None
        assert "origin not identified" in _handler()._diagnose_isolated_vertices(s)

    def test_duplicate_fix_lines_are_deduplicated(self):
        # Two exported vertices from the same external ref -> one fix line.
        ext = _ExtObj("M", "M", {"Vertex1": (1.0, 2.0, 0.0)})
        s = _sketch([WIRE, _vtx(1, 2), _vtx(1, 2)], external=[(ext, ("Vertex1",))])
        out = _handler()._diagnose_isolated_vertices(s)
        assert out.count("Turn the Defining flag off") == 1


# ---------------------------------------------------------------------------
# _diagnose_open_wires : the mislabel fix
# ---------------------------------------------------------------------------

def _diag(sketch):
    h = _handler()
    with patch.object(base_module, 'FreeCAD') as fc:
        fc.Vector = _V
        return h._diagnose_open_wires(sketch)


class TestOpenWireMislabelFix:
    def test_open_vertex_that_is_the_stray_vertex_is_not_reported(self):
        # Reproduces the live report: "1 open endpoint(s) found: geo_id=0
        # start-point" for a closed wire plus one isolated vertex.
        s = _sketch([WIRE, _vtx(10, 10)], geometry=[_LineGeo(10, 10, 20, 10)],
                    open_vertices=[(10.0, 10.0, 0.0)])
        assert "open endpoint" not in _diag(s)

    def test_without_isolated_vertex_the_open_endpoint_is_still_reported(self):
        s = _sketch([WIRE], geometry=[_LineGeo(10, 10, 20, 10)],
                    open_vertices=[(10.0, 10.0, 0.0)])
        out = _diag(s)
        assert "1 open endpoint(s) found" in out and "geo_id=0" in out

    def test_a_real_dangling_endpoint_elsewhere_is_kept(self):
        s = _sketch([WIRE, _vtx(10, 10)],
                    geometry=[_LineGeo(10, 10, 20, 10), _LineGeo(20, 10, 30, 10)],
                    open_vertices=[(10.0, 10.0, 0.0), (30.0, 10.0, 0.0)])
        out = _diag(s)
        assert "1 open endpoint(s) found" in out
        assert "(30.0000, 10.0000)" in out and "(10.0000, 10.0000)" not in out

    def test_only_one_open_vertex_consumed_per_isolated_vertex(self):
        # A genuine dangling edge end coincident with the stray vertex's spot
        # is a SEPARATE open vertex in OCCT's map; it must survive.
        s = _sketch([WIRE, _vtx(10, 10)], geometry=[_LineGeo(10, 10, 20, 10)],
                    open_vertices=[(10.0, 10.0, 0.0), (10.0, 10.0, 0.0)])
        assert "1 open endpoint(s) found" in _diag(s)

    def test_points_only_sketch_keeps_old_behaviour(self):
        # Pins: not a mixed shape, so nothing is subtracted.
        s = _sketch([_vtx(10, 10)], geometry=[_PointGeo(10, 10)],
                    open_vertices=[(10.0, 10.0, 0.0)])
        assert "1 open endpoint(s) found" in _diag(s)

    @pytest.mark.parametrize("dx,subtracted", [(0.9e-4, True), (1.1e-4, False)])
    def test_subtraction_tolerance_at_below_above(self, dx, subtracted):
        s = _sketch([WIRE, _vtx(10 + dx, 10)], geometry=[_LineGeo(10, 10, 20, 10)],
                    open_vertices=[(10.0, 10.0, 0.0)])
        assert ("open endpoint" not in _diag(s)) is subtracted


# ---------------------------------------------------------------------------
# verify_sketch / health_check integration
# ---------------------------------------------------------------------------

def _sketch_handler():
    return SketchOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def _run_verify(sketch):
    h = _sketch_handler()
    doc = MagicMock()
    doc.getObject = MagicMock(return_value=sketch)
    doc.getObjectsByLabel = MagicMock(return_value=[sketch])
    with patch.object(base_module, 'FreeCAD') as f1, patch.object(sketch_ops_module, 'FreeCAD') as f2:
        f1.ActiveDocument = f2.ActiveDocument = doc
        f1.Vector = f2.Vector = _V
        return h.verify_sketch({'sketch_name': sketch.Name})


def _closed(shape_children):
    w = MagicMock()
    w.isClosed.return_value = True
    w.ShapeType = 'Wire'
    return [w if c is WIRE else c for c in shape_children]


class TestVerifySketchSection:
    def _mixed(self):
        s = _sketch(_closed([WIRE, _vtx(99.3228, 59.5586)]),
                    external=[(MASTER, ("Vertex16",))])
        s.Shape.Wires = [c for c in s.Shape._children if c.ShapeType == 'Wire']
        return s

    def test_mixed_shape_gets_isolated_vertices_section(self):
        out = _run_verify(self._mixed())
        assert "Isolated vertices:" in out
        assert "external geometry Sketch001.Vertex16" in out

    def test_pad_verdict_is_unchanged_by_a_stray_vertex(self):
        # Pins: pad/pocket really do work with a stray vertex, so the verdict
        # still says VALID; sweep ineligibility is reported separately.
        assert "Verdict: VALID" in _run_verify(self._mixed())

    def test_clean_sketch_has_no_section(self):
        s = _sketch(_closed([WIRE]))
        s.Shape.Wires = [c for c in s.Shape._children if c.ShapeType == 'Wire']
        assert "Isolated vertices" not in _run_verify(s)


class TestHealthCheckSection:
    def _hc(self, sketch):
        with patch.object(base_module, 'FreeCAD') as fc:
            fc.Vector = _V
            return _handler()._sketch_health_check(sketch)

    def test_clean_sketch_reports_none_found(self):
        assert "Isolated vertices in Shape: none found" in self._hc(_sketch([WIRE]))

    def test_mixed_shape_is_a_problem_with_named_origin(self):
        s = _sketch([WIRE, _vtx(99.3228, 59.5586)], external=[(MASTER, ("Vertex16",))])
        out = self._hc(s)
        assert "Isolated vertices in Shape:" in out and "none found" not in \
            out.split("Isolated vertices in Shape:")[1].splitlines()[0]
        assert "external geometry Sketch001.Vertex16" in out
        assert "PROBLEMS FOUND" in out

    def test_stray_vertex_no_longer_reported_as_open_wire(self):
        s = _sketch([WIRE, _vtx(10, 10)], geometry=[_LineGeo(10, 10, 20, 10)],
                    open_vertices=[(10.0, 10.0, 0.0)])
        out = self._hc(s)
        assert "Open wire / unclosed profile: none found" in out

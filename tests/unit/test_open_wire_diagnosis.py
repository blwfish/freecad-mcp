"""Unit tests for open wire diagnosis helpers.

Tests _find_geo_for_point and _diagnose_open_wires on BaseHandler, plus
the upgraded verify_sketch and the auto-diagnosis injected into pad/pocket
failures.

Run with: python3 -m pytest tests/unit/test_open_wire_diagnosis.py -v
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal FreeCAD mocks — must happen before handler imports
# ---------------------------------------------------------------------------

class FakeVector:
    def __init__(self, x=0, y=0, z=0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def Length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    def __sub__(self, other):
        return FakeVector(self.x - other.x, self.y - other.y, self.z - other.z)


# FreeCAD.Vector arithmetic is used by _find_geo_for_point — we need a
# version that returns a FakeVector AND exposes .Length as a property.
class FakeVectorCalc:
    """FakeVector whose .Length is a float property (matches FreeCAD API)."""
    def __init__(self, x=0, y=0, z=0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @property
    def Length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5


def FakeVectorFactory(x=0, y=0, z=0):
    return FakeVectorCalc(x, y, z)


if 'FreeCAD' not in sys.modules:
    _fc = MagicMock()
    _fc.GuiUp = False
    _fc.Console = MagicMock()
    sys.modules['FreeCAD'] = _fc
    sys.modules['FreeCADGui'] = MagicMock()
    sys.modules['Part'] = MagicMock()
    sys.modules['Sketcher'] = MagicMock()

sys.modules['FreeCAD'].Vector = FakeVectorFactory
sys.modules['FreeCAD'].ActiveDocument = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import handlers.base as base_module
from handlers.base import BaseHandler
import handlers.sketch_ops as sketch_ops_module
from handlers.sketch_ops import SketchOpsHandler
import handlers.partdesign_ops as partdesign_ops_module
from handlers.partdesign_ops import PartDesignOpsHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_base_handler():
    server = MagicMock()
    return BaseHandler(server, MagicMock(), MagicMock(return_value={}))


def make_sketch_handler():
    server = MagicMock()
    return SketchOpsHandler(server, MagicMock(), MagicMock(return_value={}))


def make_pd_handler():
    server = MagicMock()
    return PartDesignOpsHandler(server, MagicMock(), MagicMock(return_value={}))


def _fake_geo(start_x, start_y, end_x, end_y):
    """Return a mock geometry object with StartPoint / EndPoint."""
    geo = MagicMock()
    geo.StartPoint = FakeVectorCalc(start_x, start_y, 0)
    geo.EndPoint = FakeVectorCalc(end_x, end_y, 0)
    return geo


def _make_sketch(geom_list, construction_flags=None):
    """Build a mock sketch with geometry and no construction by default."""
    sketch = MagicMock()
    sketch.Name = "Sketch"
    sketch.TypeId = "Sketcher::SketchObject"
    sketch.GeometryCount = len(geom_list)
    sketch.Geometry = geom_list
    flags = construction_flags or [False] * len(geom_list)
    sketch.getConstruction = lambda i: flags[i]
    return sketch


# ---------------------------------------------------------------------------
# Tests: _find_geo_for_point
# ---------------------------------------------------------------------------

def _with_vector(test_fn):
    """Context manager: patch base_module.FreeCAD.Vector to FakeVectorFactory."""
    from contextlib import contextmanager
    import unittest.mock as um

    @contextmanager
    def _ctx():
        with um.patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            yield mock_fc

    return _ctx


class TestFindGeoForPoint:

    def test_finds_exact_start_point(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0)]
        sketch = _make_sketch(geom)
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(sketch, FakeVectorCalc(0, 0, 0))
        assert result is not None
        geo_id, pos_id, dist = result
        assert geo_id == 0
        assert pos_id == 1   # start
        assert dist < 1e-9

    def test_finds_exact_end_point(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0)]
        sketch = _make_sketch(geom)
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(sketch, FakeVectorCalc(10, 0, 0))
        assert result is not None
        geo_id, pos_id, dist = result
        assert geo_id == 0
        assert pos_id == 2   # end

    def test_finds_closest_among_multiple_geometries(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0), _fake_geo(10, 0, 10, 10)]
        sketch = _make_sketch(geom)
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(sketch, FakeVectorCalc(10, 0, 0))
        assert result is not None
        geo_id, pos_id, dist = result
        assert dist < 1e-9

    def test_returns_none_when_nothing_within_tolerance(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0)]
        sketch = _make_sketch(geom)
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(
                sketch, FakeVectorCalc(100, 100, 0), tolerance=0.5
            )
        assert result is None

    def test_skips_construction_geometry(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0)]
        sketch = _make_sketch(geom, construction_flags=[True])
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(sketch, FakeVectorCalc(0, 0, 0))
        assert result is None

    def test_handles_geometry_without_start_end(self):
        handler = make_base_handler()
        circle = MagicMock(spec=[])   # no StartPoint/EndPoint attributes
        sketch = _make_sketch([circle])
        sketch.getConstruction = lambda i: False
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._find_geo_for_point(sketch, FakeVectorCalc(0, 0, 0))
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _diagnose_open_wires
# ---------------------------------------------------------------------------

class TestDiagnoseOpenWires:

    def test_empty_string_when_no_open_vertices(self):
        handler = make_base_handler()
        sketch = _make_sketch([_fake_geo(0, 0, 10, 0)])
        sketch.getOpenVertices = lambda: []
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
        result = handler._diagnose_open_wires(sketch)
        assert result == ""

    def test_reports_open_vertex_with_geo_id(self):
        handler = make_base_handler()
        geom = [_fake_geo(0, 0, 10, 0), _fake_geo(10.05, 0, 10, 10)]
        sketch = _make_sketch(geom)
        # Open vertex at the gap between the two lines' endpoints
        sketch.getOpenVertices = lambda: [FakeVectorCalc(10, 0, 0)]
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = FakeVectorFactory
            result = handler._diagnose_open_wires(sketch)
        assert "open endpoint" in result
        assert "geo_id=0" in result   # line 0 end-point at (10, 0)
        assert "end-point" in result

    def test_includes_suggested_constraints(self):
        handler = make_base_handler()
        sketch = _make_sketch([_fake_geo(0, 0, 10, 0)])
        sketch.getOpenVertices = lambda: []
        # Simulate 1 missing constraint
        c = MagicMock()
        c.First = 0
        c.FirstPos = 2
        c.Second = 1
        c.SecondPos = 1
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=1)
        sketch.getMissingPointOnPointConstraints = MagicMock(return_value=[c])
        result = handler._diagnose_open_wires(sketch)
        assert "suggested fix" in result
        assert 'constraint_type="Coincident"' in result
        assert "geo_id1=0" in result
        assert "geo_id2=1" in result

    def test_graceful_when_getOpenVertices_missing(self):
        handler = make_base_handler()
        sketch = _make_sketch([])
        sketch.getOpenVertices = MagicMock(side_effect=AttributeError("no method"))
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
        # Should not raise
        result = handler._diagnose_open_wires(sketch)
        assert isinstance(result, str)

    def test_graceful_when_detectMissing_raises(self):
        handler = make_base_handler()
        sketch = _make_sketch([])
        sketch.getOpenVertices = lambda: []
        sketch.detectMissingPointOnPointConstraints = MagicMock(
            side_effect=RuntimeError("not available")
        )
        result = handler._diagnose_open_wires(sketch)
        assert isinstance(result, str)   # no crash

    def test_dangling_point_without_matching_geo(self):
        handler = make_base_handler()
        sketch = _make_sketch([])
        sketch.GeometryCount = 0
        sketch.getOpenVertices = lambda: [FakeVectorCalc(99, 99, 0)]
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
        result = handler._diagnose_open_wires(sketch)
        assert "Dangling point" in result
        assert "99" in result


# ---------------------------------------------------------------------------
# Tests: verify_sketch includes diagnosis for open wires
# ---------------------------------------------------------------------------

class TestVerifySketchOpenWireDiagnosis:

    def _make_open_wire_sketch(self):
        """Sketch with 1 open wire (shape present but not closed)."""
        sketch = MagicMock()
        sketch.Name = "Sketch"
        sketch.TypeId = "Sketcher::SketchObject"
        sketch.GeometryCount = 2
        sketch.ConstraintCount = 3
        sketch.solve = MagicMock(return_value=0)

        # Open wire
        wire = MagicMock()
        wire.isClosed = MagicMock(return_value=False)

        shape = MagicMock()
        shape.Wires = [wire]
        sketch.Shape = shape

        sketch.getConstruction = MagicMock(return_value=False)
        sketch.getOpenVertices = MagicMock(return_value=[])
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
        return sketch

    def test_verify_sketch_calls_diagnosis_for_open_wire(self):
        handler = make_sketch_handler()

        sketch = self._make_open_wire_sketch()

        mock_doc = MagicMock()
        mock_doc.getObject = MagicMock(return_value=sketch)
        mock_doc.getObjectsByLabel = MagicMock(return_value=[sketch])

        with patch.object(base_module, 'FreeCAD') as mock_fc, \
             patch.object(sketch_ops_module, 'FreeCAD') as mock_fc2:
            mock_fc.ActiveDocument = mock_doc
            mock_fc2.ActiveDocument = mock_doc
            mock_fc.Vector = FakeVectorFactory
            mock_fc2.Vector = FakeVectorFactory

            # Spy on _diagnose_open_wires
            original_diag = handler._diagnose_open_wires
            calls = []

            def spy_diag(sk):
                calls.append(sk)
                return original_diag(sk)

            handler._diagnose_open_wires = spy_diag

            result = handler.verify_sketch({'sketch_name': 'Sketch'})

        assert len(calls) == 1, "should have called _diagnose_open_wires once"
        assert "Open wire" in result or "open" in result.lower()


# ---------------------------------------------------------------------------
# Tests: pad failure injects diagnosis
# ---------------------------------------------------------------------------

class TestPadAutodiagnosis:

    def test_pad_failure_includes_diagnosis(self):
        handler = make_pd_handler()

        sketch = MagicMock()
        sketch.Name = "Sketch"
        sketch.TypeId = "Sketcher::SketchObject"
        sketch.GeometryCount = 2
        sketch.getConstruction = MagicMock(return_value=False)
        sketch.getOpenVertices = MagicMock(
            return_value=[FakeVectorCalc(10, 0, 0)]
        )
        sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=0)

        # Body that raises on newObject (simulates the wire-not-closed error)
        body = MagicMock()
        body.newObject = MagicMock(
            side_effect=Exception("Wire is not closed")
        )

        mock_doc = MagicMock()
        mock_doc.getObject = MagicMock(return_value=sketch)
        mock_doc.getObjectsByLabel = MagicMock(return_value=[sketch])

        with patch.object(base_module, 'FreeCAD') as mock_fc, \
             patch.object(partdesign_ops_module, 'FreeCAD') as mock_fc2:
            mock_fc.ActiveDocument = mock_doc
            mock_fc2.ActiveDocument = mock_doc
            mock_fc.Vector = FakeVectorFactory
            mock_fc2.Vector = FakeVectorFactory

            # Make create_body_if_needed return our mock body
            handler.create_body_if_needed = MagicMock(return_value=body)
            handler.find_body_for_object = MagicMock(return_value=None)

            result = handler.pad_sketch({'sketch_name': 'Sketch', 'length': 10})

        assert "Wire is not closed" in result   # original error preserved
        assert "diagnosis" in result.lower() or "open" in result.lower()

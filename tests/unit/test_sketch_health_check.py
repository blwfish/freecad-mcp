"""Unit tests for the sketch health-check wrapper and its dependency-graph
traversal: BaseHandler._find_upstream_sketches, BaseHandler._sketch_health_check,
SketchOpsHandler.health_check, and MeasurementOpsHandler.diagnose_invalid_shape.

Run with: python3 -m pytest tests/unit/test_sketch_health_check.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

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

import handlers.base as base_module
from handlers.base import BaseHandler
import handlers.sketch_ops as sketch_ops_module
from handlers.sketch_ops import SketchOpsHandler
import handlers.measurement_ops as measurement_ops_module
from handlers.measurement_ops import MeasurementOpsHandler


def make_base_handler():
    return BaseHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def make_sketch_handler():
    return SketchOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def make_measurement_handler():
    return MeasurementOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def _make_clean_sketch(name="Sketch"):
    """A sketch that reports no problems on every check."""
    sketch = MagicMock()
    sketch.Name = name
    sketch.Label = name
    sketch.TypeId = "Sketcher::SketchObject"
    sketch.GeometryCount = 0
    sketch.ConstraintCount = 0
    sketch.solve.return_value = 0
    sketch.OpenVertices = []
    sketch.detectMissingPointOnPointConstraints.return_value = None
    sketch.MissingPointOnPointConstraints = []
    sketch.evaluateConstraints.return_value = True
    sketch.detectDegeneratedGeometries.return_value = 0
    sketch.detectMissingVerticalHorizontalConstraints.return_value = None
    sketch.MissingVerticalHorizontalConstraints = []
    sketch.detectMissingEqualityConstraints.return_value = None
    sketch.MissingLineEqualityConstraints = []
    sketch.MissingRadiusConstraints = []
    sketch.OutList = []
    return sketch


def _make_object(name, type_id="Part::Feature", out_list=None):
    obj = MagicMock()
    obj.Name = name
    obj.TypeId = type_id
    obj.OutList = out_list or []
    return obj


# ---------------------------------------------------------------------------
# _find_upstream_sketches
# ---------------------------------------------------------------------------

class TestFindUpstreamSketches:

    def test_no_sketch_upstream(self):
        handler = make_base_handler()
        box = _make_object("Box", out_list=[])
        result = handler._find_upstream_sketches(box)
        assert result == []

    def test_direct_sketch_upstream(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch("Sketch")
        revolve = _make_object("Revolve", "Part::Revolution", out_list=[sketch])
        result = handler._find_upstream_sketches(revolve)
        assert result == [sketch]

    def test_transitive_sketch_upstream(self):
        """Revolve -> Sketch2 -> Sketch1 (external geometry chain): both
        sketches must be found, not just the directly-linked one."""
        handler = make_base_handler()
        sketch1 = _make_clean_sketch("Sketch1")
        sketch2 = _make_clean_sketch("Sketch2")
        sketch2.OutList = [sketch1]
        revolve = _make_object("Revolve", "Part::Revolution", out_list=[sketch2])
        result = handler._find_upstream_sketches(revolve)
        assert result == [sketch2, sketch1]

    def test_diamond_dependency_deduped(self):
        """The same sketch reachable via multiple OutList paths (the
        Parker51 pattern: 8 separate external-geometry references to one
        master sketch) must appear exactly once, not once per path."""
        handler = make_base_handler()
        master = _make_clean_sketch("Master")
        sketch_a = _make_clean_sketch("SketchA")
        sketch_a.OutList = [master, master, master]  # 3 references to Master
        sketch_b = _make_clean_sketch("SketchB")
        sketch_b.OutList = [master]
        top = _make_object("Top", out_list=[sketch_a, sketch_b])

        result = handler._find_upstream_sketches(top)
        names = [s.Name for s in result]
        assert names.count("Master") == 1
        assert set(names) == {"SketchA", "SketchB", "Master"}

    def test_non_sketch_objects_in_chain_are_skipped_but_walked_through(self):
        """A Fusion feeding two Extrudes, each fed by its own sketch: both
        sketches must surface even though Fusion/Extrude aren't sketches."""
        handler = make_base_handler()
        sketch1 = _make_clean_sketch("Sketch1")
        sketch2 = _make_clean_sketch("Sketch2")
        extrude1 = _make_object("Extrude1", "Part::Extrusion", out_list=[sketch1])
        extrude2 = _make_object("Extrude2", "Part::Extrusion", out_list=[sketch2])
        fusion = _make_object("Fusion", "Part::MultiFuse", out_list=[extrude1, extrude2])

        result = handler._find_upstream_sketches(fusion)
        assert {s.Name for s in result} == {"Sketch1", "Sketch2"}

    def test_depth_cap_does_not_crash_on_a_long_chain(self):
        """60 objects deep must not stack-overflow or infinite-loop; the
        defensive depth cap (50) should simply stop early rather than error."""
        handler = make_base_handler()
        chain = _make_clean_sketch("Bottom")
        for i in range(60):
            chain = _make_object(f"Obj{i}", out_list=[chain])
        result = handler._find_upstream_sketches(chain)
        assert isinstance(result, list)   # no crash


# ---------------------------------------------------------------------------
# _sketch_health_check
# ---------------------------------------------------------------------------

class TestSketchHealthCheck:

    def test_clean_sketch_reports_clean_verdict(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "Verdict: CLEAN" in result
        assert "none found" in result

    def test_open_wire_surfaces_in_verdict(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.OpenVertices = [(1.0, 2.0, 0.0)]
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "PROBLEMS FOUND" in result
        assert "open endpoint" in result

    def test_invalid_constraints_surface_with_fix_call(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.evaluateConstraints.return_value = False
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "PROBLEMS FOUND" in result
        assert "Invalid constraints: found" in result
        assert "validateConstraints" in result

    def test_degenerate_geometry_surfaces_with_fix_call(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.detectDegeneratedGeometries.return_value = 2
        mock_part = MagicMock()
        mock_part.Precision.confusion.return_value = 1e-7
        # _sketch_health_check does a call-time `import Part` for
        # Part.Precision.confusion() -- the autouse mock_freecad fixture
        # (tests/unit/conftest.py) replaces sys.modules['Part'] with a bare
        # types.ModuleType per test (see _freecad_mocks.py's reset_mocks
        # docstring), which has no .Precision, so it must be patched here.
        with patch.object(base_module, 'FreeCAD') as mock_fc, \
             patch.dict(sys.modules, {'Part': mock_part}):
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "Degenerate geometry: 2 found" in result
        assert "removeDegeneratedGeometries" in result

    def test_missing_vh_constraints_surface_with_fix_call(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.MissingVerticalHorizontalConstraints = [(0, 0, -1, 0, 2)]
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "Missing Vertical/Horizontal constraints: 1 found" in result
        assert "makeMissingVerticalHorizontal" in result

    def test_missing_equality_constraints_surface_with_fix_call(self):
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.MissingLineEqualityConstraints = [(1, 0, 2, 0)]
        sketch.MissingRadiusConstraints = [(3, 0, 4, 0)]
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "Missing equality constraints: 1 line-length, 1 radius" in result
        assert "makeMissingEquality" in result

    def test_check_unavailable_does_not_crash_the_whole_report(self):
        """One check raising (e.g. an older FC build missing a method) must
        degrade gracefully, not take down the rest of the report."""
        handler = make_base_handler()
        sketch = _make_clean_sketch()
        sketch.evaluateConstraints.side_effect = AttributeError("no such method")
        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler._sketch_health_check(sketch)
        assert "Invalid constraints: check unavailable" in result
        # the rest of the report still ran
        assert "Missing Vertical/Horizontal constraints: none found" in result


# ---------------------------------------------------------------------------
# SketchOpsHandler.health_check (MCP entry point)
# ---------------------------------------------------------------------------

class TestHealthCheckEntryPoint:

    def test_sketch_not_found(self):
        handler = make_sketch_handler()
        mock_doc = MagicMock()
        mock_doc.getObject.return_value = None
        mock_doc.getObjectsByLabel.return_value = []
        # get_document() (used by health_check) lives on BaseHandler and
        # reads base_module's FreeCAD -- both modules' FreeCAD must be
        # patched, matching the pattern in test_open_wire_diagnosis.py.
        with patch.object(sketch_ops_module, 'FreeCAD') as mock_fc, \
             patch.object(base_module, 'FreeCAD') as mock_fc2:
            mock_fc.ActiveDocument = mock_doc
            mock_fc2.ActiveDocument = mock_doc
            result = handler.health_check({'sketch_name': 'NoSuchSketch'})
        assert "not found" in result.lower()

    def test_wrong_object_type_rejected(self):
        handler = make_sketch_handler()
        not_a_sketch = MagicMock()
        not_a_sketch.TypeId = "Part::Box"
        mock_doc = MagicMock()
        mock_doc.getObject.return_value = not_a_sketch
        mock_doc.getObjectsByLabel.return_value = [not_a_sketch]
        with patch.object(sketch_ops_module, 'FreeCAD') as mock_fc, \
             patch.object(base_module, 'FreeCAD') as mock_fc2:
            mock_fc.ActiveDocument = mock_doc
            mock_fc2.ActiveDocument = mock_doc
            result = handler.health_check({'sketch_name': 'Box'})
        assert "not a sketch" in result.lower()

    def test_delegates_to_shared_health_check(self):
        handler = make_sketch_handler()
        sketch = _make_clean_sketch()
        mock_doc = MagicMock()
        mock_doc.getObject.return_value = sketch
        mock_doc.getObjectsByLabel.return_value = [sketch]
        with patch.object(sketch_ops_module, 'FreeCAD') as mock_fc, \
             patch.object(base_module, 'FreeCAD') as mock_fc2:
            mock_fc.ActiveDocument = mock_doc
            mock_fc2.ActiveDocument = mock_doc
            mock_fc2.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler.health_check({'sketch_name': 'Sketch'})
        assert "Health check for Sketch" in result
        assert "Verdict:" in result


# ---------------------------------------------------------------------------
# MeasurementOpsHandler.diagnose_invalid_shape (MCP entry point)
# ---------------------------------------------------------------------------

class TestDiagnoseInvalidShapeEntryPoint:

    def _resolve_stub(self, obj):
        """resolve_object normally does document/name lookup; stub it to
        hand back a fixed object so these tests focus on diagnose_invalid_shape's
        own logic, not resolve_object's (which has its own tests elsewhere)."""
        return (MagicMock(), obj, None)

    def test_valid_shape_short_circuits(self):
        handler = make_measurement_handler()
        obj = MagicMock()
        obj.Shape.isNull.return_value = False
        obj.Shape.isValid.return_value = True
        handler.resolve_object = MagicMock(return_value=(MagicMock(), obj, None))

        result = handler.diagnose_invalid_shape({'object_name': 'Box'})
        assert "isValid() = True" in result
        assert "No further diagnosis needed" in result

    def test_null_shape_reported_without_crashing(self):
        handler = make_measurement_handler()
        obj = MagicMock()
        obj.Shape.isNull.return_value = True
        handler.resolve_object = MagicMock(return_value=(MagicMock(), obj, None))

        result = handler.diagnose_invalid_shape({'object_name': 'Failed'})
        assert "null" in result.lower()

    def test_invalid_shape_with_no_sketch_upstream(self):
        handler = make_measurement_handler()
        obj = MagicMock()
        obj.Shape.isNull.return_value = False
        obj.Shape.isValid.return_value = False
        obj.OutList = []   # nothing upstream at all
        handler.resolve_object = MagicMock(return_value=(MagicMock(), obj, None))

        result = handler.diagnose_invalid_shape({'object_name': 'Weird'})
        assert "isValid() = False" in result
        assert "no Sketcher::SketchObject was found upstream" in result

    def test_invalid_shape_traces_to_upstream_sketch(self):
        handler = make_measurement_handler()
        sketch = _make_clean_sketch("RootCause")
        sketch.OpenVertices = [(1.0, 1.0, 0.0)]   # the actual problem

        obj = MagicMock()
        obj.Name = "Revolve"
        obj.Shape.isNull.return_value = False
        obj.Shape.isValid.return_value = False
        obj.OutList = [sketch]
        handler.resolve_object = MagicMock(return_value=(MagicMock(), obj, None))

        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler.diagnose_invalid_shape({'object_name': 'Revolve'})

        assert "isValid() = False" in result
        assert "Walking 1 upstream sketch(es)" in result
        assert "Health check for RootCause" in result
        assert "PROBLEMS FOUND" in result

    def test_object_not_found(self):
        handler = make_measurement_handler()
        handler.resolve_object = MagicMock(return_value=(None, None, "Object not found: Ghost"))
        result = handler.diagnose_invalid_shape({'object_name': 'Ghost'})
        assert result == "Object not found: Ghost"

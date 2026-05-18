"""
Tests for AICopilot/handlers/base.py — shared handler base class.

All FreeCAD dependencies are mocked via conftest.py.
"""

import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# Add AICopilot to path for imports
AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)


@pytest.fixture
def base_handler():
    """Create a BaseHandler with mocked dependencies."""
    # Need handlers.base to import properly
    if "handlers.base" in sys.modules:
        del sys.modules["handlers.base"]
    # Do NOT delete "handlers" (the package) — only delete submodules

    from handlers.base import BaseHandler
    server = MagicMock()
    log_fn = MagicMock()
    capture_fn = MagicMock(return_value={"state": "captured"})
    handler = BaseHandler(server=server, log_operation=log_fn, capture_state=capture_fn)
    return handler


@pytest.fixture
def base_handler_class():
    """Return the BaseHandler class for instantiation tests."""
    if "handlers.base" in sys.modules:
        del sys.modules["handlers.base"]
    # Do NOT delete "handlers" (the package) — only delete submodules
    from handlers.base import BaseHandler
    return BaseHandler


# ---------------------------------------------------------------------------
# Constructor and properties
# ---------------------------------------------------------------------------

class TestBaseHandlerInit:
    def test_default_init(self, base_handler_class):
        """Handler with no args should use noop fallbacks."""
        h = base_handler_class()
        assert h.server is None
        # noop log should not raise
        h._log_operation("test")
        # noop capture should return empty dict
        assert h._capture_state() == {}

    def test_init_with_server(self, base_handler):
        assert base_handler.server is not None

    def test_selector_property(self, base_handler):
        """selector should delegate to server.selector."""
        base_handler.server.selector = "mock_selector"
        assert base_handler.selector == "mock_selector"

    def test_selector_no_server(self, base_handler_class):
        h = base_handler_class(server=None)
        assert h.selector is None


# ---------------------------------------------------------------------------
# run_on_gui_thread
# ---------------------------------------------------------------------------

class TestRunOnGuiThread:
    def test_delegates_to_server(self, base_handler):
        """Should call server._run_on_gui_thread when available."""
        base_handler.server._run_on_gui_thread.return_value = '{"result": "ok"}'
        result = base_handler.run_on_gui_thread(lambda: "task", timeout=5.0)
        base_handler.server._run_on_gui_thread.assert_called_once()
        assert result == '{"result": "ok"}'

    def test_fallback_no_server(self, base_handler_class):
        """Without server, should run task directly."""
        h = base_handler_class(server=None)
        result = h.run_on_gui_thread(lambda: "direct_result")
        assert result == "direct_result"

    def test_fallback_error(self, base_handler_class):
        """Without server, exceptions should return error string."""
        h = base_handler_class(server=None)
        result = h.run_on_gui_thread(lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert "Error:" in result
        assert "boom" in result


# ---------------------------------------------------------------------------
# log_and_return
# ---------------------------------------------------------------------------

class TestLogAndReturn:
    def test_success(self, base_handler):
        result = base_handler.log_and_return("test_op", {"x": 1}, result="ok")
        assert result == "ok"
        base_handler._log_operation.assert_called_once()

    def test_error(self, base_handler):
        err = ValueError("test error")
        result = base_handler.log_and_return("test_op", {"x": 1}, error=err)
        assert "Error in test_op" in result
        assert "test error" in result
        # Should log both the operation and the error state
        assert base_handler._log_operation.call_count == 2
        base_handler._capture_state.assert_called_once()


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------

class TestGetDocument:
    def test_returns_active_document(self, base_handler, mock_freecad):
        mock_doc = MagicMock()
        mock_freecad.ActiveDocument = mock_doc
        assert base_handler.get_document() is mock_doc

    def test_returns_none_no_document(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        assert base_handler.get_document() is None

    def test_create_if_missing(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        new_doc = MagicMock()
        mock_freecad.newDocument.return_value = new_doc
        result = base_handler.get_document(create_if_missing=True)
        mock_freecad.newDocument.assert_called_once()
        new_doc.recompute.assert_called_once()
        assert result is new_doc

    def test_create_if_missing_exception(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        mock_freecad.newDocument.side_effect = RuntimeError("creation failed")
        result = base_handler.get_document(create_if_missing=True)
        assert result is None

    def test_no_create_when_not_missing(self, base_handler, mock_freecad):
        mock_doc = MagicMock()
        mock_freecad.ActiveDocument = mock_doc
        result = base_handler.get_document(create_if_missing=True)
        assert result is mock_doc
        mock_freecad.newDocument.assert_not_called()


# ---------------------------------------------------------------------------
# get_object
# ---------------------------------------------------------------------------

class TestGetObject:
    def _make_doc(self, objects_dict):
        """Create a mock doc with getObject and getObjectsByLabel."""
        doc = MagicMock()
        doc.getObject = lambda name: objects_dict.get(name)
        doc.getObjectsByLabel = lambda label: [
            o for o in objects_dict.values() if getattr(o, 'Label', None) == label
        ]
        return doc

    def test_find_by_name(self, base_handler):
        obj = MagicMock(Label="MyBox")
        doc = self._make_doc({"Box": obj})
        assert base_handler.get_object("Box", doc) is obj

    def test_find_by_label(self, base_handler):
        obj = MagicMock(Label="LeftTab")
        doc = self._make_doc({"Box001": obj})
        assert base_handler.get_object("LeftTab", doc) is obj

    def test_not_found(self, base_handler):
        doc = self._make_doc({})
        assert base_handler.get_object("Nonexistent", doc) is None

    def test_no_doc_no_active(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        assert base_handler.get_object("Foo") is None

    def test_uses_active_doc_when_none(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Obj")
        doc = self._make_doc({"Obj": obj})
        mock_freecad.ActiveDocument = doc
        assert base_handler.get_object("Obj") is obj

    # ----------------------------------------------------------------
    # Ambiguous-label cases.
    #
    # FreeCAD allows multiple objects to share the same Label (only
    # internal Name is unique).  get_object's label fallback does
    # `results[0] if results else None`, so when two objects share a
    # label, the *first one in document order* wins silently.  Callers
    # like move_object / rotate_object then operate on the wrong object
    # — a frustrating, hard-to-debug failure.
    #
    # These tests pin the current "first match wins" behavior so a
    # future fix that errors on ambiguity is deliberate, and document
    # that callers asking for "MyBox" when there are two of them get
    # nondeterministic surgery on the wrong one.
    # ----------------------------------------------------------------

    def test_ambiguous_label_returns_first_match(self, base_handler):
        """Two objects with the same Label — first match wins silently.

        FreeCAD lets users (or tools) duplicate labels.  Today the
        fallback path picks the first one without warning.  Pinning
        the behavior so a future change to raise/warn is intentional.
        """
        obj1 = MagicMock(Label="Tab")
        obj1.Name = "Box001"
        obj2 = MagicMock(Label="Tab")
        obj2.Name = "Box002"
        # Internal name lookup misses (caller passed the Label, not Name)
        doc = MagicMock()
        doc.getObject = lambda n: None
        doc.getObjectsByLabel = lambda label: [obj1, obj2] if label == "Tab" else []

        result = base_handler.get_object("Tab", doc)
        # Today: first match wins.  This is silent — a downstream
        # move_object call will move obj1 even if the user meant obj2.
        assert result is obj1

    def test_name_takes_precedence_over_label(self, base_handler):
        """If something matches by Name AND something else by Label,
        Name wins — getObject is tried first and short-circuits the
        label fallback.  Catches a refactor that swaps the order."""
        named = MagicMock(Label="A")
        named.Name = "Box"
        labeled = MagicMock(Label="Box")
        labeled.Name = "Cyl"
        doc = MagicMock()
        doc.getObject = lambda n: {"Box": named}.get(n)
        doc.getObjectsByLabel = lambda label: (
            [labeled] if label == "Box" else []
        )
        # Asking for "Box" should return the object Named "Box",
        # not the one Labeled "Box".
        assert base_handler.get_object("Box", doc) is named


# ---------------------------------------------------------------------------
# recompute
# ---------------------------------------------------------------------------

class TestRecompute:
    def test_recomputes_given_doc(self, base_handler):
        doc = MagicMock()
        base_handler.recompute(doc)
        doc.recompute.assert_called_once()

    def test_recomputes_active_doc(self, base_handler, mock_freecad):
        doc = MagicMock()
        mock_freecad.ActiveDocument = doc
        base_handler.recompute()
        doc.recompute.assert_called_once()

    def test_no_doc_no_crash(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        base_handler.recompute()  # should not raise


# ---------------------------------------------------------------------------
# save_before_risky_op
# ---------------------------------------------------------------------------

class TestSaveBeforeRiskyOp:
    def test_saves_when_filename_set(self, base_handler, mock_freecad):
        doc = MagicMock()
        doc.FileName = "/tmp/test.FCStd"
        mock_freecad.ActiveDocument = doc
        base_handler.save_before_risky_op()
        doc.save.assert_called_once()

    def test_skips_when_no_filename(self, base_handler, mock_freecad):
        doc = MagicMock()
        doc.FileName = ""
        mock_freecad.ActiveDocument = doc
        base_handler.save_before_risky_op()
        doc.save.assert_not_called()

    def test_skips_when_no_doc(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        base_handler.save_before_risky_op()  # should not raise

    def test_exception_is_swallowed(self, base_handler, mock_freecad):
        doc = MagicMock()
        doc.FileName = "/tmp/test.FCStd"
        doc.save.side_effect = IOError("disk full")
        mock_freecad.ActiveDocument = doc
        base_handler.save_before_risky_op()  # should not raise


# ---------------------------------------------------------------------------
# check_complexity
# ---------------------------------------------------------------------------

class TestCheckComplexity:
    def _make_obj(self, n_solids, n_faces):
        obj = MagicMock()
        obj.Shape.Solids = [None] * n_solids
        obj.Shape.Faces = [None] * n_faces
        return obj

    def test_below_threshold_returns_none(self, base_handler):
        obj = self._make_obj(1, 10)
        assert base_handler.check_complexity([obj]) is None

    def test_above_solids_threshold(self, base_handler):
        obj = self._make_obj(600, 10)
        result = base_handler.check_complexity([obj])
        assert "WARNING" in result
        assert "600 solids" in result

    def test_above_faces_threshold(self, base_handler):
        obj = self._make_obj(1, 15000)
        result = base_handler.check_complexity([obj])
        assert "WARNING" in result
        assert "15000 faces" in result

    def test_multiple_objects_sum(self, base_handler):
        objs = [self._make_obj(300, 5000), self._make_obj(300, 5000)]
        result = base_handler.check_complexity(objs)
        assert "WARNING" in result

    def test_object_without_shape(self, base_handler):
        obj = MagicMock(spec=[])  # no Shape attribute
        assert base_handler.check_complexity([obj]) is None

    def test_custom_thresholds(self, base_handler):
        obj = self._make_obj(5, 50)
        assert base_handler.check_complexity([obj], max_solids=3) is not None
        assert base_handler.check_complexity([obj], max_solids=10) is None

    # ----------------------------------------------------------------
    # Threshold-boundary cases.  The existing tests use 600 solids and
    # 15000 faces — well past the defaults, so they don't pin which
    # side of the boundary the check lives on.  The check is `>`, not
    # `>=`: max_solids=500 must NOT warn at 500, MUST warn at 501.
    # ----------------------------------------------------------------

    def test_exactly_at_max_solids_no_warning(self, base_handler):
        """500 solids is exactly the default cap and must NOT warn,
        because the comparison is `>` (strict greater-than)."""
        obj = self._make_obj(500, 0)
        assert base_handler.check_complexity([obj]) is None

    def test_one_over_max_solids_warns(self, base_handler):
        """501 solids is the first value that triggers the warning."""
        obj = self._make_obj(501, 0)
        result = base_handler.check_complexity([obj])
        assert result is not None
        assert "501 solids" in result

    def test_exactly_at_max_faces_no_warning(self, base_handler):
        """10000 faces is exactly the default cap and must NOT warn."""
        obj = self._make_obj(0, 10000)
        assert base_handler.check_complexity([obj]) is None

    def test_one_over_max_faces_warns(self, base_handler):
        """10001 faces is the first value that triggers the warning."""
        obj = self._make_obj(0, 10001)
        result = base_handler.check_complexity([obj])
        assert result is not None
        assert "10001 faces" in result

    def test_zero_objects_no_warning(self, base_handler):
        """Empty input list — no objects, no warning."""
        assert base_handler.check_complexity([]) is None

    def test_sum_just_crosses_threshold(self, base_handler):
        """Boundary in the multi-object sum case: 250+251 = 501 solids
        across two objects crosses the threshold; 250+250 = 500 does not."""
        crosses = [self._make_obj(250, 0), self._make_obj(251, 0)]
        assert base_handler.check_complexity(crosses) is not None
        # The sum-exactly-at-threshold case must NOT warn.
        boundary = [self._make_obj(250, 0), self._make_obj(250, 0)]
        assert base_handler.check_complexity(boundary) is None


# ---------------------------------------------------------------------------
# find_body / find_body_for_object
# ---------------------------------------------------------------------------

class TestFindBody:
    def _make_doc_with_objects(self, objects, mock_freecad):
        doc = MagicMock()
        doc.Objects = objects
        mock_freecad.ActiveDocument = doc
        return doc

    def test_find_body(self, base_handler, mock_freecad):
        body = MagicMock()
        body.TypeId = "PartDesign::Body"
        other = MagicMock()
        other.TypeId = "Part::Box"
        doc = self._make_doc_with_objects([other, body], mock_freecad)
        assert base_handler.find_body(doc) is body

    def test_find_body_none(self, base_handler, mock_freecad):
        other = MagicMock()
        other.TypeId = "Part::Box"
        doc = self._make_doc_with_objects([other], mock_freecad)
        assert base_handler.find_body(doc) is None

    def test_find_body_uses_active_doc(self, base_handler, mock_freecad):
        body = MagicMock()
        body.TypeId = "PartDesign::Body"
        self._make_doc_with_objects([body], mock_freecad)
        assert base_handler.find_body() is body

    def test_find_body_no_doc(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        assert base_handler.find_body() is None

    def test_find_body_for_object(self, base_handler, mock_freecad):
        obj = MagicMock()
        body = MagicMock()
        body.TypeId = "PartDesign::Body"
        body.Group = [obj]
        other_body = MagicMock()
        other_body.TypeId = "PartDesign::Body"
        other_body.Group = []
        doc = self._make_doc_with_objects([other_body, body], mock_freecad)
        assert base_handler.find_body_for_object(obj, doc) is body

    def test_find_body_for_object_not_found(self, base_handler, mock_freecad):
        obj = MagicMock()
        body = MagicMock()
        body.TypeId = "PartDesign::Body"
        body.Group = []
        doc = self._make_doc_with_objects([body], mock_freecad)
        assert base_handler.find_body_for_object(obj, doc) is None

    def test_find_body_for_object_no_doc(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        assert base_handler.find_body_for_object(MagicMock()) is None


# ---------------------------------------------------------------------------
# create_body_if_needed
# ---------------------------------------------------------------------------

class TestCreateBodyIfNeeded:
    def test_returns_existing_body(self, base_handler, mock_freecad):
        body = MagicMock()
        body.TypeId = "PartDesign::Body"
        doc = MagicMock()
        doc.Objects = [body]
        mock_freecad.ActiveDocument = doc
        assert base_handler.create_body_if_needed() is body

    def test_creates_body_when_missing(self, base_handler, mock_freecad):
        doc = MagicMock()
        doc.Objects = []
        new_body = MagicMock()
        doc.addObject.return_value = new_body
        mock_freecad.ActiveDocument = doc
        result = base_handler.create_body_if_needed()
        doc.addObject.assert_called_once_with("PartDesign::Body", "Body")
        assert result is new_body

    def test_creates_document_if_needed(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        new_doc = MagicMock()
        new_doc.Objects = []
        new_body = MagicMock()
        new_doc.addObject.return_value = new_body
        mock_freecad.newDocument.return_value = new_doc
        result = base_handler.create_body_if_needed()
        assert result is new_body

    def test_returns_none_when_no_doc_possible(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        mock_freecad.newDocument.side_effect = RuntimeError("no display")
        result = base_handler.create_body_if_needed()
        assert result is None


# ---------------------------------------------------------------------------
# _diagnose_open_wires
# ---------------------------------------------------------------------------

class TestDiagnoseOpenWires:
    def test_no_issues(self, base_handler):
        sketch = MagicMock()
        sketch.getOpenVertices.return_value = []
        sketch.detectMissingPointOnPointConstraints.return_value = 0
        result = base_handler._diagnose_open_wires(sketch)
        assert result == ""

    def test_open_vertices_with_match(self, base_handler, mock_freecad):
        vertex = MagicMock()
        vertex.x = 10.0
        vertex.y = 20.0
        sketch = MagicMock()
        sketch.getOpenVertices.return_value = [vertex]
        sketch.Name = "Sketch"
        sketch.GeometryCount = 1
        sketch.getConstruction.return_value = False

        geo = MagicMock()
        geo.StartPoint = MagicMock(x=10.0, y=20.0)
        geo.EndPoint = MagicMock(x=30.0, y=20.0)
        sketch.Geometry = [geo]

        # Need FreeCAD.Vector for distance calc
        mock_freecad.Vector = lambda x, y, z: MagicMock(Length=((x**2 + y**2)**0.5))

        sketch.detectMissingPointOnPointConstraints.return_value = 0

        result = base_handler._diagnose_open_wires(sketch)
        assert "open endpoint" in result
        assert "geo_id=0" in result

    def test_open_vertices_no_match(self, base_handler, mock_freecad):
        vertex = MagicMock()
        vertex.x = 100.0
        vertex.y = 200.0
        sketch = MagicMock()
        sketch.getOpenVertices.return_value = [vertex]
        sketch.GeometryCount = 1
        sketch.getConstruction.return_value = False

        geo = MagicMock()
        geo.StartPoint = MagicMock(x=0.0, y=0.0)
        geo.EndPoint = MagicMock(x=10.0, y=0.0)
        sketch.Geometry = [geo]

        mock_freecad.Vector = lambda x, y, z: MagicMock(Length=((x**2 + y**2)**0.5))

        sketch.detectMissingPointOnPointConstraints.return_value = 0

        result = base_handler._diagnose_open_wires(sketch)
        assert "Dangling point" in result
        assert "no matching geometry" in result

    def test_suggested_fixes(self, base_handler, mock_freecad):
        sketch = MagicMock()
        sketch.getOpenVertices.return_value = []
        sketch.Name = "TestSketch"

        sketch.detectMissingPointOnPointConstraints.return_value = 1
        constraint = MagicMock()
        constraint.First = 0
        constraint.FirstPos = 2
        constraint.Second = 1
        constraint.SecondPos = 1
        sketch.getMissingPointOnPointConstraints.return_value = [constraint]

        result = base_handler._diagnose_open_wires(sketch)
        assert "suggested fix" in result
        assert "Coincident" in result
        assert "geo_id1=0" in result

    def test_getOpenVertices_unavailable(self, base_handler):
        sketch = MagicMock()
        sketch.getOpenVertices.side_effect = AttributeError("not available")
        sketch.detectMissingPointOnPointConstraints.return_value = 0
        result = base_handler._diagnose_open_wires(sketch)
        assert "getOpenVertices unavailable" in result

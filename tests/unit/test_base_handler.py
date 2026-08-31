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

    def test_returns_none_when_no_document(self, base_handler, mock_freecad):
        """get_document() never auto-creates — callers must check for None."""
        mock_freecad.ActiveDocument = None
        result = base_handler.get_document()
        assert result is None
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
    # internal Name is unique).  Previously get_object's label fallback
    # did `results[0] if results else None`, so when two objects shared
    # a label, the *first one in document order* won silently and
    # callers like move_object / rotate_object operated on the wrong
    # object — a frustrating, hard-to-debug failure that could destroy
    # work via a Boolean cut on the unintended solid.
    #
    # As of this change, get_object raises ValueError on ambiguous
    # label matches, naming the candidates so the caller can retry
    # with the unique internal Name.  The outer handler try/except
    # surfaces this as a clear MCP error response.
    # ----------------------------------------------------------------

    def test_ambiguous_label_raises_with_candidate_names(self, base_handler):
        """Two objects sharing a Label → ValueError listing both internal
        Names, so the caller can retry unambiguously.  This is the fix
        for the silent first-match-wins bug — the test was pinned to the
        old behavior at commit 628f5a9 and is updated here alongside the
        fix to base.get_object."""
        obj1 = MagicMock(Label="Tab")
        obj1.Name = "Box001"
        obj2 = MagicMock(Label="Tab")
        obj2.Name = "Box002"
        doc = MagicMock()
        doc.getObject = lambda n: None
        doc.getObjectsByLabel = lambda label: [obj1, obj2] if label == "Tab" else []

        with pytest.raises(ValueError, match="Ambiguous label 'Tab'"):
            base_handler.get_object("Tab", doc)

        # Error message must name BOTH candidates so the caller knows
        # what their disambiguation options are.
        try:
            base_handler.get_object("Tab", doc)
        except ValueError as e:
            msg = str(e)
            assert "Box001" in msg
            assert "Box002" in msg
            assert "internal Name" in msg  # nudge toward the right fix

    def test_unambiguous_label_still_works(self, base_handler):
        """A label that matches exactly one object returns that object —
        the fix only changes the ambiguous-match path."""
        obj = MagicMock(Label="UniqueLabel")
        obj.Name = "Box042"
        doc = MagicMock()
        doc.getObject = lambda n: None
        doc.getObjectsByLabel = lambda label: [obj] if label == "UniqueLabel" else []
        assert base_handler.get_object("UniqueLabel", doc) is obj

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
# resolve_object — the shared doc/object/attr preamble extracted from
# ~200 call sites across every handler file.
# ---------------------------------------------------------------------------

class TestResolveObject:
    def _make_doc(self, objects_dict):
        doc = MagicMock()
        doc.getObject = lambda name: objects_dict.get(name)
        doc.getObjectsByLabel = lambda label: [
            o for o in objects_dict.values() if getattr(o, 'Label', None) == label
        ]
        return doc

    def test_success_no_attr_check(self, base_handler):
        obj = MagicMock(Label="Box")
        doc = self._make_doc({"Box": obj})
        got_doc, got_obj, err = base_handler.resolve_object("Box", doc)
        assert got_doc is doc
        assert got_obj is obj
        assert err is None

    def test_no_active_document(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        doc, obj, err = base_handler.resolve_object("Box")
        assert doc is None
        assert obj is None
        assert err == "No active document"

    def test_fetches_active_document_when_none_given(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Box")
        active_doc = self._make_doc({"Box": obj})
        mock_freecad.ActiveDocument = active_doc
        doc, got_obj, err = base_handler.resolve_object("Box")
        assert doc is active_doc
        assert got_obj is obj
        assert err is None

    def test_object_not_found_default_noun(self, base_handler):
        doc = self._make_doc({})
        got_doc, obj, err = base_handler.resolve_object("Ghost", doc)
        assert got_doc is doc
        assert obj is None
        assert err == "Object not found: Ghost"

    def test_object_not_found_custom_noun(self, base_handler):
        """Callers preserve their own noun (Sketch/Spreadsheet/etc.)
        instead of a flattened generic 'Object' wording."""
        doc = self._make_doc({})
        _, _, err = base_handler.resolve_object("Ghost", doc, noun="Sketch")
        assert err == "Sketch not found: Ghost"

    def test_attr_check_passes_when_present(self, base_handler):
        obj = MagicMock(Label="Box")
        obj.Shape = MagicMock()
        doc = self._make_doc({"Box": obj})
        got_doc, got_obj, err = base_handler.resolve_object("Box", doc, attr="Shape")
        assert got_obj is obj
        assert err is None

    def test_attr_check_fails_when_missing(self, base_handler):
        obj = MagicMock(Label="Box", spec=["Label"])  # no Shape attribute
        doc = self._make_doc({"Box": obj})
        doc_out, obj_out, err = base_handler.resolve_object("Box", doc, attr="Shape", noun="Object")
        # obj is still returned even on attr failure -- caller may want it
        # for a more specific message, but must treat err as authoritative.
        assert obj_out is obj
        assert err == "Object Box has no Shape property"

    def test_attr_check_or_semantics_with_tuple(self, base_handler):
        """mesh_ops needs 'has Mesh OR Shape' -- a tuple means any one
        of the given attributes satisfies the check."""
        obj = MagicMock(Label="M", spec=["Label", "Mesh"])  # has Mesh, no Shape
        doc = self._make_doc({"M": obj})
        _, _, err = base_handler.resolve_object("M", doc, attr=("Mesh", "Shape"))
        assert err is None

    def test_attr_check_tuple_fails_when_none_present(self, base_handler):
        obj = MagicMock(Label="M", spec=["Label"])  # neither Mesh nor Shape
        doc = self._make_doc({"M": obj})
        _, _, err = base_handler.resolve_object("M", doc, attr=("Mesh", "Shape"), noun="Object")
        assert err == "Object M has no Mesh or Shape property"

    def test_type_id_check_passes_when_matching(self, base_handler):
        obj = MagicMock(Label="Params", TypeId="App::VarSet")
        doc = self._make_doc({"Params": obj})
        got_doc, got_obj, err = base_handler.resolve_object(
            "Params", doc, noun="VarSet", type_id="App::VarSet")
        assert got_obj is obj
        assert err is None

    def test_type_id_check_fails_when_mismatched(self, base_handler):
        """The wrong-type message reuses `noun` (a friendly name like
        'VarSet'), not the raw TypeId string -- callers rely on this exact
        wording (e.g. varset_ops.py's "is not a VarSet" tests)."""
        obj = MagicMock(Label="Box1", TypeId="Part::Feature")
        doc = self._make_doc({"Box1": obj})
        got_doc, got_obj, err = base_handler.resolve_object(
            "Box1", doc, noun="VarSet", type_id="App::VarSet")
        assert got_obj is obj  # still returned, same convention as attr check
        assert err == "Object Box1 is not a VarSet"

    def test_type_id_check_or_semantics_with_tuple(self, base_handler):
        obj = MagicMock(Label="Sheet1", TypeId="Spreadsheet::Sheet")
        doc = self._make_doc({"Sheet1": obj})
        _, _, err = base_handler.resolve_object(
            "Sheet1", doc, noun="Container",
            type_id=("App::VarSet", "Spreadsheet::Sheet"))
        assert err is None

    def test_type_id_check_runs_before_attr_check(self, base_handler):
        """A wrong-type object that also lacks the checked attr should
        report the type mismatch, not the missing-attribute error."""
        obj = MagicMock(Label="Box1", TypeId="Part::Feature", spec=["Label", "TypeId"])
        doc = self._make_doc({"Box1": obj})
        _, _, err = base_handler.resolve_object(
            "Box1", doc, noun="VarSet", type_id="App::VarSet", attr="Shape")
        assert err == "Object Box1 is not a VarSet"

    def test_type_id_none_skips_check(self, base_handler):
        """type_id=None (the default) must not change behavior for any of
        the ~200 existing call sites that don't pass it."""
        obj = MagicMock(Label="Box1", TypeId="Part::Feature")
        doc = self._make_doc({"Box1": obj})
        _, got_obj, err = base_handler.resolve_object("Box1", doc)
        assert got_obj is obj
        assert err is None

    def test_ambiguous_label_propagates_valueerror(self, base_handler):
        """resolve_object must NOT swallow get_object's ambiguous-label
        ValueError -- it propagates to the caller's own try/except,
        exactly as it did before this helper existed."""
        obj1 = MagicMock(Label="Tab")
        obj1.Name = "Box001"
        obj2 = MagicMock(Label="Tab")
        obj2.Name = "Box002"
        doc = MagicMock()
        doc.getObject = lambda n: None
        doc.getObjectsByLabel = lambda label: [obj1, obj2] if label == "Tab" else []

        with pytest.raises(ValueError, match="Ambiguous label 'Tab'"):
            base_handler.resolve_object("Tab", doc)


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
# find_assembly
# ---------------------------------------------------------------------------

class TestFindAssembly:
    def _make_doc_with_objects(self, objects, mock_freecad):
        doc = MagicMock()
        doc.Objects = objects
        mock_freecad.ActiveDocument = doc
        return doc

    def test_find_assembly(self, base_handler, mock_freecad):
        assembly = MagicMock()
        assembly.TypeId = "Assembly::AssemblyObject"
        other = MagicMock()
        other.TypeId = "Part::Box"
        doc = self._make_doc_with_objects([other, assembly], mock_freecad)
        assert base_handler.find_assembly(doc) is assembly

    def test_find_assembly_none(self, base_handler, mock_freecad):
        other = MagicMock()
        other.TypeId = "Part::Box"
        doc = self._make_doc_with_objects([other], mock_freecad)
        assert base_handler.find_assembly(doc) is None

    def test_find_assembly_uses_active_doc(self, base_handler, mock_freecad):
        assembly = MagicMock()
        assembly.TypeId = "Assembly::AssemblyObject"
        self._make_doc_with_objects([assembly], mock_freecad)
        assert base_handler.find_assembly() is assembly

    def test_find_assembly_no_doc(self, base_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        assert base_handler.find_assembly() is None

    def test_find_assembly_returns_first_when_multiple(self, base_handler, mock_freecad):
        """Pinning behavior: with more than one assembly in the document,
        the first one found wins (same 'grab first' convention as find_body —
        no error, no disambiguation)."""
        first = MagicMock()
        first.TypeId = "Assembly::AssemblyObject"
        second = MagicMock()
        second.TypeId = "Assembly::AssemblyObject"
        doc = self._make_doc_with_objects([first, second], mock_freecad)
        assert base_handler.find_assembly(doc) is first


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

    def test_returns_none_when_no_document(self, base_handler, mock_freecad):
        """No active document → None; no auto-create."""
        mock_freecad.ActiveDocument = None
        result = base_handler.create_body_if_needed()
        assert result is None
        mock_freecad.newDocument.assert_not_called()


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


class TestMmMinToMmS:
    """mm_min_to_mm_s is the single source of truth for the mm/min -> mm/s
    divide-by-60 that used to be hand-written at 6 call sites across
    cam_tool_controllers.py and ocl_surface_op.py (H13). These tests pin
    its behavior directly; the parity tests below confirm every call site
    actually imports this same function rather than a local copy."""

    def test_typical_value(self):
        from handlers.base import mm_min_to_mm_s
        assert mm_min_to_mm_s(1200) == 20.0

    def test_zero(self):
        from handlers.base import mm_min_to_mm_s
        assert mm_min_to_mm_s(0) == 0.0

    def test_accepts_string_numeric(self):
        """Call sites pass args['feed_rate'] straight from JSON — could be
        an int, float, or (if a caller stringifies) a numeric string."""
        from handlers.base import mm_min_to_mm_s
        assert mm_min_to_mm_s("600") == 10.0

    def test_negative_value_not_silently_clamped(self):
        """Not a valid feed rate, but the conversion itself must not hide
        it — validation is the caller's job, not this arithmetic helper's."""
        from handlers.base import mm_min_to_mm_s
        assert mm_min_to_mm_s(-60) == -1.0


class TestMmMinToMmSParity:
    """Every former hand-written /60.0 call site must resolve to the shared
    handlers.base.mm_min_to_mm_s — not a separately-maintained copy that
    could drift.

    Deliberately NOT an `is` identity check: several other test files in
    this suite (including this one's own base_handler/base_handler_class
    fixtures) do `del sys.modules["handlers.base"]` + reimport to force a
    clean module for their own tests, which mints a *new* function object.
    Whether that reimport has happened yet by the time this test runs
    depends on pytest's collection/execution order — a real prior instance
    of this exact class of flakiness is documented in this repo's git
    history (H9). __module__ survives that churn (it's a string baked in
    at function-definition time, not a live reference), so it proves "no
    local reimplementation exists in this module's namespace" without
    being sensitive to which particular handlers.base object is currently
    cached in sys.modules.
    """

    def test_cam_tool_controllers_imports_the_shared_function(self):
        import handlers.cam_tool_controllers as cam_tool_controllers_module
        assert cam_tool_controllers_module.mm_min_to_mm_s.__module__ == "handlers.base"
        assert cam_tool_controllers_module.mm_min_to_mm_s(1200) == 20.0

    def test_ocl_surface_op_imports_the_shared_function(self):
        # ocl_surface_op.py does `import Path` (FreeCAD's CAM module) at
        # module level; _freecad_mocks registers a stand-in in sys.modules
        # before that import resolves, same as test_ocl_surface_op.py does.
        from tests.unit._freecad_mocks import mock_FreeCAD, reset_mocks  # noqa: F401
        import ocl_surface_op as ocl_surface_op_module
        assert ocl_surface_op_module.mm_min_to_mm_s.__module__ == "handlers.base"
        assert ocl_surface_op_module.mm_min_to_mm_s(1200) == 20.0


class TestCheckFeatureStateParity:
    """_check_feature_state used to be defined only on PartDesignOpsHandler
    and hand-copied nowhere else, even though Part::Loft/Part::Sweep (created
    by PartOpsHandler) share the same FreeCAD State='Invalid'-on-failed-
    recompute mechanism (H13). Moved to BaseHandler so both handlers inherit
    the identical method rather than risking a second hand-written copy."""

    def test_partdesign_and_part_handlers_share_the_inherited_method(self):
        from handlers.partdesign_ops import PartDesignOpsHandler
        from handlers.part_ops import PartOpsHandler
        assert (
            PartDesignOpsHandler._check_feature_state
            is PartOpsHandler._check_feature_state
            is __import__("handlers.base", fromlist=["BaseHandler"]).BaseHandler._check_feature_state
        )

    def test_partdesign_ops_no_longer_defines_its_own_copy(self):
        """Guards against a future edit silently reintroducing a shadowing
        override on PartDesignOpsHandler that would defeat the parity check
        above without making it fail (an override with an identical body
        would still pass the `is` check above only if never re-added — this
        directly inspects the class's own __dict__ instead)."""
        from handlers.partdesign_ops import PartDesignOpsHandler
        assert "_check_feature_state" not in PartDesignOpsHandler.__dict__


class TestBindExpression:
    """bind_expression() -- the resolve/setExpression/recompute/check-state
    shape hoisted out of VarSetOpsHandler.bind_property and
    SpreadsheetOpsHandler.bind_property, which used to each hand-copy it
    (the review finding: a future fix to one had to be reapplied to the
    other -- confirmed by the other session's full-review hardening
    varset_ops.py's copy with a PropertiesList existence check and a
    post-recompute state check that spreadsheet_ops.py's copy never got)."""

    def _make_doc(self, objects_dict):
        doc = MagicMock()
        doc.getObject = lambda name: objects_dict.get(name)
        doc.getObjectsByLabel = lambda label: [
            o for o in objects_dict.values() if getattr(o, 'Label', None) == label
        ]
        return doc

    def test_success_builds_expression_and_recomputes(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Cube", State=[])
        target = MagicMock(Label="Params")
        doc = self._make_doc({"Cube": obj, "Params": target})
        mock_freecad.ActiveDocument = doc
        result = base_handler.bind_expression(
            "Cube", "Length", "Params", "Width", target_noun="VarSet")
        assert result == "Bound Cube.Length to Params.Width"
        obj.setExpression.assert_called_once_with("Length", "Params.Width")
        doc.recompute.assert_called_once()

    def test_object_not_found(self, base_handler, mock_freecad):
        doc = self._make_doc({})
        mock_freecad.ActiveDocument = doc
        result = base_handler.bind_expression(
            "Ghost", "Length", "Params", "Width", target_noun="VarSet")
        assert result == "Object not found: Ghost"

    def test_target_not_found_uses_target_noun(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Cube")
        doc = self._make_doc({"Cube": obj})
        mock_freecad.ActiveDocument = doc
        result = base_handler.bind_expression(
            "Cube", "Length", "Ghost", "Width", target_noun="VarSet")
        assert result == "VarSet not found: Ghost"

    def test_missing_property_name_rejected(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Cube")
        target = MagicMock(Label="Params")
        doc = self._make_doc({"Cube": obj, "Params": target})
        mock_freecad.ActiveDocument = doc
        result = base_handler.bind_expression(
            "Cube", "", "Params", "Width", target_noun="VarSet")
        assert result == "property_name is required"
        obj.setExpression.assert_not_called()

    def test_validate_target_rejection_short_circuits(self, base_handler, mock_freecad):
        """A validate_target callback returning an error string must stop
        before setExpression/recompute -- e.g. varset_ops.py's TypeId and
        PropertiesList-existence checks."""
        obj = MagicMock(Label="Cube")
        target = MagicMock(Label="Params")
        doc = self._make_doc({"Cube": obj, "Params": target})
        mock_freecad.ActiveDocument = doc

        result = base_handler.bind_expression(
            "Cube", "Length", "Params", "DoesNotExist", target_noun="VarSet",
            validate_target=lambda doc, t: "Property not found on VarSet Params: DoesNotExist",
        )
        assert result == "Property not found on VarSet Params: DoesNotExist"
        obj.setExpression.assert_not_called()
        doc.recompute.assert_not_called()

    def test_validate_target_receives_doc_and_target(self, base_handler, mock_freecad):
        obj = MagicMock(Label="Cube", State=[])
        target = MagicMock(Label="Params")
        doc = self._make_doc({"Cube": obj, "Params": target})
        mock_freecad.ActiveDocument = doc
        seen = {}

        def _capture(d, t):
            seen['doc'] = d
            seen['target'] = t
            return None

        base_handler.bind_expression(
            "Cube", "Length", "Params", "Width", target_noun="VarSet",
            validate_target=_capture,
        )
        assert seen['doc'] is doc
        assert seen['target'] is target

    def test_invalid_state_after_recompute_reported_not_swallowed(self, base_handler, mock_freecad):
        """setExpression() doesn't validate the reference until recompute,
        and a failed recompute doesn't raise -- it marks State Invalid
        instead. Without _check_feature_state, a bad target_ref would
        report unconditional success (the exact bug the other session's
        review found and fixed only in varset_ops.py's copy)."""
        obj = MagicMock(Label="Cube", State=["Invalid"])
        target = MagicMock(Label="Params")
        doc = self._make_doc({"Cube": obj, "Params": target})
        mock_freecad.ActiveDocument = doc
        result = base_handler.bind_expression(
            "Cube", "Length", "Params", "Wdith", target_noun="VarSet")
        assert result.startswith("Error: expression bound but")
        assert "Invalid" in result
        obj.setExpression.assert_called_once()


class TestFindGeoForPoint:
    """M17: _find_geo_for_point's default tolerance=0.5 had no boundary
    coverage. best_dist starts at tolerance; comparison is `if d <
    best_dist:` — a point at EXACTLY the tolerance distance must NOT
    match (0.5 < 0.5 is False)."""

    def _make_sketch_with_start_point(self, x, y):
        sketch = MagicMock()
        sketch.GeometryCount = 1
        sketch.getConstruction.return_value = False
        geo = MagicMock()
        geo.StartPoint = MagicMock(x=x, y=y)
        geo.EndPoint = MagicMock(x=1000.0, y=1000.0)  # far away, never the match
        sketch.Geometry = [geo]
        return sketch

    def test_point_exactly_at_default_tolerance_is_not_matched(self, base_handler, mock_freecad):
        mock_freecad.Vector = lambda x, y, z: MagicMock(Length=((x ** 2 + y ** 2) ** 0.5))
        vertex = MagicMock(x=0.0, y=0.0)
        sketch = self._make_sketch_with_start_point(0.5, 0.0)

        result = base_handler._find_geo_for_point(sketch, vertex)

        assert result is None

    def test_point_just_inside_default_tolerance_is_matched(self, base_handler, mock_freecad):
        mock_freecad.Vector = lambda x, y, z: MagicMock(Length=((x ** 2 + y ** 2) ** 0.5))
        vertex = MagicMock(x=0.0, y=0.0)
        sketch = self._make_sketch_with_start_point(0.49, 0.0)

        result = base_handler._find_geo_for_point(sketch, vertex)

        assert result is not None
        geo_id, pos_id, dist = result
        assert geo_id == 0
        assert pos_id == 1
        assert abs(dist - 0.49) < 1e-9

    def test_point_just_outside_default_tolerance_is_not_matched(self, base_handler, mock_freecad):
        mock_freecad.Vector = lambda x, y, z: MagicMock(Length=((x ** 2 + y ** 2) ** 0.5))
        vertex = MagicMock(x=0.0, y=0.0)
        sketch = self._make_sketch_with_start_point(0.51, 0.0)

        result = base_handler._find_geo_for_point(sketch, vertex)

        assert result is None

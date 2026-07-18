"""
Tests for AICopilot/handlers/document_ops.py — document management handler.

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
def doc_handler():
    """Create a DocumentOpsHandler with mocked dependencies."""
    # Reimport only the specific submodules so they pick up conftest's
    # FreeCAD mock.  Do NOT delete "handlers" (the package) — that breaks
    # other test files that reference handlers.view_ops etc.
    for mod in ("handlers.base", "handlers.document_ops"):
        if mod in sys.modules:
            del sys.modules[mod]

    from handlers.document_ops import DocumentOpsHandler
    server = MagicMock()
    handler = DocumentOpsHandler(
        server=server,
        gui_task_queue=None,
        gui_response_queue=None,
        log_operation=MagicMock(),
        capture_state=MagicMock(),
    )
    return handler


@pytest.fixture
def mock_doc(mock_freecad):
    """Set up a mock active document with some objects."""
    doc = MagicMock()
    doc.Name = "TestDoc"
    doc.FileName = "/tmp/TestDoc.FCStd"

    obj1 = MagicMock()
    obj1.Name = "Box"
    obj1.Label = "Box"
    obj1.TypeId = "Part::Box"

    obj2 = MagicMock()
    obj2.Name = "Cylinder001"
    obj2.Label = "MyCylinder"
    obj2.TypeId = "Part::Cylinder"

    obj3 = MagicMock()
    obj3.Name = "Body"
    obj3.Label = "Body"
    obj3.TypeId = "PartDesign::Body"

    doc.Objects = [obj1, obj2, obj3]
    doc.getObject = lambda name: {"Box": obj1, "Cylinder001": obj2, "Body": obj3}.get(name)
    # BaseHandler.get_object()'s Label-fallback path calls this; without it,
    # it auto-vivifies as a MagicMock (truthy, len()==0 by default), so
    # get_object would return a fake "found" object for ANY name instead of
    # correctly falling through to None.
    doc.getObjectsByLabel = lambda label: [o for o in doc.Objects if o.Label == label]

    mock_freecad.ActiveDocument = doc
    return doc


# ---------------------------------------------------------------------------
# create_document
# ---------------------------------------------------------------------------

class TestCreateDocument:
    def test_create_with_server(self, doc_handler, mock_freecad):
        """With server, should delegate to _run_on_gui_thread."""
        doc_handler.server._run_on_gui_thread.return_value = json.dumps({
            "result": "Document 'MyDoc' created successfully"
        })
        result = doc_handler.create_document({"document_name": "MyDoc"})
        assert "MyDoc" in result
        assert "created" in result

    def test_create_no_gui_dispatcher_errors(self, mock_freecad):
        """Without a GUI thread dispatcher, create_document must refuse rather
        than call FreeCAD.newDocument() off-thread (hardened in 6a4b39f)."""
        for mod in ("handlers.base", "handlers.document_ops"):
            if mod in sys.modules:
                del sys.modules[mod]
        from handlers.document_ops import DocumentOpsHandler

        handler = DocumentOpsHandler(server=None)
        new_doc = MagicMock()
        mock_freecad.newDocument.return_value = new_doc

        result = handler.create_document({"document_name": "DirectDoc"})
        assert "Error" in result
        assert "GUI thread dispatcher" in result
        mock_freecad.newDocument.assert_not_called()

    def test_create_uses_name_fallback(self, doc_handler):
        """Should fall back to 'name' arg if 'document_name' missing."""
        doc_handler.server._run_on_gui_thread.return_value = json.dumps({
            "result": "Document 'Alt' created successfully"
        })
        result = doc_handler.create_document({"name": "Alt"})
        assert "Alt" in result

    def test_create_default_name(self, doc_handler):
        """Should default to 'Unnamed' if no name provided."""
        doc_handler.server._run_on_gui_thread.return_value = json.dumps({
            "result": "Document 'Unnamed' created successfully"
        })
        result = doc_handler.create_document({})
        assert "Unnamed" in result

    def test_create_exception(self, mock_freecad):
        """Exception during creation should return error."""
        for mod in ("handlers.base", "handlers.document_ops"):
            if mod in sys.modules:
                del sys.modules[mod]
        from handlers.document_ops import DocumentOpsHandler

        handler = DocumentOpsHandler(server=None)
        mock_freecad.newDocument.side_effect = RuntimeError("out of memory")
        result = handler.create_document({"document_name": "FailDoc"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# open_document
# ---------------------------------------------------------------------------

class TestOpenDocument:
    def test_open_success(self, doc_handler, mock_freecad):
        opened_doc = MagicMock()
        opened_doc.Name = "OpenedDoc"
        mock_freecad.openDocument = MagicMock(return_value=opened_doc)
        result = doc_handler.open_document({"filename": "/tmp/test.FCStd"})
        assert "OpenedDoc" in result

    def test_open_failure(self, doc_handler, mock_freecad):
        mock_freecad.openDocument = MagicMock(side_effect=FileNotFoundError("not found"))
        result = doc_handler.open_document({"filename": "/nonexistent.FCStd"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# save_document
# ---------------------------------------------------------------------------

class TestSaveDocument:
    def test_save_existing(self, doc_handler, mock_doc):
        result = doc_handler.save_document({})
        assert "saved" in result.lower()
        mock_doc.save.assert_called_once()

    def test_save_as(self, doc_handler, mock_doc):
        result = doc_handler.save_document({"filename": "/tmp/new.FCStd"})
        assert "saved as" in result.lower()
        mock_doc.saveAs.assert_called_once_with("/tmp/new.FCStd")

    def test_save_no_document(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.save_document({})
        assert "No active document" in result

    def test_save_error(self, doc_handler, mock_doc):
        mock_doc.save.side_effect = IOError("disk full")
        result = doc_handler.save_document({})
        assert "Error" in result


# ---------------------------------------------------------------------------
# list_objects
# ---------------------------------------------------------------------------

class TestListObjects:
    def test_list_all(self, doc_handler, mock_doc):
        result = json.loads(doc_handler.list_objects({}))
        assert result["total"] == 3
        assert result["returned"] == 3
        assert len(result["objects"]) == 3

    def test_list_with_limit(self, doc_handler, mock_doc):
        result = json.loads(doc_handler.list_objects({"limit": 2}))
        assert result["returned"] == 2
        assert result["total"] == 3

    def test_list_with_offset(self, doc_handler, mock_doc):
        result = json.loads(doc_handler.list_objects({"offset": 1}))
        assert result["returned"] == 2
        assert result["offset"] == 1

    def test_list_with_type_filter(self, doc_handler, mock_doc):
        result = json.loads(doc_handler.list_objects({"type_filter": "Part::"}))
        assert result["returned"] == 2  # Box and Cylinder, not Body

    def test_list_limit_capped_at_500(self, doc_handler, mock_doc):
        result = json.loads(doc_handler.list_objects({"limit": 9999}))
        assert result["limit"] == 500

    def test_list_no_document(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.list_objects({})
        assert "No active document" in result

    def test_list_object_label_fallback(self, doc_handler, mock_freecad):
        """If Label access raises, should fall back to Name."""
        doc = MagicMock()
        obj = MagicMock()
        obj.Name = "FallbackObj"
        obj.TypeId = "Part::Feature"
        type(obj).Label = PropertyMock(side_effect=RuntimeError("GUI only"))
        doc.Objects = [obj]
        mock_freecad.ActiveDocument = doc
        result = json.loads(doc_handler.list_objects({}))
        assert result["returned"] == 1
        assert result["objects"][0]["label"] == "FallbackObj"


# ---------------------------------------------------------------------------
# list_objects — degenerate pagination params
#
# The existing tests cover {limit=2, offset=1, limit=9999}.  They do NOT
# cover the threshold values the function branches on:
#   - limit == 0 (silently returns zero objects)
#   - limit < 0 (silently returns zero objects — `count >= -5` is True at 0)
#   - offset >= total (returns empty list, no flag)
#   - limit == exactly 500 (the cap, the > vs >= boundary)
#   - limit == 501 (the first value that gets clipped)
# A document with 200 objects + limit=0 should not silently return [];
# at minimum, the caller needs a way to detect truncation happened.
# This test family pins current behavior so refactors don't silently
# change semantics, and documents the "silent truncation" gap.
# ---------------------------------------------------------------------------

class TestListObjectsDegeneratePagination:
    def _make_doc(self, mock_freecad, n_objects):
        doc = MagicMock()
        objs = []
        for i in range(n_objects):
            obj = MagicMock()
            obj.Name = f"Obj{i:04d}"
            obj.Label = f"Obj{i:04d}"
            obj.TypeId = "Part::Feature"
            objs.append(obj)
        doc.Objects = objs
        mock_freecad.ActiveDocument = doc
        return doc

    def test_limit_zero_returns_empty(self, doc_handler, mock_freecad):
        """limit=0 returns zero objects (no warning, no error).
        Pinning behavior — if a caller passes 0 by accident they get
        an empty list back even though the document has content."""
        self._make_doc(mock_freecad, n_objects=5)
        result = json.loads(doc_handler.list_objects({"limit": 0}))
        assert result["total"] == 5
        assert result["returned"] == 0
        assert result["objects"] == []
        # Truncation is detectable: total > returned tells the caller
        # data was elided.  Without that comparison, the response looks
        # the same as an empty document.
        assert result["total"] > result["returned"]

    def test_limit_negative_returns_empty(self, doc_handler, mock_freecad):
        """limit=-5 collapses to zero objects.  The internal check
        `count >= limit` is True from the start, so the loop exits
        immediately.  Should arguably error; today it doesn't."""
        self._make_doc(mock_freecad, n_objects=5)
        result = json.loads(doc_handler.list_objects({"limit": -5}))
        assert result["total"] == 5
        assert result["returned"] == 0

    def test_offset_beyond_total_returns_empty(self, doc_handler, mock_freecad):
        """offset > total returns an empty list — caller must compare
        offset vs total to detect this case."""
        self._make_doc(mock_freecad, n_objects=3)
        result = json.loads(doc_handler.list_objects({"offset": 999}))
        assert result["total"] == 3
        assert result["returned"] == 0
        assert result["offset"] == 999

    def test_limit_exactly_at_cap_unchanged(self, doc_handler, mock_freecad):
        """limit=500 is the cap.  min(500, 500) == 500 — unchanged."""
        self._make_doc(mock_freecad, n_objects=10)
        result = json.loads(doc_handler.list_objects({"limit": 500}))
        assert result["limit"] == 500

    def test_limit_one_over_cap_clipped(self, doc_handler, mock_freecad):
        """limit=501 is the first value that gets clipped to 500."""
        self._make_doc(mock_freecad, n_objects=10)
        result = json.loads(doc_handler.list_objects({"limit": 501}))
        assert result["limit"] == 500

    def test_cap_actually_limits_returned_objects(self, doc_handler, mock_freecad):
        """The cap must limit the OUTPUT, not just the echoed `limit` field.
        With 501 objects and limit=500, exactly 500 come back and has_more is
        True. The other cap tests use 10 objects, so dropping min(...,500) on the
        slice would pass unnoticed there — this is the test that catches it."""
        self._make_doc(mock_freecad, n_objects=501)
        result = json.loads(doc_handler.list_objects({"limit": 500}))
        assert result["returned"] == 500
        assert result["has_more"] is True

    def test_limit_499_returns_499(self, doc_handler, mock_freecad):
        """Off-by-one below the cap: limit=499 returns 499 of 600."""
        self._make_doc(mock_freecad, n_objects=600)
        result = json.loads(doc_handler.list_objects({"limit": 499}))
        assert result["returned"] == 499
        assert result["has_more"] is True

    def test_truncation_detectable_via_total_minus_returned(self, doc_handler, mock_freecad):
        """When total > returned, the caller should be able to detect
        truncation.  The handler doesn't return a `truncated` flag, but
        `total - returned` works as a fallback.  Pinned so a future
        refactor doesn't drop the `total` field."""
        self._make_doc(mock_freecad, n_objects=600)
        result = json.loads(doc_handler.list_objects({}))  # default limit=100
        assert result["total"] == 600
        assert result["returned"] == 100
        assert result["total"] - result["returned"] == 500

    def _make_doc_mixed(self, mock_freecad, n_part, n_other):
        """Document with n_part Part::Feature objects and n_other Part::Box."""
        doc = MagicMock()
        objs = []
        for i in range(n_part):
            obj = MagicMock()
            obj.Name = f"Feat{i:04d}"
            obj.Label = f"Feat{i:04d}"
            obj.TypeId = "Part::Feature"
            objs.append(obj)
        for i in range(n_other):
            obj = MagicMock()
            obj.Name = f"Box{i:04d}"
            obj.Label = f"Box{i:04d}"
            obj.TypeId = "Part::Box"
            objs.append(obj)
        doc.Objects = objs
        mock_freecad.ActiveDocument = doc
        return doc

    def test_filtered_total_reflects_type_filter(self, doc_handler, mock_freecad):
        """With a type_filter active, filtered_total must count only matching
        objects, not the whole document — otherwise a caller can't compute
        page count. `total` still reports the document size."""
        self._make_doc_mixed(mock_freecad, n_part=3, n_other=7)
        result = json.loads(doc_handler.list_objects({"type_filter": "Box"}))
        assert result["total"] == 10            # whole document
        assert result["filtered_total"] == 7    # only Part::Box
        assert result["returned"] == 7
        assert result["has_more"] is False

    def test_has_more_true_when_more_pages(self, doc_handler, mock_freecad):
        """has_more must be True when matching objects extend past the page."""
        self._make_doc(mock_freecad, n_objects=250)
        result = json.loads(doc_handler.list_objects({"limit": 100, "offset": 0}))
        assert result["returned"] == 100
        assert result["filtered_total"] == 250
        assert result["has_more"] is True

    def test_has_more_false_on_last_page(self, doc_handler, mock_freecad):
        """has_more must be False once the page reaches the end."""
        self._make_doc(mock_freecad, n_objects=250)
        result = json.loads(doc_handler.list_objects({"limit": 100, "offset": 200}))
        assert result["returned"] == 50
        assert result["offset"] == 200
        assert result["has_more"] is False

    def test_objects_carry_visibility_state_and_dependency_graph(self, doc_handler, mock_freecad):
        """Each object must surface visibility, recompute State, and the
        InList/OutList dependency graph (guarded; null when unavailable) so
        callers don't need a per-object get_object_properties round-trip."""
        self._make_doc(mock_freecad, n_objects=1)
        result = json.loads(doc_handler.list_objects({}))
        obj = result["objects"][0]
        for key in ("visible", "state", "in_list", "out_list"):
            assert key in obj, f"missing {key} in list_objects entry"


# ---------------------------------------------------------------------------
# hide_object / show_object / delete_object
# ---------------------------------------------------------------------------

class TestObjectVisibility:
    def test_hide(self, doc_handler, mock_doc):
        result = doc_handler.hide_object({"object_name": "Box"})
        assert "Hidden" in result

    def test_hide_not_found(self, doc_handler, mock_doc):
        result = doc_handler.hide_object({"object_name": "Nonexistent"})
        assert "not found" in result.lower()

    def test_hide_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.hide_object({"object_name": "Box"})
        assert "No active document" in result

    def test_show(self, doc_handler, mock_doc):
        result = doc_handler.show_object({"object_name": "Box"})
        assert "Shown" in result

    def test_show_not_found(self, doc_handler, mock_doc):
        result = doc_handler.show_object({"object_name": "Nonexistent"})
        assert "not found" in result.lower()

    def test_show_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.show_object({"object_name": "Box"})
        assert "No active document" in result

    def test_hide_resolves_by_label(self, doc_handler, mock_doc):
        """hide_object previously bypassed get_object() via raw
        doc.getObject, which matches internal Name only — a caller-supplied
        Label ("MyCylinder", Name="Cylinder001") always came back
        "not found" even though every other handler resolves labels fine."""
        result = doc_handler.hide_object({"object_name": "MyCylinder"})
        assert "Hidden" in result


class TestDeleteObject:
    def test_delete(self, doc_handler, mock_doc):
        result = doc_handler.delete_object({"object_name": "Box"})
        assert "Deleted" in result
        mock_doc.removeObject.assert_called_once_with("Box")

    def test_delete_not_found(self, doc_handler, mock_doc):
        result = doc_handler.delete_object({"object_name": "Nope"})
        assert "not found" in result.lower()

    def test_delete_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.delete_object({"object_name": "Box"})
        assert "No active document" in result

    def test_delete_by_label_removes_the_internal_name_not_the_label(self, doc_handler, mock_doc):
        """removeObject() needs the internal Name — passing a resolved
        Label straight through (rather than obj.Name) would fail against a
        real FreeCAD document even though get_object() found the object."""
        result = doc_handler.delete_object({"object_name": "MyCylinder"})
        assert "Deleted" in result
        mock_doc.removeObject.assert_called_once_with("Cylinder001")


# ---------------------------------------------------------------------------
# undo / redo
# ---------------------------------------------------------------------------

class TestUndoRedo:
    def test_undo(self, doc_handler, mock_doc):
        fcgui = sys.modules["FreeCADGui"]
        fcgui.runCommand = MagicMock()
        result = doc_handler.undo({})
        assert "Undo" in result
        fcgui.runCommand.assert_called_with("Std_Undo")

    def test_undo_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.undo({})
        assert "No active document" in result

    def test_redo(self, doc_handler, mock_doc):
        fcgui = sys.modules["FreeCADGui"]
        fcgui.runCommand = MagicMock()
        result = doc_handler.redo({})
        assert "Redo" in result
        fcgui.runCommand.assert_called_with("Std_Redo")

    def test_redo_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.redo({})
        assert "No active document" in result


# ---------------------------------------------------------------------------
# activate_workbench / run_command
# ---------------------------------------------------------------------------

class TestWorkbenchAndCommand:
    def test_activate_workbench(self, doc_handler):
        fcgui = sys.modules["FreeCADGui"]
        fcgui.activateWorkbench = MagicMock()
        result = doc_handler.activate_workbench({"workbench_name": "PartWorkbench"})
        assert "PartWorkbench" in result

    def test_run_command(self, doc_handler):
        fcgui = sys.modules["FreeCADGui"]
        fcgui.runCommand = MagicMock()
        result = doc_handler.run_command({"command": "Std_ViewFitAll"})
        assert "Std_ViewFitAll" in result


# ---------------------------------------------------------------------------
# create_group
# ---------------------------------------------------------------------------

class TestCreateGroup:
    def test_create_empty_group(self, doc_handler, mock_doc):
        group = MagicMock()
        group.Name = "MyGroup"
        mock_doc.addObject.return_value = group
        result = doc_handler.create_group({"name": "MyGroup"})
        assert "MyGroup" in result
        assert "empty" in result.lower()

    def test_create_group_with_objects(self, doc_handler, mock_doc):
        group = MagicMock()
        group.Name = "Filled"
        mock_doc.addObject.return_value = group
        result = doc_handler.create_group({
            "name": "Filled",
            "objects": ["Box", "Cylinder001"],
        })
        assert "Filled" in result
        assert "2 objects" in result

    def test_create_group_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.create_group({"name": "Fail"})
        assert "No active document" in result


# ---------------------------------------------------------------------------
# make_link
# ---------------------------------------------------------------------------

class TestMakeLink:
    def test_create_link(self, doc_handler, mock_doc):
        link = MagicMock()
        link.Name = "Box_Link"
        mock_doc.addObject.return_value = link
        result = doc_handler.make_link({"object_name": "Box"})
        assert "link" in result.lower()
        assert "Box" in result

    def test_link_with_offset(self, doc_handler, mock_doc, mock_freecad):
        link = MagicMock()
        link.Name = "Box_Link"
        mock_doc.addObject.return_value = link
        mock_freecad.Vector = MagicMock()
        result = doc_handler.make_link({
            "object_name": "Box",
            "x": 50, "y": 0, "z": 0,
        })
        assert "link" in result.lower()

    def test_link_not_found(self, doc_handler, mock_doc):
        result = doc_handler.make_link({"object_name": "Nonexistent"})
        assert "not found" in result.lower()

    def test_link_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.make_link({"object_name": "Box"})
        assert "No active document" in result

    def test_link_resolves_by_label(self, doc_handler, mock_doc):
        """make_link previously bypassed get_object() via raw
        doc.getObject, so a caller-supplied Label ("MyCylinder",
        Name="Cylinder001") always came back "not found"."""
        link = MagicMock()
        link.Name = "MyCylinder_Link"
        mock_doc.addObject.return_value = link
        cylinder = mock_doc.getObject("Cylinder001")

        result = doc_handler.make_link({"object_name": "MyCylinder"})

        assert "not found" not in result.lower()
        assert link.LinkedObject is cylinder


# ---------------------------------------------------------------------------
# checkpoint / rollback_to_checkpoint
# ---------------------------------------------------------------------------

class TestCheckpointRollback:
    def test_checkpoint_saves_names(self, doc_handler, mock_doc):
        result = doc_handler.checkpoint({"name": "before_fillet"})
        assert "before_fillet" in result
        assert "3 objects" in result

    def test_checkpoint_default_name(self, doc_handler, mock_doc):
        result = doc_handler.checkpoint({})
        assert "default" in result

    def test_checkpoint_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.checkpoint({})
        assert "No active document" in result

    def test_rollback_removes_new_objects(self, doc_handler, mock_doc):
        # Save checkpoint with current 3 objects
        doc_handler.checkpoint({"name": "snap"})

        # Add a new object to the mock
        new_obj = MagicMock()
        new_obj.Name = "Fillet"
        mock_doc.Objects = mock_doc.Objects + [new_obj]

        result = doc_handler.rollback_to_checkpoint({"name": "snap"})
        assert "removed 1" in result
        assert "Fillet" in result
        mock_doc.removeObject.assert_called_with("Fillet")

    def test_rollback_no_checkpoint(self, doc_handler):
        result = doc_handler.rollback_to_checkpoint({"name": "nonexistent"})
        assert "No checkpoint" in result

    def test_rollback_nothing_to_remove(self, doc_handler, mock_doc):
        doc_handler.checkpoint({"name": "clean"})
        result = doc_handler.rollback_to_checkpoint({"name": "clean"})
        assert "removed 0" in result

    def test_rollback_no_doc(self, doc_handler, mock_freecad):
        doc_handler._checkpoints = {"snap": ["Box"]}
        mock_freecad.ActiveDocument = None
        result = doc_handler.rollback_to_checkpoint({"name": "snap"})
        assert "No active document" in result


# ---------------------------------------------------------------------------
# insert_shape
# ---------------------------------------------------------------------------

class TestInsertShape:
    @pytest.fixture(autouse=True)
    def _setup_fc_methods(self, mock_freecad):
        """Add listDocuments/getDocument to the mock FreeCAD module."""
        mock_freecad.listDocuments = MagicMock(return_value={})
        mock_freecad.getDocument = MagicMock(return_value=None)

    def test_missing_source_doc(self, doc_handler):
        result = doc_handler.insert_shape({})
        assert "source_doc" in result

    def test_missing_source_object(self, doc_handler):
        result = doc_handler.insert_shape({"source_doc": "SomeDoc"})
        assert "source_object" in result

    def test_source_doc_not_open(self, doc_handler, mock_freecad):
        mock_freecad.listDocuments.return_value = {"ActiveDoc": MagicMock()}
        result = doc_handler.insert_shape({
            "source_doc": "ClosedDoc",
            "source_object": "Box",
        })
        assert "not open" in result.lower()

    def test_source_object_not_found(self, doc_handler, mock_freecad):
        src_doc = MagicMock()
        src_doc.getObject.return_value = None
        mock_freecad.listDocuments.return_value = {"SrcDoc": src_doc}
        mock_freecad.getDocument.return_value = src_doc
        result = doc_handler.insert_shape({
            "source_doc": "SrcDoc",
            "source_object": "Missing",
        })
        assert "not found" in result.lower()

    def test_object_no_shape(self, doc_handler, mock_freecad):
        src_obj = MagicMock(spec=["Name"])  # no Shape attr
        src_doc = MagicMock()
        src_doc.getObject.return_value = src_obj
        mock_freecad.listDocuments.return_value = {"SrcDoc": src_doc}
        mock_freecad.getDocument.return_value = src_doc
        result = doc_handler.insert_shape({
            "source_doc": "SrcDoc",
            "source_object": "NoShape",
        })
        assert "no Shape" in result

    def test_no_active_document(self, doc_handler, mock_freecad):
        src_obj = MagicMock()
        src_obj.Shape = MagicMock()
        src_doc = MagicMock()
        src_doc.getObject.return_value = src_obj
        mock_freecad.listDocuments.return_value = {"SrcDoc": src_doc}
        mock_freecad.getDocument.return_value = src_doc
        mock_freecad.ActiveDocument = None
        result = doc_handler.insert_shape({
            "source_doc": "SrcDoc",
            "source_object": "Box",
        })
        assert "No active document" in result

    def test_insert_success(self, doc_handler, mock_freecad):
        # Source — copy() must return a shape-like object with BoundBox
        class FakeBB:
            XLength = 20.0
            YLength = 15.0
            ZLength = 10.0

        copied_shape = MagicMock()
        copied_shape.BoundBox = FakeBB()

        src_shape = MagicMock()
        src_shape.copy.return_value = copied_shape
        src_obj = MagicMock()
        src_obj.Shape = src_shape
        src_doc = MagicMock()
        src_doc.getObject.return_value = src_obj

        # Destination — feature.Shape gets reassigned to copied_shape in the code,
        # but MagicMock intercepts __setattr__. Use a wrapper that tracks Shape.
        class FakeFeature:
            Name = "Box_ref"
            Shape = None
            Placement = MagicMock()

        feature = FakeFeature()
        dst_doc = MagicMock()
        dst_doc.addObject.return_value = feature

        mock_freecad.listDocuments.return_value = {"SrcDoc": src_doc}
        mock_freecad.getDocument.return_value = src_doc
        mock_freecad.ActiveDocument = dst_doc
        mock_freecad.Vector = MagicMock()

        result = doc_handler.insert_shape({
            "source_doc": "SrcDoc",
            "source_object": "Box",
        })
        assert "Inserted" in result
        assert "Box_ref" in result
        assert "20.0" in result


# ---------------------------------------------------------------------------
# make_link_array
# ---------------------------------------------------------------------------

class TestMakeLinkArray:
    def test_create_array(self, doc_handler, mock_doc, mock_freecad):
        links = []
        def add_link(type_id, name):
            link = MagicMock()
            link.Name = name
            link.Placement = MagicMock()
            links.append(link)
            return link
        mock_doc.addObject = add_link
        mock_freecad.Vector = MagicMock()

        result = doc_handler.make_link_array({
            "object_name": "Box",
            "count": 3,
            "interval_x": 50,
        })
        assert "3 instances" in result
        assert len(links) == 2  # count - 1

    def test_array_not_found(self, doc_handler, mock_doc):
        result = doc_handler.make_link_array({"object_name": "Nonexistent"})
        assert "not found" in result.lower()

    def test_array_no_doc(self, doc_handler, mock_freecad):
        mock_freecad.ActiveDocument = None
        result = doc_handler.make_link_array({"object_name": "Box"})
        assert "No active document" in result

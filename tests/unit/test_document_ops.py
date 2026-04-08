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

    def test_create_fallback_no_server(self, mock_freecad):
        """Without server, should create document directly."""
        for mod in ("handlers.base", "handlers.document_ops"):
            if mod in sys.modules:
                del sys.modules[mod]
        from handlers.document_ops import DocumentOpsHandler

        handler = DocumentOpsHandler(server=None)
        new_doc = MagicMock()
        mock_freecad.newDocument.return_value = new_doc

        result = handler.create_document({"document_name": "DirectDoc"})
        assert "DirectDoc" in result
        assert "created" in result
        mock_freecad.newDocument.assert_called_once_with("DirectDoc")

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

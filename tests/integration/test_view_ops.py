"""Integration tests for view_control operations that mutate the document
through the gui_ops queue.

Coverage focus: checkpoint / rollback_to_checkpoint / insert_shape. These
ops route through `_run_on_gui_thread` (gui_ops in
freecad_mcp_handler._dispatch_view_control) and mutate document state —
the queue serialization and Qt-task drain can only be exercised end-to-
end. Unit tests with mocks cover the handler logic in test_document_ops.py;
this file complements them with a real-FreeCAD round-trip.

Also covers: delete_object, undo, redo, recompute (real headless-testable
behavior — pure App-layer, no FreeCADGui touch); select_object,
clear_selection, get_selection (success-but-silent-no-op headless —
UniversalSelector's own methods check `if FreeCADGui:` and no-op when it's
None, so these return a success string without any real 3D-view effect;
still worth testing the returned string and the not-found error path);
save_document (via view_control, dispatches to document_ops).

Not covered here:
  * Clip planes (add_clip_plane / remove_clip_plane) — they touch Coin3D
    via pivy and we have no headless way to verify the visual effect.
    (add_clip_plane/remove_clip_plane do have a clean, deliberate headless
    guard — "Clip plane not available in headless mode" — rather than an
    exception, unlike the 8 ops below, but still produce no observable
    visual effect to assert on here.)
  * Screenshot — covered by test_view_ops_screenshot.py at the unit level
    (the macOS subprocess path is the gnarly part and doesn't benefit
    from headless integration coverage).
  * set_view, fit_all, zoom_in, zoom_out, hide_object, show_object,
    activate_workbench, get_report_view — all eight read FreeCADGui
    directly (FreeCADGui.ActiveDocument, .Selection, .activateWorkbench,
    .getMainWindow) with no headless guard, so every one of them raises
    an AttributeError-derived string headless every time, regardless of
    document/object state. The only legitimate integration assertion for
    these would be "returns an error string, doesn't crash the socket" —
    a much thinner test than the real-behavior ones above, and one that
    can't observe whether the *intended* GUI-mode behavior still works.
    Deliberately out of scope for this tier (candidates for a Phase 3
    GUI-mode-only suite if ever wanted).
"""

import json
import time
import pytest

from ._geom_helpers import assert_op_succeeded, _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"ViewOps_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })
    yield doc_name
    try:
        send_command("execute_python_sync", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


@pytest.fixture
def two_docs():
    """Create two open documents, return both names. Active doc is the second."""
    src = f"ViewOpsSrc_{int(time.time() * 1000) % 100000}"
    dst = f"ViewOpsDst_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {"operation": "create_document",
                                  "document_name": src})
    # Add a box to the source doc so insert_shape has something to copy
    send_command("part_operations", {
        "operation": "box",
        "length": 30, "width": 20, "height": 10,
        "name": "SrcBox",
    })
    send_command("view_control", {"operation": "create_document",
                                  "document_name": dst})
    yield src, dst
    for d in (src, dst):
        try:
            send_command("execute_python_sync", {
                "code": f"FreeCAD.closeDocument('{d}')"
            })
        except Exception:
            pass


# ---------------------------------------------------------------------------
# checkpoint / rollback_to_checkpoint
# ---------------------------------------------------------------------------

class TestCheckpointRollback:
    """Snapshot the object list, then add objects, then rollback to remove them."""

    def test_checkpoint_records_existing_objects(self, clean_document):
        # Add a box first
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "PreCheckpoint",
        })

        result = send_command("view_control", {
            "operation": "checkpoint",
            "name": "before_extras",
        })
        assert_op_succeeded(result, "checkpoint")
        text = _text(result)
        assert "before_extras" in text, \
            f"Expected checkpoint label in: {text[:300]}"
        # 1 object was checkpointed
        assert "1 object" in text, f"Expected 1 object in: {text[:300]}"

    def test_rollback_removes_objects_added_after_checkpoint(self, clean_document):
        """Add a box, checkpoint, add 2 more, rollback — 2 should be removed."""
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10,
            "name": "Persistent",
        })
        send_command("view_control", {
            "operation": "checkpoint", "name": "snap",
        })
        # Add 2 boxes after the checkpoint
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5,
            "name": "Temp1",
        })
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5,
            "name": "Temp2",
        })

        result = send_command("view_control", {
            "operation": "rollback_to_checkpoint", "name": "snap",
        })
        assert_op_succeeded(result, "rollback")
        text = _text(result)
        assert "removed 2 objects" in text, \
            f"Expected 2 objects removed in: {text[:300]}"

        # Verify the document state — only Persistent remains.
        # Use print() to bypass the execute_python repr() wrap; the
        # trailing result=None clears any stale namespace value.
        check = send_command("execute_python_sync", {
            "code": (
                "import json\n"
                f"doc = FreeCAD.getDocument('{clean_document}')\n"
                "print(json.dumps(sorted(o.Name for o in doc.Objects)))\n"
                "result = None\n"
            ),
        })
        check_text = _text(check).strip()
        if check_text.startswith("Result: "):
            check_text = check_text[len("Result: "):].strip()
        names = json.loads(check_text)
        assert "Persistent" in names, f"Persistent should remain: {names}"
        assert "Temp1" not in names, f"Temp1 should be gone: {names}"
        assert "Temp2" not in names, f"Temp2 should be gone: {names}"

    def test_rollback_to_unknown_checkpoint_errors(self, clean_document):
        result = send_command("view_control", {
            "operation": "rollback_to_checkpoint",
            "name": "never_made",
        })
        text = _text(result)
        assert "no checkpoint" in text.lower(), \
            f"Expected unknown checkpoint error: {text[:300]}"


# ---------------------------------------------------------------------------
# insert_shape
# ---------------------------------------------------------------------------

class TestInsertShape:
    """Copy a shape from one open document into another."""

    def test_insert_shape_copies_geometry(self, two_docs):
        src, dst = two_docs
        result = send_command("view_control", {
            "operation": "insert_shape",
            "source_doc": src,
            "source_object": "SrcBox",
            "name": "ImportedBox",
        })
        assert_op_succeeded(result, "insert_shape")
        text = _text(result)
        # Bounding box dimensions show up in the success message
        assert "30.0" in text and "20.0" in text and "10.0" in text, \
            f"Expected box dims in message: {text[:300]}"

        # Verify the shape was actually copied into the destination doc.
        # Use print() to bypass the execute_python repr() wrap; the
        # trailing result=None clears any stale namespace value.
        check = send_command("execute_python_sync", {
            "code": (
                "import json\n"
                f"doc = FreeCAD.getDocument('{dst}')\n"
                "obj = doc.getObject('ImportedBox')\n"
                "print(json.dumps({"
                "  'has_shape': hasattr(obj, 'Shape'),"
                "  'volume': float(obj.Shape.Volume) if hasattr(obj, 'Shape') else 0,"
                "}))\n"
                "result = None\n"
            ),
        })
        check_text = _text(check).strip()
        if check_text.startswith("Result: "):
            check_text = check_text[len("Result: "):].strip()
        payload = json.loads(check_text)
        assert payload['has_shape'], "ImportedBox missing Shape"
        # 30 * 20 * 10 = 6000
        assert abs(payload['volume'] - 6000.0) < 1.0, \
            f"Expected volume ~6000, got {payload['volume']}"

    def test_insert_shape_missing_source_doc(self, clean_document):
        result = send_command("view_control", {
            "operation": "insert_shape",
            "source_doc": "NoSuchDoc",
            "source_object": "Whatever",
        })
        text = _text(result)
        assert "not open" in text.lower() or "not found" in text.lower(), \
            f"Expected document-not-open error: {text[:300]}"

    def test_insert_shape_missing_source_object(self, two_docs):
        src, _ = two_docs
        result = send_command("view_control", {
            "operation": "insert_shape",
            "source_doc": src,
            "source_object": "GhostObject",
        })
        text = _text(result)
        assert "not found" in text.lower(), \
            f"Expected object-not-found error: {text[:300]}"


# ---------------------------------------------------------------------------
# delete_object / undo / redo / recompute — real, headless-testable
# behavior, pure App layer.
# ---------------------------------------------------------------------------

class TestDeleteObject:
    def test_delete_object_removes_it(self, clean_document):
        doc_name = clean_document
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "ToDelete",
        })
        result = send_command("view_control", {
            "operation": "delete_object", "object_name": "ToDelete",
        })
        text = _text(result)
        assert "deleted" in text.lower(), text[:300]

        check = send_command("execute_python_sync", {
            "code": f"print(FreeCAD.getDocument({doc_name!r}).getObject('ToDelete') is None)\nresult = None\n"
        })
        assert _text(check).strip().endswith("True"), _text(check)[:300]

    def test_delete_object_not_found(self, clean_document):
        result = send_command("view_control", {
            "operation": "delete_object", "object_name": "Ghost",
        })
        text = _text(result)
        assert "not found" in text.lower(), text[:300]


class TestUndoRedo:
    """undo/redo currently do NOT revert anything created via any MCP
    tool. Confirmed live and systemically: `grep -rn "openTransaction"
    AICopilot/` returns zero matches anywhere in this codebase.
    doc.UndoMode=1 alone does not auto-wrap arbitrary doc.addObject()
    calls in an undo transaction the way GUI Command execution normally
    does — FreeCAD's doc.UndoCount stays 0 after part_operations creates
    a box (confirmed live), so doc.undo() has nothing to revert. This is
    a real, systemic gap (every mutating handler across every workbench
    would need doc.openTransaction()/commitTransaction() pairing to fix),
    well beyond this test file's scope — flagged separately. These tests
    pin the actual current (non-)behavior rather than assert the
    documented intent, so a future fix is visible as these tests
    starting to fail in a way that should prompt updating them, not
    reverting the fix."""

    def test_undo_does_not_currently_revert_mcp_created_objects(self, clean_document):
        doc_name = clean_document
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "UndoBox",
        })
        result = send_command("view_control", {"operation": "undo"})
        text = _text(result)
        assert "undo" in text.lower(), text[:300]

        check = send_command("execute_python_sync", {
            "code": f"print(FreeCAD.getDocument({doc_name!r}).getObject('UndoBox') is None)\nresult = None\n"
        })
        assert _text(check).strip().endswith("False"), (
            "If this now says True, undo started actually reverting MCP-created "
            f"objects — investigate before assuming it's just this test being "
            f"stale. Got: {_text(check)[:300]}"
        )

    def test_undo_on_document_with_no_transactions_reports_completed_not_error(self, clean_document):
        """undo() on a document with nothing to undo doesn't error —
        FreeCAD's Document.undo() is a silent no-op with UndoCount==0,
        and the handler doesn't distinguish that from a real undo."""
        result = send_command("view_control", {"operation": "undo"})
        text = _text(result)
        assert "Undo completed" in text, text[:300]

    def test_redo_on_document_with_no_transactions_reports_completed_not_error(self, clean_document):
        result = send_command("view_control", {"operation": "redo"})
        text = _text(result)
        assert "Redo completed" in text, text[:300]


class TestRecompute:
    def test_recompute_whole_document(self, clean_document):
        result = send_command("view_control", {"operation": "recompute"})
        text = _text(result)
        assert "Recomputed document" in text, text[:300]

    def test_recompute_single_object(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "RecBox",
        })
        result = send_command("view_control", {
            "operation": "recompute", "object_name": "RecBox",
        })
        text = _text(result)
        assert "Recomputed 'RecBox'" in text, text[:300]

    def test_recompute_object_not_found(self, clean_document):
        result = send_command("view_control", {
            "operation": "recompute", "object_name": "Ghost",
        })
        text = _text(result)
        assert "not found" in text.lower(), text[:300]


# ---------------------------------------------------------------------------
# select_object / clear_selection / get_selection — headless these are
# real no-ops (UniversalSelector guards on `if FreeCADGui:`), but the
# returned strings and not-found error paths are still real behavior
# worth pinning.
# ---------------------------------------------------------------------------

class TestSelectionOpsHeadlessNoOp:
    def test_select_object_success_message(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "SelBox",
        })
        result = send_command("view_control", {
            "operation": "select_object", "object_name": "SelBox",
        })
        text = _text(result)
        assert "Selected object 'SelBox'" in text, text[:300]

    def test_select_object_not_found(self, clean_document):
        result = send_command("view_control", {
            "operation": "select_object", "object_name": "Ghost",
        })
        text = _text(result)
        assert "not found" in text.lower(), text[:300]

    def test_clear_selection(self, clean_document):
        result = send_command("view_control", {"operation": "clear_selection"})
        text = _text(result)
        assert "Selection cleared" in text, text[:300]

    def test_get_selection_empty_headless(self, clean_document):
        """select_object is itself a headless no-op (no real FreeCADGui.
        Selection to add to), so get_selection can only ever observe an
        empty selection in this tier — that's the real, correct behavior
        to pin, not a gap in this test."""
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "SelBox2",
        })
        send_command("view_control", {
            "operation": "select_object", "object_name": "SelBox2",
        })
        result = send_command("view_control", {"operation": "get_selection"})
        text = _text(result)
        assert "No objects selected" in text, text[:300]


# ---------------------------------------------------------------------------
# save_document (view_control -> document_ops.save_document)
# ---------------------------------------------------------------------------

class TestSaveDocument:
    def test_save_document_to_explicit_path(self, clean_document):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = f"{tmp_dir}/saved_doc.FCStd"
            result = send_command("view_control", {
                "operation": "save_document", "filename": path,
            })
            text = _text(result)
            assert "Document saved as" in text, text[:300]
            import os
            assert os.path.isfile(path), f"expected file at {path}"

    def test_save_document_rejects_path_outside_allowed_dirs(self, clean_document):
        result = send_command("view_control", {
            "operation": "save_document", "filename": "/etc/definitely_not_allowed.FCStd",
        })
        text = _text(result)
        assert "outside allowed directories" in text, text[:300]

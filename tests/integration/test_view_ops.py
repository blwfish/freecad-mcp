"""Integration tests for view_control operations that mutate the document
through the gui_ops queue.

Coverage focus: checkpoint / rollback_to_checkpoint / insert_shape. These
ops route through `_run_on_gui_thread` (gui_ops in
freecad_mcp_handler._dispatch_view_control) and mutate document state —
the queue serialization and Qt-task drain can only be exercised end-to-
end. Unit tests with mocks cover the handler logic in test_document_ops.py;
this file complements them with a real-FreeCAD round-trip.

Not covered here:
  * Clip planes (add_clip_plane / remove_clip_plane) — they touch Coin3D
    via pivy and we have no headless way to verify the visual effect.
  * Screenshot — covered by test_view_ops_screenshot.py at the unit level
    (the macOS subprocess path is the gnarly part and doesn't benefit
    from headless integration coverage).
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
        send_command("execute_python", {
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
            send_command("execute_python", {
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

        # Verify the document state — only Persistent remains
        check = send_command("execute_python", {
            "code": (
                "import json\n"
                f"doc = FreeCAD.getDocument('{clean_document}')\n"
                "json.dumps(sorted(o.Name for o in doc.Objects))"
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

        # Verify the shape was actually copied into the destination doc
        check = send_command("execute_python", {
            "code": (
                "import json\n"
                f"doc = FreeCAD.getDocument('{dst}')\n"
                "obj = doc.getObject('ImportedBox')\n"
                "json.dumps({"
                "  'has_shape': hasattr(obj, 'Shape'),"
                "  'volume': float(obj.Shape.Volume) if hasattr(obj, 'Shape') else 0,"
                "})"
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

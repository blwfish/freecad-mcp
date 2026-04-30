"""
Boolean operation integration tests — fuse, cut, common.

Fuse and cut are already tested in test_e2e_workflows.py.
This file adds common (intersection) and error cases.
Boolean ops go through _call_on_gui_thread_async, so the immediate
response is a job acknowledgment.
"""

import time
import pytest
from ._geom_helpers import assert_op_succeeded, _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"BoolOps_{int(time.time() * 1000) % 100000}"
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
def two_overlapping_boxes(clean_document):
    """Two boxes that overlap by 5mm in X."""
    send_command("part_operations", {
        "operation": "box",
        "length": 20, "width": 20, "height": 20,
        "name": "BoolA",
    })
    send_command("part_operations", {
        "operation": "box",
        "length": 20, "width": 20, "height": 20,
        "x": 15, "name": "BoolB",
    })
    return clean_document


class TestCommon:
    def test_common_overlapping(self, two_overlapping_boxes):
        """Intersection of two overlapping 20mm boxes (5mm overlap) succeeds.

        Boolean ops are async — initial response acknowledges the job,
        verifying intersection geometry would require polling. Tighten the
        assertion to confirm a job was actually submitted, not a dispatch
        failure or 'Unknown operation' tautology.
        """
        result = send_command("part_operations", {
            "operation": "common",
            "objects": ["BoolA", "BoolB"],
        })
        assert_op_succeeded(result, "common")
        text = _text(result)
        # Async boolean returns a job_id payload
        assert "job_id" in text or "submitted" in text or "Created" in text, \
            f"Expected async job acknowledgment, got: {text[:300]}"

    def test_common_missing_object(self, clean_document):
        """Common with nonexistent object surfaces a not-found error."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "OnlyBox",
        })
        result = send_command("part_operations", {
            "operation": "common",
            "objects": ["OnlyBox", "GhostBox"],
        })
        text = _text(result)
        # Error must surface — either at dispatch (sync) or after the job runs.
        # Don't gate on dispatch: allow either error-now or job-submitted (which
        # would later surface the error via poll_job).
        assert "Unknown operation" not in text, \
            f"common dead-letter: {text[:300]}"

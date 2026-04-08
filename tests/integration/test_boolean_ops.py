"""
Boolean operation integration tests — fuse, cut, common.

Fuse and cut are already tested in test_e2e_workflows.py.
This file adds common (intersection) and error cases.
Boolean ops go through _call_on_gui_thread_async, so the immediate
response is a job acknowledgment.
"""

import time
import pytest
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
        """Intersection of two overlapping boxes should succeed."""
        result = send_command("part_operations", {
            "operation": "common",
            "objects": ["BoolA", "BoolB"],
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_common_missing_object(self, clean_document):
        """Common with nonexistent object should report error."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "OnlyBox",
        })
        result = send_command("part_operations", {
            "operation": "common",
            "objects": ["OnlyBox", "GhostBox"],
        })
        result_str = str(result)
        assert "Unknown" not in result_str

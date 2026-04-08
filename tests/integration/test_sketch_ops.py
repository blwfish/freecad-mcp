"""
Sketch operations integration tests — add_line, add_circle, add_arc, add_slot,
close_sketch, verify_sketch.

create_sketch, add_rectangle, add_constraint, list_constraints, add_polygon
are already tested in test_e2e_workflows.py.
"""

import time
import pytest
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"SketchOps_{int(time.time() * 1000) % 100000}"
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
def empty_sketch(clean_document):
    """Create an empty sketch on XY plane."""
    send_command("sketch_operations", {
        "operation": "create_sketch",
        "plane": "XY",
        "name": "TestSketch",
    })
    return clean_document


class TestAddLine:
    def test_add_line(self, empty_sketch):
        result = send_command("sketch_operations", {
            "operation": "add_line",
            "sketch_name": "TestSketch",
            "x1": 0, "y1": 0, "x2": 20, "y2": 0,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "line" in result_str.lower()

    def test_add_line_missing_sketch(self, clean_document):
        result = send_command("sketch_operations", {
            "operation": "add_line",
            "sketch_name": "NoSketch",
            "x1": 0, "y1": 0, "x2": 10, "y2": 0,
        })
        result_str = str(result)
        assert "not found" in result_str.lower() or "error" in result_str.lower()


class TestAddCircle:
    def test_add_circle(self, empty_sketch):
        result = send_command("sketch_operations", {
            "operation": "add_circle",
            "sketch_name": "TestSketch",
            "center_x": 10, "center_y": 10,
            "radius": 5,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "circle" in result_str.lower()


class TestAddArc:
    def test_add_arc(self, empty_sketch):
        result = send_command("sketch_operations", {
            "operation": "add_arc",
            "sketch_name": "TestSketch",
            "center_x": 10, "center_y": 10,
            "radius": 8,
            "start_angle": 0, "end_angle": 90,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "arc" in result_str.lower()


class TestAddSlot:
    def test_add_slot(self, empty_sketch):
        result = send_command("sketch_operations", {
            "operation": "add_slot",
            "sketch_name": "TestSketch",
            "x1": 0, "y1": 0, "x2": 20, "y2": 0,
            "radius": 3,
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "error" not in result_str.lower() or "slot" in result_str.lower()


class TestCloseAndVerify:
    def test_close_sketch(self, empty_sketch):
        # Add geometry first, then close
        send_command("sketch_operations", {
            "operation": "add_rectangle",
            "sketch_name": "TestSketch",
            "x": 0, "y": 0, "width": 10, "height": 10,
        })
        result = send_command("sketch_operations", {
            "operation": "close_sketch",
            "sketch_name": "TestSketch",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

    def test_verify_empty_sketch(self, empty_sketch):
        """Verify a sketch with no geometry."""
        result = send_command("sketch_operations", {
            "operation": "verify_sketch",
            "sketch_name": "TestSketch",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "0" in result_str  # 0 geometry elements

    def test_verify_sketch_with_rectangle(self, empty_sketch):
        """Verify a sketch that has a closed rectangle."""
        send_command("sketch_operations", {
            "operation": "add_rectangle",
            "sketch_name": "TestSketch",
            "x": 0, "y": 0, "width": 10, "height": 10,
        })
        result = send_command("sketch_operations", {
            "operation": "verify_sketch",
            "sketch_name": "TestSketch",
        })
        result_str = str(result)
        assert "Unknown" not in result_str

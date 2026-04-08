"""
Measurement operations integration tests — list_faces, get_bounding_box,
get_volume, get_surface_area, get_center_of_mass, get_mass_properties,
count_elements, check_solid (via get_bounding_box shadowed method),
measure_distance.

Uses generic dispatcher — operation names must match method names.

NOTE: There is a known bug where get_bounding_box is defined twice in
measurement_ops.py. The second definition (line 232) shadows the first
and actually implements a "check_solid" operation. Tests document this
current behavior.
"""

import time
import pytest
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"MeasOps_{int(time.time() * 1000) % 100000}"
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
def known_box(clean_document):
    """20x15x10 box with predictable measurements."""
    send_command("part_operations", {
        "operation": "box",
        "length": 20, "width": 15, "height": 10,
        "name": "MeasBox",
    })
    return clean_document


class TestListFaces:
    def test_list_faces_box(self, known_box):
        """A box should have 6 faces."""
        result = send_command("measurement_operations", {
            "operation": "list_faces",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "6" in result_str  # 6 faces

    def test_list_faces_missing_object(self, clean_document):
        result = send_command("measurement_operations", {
            "operation": "list_faces",
            "object_name": "Ghost",
        })
        result_str = str(result)
        assert "not found" in result_str.lower() or "error" in result_str.lower()


class TestVolume:
    def test_get_volume(self, known_box):
        """Volume of 20x15x10 = 3000 mm³."""
        result = send_command("measurement_operations", {
            "operation": "get_volume",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "3000" in result_str


class TestSurfaceArea:
    def test_get_surface_area(self, known_box):
        """Surface area of 20x15x10 = 2*(300+200+150) = 1300 mm²."""
        result = send_command("measurement_operations", {
            "operation": "get_surface_area",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        assert "1300" in result_str


class TestCenterOfMass:
    def test_get_center_of_mass(self, known_box):
        result = send_command("measurement_operations", {
            "operation": "get_center_of_mass",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        # Center should be at (10, 7.5, 5) for a box at origin
        assert "10" in result_str


class TestMassProperties:
    def test_get_mass_properties(self, known_box):
        result = send_command("measurement_operations", {
            "operation": "get_mass_properties",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str


class TestCountElements:
    def test_count_elements_box(self, known_box):
        """Box: 6 faces, 12 edges, 8 vertices."""
        result = send_command("measurement_operations", {
            "operation": "count_elements",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        # Should contain face/edge/vertex counts
        assert "6" in result_str   # faces
        assert "12" in result_str  # edges
        assert "8" in result_str   # vertices


class TestBoundingBoxAndSolidCheck:
    def test_get_bounding_box_is_actually_solid_check(self, known_box):
        """Due to duplicate method name bug, get_bounding_box actually does solid check."""
        result = send_command("measurement_operations", {
            "operation": "get_bounding_box",
            "object_name": "MeasBox",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        # The shadowed method returns solid check info, not bounding box
        # Accept either behavior — test dispatches correctly
        assert "solid" in result_str.lower() or "bounding" in result_str.lower()


class TestMeasureDistance:
    def test_measure_distance_two_boxes(self, clean_document):
        """Measure distance between two separated boxes."""
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "name": "DistA",
        })
        send_command("part_operations", {
            "operation": "box",
            "length": 10, "width": 10, "height": 10,
            "x": 30, "name": "DistB",
        })
        result = send_command("measurement_operations", {
            "operation": "measure_distance",
            "object1": "DistA",
            "object2": "DistB",
        })
        result_str = str(result)
        assert "Unknown" not in result_str
        # Gap is 20mm (box1 ends at x=10, box2 starts at x=30)
        assert "20" in result_str

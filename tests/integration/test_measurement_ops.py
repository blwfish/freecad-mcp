"""
Measurement operations integration tests — list_faces, get_bounding_box,
get_volume, get_surface_area, get_center_of_mass, get_mass_properties,
count_elements, check_solid, measure_distance.

Uses generic dispatcher — operation names must match method names.
"""

import re
import time
import pytest
from ._geom_helpers import assert_op_succeeded, _result_text as _text
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
        """A 20x15x10 box has 6 faces with axis-aligned normals.

        Real FreeCAD output uses '%+.2f' format which yields '-0.00' for
        floating-point-near-zero components. Match each of the six
        axis-aligned faces tolerantly: one component is ±1, the other
        two are ±0.
        """
        result = send_command("measurement_operations", {
            "operation": "list_faces",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "list_faces")
        text = _text(result)
        # Header reports total face count
        assert "6 total" in text, f"Missing face count: {text[:300]}"
        # 1-based face indexing — Face1..Face6
        for i in range(1, 7):
            assert f"Face{i}:" in text, f"Missing Face{i} in: {text[:400]}"
        # Six axis-aligned face normals, with tolerance for ±0 zeros.
        # Each pattern: one ±1.00 component, two ±0.00 components.
        pm0 = r"[+-]0\.00"
        for direction, regex in (
            ("+X", rf"\(\+1\.00, {pm0}, {pm0}\)"),
            ("-X", rf"\(-1\.00, {pm0}, {pm0}\)"),
            ("+Y", rf"\({pm0}, \+1\.00, {pm0}\)"),
            ("-Y", rf"\({pm0}, -1\.00, {pm0}\)"),
            ("+Z", rf"\({pm0}, {pm0}, \+1\.00\)"),
            ("-Z", rf"\({pm0}, {pm0}, -1\.00\)"),
        ):
            assert re.search(regex, text), \
                f"Missing {direction} face normal (regex {regex}) in: {text[:400]}"
        # Face areas: 20×15 = 300 (top/bot), 20×10 = 200 (front/back),
        # 15×10 = 150 (left/right) — each appears at least once
        for area in ("300.00", "200.00", "150.00"):
            assert area in text, f"Missing face area {area} in: {text[:400]}"

    def test_list_faces_missing_object(self, clean_document):
        result = send_command("measurement_operations", {
            "operation": "list_faces",
            "object_name": "Ghost",
        })
        text = _text(result)
        assert "not found" in text.lower() or "error" in text.lower()


class TestVolume:
    def test_get_volume(self, known_box):
        """Volume of 20x15x10 = 3000 mm³."""
        result = send_command("measurement_operations", {
            "operation": "get_volume",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "get_volume")
        text = _text(result)
        assert "3000" in text, f"Expected volume 3000 in: {text[:200]}"
        assert "mm" in text, f"Expected unit 'mm' in: {text[:200]}"


class TestSurfaceArea:
    def test_get_surface_area(self, known_box):
        """Surface area of 20x15x10 = 2*(300+200+150) = 1300 mm²."""
        result = send_command("measurement_operations", {
            "operation": "get_surface_area",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "get_surface_area")
        text = _text(result)
        assert "1300" in text, f"Expected area 1300 in: {text[:200]}"


class TestCenterOfMass:
    def test_get_center_of_mass(self, known_box):
        """Center of 20x15x10 box at origin = (10.00, 7.50, 5.00)."""
        result = send_command("measurement_operations", {
            "operation": "get_center_of_mass",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "get_center_of_mass")
        text = _text(result)
        assert "10.00" in text, f"Expected x-component 10.00 in: {text[:200]}"
        assert "7.50" in text, f"Expected y-component 7.50 in: {text[:200]}"
        assert "5.00" in text, f"Expected z-component 5.00 in: {text[:200]}"


class TestMassProperties:
    def test_get_mass_properties(self, known_box):
        """Reports volume, surface area, and center of mass for 20x15x10 box."""
        result = send_command("measurement_operations", {
            "operation": "get_mass_properties",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "get_mass_properties")
        text = _text(result)
        assert "Volume:" in text and "3000" in text, \
            f"Expected volume 3000 in: {text[:300]}"
        assert "Surface Area:" in text and "1300" in text, \
            f"Expected surface area 1300 in: {text[:300]}"
        assert "Center of Mass:" in text and "10.00" in text, \
            f"Expected COM x=10.00 in: {text[:300]}"


class TestCountElements:
    def test_count_elements_box(self, known_box):
        """Box: 6 faces, 12 edges, 8 vertices, 1 solid."""
        result = send_command("measurement_operations", {
            "operation": "count_elements",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "count_elements")
        text = _text(result)
        # Use labeled counts so a stray "6" elsewhere won't count
        assert "Faces: 6" in text, f"Expected 6 faces: {text[:300]}"
        assert "Edges: 12" in text, f"Expected 12 edges: {text[:300]}"
        assert "Vertices: 8" in text, f"Expected 8 vertices: {text[:300]}"
        assert "Solids: 1" in text, f"Expected 1 solid: {text[:300]}"


class TestBoundingBox:
    def test_get_bounding_box_returns_dimensions(self, known_box):
        """Bounding box of 20x15x10 box reports X/Y/Z extents and lengths."""
        result = send_command("measurement_operations", {
            "operation": "get_bounding_box",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "get_bounding_box")
        text = _text(result)
        assert "Bounding box" in text, \
            f"Expected bounding box header: {text[:300]}"
        assert "length: 20" in text, f"Expected XLength 20: {text[:300]}"
        assert "width: 15" in text, f"Expected YLength 15: {text[:300]}"
        assert "height: 10" in text, f"Expected ZLength 10: {text[:300]}"


class TestCheckSolid:
    def test_check_solid_on_valid_box(self, known_box):
        """A standard 20x15x10 box is a closed, valid solid."""
        result = send_command("measurement_operations", {
            "operation": "check_solid",
            "object_name": "MeasBox",
        })
        assert_op_succeeded(result, "check_solid")
        text = _text(result)
        assert "Is a closed solid" in text, \
            f"Expected closed-solid status: {text[:300]}"
        assert "Shape is valid" in text, \
            f"Expected valid status: {text[:300]}"


class TestMeasureDistance:
    def test_measure_distance_two_boxes(self, clean_document):
        """Center-to-center distance between two 10mm boxes 30mm apart = 30.00 mm.

        Box A center = (5, 5, 5); Box B center = (35, 5, 5); distance = 30.
        """
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
        assert_op_succeeded(result, "measure_distance")
        text = _text(result)
        assert "Distance" in text or "distance" in text
        # Center-to-center: (5,5,5) → (35,5,5) = 30 mm
        assert "30.00" in text, f"Expected distance 30.00 in: {text[:300]}"

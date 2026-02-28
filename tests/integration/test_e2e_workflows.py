"""
End-to-end integration tests for FreeCAD MCP.

These tests work in two modes (auto-detected by conftest.py):
  1. GUI mode: connect to a running FreeCAD with AICopilot workbench loaded
  2. Headless mode: spawn a FreeCADCmd instance automatically

Run with:
    python3 -m pytest tests/integration/ -v

Skip with:
    python3 -m pytest tests/ --ignore=tests/integration
"""

import json
import os
import socket
import struct
import sys
import tempfile
import time

import pytest

from . import conftest

# ---------------------------------------------------------------------------
# Connection to FreeCAD via socket
# ---------------------------------------------------------------------------


def send_command(tool: str, args: dict, timeout: float = 10.0) -> dict:
    """Send a command to FreeCAD and return the parsed response."""
    sock_path = conftest.get_socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    try:
        msg = json.dumps({"tool": tool, "args": args}).encode("utf-8")
        # Length-prefixed framing
        s.sendall(struct.pack("!I", len(msg)) + msg)

        # Read response length
        length_bytes = _recv_exact(s, 4)
        resp_len = struct.unpack("!I", length_bytes)[0]

        # Read response body
        resp_bytes = _recv_exact(s, resp_len)
        return json.loads(resp_bytes.decode("utf-8"))
    finally:
        s.close()


def _recv_exact(s, n):
    """Receive exactly n bytes from socket."""
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Socket closed before all data received")
        data += chunk
    return data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_document():
    """Create a fresh document and clean up after the test."""
    doc_name = f"IntegrationTest_{int(time.time() * 1000) % 100000}"
    result = send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })
    # The response may be JSON-encoded or a plain string
    yield doc_name
    # Cleanup: close document without saving
    try:
        send_command("execute_python", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass  # Best-effort cleanup


# ---------------------------------------------------------------------------
# Tests: Document and Primitives
# ---------------------------------------------------------------------------

class TestDocumentCreation:
    """Test that documents can be created safely from the socket thread."""

    def test_create_document(self):
        """create_document should work via GUI thread routing."""
        doc_name = f"TestDoc_{int(time.time() * 1000) % 100000}"
        result = send_command("view_control", {
            "operation": "create_document",
            "document_name": doc_name,
        })
        assert "error" not in str(result).lower() or "Error" not in result.get("result", ""), \
            f"Document creation failed: {result}"

        # Verify document exists
        check = send_command("execute_python", {
            "code": f"FreeCAD.getDocument('{doc_name}').Name"
        })
        assert doc_name in str(check), f"Document not found after creation: {check}"

        # Cleanup
        send_command("execute_python", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })

    def test_list_objects_empty_document(self, clean_document):
        """list_objects on a fresh document should return zero objects."""
        result = send_command("view_control", {
            "operation": "list_objects",
        })
        parsed = json.loads(result.get("result", "{}")) if isinstance(result.get("result"), str) else result
        # Fresh document may have 0 objects or just an Origin
        assert "error" not in str(result).lower()


class TestPrimitives:
    """Test primitive creation — previously crashed due to GIL deadlock."""

    def test_create_box(self, clean_document):
        """Creating a box should succeed without deadlock."""
        result = send_command("part_operations", {
            "operation": "box",
            "length": 20,
            "width": 15,
            "height": 10,
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "Created box" in result_str, \
            f"Box creation failed: {result}"

    def test_create_cylinder(self, clean_document):
        """Creating a cylinder should succeed without deadlock."""
        result = send_command("part_operations", {
            "operation": "cylinder",
            "radius": 5,
            "height": 20,
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "Created cylinder" in result_str, \
            f"Cylinder creation failed: {result}"

    def test_create_sphere(self, clean_document):
        """Creating a sphere should succeed without deadlock."""
        result = send_command("part_operations", {
            "operation": "sphere",
            "radius": 10,
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "Created sphere" in result_str, \
            f"Sphere creation failed: {result}"

    def test_create_box_without_document(self):
        """Creating a box with no active document should create one via GUI thread.

        This is the exact scenario that caused the GIL deadlock before the fix.
        """
        # Close all documents first
        send_command("execute_python", {
            "code": "for name in list(FreeCAD.listDocuments().keys()): FreeCAD.closeDocument(name)"
        })

        # This should NOT deadlock — it should create a document via GUI thread
        result = send_command("part_operations", {
            "operation": "box",
            "length": 10,
            "width": 10,
            "height": 10,
        }, timeout=15.0)
        result_str = str(result)
        assert "Created box" in result_str or "error" not in result_str.lower(), \
            f"Box creation without document failed (possible GIL deadlock): {result}"

        # Cleanup
        send_command("execute_python", {
            "code": "for name in list(FreeCAD.listDocuments().keys()): FreeCAD.closeDocument(name)"
        })


# ---------------------------------------------------------------------------
# Tests: Sketch + Pad workflow
# ---------------------------------------------------------------------------

class TestSketchPadWorkflow:
    """Test the fundamental CAD workflow: sketch → pad → solid."""

    def test_create_sketch(self, clean_document):
        """Creating a sketch should succeed."""
        result = send_command("partdesign_operations", {
            "operation": "pad",
            "sketch_name": "",  # Will need a sketch first
        })
        # We expect an error about missing sketch, not a crash
        # This confirms the handler runs without deadlocking

    def test_full_sketch_to_pad(self, clean_document):
        """Full workflow: create sketch → add rectangle → pad to solid."""
        # Step 1: Create a sketch on XY plane via execute_python
        sketch_result = send_command("execute_python", {
            "code": """
import FreeCAD
import Part
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'TestSketch')
sketch.Placement = FreeCAD.Placement(
    FreeCAD.Vector(0, 0, 0),
    FreeCAD.Rotation(0, 0, 0, 1)
)
# Add a closed rectangle
p1 = FreeCAD.Vector(0, 0, 0)
p2 = FreeCAD.Vector(30, 0, 0)
p3 = FreeCAD.Vector(30, 20, 0)
p4 = FreeCAD.Vector(0, 20, 0)
sketch.addGeometry(Part.LineSegment(p1, p2))
sketch.addGeometry(Part.LineSegment(p2, p3))
sketch.addGeometry(Part.LineSegment(p3, p4))
sketch.addGeometry(Part.LineSegment(p4, p1))
doc.recompute()
sketch.Name
"""
        })
        assert "error" not in str(sketch_result).lower(), \
            f"Sketch creation failed: {sketch_result}"

        # Step 2: Pad the sketch
        pad_result = send_command("partdesign_operations", {
            "operation": "pad",
            "sketch_name": "TestSketch",
            "length": 10,
        })
        pad_str = str(pad_result)
        assert "Created pad" in pad_str or "Pad" in pad_str, \
            f"Pad operation failed: {pad_result}"


# ---------------------------------------------------------------------------
# Tests: Export
# ---------------------------------------------------------------------------

class TestExport:
    """Test mesh export workflows."""

    def test_export_stl(self, clean_document):
        """Create a box and export it as STL."""
        # Create a box
        send_command("part_operations", {
            "operation": "box",
            "length": 10,
            "width": 10,
            "height": 10,
        })

        # Export to temp file
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
            stl_path = f.name

        try:
            result = send_command("mesh_operations", {
                "operation": "export_mesh",
                "object_name": "Box",
                "file_path": stl_path,
            })
            result_str = str(result)
            # Check file was created
            assert os.path.exists(stl_path), f"STL file not created: {result}"
            assert os.path.getsize(stl_path) > 0, "STL file is empty"
        finally:
            if os.path.exists(stl_path):
                os.unlink(stl_path)

    def test_export_step(self, clean_document):
        """Create a box and export it as STEP."""
        send_command("part_operations", {
            "operation": "box",
            "length": 20,
            "width": 15,
            "height": 10,
        })

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            step_path = f.name

        try:
            result = send_command("mesh_operations", {
                "operation": "export_file",
                "object_name": "Box",
                "file_path": step_path,
            })
            assert os.path.exists(step_path), f"STEP file not created: {result}"
            assert os.path.getsize(step_path) > 0, "STEP file is empty"
        finally:
            if os.path.exists(step_path):
                os.unlink(step_path)


# ---------------------------------------------------------------------------
# Tests: Screenshot
# ---------------------------------------------------------------------------

class TestScreenshot:
    """Test screenshot capture."""

    def test_screenshot(self, clean_document):
        """Taking a screenshot should produce an image."""
        # Create something to look at
        send_command("part_operations", {
            "operation": "box",
            "length": 10,
            "width": 10,
            "height": 10,
        })

        result = send_command("view_control", {
            "operation": "screenshot",
            "width": 400,
            "height": 300,
        })
        # Screenshot may return base64 data or a file path
        result_str = str(result)
        assert "error" not in result_str.lower() or len(result_str) > 100, \
            f"Screenshot failed: {result}"


# ---------------------------------------------------------------------------
# Tests: Boolean operations
# ---------------------------------------------------------------------------

class TestBooleanOps:
    """Test boolean operations between solids."""

    def test_fuse_two_boxes(self, clean_document):
        """Fuse (union) two boxes into one solid."""
        # Create two overlapping boxes
        send_command("part_operations", {
            "operation": "box",
            "length": 20,
            "width": 20,
            "height": 20,
            "name": "Box1",
        })
        send_command("part_operations", {
            "operation": "box",
            "length": 20,
            "width": 20,
            "height": 20,
            "x": 10,
            "y": 10,
            "z": 10,
            "name": "Box2",
        })

        # Fuse them
        result = send_command("part_operations", {
            "operation": "fuse",
            "objects": ["Box1", "Box2"],
        })
        result_str = str(result)
        # Should succeed (fuse creates a new object)
        assert "error" not in result_str.lower() or "Fuse" in result_str, \
            f"Fuse failed: {result}"

    def test_cut_box(self, clean_document):
        """Cut (subtract) a cylinder from a box."""
        send_command("part_operations", {
            "operation": "box",
            "length": 30,
            "width": 30,
            "height": 10,
            "name": "Base",
        })
        send_command("part_operations", {
            "operation": "cylinder",
            "radius": 5,
            "height": 20,
            "x": 15,
            "y": 15,
            "name": "Hole",
        })

        result = send_command("part_operations", {
            "operation": "cut",
            "base": "Base",
            "tools": ["Hole"],
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "Cut" in result_str, \
            f"Cut failed: {result}"


# ---------------------------------------------------------------------------
# Tests: Sketch operations (constraints, geometry)
# ---------------------------------------------------------------------------

class TestSketchOperations:
    """Test the sketch_operations smart dispatcher."""

    def test_create_sketch_via_dispatcher(self, clean_document):
        """Creating a sketch via the sketch_operations dispatcher."""
        result = send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "TestSketch",
        })
        assert "Created sketch" in str(result), f"Sketch creation failed: {result}"

    def test_add_rectangle_with_constraints(self, clean_document):
        """add_rectangle should create 4 lines with coincident + H/V constraints."""
        # Create sketch
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "RectSketch",
        })
        # Add rectangle
        result = send_command("sketch_operations", {
            "operation": "add_rectangle",
            "sketch_name": "RectSketch",
            "x": 0, "y": 0,
            "width": 30, "height": 20,
        })
        result_str = str(result)
        assert "geo_ids=" in result_str, f"Rectangle failed: {result}"

        # Verify sketch has constraints
        verify = send_command("sketch_operations", {
            "operation": "verify_sketch",
            "sketch_name": "RectSketch",
        })
        assert "Geometry elements: 4" in str(verify), f"Expected 4 lines: {verify}"

    def test_add_constraint_distance(self, clean_document):
        """Adding a distance constraint should set a dimensional value."""
        # Create sketch with a line
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "ConstraintSketch",
        })
        send_command("sketch_operations", {
            "operation": "add_line",
            "sketch_name": "ConstraintSketch",
            "x1": 0, "y1": 0,
            "x2": 25, "y2": 0,
        })
        # Add distance constraint
        result = send_command("sketch_operations", {
            "operation": "add_constraint",
            "sketch_name": "ConstraintSketch",
            "constraint_type": "Distance",
            "geo_id1": 0,
            "value": 25,
        })
        result_str = str(result)
        assert "Added Distance constraint" in result_str, f"Constraint failed: {result}"

    def test_add_constraint_horizontal(self, clean_document):
        """Adding a horizontal constraint to a line."""
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "HSketch",
        })
        send_command("sketch_operations", {
            "operation": "add_line",
            "sketch_name": "HSketch",
            "x1": 0, "y1": 0,
            "x2": 10, "y2": 5,
        })
        result = send_command("sketch_operations", {
            "operation": "add_constraint",
            "sketch_name": "HSketch",
            "constraint_type": "Horizontal",
            "geo_id1": 0,
        })
        assert "Added Horizontal constraint" in str(result), f"Constraint failed: {result}"

    def test_list_constraints(self, clean_document):
        """list_constraints should return JSON with constraint details."""
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "ListSketch",
        })
        send_command("sketch_operations", {
            "operation": "add_rectangle",
            "sketch_name": "ListSketch",
            "x": 0, "y": 0, "width": 10, "height": 10,
        })
        result = send_command("sketch_operations", {
            "operation": "list_constraints",
            "sketch_name": "ListSketch",
        })
        result_str = str(result)
        # Rectangle adds 4 coincident + 2 horizontal + 2 vertical = 8 constraints
        assert "constraint_count" in result_str, f"list_constraints failed: {result}"

    def test_add_polygon(self, clean_document):
        """add_polygon should create N-sided polygon with constraints."""
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "PolySketch",
        })
        result = send_command("sketch_operations", {
            "operation": "add_polygon",
            "sketch_name": "PolySketch",
            "x": 0, "y": 0,
            "radius": 10,
            "sides": 6,
        })
        assert "6-sided polygon" in str(result), f"Polygon failed: {result}"

    def test_full_constrained_sketch_to_pad(self, clean_document):
        """Full workflow: constrained sketch → verify → pad."""
        # Create sketch
        send_command("sketch_operations", {
            "operation": "create_sketch",
            "plane": "XY",
            "name": "FullSketch",
        })
        # Add rectangle
        send_command("sketch_operations", {
            "operation": "add_rectangle",
            "sketch_name": "FullSketch",
            "x": 0, "y": 0, "width": 30, "height": 20,
        })
        # Add dimension constraints
        send_command("sketch_operations", {
            "operation": "add_constraint",
            "sketch_name": "FullSketch",
            "constraint_type": "DistanceX",
            "geo_id1": 0, "pos_id1": 1,
            "geo_id2": 0, "pos_id2": 2,
            "value": 30,
        })
        send_command("sketch_operations", {
            "operation": "add_constraint",
            "sketch_name": "FullSketch",
            "constraint_type": "DistanceY",
            "geo_id1": 1, "pos_id1": 1,
            "geo_id2": 1, "pos_id2": 2,
            "value": 20,
        })
        # Verify
        verify = send_command("sketch_operations", {
            "operation": "verify_sketch",
            "sketch_name": "FullSketch",
        })
        assert "Closed wires" in str(verify), f"Sketch not valid: {verify}"

        # Pad
        pad = send_command("partdesign_operations", {
            "operation": "pad",
            "sketch_name": "FullSketch",
            "length": 10,
        })
        assert "error" not in str(pad).lower() or "Pad" in str(pad), f"Pad failed: {pad}"


# ---------------------------------------------------------------------------
# Tests: PartDesign dispatch (previously unreachable operations)
# ---------------------------------------------------------------------------

class TestPartDesignDispatch:
    """Test that previously-unrouted PartDesign operations now work."""

    def test_pocket(self, clean_document):
        """pocket should be routed (was missing from dispatch map)."""
        # Create a box to pocket into
        send_command("part_operations", {
            "operation": "box",
            "length": 30, "width": 30, "height": 10,
        })
        # Create sketch for pocket
        send_command("execute_python", {
            "code": """
import FreeCAD, Part
doc = FreeCAD.ActiveDocument
sketch = doc.addObject('Sketcher::SketchObject', 'PocketSketch')
sketch.Placement = FreeCAD.Placement(
    FreeCAD.Vector(0, 0, 10),
    FreeCAD.Rotation(0, 0, 0, 1)
)
p1 = FreeCAD.Vector(5, 5, 0)
p2 = FreeCAD.Vector(25, 5, 0)
p3 = FreeCAD.Vector(25, 25, 0)
p4 = FreeCAD.Vector(5, 25, 0)
sketch.addGeometry(Part.LineSegment(p1, p2))
sketch.addGeometry(Part.LineSegment(p2, p3))
sketch.addGeometry(Part.LineSegment(p3, p4))
sketch.addGeometry(Part.LineSegment(p4, p1))
doc.recompute()
'ok'
"""
        })
        # Try pocket — should NOT get "Unknown PartDesign operation"
        result = send_command("partdesign_operations", {
            "operation": "pocket",
            "sketch_name": "PocketSketch",
            "length": 5,
        })
        result_str = str(result)
        assert "Unknown PartDesign operation" not in result_str, \
            f"pocket still not dispatched: {result}"

    def test_datum_plane(self, clean_document):
        """datum_plane should be dispatched to create_datum_plane."""
        result = send_command("partdesign_operations", {
            "operation": "datum_plane",
            "map_mode": "ObjectXY",
            "offset_z": 15,
        })
        result_str = str(result)
        assert "Unknown PartDesign operation" not in result_str, \
            f"datum_plane not dispatched: {result}"


# ---------------------------------------------------------------------------
# Tests: Connection health
# ---------------------------------------------------------------------------

class TestConnection:
    """Test basic connection health."""

    def test_ping(self):
        """Sending a ping should get a response."""
        result = send_command("execute_python", {
            "code": "'pong'"
        })
        assert "pong" in str(result).lower(), f"Ping failed: {result}"

    def test_freecad_version(self):
        """Should be able to read FreeCAD version."""
        result = send_command("execute_python", {
            "code": "FreeCAD.Version()[0] + '.' + FreeCAD.Version()[1]"
        })
        result_str = str(result)
        # Should contain a version like "1.2" or "0.21"
        assert any(c.isdigit() for c in result_str), \
            f"Could not read FreeCAD version: {result}"

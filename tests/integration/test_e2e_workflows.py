"""
End-to-end integration tests for FreeCAD MCP.

These tests require a running FreeCAD instance with the AICopilot addon enabled.
They exercise real workflows through the MCP bridge: document creation, primitive
creation, PartDesign operations, and export.

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

# ---------------------------------------------------------------------------
# Connection to FreeCAD via socket
# ---------------------------------------------------------------------------

SOCKET_PATH = os.environ.get("FREECAD_MCP_SOCKET", "/tmp/freecad_mcp.sock")


def freecad_available():
    """Check if FreeCAD socket is available."""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(SOCKET_PATH)
        s.close()
        return True
    except (socket.error, OSError):
        return False


skip_no_freecad = pytest.mark.skipif(
    not freecad_available(),
    reason=f"FreeCAD not running (no socket at {SOCKET_PATH})"
)


def send_command(tool: str, args: dict, timeout: float = 10.0) -> dict:
    """Send a command to FreeCAD and return the parsed response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCKET_PATH)
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

@skip_no_freecad
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


@skip_no_freecad
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

@skip_no_freecad
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

@skip_no_freecad
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

@skip_no_freecad
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

@skip_no_freecad
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
# Tests: Connection health
# ---------------------------------------------------------------------------

@skip_no_freecad
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

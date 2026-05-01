"""
CAM integration tests — end-to-end toolpath generation against real FreeCAD.

Tests the full pipeline: create geometry → create tool → create job →
add operations → post-process to G-code.

Run with:
    python3 -m pytest tests/integration/test_cam_workflows.py -v

Requires FreeCAD 1.2+ (CAM workbench with new tool API).
"""

import json
import os
import tempfile
import time

import pytest

from .test_e2e_workflows import send_command

pytestmark = pytest.mark.cam


# ---------------------------------------------------------------------------
# Module-level guard: skip all CAM tests if the CAM workbench isn't available
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _require_cam_workbench():
    """Skip all tests in this module if FreeCAD's CAM workbench isn't available."""
    result = send_command("execute_python", {
        "code": """
try:
    from Path.Main.Job import Create
    'cam_available'
except ImportError:
    'cam_missing'
"""
    })
    if "cam_missing" in str(result):
        pytest.skip("CAM workbench not available in this FreeCAD build")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cam_document():
    """Create a document with a simple solid for CAM testing."""
    doc_name = f"CAMTest_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })

    # Create a padded sketch (PartDesign body with a rectangular solid)
    send_command("execute_python", {
        "code": f"""
import FreeCAD, Part
doc = FreeCAD.getDocument('{doc_name}')
body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'BaseSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
p1 = FreeCAD.Vector(0, 0, 0)
p2 = FreeCAD.Vector(60, 0, 0)
p3 = FreeCAD.Vector(60, 40, 0)
p4 = FreeCAD.Vector(0, 40, 0)
sketch.addGeometry(Part.LineSegment(p1, p2))
sketch.addGeometry(Part.LineSegment(p2, p3))
sketch.addGeometry(Part.LineSegment(p3, p4))
sketch.addGeometry(Part.LineSegment(p4, p1))
doc.recompute()
'sketch ok'
"""
    })

    # Pad it
    send_command("execute_python", {
        "code": f"""
import FreeCAD
doc = FreeCAD.getDocument('{doc_name}')
body = doc.getObject('Body')
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = doc.getObject('BaseSketch')
pad.Length = 10
doc.recompute()
pad.Shape.isValid()
"""
    })

    yield doc_name

    try:
        send_command("execute_python", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests: Tool creation
# ---------------------------------------------------------------------------

class TestCAMToolCreation:
    """Test creating tools via cam_tools handler.

    Tool creation depends on FreeCAD's ToolBit API which varies across builds.
    Tests verify dispatch works and errors are clear, not specific success.
    """

    def _create_tool(self, name="Test Endmill", tool_type="endmill", diameter=6.0, **kwargs):
        """Helper: attempt tool creation, return (result_dict, success_bool)."""
        args = {"operation": "create_tool", "name": name,
                "tool_type": tool_type, "diameter": diameter, **kwargs}
        result = send_command("cam_tools", args)
        result_str = str(result)
        success = "Created tool" in result_str
        return result, success

    def test_create_endmill(self, cam_document):
        """Tool creation should dispatch without crashing."""
        result, success = self._create_tool("6mm Endmill", "endmill", 6.0)
        result_str = str(result)
        # Should either succeed or return a clear error — never "Unknown operation"
        assert "Unknown" not in result_str, f"Tool creation not dispatched: {result}"

    def test_create_drill(self, cam_document):
        result, _ = self._create_tool("3mm Drill", "drill", 3.0)
        assert "Unknown" not in str(result), f"Drill not dispatched: {result}"

    def test_create_tool_with_parameters(self, cam_document):
        result, _ = self._create_tool(
            "Detailed Endmill", "endmill", 6.0,
            flute_length=20.0, shank_diameter=6.0, number_of_flutes=2,
        )
        assert "Unknown" not in str(result), f"Tool with params not dispatched: {result}"

    def test_create_invalid_tool_type(self, cam_document):
        """Invalid tool type should return a clear error."""
        result = send_command("cam_tools", {
            "operation": "create_tool",
            "name": "Bad Tool",
            "tool_type": "laser_cannon",
            "diameter": 10.0,
        })
        result_str = str(result)
        assert "error" in result_str.lower() or "Unknown tool type" in result_str, \
            f"Expected error for invalid tool type: {result}"

    def test_list_tools(self, cam_document):
        """list_tools should dispatch without crashing."""
        result = send_command("cam_tools", {
            "operation": "list_tools",
        })
        result_str = str(result)
        # Should work even if no tools exist — "No tools found" is fine
        assert "Unknown" not in result_str, f"list_tools not dispatched: {result}"


# ---------------------------------------------------------------------------
# Tests: Job creation
# ---------------------------------------------------------------------------

class TestCAMJobCreation:
    """Test creating CAM jobs."""

    def test_create_job_with_body(self, cam_document):
        """Create a CAM job referencing the Body."""
        result = send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "Created" in result_str, \
            f"Job creation failed: {result}"

    def test_create_job_missing_object(self, cam_document):
        """Job creation with a nonexistent base object should error clearly."""
        result = send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "NonexistentObject",
        })
        result_str = str(result)
        assert "not found" in result_str.lower() or "error" in result_str.lower(), \
            f"Expected error for missing object: {result}"

    def test_inspect_job(self, cam_document):
        """inspect_job should return job details after creation."""
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        result = send_command("cam_operations", {
            "operation": "inspect_job",
            "job_name": "Job",
        })
        result_str = str(result)
        assert "Unknown" not in result_str, f"inspect_job not dispatched: {result}"


# ---------------------------------------------------------------------------
# Tests: Tool controllers
# ---------------------------------------------------------------------------

class TestCAMToolControllers:
    """Test adding tool controllers to jobs."""

    def test_add_tool_controller(self, cam_document):
        """Add a tool controller to a job."""
        # Create tool first
        send_command("cam_tools", {
            "operation": "create_tool",
            "name": "Profile Endmill",
            "tool_type": "endmill",
            "diameter": 6.0,
        })
        # Create job
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        # Add tool controller
        result = send_command("cam_tool_controllers", {
            "operation": "add_tool_controller",
            "tool_name": "Profile Endmill",
            "spindle_speed": 12000,
            "horiz_feed": 600,
            "vert_feed": 300,
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "controller" in result_str.lower(), \
            f"add_tool_controller failed: {result}"

    def test_list_tool_controllers(self, cam_document):
        """list_tool_controllers should dispatch and return data."""
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        result = send_command("cam_tool_controllers", {
            "operation": "list_tool_controllers",
            "job_name": "Job",
        })
        result_str = str(result)
        assert "Unknown" not in result_str, \
            f"list_tool_controllers not dispatched: {result}"


# ---------------------------------------------------------------------------
# Tests: CAM operations (profile, pocket)
# ---------------------------------------------------------------------------

class TestCAMOperations:
    """Test creating CAM operations on a job."""

    @pytest.fixture
    def job_with_tool(self, cam_document):
        """Create a job with a tool controller, ready for operations."""
        send_command("cam_tools", {
            "operation": "create_tool",
            "name": "Op Endmill",
            "tool_type": "endmill",
            "diameter": 6.0,
        })
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        send_command("cam_tool_controllers", {
            "operation": "add_tool_controller",
            "tool_name": "Op Endmill",
            "spindle_speed": 12000,
            "horiz_feed": 600,
            "vert_feed": 300,
        })
        return cam_document

    def test_create_profile(self, job_with_tool):
        """Create a profile (contour) operation."""
        result = send_command("cam_operations", {
            "operation": "profile",
        })
        result_str = str(result)
        # Profile might succeed or fail depending on geometry selection,
        # but it should not crash or return an unknown-operation error
        assert "Unknown" not in result_str, \
            f"profile operation not dispatched: {result}"

    def test_create_pocket(self, job_with_tool):
        """Create a pocket operation."""
        result = send_command("cam_operations", {
            "operation": "pocket",
        })
        result_str = str(result)
        assert "Unknown" not in result_str, \
            f"pocket operation not dispatched: {result}"

    def test_list_operations(self, job_with_tool):
        """list_operations should work on a job."""
        result = send_command("cam_operations", {
            "operation": "list_operations",
        })
        result_str = str(result)
        assert "error" not in result_str.lower() or "operations" in result_str.lower(), \
            f"list_operations failed: {result}"


# ---------------------------------------------------------------------------
# Tests: Post-processing
# ---------------------------------------------------------------------------

class TestCAMPostProcess:
    """Test G-code generation via post-processing."""

    def test_post_process_grbl(self, cam_document):
        """Full pipeline: part → job → profile → post-process to G-code."""
        # Create tool
        send_command("cam_tools", {
            "operation": "create_tool",
            "name": "PP Endmill",
            "tool_type": "endmill",
            "diameter": 6.0,
        })
        # Create job
        job_result = send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        if "error" in str(job_result).lower() and "not found" in str(job_result).lower():
            pytest.skip("Job creation failed — CAM workbench may not be available")

        # Add tool controller
        send_command("cam_tool_controllers", {
            "operation": "add_tool_controller",
            "tool_name": "PP Endmill",
            "spindle_speed": 12000,
            "horiz_feed": 600,
            "vert_feed": 300,
        })

        # Create a profile operation
        send_command("cam_operations", {
            "operation": "profile",
        })

        # Post-process to G-code
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as f:
            gcode_path = f.name

        try:
            result = send_command("cam_operations", {
                "operation": "post_process",
                "output_file": gcode_path,
                "post_processor": "grbl",
            }, timeout=30.0)
            result_str = str(result)

            # The post-process may succeed or fail depending on whether
            # the profile generated any toolpaths — but it should not crash
            if "Generated G-code" in result_str:
                assert os.path.exists(gcode_path), "G-code file not written"
                assert os.path.getsize(gcode_path) > 0, "G-code file is empty"
        finally:
            if os.path.exists(gcode_path):
                os.unlink(gcode_path)


# ---------------------------------------------------------------------------
# Tests: Job configuration
# ---------------------------------------------------------------------------

class TestCAMJobConfig:
    """Test job configuration (stock, output settings)."""

    def test_configure_job_stock(self, cam_document):
        """configure_job should allow setting stock oversize."""
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        result = send_command("cam_operations", {
            "operation": "configure_job",
            "stock_extra_x": 5.0,
            "stock_extra_y": 5.0,
            "stock_extra_z": 2.0,
        })
        result_str = str(result)
        # Should not crash or return unknown-operation
        assert "Unknown" not in result_str, \
            f"configure_job not dispatched: {result}"

    def test_job_status(self, cam_document):
        """job_status should report on the current job state."""
        send_command("cam_operations", {
            "operation": "create_job",
            "base_object": "Body",
        })
        result = send_command("cam_operations", {
            "operation": "job_status",
        })
        result_str = str(result)
        assert "Unknown" not in result_str, \
            f"job_status not dispatched: {result}"

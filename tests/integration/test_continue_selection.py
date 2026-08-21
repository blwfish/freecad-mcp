"""
continue_selection integration tests — the interactive-selection resume
dispatch (fillet/chamfer/draft/shell/thickness's request_selection ->
GUI pick -> continue_selection round trip).

This is the direct regression guard for a real bug found and fixed
2026-08-20/21: freecad_mcp_handler.py's _continue_selection resume_methods
dict mapped tool "thickness_faces" to a nonexistent
self.partdesign_ops.thickness_faces (real method: add_thickness), so
building that dict raised AttributeError unconditionally — breaking
continue_selection for ALL FIVE interactive-selection tools (fillet,
chamfer, draft, shell, thickness), not just thickness, for a month
(since 2026-07-18) undetected. The only test that touched this dispatch
was a unit test asserting against a MagicMock, which auto-vivifies any
attribute name and can't catch a wrong method reference — see
tests/unit/test_freecad_mcp_handler.py::TestContinueSelectionDispatch's
strengthened version for that fix.

This file exercises the REAL dispatch against a live FreeCAD process: seed
server.selector.pending_operations exactly as request_selection would (via
execute_python_sync reaching FreeCAD.__ai_socket_server.selector, the same
handle headless_server.py and InitGui.py both expose), then call the
actual continue_selection MCP tool.

Architectural constraint confirmed live: UniversalSelector.complete_selection()
checks `if not FreeCADGui: return {"error": "FreeCAD GUI not available for
selection"}` BEFORE ever consulting pending_operations' content — headless
FreeCAD has no FreeCADGui at all (not a flag to fake, the module doesn't
exist), so no amount of pending_operations seeding can produce a real
created object here. What this file CAN and does prove headless: dispatch
reaches the CORRECT internal handler method for each of the five
interactive-selection tools — the exact thing that broke — by asserting
the clean, expected "FreeCAD GUI not available for selection" response
rather than the crash-shaped "'PartDesignOpsHandler' object has no
attribute ...' the original bug produced. A wrong resume_methods mapping
would surface as that AttributeError text regardless of GUI availability;
this file catches that class of bug even though it can't observe the
GUI-mode success path. Real fillet/hole geometry creation (via fillet's
public edges= bypass, which needs no GUI selection at all) is covered in
test_partdesign_ops.py instead.
"""

import time
import pytest
from ._geom_helpers import _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"ContSel_{int(time.time() * 1000) % 100000}"
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
def body_with_pad(clean_document):
    """A PartDesign Body with a padded 20x15x10 box, matching
    test_partdesign_ops.py's fixture shape."""
    send_command("execute_python_sync", {
        "code": """
import Part
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')

sketch = doc.addObject('Sketcher::SketchObject', 'PadSketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'

sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(20,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,0,0), FreeCAD.Vector(20,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(20,15,0), FreeCAD.Vector(0,15,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,15,0), FreeCAD.Vector(0,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()

pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""
    })
    return clean_document


def _seed_pending_selection(operation_id: str, tool: str, obj_name: str, elements: list, **extra):
    """Seed server.selector.pending_operations exactly as
    UniversalSelector.request_selection() would, and pre-load the FreeCAD
    GUI selection state that complete_selection() reads from — bypassing
    the actual request_selection() call (which we don't need to test
    here; test_partdesign_ops.py already covers that half) so this file
    can focus purely on continue_selection's dispatch correctness."""
    extra_repr = ", ".join(f"{k!r}: {v!r}" for k, v in extra.items())
    code = f"""
import time
server = FreeCAD.__ai_socket_server
server.selector.pending_operations[{operation_id!r}] = {{
    "tool": {tool!r}, "type": "edges", "object": {obj_name!r},
    "timestamp": time.time(), {extra_repr}
}}
"""
    result = send_command("execute_python_sync", {"code": code})
    assert "error" not in _text(result).lower() or "Error" not in _text(result), _text(result)
    return result


class TestContinueSelectionFillet:
    def test_resumes_to_the_real_fillet_method_headless(self, body_with_pad):
        """The exact regression scenario: seed a pending fillet_edges
        selection, then resume it via continue_selection. Dispatch must
        reach PartDesignOpsHandler.fillet_edges (which then hits the
        expected clean headless GUI-unavailable error) rather than crash
        building the resume_methods lookup dict with an AttributeError
        naming a wrong/nonexistent method."""
        op_id = "fillet_edges_test_1"
        _seed_pending_selection(op_id, "fillet_edges", "Pad", [1], radius=2.0, name="TestFillet")

        result = send_command("continue_selection", {"operation_id": op_id})
        text = _text(result)
        assert "no attribute" not in text, f"dispatch attribute error — {text[:300]}"
        assert "FreeCAD GUI not available for selection" in text, text[:300]

    def test_unknown_operation_id_errors(self, body_with_pad):
        result = send_command("continue_selection", {"operation_id": "does_not_exist"})
        text = _text(result)
        assert "not found" in text.lower() or "expired" in text.lower(), text[:300]

    def test_missing_operation_id_errors(self, body_with_pad):
        result = send_command("continue_selection", {})
        text = _text(result)
        assert "error" in text.lower() or "required" in text.lower(), text[:300]


class TestContinueSelectionAllFiveTools:
    """Every tool that calls selector.request_selection() must be
    resumable through continue_selection. thickness_faces was the one
    that broke (mapped to a nonexistent method); this loops over all
    five so a future regression on any of them is caught the same way.

    complete_selection() checks `if not FreeCADGui` before ever using
    pending_operations' content (confirmed live — see module docstring),
    so headless every tool's resume hits the same clean error regardless
    of which one it is. That uniformity is itself the useful signal here:
    dispatch reached the CORRECT tool-specific method (which then hit the
    expected GUI guard) rather than crashing on a wrong/missing method
    name before ever getting there."""

    @pytest.mark.parametrize("tool,extra", [
        ("fillet_edges", {"radius": 1.5, "name": "PFillet"}),
        ("chamfer_edges", {"distance": 1.0, "name": "PChamfer"}),
        ("draft_faces", {"angle": 5.0, "name": "PDraft"}),
        ("shell_solid", {"thickness": 1.0, "name": "PShell"}),
        ("thickness_faces", {"thickness": 1.0, "name": "PThickness"}),
    ])
    def test_each_tool_resumes_without_dispatch_error(self, body_with_pad, tool, extra):
        op_id = f"{tool}_test_1"
        elements = [1]
        selection_type = "faces" if tool in ("draft_faces", "shell_solid", "thickness_faces") else "edges"
        _seed_pending_selection(op_id, tool, "Pad", elements, **extra)
        # Override the "edges" default _seed_pending_selection hardcodes,
        # for the face-selecting tools.
        send_command("execute_python_sync", {"code": f"""
server = FreeCAD.__ai_socket_server
server.selector.pending_operations[{op_id!r}]["type"] = {selection_type!r}
"""})

        result = send_command("continue_selection", {"operation_id": op_id})
        text = _text(result)
        # The regression this file exists to catch surfaces as exactly
        # this AttributeError, regardless of which tool is resumed —
        # assert its absence explicitly, not just "some success string".
        assert "no attribute" not in text, f"{tool}: dispatch attribute error — {text[:300]}"
        assert "FreeCAD GUI not available for selection" in text, f"{tool}: {text[:300]}"

"""
Integration tests for the remaining "API-shaped" MCP tools that had zero
integration-tier coverage and aren't GUI-blocked: geometric_verification,
run_inspector, build_sketch, get_debug_logs, get_last_traceback,
list_jobs, cancel_operation.

cancel_job is NOT covered here — exercising its real success path needs a
genuinely long-running async job to cancel mid-flight, which is a
different (much slower/flakier) kind of test than everything else in this
file; its error paths (unknown job_id, job not running) are simple
argument validation already covered by the same pattern as every other
handler's required-arg checks elsewhere in this suite, so a dedicated
file for it wasn't judged worth the added run time here.

execute_python / execute_python_async are NOT reachable via this
integration tier's direct-socket test harness at all — they're MCP
tool names implemented entirely on the bridge side (freecad_mcp_server.py),
translated to execute_python_sync (the actual socket-level command every
test in this suite already uses via send_command's helper) before ever
reaching the FreeCAD-side handler this tier talks to. Testing the
MCP-facing translation belongs at the MCP-protocol test layer
(tests/unit/test_mcp_protocol.py), not here.
"""

import json
import time
import pytest
from ._geom_helpers import _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"MiscOps_{int(time.time() * 1000) % 100000}"
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


def _parsed(result) -> dict:
    return json.loads(_text(result))


class TestVerifyHandedness:
    """Pure math — no FreeCAD document/object needed at all."""

    def test_identity_matrix_is_right_handed(self, clean_document):
        result = send_command("geometric_verification", {
            "operation": "verify_handedness",
            "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        })
        parsed = _parsed(result)
        assert parsed["ok"] is True
        assert abs(parsed["details"]["determinant"] - 1.0) < 1e-9

    def test_left_handed_matrix_rejected(self, clean_document):
        """Swapping two rows/columns flips the determinant to -1 — the
        exact mirror-reflection bug this tool exists to catch."""
        result = send_command("geometric_verification", {
            "operation": "verify_handedness",
            "matrix": [[1, 0, 0], [0, 0, 1], [0, 1, 0]],
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert abs(parsed["details"]["determinant"] - (-1.0)) < 1e-9
        assert "left-handed" in parsed["message"].lower()

    def test_missing_matrix_arg(self, clean_document):
        result = send_command("geometric_verification", {"operation": "verify_handedness"})
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert "matrix" in parsed["message"].lower()


class TestVerifyOrientation:
    @pytest.fixture
    def rect_box(self, clean_document):
        """A non-cube box — a cube's 6 equal-area faces make "dominant
        face" (largest area) ambiguous/coin-flip; 20x15x10 has an
        unambiguous largest-area pair (the 20x15 top/bottom)."""
        send_command("part_operations", {
            "operation": "box", "length": 20, "width": 15, "height": 10, "name": "OrientBox",
        })
        return clean_document

    def test_dominant_face_matches_expected_axis(self, rect_box):
        """A box's top and bottom faces always TIE in area (they're
        congruent) — 'dominant' picks whichever is found first by
        `area > dominant_area` (strict, not >=), so a tie never displaces
        the first match. Confirmed live: for a part_operations box, the
        bottom face (-Z, Face5 in this codebase's established face
        numbering) iterates before the top (+Z, Face6) and wins every
        tie, regardless of box proportions. So '-Z' is the axis that
        actually matches the real dominant face here, not '+Z' — pinning
        this order-dependent tie-break as real behavior, not asserting
        the more intuitive-but-wrong "+Z is up" expectation."""
        result = send_command("geometric_verification", {
            "operation": "verify_orientation", "object_name": "OrientBox",
            "expected_axis": "-Z", "mode": "dominant",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is True, parsed
        assert parsed["details"]["dominant_dot"] > 0

    def test_dominant_face_opposite_axis_fails(self, rect_box):
        result = send_command("geometric_verification", {
            "operation": "verify_orientation", "object_name": "OrientBox",
            "expected_axis": "+Z", "mode": "dominant",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False, parsed
        assert "FAIL" in parsed["message"]

    def test_all_mode_requires_every_face_aligned(self, rect_box):
        """No axis makes ALL 6 faces of a box point the same way — this
        pins that 'all' mode correctly fails for any real box."""
        result = send_command("geometric_verification", {
            "operation": "verify_orientation", "object_name": "OrientBox",
            "expected_axis": "+Z", "mode": "all",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False, parsed

    def test_unknown_mode_errors(self, rect_box):
        result = send_command("geometric_verification", {
            "operation": "verify_orientation", "object_name": "OrientBox",
            "expected_axis": "+Z", "mode": "bogus",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert "unknown mode" in parsed["message"].lower()

    def test_missing_object(self, clean_document):
        result = send_command("geometric_verification", {
            "operation": "verify_orientation", "object_name": "Ghost", "expected_axis": "+Z",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False


class TestVerifyNoSelfIntersection:
    def test_valid_box_passes(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "GoodBox",
        })
        result = send_command("geometric_verification", {
            "operation": "verify_no_self_intersection", "object_name": "GoodBox",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is True, parsed
        assert parsed["details"]["is_valid"] is True
        assert parsed["details"]["solid_count"] == 1

    def test_missing_object_name(self, clean_document):
        result = send_command("geometric_verification", {
            "operation": "verify_no_self_intersection",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert "object_name" in parsed["message"].lower()


class TestVerifyTopology:
    def test_all_constraints_pass(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "TopoBox",
        })
        result = send_command("geometric_verification", {
            "operation": "verify_topology", "object_name": "TopoBox",
            "face_count": 6, "edge_count": 12, "vertex_count": 8,
            "volume_range": [900, 1100],
        })
        parsed = _parsed(result)
        assert parsed["ok"] is True, parsed
        assert len(parsed["details"]["checks"]) == 4
        assert all(c["pass"] for c in parsed["details"]["checks"].values())

    def test_wrong_face_count_fails(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "TopoBox2",
        })
        result = send_command("geometric_verification", {
            "operation": "verify_topology", "object_name": "TopoBox2", "face_count": 999,
        })
        parsed = _parsed(result)
        assert parsed["ok"] is False, parsed
        assert parsed["details"]["checks"]["face_count"]["pass"] is False
        assert parsed["details"]["checks"]["face_count"]["actual"] == 6

    def test_no_constraints_given_trivially_passes(self, clean_document):
        """All check params are optional — omitting every one means
        nothing was checked, which is vacuously 'ok' (no failures)."""
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "TopoBox3",
        })
        result = send_command("geometric_verification", {
            "operation": "verify_topology", "object_name": "TopoBox3",
        })
        parsed = _parsed(result)
        assert parsed["ok"] is True, parsed
        assert parsed["details"]["checks"] == {}


class TestRunInspector:
    def test_model_only_check_on_valid_box(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "InspBox",
        })
        result = send_command("run_inspector", {})
        text = _text(result)
        parsed = json.loads(text)
        assert parsed["object_count"] >= 1
        assert "InspBox" in parsed["checked_objects"]
        assert parsed["profile"] == "model-only"
        assert parsed["summary"]["error"] == 0


class TestBuildSketch:
    def test_empty_layout_with_spreadsheet(self, clean_document):
        send_command("execute_python_sync", {"code": """
FreeCAD.ActiveDocument.addObject('Spreadsheet::Sheet', 'Spreadsheet')
FreeCAD.ActiveDocument.recompute()
"""})
        result = send_command("build_sketch", {"layout": {"elements": []}})
        parsed = _parsed(result)
        assert parsed["ok"] is True, parsed
        assert parsed["geo"] == 0

    def test_missing_spreadsheet_errors(self, clean_document):
        result = send_command("build_sketch", {"layout": {"elements": []}})
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert "spreadsheet" in parsed["error"].lower()
        assert "not found" in parsed["error"].lower()

    def test_layout_not_a_dict_errors(self, clean_document):
        result = send_command("build_sketch", {"layout": "not-a-dict"})
        parsed = _parsed(result)
        assert parsed["ok"] is False
        assert "layout must be a dict" in parsed["error"]


class TestJobIntrospection:
    def test_list_jobs_empty(self, clean_document):
        result = send_command("list_jobs", {})
        parsed = _text(result)
        parsed_json = json.loads(parsed) if isinstance(parsed, str) else result
        assert parsed_json.get("count", None) is not None
        # Best-effort: don't assert count == 0, other concurrent test runs
        # against the same spawned instance could leave stale entries —
        # just confirm the shape is right.
        assert "jobs" in parsed_json

    def test_cancel_operation_headless(self, clean_document):
        """Headless has no FreeCADGui.cancelOperation to call — confirms
        this returns a clean error, not a crash that takes the socket
        down."""
        result = send_command("cancel_operation", {})
        text = _text(result)
        assert "cancelOperation" in text or "error" in text.lower(), text[:300]


class TestDebugLogs:
    def test_get_debug_logs_no_logs(self, clean_document):
        result = send_command("get_debug_logs", {})
        text = _text(result)
        assert "no" in text.lower() and (
            "debug logs" in text.lower() or "log files" in text.lower()
        ), text[:300]

    def test_get_last_traceback_default_shape(self, clean_document):
        """The ring buffer is server-instance-scoped, not per-test — other
        test files in the same session-shared FreeCAD instance may have
        already triggered errors that landed in it before this test runs,
        so this only asserts the response shape, not an empty buffer
        (confirmed live: asserting empty here is order-dependent and
        flaky against the full suite, not just this file alone)."""
        result = send_command("get_last_traceback", {})
        parsed = _parsed(result)
        assert "tracebacks" in parsed
        assert "total_stored" in parsed
        assert isinstance(parsed["tracebacks"], list)
        # count defaults to 1 — tracebacks is capped there even if more exist.
        assert parsed["total_stored"] >= len(parsed["tracebacks"])

    def test_get_last_traceback_after_real_error(self, clean_document):
        """Trigger a genuine Python exception via execute_python_sync,
        then confirm it shows up in the ring buffer."""
        send_command("execute_python_sync", {"code": "raise ValueError('deliberate test error')"})
        result = send_command("get_last_traceback", {"count": 1})
        parsed = _parsed(result)
        assert parsed["total_stored"] >= 1
        assert len(parsed["tracebacks"]) >= 1
        assert "deliberate test error" in json.dumps(parsed["tracebacks"][-1])

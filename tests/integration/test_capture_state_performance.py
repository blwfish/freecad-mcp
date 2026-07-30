"""
Regression guard: ordinary commands must stay fast against a real, complex
document.

Background (see KNOWN_ISSUES.md / freecad-mr-generators session 2026-07-29):
commit 77b78885 added shape.isValid() -- a full OCCT BRep validity walk --
to capture_freecad_state()'s per-object capture, called unconditionally on
*every* MCP command whenever debug logging was enabled. Every existing test
for this path used MagicMock() objects, where shape.isValid() returns
instantly regardless of "document size" -- none of them could have caught
the real cost. Against a real document with multi-hundred-solid compounds
(slate tile courses, clipped Boolean fragments), a single list_objects call
took 38-75 seconds instead of ~5ms. The fix (CAPTURE_STATE_PER_COMMAND,
default off) is a runtime env-gate with no test of its own; this file is
that test -- it opens a real complex .FCStd fixture and asserts ordinary
commands stay fast, so a future change that makes capture_state()
unconditional again fails loudly here instead of silently in production.

This deliberately does NOT test capture_state() itself for speed -- it's
legitimate diagnostic work and is not required to be fast when explicitly
invoked. What must stay fast is the ORDINARY command path when the gate is
off (the production default), which is what every test below exercises.

Run with: python3 -m pytest tests/integration/test_capture_state_performance.py -v
"""

import os
import time

import pytest

from . import conftest as _conftest  # noqa: F401
from .test_e2e_workflows import send_command

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "equipment-hut.FCStd"
)

# Real-world baseline: the actual 2026-07-29 incident measured 38-75s for a
# single list_objects call against this class of document with the old
# unconditional capture_state() call. 3s gives generous headroom above the
# fixed behavior (~5ms) while remaining far below the regressed behavior --
# any reintroduction of unconditional per-object OCCT validation will blow
# through this by more than an order of magnitude, not marginally.
FAST_COMMAND_BUDGET_SECONDS = 3.0

# Sanity floor -- if the fixture failed to load (e.g. wrong path, corrupt
# file), object_count would be 0 or the open would error; this guards
# against the test passing for the wrong reason (an empty/failed document
# is trivially "fast").
MIN_EXPECTED_OBJECT_COUNT = 15


@pytest.fixture
def equipment_hut_document():
    """Open the real complex fixture document; close it afterward.

    Uses the open_document view_control operation (wired into the
    dispatch table alongside create_document/save_document -- previously
    implemented in document_ops.py but never registered, so unreachable
    via any MCP call before this fixture existed).
    """
    assert os.path.isfile(FIXTURE_PATH), f"Fixture missing: {FIXTURE_PATH}"

    result = send_command("view_control", {
        "operation": "open_document",
        "filename": FIXTURE_PATH,
    }, timeout=30.0)
    result_str = str(result)
    assert "error" not in result_str.lower() or "Opened document" in result_str, \
        f"Failed to open fixture document: {result}"

    doc_name = "equipment-hut"

    yield doc_name

    try:
        send_command("execute_python_sync", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass  # Best-effort cleanup


@pytest.mark.real_document
class TestCaptureStatePerformance:
    """Ordinary per-command latency against a real, non-trivial document.

    Marked real_document: this pulls in an 8MB LFS-tracked fixture and is
    slower/heavier than the rest of the suite. Run on PR, not on every push
    -- see integration-tests.yml's separate PR-only job."""

    def test_fixture_loads_with_substantial_object_count(self, equipment_hut_document):
        """Sanity check: the fixture actually loaded a real, non-trivial
        document -- not an empty or failed one. This is the precondition
        every other test in this file depends on; if this fails, the
        timing assertions below are meaningless (a failed/empty document
        is trivially fast for the wrong reason)."""
        result = send_command("view_control", {"operation": "list_objects"})
        result_str = str(result)
        assert "error" not in result_str.lower(), f"list_objects failed: {result}"

        # list_objects's own JSON shape uses "total" (not capture_state's
        # "object_count" -- a different code path with a different key).
        assert "total" in result_str, f"No total count in response: {result}"
        import json
        payload = result.get("result", result)
        if isinstance(payload, str):
            payload = json.loads(payload)
        # Rough count check is enough here -- the precise number isn't the
        # point, only that it's not suspiciously small (an empty or
        # near-empty document would make every timing test trivially fast
        # for the wrong reason).
        assert payload.get("total", 0) >= MIN_EXPECTED_OBJECT_COUNT, (
            f"Fixture document has suspiciously few objects "
            f"({payload.get('total')}) -- did it load correctly?"
        )

    def test_list_objects_stays_fast(self, equipment_hut_document):
        """The exact command that took 38-75s in the real 2026-07-29
        incident. With CAPTURE_STATE_PER_COMMAND unset (production
        default, matching this test environment), it must stay fast."""
        t0 = time.time()
        result = send_command("view_control", {"operation": "list_objects"}, timeout=30.0)
        elapsed = time.time() - t0

        assert "error" not in str(result).lower(), f"list_objects failed: {result}"
        assert elapsed < FAST_COMMAND_BUDGET_SECONDS, (
            f"list_objects took {elapsed:.2f}s against a real complex document -- "
            f"budget is {FAST_COMMAND_BUDGET_SECONDS}s. This is the exact "
            f"regression from commit 77b78885: shape.isValid() running "
            f"unconditionally on every object, on every command. Check "
            f"whether CAPTURE_STATE_PER_COMMAND (freecad_mcp_handler.py) is "
            f"still gating _capture_state() out of the per-command path."
        )

    def test_get_object_properties_stays_fast(self, equipment_hut_document):
        """A second, independent ordinary command on the same complex
        document -- guards against a regression that's specific to one
        command's code path rather than the shared per-command hook."""
        list_result = send_command("view_control", {"operation": "list_objects"})
        import json
        payload = list_result.get("result", list_result)
        if isinstance(payload, str):
            payload = json.loads(payload)
        objects = payload.get("objects", [])
        assert objects, f"No objects returned to probe: {list_result}"
        probe_name = objects[0]["name"]

        t0 = time.time()
        result = send_command("view_control", {
            "operation": "get_object_properties",
            "object_name": probe_name,
        }, timeout=30.0)
        elapsed = time.time() - t0

        assert "error" not in str(result).lower(), f"get_object_properties failed: {result}"
        assert elapsed < FAST_COMMAND_BUDGET_SECONDS, (
            f"get_object_properties took {elapsed:.2f}s against a real complex "
            f"document -- budget is {FAST_COMMAND_BUDGET_SECONDS}s."
        )

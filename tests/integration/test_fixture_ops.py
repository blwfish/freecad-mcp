"""
Fixture (snapshot regression) integration tests — save_fixture,
compare_to_fixture.

fixture_operations writes into the REAL repo fixtures/ directory (no
test-injectable override — see AICopilot/handlers/fixture_ops.py's
_fixtures_root(), which resolves two directories up from this file's own
location on the FreeCAD-side process, i.e. the checked-in fixtures/ dir).
Every fixture_name used here is uuid-suffixed and removed in a fixture
teardown so tests never collide with or leave behind real fixtures like
fixtures/shingle_complex_roof/.
"""

import json
import os
import shutil
import time
import uuid
import pytest
from ._geom_helpers import _result_text as _text
from .test_e2e_workflows import send_command

# fixtures/ lives at the repo root; this test file is at
# tests/integration/test_fixture_ops.py — two directories up.
_FIXTURES_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fixtures",
)


def _fixture_result(result) -> dict:
    """Parse fixture_operations' {"ok": ..., "details": ..., "message": ...}
    JSON envelope out of a send_command response."""
    text = _text(result)
    return json.loads(text)


@pytest.fixture
def clean_document():
    doc_name = f"FixtureOps_{int(time.time() * 1000) % 100000}"
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
def test_fixture_name():
    """A collision-proof fixture_name, cleaned up from the real repo
    fixtures/ directory in teardown regardless of what the test did to it."""
    name = f"test-fixture-{uuid.uuid4().hex[:8]}"
    yield name
    shutil.rmtree(os.path.join(_FIXTURES_ROOT, name), ignore_errors=True)


@pytest.fixture
def known_box(clean_document):
    send_command("part_operations", {
        "operation": "box", "length": 20, "width": 15, "height": 10, "name": "FixtureBox",
    })
    return clean_document


class TestSaveFixture:
    def test_save_fixture_success(self, known_box, test_fixture_name):
        result = send_command("fixture_operations", {
            "operation": "save_fixture",
            "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
            "description": "integration test fixture",
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is True, parsed
        files = parsed["details"]["files_written"]
        assert "topology.json" in files, files
        assert "shape.stl" in files, files
        assert "fixture.md" in files, files
        # Headless: no GUI, no active view — screenshot must not be captured.
        assert "screenshot.png" not in files, files
        assert parsed["details"]["screenshot_captured"] is False, parsed["details"]

        topo = parsed["details"]["topology"]
        assert topo["face_count"] == 6
        assert abs(topo["volume"] - 20 * 15 * 10) < 1e-6

        fdir = os.path.join(_FIXTURES_ROOT, test_fixture_name)
        assert os.path.isfile(os.path.join(fdir, "topology.json"))
        assert os.path.isfile(os.path.join(fdir, "shape.stl"))
        assert os.path.isfile(os.path.join(fdir, "fixture.md"))
        assert not os.path.isfile(os.path.join(fdir, "screenshot.png"))

    def test_save_fixture_missing_shape(self, clean_document, test_fixture_name):
        result = send_command("fixture_operations", {
            "operation": "save_fixture", "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False
        assert "shape" in parsed["message"].lower()

    def test_save_fixture_rejects_unsafe_name(self, known_box):
        result = send_command("fixture_operations", {
            "operation": "save_fixture", "shape": "FixtureBox",
            "fixture_name": "../evil",
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False
        assert "invalid fixture_name" in parsed["message"].lower()
        # Confirm nothing escaped fixtures/ — no directory literally named
        # "evil" one level above the fixtures root.
        escaped = os.path.join(os.path.dirname(_FIXTURES_ROOT), "evil")
        assert not os.path.exists(escaped)


class TestCompareToFixture:
    def test_round_trip_matches(self, known_box, test_fixture_name):
        save_result = send_command("fixture_operations", {
            "operation": "save_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        assert _fixture_result(save_result)["ok"] is True

        compare_result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(compare_result)
        assert parsed["ok"] is True, parsed
        assert "matches fixture" in parsed["message"]

    def test_resize_fails_volume_check(self, known_box, test_fixture_name):
        send_command("fixture_operations", {
            "operation": "save_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        # Resize in place — same object name, new dimensions.
        send_command("execute_python_sync", {"code": """
doc = FreeCAD.ActiveDocument
box = doc.getObject('FixtureBox')
box.Length = 40
doc.recompute()
"""})
        result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False, parsed
        assert parsed["details"]["checks"]["volume"]["ok"] is False
        assert "volume" in parsed["message"].lower()

    def test_pure_translation_fails_only_center_of_mass(self, known_box, test_fixture_name):
        """A pure translation leaves face/edge/vertex counts, volume, and
        bbox EXTENT unchanged, but shifts center_of_mass — the exact field
        schema v2 added specifically to catch a mirror/chirality flip
        (see fixture_ops.py's _SCHEMA_VERSION comment). bbox itself still
        shifts (it's absolute min/max, not extent), so both bbox and
        center_of_mass are expected to fail here; volume and topology
        counts must still pass."""
        send_command("fixture_operations", {
            "operation": "save_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        send_command("execute_python_sync", {"code": """
import FreeCAD
doc = FreeCAD.ActiveDocument
box = doc.getObject('FixtureBox')
box.Placement.Base = FreeCAD.Vector(100, 0, 0)
doc.recompute()
"""})
        result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False, parsed
        checks = parsed["details"]["checks"]
        assert checks["center_of_mass"]["ok"] is False, checks
        assert checks["volume"]["ok"] is True, checks
        for field in ("face_count", "edge_count", "vertex_count", "solids_count", "shells_count"):
            assert checks[field]["ok"] is True, (field, checks[field])

    def test_missing_fixture(self, known_box):
        result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "shape": "FixtureBox",
            "fixture_name": "does-not-exist-fixture",
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False
        assert "not found" in parsed["message"].lower()

    def test_schema_version_mismatch(self, known_box, test_fixture_name):
        fdir = os.path.join(_FIXTURES_ROOT, test_fixture_name)
        os.makedirs(fdir, exist_ok=True)
        with open(os.path.join(fdir, "topology.json"), "w") as f:
            json.dump({"schema_version": 1, "face_count": 6}, f)

        result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "shape": "FixtureBox",
            "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False
        assert "schema version mismatch" in parsed["message"].lower()

    def test_compare_missing_shape_arg(self, clean_document, test_fixture_name):
        result = send_command("fixture_operations", {
            "operation": "compare_to_fixture", "fixture_name": test_fixture_name,
        })
        parsed = _fixture_result(result)
        assert parsed["ok"] is False
        assert "shape" in parsed["message"].lower()

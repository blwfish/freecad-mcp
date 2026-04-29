"""Integration tests for api_introspection against a live FreeCAD instance.

Exercises inspect / search / record_useful against the real FreeCAD module
tree. Feedback is redirected to a per-test temp file via the
FREECAD_MCP_FEEDBACK_FILE env var on the FreeCAD side, so the user's real
~/.freecad-mcp/introspection_feedback.json is never touched.

Run with: python3 -m pytest tests/integration/test_introspection_ops.py -v
"""

import json
import os
import tempfile

import pytest

from . import conftest as _conftest  # noqa: F401
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_feedback_path(path: str) -> dict:
    """Set FREECAD_MCP_FEEDBACK_FILE env var inside the live instance.

    The introspection handler reads this var on every call, so changing the
    process env mid-session is enough — no module reload needed.
    """
    code = f"""
import os
os.environ['FREECAD_MCP_FEEDBACK_FILE'] = {path!r}
result = os.environ['FREECAD_MCP_FEEDBACK_FILE']
"""
    return send_command("execute_python", {"code": code}, timeout=10.0)


def _clear_feedback_path() -> dict:
    code = """
import os
os.environ.pop('FREECAD_MCP_FEEDBACK_FILE', None)
result = 'cleared'
"""
    return send_command("execute_python", {"code": code}, timeout=10.0)


def _intro(tool_args: dict) -> dict:
    resp = send_command("api_introspection", tool_args, timeout=30.0)
    if isinstance(resp, dict) and "result" in resp and isinstance(resp["result"], str):
        try:
            return json.loads(resp["result"])
        except json.JSONDecodeError:
            return resp
    return resp


# ---------------------------------------------------------------------------
# Fixture: per-test feedback file
# ---------------------------------------------------------------------------
@pytest.fixture
def feedback_file():
    with tempfile.TemporaryDirectory(prefix="freecad_mcp_intro_test_") as tmp:
        path = os.path.join(tmp, "feedback.json")
        _set_feedback_path(path)
        try:
            yield path
        finally:
            _clear_feedback_path()


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestInspectLive:
    def test_inspect_freecad_vector(self):
        """FreeCAD.Vector is a foundational class — must resolve."""
        result = _intro({"operation": "inspect", "path": "FreeCAD.Vector"})
        assert "error" not in result, result
        assert result["kind"] == "class"
        names = {m["name"] for m in result.get("members", [])}
        # Vector reliably has these
        assert "x" in names or "X" in names or len(names) > 0

    def test_inspect_part_makebox(self):
        """Part.makeBox is the canonical box-creation function."""
        result = _intro({"operation": "inspect", "path": "Part.makeBox"})
        assert "error" not in result, result
        # Part.makeBox is a Boost-Python function — kind may be 'builtin'
        # or 'function' depending on the build, but signature/doc should
        # be retrievable in some form.
        assert result["kind"] in ("function", "builtin")

    def test_inspect_unknown_attribute(self):
        result = _intro({"operation": "inspect",
                         "path": "FreeCAD.definitelyNotARealAttr"})
        assert "error" in result

    def test_inspect_module_top_level(self):
        result = _intro({"operation": "inspect", "path": "FreeCAD"})
        assert "error" not in result, result
        assert result["kind"] == "module"
        assert isinstance(result.get("members"), list)
        assert len(result["members"]) > 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestSearchLive:
    def test_search_finds_makebox(self):
        result = _intro({
            "operation": "search",
            "query": "makeBox",
            "modules": ["Part"],
        })
        assert "error" not in result, result
        paths = [r["path"] for r in result.get("results", [])]
        assert "Part.makeBox" in paths

    def test_search_default_modules_loaded(self):
        """Default module list should partially scan even if some workbenches
        aren't loaded in headless mode."""
        result = _intro({"operation": "search", "query": "makeBox"})
        assert "error" not in result, result
        # FreeCAD and Part must be scannable; some workbenches may be missing
        # in headless mode and that's OK — they show up in missing_modules.
        assert "FreeCAD" in result["scanned_modules"]
        assert "Part" in result["scanned_modules"]
        # At minimum, Part.makeBox should surface
        paths = [r["path"] for r in result["results"]]
        assert any("makeBox" in p for p in paths)

    def test_search_missing_modules_recorded(self):
        result = _intro({
            "operation": "search",
            "query": "anything",
            "modules": ["Part", "definitelyNotARealModule"],
        })
        missing_names = [m["module"] for m in result.get("missing_modules", [])]
        assert "definitelyNotARealModule" in missing_names

    def test_search_missing_query(self):
        result = _intro({"operation": "search"})
        assert "error" in result


# ---------------------------------------------------------------------------
# record_useful
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestRecordUsefulLive:
    def test_record_writes_to_redirected_file(self, feedback_file):
        result = _intro({
            "operation": "record_useful",
            "query": "make box",
            "path": "Part.makeBox",
        })
        assert result.get("recorded") is True
        # The handler reports the actual feedback file path it wrote to.
        assert result["feedback_file"] == feedback_file
        assert os.path.isfile(feedback_file)
        with open(feedback_file) as f:
            data = json.load(f)
        assert data["queries"]["make box"]["Part.makeBox"]["count"] == 1

    def test_feedback_boosts_subsequent_search(self, feedback_file):
        # Baseline ranking
        before = _intro({
            "operation": "search",
            "query": "makeBox",
            "modules": ["Part"],
        })
        for _ in range(8):
            _intro({
                "operation": "record_useful",
                "query": "makeBox",
                "path": "Part.makeBox",
            })
        after = _intro({
            "operation": "search",
            "query": "makeBox",
            "modules": ["Part"],
        })

        def find(rs, p):
            for r in rs:
                if r["path"] == p:
                    return r
            return None

        b = find(before["results"], "Part.makeBox")
        a = find(after["results"], "Part.makeBox")
        assert b is not None and a is not None
        # Score should rise; feedback_boost > 1 after recording
        assert a["feedback_boost"] > 1.0
        assert a["score"] >= b["score"]

"""Unit tests for IntrospectionOpsHandler.

Uses a synthetic 'fakecad' module mounted in sys.modules as the search target
so the tests don't depend on FreeCAD being installed.

Handler imports are deferred into fixtures so this test module does NOT trigger
`handlers/__init__.py` at collection time. That would cache every handler
module bound to whichever FreeCAD mock happened to be in sys.modules first,
breaking other test files like test_mesh_ops.

Run with: python3 -m pytest tests/unit/test_introspection_ops.py -v
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

# Make AICopilot importable, but DO NOT import any handler module here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))


# ---------------------------------------------------------------------------
# Synthetic fakecad module
# ---------------------------------------------------------------------------
def _build_fakecad() -> types.ModuleType:
    """Construct a stub package that looks like a small FreeCAD-style API."""
    fc = types.ModuleType("fakecad")
    fc.__doc__ = "Synthetic CAD module for tests."

    def make_box(length, width, height):
        """Create a box with the given dimensions."""
        return ("box", length, width, height)

    def make_cylinder(radius, height):
        """Create a cylinder with the given radius and height."""
        return ("cyl", radius, height)

    def fillet_edges(shape, edges, radius):
        """Apply a fillet of the given radius to selected edges."""
        return shape

    class Vector:
        """3D vector."""

        def __init__(self, x=0, y=0, z=0):
            self.x, self.y, self.z = x, y, z

        def length(self):
            """Return the vector length."""
            return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    fc.makeBox = make_box
    fc.makeCylinder = make_cylinder
    fc.filletEdges = fillet_edges
    fc.Vector = Vector

    sub = types.ModuleType("fakecad.sketcher")
    sub.__doc__ = "Sketcher submodule."

    class SketchObject:
        """A sketch container."""

        def addLine(self, p1, p2):
            """Add a line between two points."""
            return None

        def close(self):
            """Close the sketch."""
            return None

    sub.SketchObject = SketchObject
    fc.sketcher = sub
    return fc


@pytest.fixture(autouse=True)
def install_fakecad(monkeypatch):
    fc = _build_fakecad()
    monkeypatch.setitem(sys.modules, "fakecad", fc)
    monkeypatch.setitem(sys.modules, "fakecad.sketcher", fc.sketcher)
    yield


@pytest.fixture
def feedback_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "feedback.json")
        monkeypatch.setenv("FREECAD_MCP_FEEDBACK_FILE", path)
        yield path


@pytest.fixture
def handler():
    from handlers.introspection_ops import IntrospectionOpsHandler
    return IntrospectionOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


# Helpers — also deferred imports
def _fuzzy_score(*args, **kwargs):
    from handlers.introspection_ops import _fuzzy_score as fn
    return fn(*args, **kwargs)


def _recency_decay(*args, **kwargs):
    from handlers.introspection_ops import _recency_decay as fn
    return fn(*args, **kwargs)


def _feedback_boost(*args, **kwargs):
    from handlers.introspection_ops import _feedback_boost as fn
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Fuzzy scoring
# ---------------------------------------------------------------------------
class TestFuzzyScore:
    def test_exact_leaf_match_is_one(self):
        assert _fuzzy_score("makeBox", "fakecad.makeBox") == 1.0

    def test_substring_in_leaf_scores_high(self):
        assert _fuzzy_score("Box", "fakecad.makeBox") >= 0.75

    def test_unrelated_query_scores_low(self):
        assert _fuzzy_score("xyzzy", "fakecad.makeBox") < 0.5

    def test_empty_query_returns_zero(self):
        assert _fuzzy_score("", "fakecad.makeBox") == 0.0

    def test_case_insensitive(self):
        assert _fuzzy_score("MAKEBOX", "fakecad.makeBox") == 1.0


# ---------------------------------------------------------------------------
# Recency decay
# ---------------------------------------------------------------------------
class TestRecencyDecay:
    def test_now_is_full_weight(self):
        ts = datetime.now(timezone.utc).isoformat()
        assert _recency_decay(ts) == pytest.approx(1.0, abs=0.05)

    def test_thirty_days_is_half(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        assert _recency_decay(ts) == pytest.approx(0.5, abs=0.05)

    def test_floor_clamp(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
        assert _recency_decay(ts) == 0.1

    def test_invalid_timestamp_returns_one(self):
        assert _recency_decay("not-a-date") == 1.0

    def test_empty_returns_one(self):
        assert _recency_decay("") == 1.0


# ---------------------------------------------------------------------------
# Feedback boost
# ---------------------------------------------------------------------------
class TestFeedbackBoost:
    def test_no_feedback_returns_one(self):
        assert _feedback_boost({"queries": {}}, "q", "p") == 1.0

    def test_boost_increases_with_count(self):
        now = datetime.now(timezone.utc).isoformat()
        fb = {"queries": {"q": {"p": {"count": 5, "last_used": now}}}}
        boost = _feedback_boost(fb, "q", "p")
        assert boost > 1.0
        fb2 = {"queries": {"q": {"p": {"count": 50, "last_used": now}}}}
        assert _feedback_boost(fb2, "q", "p") > boost

    def test_boost_does_not_apply_to_other_queries(self):
        now = datetime.now(timezone.utc).isoformat()
        fb = {"queries": {"q": {"p": {"count": 100, "last_used": now}}}}
        assert _feedback_boost(fb, "different-query", "p") == 1.0


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------
class TestInspect:
    def test_inspect_function_returns_signature(self, handler):
        result = json.loads(handler.inspect({"path": "fakecad.makeBox"}))
        assert result["kind"] == "function"
        assert "length" in result["signature"]
        assert "Create a box" in result["doc"]

    def test_inspect_class_returns_members(self, handler):
        result = json.loads(handler.inspect({"path": "fakecad.Vector"}))
        assert result["kind"] == "class"
        names = [m["name"] for m in result["members"]]
        assert "length" in names

    def test_inspect_method(self, handler):
        result = json.loads(handler.inspect({"path": "fakecad.Vector.length"}))
        assert result["kind"] == "function"
        assert "Return the vector length" in result["doc"]

    def test_inspect_module(self, handler):
        result = json.loads(handler.inspect({"path": "fakecad"}))
        assert result["kind"] == "module"
        names = [m["name"] for m in result["members"]]
        assert "makeBox" in names
        assert "Vector" in names

    def test_inspect_unknown_attribute(self, handler):
        result = json.loads(handler.inspect({"path": "fakecad.nope"}))
        assert "error" in result

    def test_inspect_unknown_module(self, handler):
        result = json.loads(handler.inspect({"path": "noSuchModule.foo"}))
        assert "error" in result

    def test_inspect_missing_path(self, handler):
        result = json.loads(handler.inspect({}))
        assert "error" in result


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
class TestSearch:
    def test_search_finds_function(self, handler):
        result = json.loads(handler.search({
            "query": "makeBox",
            "modules": ["fakecad"],
        }))
        paths = [r["path"] for r in result["results"]]
        assert "fakecad.makeBox" in paths

    def test_search_fuzzy_partial(self, handler):
        result = json.loads(handler.search({
            "query": "fillet",
            "modules": ["fakecad"],
        }))
        paths = [r["path"] for r in result["results"]]
        assert any("fillet" in p.lower() for p in paths)

    def test_search_finds_in_submodule(self, handler):
        result = json.loads(handler.search({
            "query": "SketchObject",
            "modules": ["fakecad"],
        }))
        paths = [r["path"] for r in result["results"]]
        assert any("SketchObject" in p for p in paths)

    def test_search_missing_module_recorded(self, handler):
        result = json.loads(handler.search({
            "query": "anything",
            "modules": ["fakecad", "definitelyNotAModule"],
        }))
        assert any(m["module"] == "definitelyNotAModule" for m in result["missing_modules"])
        assert "fakecad" in result["scanned_modules"]

    def test_search_respects_limit(self, handler):
        result = json.loads(handler.search({
            "query": "make",
            "modules": ["fakecad"],
            "limit": 1,
        }))
        assert len(result["results"]) <= 1

    def test_search_missing_query(self, handler):
        result = json.loads(handler.search({"modules": ["fakecad"]}))
        assert "error" in result

    def test_search_results_are_sorted_by_score(self, handler):
        result = json.loads(handler.search({
            "query": "make",
            "modules": ["fakecad"],
        }))
        scores = [r["score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# record_useful + ranking influence
# ---------------------------------------------------------------------------
class TestRecordUseful:
    def test_record_creates_file(self, handler, feedback_file):
        result = json.loads(handler.record_useful({
            "query": "make box",
            "path": "fakecad.makeBox",
        }))
        assert result["recorded"] is True
        assert result["count"] == 1
        assert os.path.isfile(feedback_file)

    def test_record_increments_count(self, handler, feedback_file):
        handler.record_useful({"query": "q", "path": "fakecad.makeBox"})
        handler.record_useful({"query": "q", "path": "fakecad.makeBox"})
        result = json.loads(handler.record_useful({
            "query": "q", "path": "fakecad.makeBox",
        }))
        assert result["count"] == 3

    def test_record_requires_both_args(self, handler, feedback_file):
        result = json.loads(handler.record_useful({"query": "q"}))
        assert "error" in result
        result = json.loads(handler.record_useful({"path": "p"}))
        assert "error" in result

    def test_feedback_persists_across_handler_instances(self, handler, feedback_file):
        from handlers.introspection_ops import IntrospectionOpsHandler
        handler.record_useful({"query": "q", "path": "fakecad.makeBox"})
        h2 = IntrospectionOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))
        result = json.loads(h2.record_useful({
            "query": "q", "path": "fakecad.makeBox",
        }))
        assert result["count"] == 2

    def test_feedback_boosts_search_ranking(self, handler, feedback_file):
        before = json.loads(handler.search({
            "query": "make",
            "modules": ["fakecad"],
        }))
        for _ in range(10):
            handler.record_useful({"query": "make", "path": "fakecad.makeCylinder"})

        after = json.loads(handler.search({
            "query": "make",
            "modules": ["fakecad"],
        }))

        def find(results, path):
            for r in results:
                if r["path"] == path:
                    return r
            return None

        before_cyl = find(before["results"], "fakecad.makeCylinder")
        after_cyl = find(after["results"], "fakecad.makeCylinder")
        assert before_cyl is not None and after_cyl is not None
        assert after_cyl["score"] > before_cyl["score"]
        assert after_cyl["feedback_boost"] > 1.0


# ---------------------------------------------------------------------------
# Feedback file resilience
# ---------------------------------------------------------------------------
class TestFeedbackFileResilience:
    def test_corrupt_feedback_file_is_recovered(self, handler, feedback_file):
        with open(feedback_file, "w") as f:
            f.write("not valid json {{{")
        result = json.loads(handler.record_useful({
            "query": "q", "path": "fakecad.makeBox",
        }))
        assert result["count"] == 1

    def test_missing_feedback_file_creates_one(self, handler, feedback_file):
        assert not os.path.isfile(feedback_file)
        handler.record_useful({"query": "q", "path": "fakecad.makeBox"})
        assert os.path.isfile(feedback_file)

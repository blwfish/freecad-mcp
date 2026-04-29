"""Integration tests for spatial_query against a live FreeCAD instance.

The unit tests use mock geometry; spatial queries depend on real OCCT
behavior (boolean common(), distToShape, BoundBox.intersect). This is the
only way to validate that interference / clearance / containment / alignment
return realistic answers for real solids.

All responses are plain text strings (the spatial handler formats human
readable summaries rather than JSON), so tests assert on substrings.

Run with: python3 -m pytest tests/integration/test_spatial_ops.py -v
"""

import time

import pytest

from . import conftest as _conftest  # noqa: F401
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _spatial(args: dict, timeout: float = 15.0) -> str:
    resp = send_command("spatial_query", args, timeout=timeout)
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return str(resp)


def _exec(code: str, timeout: float = 10.0):
    return send_command("execute_python", {"code": code}, timeout=timeout)


# ---------------------------------------------------------------------------
# Fixture: a fresh document with two boxes positioned for the test scenario.
# ---------------------------------------------------------------------------
@pytest.fixture
def overlapping_boxes():
    """Two boxes that overlap in a known volume.

    BoxA: 10×10×10 at origin
    BoxB: 10×10×10 translated to (5, 5, 5) — overlap is a 5×5×5 = 125 mm³ cube
    """
    doc = f"Spatial_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {"operation": "create_document", "document_name": doc})
    _exec(f"""
import FreeCAD
d = FreeCAD.getDocument({doc!r})
a = d.addObject('Part::Box', 'BoxA')
a.Length = 10; a.Width = 10; a.Height = 10
b = d.addObject('Part::Box', 'BoxB')
b.Length = 10; b.Width = 10; b.Height = 10
b.Placement.Base = FreeCAD.Vector(5, 5, 5)
d.recompute()
""")
    yield doc
    try:
        _exec(f"FreeCAD.closeDocument({doc!r})")
    except Exception:
        pass


@pytest.fixture
def separated_boxes():
    """Two non-overlapping boxes 10mm apart along X."""
    doc = f"Spatial_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {"operation": "create_document", "document_name": doc})
    _exec(f"""
import FreeCAD
d = FreeCAD.getDocument({doc!r})
a = d.addObject('Part::Box', 'BoxA')
a.Length = 10; a.Width = 10; a.Height = 10
b = d.addObject('Part::Box', 'BoxB')
b.Length = 10; b.Width = 10; b.Height = 10
b.Placement.Base = FreeCAD.Vector(20, 0, 0)  # 10mm gap on +X face
d.recompute()
""")
    yield doc
    try:
        _exec(f"FreeCAD.closeDocument({doc!r})")
    except Exception:
        pass


@pytest.fixture
def nested_boxes():
    """Inner 5×5×5 box centered in an outer 20×20×20 box."""
    doc = f"Spatial_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {"operation": "create_document", "document_name": doc})
    _exec(f"""
import FreeCAD
d = FreeCAD.getDocument({doc!r})
inner = d.addObject('Part::Box', 'Inner')
inner.Length = 5; inner.Width = 5; inner.Height = 5
inner.Placement.Base = FreeCAD.Vector(7.5, 7.5, 7.5)
outer = d.addObject('Part::Box', 'Outer')
outer.Length = 20; outer.Width = 20; outer.Height = 20
d.recompute()
""")
    yield doc
    try:
        _exec(f"FreeCAD.closeDocument({doc!r})")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# interference_check
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestInterferenceCheck:
    def test_overlapping_boxes_intersect(self, overlapping_boxes):
        result = _spatial({"operation": "interference_check",
                           "object1": "BoxA", "object2": "BoxB"})
        assert "Intersects: True" in result
        # Overlap should be a 5×5×5 cube = 125 mm³
        assert "125" in result

    def test_separated_boxes_dont_intersect(self, separated_boxes):
        result = _spatial({"operation": "interference_check",
                           "object1": "BoxA", "object2": "BoxB"})
        assert "Intersects: False" in result
        # Should report a 10mm minimum clearance
        assert "Minimum clearance" in result
        assert "10.0" in result or "10.00" in result

    def test_missing_object(self, overlapping_boxes):
        result = _spatial({"operation": "interference_check",
                           "object1": "BoxA", "object2": "Ghost"})
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# clearance
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestClearance:
    def test_separated_boxes_report_distance(self, separated_boxes):
        result = _spatial({"operation": "clearance",
                           "object1": "BoxA", "object2": "BoxB"})
        # Ten mm gap on X axis
        assert "Minimum distance" in result
        assert "10.0" in result or "10.00" in result
        assert "Dominant gap axis: X" in result

    def test_touching_boxes_report_zero(self, separated_boxes):
        # Move BoxB so the X-faces touch exactly
        _exec(f"""
import FreeCAD
d = FreeCAD.ActiveDocument
d.BoxB.Placement.Base = FreeCAD.Vector(10, 0, 0)
d.recompute()
""")
        result = _spatial({"operation": "clearance",
                           "object1": "BoxA", "object2": "BoxB"})
        assert "TOUCHING" in result or "0.0000" in result


# ---------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestContainment:
    def test_inner_contained_in_outer(self, nested_boxes):
        result = _spatial({"operation": "containment",
                           "object1": "Inner", "object2": "Outer"})
        assert "Bounding box contained: True" in result
        assert "Geometric containment: True" in result

    def test_outer_not_contained_in_inner(self, nested_boxes):
        result = _spatial({"operation": "containment",
                           "object1": "Outer", "object2": "Inner"})
        assert "Bounding box contained: False" in result
        assert "Overhangs" in result


# ---------------------------------------------------------------------------
# batch_interference
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBatchInterference:
    def test_three_objects_one_collision(self, overlapping_boxes):
        # Add a third box that doesn't collide with either
        _exec("""
import FreeCAD
d = FreeCAD.ActiveDocument
c = d.addObject('Part::Box', 'BoxC')
c.Length = 5; c.Width = 5; c.Height = 5
c.Placement.Base = FreeCAD.Vector(50, 50, 50)
d.recompute()
""")
        result = _spatial({"operation": "batch_interference",
                           "objects": ["BoxA", "BoxB", "BoxC"]})
        assert "3 objects" in result
        assert "Collisions: 1" in result
        # The colliding pair should be A↔B (overlapping), not involving C
        assert "BoxA" in result and "BoxB" in result

    def test_too_few_objects(self, overlapping_boxes):
        result = _spatial({"operation": "batch_interference",
                           "objects": ["BoxA"]})
        assert "at least 2" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# alignment_check
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestAlignmentCheck:
    def test_aligned_boxes(self, separated_boxes):
        # The two boxes are at the same Y and Z, separated only on X
        result = _spatial({"operation": "alignment_check",
                           "object1": "BoxA", "object2": "BoxB",
                           "axis": "X"})
        # Center-of-mass alignment along X is meaningful here; both have CoMs
        # at the same Y=5, Z=5
        assert "BoxA" in result and "BoxB" in result

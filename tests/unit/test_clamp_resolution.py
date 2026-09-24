"""Threshold-boundary tests for view_ops._clamp_resolution.

Background (full-review 2026-09-23, Phase 3 mutation spot-check): this
function had ZERO test coverage of any kind -- a manual mutation of the
MEDIUM threshold's `>=` to `>` survived all 23 tests in the suite that
happened to touch view_ops.py, none of which actually called this function.
Per this repo's own CLAUDE.md Threshold-Boundary Testing Rule, every `>=`/`<`
comparison needs a test at the exact boundary, one below, and one above --
that's what pins the strict-vs-non-strict semantics this mutant proved were
otherwise unverified.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot', 'handlers'))

from tests.unit._freecad_mocks import reset_mocks  # noqa: F401 -- ensures mock_FreeCAD is installed
from handlers.view_ops import _clamp_resolution, _FACE_THRESH_MED, _FACE_THRESH_HIGH, _FACE_THRESH_HUGE


class TestClampResolutionMediumThreshold(unittest.TestCase):
    """_FACE_THRESH_MED (20_000): >= this face count caps at 800x600."""

    def test_one_below_threshold_is_not_clamped(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_MED - 1)
        self.assertEqual((w, h, clamped), (1920, 1080, False))

    def test_exactly_at_threshold_is_clamped(self):
        # This is the exact case the surviving >= -> > mutant broke: at
        # face_count == _FACE_THRESH_MED, >= is True (clamped) but > is
        # False (not clamped) -- only a test at this exact value can tell
        # them apart.
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_MED)
        self.assertEqual((w, h, clamped), (800, 600, True))

    def test_one_above_threshold_is_clamped(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_MED + 1)
        self.assertEqual((w, h, clamped), (800, 600, True))


class TestClampResolutionHighThreshold(unittest.TestCase):
    """_FACE_THRESH_HIGH (80_000): >= this face count caps at 640x480."""

    def test_one_below_threshold_uses_medium_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HIGH - 1)
        self.assertEqual((w, h, clamped), (800, 600, True))

    def test_exactly_at_threshold_uses_high_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HIGH)
        self.assertEqual((w, h, clamped), (640, 480, True))

    def test_one_above_threshold_uses_high_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HIGH + 1)
        self.assertEqual((w, h, clamped), (640, 480, True))


class TestClampResolutionHugeThreshold(unittest.TestCase):
    """_FACE_THRESH_HUGE (200_000): >= this face count caps at 400x300."""

    def test_one_below_threshold_uses_high_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HUGE - 1)
        self.assertEqual((w, h, clamped), (640, 480, True))

    def test_exactly_at_threshold_uses_huge_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HUGE)
        self.assertEqual((w, h, clamped), (400, 300, True))

    def test_one_above_threshold_uses_huge_cap(self):
        w, h, clamped = _clamp_resolution(1920, 1080, _FACE_THRESH_HUGE + 1)
        self.assertEqual((w, h, clamped), (400, 300, True))


class TestClampResolutionAmbiguousInputs(unittest.TestCase):
    """Ambiguous/degenerate inputs per the Threshold-Boundary Testing Rule."""

    def test_zero_faces_is_not_clamped(self):
        w, h, clamped = _clamp_resolution(1920, 1080, 0)
        self.assertEqual((w, h, clamped), (1920, 1080, False))

    def test_requested_resolution_already_below_cap_is_not_reported_clamped(self):
        # Above the MEDIUM threshold, but the caller asked for a resolution
        # already smaller than the cap in both dimensions -- min() leaves
        # both unchanged, so `was_clamped` must be False even though the
        # face count alone would normally trigger clamping.
        w, h, clamped = _clamp_resolution(320, 240, _FACE_THRESH_MED)
        self.assertEqual((w, h, clamped), (320, 240, False))

    def test_requested_width_below_cap_but_height_above_is_clamped(self):
        # Only one dimension needs clamping -- was_clamped must still be True.
        w, h, clamped = _clamp_resolution(100, 1080, _FACE_THRESH_MED)
        self.assertEqual((w, h, clamped), (100, 600, True))


if __name__ == "__main__":
    unittest.main()

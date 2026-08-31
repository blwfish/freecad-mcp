"""Tests for _Rotation.multiply's quaternion-based composition in
_freecad_mocks.py (full-review 2026-07-18 finding L10, fixed 2026-08-31).

The mock previously approximated rotation composition as angle addition
(`_Rotation(self.axis, self.angle + other.angle)`), which is only correct
when both rotations share a common axis -- for any other combination it
silently gave a wrong result. Undetected because nothing in this suite
called .multiply() with non-coincident axes; two production call sites
(transforms.py's rotate_object, partdesign_ops.py's feature placement) do
call it, so a real bug here could have masked a real bug there.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

from tests.unit._freecad_mocks import _Rotation, _Vec


def _vecs_close(a, b, tol=1e-9):
    return abs(a.x - b.x) < tol and abs(a.y - b.y) < tol and abs(a.z - b.z) < tol


class TestRotationMultiply:
    def test_common_axis_matches_angle_addition(self):
        """The one case where naive angle-addition happened to be correct --
        must still hold after switching to quaternion composition."""
        r1 = _Rotation(_Vec(0, 0, 1), 30)
        r2 = _Rotation(_Vec(0, 0, 1), 60)
        composed = r1.multiply(r2)
        v = _Vec(1, 0, 0)
        # 90 degrees about Z: (1,0,0) -> (0,1,0)
        assert _vecs_close(composed.multVec(v), _Vec(0, 1, 0))

    def test_non_coincident_axes_satisfy_composition_law(self):
        """result.multVec(v) must equal self.multVec(other.multVec(v)) --
        the fundamental correctness property of rotation composition, which
        angle-addition does not satisfy for non-coincident axes."""
        rx = _Rotation(_Vec(1, 0, 0), 90)
        ry = _Rotation(_Vec(0, 1, 0), 90)
        v = _Vec(1, 2, 3)
        lhs = rx.multiply(ry).multVec(v)
        rhs = rx.multVec(ry.multVec(v))
        assert _vecs_close(lhs, rhs)

    def test_non_coincident_axes_naive_angle_addition_would_be_wrong(self):
        """Pins the actual bug: the old `angle + angle` result for two
        90-degree rotations about different axes is not a 180-degree
        rotation about either axis -- confirms this case would have failed
        under the old implementation, not just passed by coincidence."""
        rx = _Rotation(_Vec(1, 0, 0), 90)
        ry = _Rotation(_Vec(0, 1, 0), 90)
        composed = rx.multiply(ry)
        naive_angle = rx.angle + ry.angle  # what the old code would have used
        assert composed.angle != naive_angle or composed.axis.x != rx.axis.x

    def test_identity_composition_is_no_op(self):
        identity = _Rotation()
        r = _Rotation(_Vec(1, 1, 1), 45)
        v = _Vec(1, 0, 0)
        assert _vecs_close(identity.multiply(r).multVec(v), r.multVec(v))
        assert _vecs_close(r.multiply(identity).multVec(v), r.multVec(v))

    def test_self_times_inverse_is_identity(self):
        r = _Rotation(_Vec(0.3, 0.6, 0.7), 73)
        composed = r.multiply(r.inverted())
        v = _Vec(5, -2, 1)
        # Looser tolerance than the other cases here -- degrees->radians->
        # trig->acos->degrees round-trips through this composition chain
        # accumulate float error on the order of 1e-7, not the ~1e-9 the
        # more direct compositions above hit.
        assert _vecs_close(composed.multVec(v), v, tol=1e-5)

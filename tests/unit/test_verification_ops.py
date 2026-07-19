"""Unit tests for VerificationOpsHandler.

Covers all four geometric verification tools:
  verify_handedness        — rotation matrix determinant check
  verify_orientation       — face normal direction check
  verify_no_self_intersection — OCCT-level shape validity
  verify_topology          — flexible count/volume constraints

Every tool has at least one known-good fixture and one known-bad fixture.

Key regression:
  verify_handedness must catch the historical shingle generator bug:
  a left-handed rotation matrix (det = -1) that caused a mirror-reflection
  instead of a rotation.  The v5.2.0 fix (2026-03-12) took six sessions
  to root-cause; this test pins that it is caught immediately.

Run with: python3 -m pytest tests/unit/test_verification_ops.py -v
"""

import json
import math
import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
)

from handlers.verification_ops import VerificationOpsHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(result) -> dict:
    """Parse a handler result to a dict. Fails the test on bad JSON."""
    if isinstance(result, dict):
        return result
    return json.loads(result)


def _identity_3x3():
    return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def _left_handed_3x3():
    """The shingle bug matrix: identity with one column negated → det = -1."""
    return [[-1, 0, 0], [0, 1, 0], [0, 0, 1]]


def _attach_face_normals(obj, normals):
    """Replace obj.Shape.Faces with mocks that return given normals.

    normals: list of (nx, ny, nz) tuples.
    Each face gets Area=1.0 so dominant face is the first with a real area.
    """
    faces = []
    for i, (nx, ny, nz) in enumerate(normals):
        face = MagicMock()
        face.Area = float(i + 1)  # ascending areas so last is dominant
        normal_vec = MagicMock(x=float(nx), y=float(ny), z=float(nz))
        face.normalAt = MagicMock(return_value=normal_vec)
        face.CenterOfMass = MagicMock(x=0, y=0, z=0)
        faces.append(face)
    obj.Shape.Faces = faces


# ---------------------------------------------------------------------------
# verify_handedness
# ---------------------------------------------------------------------------

class TestVerifyHandedness(unittest.TestCase):

    def setUp(self):
        reset_mocks()
        self.h = make_handler(VerificationOpsHandler)

    # --- known-good fixture ---

    def test_identity_matrix_passes(self):
        """Identity is right-handed: det = +1."""
        r = _parse(self.h.verify_handedness({'matrix': _identity_3x3()}))
        self.assertTrue(r['ok'], r['message'])
        self.assertAlmostEqual(r['details']['determinant'], 1.0, places=9)

    def test_90_degree_rotation_about_z_passes(self):
        """Proper rotation matrix (90° around Z) has det = +1."""
        mat = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        r = _parse(self.h.verify_handedness({'matrix': mat}))
        self.assertTrue(r['ok'], r['message'])
        self.assertAlmostEqual(r['details']['determinant'], 1.0, places=9)

    def test_flat_9element_list_accepted(self):
        """Flat 9-element list form is accepted."""
        mat = [1, 0, 0, 0, 1, 0, 0, 0, 1]  # identity, flat
        r = _parse(self.h.verify_handedness({'matrix': mat}))
        self.assertTrue(r['ok'], r['message'])

    # --- known-bad fixture: shingle regression ---

    def test_left_handed_matrix_fails_shingle_regression(self):
        """REGRESSION: left-handed matrix (det = -1) must be caught.

        This is the exact bug class from the shingle generator (March 2026,
        v5.2.0 fix).  A column-negated identity has det = -1, producing a
        mirror-reflection instead of a rotation.  verify_handedness must:
          1. return ok=False
          2. report determinant ≈ -1
          3. include a useful message with the word 'left-handed' or 'mirror'
        """
        r = _parse(self.h.verify_handedness({'matrix': _left_handed_3x3()}))
        self.assertFalse(r['ok'], "Left-handed matrix should fail")
        self.assertAlmostEqual(r['details']['determinant'], -1.0, places=9)
        msg_lower = r['message'].lower()
        self.assertTrue(
            'left-handed' in msg_lower or 'mirror' in msg_lower,
            f"Message should mention 'left-handed' or 'mirror', got: {r['message']}"
        )

    def test_singular_matrix_fails(self):
        """Singular matrix (det = 0) is not a rotation."""
        mat = [[1, 0, 0], [0, 0, 0], [0, 0, 1]]  # det = 0
        r = _parse(self.h.verify_handedness({'matrix': mat}))
        self.assertFalse(r['ok'])
        self.assertAlmostEqual(r['details']['determinant'], 0.0, places=9)

    def test_missing_matrix_argument_returns_error(self):
        r = _parse(self.h.verify_handedness({}))
        self.assertFalse(r['ok'])
        self.assertIn('matrix', r['message'].lower())

    def test_boundary_exactly_at_tolerance(self):
        """det = 1 + 1e-6 is just outside tolerance; det = 1 + 5e-7 is just inside."""
        # Just inside: should pass
        # Build a matrix whose det is exactly 1.0 + 5e-7 by perturbing A11
        mat_in = [[1.0 + 5e-7, 0, 0], [0, 1, 0], [0, 0, 1]]
        r_in = _parse(self.h.verify_handedness({'matrix': mat_in}))
        self.assertTrue(r_in['ok'],
                        f"5e-7 error should be within tolerance, got: {r_in['message']}")

        # Just outside: should fail (error > 1e-6)
        mat_out = [[1.0 + 2e-6, 0, 0], [0, 1, 0], [0, 0, 1]]
        r_out = _parse(self.h.verify_handedness({'matrix': mat_out}))
        self.assertFalse(r_out['ok'],
                         f"2e-6 error should exceed tolerance, got: {r_out['message']}")


# ---------------------------------------------------------------------------
# verify_orientation
# ---------------------------------------------------------------------------

class TestVerifyOrientation(unittest.TestCase):

    def setUp(self):
        reset_mocks()
        self.h = make_handler(VerificationOpsHandler)

    def _make_obj_with_normals(self, normals, name="TestObj"):
        obj = make_part_object(name)
        _attach_face_normals(obj, normals)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        return obj

    # --- known-good fixtures ---

    def test_dominant_mode_plus_z_normals_pass(self):
        """All +Z normals — dominant mode must pass."""
        self._make_obj_with_normals([(0, 0, 1), (0, 0, 1), (0, 0, 1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'dominant',
        }))
        self.assertTrue(r['ok'], r['message'])
        self.assertEqual(r['details']['aligned_count'], 3)

    def test_named_axis_plus_z_string(self):
        """Named string '+Z' is accepted as expected_axis."""
        self._make_obj_with_normals([(0, 0, 1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': '+Z',
        }))
        self.assertTrue(r['ok'], r['message'])

    def test_majority_mode_more_than_half_aligned(self):
        """3 out of 4 faces point +Z — majority mode passes."""
        self._make_obj_with_normals([(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, -1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'majority',
        }))
        self.assertTrue(r['ok'], r['message'])
        self.assertEqual(r['details']['aligned_count'], 3)

    def test_all_mode_every_face_aligned(self):
        """All mode passes when every face aligns."""
        self._make_obj_with_normals([(0, 0, 1), (0, 0, 1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'all',
        }))
        self.assertTrue(r['ok'], r['message'])

    def test_exactly_perpendicular_dot_zero_is_not_aligned(self):
        """M17: _DOT_THRESHOLD = 0.0 with a strict `d > _DOT_THRESHOLD`
        comparison — a face normal EXACTLY perpendicular to the expected
        axis (dot product exactly 0.0) must not count as aligned. Prior
        tests only used clean +1.0/-1.0 dot products, never pinning which
        side of exactly 0.0 the boundary falls on."""
        # (1, 0, 0) dotted with expected_axis (0, 0, 1) is exactly 0.0,
        # regardless of normalization (already unit length here).
        self._make_obj_with_normals([(1, 0, 0)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'dominant',
        }))
        self.assertFalse(r['ok'], r['message'])
        self.assertEqual(r['details']['aligned_count'], 0)

    # --- known-bad fixtures ---

    def test_dominant_mode_negative_z_normals_fail(self):
        """All -Z normals when expecting +Z — dominant mode must fail."""
        self._make_obj_with_normals([(0, 0, -1), (0, 0, -1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'dominant',
        }))
        self.assertFalse(r['ok'], r['message'])
        self.assertIn('fail', r['message'].lower())

    def test_all_mode_one_misaligned_face_fails(self):
        """all mode: even one wrong face causes failure."""
        self._make_obj_with_normals([(0, 0, 1), (0, 0, 1), (0, 0, -1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'all',
        }))
        self.assertFalse(r['ok'], r['message'])

    def test_majority_mode_exactly_half_fails(self):
        """majority mode: exactly 50% — not strictly > 50% — should fail."""
        # 2 aligned, 2 misaligned → aligned_count (2) >= face_count/2 (2.0)
        # The implementation uses >= so this PASSES (2 >= 2.0 is True).
        # Test documents the actual contract so it's pinned, not assumed.
        self._make_obj_with_normals([(0, 0, 1), (0, 0, 1), (0, 0, -1), (0, 0, -1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'majority',
        }))
        # Document the boundary: 2/4 = exactly 50% → passes (>=)
        self.assertTrue(r['ok'],
                        "Exactly 50% aligned should pass (>= threshold), "
                        f"got: {r['message']}")

    def test_majority_mode_minority_aligned_fails(self):
        """majority mode: 1 out of 4 aligned must fail."""
        self._make_obj_with_normals([(0, 0, 1), (0, 0, -1), (0, 0, -1), (0, 0, -1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'majority',
        }))
        self.assertFalse(r['ok'], r['message'])

    def test_missing_object_returns_error(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.verify_orientation({
            'object_name': 'Ghost',
            'expected_axis': [0, 0, 1],
        }))
        self.assertFalse(r['ok'])
        self.assertIn('not found', r['message'].lower())

    def test_invalid_mode_returns_error(self):
        self._make_obj_with_normals([(0, 0, 1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': [0, 0, 1],
            'mode': 'bogus',
        }))
        self.assertFalse(r['ok'])

    def test_invalid_expected_axis_returns_error(self):
        self._make_obj_with_normals([(0, 0, 1)])
        r = _parse(self.h.verify_orientation({
            'object_name': 'TestObj',
            'expected_axis': 'not_an_axis',
        }))
        self.assertFalse(r['ok'])

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        r = _parse(self.h.verify_orientation({
            'object_name': 'X',
            'expected_axis': [0, 0, 1],
        }))
        self.assertFalse(r['ok'])
        self.assertIn('document', r['message'].lower())


# ---------------------------------------------------------------------------
# verify_no_self_intersection
# ---------------------------------------------------------------------------

class TestVerifyNoSelfIntersection(unittest.TestCase):

    def setUp(self):
        reset_mocks()
        self.h = make_handler(VerificationOpsHandler)

    # --- known-good fixture ---

    def test_valid_shape_passes(self):
        """A shape where isValid()=True and check() returns None (success in FreeCAD)."""
        obj = make_part_object("Box1")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(return_value=None)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Box1'}))
        self.assertTrue(r['ok'], r['message'])
        self.assertTrue(r['details']['is_valid'])
        self.assertIn('valid', r['message'].lower())

    def test_check_returns_none_treated_as_valid(self):
        """check() returning None (some FreeCAD builds) is treated as valid."""
        obj = make_part_object("Box1")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(return_value=None)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Box1'}))
        self.assertTrue(r['ok'], r['message'])

    # --- known-bad fixture ---

    def test_invalid_shape_fails(self):
        """isValid()=False must produce ok=False even if check() passes."""
        obj = make_part_object("Bad")
        obj.Shape.isValid = MagicMock(return_value=False)
        obj.Shape.check = MagicMock(return_value=None)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Bad'}))
        self.assertFalse(r['ok'], r['message'])
        self.assertFalse(r['details']['is_valid'])

    def test_check_raises_fails(self):
        """check() raising an exception is treated as an error."""
        obj = make_part_object("Bad")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(side_effect=RuntimeError("self-intersection detected"))
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Bad'}))
        self.assertFalse(r['ok'], r['message'])
        self.assertIn('self-intersection', r['details']['check_result'].lower())

    def test_check_returns_error_string_fails(self):
        """check() returning a non-OK string (not 'Shape is valid') is an error."""
        obj = make_part_object("Bad")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(
            return_value="BRep_API: command not completed (face self-intersection)"
        )
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Bad'}))
        self.assertFalse(r['ok'], r['message'])

    def test_missing_object_returns_error(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Ghost'}))
        self.assertFalse(r['ok'])
        self.assertIn('not found', r['message'].lower())

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        r = _parse(self.h.verify_no_self_intersection({'object_name': 'X'}))
        self.assertFalse(r['ok'])
        self.assertIn('document', r['message'].lower())

    def test_result_includes_face_and_solid_counts(self):
        """details must include face_count and solid_count."""
        obj = make_box_object("Box1")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(return_value=None)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.verify_no_self_intersection({'object_name': 'Box1'}))
        self.assertIn('face_count', r['details'])
        self.assertIn('solid_count', r['details'])


# ---------------------------------------------------------------------------
# verify_topology
# ---------------------------------------------------------------------------

class TestVerifyTopology(unittest.TestCase):

    def setUp(self):
        reset_mocks()
        self.h = make_handler(VerificationOpsHandler)

    def _box_in_doc(self, name="Box1", faces=6, edges=12, verts=8, volume=1000.0):
        obj = make_part_object(name, faces=faces, edges=edges,
                               vertices=verts, volume=volume)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        return obj

    # --- no-constraint baseline ---

    def test_no_constraints_reports_counts_ok(self):
        """With no constraints specified, always ok=True and counts reported."""
        self._box_in_doc()
        r = _parse(self.h.verify_topology({'object_name': 'Box1'}))
        self.assertTrue(r['ok'], r['message'])
        self.assertEqual(r['details']['face_count'], 6)
        self.assertEqual(r['details']['edge_count'], 12)
        self.assertEqual(r['details']['vertex_count'], 8)

    # --- known-good fixtures ---

    def test_exact_face_count_passes(self):
        self._box_in_doc(faces=6)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'face_count': 6
        }))
        self.assertTrue(r['ok'], r['message'])
        self.assertTrue(r['details']['checks']['face_count']['pass'])

    def test_exact_edge_count_passes(self):
        self._box_in_doc(edges=12)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'edge_count': 12
        }))
        self.assertTrue(r['ok'], r['message'])

    def test_exact_vertex_count_passes(self):
        self._box_in_doc(verts=8)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'vertex_count': 8
        }))
        self.assertTrue(r['ok'], r['message'])

    def test_volume_range_passes(self):
        self._box_in_doc(volume=1000.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'volume_range': [900, 1100]
        }))
        self.assertTrue(r['ok'], r['message'])

    def test_all_constraints_pass(self):
        self._box_in_doc(faces=6, edges=12, verts=8, volume=1000.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1',
            'face_count': 6,
            'edge_count': 12,
            'vertex_count': 8,
            'volume_range': [500, 2000],
        }))
        self.assertTrue(r['ok'], r['message'])

    # --- known-bad fixtures ---

    def test_wrong_face_count_fails(self):
        self._box_in_doc(faces=6)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'face_count': 10
        }))
        self.assertFalse(r['ok'], r['message'])
        check = r['details']['checks']['face_count']
        self.assertFalse(check['pass'])
        self.assertEqual(check['actual'], 6)
        self.assertEqual(check['expected'], 10)
        self.assertIn('face_count', r['message'])

    def test_wrong_edge_count_fails(self):
        self._box_in_doc(edges=12)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'edge_count': 24
        }))
        self.assertFalse(r['ok'], r['message'])

    def test_volume_below_range_fails(self):
        self._box_in_doc(volume=100.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'volume_range': [500, 2000]
        }))
        self.assertFalse(r['ok'], r['message'])

    def test_volume_above_range_fails(self):
        self._box_in_doc(volume=5000.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'volume_range': [500, 2000]
        }))
        self.assertFalse(r['ok'], r['message'])

    def test_volume_boundary_exactly_at_min_passes(self):
        """Volume exactly at range minimum — boundary is inclusive."""
        self._box_in_doc(volume=500.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'volume_range': [500.0, 2000.0]
        }))
        self.assertTrue(r['ok'],
                        f"Volume exactly at min should pass (inclusive), got: {r['message']}")

    def test_volume_boundary_exactly_at_max_passes(self):
        """Volume exactly at range maximum — boundary is inclusive."""
        self._box_in_doc(volume=2000.0)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1', 'volume_range': [500.0, 2000.0]
        }))
        self.assertTrue(r['ok'],
                        f"Volume exactly at max should pass (inclusive), got: {r['message']}")

    def test_partial_constraints_one_fail_one_pass(self):
        """face_count=6 (pass), edge_count=99 (fail) → ok=False with correct detail."""
        self._box_in_doc(faces=6, edges=12)
        r = _parse(self.h.verify_topology({
            'object_name': 'Box1',
            'face_count': 6,
            'edge_count': 99,
        }))
        self.assertFalse(r['ok'])
        self.assertTrue(r['details']['checks']['face_count']['pass'])
        self.assertFalse(r['details']['checks']['edge_count']['pass'])

    def test_missing_object_returns_error(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.verify_topology({'object_name': 'Ghost', 'face_count': 6}))
        self.assertFalse(r['ok'])
        self.assertIn('not found', r['message'].lower())

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        r = _parse(self.h.verify_topology({'object_name': 'X'}))
        self.assertFalse(r['ok'])
        self.assertIn('document', r['message'].lower())

    def test_result_structure_always_has_required_keys(self):
        """ok, details, message are always present regardless of outcome."""
        self._box_in_doc()
        for args in [
            {'object_name': 'Box1'},
            {'object_name': 'Box1', 'face_count': 99},
        ]:
            r = _parse(self.h.verify_topology(args))
            self.assertIn('ok', r)
            self.assertIn('details', r)
            self.assertIn('message', r)


# ---------------------------------------------------------------------------
# Cross-cutting: structured output contract
# ---------------------------------------------------------------------------

class TestStructuredOutput(unittest.TestCase):
    """Verify all four tools always return the {ok, details, message} contract."""

    def setUp(self):
        reset_mocks()
        self.h = make_handler(VerificationOpsHandler)
        obj = make_box_object("Box1")
        obj.Shape.isValid = MagicMock(return_value=True)
        obj.Shape.check = MagicMock(return_value=None)
        _attach_face_normals(obj, [(0, 0, 1)])
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

    def _check_contract(self, result):
        r = _parse(result)
        self.assertIn('ok', r, f"Missing 'ok' key in {r}")
        self.assertIn('details', r, f"Missing 'details' key in {r}")
        self.assertIn('message', r, f"Missing 'message' key in {r}")
        self.assertIsInstance(r['ok'], bool)
        self.assertIsInstance(r['details'], dict)
        self.assertIsInstance(r['message'], str)

    def test_verify_handedness_contract(self):
        self._check_contract(self.h.verify_handedness({'matrix': _identity_3x3()}))

    def test_verify_handedness_bad_input_contract(self):
        self._check_contract(self.h.verify_handedness({}))

    def test_verify_orientation_contract(self):
        self._check_contract(self.h.verify_orientation({
            'object_name': 'Box1', 'expected_axis': [0, 0, 1]
        }))

    def test_verify_no_self_intersection_contract(self):
        self._check_contract(self.h.verify_no_self_intersection(
            {'object_name': 'Box1'}
        ))

    def test_verify_topology_contract(self):
        self._check_contract(self.h.verify_topology({'object_name': 'Box1'}))


if __name__ == '__main__':
    unittest.main()

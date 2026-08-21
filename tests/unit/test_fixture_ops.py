"""Unit tests for FixtureOpsHandler.

Two tools under test:
  save_fixture        — topology.json + STL + fixture.md, idempotent
  compare_to_fixture  — structured diff with ok boolean

Key acceptance criteria:
  1. Round-trip: save_fixture then compare_to_fixture on the same shape → ok=True
  2. Diff detection: save fixture, modify shape topology, compare → ok=False with
     structured info (face_count delta, volume error, bbox delta as appropriate)
  3. Missing fixture → ok=False with actionable message
  4. Bad fixture_name → ok=False
  5. Tolerance overrides work

Run with: python3 -m pytest tests/unit/test_fixture_ops.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
)

from handlers.fixture_ops import FixtureOpsHandler, _fixtures_root
import handlers.fixture_ops as _fixture_ops_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(result) -> dict:
    if isinstance(result, dict):
        return result
    return json.loads(result)


def _make_handler_in_tmpdir(tmp_dir):
    """Create a FixtureOpsHandler and patch _fixtures_root to use tmp_dir."""
    h = make_handler(FixtureOpsHandler)
    return h


def _make_box_obj(name="Box", volume=1000.0, faces=6, edges=12, vertices=8,
                  xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0, zmin=0.0, zmax=10.0,
                  is_solid=True, is_closed=True):
    """Create a mock box-like object with specified topology."""
    obj = make_part_object(
        name,
        volume=volume, faces=faces, edges=edges, vertices=vertices,
        bbox=(xmax - xmin, ymax - ymin, zmax - zmin),
        is_closed=is_closed,
        # fixture_ops.py derives is_solid from bool(shape.Solids), not a
        # shape.isSolid() call — that method doesn't exist on real FreeCAD
        # Part.Shape/Part.Solid objects as of weekly-2026.08.20 (confirmed
        # live, fixed 2026-08-21). solids=1 when is_solid=True mirrors a
        # real solid's own .Solids traversal including itself; solids=0
        # mirrors a bare Face/open Shell's empty .Solids.
        solids=1 if is_solid else 0,
    )
    # Override bbox mins (make_part_object sets XMin=0 by default)
    bb = obj.Shape.BoundBox
    bb.XMin, bb.XMax = xmin, xmax
    bb.YMin, bb.YMax = ymin, ymax
    bb.ZMin, bb.ZMax = zmin, zmax
    return obj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaveFixtureValidation(unittest.TestCase):
    """Input validation for save_fixture."""

    def setUp(self):
        reset_mocks()
        self.h = make_handler(FixtureOpsHandler)

    def test_missing_shape_argument(self):
        r = _parse(self.h.save_fixture({'fixture_name': 'test'}))
        self.assertFalse(r['ok'])
        self.assertIn('shape', r['message'].lower())

    def test_missing_fixture_name_argument(self):
        r = _parse(self.h.save_fixture({'shape': 'Box'}))
        self.assertFalse(r['ok'])
        self.assertIn('fixture_name', r['message'].lower())

    def test_bad_fixture_name_path_traversal(self):
        r = _parse(self.h.save_fixture({'shape': 'Box', 'fixture_name': '../evil'}))
        self.assertFalse(r['ok'])
        self.assertIn('invalid', r['message'].lower())

    def test_bad_fixture_name_slash(self):
        r = _parse(self.h.save_fixture({'shape': 'Box', 'fixture_name': 'foo/bar'}))
        self.assertFalse(r['ok'])

    def test_bad_fixture_name_empty(self):
        r = _parse(self.h.save_fixture({'shape': 'Box', 'fixture_name': ''}))
        self.assertFalse(r['ok'])

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        r = _parse(self.h.save_fixture({'shape': 'Box', 'fixture_name': 'test'}))
        self.assertFalse(r['ok'])
        self.assertIn('document', r['message'].lower())

    def test_object_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.save_fixture({'shape': 'NonExistent', 'fixture_name': 'test'}))
        self.assertFalse(r['ok'])
        self.assertIn('not found', r['message'].lower())

    def test_object_without_shape(self):
        # spec limits attribute access to only 'Name' and 'Label' — no Shape
        obj = MagicMock(spec=['Name', 'Label'])
        obj.Name = 'NoShape'
        obj.Label = 'NoShape'
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.save_fixture({'shape': 'NoShape', 'fixture_name': 'test'}))
        self.assertFalse(r['ok'])
        self.assertIn('shape', r['message'].lower())


class TestSaveAndCompareRoundTrip(unittest.TestCase):
    """Core round-trip and diff-detection tests.

    These run against a real temp directory so topology.json / fixture.md
    are written and read back for real.
    """

    def setUp(self):
        reset_mocks()
        self.h = make_handler(FixtureOpsHandler)
        self.tmp = tempfile.mkdtemp(prefix='fixture_test_')
        # Patch _fixtures_root on the module object directly so the patch works
        # regardless of how the module was imported (handles multi-path imports
        # in the full test suite where 'handlers.fixture_ops' may also appear
        # as a different sys.modules key).
        self._orig_fixtures_root = _fixture_ops_module._fixtures_root
        _fixture_ops_module._fixtures_root = lambda: self.tmp

    def tearDown(self):
        _fixture_ops_module._fixtures_root = self._orig_fixtures_root
        # Clean up temp files
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_doc(self, obj):
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        return doc

    # ------------------------------------------------------------------
    # Round-trip: same shape should give ok=True
    # ------------------------------------------------------------------

    def test_round_trip_same_shape_returns_ok_true(self):
        """ACCEPTANCE: build shape, save_fixture, compare → ok=True."""
        obj = _make_box_obj('Box', volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj)

        # Save
        sv = _parse(self.h.save_fixture({
            'shape': 'Box',
            'fixture_name': 'round_trip',
            'description': 'Round-trip test box',
        }))
        self.assertTrue(sv['ok'], f"save_fixture failed: {sv['message']}")
        self.assertIn('topology.json', sv['details']['files_written'])
        self.assertIn('fixture.md', sv['details']['files_written'])

        # Compare same object → should pass
        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'Box',
            'fixture_name': 'round_trip',
        }))
        self.assertTrue(cmp['ok'],
                        f"Round-trip compare failed: {cmp['message']}\nChecks: {cmp['details'].get('checks')}")

    def test_round_trip_topology_json_persisted_correctly(self):
        """topology.json on disk must contain expected schema_version and counts."""
        obj = _make_box_obj('MyBox', volume=500.0, faces=10, edges=20, vertices=12)
        self._setup_doc(obj)
        self.h.save_fixture({'shape': 'MyBox', 'fixture_name': 'topo_check'})

        topo_path = os.path.join(self.tmp, 'topo_check', 'topology.json')
        self.assertTrue(os.path.exists(topo_path))
        with open(topo_path) as f:
            data = json.load(f)
        self.assertEqual(data['schema_version'], 2)
        self.assertEqual(data['face_count'], 10)
        self.assertEqual(data['edge_count'], 20)
        self.assertEqual(data['vertex_count'], 12)
        self.assertAlmostEqual(data['volume'], 500.0, places=4)
        self.assertIn('bbox', data)
        self.assertIn('x', data['bbox'])
        self.assertIn('is_solid', data)
        self.assertIn('is_closed', data)

    def test_fixture_md_written(self):
        """fixture.md must be written with fixture name and description."""
        obj = _make_box_obj('BoxMd')
        self._setup_doc(obj)
        self.h.save_fixture({
            'shape': 'BoxMd',
            'fixture_name': 'md_test',
            'description': 'This is a test fixture for the round-trip scenario.',
        })
        md_path = os.path.join(self.tmp, 'md_test', 'fixture.md')
        self.assertTrue(os.path.exists(md_path))
        with open(md_path) as f:
            content = f.read()
        self.assertIn('md_test', content)
        self.assertIn('BoxMd', content)
        self.assertIn('round-trip scenario', content)

    def test_save_fixture_is_idempotent(self):
        """Calling save_fixture twice must not raise; second call overwrites cleanly."""
        obj = _make_box_obj('Idem')
        self._setup_doc(obj)
        for _ in range(2):
            r = _parse(self.h.save_fixture({'shape': 'Idem', 'fixture_name': 'idem_test'}))
            self.assertTrue(r['ok'], r['message'])

    # ------------------------------------------------------------------
    # Diff detection: modified shape must give ok=False
    # ------------------------------------------------------------------

    def test_chirality_flip_detected_via_center_of_mass(self):
        """The motivating bug this whole module exists to catch (see the
        module docstring: the v5.2.0 left-handed rotation matrix). Face/
        edge/vertex counts, volume, and bbox extents are ALL invariant
        under a mirror — a v1-schema fixture (pre-CenterOfMass) would NOT
        have caught this exact bug. center_of_mass is the field that
        actually shifts under a mirror; this proves the comparison now
        catches it."""
        obj = _make_box_obj('Shape', faces=6, edges=12, vertices=8, volume=1000.0,
                             xmin=0, xmax=10, ymin=0, ymax=10, zmin=0, zmax=10)
        self._setup_doc(obj)
        self.h.save_fixture({'shape': 'Shape', 'fixture_name': 'chirality'})

        # Simulate a mirrored/chirality-flipped rebuild: identical counts,
        # volume, and bbox (all mirror-invariant) but a shifted center of
        # mass (what a left-handed rotation matrix bug would produce).
        obj.Shape.CenterOfMass.x = obj.Shape.CenterOfMass.x + 5.0

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'Shape', 'fixture_name': 'chirality',
        }))

        self.assertFalse(cmp['ok'], "chirality flip must be detected, not silently passed")
        self.assertIn('center_of_mass', cmp['details']['checks'])
        self.assertFalse(cmp['details']['checks']['center_of_mass']['ok'])

    def test_diff_face_count_change_detected(self):
        """ACCEPTANCE: add faces (topology change) → compare returns ok=False."""
        obj_orig = _make_box_obj('Shape', faces=6, edges=12, vertices=8, volume=1000.0)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'Shape', 'fixture_name': 'diff_faces'})

        # Now create a shape with more faces
        obj_mod = _make_box_obj('Shape', faces=12, edges=20, vertices=10, volume=1000.0)
        doc = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'Shape',
            'fixture_name': 'diff_faces',
        }))
        self.assertFalse(cmp['ok'],
                         f"Expected ok=False on face count change, got: {cmp['message']}")
        # Structured info: face_count check must show the delta
        checks = cmp['details']['checks']
        self.assertIn('face_count', checks)
        self.assertFalse(checks['face_count']['ok'])
        self.assertEqual(checks['face_count']['actual'], 12)
        self.assertEqual(checks['face_count']['saved'], 6)
        self.assertEqual(checks['face_count']['delta'], 6)

    def test_diff_volume_change_detected(self):
        """Volume change > 0.1% → compare returns ok=False with relative error."""
        obj_orig = _make_box_obj('ShapeV', volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'ShapeV', 'fixture_name': 'diff_vol'})

        # 2% larger volume — well outside 0.1% tolerance
        obj_mod = _make_box_obj('ShapeV', volume=1020.0, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeV',
            'fixture_name': 'diff_vol',
        }))
        self.assertFalse(cmp['ok'])
        checks = cmp['details']['checks']
        self.assertIn('volume', checks)
        self.assertFalse(checks['volume']['ok'])
        self.assertAlmostEqual(checks['volume']['relative_error'], 0.02, places=3)

    def test_diff_bbox_change_detected(self):
        """Bbox shift > 0.001 mm → compare returns ok=False."""
        obj_orig = _make_box_obj('ShapeBB', xmax=10.0, ymax=10.0, zmax=10.0,
                                  volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'ShapeBB', 'fixture_name': 'diff_bbox'})

        # Scale the shape by 1.1 in X — bbox shifts by 1 mm, well over tolerance
        obj_mod = _make_box_obj('ShapeBB', xmax=11.0, ymax=10.0, zmax=10.0,
                                 volume=1100.0, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeBB',
            'fixture_name': 'diff_bbox',
        }))
        self.assertFalse(cmp['ok'])
        checks = cmp['details']['checks']
        self.assertIn('bbox', checks)
        self.assertFalse(checks['bbox']['ok'])

    def test_diff_volume_within_tolerance_passes(self):
        """Volume change < 0.1% (tiny FP drift) must still pass."""
        obj_orig = _make_box_obj('ShapeSmall', volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'ShapeSmall', 'fixture_name': 'small_drift'})

        # 0.05% change — well within 0.1% tolerance
        obj_mod = _make_box_obj('ShapeSmall', volume=1000.5, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeSmall',
            'fixture_name': 'small_drift',
        }))
        self.assertTrue(cmp['ok'],
                        f"0.05% volume drift should pass, got: {cmp['message']}")

    def test_diff_exactly_at_volume_tolerance_boundary(self):
        """Volume exactly at 0.1% tolerance boundary pins the < vs <= contract.

        The implementation uses <= so exactly 0.1% passes.
        """
        obj_orig = _make_box_obj('ShapeTol', volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'ShapeTol', 'fixture_name': 'tol_boundary'})

        # Exactly at 0.1% (1000 * 0.001 = 1.0) → should pass (<=)
        obj_at = _make_box_obj('ShapeTol', volume=1001.0, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj_at])
        mock_FreeCAD.ActiveDocument = doc
        cmp_at = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeTol', 'fixture_name': 'tol_boundary',
        }))
        self.assertTrue(cmp_at['ok'],
                        f"Volume exactly at 0.1% should pass (<=), got: {cmp_at['message']}")

        # Just over 0.1% → should fail
        obj_over = _make_box_obj('ShapeTol', volume=1001.1, faces=6, edges=12, vertices=8)
        doc2 = make_mock_doc([obj_over])
        mock_FreeCAD.ActiveDocument = doc2
        cmp_over = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeTol', 'fixture_name': 'tol_boundary',
        }))
        self.assertFalse(cmp_over['ok'],
                         f"Volume just over 0.1% should fail, got: {cmp_over['message']}")

    # ------------------------------------------------------------------
    # Tolerance override
    # ------------------------------------------------------------------

    def test_tolerance_override_volume(self):
        """Caller can loosen volume tolerance to accept larger variation."""
        obj_orig = _make_box_obj('ShapeLoose', volume=1000.0, faces=6, edges=12, vertices=8)
        self._setup_doc(obj_orig)
        self.h.save_fixture({'shape': 'ShapeLoose', 'fixture_name': 'loose_vol'})

        # 5% change — fails with default 0.1% but should pass with 10% override
        obj_mod = _make_box_obj('ShapeLoose', volume=1050.0, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'ShapeLoose',
            'fixture_name': 'loose_vol',
            'tolerances': {'volume_rel_tol': 0.10},  # 10%
        }))
        self.assertTrue(cmp['ok'],
                        f"5% change with 10% tolerance should pass, got: {cmp['message']}")

    def test_tolerance_used_is_reported_in_details(self):
        """compare_to_fixture must report which tolerances were actually used."""
        obj = _make_box_obj('TolReport')
        self._setup_doc(obj)
        self.h.save_fixture({'shape': 'TolReport', 'fixture_name': 'tol_report'})

        cmp = _parse(self.h.compare_to_fixture({
            'shape': 'TolReport',
            'fixture_name': 'tol_report',
            'tolerances': {'volume_rel_tol': 0.05, 'bbox_abs_tol': 0.01},
        }))
        tols = cmp['details']['tolerances_used']
        self.assertAlmostEqual(tols['volume_rel_tol'], 0.05)
        self.assertAlmostEqual(tols['bbox_abs_tol'], 0.01)


class TestCompareToFixtureValidation(unittest.TestCase):
    """Input validation and missing-fixture errors for compare_to_fixture."""

    def setUp(self):
        reset_mocks()
        self.h = make_handler(FixtureOpsHandler)
        self.tmp = tempfile.mkdtemp(prefix='fixture_cmp_test_')
        self._orig_fixtures_root = _fixture_ops_module._fixtures_root
        _fixture_ops_module._fixtures_root = lambda: self.tmp

    def tearDown(self):
        _fixture_ops_module._fixtures_root = self._orig_fixtures_root
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_shape_argument(self):
        r = _parse(self.h.compare_to_fixture({'fixture_name': 'x'}))
        self.assertFalse(r['ok'])
        self.assertIn('shape', r['message'].lower())

    def test_missing_fixture_name_argument(self):
        r = _parse(self.h.compare_to_fixture({'shape': 'Box'}))
        self.assertFalse(r['ok'])
        self.assertIn('fixture_name', r['message'].lower())

    def test_fixture_not_found(self):
        """compare_to_fixture on a nonexistent fixture gives ok=False with save hint."""
        obj = _make_box_obj('Box')
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        r = _parse(self.h.compare_to_fixture({'shape': 'Box', 'fixture_name': 'no_such_fixture'}))
        self.assertFalse(r['ok'])
        # Must hint at save_fixture
        self.assertIn('save_fixture', r['message'])

    def test_fixture_dir_exists_but_topology_json_missing(self):
        """If fixture/ dir exists but topology.json is absent, report clearly."""
        fixture_dir = os.path.join(self.tmp, 'broken_fixture')
        os.makedirs(fixture_dir, exist_ok=True)
        # Don't write topology.json

        obj = _make_box_obj('Box')
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        r = _parse(self.h.compare_to_fixture({'shape': 'Box', 'fixture_name': 'broken_fixture'}))
        self.assertFalse(r['ok'])
        self.assertIn('topology.json', r['message'])

    def test_bad_fixture_name(self):
        r = _parse(self.h.compare_to_fixture({'shape': 'Box', 'fixture_name': '../etc/passwd'}))
        self.assertFalse(r['ok'])
        self.assertIn('invalid', r['message'].lower())

    def test_details_include_actual_and_saved_topology(self):
        """compare_to_fixture details must include both actual and saved topology."""
        obj = _make_box_obj('DetailBox', volume=1000.0, faces=6, edges=12, vertices=8)
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        self.h.save_fixture({'shape': 'DetailBox', 'fixture_name': 'detail_test'})

        cmp = _parse(self.h.compare_to_fixture({'shape': 'DetailBox', 'fixture_name': 'detail_test'}))
        self.assertIn('actual', cmp['details'])
        self.assertIn('saved', cmp['details'])
        self.assertIn('face_count', cmp['details']['actual'])
        self.assertIn('face_count', cmp['details']['saved'])

    def test_message_contains_failure_details_on_diff(self):
        """ok=False message must be human-readable with field names."""
        obj_orig = _make_box_obj('MsgBox', faces=6, edges=12, vertices=8, volume=1000.0)
        doc = make_mock_doc([obj_orig])
        mock_FreeCAD.ActiveDocument = doc
        self.h.save_fixture({'shape': 'MsgBox', 'fixture_name': 'msg_test'})

        obj_mod = _make_box_obj('MsgBox', faces=10, edges=12, vertices=8, volume=1000.0)
        doc2 = make_mock_doc([obj_mod])
        mock_FreeCAD.ActiveDocument = doc2

        cmp = _parse(self.h.compare_to_fixture({'shape': 'MsgBox', 'fixture_name': 'msg_test'}))
        self.assertFalse(cmp['ok'])
        # Message should mention what changed
        self.assertIn('face_count', cmp['message'])


class TestFixtureNameValidation(unittest.TestCase):
    """Boundary tests for fixture name validation."""

    def setUp(self):
        reset_mocks()
        self.h = make_handler(FixtureOpsHandler)

    def _call(self, name):
        # We don't need a real doc for name-validation tests — they short-circuit
        return _parse(self.h.save_fixture({'shape': 'x', 'fixture_name': name}))

    def test_valid_names_accepted_structure(self):
        """Valid fixture names must not get rejected at the name-validation gate.

        We don't need a FreeCAD doc for this — just check the error message
        doesn't say 'invalid' when the name is structurally fine.
        (The test will fail later at 'No active document', not 'invalid name'.)
        """
        mock_FreeCAD.ActiveDocument = None
        for name in ('shingle_dormer', 'test-fixture', 'roof.v2', 'a1b2c3'):
            r = self._call(name)
            # Should NOT fail with 'invalid' in the message
            if not r['ok']:
                self.assertNotIn('invalid', r['message'].lower(),
                                 f"Name {name!r} should be structurally valid, got: {r['message']}")

    def test_invalid_names_rejected(self):
        """Invalid fixture names must fail.

        Empty string fails with 'fixture_name' (missing-arg path).
        Non-empty but invalid names fail with 'invalid' in the message.
        """
        mock_FreeCAD.ActiveDocument = None

        # Empty string is caught as missing arg, not invalid-name
        r_empty = self._call('')
        self.assertFalse(r_empty['ok'], "Empty name should be rejected")

        # Non-empty but structurally invalid names must produce 'invalid'
        for name in ('../escape', 'foo/bar', '/absolute', '.hidden'):
            r = self._call(name)
            self.assertFalse(r['ok'], f"Name {name!r} should be rejected")
            self.assertIn('invalid', r['message'].lower(),
                          f"Name {name!r} should produce 'invalid' message, got: {r['message']}")


if __name__ == '__main__':
    unittest.main()

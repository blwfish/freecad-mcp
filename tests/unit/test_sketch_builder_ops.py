"""Unit tests for SketchBuilderOpsHandler.

Tests cover:
  - build_sketch success path (validate + emit, returns dof/geo/constraints)
  - build_sketch validation failure (returns conflicts)
  - Missing document
  - Missing spreadsheet
  - Element dispatch: envelope, hline, arch, arch_array, door, monitor
  - Unknown element type raises
  - _read_spreadsheet_params alias extraction
  - _apply_layout iterates all element types
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# Install FreeCAD mocks BEFORE importing the handler
from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_mock_doc,
    assert_error_contains,
    assert_success_contains,
)

# ---------------------------------------------------------------------------
# Stub out sketch_builder (FC-tools) — not on the test sys.path
# ---------------------------------------------------------------------------

_mock_sketch_builder_module = MagicMock()
_mock_sketch_builder_class = MagicMock()
_mock_sketch_builder_module.SketchBuilder = _mock_sketch_builder_class
sys.modules['sketch_builder'] = _mock_sketch_builder_module


# ---------------------------------------------------------------------------
# Now we can import the handler
# ---------------------------------------------------------------------------

from handlers.sketch_builder_ops import (  # noqa: E402
    SketchBuilderOpsHandler,
    _read_spreadsheet_params,
    _apply_layout,
    _ensure_sketch_builder_importable,
    FC_TOOLS_PATH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ok_report(dof=8):
    r = MagicMock()
    r.ok = True
    r.dof = dof
    r.conflicts = []
    r.under_constrained = False
    return r


def _make_fail_report(conflicts=None, under_constrained=False):
    r = MagicMock()
    r.ok = False
    r.dof = 0
    r.conflicts = conflicts or ['constraint#5']
    r.under_constrained = under_constrained
    return r


def _make_spreadsheet(alias_map: dict):
    """Make a mock FreeCAD spreadsheet with XML Content and get()."""
    # Build minimal spreadsheet XML
    cells_xml = ''
    for alias, value in alias_map.items():
        cells_xml += f'<Cell alias="{alias}" content="{value}" />\n'
    xml = f'<Spreadsheet><cells>\n{cells_xml}</cells></Spreadsheet>'

    ss = MagicMock()
    ss.Content = xml
    ss.get = lambda alias: str(alias_map[alias])
    ss.Name = 'Spreadsheet'
    return ss


def _make_sketch_obj(name, geo_count=10, constraint_count=20):
    sk = MagicMock()
    sk.Name = name
    sk.GeometryCount = geo_count
    sk.Constraints = [MagicMock()] * constraint_count
    return sk


def _make_handler():
    return SketchBuilderOpsHandler(server=None)


# ---------------------------------------------------------------------------
# Tests: _read_spreadsheet_params
# ---------------------------------------------------------------------------

class TestReadSpreadsheetParams(unittest.TestCase):

    def setUp(self):
        reset_mocks()

    def test_reads_numeric_aliases(self):
        ss = _make_spreadsheet({'width': 100.0, 'height': 50.0, 'depth': 25.5})
        doc = make_mock_doc([ss])
        ss.Name = 'Spreadsheet'
        doc.getObject = lambda n: ss if n == 'Spreadsheet' else None

        params = _read_spreadsheet_params(doc, 'Spreadsheet')

        self.assertAlmostEqual(params['width'], 100.0)
        self.assertAlmostEqual(params['height'], 50.0)
        self.assertAlmostEqual(params['depth'], 25.5)

    def test_skips_non_numeric_cells(self):
        cells_xml = '''<Spreadsheet><cells>
            <Cell alias="title" content="MyBuilding" />
            <Cell alias="width" content="100.0" />
        </cells></Spreadsheet>'''
        ss = MagicMock()
        ss.Content = cells_xml

        def _get(alias):
            if alias == 'title':
                raise ValueError("not a number")
            return '100.0'

        ss.get = _get
        doc = make_mock_doc()
        doc.getObject = lambda n: ss if n == 'Spreadsheet' else None

        params = _read_spreadsheet_params(doc, 'Spreadsheet')
        self.assertIn('width', params)
        self.assertNotIn('title', params)

    def test_missing_spreadsheet_raises(self):
        doc = make_mock_doc([])
        with self.assertRaises(ValueError, msg="Spreadsheet 'Missing' not found"):
            _read_spreadsheet_params(doc, 'Missing')

    def test_cells_without_alias_skipped(self):
        cells_xml = '<Spreadsheet><cells><Cell content="99.0" /></cells></Spreadsheet>'
        ss = MagicMock()
        ss.Content = cells_xml
        doc = make_mock_doc()
        doc.getObject = lambda n: ss

        params = _read_spreadsheet_params(doc, 'Spreadsheet')
        self.assertEqual(params, {})


# ---------------------------------------------------------------------------
# Tests: _apply_layout
# ---------------------------------------------------------------------------

class TestApplyLayout(unittest.TestCase):

    def _make_sb(self):
        return MagicMock()

    def test_envelope(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'envelope', 'width': 'width', 'height': 'eaveHeight'}
        ]})
        sb.add_envelope.assert_called_once_with(width='width', height='eaveHeight')

    def test_hline(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'hline', 'y': 'firstFloorHt', 'name': 'first_floor'}
        ]})
        sb.add_hline.assert_called_once_with(y='firstFloorHt', name='first_floor')

    def test_arch(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'arch', 'cx': 'cx0', 'sill': 'sill0', 'spring': 'spr0',
             'radius': 'r0', 'name': 'win_0'}
        ]})
        sb.add_arch.assert_called_once_with(
            cx_expr='cx0', sill_expr='sill0', spring_expr='spr0',
            radius_expr='r0', name='win_0',
        )

    def test_arch_array_expands_i(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'arch_array', 'count': 3,
             'cx': 'left + {i} * spacing',
             'sill': 'sill', 'spring': 'spr', 'radius': 'r', 'name': 'win'}
        ]})
        self.assertEqual(sb.add_arch.call_count, 3)
        calls = sb.add_arch.call_args_list
        self.assertEqual(calls[0], call(cx_expr='left + 0 * spacing',
                                        sill_expr='sill', spring_expr='spr',
                                        radius_expr='r', name='win_0'))
        self.assertEqual(calls[1], call(cx_expr='left + 1 * spacing',
                                        sill_expr='sill', spring_expr='spr',
                                        radius_expr='r', name='win_1'))
        self.assertEqual(calls[2], call(cx_expr='left + 2 * spacing',
                                        sill_expr='sill', spring_expr='spr',
                                        radius_expr='r', name='win_2'))

    def test_door(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'door', 'left_x': 'doorX', 'spring': 'doorSpr',
             'width': 'doorW', 'name': 'door0', 'floor_ref': 'first_floor'}
        ]})
        sb.add_door.assert_called_once_with(
            left_x_expr='doorX', spring_expr='doorSpr', width_expr='doorW',
            name='door0', floor_ref='first_floor',
        )

    def test_monitor(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [
            {'type': 'monitor', 'width': 'mW', 'height': 'mH',
             'cx': 'mCx', 'base_y': 'mBase', 'name': 'mon0'}
        ]})
        sb.add_monitor.assert_called_once_with(
            width='mW', height='mH', cx='mCx', base_y='mBase', name='mon0',
        )

    def test_unknown_type_raises(self):
        sb = self._make_sb()
        with self.assertRaises(ValueError, msg="Unknown element type"):
            _apply_layout(sb, {'elements': [{'type': 'spaceship'}]})

    def test_empty_elements(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': []})
        sb.add_envelope.assert_not_called()
        sb.add_arch.assert_not_called()

    def test_envelope_defaults(self):
        sb = self._make_sb()
        _apply_layout(sb, {'elements': [{'type': 'envelope'}]})
        sb.add_envelope.assert_called_once_with(width='width', height='eaveHeight')


# ---------------------------------------------------------------------------
# Tests: SketchBuilderOpsHandler.build_sketch
# ---------------------------------------------------------------------------

class TestBuildSketch(unittest.TestCase):

    def setUp(self):
        reset_mocks()
        _mock_sketch_builder_class.reset_mock()
        self.handler = _make_handler()

    def _make_doc_with_spreadsheet(self, alias_map=None, sketch_name='XZ_Test'):
        alias_map = alias_map or {'width': 100.0, 'eaveHeight': 60.0}
        ss = _make_spreadsheet(alias_map)
        sk = _make_sketch_obj(sketch_name, geo_count=12, constraint_count=30)
        doc = make_mock_doc()

        def _get(name):
            if name == 'Spreadsheet':
                return ss
            if name == sketch_name:
                return sk
            return None

        doc.getObject = _get
        return doc, sk

    def test_success_returns_ok_and_counts(self):
        doc, sk = self._make_doc_with_spreadsheet(sketch_name='XZ_Test')
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_ok_report(dof=5)
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': [{'type': 'envelope', 'width': 'width', 'height': 'eaveHeight'}]},
            'sketch_name': 'XZ_Test',
            'placement': 'XZ',
            'spreadsheet': 'Spreadsheet',
        }))

        self.assertTrue(result['ok'])
        self.assertEqual(result['dof'], 5)
        self.assertEqual(result['geo'], 12)
        self.assertEqual(result['constraints'], 30)
        sb_instance.emit.assert_called_once_with(doc, sketch_name='XZ_Test', placement='XZ')

    def test_validation_failure_returns_conflicts(self):
        doc, _ = self._make_doc_with_spreadsheet()
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_fail_report(conflicts=['c#1', 'c#2'])
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
            'sketch_name': 'XZ_Test',
        }))

        self.assertFalse(result['ok'])
        self.assertEqual(result['conflicts'], ['c#1', 'c#2'])
        sb_instance.emit.assert_not_called()

    def test_under_constrained_failure(self):
        doc, _ = self._make_doc_with_spreadsheet()
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_fail_report(under_constrained=True, conflicts=[])
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
        }))

        self.assertFalse(result['ok'])
        self.assertTrue(result['under_constrained'])

    def test_missing_active_document(self):
        mock_FreeCAD.ActiveDocument = None

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
        }))

        self.assertFalse(result['ok'])
        self.assertIn('No active FreeCAD document', result['error'])

    def test_invalid_layout_type(self):
        mock_FreeCAD.ActiveDocument = make_mock_doc()

        result = json.loads(self.handler.build_sketch({
            'layout': 'not a dict',
        }))

        self.assertFalse(result['ok'])
        self.assertIn('layout must be a dict', result['error'])

    def test_missing_spreadsheet_returns_error(self):
        doc = make_mock_doc()
        doc.getObject = lambda n: None  # no spreadsheet
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
            'spreadsheet': 'NoSuchSheet',
        }))

        self.assertFalse(result['ok'])
        self.assertIn('NoSuchSheet', result['error'])

    def test_default_sketch_name_and_placement(self):
        alias_map = {'width': 80.0, 'eaveHeight': 40.0}
        ss = _make_spreadsheet(alias_map)
        default_sk = _make_sketch_obj('Master XZ', geo_count=5, constraint_count=15)
        doc = make_mock_doc()
        doc.getObject = lambda n: ss if n == 'Spreadsheet' else (default_sk if n == 'Master XZ' else None)
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_ok_report(dof=2)
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
        }))

        self.assertTrue(result['ok'])
        sb_instance.emit.assert_called_once_with(doc, sketch_name='Master XZ', placement='XZ')

    def test_exception_in_emit_returns_error(self):
        doc, _ = self._make_doc_with_spreadsheet()
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_ok_report()
        sb_instance.emit.side_effect = RuntimeError("FreeCAD exploded")
        _mock_sketch_builder_class.return_value = sb_instance

        result = json.loads(self.handler.build_sketch({
            'layout': {'elements': []},
            'sketch_name': 'XZ_Test',
        }))

        self.assertFalse(result['ok'])
        self.assertIn('FreeCAD exploded', result['error'])
        self.assertIn('traceback', result)

    def test_full_layout_dispatch(self):
        """Verify all element types are passed through to SketchBuilder."""
        alias_map = {
            'width': 100.0, 'eaveHeight': 60.0, 'firstFloorHt': 30.0,
            'winW': 10.0, 'winLeftMargin': 5.0, 'winBaySpacing': 20.0,
            'winLowSill': 8.0, 'winLowSpring': 25.0,
            'doorPosX': 40.0, 'doorW': 12.0, 'doorSpring': 28.0,
        }
        ss = _make_spreadsheet(alias_map)
        sk = _make_sketch_obj('XZ_Facade', geo_count=33, constraint_count=90)
        doc = make_mock_doc()
        doc.getObject = lambda n: ss if n == 'Spreadsheet' else (sk if n == 'XZ_Facade' else None)
        mock_FreeCAD.ActiveDocument = doc

        sb_instance = MagicMock()
        sb_instance.validate.return_value = _make_ok_report(dof=8)
        _mock_sketch_builder_class.return_value = sb_instance

        layout = {
            'elements': [
                {'type': 'envelope', 'width': 'width', 'height': 'eaveHeight'},
                {'type': 'hline', 'y': 'firstFloorHt', 'name': 'first_floor'},
                {'type': 'arch_array', 'count': 3,
                 'cx': 'winLeftMargin + winW/2 + {i} * winBaySpacing',
                 'sill': 'winLowSill', 'spring': 'winLowSpring', 'radius': 'winW/2',
                 'name': 'win_lo'},
                {'type': 'door', 'left_x': 'doorPosX', 'spring': 'doorSpring',
                 'width': 'doorW', 'floor_ref': 'first_floor'},
            ]
        }

        result = json.loads(self.handler.build_sketch({
            'layout': layout,
            'sketch_name': 'XZ_Facade',
            'placement': 'XZ',
        }))

        self.assertTrue(result['ok'])
        self.assertEqual(result['dof'], 8)
        self.assertEqual(result['geo'], 33)
        self.assertEqual(result['constraints'], 90)
        # envelope + 3 arches + 1 door = 5 add_* calls total
        sb_instance.add_envelope.assert_called_once()
        sb_instance.add_hline.assert_called_once()
        self.assertEqual(sb_instance.add_arch.call_count, 3)
        sb_instance.add_door.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _ensure_sketch_builder_importable
# ---------------------------------------------------------------------------

class TestEnsureImportable(unittest.TestCase):

    def test_adds_fc_tools_to_path(self):
        import sys as _sys
        if FC_TOOLS_PATH in _sys.path:
            _sys.path.remove(FC_TOOLS_PATH)
        _ensure_sketch_builder_importable()
        self.assertIn(FC_TOOLS_PATH, _sys.path)

    def test_idempotent(self):
        import sys as _sys
        _ensure_sketch_builder_importable()
        _ensure_sketch_builder_importable()
        count = _sys.path.count(FC_TOOLS_PATH)
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()

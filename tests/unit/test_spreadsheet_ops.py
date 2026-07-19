"""Unit tests for SpreadsheetOpsHandler.

Covers create_spreadsheet, set_cell, get_cell, set_alias, get_alias,
clear_cell, set_cell_range, bind_property, list_aliases, import_csv,
export_csv. KNOWN_ISSUES.md self-flagged this handler as needing
comprehensive tests; previously had only basic smoke coverage and 5%
statement coverage.

Spreadsheets back parametric workflows (parametric_helpers.py builds
on top of this), so silent regressions corrupt parameter-driven
models. Tests verify cell round-trip, alias resolution, TypeId
validation, and missing-object errors.
"""

import json
import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_spreadsheet,
    assert_error_contains,
    assert_success_contains,
)

from handlers.spreadsheet_ops import SpreadsheetOpsHandler, _col_to_num, _num_to_col


class TestColumnConversionHelpers(unittest.TestCase):
    """_col_to_num/_num_to_col are the single source of truth this handler's
    4 former hand-written copies (set_cell_range, get_cell_range, import_csv,
    export_csv) were consolidated into (H13). Pinning the boundary here
    covers every call site structurally — a regression in either function
    breaks all four callers identically, no per-site duplication needed."""

    def test_single_letter_columns(self):
        self.assertEqual(_col_to_num('A'), 1)
        self.assertEqual(_col_to_num('Z'), 26)

    def test_z_to_aa_boundary(self):
        """col 26 (Z) -> col 27 (AA) is the wraparound the review flagged
        as unverified at every one of the original 4 duplicate sites."""
        self.assertEqual(_col_to_num('AA'), 27)
        self.assertEqual(_num_to_col(26), 'Z')
        self.assertEqual(_num_to_col(27), 'AA')

    def test_round_trip_across_boundary(self):
        for n in (1, 25, 26, 27, 28, 52, 53, 702, 703):
            self.assertEqual(_col_to_num(_num_to_col(n)), n)


class TestCreateSpreadsheet(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.create_spreadsheet({'name': 'Params'})
        assert_error_contains(self, result, "no active document")

    def test_creates_spreadsheet_sheet_typeid(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_spreadsheet({'name': 'Params'})
        assert_success_contains(self, result, "Params")
        doc.addObject.assert_called_once_with('Spreadsheet::Sheet', 'Params')

    def test_default_name_is_Spreadsheet(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_spreadsheet({})
        doc.addObject.assert_called_once_with('Spreadsheet::Sheet', 'Spreadsheet')

    def test_spreadsheet_name_alias_accepted(self):
        """spreadsheet_name and name should both work."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_spreadsheet({'spreadsheet_name': 'BOM'})
        doc.addObject.assert_called_once_with('Spreadsheet::Sheet', 'BOM')


class TestSetCell(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_missing_spreadsheet(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_cell({
            'spreadsheet_name': 'Ghost', 'cell': 'A1', 'value': 10,
        })
        assert_error_contains(self, result, "spreadsheet not found", "ghost")

    def test_wrong_typeid_rejects(self):
        """Setting on a non-spreadsheet object errors."""
        not_a_sheet = make_part_object("Box1")  # TypeId = Part::Feature
        doc = make_mock_doc([not_a_sheet])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_cell({
            'spreadsheet_name': 'Box1', 'cell': 'A1', 'value': 10,
        })
        assert_error_contains(self, result, "not a spreadsheet")

    def test_set_none_value_clears_not_literal_none(self):
        """value=None must clear the cell, not write the string 'None'."""
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.set_cell({
            'spreadsheet_name': 'Params', 'cell': 'A1', 'value': None,
        })
        self.assertEqual(sheet._cells_data.get('A1'), '')

    def test_set_cell_round_trips(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_cell({
            'spreadsheet_name': 'Params', 'cell': 'A1', 'value': 25,
        })

        assert_success_contains(self, result, "Params", "A1", "25")
        sheet.set.assert_called_once_with('A1', '25')
        # And cell is in the in-memory store
        self.assertEqual(sheet._cells_data['A1'], '25')


class TestGetCell(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_get_returns_cell_value_as_json(self):
        sheet = make_spreadsheet("Params")
        sheet._cells_data['B2'] = '42'  # pre-populate
        sheet.getContents = MagicMock(return_value='42')  # stored expression
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell({
            'spreadsheet_name': 'Params', 'cell': 'B2',
        })

        # JSON shape now carries value, type, and the stored formula so a
        # read→write round-trip can preserve expressions.
        payload = json.loads(result)
        self.assertEqual(payload['cell'], 'B2')
        self.assertEqual(payload['value'], '42')
        self.assertEqual(payload['formula'], '42')
        self.assertEqual(payload['type'], 'str')

    def test_get_empty_cell_value_is_json_null(self):
        """An empty cell must serialize as JSON null, not the string 'None'."""
        sheet = make_spreadsheet("Params")
        sheet.get = MagicMock(return_value=None)
        sheet.getContents = MagicMock(return_value='')
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell({
            'spreadsheet_name': 'Params', 'cell': 'Z9',
        })
        payload = json.loads(result)
        self.assertIsNone(payload['value'])


class TestSetAlias(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_alias_name_required(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_alias({
            'spreadsheet_name': 'Params', 'cell': 'A1', 'alias': '',
        })
        assert_error_contains(self, result, "alias name is required")

    def test_set_alias_succeeds(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_alias({
            'spreadsheet_name': 'Params', 'cell': 'A1', 'alias': 'wallThickness',
        })

        assert_success_contains(self, result, "wallThickness", "Params", "A1")
        sheet.setAlias.assert_called_once_with('A1', 'wallThickness')
        self.assertEqual(sheet._aliases['A1'], 'wallThickness')


class TestGetAlias(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_get_existing_alias(self):
        sheet = make_spreadsheet("Params")
        sheet._aliases['A1'] = 'wallThickness'
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_alias({
            'spreadsheet_name': 'Params', 'cell': 'A1',
        })

        payload = json.loads(result)
        self.assertEqual(payload['cell'], 'A1')
        self.assertEqual(payload['alias'], 'wallThickness')

    def test_get_nonexistent_alias_returns_null(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_alias({
            'spreadsheet_name': 'Params', 'cell': 'Z99',
        })

        payload = json.loads(result)
        self.assertIsNone(payload['alias'])


class TestClearCell(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_clear_removes_cell(self):
        sheet = make_spreadsheet("Params")
        sheet._cells_data['A1'] = '99'
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.clear_cell({
            'spreadsheet_name': 'Params', 'cell': 'A1',
        })

        assert_success_contains(self, result, "Params", "A1")
        sheet.clear.assert_called_once_with('A1')
        self.assertNotIn('A1', sheet._cells_data)


class TestSetCellRange(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_invalid_start_cell_errors(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_cell_range({
            'spreadsheet_name': 'Params',
            'start_cell': 'not-a-cell',
            'values': [[1]],
        })
        assert_error_contains(self, result, "invalid cell")

    def test_no_values_errors(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'A1', 'values': [],
        })
        assert_error_contains(self, result, "no values")

    def test_2d_values_set_at_correct_cells(self):
        """A 2x3 grid starting at B2 fills B2..D3."""
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_cell_range({
            'spreadsheet_name': 'Params',
            'start_cell': 'B2',
            'values': [[1, 2, 3], [4, 5, 6]],
        })

        assert_success_contains(self, result, "Params")
        # 6 cells set
        self.assertEqual(sheet.set.call_count, 6)
        # Sample cells written
        self.assertEqual(sheet._cells_data['B2'], '1')
        self.assertEqual(sheet._cells_data['D2'], '3')
        self.assertEqual(sheet._cells_data['D3'], '6')


class TestGetCellRange(unittest.TestCase):
    """get_cell_range had zero test coverage before this class."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_single_cell_range(self):
        sheet = make_spreadsheet("Params")
        sheet.set('A1', '42')
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = json.loads(self.handler.get_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'A1', 'end_cell': 'A1',
        }))

        self.assertEqual(result['range'], 'A1:A1')
        self.assertEqual(result['values'], [['42']])

    def test_2d_range_reads_correct_cells(self):
        sheet = make_spreadsheet("Params")
        for cell, val in [('B2', '1'), ('C2', '2'), ('B3', '3'), ('C3', '4')]:
            sheet.set(cell, val)
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = json.loads(self.handler.get_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'B2', 'end_cell': 'C3',
        }))

        self.assertEqual(result['values'], [['1', '2'], ['3', '4']])

    def test_multi_letter_column_boundary_z_to_aa(self):
        """The column-letter<->number conversion is reimplemented 4x in
        this file (set_cell_range, get_cell_range, import_csv, export_csv)
        with no shared helper and no test using a multi-letter column
        anywhere — the Z (26) -> AA (27) wraparound was unverified in all
        four copies."""
        sheet = make_spreadsheet("Params")
        sheet.set('Z1', 'last-single-letter')
        sheet.set('AA1', 'first-double-letter')
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = json.loads(self.handler.get_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'Z1', 'end_cell': 'AA1',
        }))

        self.assertEqual(result['range'], 'Z1:AA1')
        self.assertEqual(result['values'], [['last-single-letter', 'first-double-letter']])

    def test_empty_cell_reads_as_empty_string(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = json.loads(self.handler.get_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'A1', 'end_cell': 'A1',
        }))

        self.assertEqual(result['values'], [['']])

    def test_invalid_start_cell_errors(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell_range({
            'spreadsheet_name': 'Params', 'start_cell': 'not-a-cell', 'end_cell': 'A1',
        })

        assert_error_contains(self, result, "invalid cell")

    def test_not_a_spreadsheet_errors(self):
        obj = make_part_object("NotASheet")
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell_range({
            'spreadsheet_name': 'NotASheet', 'start_cell': 'A1', 'end_cell': 'A1',
        })

        assert_error_contains(self, result, "not a spreadsheet")

    def test_spreadsheet_not_found_errors(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell_range({
            'spreadsheet_name': 'Ghost', 'start_cell': 'A1', 'end_cell': 'A1',
        })

        assert_error_contains(self, result, "not found")


class TestBindProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_missing_object(self):
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.bind_property({
            'object_name': 'Ghost', 'property_name': 'Length',
            'spreadsheet_name': 'Params', 'cell': 'A1',
        })
        assert_error_contains(self, result, "object not found", "ghost")

    def test_binds_via_setExpression(self):
        box = make_part_object("Box1")
        sheet = make_spreadsheet("Params")
        doc = make_mock_doc([box, sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.bind_property({
            'object_name': 'Box1', 'property_name': 'Length',
            'spreadsheet_name': 'Params', 'cell': 'wallThickness',
        })

        box.setExpression.assert_called_once_with(
            'Length', 'Params.wallThickness',
        )
        assert_success_contains(self, result, "Box1", "Length",
                                "Params.wallThickness")


class TestListAliases(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_returns_alias_map(self):
        """list_aliases parses cells.Content XML for alias entries."""
        sheet = make_spreadsheet("Params")
        sheet.cells.Content = (
            '<cells>'
            '<Cell address="A1" alias="wallThickness"/>'
            '<Cell address="A2" alias="height"/>'
            '<Cell address="A3"/>'  # no alias — skipped
            '</cells>'
        )
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_aliases({'spreadsheet_name': 'Params'})

        payload = json.loads(result)
        self.assertEqual(payload['spreadsheet'], 'Params')
        self.assertEqual(payload['aliases'], {
            'A1': 'wallThickness', 'A2': 'height',
        })


class TestCsvRoundTrip(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(SpreadsheetOpsHandler)

    def test_import_keeps_quoted_field_with_comma_in_one_cell(self):
        """A quoted field containing the delimiter must land in a single cell —
        naive split(',') would break it across two cells."""
        sheet = make_spreadsheet("S")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.import_csv({
            'spreadsheet_name': 'S', 'start_cell': 'A1',
            'csv_data': '"Smith, John",42\n',
        })

        self.assertEqual(sheet._cells_data.get('A1'), 'Smith, John')
        self.assertEqual(sheet._cells_data.get('B1'), '42')

    def test_export_quotes_field_with_comma(self):
        """csv.writer must quote a comma-containing field so the CSV re-imports
        into the correct columns; the old delimiter.join did not."""
        sheet = make_spreadsheet("S")
        sheet._cells_data['A1'] = 'a,b'
        sheet._cells_data['B1'] = 'plain'
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.export_csv({
            'spreadsheet_name': 'S', 'start_cell': 'A1', 'end_cell': 'B1',
        })
        payload = json.loads(result)
        self.assertIn('"a,b"', payload['csv'])

    def test_truncation_check_failure_is_surfaced_not_silenced(self):
        """M12: a bare except around the getUsedRange() truncation check
        made truncated silently stay False whenever the CHECK ITSELF
        failed — indistinguishable from 'verified: nothing was truncated'.
        Must surface a truncation_check_error instead."""
        sheet = make_spreadsheet("S")
        sheet._cells_data['A1'] = 'x'
        sheet.getUsedRange.side_effect = RuntimeError("scripting object invalidated")
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.export_csv({
            'spreadsheet_name': 'S', 'start_cell': 'A1', 'end_cell': 'A1',
        })
        payload = json.loads(result)

        # The export itself must still succeed despite the check failing.
        self.assertIn('x', payload['csv'])
        self.assertFalse(payload['truncated'])
        self.assertIn('truncation_check_error', payload)
        self.assertIn('scripting object invalidated', payload['truncation_check_error'])

    def test_no_truncation_check_error_key_when_check_succeeds(self):
        sheet = make_spreadsheet("S")
        sheet._cells_data['A1'] = 'x'
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.export_csv({
            'spreadsheet_name': 'S', 'start_cell': 'A1', 'end_cell': 'A1',
        })
        payload = json.loads(result)
        self.assertNotIn('truncation_check_error', payload)


if __name__ == '__main__':
    unittest.main()

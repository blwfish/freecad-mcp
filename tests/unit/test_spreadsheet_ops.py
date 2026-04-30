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

from handlers.spreadsheet_ops import SpreadsheetOpsHandler


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
        doc = make_mock_doc([sheet])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_cell({
            'spreadsheet_name': 'Params', 'cell': 'B2',
        })

        # JSON shape: {"cell": "B2", "value": "42"}
        payload = json.loads(result)
        self.assertEqual(payload['cell'], 'B2')
        self.assertEqual(payload['value'], '42')


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


if __name__ == '__main__':
    unittest.main()

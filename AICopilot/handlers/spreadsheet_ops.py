# Spreadsheet workbench operation handlers for FreeCAD MCP

import json
import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class SpreadsheetOpsHandler(BaseHandler):
    """Handler for Spreadsheet workbench operations."""

    def create_spreadsheet(self, args: Dict[str, Any]) -> str:
        """Create a new spreadsheet in the active document."""
        try:
            name = args.get('name', 'Spreadsheet')

            doc = self.get_document(create_if_missing=True)

            spreadsheet = doc.addObject('Spreadsheet::Sheet', name)
            self.recompute(doc)

            return f"Created spreadsheet: {spreadsheet.Name}"

        except Exception as e:
            return f"Error creating spreadsheet: {e}"

    def set_cell(self, args: Dict[str, Any]) -> str:
        """Set a cell value in a spreadsheet."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell = args.get('cell', 'A1')
            value = args.get('value', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            spreadsheet.set(cell, str(value))
            self.recompute(doc)

            return f"Set {spreadsheet_name}.{cell} = {value}"

        except Exception as e:
            return f"Error setting cell: {e}"

    def get_cell(self, args: Dict[str, Any]) -> str:
        """Get a cell value from a spreadsheet."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell = args.get('cell', 'A1')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            value = spreadsheet.get(cell)

            return json.dumps({"cell": cell, "value": str(value)})

        except Exception as e:
            return f"Error getting cell: {e}"

    def set_alias(self, args: Dict[str, Any]) -> str:
        """Set an alias for a cell (allows referencing cell by name in expressions)."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell = args.get('cell', 'A1')
            alias = args.get('alias', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            if not alias:
                return "Alias name is required"

            spreadsheet.setAlias(cell, alias)
            self.recompute(doc)

            return f"Set alias '{alias}' for {spreadsheet_name}.{cell}"

        except Exception as e:
            return f"Error setting alias: {e}"

    def get_alias(self, args: Dict[str, Any]) -> str:
        """Get the alias for a cell."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell = args.get('cell', 'A1')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            alias = spreadsheet.getAlias(cell)

            if alias:
                return json.dumps({"cell": cell, "alias": alias})
            else:
                return json.dumps({"cell": cell, "alias": None})

        except Exception as e:
            return f"Error getting alias: {e}"

    def clear_cell(self, args: Dict[str, Any]) -> str:
        """Clear a cell in a spreadsheet."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell = args.get('cell', 'A1')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            spreadsheet.clear(cell)
            self.recompute(doc)

            return f"Cleared {spreadsheet_name}.{cell}"

        except Exception as e:
            return f"Error clearing cell: {e}"

    def set_cell_range(self, args: Dict[str, Any]) -> str:
        """Set values for a range of cells."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            start_cell = args.get('start_cell', 'A1')
            values = args.get('values', [])  # 2D array of values

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            if not values:
                return "No values provided"

            # Parse start cell (e.g., "A1" -> col='A', row=1)
            import re
            match = re.match(r'([A-Z]+)(\d+)', start_cell.upper())
            if not match:
                return f"Invalid cell reference: {start_cell}"

            start_col = match.group(1)
            start_row = int(match.group(2))

            cells_set = 0
            for row_idx, row_values in enumerate(values):
                if not isinstance(row_values, list):
                    row_values = [row_values]
                for col_idx, value in enumerate(row_values):
                    # Calculate column letter
                    col_num = 0
                    for c in start_col:
                        col_num = col_num * 26 + (ord(c) - ord('A') + 1)
                    col_num += col_idx

                    # Convert back to letter(s)
                    col_letter = ''
                    while col_num > 0:
                        col_num -= 1
                        col_letter = chr(col_num % 26 + ord('A')) + col_letter
                        col_num //= 26

                    cell = f"{col_letter}{start_row + row_idx}"
                    spreadsheet.set(cell, str(value))
                    cells_set += 1

            self.recompute(doc)

            return f"Set {cells_set} cells in {spreadsheet_name} starting at {start_cell}"

        except Exception as e:
            return f"Error setting cell range: {e}"

    def get_cell_range(self, args: Dict[str, Any]) -> str:
        """Get values from a range of cells."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            start_cell = args.get('start_cell', 'A1')
            end_cell = args.get('end_cell', 'A1')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            import re

            # Parse start cell
            match_start = re.match(r'([A-Z]+)(\d+)', start_cell.upper())
            match_end = re.match(r'([A-Z]+)(\d+)', end_cell.upper())

            if not match_start or not match_end:
                return f"Invalid cell reference: {start_cell} or {end_cell}"

            def col_to_num(col):
                num = 0
                for c in col:
                    num = num * 26 + (ord(c) - ord('A') + 1)
                return num

            def num_to_col(num):
                col = ''
                while num > 0:
                    num -= 1
                    col = chr(num % 26 + ord('A')) + col
                    num //= 26
                return col

            start_col_num = col_to_num(match_start.group(1))
            end_col_num = col_to_num(match_end.group(1))
            start_row = int(match_start.group(2))
            end_row = int(match_end.group(2))

            values = []
            for row in range(start_row, end_row + 1):
                row_values = []
                for col_num in range(start_col_num, end_col_num + 1):
                    cell = f"{num_to_col(col_num)}{row}"
                    try:
                        value = spreadsheet.get(cell)
                        row_values.append(str(value) if value is not None else "")
                    except Exception:
                        row_values.append("")
                values.append(row_values)

            return json.dumps({
                "range": f"{start_cell}:{end_cell}",
                "values": values
            })

        except Exception as e:
            return f"Error getting cell range: {e}"

    def bind_property(self, args: Dict[str, Any]) -> str:
        """Bind an object property to a spreadsheet cell using expressions."""
        try:
            object_name = args.get('object_name', '')
            property_name = args.get('property_name', '')
            spreadsheet_name = args.get('spreadsheet_name', '')
            cell_or_alias = args.get('cell', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            # Set expression binding
            expression = f"{spreadsheet_name}.{cell_or_alias}"
            obj.setExpression(property_name, expression)

            self.recompute(doc)

            return f"Bound {object_name}.{property_name} to {expression}"

        except Exception as e:
            return f"Error binding property: {e}"

    def list_aliases(self, args: Dict[str, Any]) -> str:
        """List all aliases in a spreadsheet."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            # Get all cells with aliases
            aliases = {}
            if hasattr(spreadsheet, 'cells'):
                cells_content = spreadsheet.cells.Content
                # Parse the content to find aliases
                # FreeCAD stores this in XML format
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(cells_content)
                    for cell in root.findall('.//Cell'):
                        alias = cell.get('alias')
                        address = cell.get('address')
                        if alias and address:
                            aliases[address] = alias
                except Exception:
                    pass

            # Alternative method using getAlias on known cells
            # Scan common cell range if XML parsing fails
            if not aliases:
                for row in range(1, 101):  # Check first 100 rows
                    for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                        cell = f"{col}{row}"
                        try:
                            alias = spreadsheet.getAlias(cell)
                            if alias:
                                aliases[cell] = alias
                        except Exception:
                            pass

            return json.dumps({
                "spreadsheet": spreadsheet_name,
                "aliases": aliases
            })

        except Exception as e:
            return f"Error listing aliases: {e}"

    def import_csv(self, args: Dict[str, Any]) -> str:
        """Import CSV data into a spreadsheet."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            csv_data = args.get('csv_data', '')
            start_cell = args.get('start_cell', 'A1')
            delimiter = args.get('delimiter', ',')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            if not csv_data:
                return "No CSV data provided"

            import re
            match = re.match(r'([A-Z]+)(\d+)', start_cell.upper())
            if not match:
                return f"Invalid cell reference: {start_cell}"

            start_col = match.group(1)
            start_row = int(match.group(2))

            def col_to_num(col):
                num = 0
                for c in col:
                    num = num * 26 + (ord(c) - ord('A') + 1)
                return num

            def num_to_col(num):
                col = ''
                while num > 0:
                    num -= 1
                    col = chr(num % 26 + ord('A')) + col
                    num //= 26
                return col

            start_col_num = col_to_num(start_col)

            lines = csv_data.strip().split('\n')
            cells_set = 0

            for row_idx, line in enumerate(lines):
                values = line.split(delimiter)
                for col_idx, value in enumerate(values):
                    col_letter = num_to_col(start_col_num + col_idx)
                    cell = f"{col_letter}{start_row + row_idx}"
                    spreadsheet.set(cell, value.strip())
                    cells_set += 1

            self.recompute(doc)

            return f"Imported {cells_set} cells from CSV into {spreadsheet_name}"

        except Exception as e:
            return f"Error importing CSV: {e}"

    def export_csv(self, args: Dict[str, Any]) -> str:
        """Export spreadsheet data as CSV."""
        try:
            spreadsheet_name = args.get('spreadsheet_name', '')
            start_cell = args.get('start_cell', 'A1')
            end_cell = args.get('end_cell', 'J100')
            delimiter = args.get('delimiter', ',')

            doc = self.get_document()
            if not doc:
                return "No active document"

            spreadsheet = self.get_object(spreadsheet_name, doc)
            if not spreadsheet:
                return f"Spreadsheet not found: {spreadsheet_name}"

            if spreadsheet.TypeId != 'Spreadsheet::Sheet':
                return f"Object {spreadsheet_name} is not a spreadsheet"

            import re

            def col_to_num(col):
                num = 0
                for c in col:
                    num = num * 26 + (ord(c) - ord('A') + 1)
                return num

            def num_to_col(num):
                col = ''
                while num > 0:
                    num -= 1
                    col = chr(num % 26 + ord('A')) + col
                    num //= 26
                return col

            match_start = re.match(r'([A-Z]+)(\d+)', start_cell.upper())
            match_end = re.match(r'([A-Z]+)(\d+)', end_cell.upper())

            if not match_start or not match_end:
                return f"Invalid cell reference"

            start_col_num = col_to_num(match_start.group(1))
            end_col_num = col_to_num(match_end.group(1))
            start_row = int(match_start.group(2))
            end_row = int(match_end.group(2))

            csv_lines = []
            for row in range(start_row, end_row + 1):
                row_values = []
                for col_num in range(start_col_num, end_col_num + 1):
                    cell = f"{num_to_col(col_num)}{row}"
                    try:
                        value = spreadsheet.get(cell)
                        row_values.append(str(value) if value is not None else "")
                    except Exception:
                        row_values.append("")
                csv_lines.append(delimiter.join(row_values))

            csv_data = '\n'.join(csv_lines)

            return json.dumps({
                "spreadsheet": spreadsheet_name,
                "range": f"{start_cell}:{end_cell}",
                "csv": csv_data
            })

        except Exception as e:
            return f"Error exporting CSV: {e}"

"""Integration tests for spreadsheet_operations against a live FreeCAD instance.

Spreadsheets exercise FreeCAD's expression engine (aliases, property binding)
which mocks can't reproduce — so the integration tests focus on the
end-to-end roundtrips that the unit tests can't cover meaningfully.

Run with: python3 -m pytest tests/integration/test_spreadsheet_ops.py -v
"""

import json
import time

import pytest

from . import conftest as _conftest  # noqa: F401  bootstraps the session fixture
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ss(args: dict, timeout: float = 10.0) -> str:
    """Call spreadsheet_operations and return the bridge-wrapped result.

    The handler returns either a plain status string (e.g. 'Set Foo.A1 = 5')
    or a JSON-encoded payload (for get_cell / get_cell_range / list_aliases).
    Callers that expect JSON should `json.loads` the returned string.
    """
    resp = send_command("spreadsheet_operations", args, timeout=timeout)
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return resp


def _exec(code: str, timeout: float = 10.0):
    return send_command("execute_python", {"code": code}, timeout=timeout)


# ---------------------------------------------------------------------------
# Fixture: per-test document with a fresh spreadsheet
# ---------------------------------------------------------------------------
@pytest.fixture
def doc_with_sheet():
    doc_name = f"SS_{int(time.time() * 1000) % 100000}"
    sheet_name = "TestSheet"
    send_command("view_control", {"operation": "create_document",
                                  "document_name": doc_name})
    _ss({"operation": "create_spreadsheet", "spreadsheet_name": sheet_name})
    yield doc_name, sheet_name
    try:
        _exec(f"FreeCAD.closeDocument({doc_name!r})")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Basic cell I/O
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestCellIO:
    def test_create_and_set_cell(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        result = _ss({"operation": "set_cell",
                      "spreadsheet_name": sheet, "cell": "A1", "value": "42"})
        assert "A1 = 42" in result

    def test_get_cell_returns_value(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "A1", "value": "42"})
        result = _ss({"operation": "get_cell",
                      "spreadsheet_name": sheet, "cell": "A1"})
        payload = json.loads(result)
        assert payload["cell"] == "A1"
        # Stored as string by set_cell; FreeCAD coerces back to int on get
        assert payload["value"] in ("42", "42.0")

    def test_clear_cell(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "B2", "value": "hello"})
        _ss({"operation": "clear_cell",
             "spreadsheet_name": sheet, "cell": "B2"})
        # After clear, get_cell may return either a payload with empty value
        # or an error string (depending on FreeCAD version) — either is fine
        # as long as the old value is gone.
        result = _ss({"operation": "get_cell",
                      "spreadsheet_name": sheet, "cell": "B2"})
        try:
            payload = json.loads(result)
            assert payload["value"] != "hello"
        except json.JSONDecodeError:
            # Plain-string error response — acceptable; cell was cleared
            assert "hello" not in result

    def test_set_cell_unknown_spreadsheet(self, doc_with_sheet):
        result = _ss({"operation": "set_cell",
                      "spreadsheet_name": "NoSuchSheet",
                      "cell": "A1", "value": "x"})
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# Aliases — the FreeCAD-specific behavior worth integration-testing
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestAliases:
    def test_set_and_get_alias(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "A1", "value": "10"})
        _ss({"operation": "set_alias",
             "spreadsheet_name": sheet, "cell": "A1", "alias": "width"})
        result = _ss({"operation": "get_alias",
                      "spreadsheet_name": sheet, "cell": "A1"})
        payload = json.loads(result)
        assert payload["alias"] == "width"

    def test_list_aliases(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "A1", "value": "1"})
        _ss({"operation": "set_alias",
             "spreadsheet_name": sheet, "cell": "A1", "alias": "alpha"})
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "B2", "value": "2"})
        _ss({"operation": "set_alias",
             "spreadsheet_name": sheet, "cell": "B2", "alias": "beta"})
        result = _ss({"operation": "list_aliases",
                      "spreadsheet_name": sheet})
        payload = json.loads(result)
        # Aliases are keyed by cell address
        assert payload["aliases"].get("A1") == "alpha"
        assert payload["aliases"].get("B2") == "beta"


# ---------------------------------------------------------------------------
# Property binding — connects spreadsheet to live geometry, exercises the
# FreeCAD expression engine end-to-end. This is the high-value test.
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestPropertyBinding:
    def test_bind_box_length_to_alias(self, doc_with_sheet):
        doc, sheet = doc_with_sheet
        # Set a length value with an alias
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "A1", "value": "25"})
        _ss({"operation": "set_alias",
             "spreadsheet_name": sheet, "cell": "A1", "alias": "boxLength"})

        # Create a box, then bind its Length to the spreadsheet
        send_command("execute_python", {"code": f"""
import FreeCAD
doc = FreeCAD.getDocument({doc!r})
box = doc.addObject('Part::Box', 'TestBox')
box.Length = 10
box.Width = 10
box.Height = 10
doc.recompute()
result = box.Length.Value
"""})
        result = _ss({"operation": "bind_property",
                      "object_name": "TestBox",
                      "property_name": "Length",
                      "spreadsheet_name": sheet,
                      "cell": "boxLength"})
        assert "Bound" in result, result

        # Verify the box length now reflects the spreadsheet value
        verify = _exec(f"""
import FreeCAD
doc = FreeCAD.getDocument({doc!r})
doc.recompute()
result = round(doc.TestBox.Length.Value, 3)
""")
        # bind_property uses expressions, so Length should now be 25
        assert verify.get("result") in ("25", "25.0", "25.000")

        # Now change the spreadsheet value and confirm the box updates
        _ss({"operation": "set_cell",
             "spreadsheet_name": sheet, "cell": "A1", "value": "50"})
        verify2 = _exec(f"""
import FreeCAD
doc = FreeCAD.getDocument({doc!r})
doc.recompute()
result = round(doc.TestBox.Length.Value, 3)
""")
        assert verify2.get("result") in ("50", "50.0", "50.000")


# ---------------------------------------------------------------------------
# Range I/O
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestRangeIO:
    def test_set_and_get_range(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        # Set 2x3 range
        _ss({"operation": "set_cell_range",
             "spreadsheet_name": sheet,
             "start_cell": "A1",
             "values": [[1, 2, 3], [4, 5, 6]]})
        result = _ss({"operation": "get_cell_range",
                      "spreadsheet_name": sheet,
                      "start_cell": "A1",
                      "end_cell": "C2"})
        payload = json.loads(result)
        assert len(payload["values"]) == 2
        assert len(payload["values"][0]) == 3
        # Values come back as strings/floats
        flat = [str(v).rstrip("0").rstrip(".") for row in payload["values"] for v in row]
        for expected in ("1", "2", "3", "4", "5", "6"):
            assert expected in flat


# ---------------------------------------------------------------------------
# CSV roundtrip
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestCsvRoundtrip:
    def test_import_then_export_csv(self, doc_with_sheet):
        _, sheet = doc_with_sheet
        csv_in = "name,value\nalpha,10\nbeta,20\n"
        result = _ss({"operation": "import_csv",
                      "spreadsheet_name": sheet,
                      "csv_data": csv_in,
                      "start_cell": "A1"})
        assert "error" not in result.lower(), result

        out = _ss({"operation": "export_csv",
                   "spreadsheet_name": sheet,
                   "start_cell": "A1",
                   "end_cell": "B3"})
        # export_csv may return a plain string or a JSON wrapper
        try:
            payload = json.loads(out)
            csv_out = payload.get("csv", payload.get("csv_data", out))
        except (json.JSONDecodeError, TypeError):
            csv_out = out
        assert "alpha" in csv_out
        assert "beta" in csv_out
        assert "10" in csv_out and "20" in csv_out

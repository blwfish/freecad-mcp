"""
Shared fixtures and mocks for FreeCAD MCP unit tests.

These tests run WITHOUT FreeCAD installed by mocking the FreeCAD modules.
"""

import sys
import types
import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def mock_freecad(monkeypatch):
    """Mock the FreeCAD and related modules so freecad_mcp_handler.py can import."""
    # Create mock FreeCAD module
    fc = types.ModuleType("FreeCAD")
    fc.GuiUp = False  # Console mode — avoids PySide/FreeCADGui imports
    fc.Console = MagicMock()
    fc.ActiveDocument = None
    fc.newDocument = MagicMock()
    fc.getUserAppDataDir = MagicMock(return_value="/tmp/fake_freecad")
    fc.Document = type("Document", (), {})  # type stub for annotations

    # Preference store: an EMPTY store, so GetBool/GetString return the
    # default the caller passed. A test that needs a set preference replaces
    # fc.ParamGet on this fixture (see test_base_handler.TestAutosaveBefore).
    pref = MagicMock()
    pref.GetBool = MagicMock(side_effect=lambda key, default=False: default)
    pref.GetString = MagicMock(side_effect=lambda key, default="": default)
    fc.ParamGet = MagicMock(return_value=pref)

    # Create mock FreeCADGui
    fcgui = types.ModuleType("FreeCADGui")

    # Create mock Part module
    part = types.ModuleType("Part")

    # Install into sys.modules before any import
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fcgui)
    monkeypatch.setitem(sys.modules, "Part", part)
    monkeypatch.setitem(sys.modules, "PySide", MagicMock())
    monkeypatch.setitem(sys.modules, "PySide.QtCore", MagicMock())

    return fc

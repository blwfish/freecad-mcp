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
    """Mock the FreeCAD and related modules so socket_server.py can import."""
    # Create mock FreeCAD module
    fc = types.ModuleType("FreeCAD")
    fc.GuiUp = False  # Console mode — avoids PySide/FreeCADGui imports
    fc.Console = MagicMock()
    fc.ActiveDocument = None
    fc.newDocument = MagicMock()
    fc.getUserAppDataDir = MagicMock(return_value="/tmp/fake_freecad")

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

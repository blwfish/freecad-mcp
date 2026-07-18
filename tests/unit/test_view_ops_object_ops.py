"""Unit tests for ViewOpsHandler.select_object/hide_object/show_object/
delete_object.

These had zero test coverage before this file — grep for the method names
across tests/unit/ only hit document_ops's own copies and dispatch-routing
tests, never ViewOpsHandler's. All four previously bypassed
BaseHandler.get_object() via raw doc.getObject, which matches internal
Name only — a caller-supplied Label always came back "not found" even
though the Name/Label distinction is exactly what get_object() exists to
paper over for every other handler.

Follows test_view_ops_screenshot.py's pattern (module-level import,
inline unittest.mock.patch) rather than test_document_ops.py's
del-sys.modules-and-reimport pattern — the latter invalidates
test_view_ops_screenshot.py's already-bound ViewOpsHandler class when both
files run in the same session (confirmed: an earlier version of this file
broke that file's tests by deleting handlers.view_ops out from under it).
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.modules.setdefault("FreeCAD", MagicMock(GuiUp=False, Console=MagicMock()))
sys.modules.setdefault("FreeCADGui", MagicMock())
sys.modules.setdefault("Part", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))
from handlers.view_ops import ViewOpsHandler  # noqa: E402

_GUI_PATH = "handlers.view_ops.FreeCADGui"
_FREECAD_PATH = "handlers.view_ops.FreeCAD"


def make_handler():
    server = MagicMock()
    server.selector = None  # exercise the "no selector" fallback branch
    return ViewOpsHandler(server=server, log_operation=None, capture_state=None)


def make_mock_doc():
    doc = MagicMock()
    doc.Name = "TestDoc"

    box = MagicMock()
    box.Name = "Box"
    box.Label = "Box"

    cylinder = MagicMock()
    cylinder.Name = "Cylinder001"
    cylinder.Label = "MyCylinder"

    doc.Objects = [box, cylinder]
    doc.getObject = lambda name: {"Box": box, "Cylinder001": cylinder}.get(name)
    doc.getObjectsByLabel = lambda label: [o for o in doc.Objects if o.Label == label]
    return doc


class TestSelectObject:
    def test_selects_by_internal_name(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH):
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().select_object({"object_name": "Box"})
        assert "not found" not in result.lower()

    def test_selects_by_label(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH):
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().select_object({"object_name": "MyCylinder"})
        assert "not found" not in result.lower()

    def test_not_found(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH):
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().select_object({"object_name": "Ghost"})
        assert "not found" in result.lower()


class TestHideShowObject:
    def test_hide_by_label(self):
        with patch(_FREECAD_PATH) as fc:
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().hide_object({"object_name": "MyCylinder"})
        assert "hidden" in result.lower()

    def test_show_by_label(self):
        with patch(_FREECAD_PATH) as fc:
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().show_object({"object_name": "MyCylinder"})
        assert "shown" in result.lower()

    def test_hide_not_found(self):
        with patch(_FREECAD_PATH) as fc:
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().hide_object({"object_name": "Ghost"})
        assert "not found" in result.lower()


class TestDeleteObject:
    def test_delete_by_internal_name(self):
        with patch(_FREECAD_PATH) as fc:
            doc = make_mock_doc()
            fc.ActiveDocument = doc
            result = make_handler().delete_object({"object_name": "Box"})
        assert "deleted" in result.lower()
        doc.removeObject.assert_called_once_with("Box")

    def test_delete_by_label_removes_internal_name_not_label(self):
        """removeObject() needs the internal Name — passing the resolved
        Label straight through would fail against a real FreeCAD document
        even though get_object() found the object."""
        with patch(_FREECAD_PATH) as fc:
            doc = make_mock_doc()
            fc.ActiveDocument = doc
            result = make_handler().delete_object({"object_name": "MyCylinder"})
        assert "deleted" in result.lower()
        doc.removeObject.assert_called_once_with("Cylinder001")

    def test_delete_not_found(self):
        with patch(_FREECAD_PATH) as fc:
            fc.ActiveDocument = make_mock_doc()
            result = make_handler().delete_object({"object_name": "Ghost"})
        assert "not found" in result.lower()

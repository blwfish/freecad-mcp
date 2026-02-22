"""
Tests for ViewOpsHandler.take_screenshot()
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock FreeCAD modules at module level (before any handler imports)
# ---------------------------------------------------------------------------

sys.modules.setdefault("FreeCAD", MagicMock(GuiUp=False, Console=MagicMock()))
sys.modules.setdefault("FreeCADGui", MagicMock())
sys.modules.setdefault("Part", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))
from handlers.view_ops import ViewOpsHandler  # noqa: E402

# Target for patching the FreeCADGui name inside the handler module
_GUI_PATH = "handlers.view_ops.FreeCADGui"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 1×1 transparent PNG
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def make_handler():
    return ViewOpsHandler(server=None, log_operation=None, capture_state=None)


def make_mock_view(png_bytes=PNG_1x1):
    """View whose saveImage() writes png_bytes to the given path."""
    mock_view = MagicMock()

    def _save_image(path, w, h):
        with open(path, "wb") as f:
            f.write(png_bytes)

    mock_view.saveImage.side_effect = _save_image
    return mock_view


def make_mock_doc(view):
    doc = MagicMock()
    doc.activeView.return_value = view
    return doc


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestTakeScreenshotSuccess:
    def test_returns_success_true(self):
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is True

    def test_returns_valid_base64_png(self):
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert base64.b64decode(result["image_data"]) == PNG_1x1

    def test_mime_type_is_png(self):
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert result["mime_type"] == "image/png"

    def test_default_dimensions(self):
        captured = {}
        mock_view = MagicMock()

        def _save(path, w, h):
            captured["w"] = w
            captured["h"] = h
            with open(path, "wb") as f:
                f.write(PNG_1x1)

        mock_view.saveImage.side_effect = _save
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            make_handler().take_screenshot({})

        assert captured == {"w": 800, "h": 600}

    def test_custom_dimensions(self):
        captured = {}
        mock_view = MagicMock()

        def _save(path, w, h):
            captured["w"] = w
            captured["h"] = h
            with open(path, "wb") as f:
                f.write(PNG_1x1)

        mock_view.saveImage.side_effect = _save
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            make_handler().take_screenshot({"width": 1920, "height": 1080})

        assert captured == {"w": 1920, "h": 1080}

    def test_temp_file_is_cleaned_up(self):
        created = []
        mock_view = MagicMock()

        def _save(path, w, h):
            created.append(path)
            with open(path, "wb") as f:
                f.write(PNG_1x1)

        mock_view.saveImage.side_effect = _save
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            make_handler().take_screenshot({})

        assert created, "saveImage was never called"
        assert not os.path.exists(created[0]), "Temp file was not deleted"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestTakeScreenshotErrors:
    def test_no_active_document(self):
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = None
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active document" in result["error"]

    def test_no_active_view(self):
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(view=None)
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active view" in result["error"]

    def test_save_image_raises(self):
        mock_view = MagicMock()
        mock_view.saveImage.side_effect = RuntimeError("GPU error")
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "GPU error" in result["error"]

    def test_get_screenshot_is_alias_for_take_screenshot(self):
        """get_screenshot() should return the same result as take_screenshot()."""
        with patch(_GUI_PATH) as gui:
            gui.activeDocument.return_value = None
            h = make_handler()
            assert h.get_screenshot({}) == h.take_screenshot({})

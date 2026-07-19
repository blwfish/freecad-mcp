"""
Tests for ViewOpsHandler.take_screenshot()

Covers both the saveImage() code path (non-macOS) and the handler-side
Darwin/screencapture path (M7/M8). Note there is also a SEPARATE
screencapture implementation in freecad_mcp_server.py (the bridge process)
that intercepts view_control screenshot calls before they'd reach this
handler on a live macOS deployment — that path doesn't claim width/height
at all, so it doesn't share M7's bug, and is not covered by this file.
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock FreeCAD modules at module level (before any handler imports)
# ---------------------------------------------------------------------------

sys.modules.setdefault("FreeCAD", MagicMock(GuiUp=False, Console=MagicMock()))
sys.modules.setdefault("FreeCADGui", MagicMock())
sys.modules.setdefault("Part", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))
from handlers.view_ops import ViewOpsHandler  # noqa: E402

# Patch targets inside the handler module
_GUI_PATH = "handlers.view_ops.FreeCADGui"
_PLATFORM_PATH = "handlers.view_ops.platform"
_FREECAD_PATH = "handlers.view_ops.FreeCAD"

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
# Success path (Linux/saveImage — GuiUp=True)
# ---------------------------------------------------------------------------

class TestTakeScreenshotSuccess:
    def test_returns_success_true(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is True

    def test_returns_valid_base64_png(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert base64.b64decode(result["image_data"]) == PNG_1x1

    def test_mime_type_is_png(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
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
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
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
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
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
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            make_handler().take_screenshot({})

        assert created, "saveImage was never called"
        assert not os.path.exists(created[0]), "Temp file was not deleted"


# ---------------------------------------------------------------------------
# Error paths (Linux/saveImage — GuiUp=True)
# ---------------------------------------------------------------------------

class TestTakeScreenshotErrors:
    def test_no_active_document(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = None
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active document" in result["error"]

    def test_no_active_view(self):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(view=None)
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active view" in result["error"]

    def test_save_image_raises(self):
        mock_view = MagicMock()
        mock_view.saveImage.side_effect = RuntimeError("GPU error")
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "GPU error" in result["error"]

    def test_get_screenshot_is_alias_for_take_screenshot(self):
        """get_screenshot() should return the same result as take_screenshot()."""
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = None
            h = make_handler()
            assert h.get_screenshot({}) == h.take_screenshot({})


# ---------------------------------------------------------------------------
# Headless guard (GuiUp=False, non-macOS)
# ---------------------------------------------------------------------------

class TestHeadlessScreenshot:
    def test_returns_error_in_headless_mode(self):
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = False
            plat.system.return_value = "Linux"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "headless" in result["error"].lower()


# ---------------------------------------------------------------------------
# Darwin / screencapture path (M7, M8)
# ---------------------------------------------------------------------------

class TestDarwinScreenshot:
    """The non-Darwin branch above always had a GuiUp guard; the Darwin
    branch was missing it (M8), and separately claimed the caller's
    requested width/height as if they were the actual capture dimensions,
    even though `screencapture -x` (no -R region flag) always captures the
    whole screen at native resolution (M7)."""

    def _mock_screencapture_writes(self, png_bytes):
        def _run(cmd, timeout=None, capture_output=None):
            # cmd is ["screencapture", "-x", tmp_path]
            with open(cmd[2], "wb") as f:
                f.write(png_bytes)
            proc = MagicMock()
            proc.returncode = 0
            proc.stderr = b""
            return proc
        return _run

    def test_headless_darwin_screenshot_refused(self):
        """M8: a headless macOS instance (GuiUp=False) must refuse before
        ever invoking screencapture — previously it would photograph
        whatever's on the physical screen, unrelated to FreeCAD, and
        report success."""
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat, \
             patch("subprocess.run") as mock_run:
            fc.GuiUp = False
            fc.ActiveDocument = MagicMock()  # a document can exist without a GUI
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "headless" in result["error"].lower()
        mock_run.assert_not_called()

    def test_darwin_no_active_document_refused(self):
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat, \
             patch("subprocess.run") as mock_run:
            fc.GuiUp = True
            fc.ActiveDocument = None
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active document" in result["error"]
        mock_run.assert_not_called()

    def test_reports_actual_captured_dimensions_not_requested(self):
        """M7: the caller requests 1920x1080, but the mocked
        `screencapture` writes the 1x1 PNG_1x1 fixture (standing in for
        the real screen's actual native resolution) — the response must
        report the file's real dimensions, not echo the request."""
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat, \
             patch("subprocess.run", side_effect=self._mock_screencapture_writes(PNG_1x1)):
            fc.GuiUp = True
            fc.ActiveDocument = MagicMock()
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({"width": 1920, "height": 1080}))
        assert result["success"] is True
        assert result["width"] == 1
        assert result["height"] == 1

    def test_screencapture_failure_returns_error_not_fallback(self):
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat, \
             patch("subprocess.run") as mock_run:
            fc.GuiUp = True
            fc.ActiveDocument = MagicMock()
            plat.system.return_value = "Darwin"
            proc = MagicMock()
            proc.returncode = 1
            proc.stderr = b"not authorized"
            mock_run.return_value = proc
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "screencapture failed" in result["error"]


class TestReadPngDimensions:
    """_read_png_dimensions is the module-level helper M7's fix depends on
    for real (not requested) capture dimensions."""

    def test_reads_1x1_fixture_correctly(self, tmp_path):
        from handlers.view_ops import _read_png_dimensions
        p = tmp_path / "test.png"
        p.write_bytes(PNG_1x1)
        assert _read_png_dimensions(str(p)) == (1, 1)

    def test_non_png_file_returns_none(self, tmp_path):
        from handlers.view_ops import _read_png_dimensions
        p = tmp_path / "not_a_png.png"
        p.write_bytes(b"not a real png file at all, just text")
        assert _read_png_dimensions(str(p)) is None

    def test_missing_file_returns_none(self):
        from handlers.view_ops import _read_png_dimensions
        assert _read_png_dimensions("/nonexistent/path/to/nothing.png") is None

    def test_truncated_header_returns_none(self, tmp_path):
        from handlers.view_ops import _read_png_dimensions
        p = tmp_path / "truncated.png"
        p.write_bytes(PNG_1x1[:10])  # PNG signature but no IHDR chunk
        assert _read_png_dimensions(str(p)) is None

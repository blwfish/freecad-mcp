"""
Tests for ViewOpsHandler.take_screenshot()

Covers the saveImage() code path (non-macOS) and this handler's Darwin
guards/tripwire. The actual macOS capture lives entirely in
freecad_mcp_server.py (the bridge process) -- its view_control screenshot
shortcut is instance-aware (steps aside for headless targets) and crops to
FreeCAD's window; that logic is not covered by this file. This handler's
own Darwin branch only handles the headless/no-document guards (reached
when the bridge steps aside) and a loud failure if a GUI instance ever
reaches this code path directly, meaning the bridge-side shortcut was
bypassed.
"""

import base64
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock FreeCAD modules at module level (before any handler imports)
# ---------------------------------------------------------------------------

sys.modules.setdefault("FreeCAD", MagicMock(GuiUp=False, Console=MagicMock()))
sys.modules.setdefault("FreeCADGui", MagicMock())
sys.modules.setdefault("Part", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))
import handlers.view_ops as view_ops  # noqa: E402
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


@pytest.fixture(autouse=True)
def _temp_files_in_tmp_path(tmp_path, monkeypatch):
    """On success take_screenshot hands its temp PNG to the bridge (which
    deletes it after reading), so tests that don't read it would leak it
    into the system temp dir -- keep the screenshot temp dir under pytest's
    tmp_path."""
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


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

    def test_returns_path_to_png_not_inline_bytes(self):
        """The PNG goes to a file the bridge reads -- inline base64 of any
        real model is far over the 50 KB socket frame limit."""
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert "image_data" not in result
        assert result["keep_file"] is False
        assert result["bytes"] == len(PNG_1x1)
        with open(result["image_path"], "rb") as f:
            assert f.read() == PNG_1x1

    def test_temp_png_is_written_inside_the_screenshot_tmp_dir(self):
        """The bridge deletes only inside this directory, so the temp file
        must land there."""
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        path = result["image_path"]
        assert os.path.samefile(os.path.dirname(path), view_ops.screenshot_tmp_dir())
        assert os.path.basename(path).startswith("shot_")

    def test_filename_writes_and_keeps_the_file(self, tmp_path):
        target = tmp_path / "shot.png"
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({"filename": str(target)}))
        assert result["success"] is True
        assert result["keep_file"] is True
        assert os.path.samefile(result["image_path"], target)
        assert target.read_bytes() == PNG_1x1

    def test_filename_creates_no_temp_file(self, tmp_path):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            make_handler().take_screenshot({"filename": str(tmp_path / "shot.png")})
        tmp_dir = view_ops.screenshot_tmp_dir()
        assert not os.path.isdir(tmp_dir) or not os.listdir(tmp_dir)

    def test_filename_outside_allowed_dirs_is_refused(self):
        """Validated like save_document: no writing a .png anywhere writable."""
        outside = os.path.abspath(os.path.join(os.sep, "fcmcp_not_allowed", "shot.png"))
        view = make_mock_view()
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(view)
            result = json.loads(make_handler().take_screenshot({"filename": outside}))
        assert result["success"] is False
        assert "outside allowed directories" in result["error"]
        view.saveImage.assert_not_called()

    def test_filename_in_missing_directory_is_refused(self, tmp_path):
        target = tmp_path / "nope" / "shot.png"
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({"filename": str(target)}))
        assert result["success"] is False
        assert "Directory does not exist" in result["error"]

    def test_filename_without_png_extension_is_refused(self, tmp_path):
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({"filename": str(tmp_path / "shot.jpg")}))
        assert result["success"] is False
        assert ".png" in result["error"]

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

    def test_temp_file_is_handed_to_the_bridge_on_success(self):
        """On success the bridge owns the temp file (it deletes it after
        reading), so the handler must NOT delete it."""
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert os.path.exists(result["image_path"])

    def test_temp_file_is_cleaned_up_on_failure(self):
        created = []
        mock_view = MagicMock()

        def _save(path, w, h):
            created.append(path)
            with open(path, "wb") as f:
                f.write(PNG_1x1)
            raise RuntimeError("GPU error after partial write")

        mock_view.saveImage.side_effect = _save
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(mock_view)
            result = json.loads(make_handler().take_screenshot({}))

        assert result["success"] is False
        assert created, "saveImage was never called"
        assert not os.path.exists(created[0]), "Temp file was not deleted"


# ---------------------------------------------------------------------------
# Orphaned temp screenshots (bridge gave up polling and never read them)
# ---------------------------------------------------------------------------

class TestStaleScreenshotSweep:
    def _touch(self, directory, name, age_s):
        path = os.path.join(directory, name)
        with open(path, "wb") as f:
            f.write(PNG_1x1)
        then = time.time() - age_s
        os.utime(path, (then, then))
        return path

    def test_sweep_removes_only_stale_screenshots(self, tmp_path):
        d = str(tmp_path)
        stale = self._touch(d, "shot_old.png", age_s=3600)
        fresh = self._touch(d, "shot_new.png", age_s=5)
        other = self._touch(d, "notes_old.png", age_s=3600)
        view_ops._sweep_stale_screenshots(d)
        assert not os.path.exists(stale)
        assert os.path.exists(fresh)
        assert os.path.exists(other)

    def test_sweep_of_missing_directory_is_harmless(self, tmp_path):
        view_ops._sweep_stale_screenshots(str(tmp_path / "does_not_exist"))

    def test_take_screenshot_sweeps_orphans(self):
        """A temp PNG orphaned by a bridge poll timeout is collected on the
        next screenshot, so the directory can't grow without bound."""
        tmp_dir = view_ops.screenshot_tmp_dir()
        os.makedirs(tmp_dir, exist_ok=True)
        orphan = self._touch(tmp_dir, "shot_orphan.png", age_s=3600)
        with patch(_FREECAD_PATH) as fc, patch(_GUI_PATH) as gui, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            plat.system.return_value = "Linux"
            gui.activeDocument.return_value = make_mock_doc(make_mock_view())
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is True
        assert not os.path.exists(orphan)
        assert os.path.exists(result["image_path"])


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
# Darwin path (M7, M8, and the bridge-side consolidation that followed)
# ---------------------------------------------------------------------------

class TestDarwinScreenshot:
    """Actual macOS capture now happens in the bridge process
    (freecad_mcp_server.py's view_control screenshot shortcut), not here —
    it crops to FreeCAD's window instead of the whole screen and never
    touches this thread. This handler still owns the headless/no-document
    guards (needed when the bridge steps aside for a headless target), and
    fails loudly rather than silently falling back to saveImage() -- which
    deadlocks the GUI thread on macOS -- if a GUI instance ever reaches it
    directly, meaning the bridge-side shortcut was bypassed."""

    def test_headless_darwin_screenshot_refused(self):
        """M8: a headless macOS instance (GuiUp=False) must refuse before
        reaching the Darwin tripwire below — previously it would photograph
        whatever's on the physical screen, unrelated to FreeCAD, and
        report success."""
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = False
            fc.ActiveDocument = MagicMock()  # a document can exist without a GUI
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "headless" in result["error"].lower()

    def test_darwin_no_active_document_refused(self):
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat:
            fc.GuiUp = True
            fc.ActiveDocument = None
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "No active document" in result["error"]

    def test_gui_instance_hitting_handler_directly_fails_loudly(self):
        """A GUI macOS instance reaching this handler at all means the
        bridge-side shortcut was bypassed. It must fail with an explicit
        error, never silently fall through to saveImage() (GUI-thread
        deadlock) or attempt its own screencapture."""
        with patch(_FREECAD_PATH) as fc, patch(_PLATFORM_PATH) as plat, \
             patch("subprocess.run") as mock_run:
            fc.GuiUp = True
            fc.ActiveDocument = MagicMock()
            plat.system.return_value = "Darwin"
            result = json.loads(make_handler().take_screenshot({}))
        assert result["success"] is False
        assert "bridge process" in result["error"].lower()
        mock_run.assert_not_called()

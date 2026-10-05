"""Unit tests for freecad_mcp_server._build_image_reply.

Observed on 8.2.2 (Windows): view_control(operation="screenshot") came back
as base64 inside a TextContent -- the async-job branch of handle_call_tool
never reached the image_data -> ImageContent conversion -- and any real
model's PNG was over the 50 KB socket frame limit anyway. The FreeCAD side
now writes the PNG to a file and replies with its path; this helper reads it
in the bridge (always on the same machine) and builds the MCP image reply.
It deletes the file afterwards only when it is inside the screenshot temp
directory, never a caller's `filename` or any other path a reply names.
"""

import base64
import json
import os
import re
import sys
import tempfile

import mcp.types as types
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import freecad_mcp_server as bridge  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _tempdir_in_tmp_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _screenshot_dir(tmp_path):
    d = tmp_path / bridge.SCREENSHOT_TMP_DIRNAME
    d.mkdir(exist_ok=True)
    return d


def _png(directory, name="shot_x.png"):
    p = directory / name
    p.write_bytes(PNG)
    return p


def test_temp_file_is_read_into_an_image_block_and_deleted(tmp_path):
    p = _png(_screenshot_dir(tmp_path))
    reply = bridge._build_image_reply(
        json.dumps({"success": True, "image_path": str(p), "keep_file": False,
                    "width": 1, "height": 1, "mime_type": "image/png"}),
        types)
    assert isinstance(reply[-1], types.ImageContent)
    assert base64.b64decode(reply[-1].data) == PNG
    assert reply[-1].model_dump(by_alias=True)["mimeType"] == "image/png"
    meta = json.loads(reply[0].text)
    assert meta == {"width": 1, "height": 1}
    assert not p.exists()


def test_file_outside_the_screenshot_dir_is_never_deleted(tmp_path):
    """The reply names the path; a path outside the screenshot temp dir is
    read but left alone, even with keep_file false."""
    p = _png(tmp_path, "elsewhere.png")
    reply = bridge._build_image_reply(
        {"success": True, "image_path": str(p), "keep_file": False}, types)
    assert isinstance(reply[-1], types.ImageContent)
    assert p.exists()


def test_the_screenshot_dir_itself_is_not_a_deletable_file(tmp_path):
    d = _screenshot_dir(tmp_path)
    assert bridge._is_in_screenshot_tmp_dir(str(d)) is False
    assert bridge._is_in_screenshot_tmp_dir(str(d / "shot_a.png")) is True
    assert bridge._is_in_screenshot_tmp_dir(str(d / ".." / "shot_a.png")) is False


def test_kept_file_stays_and_its_path_is_reported(tmp_path):
    p = _png(_screenshot_dir(tmp_path))
    reply = bridge._build_image_reply(
        {"success": True, "image_path": str(p), "keep_file": True, "note": "clamped"}, types)
    assert p.exists()
    meta = json.loads(reply[0].text)
    assert meta["saved_to"] == str(p)
    assert meta["note"] == "clamped"
    assert isinstance(reply[1], types.ImageContent)


def test_missing_file_is_an_error_naming_the_path(tmp_path):
    missing = _screenshot_dir(tmp_path) / "gone.png"
    reply = bridge._build_image_reply({"success": True, "image_path": str(missing)}, types)
    assert len(reply) == 1
    err = json.loads(reply[0].text)["error"]
    assert str(missing) in err


def test_legacy_inline_image_data_still_works():
    data = base64.b64encode(PNG).decode()
    reply = bridge._build_image_reply({"success": True, "image_data": data}, types)
    assert len(reply) == 1
    assert reply[0].data == data


def test_failed_screenshot_is_an_error_not_a_success():
    reply = bridge._build_image_reply(
        json.dumps({"success": False, "error": "No active view"}), types)
    assert json.loads(reply[0].text) == {"error": "No active view"}


def test_non_screenshot_results_are_left_alone():
    assert bridge._build_image_reply("Code executed successfully", types) is None
    assert bridge._build_image_reply(json.dumps({"success": True, "x": 1}), types) is None
    assert bridge._build_image_reply(None, types) is None


def test_screenshot_dirname_matches_the_addon():
    """The bridge deletes only inside the directory the add-on writes temp
    screenshots to, so the two names must stay equal (read from source, so
    this needs no FreeCAD mocks)."""
    src_path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "AICopilot", "handlers", "view_ops.py")
    with open(src_path, encoding="utf-8") as f:
        m = re.search(r'^SCREENSHOT_TMP_DIRNAME = "([^"]+)"', f.read(), re.M)
    assert m, "SCREENSHOT_TMP_DIRNAME not found in view_ops.py"
    assert m.group(1) == bridge.SCREENSHOT_TMP_DIRNAME

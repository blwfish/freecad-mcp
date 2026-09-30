"""Unit tests for freecad_mcp_server._build_image_reply.

Observed on 8.2.2 (Windows): view_control(operation="screenshot") came back
as base64 inside a TextContent -- the async-job branch of handle_call_tool
never reached the image_data -> ImageContent conversion -- and any real
model's PNG was over the 50 KB socket frame limit anyway. The FreeCAD side
now writes the PNG to a file and replies with its path; this helper reads it
in the bridge (always on the same machine) and builds the MCP image reply.
"""

import base64
import json
import os
import sys

import mcp.types as types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import freecad_mcp_server as bridge  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _png(tmp_path, name="shot.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return p


def test_temp_file_is_read_into_an_image_block_and_deleted(tmp_path):
    p = _png(tmp_path)
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


def test_kept_file_stays_and_its_path_is_reported(tmp_path):
    p = _png(tmp_path)
    reply = bridge._build_image_reply(
        {"success": True, "image_path": str(p), "keep_file": True, "note": "clamped"}, types)
    assert p.exists()
    meta = json.loads(reply[0].text)
    assert meta["saved_to"] == str(p)
    assert meta["note"] == "clamped"
    assert isinstance(reply[1], types.ImageContent)


def test_missing_file_is_an_error_naming_the_path(tmp_path):
    missing = tmp_path / "gone.png"
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

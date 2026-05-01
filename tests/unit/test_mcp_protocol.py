"""Tests for the MCP JSON-RPC protocol layer in freecad_mcp_server.py.

No FreeCAD connection needed — these exercise the protocol surface any MCP
client (Claude, Cursor, etc.) would see.
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

import freecad_mcp_server


# ---------------------------------------------------------------------------
# Shared server setup
# ---------------------------------------------------------------------------

def _build_server() -> Server:
    """Run main() without starting stdio to get a Server with all handlers registered."""
    captured: dict = {}

    async def noop_run(self, *args, **kwargs):
        captured["server"] = self

    @asynccontextmanager
    async def fake_stdio():
        yield AsyncMock(), AsyncMock()

    async def _setup():
        with patch.object(Server, "run", noop_run):
            with patch.object(mcp.server.stdio, "stdio_server", fake_stdio):
                await freecad_mcp_server.main()

    asyncio.run(_setup())
    return captured["server"]


_SERVER: Server = _build_server()


def _list_tools() -> list[types.Tool]:
    async def _call():
        req = types.ListToolsRequest(method="tools/list", params=None)
        result = await _SERVER.request_handlers[types.ListToolsRequest](req)
        return result.root.tools

    return asyncio.run(_call())


def _call_tool(name: str, arguments: dict | None = None) -> list[types.TextContent]:
    async def _call():
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        result = await _SERVER.request_handlers[types.CallToolRequest](req)
        return result.root.content

    return asyncio.run(_call())


# ---------------------------------------------------------------------------
# tools/list tests
# ---------------------------------------------------------------------------


class TestToolsList:
    def test_returns_nonempty_list(self):
        assert len(_list_tools()) > 0

    def test_every_tool_has_name(self):
        for tool in _list_tools():
            assert isinstance(tool.name, str) and tool.name

    def test_every_tool_has_description(self):
        for tool in _list_tools():
            assert isinstance(tool.description, str) and tool.description

    def test_every_tool_has_object_schema(self):
        for tool in _list_tools():
            schema = tool.inputSchema
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_tool_names_are_unique(self):
        names = [t.name for t in _list_tools()]
        assert len(names) == len(set(names))

    def test_schema_properties_is_dict_when_present(self):
        for tool in _list_tools():
            props = tool.inputSchema.get("properties")
            if props is not None:
                assert isinstance(props, dict)


# ---------------------------------------------------------------------------
# tools/call — known no-op tool
# ---------------------------------------------------------------------------


class TestCallToolKnown:
    def test_returns_content_list(self):
        content = _call_tool("check_freecad_connection")
        assert isinstance(content, list) and len(content) > 0

    def test_content_items_have_text_type(self):
        for item in _call_tool("check_freecad_connection"):
            assert item.type == "text"
            assert isinstance(item.text, str)

    def test_response_text_is_valid_json(self):
        content = _call_tool("check_freecad_connection")
        parsed = json.loads(content[0].text)
        assert isinstance(parsed, dict)

    def test_response_includes_socket_fields(self):
        parsed = json.loads(_call_tool("check_freecad_connection")[0].text)
        assert "freecad_socket_exists" in parsed
        assert "socket_path" in parsed
        assert "status" in parsed


# ---------------------------------------------------------------------------
# tools/call — unknown tool name
# ---------------------------------------------------------------------------


class TestCallToolUnknown:
    def test_does_not_raise(self):
        content = _call_tool("__nonexistent_tool__")
        assert content is not None

    def test_returns_text_content(self):
        content = _call_tool("__nonexistent_tool__")
        assert isinstance(content, list) and len(content) > 0
        assert content[0].type == "text"

    def test_response_mentions_tool_name(self):
        name = "__nonexistent_tool__"
        content = _call_tool(name)
        assert name in content[0].text

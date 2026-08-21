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

import pytest

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
        entry = _SERVER.get_request_handler("tools/list")
        result = await entry.handler(None, None)
        return result.tools

    return asyncio.run(_call())


def _call_tool(name: str, arguments: dict | None = None) -> list[types.TextContent]:
    async def _call():
        entry = _SERVER.get_request_handler("tools/call")
        params = types.CallToolRequestParams(name=name, arguments=arguments)
        result = await entry.handler(None, params)
        return result.content

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
            schema = tool.input_schema
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_tool_names_are_unique(self):
        names = [t.name for t in _list_tools()]
        assert len(names) == len(set(names))

    def test_schema_properties_is_dict_when_present(self):
        for tool in _list_tools():
            props = tool.input_schema.get("properties")
            if props is not None:
                assert isinstance(props, dict)


# ---------------------------------------------------------------------------
# tools/call — known no-op tool
# ---------------------------------------------------------------------------


class TestCallToolKnown:
    @pytest.fixture(autouse=True)
    def _no_network_version_check(self):
        """check_freecad_connection calls check_for_update(), which can hit
        the network. Stub it to a fast no-op for every test in this class
        so the suite never depends on network access or mutates the real
        ~/.cache/freecad-mcp/ state on the machine running tests. Tests
        that specifically exercise update_available override this
        per-test via their own `with patch(...)`.
        """
        async def _noop():
            return None
        with patch.object(freecad_mcp_server, "check_for_update", _noop):
            yield

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

    def test_update_available_absent_when_no_update(self):
        # The class-level autouse fixture already stubs check_for_update to
        # return None -- this pins that "no update" means the key is
        # missing entirely, not present-and-null.
        parsed = json.loads(_call_tool("check_freecad_connection")[0].text)
        assert "update_available" not in parsed

    def test_update_available_present_with_exact_shape_when_update_found(self):
        async def _fake_update():
            return {"current": "7.0.0", "latest": "7.1.0", "url": "https://example.invalid/releases/tag/v7.1.0"}
        with patch.object(freecad_mcp_server, "check_for_update", _fake_update):
            parsed = json.loads(_call_tool("check_freecad_connection")[0].text)
        assert parsed.get("update_available") == {
            "current": "7.0.0",
            "latest": "7.1.0",
            "url": "https://example.invalid/releases/tag/v7.1.0",
        }


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


# ---------------------------------------------------------------------------
# execute_python — non-JSON response from FreeCAD must not raise uncaught
# ---------------------------------------------------------------------------


class TestExecutePythonNonJsonResponse:
    """execute_python is the most-used tool (the documented escape hatch).
    json.loads(await send_to_freecad(...)) was previously unguarded —
    send_to_freecad's success path returns whatever the FreeCAD-side
    handler sent verbatim, not guaranteed JSON (truncated response,
    handler bug, encoding issue). An uncaught exception here would
    propagate out of handle_call_tool entirely, bypassing the
    crash-diagnosis system this codebase otherwise invests in for every
    other failure path."""

    def test_non_json_response_returns_clean_error_not_raise(self):
        from unittest.mock import MagicMock as _MagicMock

        # freecad_mcp_server.socket IS the stdlib socket module (import
        # socket, not a private copy), so patch.object(..., "socket")
        # below replaces socket.socket GLOBALLY for the duration of the
        # `with` block — not just this module's reference to it.
        # asyncio.run() (used by the shared _call_tool() helper) creates a
        # brand-new event loop on every call, and that bootstrap itself
        # opens a real self-pipe socket internally (selector_events.py's
        # _make_self_pipe). Doing that loop creation *inside* the patched
        # block hands asyncio's own internals a MagicMock instead of a
        # real socket, and the selector then fails registering the fake
        # fd with the OS (PermissionError on Linux's epoll; happened to
        # be silently tolerated on some macOS/Python combinations, which
        # is why this wasn't caught locally). Pre-creating the loop
        # outside the patch and reusing it via run_until_complete keeps
        # the mock scoped to what send_to_freecad's own code actually
        # calls, without touching asyncio's bootstrap.
        loop = asyncio.new_event_loop()
        try:
            async def _call():
                entry = _SERVER.get_request_handler("tools/call")
                params = types.CallToolRequestParams(
                    name="execute_python", arguments={"code": "1 + 1"})
                result = await entry.handler(None, params)
                return result.content

            with patch.object(freecad_mcp_server._ctx, "resolve_target",
                               return_value=("/tmp/fake.sock", None)), \
                 patch.object(freecad_mcp_server.socket, "socket") as mock_socket_cls, \
                 patch.object(freecad_mcp_server, "send_message", return_value=True), \
                 patch.object(freecad_mcp_server, "receive_message",
                               return_value="not valid json {{{"):
                # socket.socket()/.connect()/.close() are all synchronous real
                # methods even though send_to_freecad itself is an async def —
                # a plain MagicMock, not AsyncMock, is what a sync socket call
                # returns.
                mock_socket_cls.return_value = _MagicMock()

                content = loop.run_until_complete(_call())
        finally:
            loop.close()

        assert content is not None and len(content) > 0
        parsed = json.loads(content[0].text)
        assert "error" in parsed
        assert "non-json" in parsed["error"].lower() or "json" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# cam_tool_controllers — omitted tool_number must reach the bridge omitted,
# not filled in from a JSON-schema default. Regression test for the schema
# `"default": 1` bug: a schema-respecting client (or jsonschema itself) could
# fill in tool_number=1 before the handler's `'tool_number' in args` check
# ever runs, making the auto-assign path dead. See freecad_mcp_server.py's
# cam_tool_controllers tool schema and AICopilot/handlers/cam_tool_controllers.py.
# ---------------------------------------------------------------------------


class TestCamToolControllersSchemaHasNoDefaults:
    """The MCP tool schemas for cam_tool_controllers/cam_tools/cam_operations
    intentionally omit "default" on fields whose handlers do a presence
    check (`if 'field' in args`) rather than `args.get(field, fallback)` —
    a declared schema default gets sent explicitly by schema-respecting
    clients, making "omitted" indistinguishable from "explicitly chose the
    default value". Pins that this doesn't regress."""

    _NO_DEFAULT_FIELDS = {
        "cam_tool_controllers": ["tool_number", "spindle_speed", "feed_rate"],
        "cam_tools": ["diameter"],
        "cam_operations": ["stock_type", "post_processor"],
    }

    def test_presence_checked_fields_have_no_schema_default(self):
        tools_by_name = {t.name: t for t in _list_tools()}
        for tool_name, fields in self._NO_DEFAULT_FIELDS.items():
            props = tools_by_name[tool_name].input_schema["properties"]
            for field in fields:
                assert "default" not in props[field], (
                    f"{tool_name}.{field} declares a schema default; a "
                    "schema-respecting client can send it explicitly, "
                    "making the handler's presence check always true"
                )


class TestCamToolControllersToolNumberOmission:
    def test_omitted_tool_number_reaches_bridge_omitted(self):
        from unittest.mock import MagicMock as _MagicMock

        captured_commands = []

        def _fake_send_message(sock, command):
            captured_commands.append(command)
            return True

        # See TestExecutePythonNonJsonResponse above for why the event loop
        # is created outside the patched block.
        loop = asyncio.new_event_loop()
        try:
            async def _call():
                entry = _SERVER.get_request_handler("tools/call")
                params = types.CallToolRequestParams(
                    name="cam_tool_controllers",
                    arguments={
                        "operation": "add_tool_controller",
                        "job_name": "Job",
                        "tool_name": "EM6",
                    },
                )
                result = await entry.handler(None, params)
                return result.content

            with patch.object(freecad_mcp_server._ctx, "resolve_target",
                               return_value=("/tmp/fake.sock", None)), \
                 patch.object(freecad_mcp_server.socket, "socket") as mock_socket_cls, \
                 patch.object(freecad_mcp_server, "send_message", _fake_send_message), \
                 patch.object(freecad_mcp_server, "receive_message",
                               return_value=json.dumps({"result": "ok"})):
                mock_socket_cls.return_value = _MagicMock()

                loop.run_until_complete(_call())
        finally:
            loop.close()

        assert len(captured_commands) == 1
        sent_args = json.loads(captured_commands[0])["args"]
        assert "tool_number" not in sent_args, (
            "tool_number was omitted by the caller but reached the bridge "
            "anyway -- a JSON-schema default is leaking through the MCP "
            "dispatch layer"
        )

"""
Tests for AICopilot/freecad_mcp_handler.py — the FreeCAD-side MCP server core.

FreeCAD is mocked via conftest.py fixtures.
"""

import collections
import json
import queue
import struct
import sys
import os
import types
import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# Add AICopilot to path for imports
AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_handlers(monkeypatch):
    """Mock out the handler imports so freecad_mcp_handler.py can load."""
    handler_classes = [
        "PrimitivesHandler", "BooleanOpsHandler", "TransformsHandler",
        "SketchOpsHandler", "PartDesignOpsHandler", "PartOpsHandler",
        "CAMOpsHandler", "CAMToolsHandler", "CAMToolControllersHandler",
        "DraftOpsHandler", "ViewOpsHandler", "DocumentOpsHandler",
        "MeasurementOpsHandler", "SpreadsheetOpsHandler", "MeshOpsHandler",
        "SpatialOpsHandler", "InspectorOpsHandler",
        "MacroOpsHandler", "IntrospectionOpsHandler", "SketchBuilderOpsHandler",
        "VerificationOpsHandler", "FixtureOpsHandler", "DiagnosticsOpsHandler",
        "ExecutePythonOpsHandler", "AssemblyOpsHandler",
    ]

    handlers_mod = types.ModuleType("handlers")
    for cls_name in handler_classes:
        mock_cls = MagicMock()
        mock_cls.return_value = MagicMock()
        setattr(handlers_mod, cls_name, mock_cls)

    monkeypatch.setitem(sys.modules, "handlers", handlers_mod)

    # Make optional modules raise ImportError so freecad_mcp_handler takes fallback paths.
    # This is cleaner than trying to mock all their internals correctly.
    class _ImportBlocker:
        """Module that raises ImportError when you try to import from it."""
        def __getattr__(self, name):
            raise ImportError(f"Mocked: {name} not available in tests")

    for mod_name in ["freecad_debug", "freecad_health", "mcp_versions"]:
        monkeypatch.setitem(sys.modules, mod_name, _ImportBlocker())

    return handlers_mod


@pytest.fixture
def server(mock_freecad, mock_handlers):
    """Create a FreeCADSocketServer instance with mocked dependencies."""
    # Need to clear any cached module to pick up our mocks
    if "freecad_mcp_handler" in sys.modules:
        del sys.modules["freecad_mcp_handler"]

    import freecad_mcp_handler as ss_mod
    server = ss_mod.FreeCADSocketServer()
    return server


@pytest.fixture
def ss_module(mock_freecad, mock_handlers):
    """Import the freecad_mcp_handler module with mocks in place."""
    if "freecad_mcp_handler" in sys.modules:
        del sys.modules["freecad_mcp_handler"]

    import freecad_mcp_handler as ss_mod
    return ss_mod


# ---------------------------------------------------------------------------
# Message Framing (server-side)
# ---------------------------------------------------------------------------

class TestServerFraming:
    """Test the server-side send_message/receive_message/recv_exact."""

    def _make_socketpair(self):
        import socket as sock_mod
        server_sock = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        path = f"/tmp/test_ss_framing_{os.getpid()}.sock"
        if os.path.exists(path):
            os.remove(path)
        server_sock.bind(path)
        server_sock.listen(1)
        client = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        client.connect(path)
        peer, _ = server_sock.accept()
        server_sock.close()
        os.remove(path)
        return client, peer

    def test_send_message(self, ss_module):
        client, peer = self._make_socketpair()
        try:
            assert ss_module.send_message(client, "hello") is True
            raw = peer.recv(4096)
            length = struct.unpack(">I", raw[:4])[0]
            assert length == 5
            assert raw[4:] == b"hello"
        finally:
            client.close()
            peer.close()

    def test_receive_message(self, ss_module):
        client, peer = self._make_socketpair()
        try:
            msg_bytes = "hello".encode("utf-8")
            peer.sendall(struct.pack(">I", len(msg_bytes)) + msg_bytes)
            result = ss_module.receive_message(client, timeout=5.0)
            assert result == "hello"
        finally:
            client.close()
            peer.close()

    def test_receive_oversized(self, ss_module):
        client, peer = self._make_socketpair()
        try:
            peer.sendall(struct.pack(">I", 100 * 1024))
            result = ss_module.receive_message(client, timeout=2.0)
            assert result is None
        finally:
            client.close()
            peer.close()

    def test_max_message_size_aligned(self, ss_module):
        """Server-side MAX_MESSAGE_SIZE should be 50KB, matching bridge."""
        assert ss_module.MAX_MESSAGE_SIZE == 50 * 1024

    def test_max_message_size_matches_bridge_module_directly(self, ss_module):
        """Real parity check, not two independent hardcoded literals.

        mcp_bridge_framing.py (bridge process) and freecad_mcp_handler.py
        (FreeCAD process) each define their own MAX_MESSAGE_SIZE — they
        can't share an import across the process boundary (same
        architectural constraint as instance_registry.scan_discovery /
        freecad_mcp_server._scan_discovery). Both this test AND
        test_bridge_framing.py's TestMaxMessageSizeThreshold used to assert
        only against a hardcoded `50 * 1024` on their own side, so the two
        constants could silently drift apart with every test staying
        green. This imports both modules and compares them directly.
        """
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        import mcp_bridge_framing
        assert ss_module.MAX_MESSAGE_SIZE == mcp_bridge_framing.MAX_MESSAGE_SIZE

    def test_receive_exactly_at_max_message_size_accepted(self, ss_module):
        """Confirmed as a surviving mutant during the full review: mutating
        `>` to `>=` on the server-side receive check left all tests passing
        because no test used a message of exactly MAX_MESSAGE_SIZE bytes —
        test_receive_oversized only checks well above the cap (100KB vs the
        50KB limit). The check is `message_len > MAX_MESSAGE_SIZE`, so a
        message of exactly the cap must be ACCEPTED.

        Sends from a background thread (matching
        test_bridge_framing.py's TestMaxMessageSizeThreshold): a
        MAX_MESSAGE_SIZE payload exceeds the socket's kernel send buffer, so
        a synchronous sendall() before receive_message starts draining would
        deadlock — both sides must run concurrently.
        """
        import threading
        client, peer = self._make_socketpair()
        try:
            msg = "x" * ss_module.MAX_MESSAGE_SIZE
            msg_bytes = msg.encode("utf-8")

            def sender():
                peer.sendall(struct.pack(">I", len(msg_bytes)) + msg_bytes)

            t = threading.Thread(target=sender)
            t.start()
            result = ss_module.receive_message(client, timeout=10.0)
            t.join()
            assert result == msg
        finally:
            client.close()
            peer.close()

    def test_receive_one_byte_over_max_message_size_rejected(self, ss_module):
        client, peer = self._make_socketpair()
        try:
            peer.sendall(struct.pack(">I", ss_module.MAX_MESSAGE_SIZE + 1))
            result = ss_module.receive_message(client, timeout=2.0)
            assert result is None
        finally:
            client.close()
            peer.close()


# ---------------------------------------------------------------------------
# _run_on_gui_thread
# ---------------------------------------------------------------------------

class TestRunOnGuiThread:
    def _simulate_gui_thread(self, server):
        """Simulate the GUI timer draining the task queue with tagged request IDs."""
        def process():
            time.sleep(0.05)
            req_id, task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put((req_id, result))
        return process

    def test_success_result(self, server):
        """A task returning {success: True, result: X} should produce {"result": X}."""
        def fake_task():
            return {"success": True, "result": "Box created"}

        t = threading.Thread(target=self._simulate_gui_thread(server))
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "Box created"

    def test_error_result(self, server):
        """A task returning {error: X} should produce {"error": X}."""
        def fake_task():
            return {"error": "Something broke"}

        t = threading.Thread(target=self._simulate_gui_thread(server))
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert "Something broke" in parsed["error"]

    def test_timeout(self, server):
        """In headless mode (QtCore=None) tasks run inline — no queue, no timeout.

        The GUI-mode timeout path (queue + QTimer) is only exercised when a real
        Qt event loop is present.  In the test environment FreeCAD.GuiUp=False so
        QtCore=None and _run_on_gui_thread runs the task synchronously and returns
        a result rather than a timeout error.
        """
        def fake_task():
            return {"success": True, "result": "ran inline"}

        response = server._run_on_gui_thread(fake_task, timeout=0.1)
        parsed = json.loads(response)
        # Headless: task ran inline, no error
        assert "error" not in parsed
        assert "ran inline" in parsed.get("result", "")

    def test_non_dict_result(self, server):
        """A task returning a plain value should be stringified."""
        def fake_task():
            return 42

        t = threading.Thread(target=self._simulate_gui_thread(server))
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "42"

    def test_qt_mode_success_dict_without_result_key_does_not_crash(self, server):
        """M3: a task dict with 'success' but no 'result' key (e.g.
        {"success": True, "view": "top"}, the shape the now-removed
        view_ops.set_view_gui_safe used to return) previously matched a
        GUI-mode-only `if "success" in result:` branch that unconditionally
        indexed result["result"] -> KeyError. Pre-loading the response
        queue directly (rather than a background thread) avoids needing
        QtCore's real event loop timing at all — same pattern as
        test_stale_response_discarded above.

        Must fall through to stringifying the whole dict, matching what the
        headless branch has always done — the general contract any GUI
        task returning a bare dict depends on.
        """
        import freecad_mcp_handler as ss_mod
        ss_mod.QtCore = MagicMock()
        try:
            req_id = server._request_counter + 1
            server._gui_response_queue.put((req_id, {"success": True, "view": "top"}))
            result = json.loads(server._run_on_gui_thread(
                lambda: {"success": True, "view": "top"}, timeout=2.0
            ))
            assert "error" not in result
            assert "success" in result["result"]
        finally:
            ss_mod.QtCore = None


# ---------------------------------------------------------------------------
# _process_gui_tasks
# ---------------------------------------------------------------------------

class TestProcessGuiTasks:
    def test_processes_queued_task(self, server):
        """_process_gui_tasks should drain the queue and put results."""
        server._gui_task_queue.put((1, lambda: {"success": True, "result": "done"}))
        server._process_gui_tasks()
        req_id, result = server._gui_response_queue.get_nowait()
        assert req_id == 1
        assert result == {"success": True, "result": "done"}

    def test_handles_task_exception(self, server):
        """If a task raises, it should put an error dict instead of crashing."""
        def bad_task():
            raise ValueError("boom")

        server._gui_task_queue.put((2, bad_task))
        server._process_gui_tasks()
        req_id, result = server._gui_response_queue.get_nowait()
        assert req_id == 2
        assert "error" in result
        assert "boom" in result["error"]

    def test_processes_multiple_tasks(self, server):
        """Should drain all tasks in one call."""
        for i in range(3):
            server._gui_task_queue.put((i, lambda i=i: {"success": True, "result": f"task_{i}"}))

        server._process_gui_tasks()

        results = []
        while not server._gui_response_queue.empty():
            results.append(server._gui_response_queue.get_nowait())
        assert len(results) == 3
        # Each result should be a (req_id, result_dict) tuple
        for req_id, result in results:
            assert "success" in result


# ---------------------------------------------------------------------------
# _process_command
# ---------------------------------------------------------------------------

class TestProcessCommand:
    def test_valid_command(self, server):
        """A valid command should be routed to _execute_tool."""
        server._execute_tool = MagicMock(return_value=json.dumps({"result": "ok"}))
        response = server._process_command('{"tool": "create_box", "args": {"length": 10}}')
        parsed = json.loads(response)
        assert parsed["result"] == "ok"
        server._execute_tool.assert_called_once_with("create_box", {"length": 10})

    def test_malformed_json(self, server):
        response = server._process_command("not json at all")
        parsed = json.loads(response)
        assert "Invalid JSON" in parsed["error"]

    def test_missing_tool(self, server):
        response = server._process_command('{"args": {}}')
        parsed = json.loads(response)
        assert "No tool specified" in parsed["error"]

    def test_empty_tool(self, server):
        response = server._process_command('{"tool": "", "args": {}}')
        parsed = json.loads(response)
        assert "No tool specified" in parsed["error"]

    def test_exception_in_execute_tool(self, server):
        server._execute_tool = MagicMock(side_effect=RuntimeError("handler crashed"))
        response = server._process_command('{"tool": "create_box", "args": {}}')
        parsed = json.loads(response)
        assert "error" in parsed
        assert "handler crashed" in parsed["error"]

    def test_missing_args_defaults_to_empty(self, server):
        server._execute_tool = MagicMock(return_value=json.dumps({"result": "ok"}))
        server._process_command('{"tool": "create_box"}')
        server._execute_tool.assert_called_once_with("create_box", {})

    def test_typo_d_args_key_rejected_not_silently_empty(self, server):
        """H16: a key typo'd as 'arg' instead of 'args' previously fell
        through `command.get("args", {})` to an empty dict indistinguishable
        from a legitimate no-args call — the tool would then run with no
        args and fail several layers deeper with a confusing error instead
        of a clear one here."""
        server._execute_tool = MagicMock(return_value=json.dumps({"result": "ok"}))
        response = server._process_command('{"tool": "create_box", "arg": {"length": 10}}')
        parsed = json.loads(response)
        assert "error" in parsed
        assert "arg" in parsed["error"]
        server._execute_tool.assert_not_called()

    def test_unrecognized_extra_key_rejected(self, server):
        server._execute_tool = MagicMock(return_value=json.dumps({"result": "ok"}))
        response = server._process_command(
            '{"tool": "create_box", "args": {}, "request_id": "abc"}'
        )
        parsed = json.loads(response)
        assert "error" in parsed
        assert "request_id" in parsed["error"]
        server._execute_tool.assert_not_called()

    def test_non_dict_command_rejected(self, server):
        """A JSON array or scalar at the top level previously reached
        `.get("tool", "")`, which would raise AttributeError caught by the
        generic except-Exception branch — a confusing internal error instead
        of a clear "malformed request" message."""
        response = server._process_command('["not", "a", "dict"]')
        parsed = json.loads(response)
        assert "error" in parsed
        assert "malformed" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# _execute_tool routing
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def test_direct_map_routing(self, server):
        """Tools in the direct_map should call the handler via _call_on_gui_thread_async."""
        server._call_on_gui_thread_async = MagicMock(return_value=json.dumps({"result": "box"}))
        result = server._execute_tool("create_box", {"length": 10})
        server._call_on_gui_thread_async.assert_called_once()
        call_args = server._call_on_gui_thread_async.call_args
        assert call_args[0][1] == {"length": 10}  # args passed through
        assert call_args[0][2] == "create_box"  # label

    def test_generic_dispatch_routing(self, server):
        """Tools in generic_dispatch_map should use _dispatch_to_handler."""
        server._dispatch_to_handler = MagicMock(return_value=json.dumps({"result": "ok"}))
        # Satisfy CAM version gate so all five tools reach _dispatch_to_handler
        from freecad_mcp_handler import CAM_MIN_FC_VERSION
        server._fc_version = CAM_MIN_FC_VERSION
        for tool in ["cam_operations", "cam_tools", "cam_tool_controllers",
                      "draft_operations", "spreadsheet_operations"]:
            server._execute_tool(tool, {"operation": "test"})

        assert server._dispatch_to_handler.call_count == 5

    def test_cam_version_gate(self, server):
        """CAM tools return a version error when FreeCAD is below CAM_MIN_FC_VERSION."""
        from freecad_mcp_handler import CAM_MIN_FC_VERSION
        server._fc_version = (CAM_MIN_FC_VERSION[0], CAM_MIN_FC_VERSION[1] - 1, 0)
        for tool in ["cam_operations", "cam_tools", "cam_tool_controllers"]:
            result = json.loads(server._execute_tool(tool, {"operation": "test"}))
            assert "error" in result
            assert "CAM tools require" in result["error"]

    def test_partdesign_routing(self, server):
        server._dispatch_partdesign = MagicMock(return_value=json.dumps({"result": "ok"}))
        server._execute_tool("partdesign_operations", {"operation": "pad"})
        server._dispatch_partdesign.assert_called_once_with({"operation": "pad"})

    def test_view_control_routing(self, server):
        server._dispatch_view_control = MagicMock(return_value=json.dumps({"result": "ok"}))
        server._execute_tool("view_control", {"operation": "fit_all"})
        server._dispatch_view_control.assert_called_once_with({"operation": "fit_all"})

    def test_part_operations_routing(self, server):
        server._dispatch_part_operations = MagicMock(return_value=json.dumps({"result": "ok"}))
        server._execute_tool("part_operations", {"operation": "box"})
        server._dispatch_part_operations.assert_called_once_with({"operation": "box"})

    def test_execute_python_routing(self, server):
        server.execute_python_ops.execute = MagicMock(return_value=json.dumps({"result": "2"}))
        server._execute_tool("execute_python", {"code": "1+1"})
        server.execute_python_ops.execute.assert_called_once_with({"code": "1+1"})

    def test_get_debug_logs_routing(self, server):
        server.diagnostics_ops.get_debug_logs = MagicMock(return_value=json.dumps({"result": "logs"}))
        server._execute_tool("get_debug_logs", {"count": 10})
        server.diagnostics_ops.get_debug_logs.assert_called_once_with({"count": 10})

    def test_unknown_tool(self, server):
        result = server._execute_tool("nonexistent_tool", {})
        parsed = json.loads(result)
        assert "Unknown tool" in parsed["error"]

    def test_all_direct_map_tools(self, server):
        """Every tool in the direct_map should route without error."""
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        direct_tools = [
            "create_box", "create_cylinder", "create_sphere", "create_cone",
            "create_torus", "create_wedge", "fuse_objects", "cut_objects",
            "common_objects", "move_object", "rotate_object", "copy_object",
            "array_object", "create_sketch", "sketch_verify",
        ]
        for tool in direct_tools:
            result = server._execute_tool(tool, {})
            parsed = json.loads(result)
            assert "error" not in parsed, f"{tool} returned error: {parsed}"


# ---------------------------------------------------------------------------
# _dispatch_to_handler (generic dispatch)
# ---------------------------------------------------------------------------

class TestDispatchToHandler:
    def test_valid_operation(self, server):
        """Should look up the operation via _ALLOWED_OPERATIONS and call it via async GUI path."""
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"create_spreadsheet"})
        handler.create_spreadsheet = MagicMock(return_value="Spreadsheet created")

        with patch.object(server, '_call_on_gui_thread_async',
                          return_value=json.dumps({"result": "Spreadsheet created"})):
            result = json.loads(
                server._dispatch_to_handler(
                    handler, {"operation": "create_spreadsheet"}, "spreadsheet_operations"
                )
            )
        assert result["result"] == "Spreadsheet created"

    def test_unknown_operation(self, server):
        """Operations not in _ALLOWED_OPERATIONS are rejected."""
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"list"})
        result = server._dispatch_to_handler(
            handler, {"operation": "nonexistent"}, "test_tool"
        )
        parsed = json.loads(result)
        assert "Unknown test_tool operation: nonexistent" in parsed["error"]

    def test_missing_operation(self, server):
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"list"})
        result = server._dispatch_to_handler(handler, {}, "test_tool")
        parsed = json.loads(result)
        assert "Missing operation" in parsed["error"]

    def test_no_registry_rejected(self, server):
        """Handlers without _ALLOWED_OPERATIONS are rejected at dispatch time."""
        handler = MagicMock(spec=[])  # no _ALLOWED_OPERATIONS
        result = server._dispatch_to_handler(
            handler, {"operation": "anything"}, "test_tool"
        )
        parsed = json.loads(result)
        assert "_ALLOWED_OPERATIONS" in parsed["error"]


# ---------------------------------------------------------------------------
# _dispatch_partdesign
# ---------------------------------------------------------------------------

class TestDispatchPartDesign:
    def test_known_operations(self, server):
        """All mapped PartDesign operations should route correctly."""
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        ops = ["pad", "fillet", "chamfer", "hole", "linear_pattern",
               "mirror", "revolution", "loft", "sweep", "draft", "shell"]
        for op in ops:
            result = server._dispatch_partdesign({"operation": op})
            parsed = json.loads(result)
            assert "error" not in parsed, f"PartDesign {op} returned error"

    def test_unknown_operation(self, server):
        result = server._dispatch_partdesign({"operation": "nonexistent"})
        parsed = json.loads(result)
        assert "Unknown PartDesign operation" in parsed["error"]


# ---------------------------------------------------------------------------
# _dispatch_part_operations
# ---------------------------------------------------------------------------

class TestDispatchPartOperations:
    def test_primitive_routing(self, server):
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        for op in ["box", "cylinder", "sphere", "cone", "torus", "wedge"]:
            result = server._dispatch_part_operations({"operation": op})
            parsed = json.loads(result)
            assert "error" not in parsed, f"Part {op} returned error"

    def test_boolean_routing(self, server):
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        for op in ["fuse", "cut", "common"]:
            result = server._dispatch_part_operations({"operation": op})
            parsed = json.loads(result)
            assert "error" not in parsed, f"Part {op} returned error"

    def test_transform_routing(self, server):
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        for op in ["move", "rotate", "copy", "array"]:
            result = server._dispatch_part_operations({"operation": op})
            parsed = json.loads(result)
            assert "error" not in parsed, f"Part {op} returned error"

    def test_advanced_routing(self, server):
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "ok"}))
        for op in ["extrude", "revolve", "loft", "sweep"]:
            result = server._dispatch_part_operations({"operation": op})
            parsed = json.loads(result)
            assert "error" not in parsed, f"Part {op} returned error"

    def test_unknown_operation(self, server):
        result = server._dispatch_part_operations({"operation": "nonexistent"})
        parsed = json.loads(result)
        assert "Unknown Part operation" in parsed["error"]


# ---------------------------------------------------------------------------
# _dispatch_view_control
# ---------------------------------------------------------------------------

class TestDispatchViewControl:
    def test_known_operations(self, server):
        """All mapped view_control operations should route.

        GUI ops go through _run_on_gui_thread; safe ops call handlers directly.
        We mock _run_on_gui_thread to return a success JSON string so tests
        don't block waiting for the (absent) GUI thread.
        """
        gui_ops = ["screenshot", "set_view", "fit_all", "zoom_in", "zoom_out",
                   "select_object", "clear_selection", "get_selection",
                   "hide_object", "show_object", "delete_object",
                   "undo", "redo", "activate_workbench"]
        safe_ops = ["create_document", "save_document", "list_objects"]

        # GUI ops: mock _call_on_gui_thread_async to avoid blocking.
        # Also force non-macOS path so screenshot doesn't take the Darwin early-exit.
        for op in gui_ops:
            with patch.object(server, '_call_on_gui_thread_async',
                              return_value=json.dumps({"result": "ok"})), \
                 patch("freecad_mcp_handler.platform.system", return_value="Linux"):
                result = server._dispatch_view_control({"operation": op})
                parsed = json.loads(result)
                assert "error" not in parsed, f"view_control {op} returned error: {parsed}"

        # Safe ops: mock the handler methods directly
        handler_method = MagicMock(return_value="ok")
        for op in safe_ops:
            with patch.object(server, 'document_ops') as mock_doc:
                mock_doc.create_document = handler_method
                mock_doc.save_document = handler_method
                mock_doc.list_objects = handler_method

                result = server._dispatch_view_control({"operation": op})
                parsed = json.loads(result)
                assert "error" not in parsed, f"view_control {op} returned error: {parsed}"

    def test_unknown_operation(self, server):
        result = server._dispatch_view_control({"operation": "nonexistent"})
        parsed = json.loads(result)
        assert "Unknown view control operation" in parsed["error"]

    def test_handler_exception(self, server):
        """If the handler raises, view_control should catch and return error."""
        server.view_ops.take_screenshot = MagicMock(side_effect=RuntimeError("screenshot failed"))

        def run_inline(method, args, label):
            try:
                result = method(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"{label} error: {e}"})

        with patch.object(server, '_call_on_gui_thread_async', side_effect=run_inline), \
             patch("freecad_mcp_handler.platform.system", return_value="Linux"):
            result = server._dispatch_view_control({"operation": "screenshot"})
            parsed = json.loads(result)
            assert "screenshot failed" in parsed["error"]

    def test_safe_op_exception(self, server):
        """If a safe (non-GUI) handler raises, view_control should catch and return error."""
        server.document_ops.list_objects = MagicMock(side_effect=RuntimeError("list failed"))
        result = server._dispatch_view_control({"operation": "list_objects"})
        parsed = json.loads(result)
        assert "list failed" in parsed["error"]


# ---------------------------------------------------------------------------
# _call_on_gui_thread
# ---------------------------------------------------------------------------

class TestCallOnGuiThread:
    def _simulate_gui_thread(self, server):
        """Simulate the GUI timer draining the task queue with tagged request IDs."""
        def process():
            time.sleep(0.05)
            req_id, task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put((req_id, result))
        return process

    def test_wraps_handler_success(self, server):
        """Should wrap handler result in {success: True, result: ...}."""
        handler_method = MagicMock(return_value="created")

        t = threading.Thread(target=self._simulate_gui_thread(server))
        t.start()

        response = server._call_on_gui_thread(handler_method, {"x": 1}, "test")
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "created"
        handler_method.assert_called_once_with({"x": 1})

    def test_wraps_handler_exception(self, server):
        """If handler raises, should return error with traceback."""
        handler_method = MagicMock(side_effect=ValueError("bad value"))

        t = threading.Thread(target=self._simulate_gui_thread(server))
        t.start()

        response = server._call_on_gui_thread(handler_method, {}, "test")
        t.join()
        parsed = json.loads(response)
        assert "bad value" in parsed["error"]


class TestCallOnGuiThreadReload:
    """_call_on_gui_thread_reload wraps _reload_handlers() for GUI-thread
    execution. Unlike _call_on_gui_thread/_call_on_gui_thread_async,
    _reload_handlers() already returns a full JSON string rather than a
    plain result value, so this has to parse-then-rewrap instead of letting
    _run_on_gui_thread's generic dict handling wrap it directly -- otherwise
    the JSON string comes back double-encoded as an escaped string (see
    _call_on_gui_thread_reload's docstring)."""

    def test_wraps_reload_result_without_double_encoding(self, server):
        server._reload_handlers = MagicMock(
            return_value=json.dumps({
                "result": "Reloaded 24 handler modules successfully",
                "modules_reloaded": 24,
            })
        )
        response = server._call_on_gui_thread_reload()
        parsed = json.loads(response)
        # The inner value must be a real nested object, not a JSON-in-a-string.
        assert isinstance(parsed["result"], dict)
        assert parsed["result"]["result"] == "Reloaded 24 handler modules successfully"
        assert parsed["result"]["modules_reloaded"] == 24

    def test_propagates_reload_error_json(self, server):
        server._reload_handlers = MagicMock(
            return_value=json.dumps({"error": "Handler reload failed: boom"})
        )
        response = server._call_on_gui_thread_reload()
        parsed = json.loads(response)
        assert "boom" in parsed["error"]

    def test_reload_handlers_raising_does_not_propagate_raw_traceback(self, server):
        """If _reload_handlers itself raises instead of returning an error
        JSON string, the wrapper must still return valid, parseable JSON."""
        server._reload_handlers = MagicMock(side_effect=RuntimeError("kaboom"))
        response = server._call_on_gui_thread_reload()
        parsed = json.loads(response)
        assert "kaboom" in parsed["error"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_default_socket_path(self, ss_module):
        # SOCKET_PATH is None when FREECAD_MCP_SOCKET is unset; the real path
        # is generated per-instance at start_server() time (uuid-suffixed).
        assert ss_module.SOCKET_PATH is None

    def test_default_windows_port(self, ss_module):
        assert ss_module.WINDOWS_PORT == 23456

    def test_max_message_size(self, ss_module):
        assert ss_module.MAX_MESSAGE_SIZE == 50 * 1024


# ---------------------------------------------------------------------------
# Async Job Machinery
# ---------------------------------------------------------------------------

class TestExecutePythonAsync:
    """Tests for _execute_python_async (submit-and-return-job-id path)."""

    def test_returns_job_id(self, server):
        """Should return a job_id and status=submitted."""
        result = json.loads(server._execute_python_async({"code": "1 + 1"}))
        assert "job_id" in result
        assert result["status"] == "submitted"
        assert len(result["job_id"]) == 8

    def test_empty_code_returns_error(self, server):
        result = json.loads(server._execute_python_async({"code": ""}))
        assert "No code provided" in result["error"]

    def test_no_code_key_returns_error(self, server):
        result = json.loads(server._execute_python_async({}))
        assert "No code provided" in result["error"]

    def test_max_jobs_limit(self, server):
        """Should reject when MAX_ASYNC_JOBS is reached."""
        import freecad_mcp_handler as ss_mod
        # Fill up the job slots
        for i in range(ss_mod.MAX_ASYNC_JOBS):
            server._async_jobs[f"job{i}"] = {
                "status": "running",
                "started": time.time(),
                "tool": "test",
            }
        result = json.loads(server._execute_python_async({"code": "1"}))
        assert "Too many async jobs" in result["error"]

    def test_stale_jobs_cleaned_before_limit_check(self, server):
        """Stale completed jobs should be cleaned up before checking the limit."""
        import freecad_mcp_handler as ss_mod
        # Fill with stale completed jobs
        old_time = time.time() - ss_mod.ASYNC_JOB_TTL - 10
        for i in range(ss_mod.MAX_ASYNC_JOBS):
            server._async_jobs[f"stale{i}"] = {
                "status": "done",
                "started": old_time,
                "finished": old_time,
                "tool": "test",
            }
        # Should succeed because stale jobs are cleaned first
        result = json.loads(server._execute_python_async({"code": "1 + 1"}))
        assert "job_id" in result

    def test_job_registered_as_running(self, server):
        """After submit, the job should be tracked as running (Qt mode)."""
        import freecad_mcp_handler as ss_mod
        # With QtCore set, async runs via queue (not inline) so status stays "running"
        ss_mod.QtCore = MagicMock()
        try:
            result = json.loads(server._execute_python_async({"code": "x = 1"}))
            job_id = result["job_id"]
            assert job_id in server._async_jobs
            assert server._async_jobs[job_id]["status"] == "running"
            assert server._async_jobs[job_id]["tool"] == "execute_python_async"
        finally:
            ss_mod.QtCore = None


class TestPollJob:
    """Tests for _poll_job — checking async job status."""

    def test_missing_job_id(self, server):
        result = json.loads(server._poll_job({}))
        assert "job_id required" in result["error"]

    def test_unknown_job_id(self, server):
        result = json.loads(server._poll_job({"job_id": "nonexistent"}))
        assert "Unknown job_id" in result["error"]

    def test_running_job_returns_status(self, server):
        server._async_jobs["abc123"] = {
            "status": "running",
            "started": time.time() - 5,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "abc123"}))
        assert result["status"] == "running"
        assert result["elapsed_s"] >= 4

    def test_running_job_long_warning(self, server):
        """Jobs running > 120s should include a warning."""
        server._async_jobs["slow"] = {
            "status": "running",
            "started": time.time() - 200,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "slow"}))
        assert result["status"] == "running"
        assert "warning" in result

    def test_done_job_returns_result(self, server):
        server._async_jobs["done1"] = {
            "status": "done",
            "started": time.time() - 2,
            "result": {"result": "42"},
            "elapsed": 1.5,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "done1"}))
        assert result["status"] == "done"
        assert result["result"] == "42"
        # Job should be removed after retrieval
        assert "done1" not in server._async_jobs

    def test_done_job_with_error_in_result(self, server):
        """If the task result contains an error dict, poll should return error status."""
        server._async_jobs["err1"] = {
            "status": "done",
            "started": time.time() - 1,
            "result": {"error": "something broke"},
            "elapsed": 0.5,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "err1"}))
        assert result["status"] == "error"
        assert "something broke" in result["error"]

    def test_error_job_returns_error(self, server):
        server._async_jobs["err2"] = {
            "status": "error",
            "started": time.time() - 3,
            "error": "crashed",
            "elapsed": 2.0,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "err2"}))
        assert result["status"] == "error"
        assert "crashed" in result["error"]
        # Job should be removed after retrieval
        assert "err2" not in server._async_jobs

    def test_done_with_error_in_result_forwards_error_id(self, server):
        """A task result carrying error_id must forward it so the client can
        call get_last_traceback. Regression: _poll_job used to drop error_id,
        making the traceback-retrieval feature dead for async errors."""
        server._async_jobs["e_id1"] = {
            "status": "done",
            "started": time.time() - 1,
            "result": {"error": "boom", "error_id": "err-0007"},
            "elapsed": 0.5,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "e_id1"}))
        assert result["status"] == "error"
        assert result["error_id"] == "err-0007"

    def test_error_job_forwards_error_id(self, server):
        """An error-status job with a stored error_id must forward it."""
        server._async_jobs["e_id2"] = {
            "status": "error",
            "started": time.time() - 2,
            "error": "crashed",
            "error_id": "err-0042",
            "elapsed": 1.0,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "e_id2"}))
        assert result["status"] == "error"
        assert result["error_id"] == "err-0042"

    def test_done_job_non_dict_result(self, server):
        """If result is not a dict, should stringify it."""
        server._async_jobs["nd1"] = {
            "status": "done",
            "started": time.time(),
            "result": "plain string",
            "elapsed": 0.1,
            "tool": "test",
        }
        result = json.loads(server._poll_job({"job_id": "nd1"}))
        assert result["status"] == "done"
        assert result["result"] == "plain string"


class TestCancelJob:
    """Tests for _cancel_job."""

    def test_missing_job_id(self, server):
        result = json.loads(server._cancel_job({}))
        assert "job_id required" in result["error"]

    def test_unknown_job_id(self, server):
        result = json.loads(server._cancel_job({"job_id": "nope"}))
        assert "Unknown job_id" in result["error"]

    def test_cancel_running_job(self, server):
        server._async_jobs["run1"] = {
            "status": "running",
            "started": time.time() - 10,
            "tool": "test",
        }
        result = json.loads(server._cancel_job({"job_id": "run1"}))
        assert "result" in result
        assert "cancelled" in result["result"]
        # Job should be marked as error
        assert server._async_jobs["run1"]["status"] == "error"

    def test_cancel_non_running_job(self, server):
        server._async_jobs["done1"] = {
            "status": "done",
            "started": time.time(),
            "result": "ok",
            "tool": "test",
        }
        result = json.loads(server._cancel_job({"job_id": "done1"}))
        assert "not running" in result["error"]

    def test_cancel_attempts_freecad_cancel(self, server):
        """If FreeCADGui is available, should call cancelOperation."""
        import freecad_mcp_handler as ss_mod
        mock_gui = MagicMock()
        ss_mod.FreeCADGui = mock_gui

        server._async_jobs["gui1"] = {
            "status": "running",
            "started": time.time() - 5,
            "tool": "test",
        }
        result = json.loads(server._cancel_job({"job_id": "gui1"}))
        assert "cancel flag set" in result["result"]
        mock_gui.cancelOperation.assert_called_once()


class TestListJobs:
    """Tests for _list_jobs."""

    def test_empty_jobs(self, server):
        result = json.loads(server._list_jobs({}))
        assert result["count"] == 0
        assert result["jobs"] == {}

    def test_lists_running_jobs(self, server):
        server._async_jobs["j1"] = {
            "status": "running",
            "started": time.time() - 5,
            "tool": "execute_python_async",
        }
        server._async_jobs["j2"] = {
            "status": "done",
            "started": time.time() - 10,
            "tool": "fuse_objects",
        }
        result = json.loads(server._list_jobs({}))
        assert result["count"] == 2
        assert result["jobs"]["j1"]["status"] == "running"
        assert result["jobs"]["j2"]["status"] == "done"
        assert result["jobs"]["j1"]["tool"] == "execute_python_async"

    def test_label_used_when_tool_field_absent(self, server):
        """Async jobs carry 'label' not 'tool' — list_jobs must fall back to it
        instead of showing '?'."""
        server._async_jobs["j3"] = {
            "status": "running",
            "started": time.time() - 1,
            "label": "build_sketch",
        }
        result = json.loads(server._list_jobs({}))
        assert result["jobs"]["j3"]["tool"] == "build_sketch"


class TestCleanupStaleAsyncJobs:
    """Tests for _cleanup_stale_async_jobs."""

    def test_removes_stale_done_jobs(self, server):
        import freecad_mcp_handler as ss_mod
        old = time.time() - ss_mod.ASYNC_JOB_TTL - 100
        server._async_jobs["old1"] = {
            "status": "done",
            "started": old,
            "finished": old,
        }
        server._async_jobs["fresh1"] = {
            "status": "done",
            "started": time.time(),
            "finished": time.time(),
        }
        server._cleanup_stale_async_jobs()
        assert "old1" not in server._async_jobs
        assert "fresh1" in server._async_jobs

    def test_does_not_remove_running_jobs(self, server):
        import freecad_mcp_handler as ss_mod
        old = time.time() - ss_mod.ASYNC_JOB_TTL - 100
        server._async_jobs["running1"] = {
            "status": "running",
            "started": old,
        }
        server._cleanup_stale_async_jobs()
        assert "running1" in server._async_jobs

    def test_removes_stale_error_jobs(self, server):
        import freecad_mcp_handler as ss_mod
        old = time.time() - ss_mod.ASYNC_JOB_TTL - 100
        server._async_jobs["err1"] = {
            "status": "error",
            "started": old,
            "finished": old,
        }
        server._cleanup_stale_async_jobs()
        assert "err1" not in server._async_jobs

    def test_exactly_at_ttl_is_kept(self, server):
        """age == TTL is NOT removed — the check is strict `>`. Pins which side
        of the boundary the `>`/`>=` choice falls on. Time is mocked so the
        boundary is deterministic (real time would drift age just past TTL)."""
        import freecad_mcp_handler as ss_mod
        from unittest.mock import patch
        T = 1_000_000.0
        server._async_jobs["b"] = {"status": "done", "started": T - ss_mod.ASYNC_JOB_TTL}
        with patch.object(ss_mod.time, "time", return_value=T):
            server._cleanup_stale_async_jobs()
        assert "b" in server._async_jobs

    def test_one_second_over_ttl_is_removed(self, server):
        import freecad_mcp_handler as ss_mod
        from unittest.mock import patch
        T = 1_000_000.0
        server._async_jobs["b"] = {"status": "done", "started": T - ss_mod.ASYNC_JOB_TTL - 1}
        with patch.object(ss_mod.time, "time", return_value=T):
            server._cleanup_stale_async_jobs()
        assert "b" not in server._async_jobs


# ---------------------------------------------------------------------------
# _run_on_gui_thread edge cases
# ---------------------------------------------------------------------------

class TestRunOnGuiThreadEdgeCases:
    """Test busy guard, stale responses, and headless mode."""

    def test_busy_guard_rejects_when_busy(self, server):
        """If GUI thread is already busy, should reject immediately."""
        import freecad_mcp_handler as ss_mod
        # Busy guard only applies in Qt mode (QtCore is not None)
        ss_mod.QtCore = MagicMock()
        try:
            server._gui_thread_busy = True
            result = json.loads(server._run_on_gui_thread(lambda: {"result": "ok"}))
            assert "busy" in result["error"].lower()
        finally:
            server._gui_thread_busy = False
            ss_mod.QtCore = None

    def test_stale_response_discarded(self, server):
        """Stale responses from timed-out requests should be skipped."""
        import freecad_mcp_handler as ss_mod
        # Stale-response logic only applies in Qt mode
        ss_mod.QtCore = MagicMock()
        try:
            # Pre-load the response queue: stale response first, then correct
            correct_id = server._request_counter + 1
            server._gui_response_queue.put((999, {"result": "stale"}))
            server._gui_response_queue.put((correct_id, {"success": True, "result": "fresh"}))

            result = json.loads(server._run_on_gui_thread(
                lambda: {"result": "ignored"}, timeout=2.0
            ))
            # The method queues a task with the next request_counter; the first
            # dequeued response (id=999) won't match, so it discards it and
            # gets the second one (correct_id)
            assert result["result"] == "fresh"
        finally:
            ss_mod.QtCore = None

    def test_headless_mode_runs_inline(self, server):
        """When QtCore is None, tasks run inline on the calling thread."""
        import freecad_mcp_handler as ss_mod
        original = ss_mod.QtCore
        ss_mod.QtCore = None
        try:
            result = json.loads(server._run_on_gui_thread(
                lambda: {"result": "headless_ok"}
            ))
            assert result["result"] == "headless_ok"
        finally:
            ss_mod.QtCore = original

    def test_headless_mode_error_handling(self, server):
        """Headless mode should catch and return errors."""
        import freecad_mcp_handler as ss_mod
        original = ss_mod.QtCore
        ss_mod.QtCore = None
        try:
            result = json.loads(server._run_on_gui_thread(
                lambda: (_ for _ in ()).throw(ValueError("headless boom"))
            ))
            assert "error" in result
        finally:
            ss_mod.QtCore = original

    def test_headless_mode_error_dict(self, server):
        """Headless mode should pass through error dicts."""
        import freecad_mcp_handler as ss_mod
        original = ss_mod.QtCore
        ss_mod.QtCore = None
        try:
            result = json.loads(server._run_on_gui_thread(
                lambda: {"error": "custom error msg"}
            ))
            assert result["error"] == "custom error msg"
        finally:
            ss_mod.QtCore = original


# ---------------------------------------------------------------------------
# _run_on_gui_thread_async
# ---------------------------------------------------------------------------

class TestRunOnGuiThreadAsync:
    """Tests for _run_on_gui_thread_async (console/headless mode)."""

    def test_headless_success(self, server):
        """In headless mode, task runs inline and populates job dict."""
        import freecad_mcp_handler as ss_mod
        original = ss_mod.QtCore
        ss_mod.QtCore = None
        try:
            job_id = "test_async_1"
            server._async_jobs[job_id] = {
                "status": "running",
                "started": time.time(),
            }
            server._run_on_gui_thread_async(
                job_id, lambda: {"result": "async_done"}
            )
            assert server._async_jobs[job_id]["status"] == "done"
            assert server._async_jobs[job_id]["result"] == {"result": "async_done"}
        finally:
            ss_mod.QtCore = original

    def test_headless_error(self, server):
        """In headless mode, task errors are captured in the job dict."""
        import freecad_mcp_handler as ss_mod
        original = ss_mod.QtCore
        ss_mod.QtCore = None
        try:
            job_id = "test_async_err"
            server._async_jobs[job_id] = {
                "status": "running",
                "started": time.time(),
            }

            def failing_task():
                raise RuntimeError("async fail")

            server._run_on_gui_thread_async(job_id, failing_task)
            assert server._async_jobs[job_id]["status"] == "error"
            assert "async fail" in server._async_jobs[job_id]["error"]
        finally:
            ss_mod.QtCore = original

    def test_qt_mode_queues_task(self, server):
        """With QtCore available, task should be queued (not run inline)."""
        import freecad_mcp_handler as ss_mod
        ss_mod.QtCore = MagicMock()
        try:
            job_id = "test_qt_q"
            server._async_jobs[job_id] = {
                "status": "running",
                "started": time.time(),
            }
            server._run_on_gui_thread_async(
                job_id, lambda: {"result": "queued"}
            )
            # Task should be in the queue, not yet executed
            assert not server._gui_task_queue.empty()
            req_id, task_fn = server._gui_task_queue.get_nowait()
            assert req_id == f"async:{job_id}"
        finally:
            ss_mod.QtCore = None


# ---------------------------------------------------------------------------
# _call_on_gui_thread_async
# ---------------------------------------------------------------------------

class TestCallOnGuiThreadAsync:
    """Tests for _call_on_gui_thread_async (boolean op path)."""

    def test_returns_job_id(self, server):
        method = MagicMock(return_value="fused")
        result = json.loads(
            server._call_on_gui_thread_async(method, {"tool1": "a"}, "fuse")
        )
        assert "job_id" in result
        assert result["status"] == "submitted"

    def test_max_jobs_limit(self, server):
        import freecad_mcp_handler as ss_mod
        for i in range(ss_mod.MAX_ASYNC_JOBS):
            server._async_jobs[f"bj{i}"] = {
                "status": "running",
                "started": time.time(),
            }
        result = json.loads(
            server._call_on_gui_thread_async(MagicMock(), {}, "fuse")
        )
        assert "Too many async jobs" in result["error"]


# ---------------------------------------------------------------------------
# _cancel_operation
# ---------------------------------------------------------------------------

class TestCancelOperation:
    """Tests for _cancel_operation (FreeCADGui.cancelOperation wrapper)."""

    def test_success(self, server):
        # _cancel_operation does `import FreeCADGui as Gui` — patch sys.modules
        mock_gui = MagicMock()
        with patch.dict(sys.modules, {"FreeCADGui": mock_gui}):
            result = json.loads(server._cancel_operation({}))
            assert "Cancel requested" in result["result"]
            mock_gui.cancelOperation.assert_called_once()

    def test_no_gui_raises(self, server):
        # When FreeCADGui.cancelOperation raises, should return error
        mock_gui = MagicMock()
        mock_gui.cancelOperation.side_effect = AttributeError("no GUI")
        with patch.dict(sys.modules, {"FreeCADGui": mock_gui}):
            result = json.loads(server._cancel_operation({}))
            assert "error" in result


# ---------------------------------------------------------------------------
# _dispatch_sketch
# ---------------------------------------------------------------------------

class TestDispatchSketch:
    """Tests for _dispatch_sketch routing."""

    def test_known_operations(self, server):
        """All sketch operations should route through _call_on_gui_thread."""
        sketch_ops = [
            "create_sketch", "close_sketch", "verify_sketch",
            "add_line", "add_circle", "add_rectangle", "add_arc",
            "add_polygon", "add_slot", "add_fillet",
            "add_constraint", "delete_constraint", "list_constraints",
            "add_external_geometry",
        ]
        for op in sketch_ops:
            with patch.object(server, '_call_on_gui_thread',
                              return_value=json.dumps({"result": "ok"})):
                result = server._dispatch_sketch({"operation": op})
                parsed = json.loads(result)
                assert "error" not in parsed, f"sketch {op} returned error: {parsed}"

    def test_unknown_operation(self, server):
        result = json.loads(server._dispatch_sketch({"operation": "nonexistent"}))
        assert "Unknown Sketch operation" in result["error"]

    def test_empty_operation(self, server):
        result = json.loads(server._dispatch_sketch({"operation": ""}))
        assert "Unknown Sketch operation" in result["error"]

    def test_missing_operation(self, server):
        result = json.loads(server._dispatch_sketch({}))
        assert "Unknown Sketch operation" in result["error"]


# ---------------------------------------------------------------------------
# _dispatch_view_control — extended ops
# ---------------------------------------------------------------------------

class TestDispatchViewControlExtended:
    """Test additional view_control operations (clip plane, checkpoint, etc.)."""

    def test_checkpoint_is_safe_op(self, server):
        """checkpoint should call document_ops directly (not via GUI thread)."""
        server.document_ops.checkpoint = MagicMock(return_value="snapshot_ok")
        result = json.loads(
            server._dispatch_view_control({"operation": "checkpoint"})
        )
        assert result["result"] == "snapshot_ok"

    def test_rollback_is_gui_op(self, server):
        """rollback_to_checkpoint should go through async GUI thread."""
        with patch.object(server, '_call_on_gui_thread_async',
                          return_value=json.dumps({"result": "rolled_back"})):
            result = json.loads(
                server._dispatch_view_control({"operation": "rollback_to_checkpoint"})
            )
            assert result["result"] == "rolled_back"

    def test_insert_shape_is_gui_op(self, server):
        with patch.object(server, '_call_on_gui_thread_async',
                          return_value=json.dumps({"result": "inserted"})):
            result = json.loads(
                server._dispatch_view_control({"operation": "insert_shape"})
            )
            assert result["result"] == "inserted"

    def test_clip_plane_ops(self, server):
        for op in ("add_clip_plane", "remove_clip_plane"):
            with patch.object(server, '_run_on_gui_thread',
                              return_value=json.dumps({"result": "ok"})):
                result = json.loads(
                    server._dispatch_view_control({"operation": op})
                )
                assert "error" not in result, f"{op} returned error"

    def test_get_report_view_is_gui_op(self, server):
        with patch.object(server, '_call_on_gui_thread_async',
                          return_value=json.dumps({"result": "report text"})):
            result = json.loads(
                server._dispatch_view_control({"operation": "get_report_view"})
            )
            assert result["result"] == "report text"

    def test_macos_screenshot_bypass(self, server):
        """On macOS, screenshot should bypass _run_on_gui_thread."""
        server.view_ops.take_screenshot = MagicMock(return_value="base64data")
        # _dispatch_view_control does `import platform as _platform` locally
        with patch("freecad_mcp_handler.platform") as mock_plat:
            mock_plat.system.return_value = "Darwin"
            result = json.loads(
                server._dispatch_view_control({"operation": "screenshot"})
            )
            assert result["result"] == "base64data"
            server.view_ops.take_screenshot.assert_called_once()

    def test_macos_screenshot_error(self, server):
        """On macOS, screenshot errors should be caught."""
        server.view_ops.take_screenshot = MagicMock(
            side_effect=RuntimeError("no permission")
        )
        with patch("freecad_mcp_handler.platform") as mock_plat:
            mock_plat.system.return_value = "Darwin"
            result = json.loads(
                server._dispatch_view_control({"operation": "screenshot"})
            )
            assert "no permission" in result["error"]


# ---------------------------------------------------------------------------
# _execute_tool wrapper (crash watcher integration)
# ---------------------------------------------------------------------------

class TestExecuteToolWrapper:
    """Tests for _execute_tool crash watcher wrapping."""

    def test_calls_inner(self, server):
        """Should delegate to _execute_tool_inner."""
        server._execute_tool_inner = MagicMock(
            return_value=json.dumps({"result": "ok"})
        )
        result = server._execute_tool("test_tool", {"arg": 1})
        server._execute_tool_inner.assert_called_once_with("test_tool", {"arg": 1})
        assert json.loads(result)["result"] == "ok"

    def test_crash_watcher_set_and_clear(self, server):
        """Should call _set_current_op before and _clear_current_op after."""
        import freecad_mcp_handler as ss_mod

        calls = []
        original_set = ss_mod._set_current_op
        original_clear = ss_mod._clear_current_op

        ss_mod._set_current_op = lambda t, a: calls.append(("set", t, a))
        ss_mod._clear_current_op = lambda: calls.append(("clear",))

        server._execute_tool_inner = MagicMock(
            return_value=json.dumps({"result": "ok"})
        )
        try:
            server._execute_tool("my_tool", {"x": 1})
            assert calls[0] == ("set", "my_tool", {"x": 1})
            assert calls[1] == ("clear",)
        finally:
            ss_mod._set_current_op = original_set
            ss_mod._clear_current_op = original_clear

    def test_crash_watcher_clears_on_exception(self, server):
        """_clear_current_op should be called even if _execute_tool_inner raises."""
        import freecad_mcp_handler as ss_mod

        cleared = []
        original_clear = ss_mod._clear_current_op
        ss_mod._clear_current_op = lambda: cleared.append(True)

        server._execute_tool_inner = MagicMock(side_effect=RuntimeError("boom"))
        try:
            with pytest.raises(RuntimeError):
                server._execute_tool("bad_tool", {})
            assert len(cleared) == 1
        finally:
            ss_mod._clear_current_op = original_clear


# ---------------------------------------------------------------------------
# _dispatch_to_handler
# ---------------------------------------------------------------------------

class TestDispatchToHandlerExtended:
    """Additional tests for _dispatch_to_handler edge cases."""

    def test_private_method_not_in_registry_rejected(self, server):
        """Operations not in _ALLOWED_OPERATIONS are rejected (including _ prefixed)."""
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"list"})
        result = json.loads(
            server._dispatch_to_handler(handler, {"operation": "_secret"}, "test_tool")
        )
        assert "Unknown test_tool operation: _secret" in result["error"]

    def test_dunder_method_not_in_registry_rejected(self, server):
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"list"})
        result = json.loads(
            server._dispatch_to_handler(handler, {"operation": "__init__"}, "test_tool")
        )
        assert "Unknown test_tool operation: __init__" in result["error"]

    def test_non_callable_in_registry_rejected(self, server):
        """An op in the registry that resolves to a non-callable is rejected."""
        class FakeHandler:
            _ALLOWED_OPERATIONS = frozenset({"some_attr"})
            some_attr = "not a function"

        result = json.loads(
            server._dispatch_to_handler(FakeHandler(), {"operation": "some_attr"}, "test_tool")
        )
        assert "not callable" in result["error"]

    def test_handler_exception_returns_error(self, server):
        """If the handler method raises, should return a formatted error."""
        handler = MagicMock()
        handler._ALLOWED_OPERATIONS = frozenset({"do_thing"})
        handler.do_thing = MagicMock(side_effect=ValueError("bad arg"))

        def run_inline(method, args, label):
            try:
                result = method(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"{label} error: {e}"})

        with patch.object(server, '_call_on_gui_thread_async', side_effect=run_inline):
            result = json.loads(
                server._dispatch_to_handler(handler, {"operation": "do_thing"}, "my_tool")
            )
            assert "bad arg" in result["error"]


# ---------------------------------------------------------------------------
# _execute_tool_inner routing (additional routes)
# ---------------------------------------------------------------------------

class TestExecuteToolInnerRouting:
    """Test routing paths not covered by TestExecuteTool."""

    def test_execute_python_async_routing(self, server):
        server._execute_python_async = MagicMock(
            return_value=json.dumps({"job_id": "x"})
        )
        server._execute_tool_inner("execute_python_async", {"code": "1"})
        server._execute_python_async.assert_called_once()

    def test_poll_job_routing(self, server):
        server._poll_job = MagicMock(
            return_value=json.dumps({"status": "done"})
        )
        server._execute_tool_inner("poll_job", {"job_id": "x"})
        server._poll_job.assert_called_once()

    def test_cancel_job_routing(self, server):
        server._cancel_job = MagicMock(
            return_value=json.dumps({"result": "cancelled"})
        )
        server._execute_tool_inner("cancel_job", {"job_id": "x"})
        server._cancel_job.assert_called_once()

    def test_list_jobs_routing(self, server):
        server._list_jobs = MagicMock(
            return_value=json.dumps({"jobs": {}, "count": 0})
        )
        server._execute_tool_inner("list_jobs", {})
        server._list_jobs.assert_called_once()

    def test_cancel_operation_routing(self, server):
        server._cancel_operation = MagicMock(
            return_value=json.dumps({"result": "ok"})
        )
        server._execute_tool_inner("cancel_operation", {})
        server._cancel_operation.assert_called_once()

    def test_restart_freecad_routing(self, server):
        server.diagnostics_ops.restart_freecad = MagicMock(
            return_value=json.dumps({"result": "restarting"})
        )
        server._execute_tool_inner("restart_freecad", {})
        server.diagnostics_ops.restart_freecad.assert_called_once()

    def test_reload_modules_routing(self, server):
        """reload_modules must route through the GUI-thread wrapper, not call
        _reload_handlers() directly from the socket thread (2026-07-27: doing
        that raced the live Qt event loop and crashed FreeCAD)."""
        server._call_on_gui_thread_reload = MagicMock(
            return_value=json.dumps({"result": {"result": "reloaded"}})
        )
        server._execute_tool_inner("reload_modules", {})
        server._call_on_gui_thread_reload.assert_called_once()

    def test_run_inspector_routing(self, server):
        with patch.object(server, '_call_on_gui_thread',
                          return_value=json.dumps({"result": "ok"})):
            server._execute_tool_inner("run_inspector", {})

    def test_sketch_operations_routing(self, server):
        server._dispatch_sketch = MagicMock(
            return_value=json.dumps({"result": "ok"})
        )
        server._execute_tool_inner("sketch_operations", {"operation": "create_sketch"})
        server._dispatch_sketch.assert_called_once()

    def test_boolean_async_routing(self, server):
        """Boolean ops should use the async path."""
        server._call_on_gui_thread_async = MagicMock(
            return_value=json.dumps({"job_id": "x"})
        )
        for tool in ("fuse_objects", "cut_objects", "common_objects"):
            server._execute_tool_inner(tool, {})
        assert server._call_on_gui_thread_async.call_count == 3

    def test_test_echo_routing(self, server):
        """test_echo is a raw internal liveness probe restart_freecad's
        readiness poll sends over the socket (separate from the client-facing
        MCP tool of the same name, which the bridge answers directly without
        reaching FreeCAD). Any reply with no "error" key proves this instance
        is up and dispatching — pins the restart_freecad-always-times-out fix.
        """
        result = json.loads(server._execute_tool_inner("test_echo", {"message": "ping"}))
        assert "error" not in result
        assert result["echo"] == "ping"


class TestReloadHandlersPreservesState:
    """M4: _reload_handlers() unconditionally builds fresh ViewOpsHandler/
    DocumentOpsHandler instances, which would otherwise silently discard
    _checkpoints/_clip_planes — plain lazily-created instance attributes
    with no persistence anywhere else.

    The `server` fixture's mock_handlers fixture replaces sys.modules
    ['handlers'] with a bare types.ModuleType exposing only mocked classes
    (no real submodules, no __path__) so the rest of this test file doesn't
    need real handler logic — but that means _reload_handlers' own `import
    handlers.base` (its first line) fails immediately with "'handlers' is
    not a package", never reaching the code under test here. Deleting the
    fake stand-in from sys.modules right before the call forces a genuine
    import of the real on-disk handlers package (which does work — FreeCAD
    is already mocked in sys.modules by this point, and handlers/base.py's
    only external dependency is FreeCAD). sys.modules is snapshotted and
    fully restored afterward so this doesn't leak into other tests, mirroring
    what monkeypatch would do automatically if the mutation had gone through
    it — done manually here because the deletion needs to happen mid-test,
    not just at fixture teardown.
    """

    def _drop_fake_handlers_package(self):
        for name in list(sys.modules):
            if name == "handlers" or name.startswith("handlers."):
                del sys.modules[name]

    def test_checkpoints_carried_over_to_new_document_ops_instance(self, server):
        server.document_ops._checkpoints = {"before_reload": ["Box", "Cylinder"]}
        old_document_ops = server.document_ops

        snapshot = dict(sys.modules)
        try:
            self._drop_fake_handlers_package()
            result = json.loads(server._reload_handlers())
        finally:
            sys.modules.clear()
            sys.modules.update(snapshot)

        assert "error" not in result, result
        assert server.document_ops is not old_document_ops, (
            "test is meaningless unless the instance was actually replaced"
        )
        assert server.document_ops._checkpoints == {"before_reload": ["Box", "Cylinder"]}

    def test_clip_planes_carried_over_to_new_view_ops_instance(self, server):
        server.view_ops._clip_planes = [("scenegraph_node", "clip_node")]
        old_view_ops = server.view_ops

        snapshot = dict(sys.modules)
        try:
            self._drop_fake_handlers_package()
            result = json.loads(server._reload_handlers())
        finally:
            sys.modules.clear()
            sys.modules.update(snapshot)

        assert "error" not in result, result
        assert server.view_ops is not old_view_ops, (
            "test is meaningless unless the instance was actually replaced"
        )
        assert server.view_ops._clip_planes == [("scenegraph_node", "clip_node")]

    def test_no_prior_checkpoints_does_not_crash_or_fabricate_state(self, server):
        """A fresh server (no checkpoints ever taken) must not error, and
        the new instance's _checkpoints must not be fabricated out of
        nothing. server.document_ops starts as a MagicMock() from the
        mock_handlers fixture, and MagicMock's getattr(obj, name, default)
        always auto-vivifies rather than honoring `default` the way a real
        object would — so it's swapped for a real DocumentOpsHandler with
        no _checkpoints ever set, to faithfully represent the actual
        pre-reload production state this case is meant to cover."""
        snapshot = dict(sys.modules)
        try:
            self._drop_fake_handlers_package()
            from handlers.document_ops import DocumentOpsHandler
            server.document_ops = DocumentOpsHandler(server)
            assert not hasattr(server.document_ops, "_checkpoints")

            result = json.loads(server._reload_handlers())
        finally:
            sys.modules.clear()
            sys.modules.update(snapshot)

        assert "error" not in result, result
        assert not getattr(server.document_ops, "_checkpoints", None)

    def test_tracebacks_carried_over_to_new_diagnostics_ops_instance(self, server):
        """Same M4-class hazard as _checkpoints/_clip_planes, for the
        traceback ring buffer added when restart_freecad/get_debug_logs/
        get_last_traceback/store_traceback were extracted into
        DiagnosticsOpsHandler: replacing the instance on reload must not
        silently wipe crash history from right before the reload."""
        server.diagnostics_ops._last_tracebacks = collections.deque(
            [{"error_id": "err-0001", "timestamp": 0, "traceback": "boom"}],
            maxlen=20,
        )
        server.diagnostics_ops._traceback_counter = 1
        old_diagnostics_ops = server.diagnostics_ops

        snapshot = dict(sys.modules)
        try:
            self._drop_fake_handlers_package()
            result = json.loads(server._reload_handlers())
        finally:
            sys.modules.clear()
            sys.modules.update(snapshot)

        assert "error" not in result, result
        assert server.diagnostics_ops is not old_diagnostics_ops, (
            "test is meaningless unless the instance was actually replaced"
        )
        assert list(server.diagnostics_ops._last_tracebacks) == [
            {"error_id": "err-0001", "timestamp": 0, "traceback": "boom"}
        ]
        assert server.diagnostics_ops._traceback_counter == 1

    def test_python_namespace_carried_over_to_new_execute_python_ops_instance(self, server):
        """Same M4-class hazard, for execute_python's persistent namespace
        added when _run_python_code/_execute_python were extracted into
        ExecutePythonOpsHandler. Before the extraction this was server
        state untouched by handler re-instantiation, so it silently
        survived reload for free; the move makes it subject to the same
        hazard as checkpoints/clip_planes/tracebacks and needs the same
        fix — a variable set before a hot-reload must still be readable
        by execute_python calls after it."""
        server.execute_python_ops._python_namespace = {"persisted_var": 99}
        old_execute_python_ops = server.execute_python_ops

        snapshot = dict(sys.modules)
        try:
            self._drop_fake_handlers_package()
            result = json.loads(server._reload_handlers())
        finally:
            sys.modules.clear()
            sys.modules.update(snapshot)

        assert "error" not in result, result
        assert server.execute_python_ops is not old_execute_python_ops, (
            "test is meaningless unless the instance was actually replaced"
        )
        assert server.execute_python_ops._python_namespace["persisted_var"] == 99


# ---------------------------------------------------------------------------
# Interactive selection subsystem (UniversalSelector + continue_selection)
#
# Regression coverage: FreeCADSocketServer never set self.selector (the
# UniversalSelector backing class had been deleted as "dead code" while 13
# live call sites in partdesign_ops.py/view_ops.py remained), so every
# interactive fillet/chamfer/draft/shell/thickness call raised AttributeError,
# silently swallowed and reported as a false success. continue_selection was
# also a dead letter with no handler route. Hidden for ~5 months because
# tests/unit/_freecad_mocks.py unconditionally injects
# server.selector = MagicMock(), which the production code never had.
# ---------------------------------------------------------------------------

class TestUniversalSelector:
    """Direct unit tests for UniversalSelector, independent of the mock the
    rest of the suite uses for server.selector."""

    def test_request_selection_returns_awaiting_selection_shape(self, ss_module):
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="fillet_edges", selection_type="edges",
            message="pick edges", object_name="Box", radius=2.0, name="F1",
        )
        assert result["status"] == "awaiting_selection"
        assert result["selection_type"] == "edges"
        assert result["object_name"] == "Box"
        assert "operation_id" in result

    def test_request_selection_stashes_kwargs_for_resume(self, ss_module):
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="fillet_edges", selection_type="edges",
            message="pick edges", object_name="Box", radius=2.0, name="F1",
        )
        pending = selector.pending_operations[result["operation_id"]]
        assert pending["tool"] == "fillet_edges"
        assert pending["radius"] == 2.0
        assert pending["name"] == "F1"

    def test_complete_selection_unknown_id_returns_none(self, ss_module):
        selector = ss_module.UniversalSelector()
        assert selector.complete_selection("nonexistent") is None

    def test_complete_selection_no_gui_returns_error(self, ss_module):
        """Console/headless mode: FreeCADGui is None module-wide (mock_freecad
        sets fc.GuiUp = False)."""
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="fillet_edges", selection_type="edges", message="m", object_name="Box",
        )
        op_id = result["operation_id"]
        completed = selector.complete_selection(op_id)
        assert "error" in completed
        # Popped even on error — a failed completion must not be retryable
        # with stale state; a retry correctly gets "not found" instead.
        assert op_id not in selector.pending_operations

    def test_complete_selection_returns_indices_matching_edge_naming(self, ss_module):
        """elements must be ints that round-trip through f"Edge{idx}" — the
        exact shape fillet_edges/chamfer_edges read via
        selection_result["selection_data"]["elements"]."""
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="fillet_edges", selection_type="edges", message="m", object_name="Box",
        )
        op_id = result["operation_id"]

        fake_sel = MagicMock()
        fake_sel.SubElementNames = ["Edge1", "Edge3"]
        # UniversalSelector now lives in its own module (AICopilot/
        # universal_selector.py) — its methods close over that module's
        # FreeCADGui global, not freecad_mcp_handler's, even though the
        # class is re-exported into ss_module via `from universal_selector
        # import UniversalSelector`.
        import universal_selector
        with patch.object(universal_selector, "FreeCADGui") as mock_gui:
            mock_gui.Selection.getSelectionEx.return_value = [fake_sel]
            completed = selector.complete_selection(op_id)

        assert completed["status"] == "completed"
        assert completed["selection_data"]["elements"] == [1, 3]
        assert completed["context"]["tool"] == "fillet_edges"

    def test_complete_selection_faces_parsed_by_face_prefix(self, ss_module):
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="shell_solid", selection_type="faces", message="m", object_name="Box",
        )
        op_id = result["operation_id"]

        fake_sel = MagicMock()
        fake_sel.SubElementNames = ["Face2"]
        import universal_selector
        with patch.object(universal_selector, "FreeCADGui") as mock_gui:
            mock_gui.Selection.getSelectionEx.return_value = [fake_sel]
            completed = selector.complete_selection(op_id)

        assert completed["selection_data"]["elements"] == [2]

    def test_cancel_selection_removes_pending_op(self, ss_module):
        selector = ss_module.UniversalSelector()
        result = selector.request_selection(
            tool_name="fillet_edges", selection_type="edges", message="m", object_name="Box",
        )
        op_id = result["operation_id"]
        assert selector.cancel_selection(op_id) is True
        assert op_id not in selector.pending_operations
        assert selector.cancel_selection(op_id) is False

    def test_get_selected_objects_headless_returns_empty_list(self, ss_module):
        selector = ss_module.UniversalSelector()
        assert selector.get_selected_objects() == []

    def test_select_object_and_clear_selection_headless_do_not_raise(self, ss_module):
        selector = ss_module.UniversalSelector()
        selector.select_object(MagicMock())
        selector.clear_selection()

    def test_cleanup_old_operations_drops_expired_only(self, ss_module):
        selector = ss_module.UniversalSelector()
        selector.pending_operations["old"] = {
            "tool": "x", "type": "edges", "object": "", "timestamp": 0.0,
        }
        selector.pending_operations["fresh"] = {
            "tool": "x", "type": "edges", "object": "", "timestamp": time.time(),
        }
        selector.cleanup_old_operations(max_age_seconds=300)
        assert "old" not in selector.pending_operations
        assert "fresh" in selector.pending_operations


class TestContinueSelectionDispatch:
    """continue_selection resumes a pending selector.request_selection() by
    re-invoking the originating handler method with _continue_selection=True
    + _operation_id — the mechanism fillet_edges/chamfer_edges/draft_faces/
    shell_solid/thickness_faces already use internally."""

    def test_selector_is_a_real_instance_not_missing(self, server):
        """The core regression: server.selector used to not exist at all."""
        import freecad_mcp_handler as ss_mod
        assert isinstance(server.selector, ss_mod.UniversalSelector)

    def test_missing_operation_id_errors(self, server):
        result = json.loads(server._execute_tool_inner("continue_selection", {}))
        assert "error" in result

    def test_unknown_operation_id_errors(self, server):
        result = json.loads(server._execute_tool_inner(
            "continue_selection", {"operation_id": "does_not_exist"}
        ))
        assert "error" in result
        assert "not found" in result["error"].lower() or "expired" in result["error"].lower()

    def test_unknown_resume_tool_errors(self, server):
        server.selector.pending_operations["op_1"] = {
            "tool": "not_a_real_tool", "type": "edges", "object": "Box", "timestamp": 0.0,
        }
        result = json.loads(server._execute_tool_inner(
            "continue_selection", {"operation_id": "op_1"}
        ))
        assert "error" in result
        assert "not_a_real_tool" in result["error"]

    def test_resumes_fillet_edges_with_reconstructed_args(self, server):
        """A pending fillet_edges selection resumes by re-calling
        partdesign_ops.fillet_edges with the original radius/name plus the
        continuation flags — not a generic/blank re-dispatch."""
        server.selector.pending_operations["op_1"] = {
            "tool": "fillet_edges", "type": "edges", "object": "Box",
            "timestamp": 0.0, "radius": 3.5, "name": "MyFillet",
        }
        server._call_on_gui_thread_async = MagicMock(
            return_value=json.dumps({"job_id": "x", "status": "submitted"})
        )
        server._execute_tool_inner("continue_selection", {"operation_id": "op_1"})

        server._call_on_gui_thread_async.assert_called_once()
        method, resumed_args, label = server._call_on_gui_thread_async.call_args[0]
        assert method == server.partdesign_ops.fillet_edges
        assert label == "fillet_edges"
        assert resumed_args["object_name"] == "Box"
        assert resumed_args["radius"] == 3.5
        assert resumed_args["name"] == "MyFillet"
        assert resumed_args["_continue_selection"] is True
        assert resumed_args["_operation_id"] == "op_1"

    def test_all_five_selection_tools_have_resume_routes(self, server):
        """Every method that calls selector.request_selection() must be
        resumable — a sixth interactive-selection method added later without
        a matching resume_methods entry would silently dead-letter its
        continuation, same as continue_selection did before this fix."""
        for tool_name in (
            "fillet_edges", "chamfer_edges", "draft_faces",
            "shell_solid", "thickness_faces",
        ):
            server.selector.pending_operations["op_x"] = {
                "tool": tool_name, "type": "edges", "object": "Box", "timestamp": 0.0,
            }
            server._call_on_gui_thread_async = MagicMock(
                return_value=json.dumps({"job_id": "x", "status": "submitted"})
            )
            server._execute_tool_inner("continue_selection", {"operation_id": "op_x"})
            method = server._call_on_gui_thread_async.call_args[0][0]
            assert method == getattr(server.partdesign_ops, tool_name)


# ---------------------------------------------------------------------------
# _handle_client
# ---------------------------------------------------------------------------

class TestHandleClient:
    """Tests for _handle_client (socket I/O wrapper)."""

    def test_normal_request(self, server):
        import freecad_mcp_handler as ss_mod
        mock_sock = MagicMock()

        cmd = json.dumps({"tool": "test_echo", "args": {}})
        server._process_command = MagicMock(return_value='{"result":"ok"}')

        with patch.object(ss_mod, 'receive_message', return_value=cmd), \
             patch.object(ss_mod, 'send_message') as mock_send:
            server._handle_client(mock_sock)
            server._process_command.assert_called_once_with(cmd)
            mock_send.assert_called_once_with(mock_sock, '{"result":"ok"}')
        mock_sock.close.assert_called_once()

    def test_empty_message(self, server):
        import freecad_mcp_handler as ss_mod
        mock_sock = MagicMock()

        with patch.object(ss_mod, 'receive_message', return_value=None), \
             patch.object(ss_mod, 'send_message') as mock_send:
            server._handle_client(mock_sock)
            mock_send.assert_not_called()
        mock_sock.close.assert_called_once()

    def test_exception_sends_error(self, server):
        import freecad_mcp_handler as ss_mod
        mock_sock = MagicMock()

        with patch.object(ss_mod, 'receive_message',
                          side_effect=ConnectionError("broken")), \
             patch.object(ss_mod, 'send_message') as mock_send:
            server._handle_client(mock_sock)
            # Should attempt to send error back
            if mock_send.called:
                error_msg = json.loads(mock_send.call_args[0][1])
                assert "error" in error_msg
        mock_sock.close.assert_called_once()

    def test_send_failure_retries_with_fallback_error(self, server):
        """H16: send_message's bool return (False on oversized response or
        socket error) was previously discarded — the client saw only a
        closed connection with no explanation. Must retry once with a
        small, guaranteed-to-fit error payload."""
        import freecad_mcp_handler as ss_mod
        mock_sock = MagicMock()

        cmd = json.dumps({"tool": "test_echo", "args": {}})
        server._process_command = MagicMock(return_value='{"result":"a huge payload"}')

        with patch.object(ss_mod, 'receive_message', return_value=cmd), \
             patch.object(ss_mod, 'send_message', return_value=False) as mock_send:
            server._handle_client(mock_sock)

            assert mock_send.call_count == 2
            fallback_payload = json.loads(mock_send.call_args_list[1][0][1])
            assert "error" in fallback_payload
        mock_sock.close.assert_called_once()

    def test_send_success_does_not_retry(self, server):
        import freecad_mcp_handler as ss_mod
        mock_sock = MagicMock()
        cmd = json.dumps({"tool": "test_echo", "args": {}})
        server._process_command = MagicMock(return_value='{"result":"ok"}')

        with patch.object(ss_mod, 'receive_message', return_value=cmd), \
             patch.object(ss_mod, 'send_message', return_value=True) as mock_send:
            server._handle_client(mock_sock)
            assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# _process_command edge cases
# ---------------------------------------------------------------------------

class TestProcessCommandExtended:
    """Additional _process_command tests for debug/monitor paths."""

    def test_invalid_json_with_debug(self, server):
        import freecad_mcp_handler as ss_mod
        original = ss_mod.DEBUG_ENABLED
        ss_mod.DEBUG_ENABLED = True
        try:
            result = json.loads(server._process_command("not json"))
            assert "Invalid JSON" in result["error"]
        finally:
            ss_mod.DEBUG_ENABLED = original

    def test_exception_with_debug_logging(self, server):
        """When DEBUG_ENABLED and an exception occurs, should log and return error."""
        import freecad_mcp_handler as ss_mod
        original_debug = ss_mod.DEBUG_ENABLED
        original_monitor = ss_mod._monitor
        ss_mod.DEBUG_ENABLED = True
        ss_mod._monitor = MagicMock()

        server._execute_tool = MagicMock(side_effect=RuntimeError("kaboom"))
        try:
            result = json.loads(
                server._process_command(json.dumps({"tool": "test", "args": {}}))
            )
            assert "kaboom" in result["error"]
            ss_mod._monitor.log_crash.assert_called_once()
        finally:
            ss_mod.DEBUG_ENABLED = original_debug
            ss_mod._monitor = original_monitor


# ---------------------------------------------------------------------------
# start_server updates the health monitor's socket_path
#
# init_monitor() runs at module import time, before any instance's real
# socket path exists, so the health monitor always got constructed with its
# hardcoded default ("/tmp/freecad_mcp.sock" — the legacy single-instance
# path). Under the multi-instance architecture, every health check/crash
# snapshot was silently probing the wrong socket whenever a non-default
# instance was active — the normal case.
# ---------------------------------------------------------------------------

class TestStartServerMonitorSocketPath:
    def test_updates_monitor_socket_path_to_the_real_one(self, server, monkeypatch, tmp_path):
        import freecad_mcp_handler as ss_mod

        fake_monitor = MagicMock()
        custom_path = str(tmp_path / "custom.sock")

        monkeypatch.setattr(ss_mod, "_monitor", fake_monitor)
        monkeypatch.setattr(ss_mod, "SOCKET_PATH", custom_path)
        monkeypatch.setattr(ss_mod, "IS_WINDOWS", False)
        # Avoid real socket/OS/thread work — start_server does a real
        # AF_UNIX bind otherwise.
        monkeypatch.setattr(ss_mod.socket, "socket", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(ss_mod.os, "chmod", MagicMock())
        monkeypatch.setattr(ss_mod.os.path, "exists", MagicMock(return_value=False))
        monkeypatch.setattr(ss_mod.threading, "Thread", MagicMock(return_value=MagicMock()))

        server.start_server()

        assert str(fake_monitor.socket_path) == custom_path

    def test_no_monitor_does_not_raise(self, server, monkeypatch, tmp_path):
        """DEBUG_ENABLED False (the common case: freecad_debug/freecad_health
        not installed) means _monitor is None — must not crash trying to
        update it."""
        import freecad_mcp_handler as ss_mod

        monkeypatch.setattr(ss_mod, "_monitor", None)
        monkeypatch.setattr(ss_mod, "SOCKET_PATH", str(tmp_path / "custom.sock"))
        monkeypatch.setattr(ss_mod, "IS_WINDOWS", False)
        monkeypatch.setattr(ss_mod.socket, "socket", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(ss_mod.os, "chmod", MagicMock())
        monkeypatch.setattr(ss_mod.os.path, "exists", MagicMock(return_value=False))
        monkeypatch.setattr(ss_mod.threading, "Thread", MagicMock(return_value=MagicMock()))

        server.start_server()  # must not raise (the assertion is reaching this line)
        assert server.running is True

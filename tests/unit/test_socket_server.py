"""
Tests for AICopilot/socket_server.py — the FreeCAD-side MCP server core.

FreeCAD is mocked via conftest.py fixtures.
"""

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
    """Mock out the handler imports so socket_server.py can load."""
    handler_classes = [
        "PrimitivesHandler", "BooleanOpsHandler", "TransformsHandler",
        "SketchOpsHandler", "PartDesignOpsHandler", "PartOpsHandler",
        "CAMOpsHandler", "CAMToolsHandler", "CAMToolControllersHandler",
        "DraftOpsHandler", "ViewOpsHandler", "DocumentOpsHandler",
        "MeasurementOpsHandler", "SpreadsheetOpsHandler", "MeshOpsHandler",
    ]

    handlers_mod = types.ModuleType("handlers")
    for cls_name in handler_classes:
        mock_cls = MagicMock()
        mock_cls.return_value = MagicMock()
        setattr(handlers_mod, cls_name, mock_cls)

    monkeypatch.setitem(sys.modules, "handlers", handlers_mod)

    # Make optional modules raise ImportError so socket_server takes fallback paths.
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
    if "socket_server" in sys.modules:
        del sys.modules["socket_server"]

    import socket_server as ss_mod
    server = ss_mod.FreeCADSocketServer()
    return server


@pytest.fixture
def ss_module(mock_freecad, mock_handlers):
    """Import the socket_server module with mocks in place."""
    if "socket_server" in sys.modules:
        del sys.modules["socket_server"]

    import socket_server as ss_mod
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


# ---------------------------------------------------------------------------
# _run_on_gui_thread
# ---------------------------------------------------------------------------

class TestRunOnGuiThread:
    def test_success_result(self, server):
        """A task returning {success: True, result: X} should produce {"result": X}."""
        def fake_task():
            return {"success": True, "result": "Box created"}

        # Simulate the GUI timer processing the task
        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "Box created"

    def test_error_result(self, server):
        """A task returning {error: X} should produce {"error": X}."""
        def fake_task():
            return {"error": "Something broke"}

        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert "Something broke" in parsed["error"]

    def test_timeout(self, server):
        """If no one processes the task, should return timeout error."""
        def fake_task():
            return {"success": True, "result": "never happens"}

        response = server._run_on_gui_thread(fake_task, timeout=0.1)
        parsed = json.loads(response)
        assert "timeout" in parsed["error"].lower()

    def test_non_dict_result(self, server):
        """A task returning a plain value should be stringified."""
        def fake_task():
            return 42

        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        response = server._run_on_gui_thread(fake_task)
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "42"


# ---------------------------------------------------------------------------
# _process_gui_tasks
# ---------------------------------------------------------------------------

class TestProcessGuiTasks:
    def test_processes_queued_task(self, server):
        """_process_gui_tasks should drain the queue and put results."""
        server._gui_task_queue.put(lambda: {"success": True, "result": "done"})
        server._process_gui_tasks()
        result = server._gui_response_queue.get_nowait()
        assert result == {"success": True, "result": "done"}

    def test_handles_task_exception(self, server):
        """If a task raises, it should put an error dict instead of crashing."""
        def bad_task():
            raise ValueError("boom")

        server._gui_task_queue.put(bad_task)
        server._process_gui_tasks()
        result = server._gui_response_queue.get_nowait()
        assert "error" in result
        assert "boom" in result["error"]

    def test_processes_multiple_tasks(self, server):
        """Should drain all tasks in one call."""
        for i in range(3):
            server._gui_task_queue.put(lambda i=i: {"success": True, "result": f"task_{i}"})

        server._process_gui_tasks()

        results = []
        while not server._gui_response_queue.empty():
            results.append(server._gui_response_queue.get_nowait())
        assert len(results) == 3


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


# ---------------------------------------------------------------------------
# _execute_tool routing
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def test_direct_map_routing(self, server):
        """Tools in the direct_map should call the handler via _call_on_gui_thread."""
        server._call_on_gui_thread = MagicMock(return_value=json.dumps({"result": "box"}))
        result = server._execute_tool("create_box", {"length": 10})
        server._call_on_gui_thread.assert_called_once()
        # First arg to _call_on_gui_thread should be the handler method
        call_args = server._call_on_gui_thread.call_args
        assert call_args[0][1] == {"length": 10}  # args passed through
        assert call_args[0][2] == "create_box"  # label

    def test_generic_dispatch_routing(self, server):
        """Tools in generic_dispatch_map should use _dispatch_to_handler."""
        server._dispatch_to_handler = MagicMock(return_value=json.dumps({"result": "ok"}))
        for tool in ["cam_operations", "cam_tools", "cam_tool_controllers",
                      "draft_operations", "spreadsheet_operations"]:
            server._execute_tool(tool, {"operation": "test"})

        assert server._dispatch_to_handler.call_count == 5

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
        server._execute_python = MagicMock(return_value=json.dumps({"result": "2"}))
        server._execute_tool("execute_python", {"code": "1+1"})
        server._execute_python.assert_called_once_with({"code": "1+1"})

    def test_get_debug_logs_routing(self, server):
        server._get_debug_logs = MagicMock(return_value=json.dumps({"result": "logs"}))
        server._execute_tool("get_debug_logs", {"count": 10})
        server._get_debug_logs.assert_called_once_with({"count": 10})

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
        """Should look up the operation as a method and call it on GUI thread."""
        handler = MagicMock()
        handler.create_spreadsheet = MagicMock(return_value="Spreadsheet created")

        # Simulate GUI processing
        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        result = server._dispatch_to_handler(
            handler, {"operation": "create_spreadsheet"}, "spreadsheet_operations"
        )
        t.join()
        parsed = json.loads(result)
        assert parsed["result"] == "Spreadsheet created"

    def test_unknown_operation(self, server):
        handler = MagicMock(spec=[])  # No attributes
        result = server._dispatch_to_handler(
            handler, {"operation": "nonexistent"}, "test_tool"
        )
        parsed = json.loads(result)
        assert "Unknown test_tool operation: nonexistent" in parsed["error"]

    def test_missing_operation(self, server):
        handler = MagicMock(spec=[])
        result = server._dispatch_to_handler(handler, {}, "test_tool")
        parsed = json.loads(result)
        assert "Unknown test_tool operation" in parsed["error"]


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
        """All mapped view_control operations should route."""
        ops = ["screenshot", "set_view", "fit_all", "zoom_in", "zoom_out",
               "create_document", "save_document", "list_objects",
               "select_object", "clear_selection", "get_selection"]
        for op in ops:
            # view_control calls handler methods directly (they manage own GUI safety)
            handler_method = MagicMock(return_value="ok")

            # Patch the operation map lookup
            with patch.object(server, 'view_ops') as mock_view, \
                 patch.object(server, 'document_ops') as mock_doc:
                mock_view.take_screenshot = handler_method
                mock_view.set_view = handler_method
                mock_view.fit_all = handler_method
                mock_view.zoom_in = handler_method
                mock_view.zoom_out = handler_method
                mock_view.select_object = handler_method
                mock_view.clear_selection = handler_method
                mock_view.get_selection = handler_method
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
        result = server._dispatch_view_control({"operation": "screenshot"})
        parsed = json.loads(result)
        assert "screenshot failed" in parsed["error"]


# ---------------------------------------------------------------------------
# _execute_python
# ---------------------------------------------------------------------------

class TestExecutePython:
    def _run_python(self, server, code):
        """Helper: run _execute_python with simulated GUI thread processing."""
        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=2)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()
        response = server._execute_python({"code": code})
        t.join()
        return json.loads(response)

    def test_expression_evaluation(self, server):
        result = self._run_python(server, "1 + 1")
        assert result["result"] == "2"

    def test_string_expression(self, server):
        result = self._run_python(server, "'hello' + ' world'")
        assert result["result"] == "'hello world'"

    def test_statement_then_expression(self, server):
        result = self._run_python(server, "x = 5\nx * 2")
        assert result["result"] == "10"

    def test_pure_statement(self, server):
        result = self._run_python(server, "x = 42")
        assert result["result"] == "Code executed successfully"

    def test_result_variable(self, server):
        """If code sets 'result' variable, that should be returned."""
        result = self._run_python(server, "result = 42")
        assert result["result"] == "42"

    def test_syntax_error(self, server):
        result = self._run_python(server, "def (broken")
        assert "error" in result
        assert "SyntaxError" in result["error"]

    def test_runtime_error(self, server):
        result = self._run_python(server, "1 / 0")
        assert "error" in result
        assert "ZeroDivisionError" in result["error"] or "execution error" in result["error"]

    def test_empty_code(self, server):
        # Empty code doesn't go through GUI thread — returns immediately
        result = server._execute_python({"code": ""})
        parsed = json.loads(result)
        assert "No code provided" in parsed["error"]

    def test_no_code_key(self, server):
        result = server._execute_python({})
        parsed = json.loads(result)
        assert "No code provided" in parsed["error"]

    def test_multiline_code(self, server):
        code = "a = 10\nb = 20\na + b"
        result = self._run_python(server, code)
        assert result["result"] == "30"

    def test_list_comprehension(self, server):
        result = self._run_python(server, "[i**2 for i in range(5)]")
        assert result["result"] == "[0, 1, 4, 9, 16]"

    def test_namespace_has_freecad(self, server):
        """FreeCAD should be available in the execution namespace."""
        result = self._run_python(server, "type(FreeCAD).__name__")
        assert "error" not in result


# ---------------------------------------------------------------------------
# _call_on_gui_thread
# ---------------------------------------------------------------------------

class TestCallOnGuiThread:
    def test_wraps_handler_success(self, server):
        """Should wrap handler result in {success: True, result: ...}."""
        handler_method = MagicMock(return_value="created")

        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        response = server._call_on_gui_thread(handler_method, {"x": 1}, "test")
        t.join()
        parsed = json.loads(response)
        assert parsed["result"] == "created"
        handler_method.assert_called_once_with({"x": 1})

    def test_wraps_handler_exception(self, server):
        """If handler raises, should return error with traceback."""
        handler_method = MagicMock(side_effect=ValueError("bad value"))

        def process():
            time.sleep(0.05)
            task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put(result)

        t = threading.Thread(target=process)
        t.start()

        response = server._call_on_gui_thread(handler_method, {}, "test")
        t.join()
        parsed = json.loads(response)
        assert "bad value" in parsed["error"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_default_socket_path(self, ss_module):
        assert ss_module.SOCKET_PATH == "/tmp/freecad_mcp.sock"

    def test_default_windows_port(self, ss_module):
        assert ss_module.WINDOWS_PORT == 23456

    def test_version(self, ss_module):
        assert ss_module.__version__ == "5.1.0"

    def test_max_message_size(self, ss_module):
        assert ss_module.MAX_MESSAGE_SIZE == 50 * 1024

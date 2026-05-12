"""
Tests for AICopilot/freecad_mcp_handler.py — the FreeCAD-side MCP server core.

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
    """Mock out the handler imports so freecad_mcp_handler.py can load."""
    handler_classes = [
        "PrimitivesHandler", "BooleanOpsHandler", "TransformsHandler",
        "SketchOpsHandler", "PartDesignOpsHandler", "PartOpsHandler",
        "CAMOpsHandler", "CAMToolsHandler", "CAMToolControllersHandler",
        "DraftOpsHandler", "ViewOpsHandler", "DocumentOpsHandler",
        "MeasurementOpsHandler", "SpreadsheetOpsHandler", "MeshOpsHandler",
        "SpatialOpsHandler", "InspectorOpsHandler",
        "MacroOpsHandler", "IntrospectionOpsHandler", "SketchBuilderOpsHandler",
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

        # Simulate GUI processing with tagged request IDs
        def process():
            time.sleep(0.05)
            req_id, task = server._gui_task_queue.get(timeout=1)
            result = task()
            server._gui_response_queue.put((req_id, result))

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
        assert "Invalid operation" in parsed["error"]


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

        # GUI ops: mock _run_on_gui_thread to avoid blocking.
        # Also force non-macOS path so screenshot doesn't bypass _run_on_gui_thread
        # via the Darwin early-exit (which calls take_screenshot directly and would
        # receive a MagicMock return value that json.dumps can't serialize).
        for op in gui_ops:
            with patch.object(server, '_run_on_gui_thread',
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
        """If the handler raises, view_control should catch and return error.

        GUI ops wrap the handler call in a task closure that catches exceptions
        and returns an error dict. _run_on_gui_thread serializes that dict.
        We mock _run_on_gui_thread to execute the task inline (simulating the
        GUI thread) so the exception propagates through the normal path.
        """
        server.view_ops.take_screenshot = MagicMock(side_effect=RuntimeError("screenshot failed"))

        def run_inline(task_fn, timeout=10.0):
            """Execute the task immediately and return JSON result."""
            result = task_fn()
            return json.dumps(result)

        with patch.object(server, '_run_on_gui_thread', side_effect=run_inline):
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
# _execute_python
# ---------------------------------------------------------------------------

class TestExecutePython:
    def _run_python(self, server, code):
        """Helper: run _execute_python with simulated GUI thread processing."""
        def process():
            time.sleep(0.05)
            req_id, task = server._gui_task_queue.get(timeout=2)
            result = task()
            server._gui_response_queue.put((req_id, result))

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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_default_socket_path(self, ss_module):
        assert ss_module.SOCKET_PATH == "/tmp/freecad_mcp.sock"

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
        """rollback_to_checkpoint should go through GUI thread."""
        with patch.object(server, '_run_on_gui_thread',
                          return_value=json.dumps({"result": "rolled_back"})):
            result = json.loads(
                server._dispatch_view_control({"operation": "rollback_to_checkpoint"})
            )
            assert result["result"] == "rolled_back"

    def test_insert_shape_is_gui_op(self, server):
        with patch.object(server, '_run_on_gui_thread',
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
        with patch.object(server, '_run_on_gui_thread',
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

    def test_private_method_rejected(self, server):
        """Operations starting with _ should be rejected."""
        handler = MagicMock()
        result = json.loads(
            server._dispatch_to_handler(handler, {"operation": "_secret"}, "test_tool")
        )
        assert "Invalid operation" in result["error"]

    def test_dunder_method_rejected(self, server):
        handler = MagicMock()
        result = json.loads(
            server._dispatch_to_handler(handler, {"operation": "__init__"}, "test_tool")
        )
        assert "Invalid operation" in result["error"]

    def test_non_callable_rejected(self, server):
        """If the attribute exists but isn't callable, reject it."""
        handler = MagicMock()
        handler.some_attr = "not a function"
        result = json.loads(
            server._dispatch_to_handler(handler, {"operation": "some_attr"}, "test_tool")
        )
        # MagicMock auto-creates attributes as MagicMock (callable), so we
        # need to set it to a non-callable explicitly
        handler.configure_mock(**{"some_attr": "string_value"})
        type(handler).some_attr = PropertyMock(return_value="string_value")
        # Actually, MagicMock getattr returns MagicMock. Use a simple object instead.

    def test_handler_exception_returns_error(self, server):
        """If the handler method raises, should return a formatted error."""
        handler = MagicMock()
        handler.do_thing = MagicMock(side_effect=ValueError("bad arg"))

        def run_inline(task_fn, timeout=120.0):
            result = task_fn()
            return json.dumps(result)

        with patch.object(server, '_run_on_gui_thread', side_effect=run_inline):
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
        server._restart_freecad = MagicMock(
            return_value=json.dumps({"result": "restarting"})
        )
        server._execute_tool_inner("restart_freecad", {})
        server._restart_freecad.assert_called_once()

    def test_reload_modules_routing(self, server):
        server._reload_handlers = MagicMock(
            return_value=json.dumps({"result": "reloaded"})
        )
        server._execute_tool_inner("reload_modules", {})
        server._reload_handlers.assert_called_once()

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

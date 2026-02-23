# FreeCAD Socket Server for MCP Communication
# Runs inside FreeCAD to receive commands from external MCP bridge
#
# Version: 5.0.0 - Core rewrite: eliminated dead code, unified dispatch,
#                   replaced busy-wait polling with Queue.get(timeout)

__version__ = "5.3.0"
REQUIRED_VERSIONS = {
    "freecad_debug": ">=1.1.0",
    "freecad_health": ">=1.0.1",
}

import FreeCAD
import socket
import threading
import json
import os
import time
import queue
import uuid
import platform
import struct
import sys
import traceback as tb_module
from typing import Dict, Any, Optional

# Conditional GUI imports (not available in console mode)
if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtCore
else:
    FreeCADGui = None
    QtCore = None

IS_WINDOWS = platform.system() == "Windows"

# Configurable socket path/port via environment variables
SOCKET_PATH = os.environ.get("FREECAD_MCP_SOCKET", "/tmp/freecad_mcp.sock")
WINDOWS_HOST = "localhost"
WINDOWS_PORT = int(os.environ.get("FREECAD_MCP_PORT", "23456"))

# =============================================================================
# MCP Debug Infrastructure (Optional)
# =============================================================================
DEBUG_ENABLED = False
_debugger = None
_monitor = None


def _log_operation(operation, parameters=None, result=None, error=None, duration=None):
    """No-op fallback if debug not enabled"""
    pass


def _capture_state():
    """No-op fallback if debug not enabled"""
    return {}


try:
    from freecad_debug import (
        init_debugger,
        log_operation as _log_op_impl,
        capture_state as _capture_state_impl,
        get_debugger
    )
    from freecad_health import init_monitor, get_monitor

    _debugger = init_debugger(
        log_dir="/tmp/freecad_mcp_debug",
        enable_console=False,
        enable_file=True,
        lean_logging=False,
    )
    _monitor = init_monitor()

    _log_operation = _log_op_impl
    _capture_state = _capture_state_impl

    DEBUG_ENABLED = True
    FreeCAD.Console.PrintMessage("MCP Debug infrastructure loaded\n")
    FreeCAD.Console.PrintMessage("  Logs: /tmp/freecad_mcp_debug/\n")
    FreeCAD.Console.PrintMessage("  Crashes: /tmp/freecad_mcp_crashes/\n")

except ImportError as e:
    FreeCAD.Console.PrintMessage(f"MCP Debug not available (optional): {e}\n")

except Exception as e:
    FreeCAD.Console.PrintError(f"MCP Debug modules broken: {e}\n")
    FreeCAD.Console.PrintError("  Fix or remove freecad_debug.py/freecad_health.py\n")
    sys.exit(1)

# =============================================================================
# Version Validation
# =============================================================================
try:
    from mcp_versions import (
        register_component,
        declare_requirements,
        validate_all,
        get_status,
    )
    register_component("socket_server", __version__)
    declare_requirements("socket_server", REQUIRED_VERSIONS)
    valid, error = validate_all()
    if not valid:
        FreeCAD.Console.PrintError(f"Version validation failed: {error}\n")
        FreeCAD.Console.PrintError("Component status:\n")
        FreeCAD.Console.PrintError(json.dumps(get_status(), indent=2) + "\n")
        sys.exit(1)
    FreeCAD.Console.PrintMessage(f"socket_server v{__version__} validated\n")
except ImportError as e:
    FreeCAD.Console.PrintWarning(f"Version system not available (optional): {e}\n")

# =============================================================================
# Modular Handlers
# =============================================================================
try:
    from handlers import (
        PrimitivesHandler,
        BooleanOpsHandler,
        TransformsHandler,
        SketchOpsHandler,
        PartDesignOpsHandler,
        PartOpsHandler,
        CAMOpsHandler,
        CAMToolsHandler,
        CAMToolControllersHandler,
        DraftOpsHandler,
        ViewOpsHandler,
        DocumentOpsHandler,
        MeasurementOpsHandler,
        SpreadsheetOpsHandler,
        MeshOpsHandler,
    )
    FreeCAD.Console.PrintMessage("Modular handlers loaded successfully\n")
except ImportError as e:
    FreeCAD.Console.PrintError(f"Modular handlers required but not available: {e}\n")
    sys.exit(1)


# =============================================================================
# Message Framing Protocol (v2.1.1)
# =============================================================================
# Length-prefixed protocol: [4-byte big-endian length][JSON message]
# Keep in sync with mcp_bridge_framing.py on the bridge side.

MAX_MESSAGE_SIZE = 50 * 1024  # 50KB — matches bridge-side limit


def send_message(sock: socket.socket, message_str: str) -> bool:
    """Send a length-prefixed message over the socket."""
    try:
        message_bytes = message_str.encode('utf-8')
        length_prefix = struct.pack('>I', len(message_bytes))
        sock.sendall(length_prefix + message_bytes)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        FreeCAD.Console.PrintWarning(f"Socket send error: {e}\n")
        return False


def receive_message(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive a length-prefixed message from the socket."""
    try:
        old_timeout = sock.gettimeout()
        sock.settimeout(timeout)

        length_bytes = _recv_exact(sock, 4)
        if not length_bytes:
            sock.settimeout(old_timeout)
            return None

        message_len = struct.unpack('>I', length_bytes)[0]

        if message_len > MAX_MESSAGE_SIZE:
            FreeCAD.Console.PrintError(
                f"Message too large: {message_len} bytes (limit: {MAX_MESSAGE_SIZE})\n"
            )
            sock.settimeout(old_timeout)
            return None

        message_bytes = _recv_exact(sock, message_len)
        sock.settimeout(old_timeout)
        if not message_bytes:
            return None

        return message_bytes.decode('utf-8')

    except socket.timeout:
        FreeCAD.Console.PrintWarning("Socket receive timeout\n")
        return None
    except UnicodeDecodeError as e:
        FreeCAD.Console.PrintError(f"Message decode error: {e}\n")
        return None
    except Exception as e:
        FreeCAD.Console.PrintError(f"Receive error: {e}\n")
        return None


def _recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Receive exactly num_bytes, handling partial reads."""
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(min(num_bytes - len(buf), 65536))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# =============================================================================
# FreeCAD Socket Server
# =============================================================================
class FreeCADSocketServer:
    """Socket server for FreeCAD MCP communication with modular handler architecture."""

    def __init__(self):
        self.running = False
        self.server_socket = None
        self.server_thread = None

        # GUI thread task queues (used by handlers that need Qt main thread)
        # Tasks are (request_id, callable) tuples; responses are (request_id, result).
        self._gui_task_queue = queue.Queue()
        self._gui_response_queue = queue.Queue()
        self._request_counter = 0  # monotonic request ID

        # Async job tracking: job_id -> {status, started, result, error, elapsed}
        self._async_jobs: Dict[str, Dict] = {}

        # Persistent namespace for execute_python calls.
        # Variables created in one call survive to the next.
        self._python_namespace: Dict[str, Any] = {}

        # Initialize handlers
        self.primitives = PrimitivesHandler(self, _log_operation, _capture_state)
        self.boolean_ops = BooleanOpsHandler(self, _log_operation, _capture_state)
        self.transforms = TransformsHandler(self, _log_operation, _capture_state)
        self.sketch_ops = SketchOpsHandler(self, _log_operation, _capture_state)
        self.partdesign_ops = PartDesignOpsHandler(self, _log_operation, _capture_state)
        self.part_ops = PartOpsHandler(self, _log_operation, _capture_state)
        self.cam_ops = CAMOpsHandler(self, _log_operation, _capture_state)
        self.cam_tools = CAMToolsHandler(self, _log_operation, _capture_state)
        self.cam_tool_controllers = CAMToolControllersHandler(self, _log_operation, _capture_state)
        self.draft_ops = DraftOpsHandler(self, _log_operation, _capture_state)
        self.measurement_ops = MeasurementOpsHandler(self, _log_operation, _capture_state)
        self.spreadsheet_ops = SpreadsheetOpsHandler(self, _log_operation, _capture_state)
        self.mesh_ops = MeshOpsHandler(self, _log_operation, _capture_state)
        # GUI-sensitive handlers get the task queues for thread safety
        self.view_ops = ViewOpsHandler(
            self, self._gui_task_queue, self._gui_response_queue, _log_operation, _capture_state
        )
        self.document_ops = DocumentOpsHandler(
            self, self._gui_task_queue, self._gui_response_queue, _log_operation, _capture_state
        )

        FreeCAD.Console.PrintMessage("Socket server initialized with modular handlers\n")

    # -----------------------------------------------------------------
    # Server lifecycle
    # -----------------------------------------------------------------

    def start_server(self):
        """Start the socket server."""
        try:
            if IS_WINDOWS:
                self.socket_path = None
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind((WINDOWS_HOST, WINDOWS_PORT))
                FreeCAD.Console.PrintMessage(
                    f"Socket server started on {WINDOWS_HOST}:{WINDOWS_PORT} (Windows TCP)\n"
                )
            else:
                self.socket_path = SOCKET_PATH
                if os.path.exists(self.socket_path):
                    os.remove(self.socket_path)
                self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.server_socket.bind(self.socket_path)
                os.chmod(self.socket_path, 0o666)
                FreeCAD.Console.PrintMessage(f"Socket server started on {self.socket_path}\n")

            self.server_socket.listen(5)
            self.running = True

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()

            # Start GUI task processing on the Qt main thread
            if QtCore:
                QtCore.QTimer.singleShot(100, self._process_gui_tasks)

            return True

        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to start socket server: {e}\n")
            return False

    def stop_server(self):
        """Stop the socket server."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        if not IS_WINDOWS and hasattr(self, 'socket_path') and self.socket_path:
            try:
                if os.path.exists(self.socket_path):
                    os.remove(self.socket_path)
            except Exception:
                pass
        FreeCAD.Console.PrintMessage("Socket server stopped\n")

    # -----------------------------------------------------------------
    # GUI thread task processing
    # -----------------------------------------------------------------

    def _process_gui_tasks(self):
        """Process queued tasks on the Qt main thread (called by QTimer)."""
        while not self._gui_task_queue.empty():
            try:
                req_id, task = self._gui_task_queue.get_nowait()
                result = task()
                self._gui_response_queue.put((req_id, result))
            except queue.Empty:
                break
            except Exception as e:
                self._gui_response_queue.put((req_id, {"error": f"GUI task error: {e}"}))

        if QtCore:
            QtCore.QTimer.singleShot(100, self._process_gui_tasks)

    def _run_on_gui_thread(self, task_fn, timeout=30.0) -> str:
        """Run a callable on the Qt GUI thread and wait for the result.

        This is the single entry point for all GUI-safe execution.
        Uses request IDs to prevent stale responses from previous
        timed-out calls from being confused with the current response.
        """
        self._request_counter += 1
        req_id = self._request_counter
        self._gui_task_queue.put((req_id, task_fn))

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                FreeCAD.Console.PrintWarning(
                    f"[MCP] Request {req_id} timed out after {timeout:.0f}s "
                    f"- GUI thread may still be busy\n"
                )
                return json.dumps({
                    "error": f"Operation timeout ({timeout:.0f}s) - "
                             f"GUI thread may be busy. Use execute_python_async "
                             f"for long operations."
                })
            try:
                resp_id, result = self._gui_response_queue.get(timeout=remaining)
            except queue.Empty:
                continue  # recalculate remaining and check deadline

            if resp_id != req_id:
                # Stale response from a previously timed-out request -- discard
                FreeCAD.Console.PrintWarning(
                    f"[MCP] Discarded stale response for request {resp_id} "
                    f"(waiting for {req_id})\n"
                )
                continue

            # Matched -- process result
            if isinstance(result, dict):
                if "error" in result:
                    return json.dumps({"error": result["error"]})
                if "success" in result:
                    return json.dumps({"result": result["result"]})
            return json.dumps({"result": str(result)})

    def _run_on_gui_thread_async(self, job_id: str, task_fn) -> None:
        """Schedule task_fn on the Qt GUI thread without blocking the caller.

        The result is stored in self._async_jobs[job_id] when complete.
        QTimer.singleShot is thread-safe and always fires on the Qt main thread.
        """
        def wrapper():
            try:
                result = task_fn()
                self._async_jobs[job_id].update({
                    "status": "done",
                    "result": result,
                    "elapsed": time.time() - self._async_jobs[job_id]["started"],
                })
            except Exception as e:
                self._async_jobs[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "traceback": tb_module.format_exc(),
                    "elapsed": time.time() - self._async_jobs[job_id]["started"],
                })
            FreeCAD.Console.PrintMessage(
                f"[MCP] async job {job_id} finished: {self._async_jobs[job_id]['status']}\n"
            )

        if QtCore:
            QtCore.QTimer.singleShot(0, wrapper)
        else:
            # Fallback: run synchronously (console mode)
            wrapper()

    def _execute_python_async(self, args: Dict[str, Any]) -> str:
        """Submit Python code for async GUI-safe execution; returns job_id immediately.

        Use poll_job(job_id) to check status and retrieve the result.
        Identical execution semantics to execute_python.
        """
        code = args.get("code", "")
        if not code:
            return json.dumps({"error": "No code provided"})

        job_id = uuid.uuid4().hex[:8]
        self._async_jobs[job_id] = {
            "status": "running",
            "started": time.time(),
            "tool": "execute_python_async",
        }

        self._run_on_gui_thread_async(job_id, lambda: self._run_python_code(code))
        return json.dumps({"job_id": job_id, "status": "submitted"})

    def _poll_job(self, args: Dict[str, Any]) -> str:
        """Poll status and result of an async job.

        Returns:
          {"status": "running", "elapsed_s": N}          — still computing
          {"status": "done",    "elapsed_s": N, "result": ...}  — finished
          {"status": "error",   "elapsed_s": N, "error": ...}   — failed
        Completed jobs are removed from tracking after retrieval.
        """
        job_id = args.get("job_id", "")
        if not job_id:
            return json.dumps({"error": "job_id required"})
        if job_id not in self._async_jobs:
            return json.dumps({"error": f"Unknown job_id: {job_id!r}. Already retrieved or never submitted."})

        job = self._async_jobs[job_id]
        elapsed = round(time.time() - job["started"], 1)

        if job["status"] == "running":
            return json.dumps({"status": "running", "elapsed_s": elapsed})

        # Done or error — retrieve and clean up
        del self._async_jobs[job_id]

        if job["status"] == "done":
            task_result = job.get("result", {})
            if isinstance(task_result, dict) and "error" in task_result:
                return json.dumps({
                    "status": "error",
                    "error": task_result["error"],
                    "elapsed_s": round(job.get("elapsed", elapsed), 1),
                })
            result_val = task_result.get("result", "done") if isinstance(task_result, dict) else str(task_result)
            return json.dumps({
                "status": "done",
                "result": result_val,
                "elapsed_s": round(job.get("elapsed", elapsed), 1),
            })
        else:
            return json.dumps({
                "status": "error",
                "error": job.get("error", "unknown error"),
                "elapsed_s": round(job.get("elapsed", elapsed), 1),
            })

    def _list_jobs(self, args: Dict[str, Any]) -> str:
        """List all tracked async jobs and their current status."""
        now = time.time()
        jobs = {
            jid: {
                "status": j["status"],
                "elapsed_s": round(now - j["started"], 1),
                "tool": j.get("tool", "?"),
            }
            for jid, j in self._async_jobs.items()
        }
        return json.dumps({"jobs": jobs, "count": len(jobs)})

    # -----------------------------------------------------------------
    # cancel_operation
    # -----------------------------------------------------------------

    def _cancel_operation(self, args: Dict[str, Any]) -> str:
        """Request cancellation of the current long-running FreeCAD operation.

        Calls FreeCADGui.cancelOperation() which sets Base::OperationCancel::requested.
        The flag is checked within ≤200 ms by any cancellable operation
        (Thickness, boolean, Check Geometry, …).

        Safe to call from the socket thread while the GUI thread is blocked —
        the atomic store is lock-free and immediately visible to the GUI thread.
        """
        try:
            import FreeCADGui as Gui
            Gui.cancelOperation()
            return json.dumps({"result": "Cancel requested — operation will stop within 200 ms"})
        except Exception as e:
            return json.dumps({"error": f"cancelOperation failed: {e}"})

    # -----------------------------------------------------------------
    # Connection handling
    # -----------------------------------------------------------------

    def _server_loop(self):
        """Accept connections in a loop."""
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                try:
                    client_socket, _ = self.server_socket.accept()
                    threading.Thread(
                        target=self._handle_client,
                        args=(client_socket,),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
            except Exception as e:
                if self.running:
                    FreeCAD.Console.PrintError(f"Server loop error: {e}\n")

    def _handle_client(self, client_socket):
        """Handle a single client connection."""
        try:
            message_str = receive_message(client_socket)
            if message_str:
                response = self._process_command(message_str)
                send_message(client_socket, response)
        except Exception as e:
            FreeCAD.Console.PrintError(f"Client handler error: {e}\n")
            if DEBUG_ENABLED:
                _log_operation(
                    operation="CLIENT_ERROR",
                    error=e,
                    parameters={"traceback": tb_module.format_exc()},
                )
            try:
                send_message(client_socket, json.dumps({"error": f"Server error: {e}"}))
            except Exception:
                pass
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Command processing
    # -----------------------------------------------------------------

    def _process_command(self, command_str: str) -> str:
        """Parse and dispatch an incoming command."""
        start_time = time.time()
        tool_name = "unknown"

        try:
            command = json.loads(command_str)
            tool_name = command.get("tool", "")
            args = command.get("args", {})

            if not tool_name:
                return json.dumps({"error": "No tool specified"})

            if DEBUG_ENABLED:
                _capture_state()
                _log_operation(
                    operation="COMMAND_START",
                    parameters={"tool": tool_name, "args": args},
                )

            result = self._execute_tool(tool_name, args)

            if DEBUG_ENABLED:
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_SUCCESS",
                    parameters={"tool": tool_name},
                    result=result[:200] if len(result) > 200 else result,
                    duration=duration,
                )

            return result

        except json.JSONDecodeError as e:
            if DEBUG_ENABLED:
                _log_operation(
                    operation="JSON_PARSE_ERROR",
                    error=e,
                    parameters={"command_preview": command_str[:500]},
                )
            return json.dumps({"error": f"Invalid JSON: {e}"})
        except Exception as e:
            if DEBUG_ENABLED:
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_ERROR",
                    error=e,
                    parameters={
                        "tool": tool_name,
                        "traceback": tb_module.format_exc(),
                    },
                    duration=duration,
                )
                if _monitor:
                    try:
                        _monitor.log_crash(
                            health_status={"tool": tool_name, "error": str(e)},
                            error_context=tb_module.format_exc(),
                        )
                    except Exception:
                        pass
            return json.dumps({"error": f"Command processing error: {e}"})

    # -----------------------------------------------------------------
    # Tool routing and dispatch
    # -----------------------------------------------------------------

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Route a tool call to the appropriate handler."""

        # Direct handler method map (GUI-safe — runs on Qt thread)
        direct_map = {
            "create_box": self.primitives.create_box,
            "create_cylinder": self.primitives.create_cylinder,
            "create_sphere": self.primitives.create_sphere,
            "create_cone": self.primitives.create_cone,
            "create_torus": self.primitives.create_torus,
            "create_wedge": self.primitives.create_wedge,
            "fuse_objects": self.boolean_ops.fuse_objects,
            "cut_objects": self.boolean_ops.cut_objects,
            "common_objects": self.boolean_ops.common_objects,
            "move_object": self.transforms.move_object,
            "rotate_object": self.transforms.rotate_object,
            "copy_object": self.transforms.copy_object,
            "array_object": self.transforms.array_object,
            "create_sketch": self.sketch_ops.create_sketch,
            "sketch_verify": self.sketch_ops.verify_sketch,
        }

        if tool_name in direct_map:
            method = direct_map[tool_name]
            return self._call_on_gui_thread(method, args, tool_name)

        # Smart dispatchers — route by operation name within a handler
        # PartDesign has explicit method mapping (operation names differ from method names)
        if tool_name == "partdesign_operations":
            return self._dispatch_partdesign(args)
        # Part operations have mixed routing across multiple handlers
        if tool_name == "part_operations":
            return self._dispatch_part_operations(args)
        # View control mixes view_ops and document_ops
        if tool_name == "view_control":
            return self._dispatch_view_control(args)

        # Generic dispatchers — operation name matches handler method name
        generic_dispatch_map = {
            "cam_operations": self.cam_ops,
            "cam_tools": self.cam_tools,
            "cam_tool_controllers": self.cam_tool_controllers,
            "draft_operations": self.draft_ops,
            "mesh_operations": self.mesh_ops,
            "spreadsheet_operations": self.spreadsheet_ops,
        }

        if tool_name in generic_dispatch_map:
            return self._dispatch_to_handler(generic_dispatch_map[tool_name], args, tool_name)

        # Special tools
        if tool_name == "execute_python":
            return self._execute_python(args)
        if tool_name == "execute_python_async":
            return self._execute_python_async(args)
        if tool_name == "poll_job":
            return self._poll_job(args)
        if tool_name == "list_jobs":
            return self._list_jobs(args)
        if tool_name == "cancel_operation":
            return self._cancel_operation(args)
        if tool_name == "get_debug_logs":
            return self._get_debug_logs(args)
        if tool_name == "restart_freecad":
            return self._restart_freecad(args)

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _call_on_gui_thread(self, method, args: Dict[str, Any], label: str) -> str:
        """Wrap a handler method call for GUI-safe execution."""
        def task():
            try:
                result = method(args)
                return {"success": True, "result": result}
            except Exception as e:
                return {"error": f"{label} error: {e}", "traceback": tb_module.format_exc()}
        return self._run_on_gui_thread(task)

    def _dispatch_to_handler(self, handler, args: Dict[str, Any], tool_name: str) -> str:
        """Generic dispatch: look up args['operation'] as a method on handler."""
        operation = args.get("operation", "")
        method = getattr(handler, operation, None)

        if not method or not callable(method):
            return json.dumps({"error": f"Unknown {tool_name} operation: {operation}"})

        def task():
            try:
                result = method(args)
                return {"success": True, "result": result}
            except Exception as e:
                return {"error": f"{tool_name} {operation} error: {e}", "traceback": tb_module.format_exc()}
        return self._run_on_gui_thread(task)

    def _dispatch_partdesign(self, args: Dict[str, Any]) -> str:
        """Route PartDesign operations (operation names differ from method names)."""
        operation = args.get("operation", "")

        operation_map = {
            "pad": self.partdesign_ops.pad_sketch,
            "fillet": self.partdesign_ops.fillet_edges,
            "chamfer": self.partdesign_ops.chamfer_edges,
            "hole": self.partdesign_ops.hole_wizard,
            "linear_pattern": self.partdesign_ops.linear_pattern,
            "mirror": self.partdesign_ops.mirror_feature,
            "revolution": self.partdesign_ops.revolution,
            "loft": self.partdesign_ops.loft_profiles,
            "sweep": self.partdesign_ops.sweep_path,
            "draft": self.partdesign_ops.draft_faces,
            "shell": self.partdesign_ops.shell_solid,
        }

        if operation not in operation_map:
            return json.dumps({"error": f"Unknown PartDesign operation: {operation}"})

        return self._call_on_gui_thread(operation_map[operation], args, f"PartDesign {operation}")

    def _dispatch_part_operations(self, args: Dict[str, Any]) -> str:
        """Route Part operations across multiple handlers."""
        operation = args.get("operation", "")

        method = None
        if operation in ("box", "cylinder", "sphere", "cone", "torus", "wedge"):
            method = getattr(self.primitives, f"create_{operation}", None)
        elif operation in ("fuse", "cut", "common"):
            method = getattr(self.boolean_ops, f"{operation}_objects", None)
        elif operation in ("move", "rotate", "copy", "array"):
            method = getattr(self.transforms, f"{operation}_object", None)
        elif operation in ("extrude", "revolve", "loft", "sweep"):
            method = getattr(self.part_ops, operation, None)

        if not method:
            return json.dumps({"error": f"Unknown Part operation: {operation}"})

        return self._call_on_gui_thread(method, args, f"Part {operation}")

    def _dispatch_view_control(self, args: Dict[str, Any]) -> str:
        """Route view control operations (mixes view_ops and document_ops)."""
        operation = args.get("operation", "")

        operation_map = {
            "screenshot": self.view_ops.take_screenshot,
            "set_view": self.view_ops.set_view,
            "fit_all": self.view_ops.fit_all,
            "zoom_in": self.view_ops.zoom_in,
            "zoom_out": self.view_ops.zoom_out,
            "create_document": self.document_ops.create_document,
            "save_document": self.document_ops.save_document,
            "list_objects": self.document_ops.list_objects,
            "select_object": self.view_ops.select_object,
            "clear_selection": self.view_ops.clear_selection,
            "get_selection": self.view_ops.get_selection,
        }

        if operation not in operation_map:
            return json.dumps({"error": f"Unknown view control operation: {operation}"})

        # view_control handlers manage their own GUI thread safety via their queues
        try:
            result = operation_map[operation](args)
            return json.dumps({"result": result})
        except Exception as e:
            return json.dumps({"error": f"View control {operation} error: {e}"})

    # -----------------------------------------------------------------
    # execute_python
    # -----------------------------------------------------------------

    def _run_python_code(self, code: str) -> dict:
        """Core Python execution: runs code on the GUI thread, captures stdout.

        Returns a result dict suitable for _run_on_gui_thread / _run_on_gui_thread_async.

        Uses a persistent namespace so variables survive across calls.

        Output priority:
          - stdout lines (from print() calls) are always included when present
          - the last-expression value (or `result` variable) is appended when present
          - if neither, returns "Code executed successfully"
        """
        import ast, io, sys

        # Ensure base modules are always available (even if user overwrites them)
        self._python_namespace["FreeCAD"] = FreeCAD
        self._python_namespace["FreeCADGui"] = FreeCADGui
        self._python_namespace["App"] = FreeCAD
        self._python_namespace["Gui"] = FreeCADGui
        try:
            import Part
            self._python_namespace["Part"] = Part
        except ImportError:
            pass
        try:
            from FreeCAD import Vector
            self._python_namespace["Vector"] = Vector
        except ImportError:
            pass
        namespace = self._python_namespace

        result_value = None
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            try:
                tree = ast.parse(code)
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    # Execute all statements except the last
                    if len(tree.body) > 1:
                        exec_module = ast.Module(body=tree.body[:-1], type_ignores=[])
                        ast.fix_missing_locations(exec_module)
                        exec(compile(exec_module, "<string>", "exec"), namespace)
                    # Evaluate the last expression
                    expr_ast = ast.Expression(body=tree.body[-1].value)
                    ast.fix_missing_locations(expr_ast)
                    result_value = eval(compile(expr_ast, "<string>", "eval"), namespace)
                else:
                    exec(code, namespace)
                    if "result" in namespace:
                        result_value = namespace["result"]
            except SyntaxError as e:
                return {"error": f"SyntaxError: {e}", "traceback": tb_module.format_exc()}
        except Exception as e:
            return {"error": f"Python execution error: {e}", "traceback": tb_module.format_exc()}
        finally:
            sys.stdout = old_stdout

        stdout_output = captured.getvalue().rstrip("\n")
        parts = []
        if stdout_output:
            parts.append(stdout_output)
        if result_value is not None:
            parts.append(repr(result_value))

        if parts:
            return {"success": True, "result": "\n".join(parts)}
        return {"success": True, "result": "Code executed successfully"}

    def _execute_python(self, args: Dict[str, Any]) -> str:
        """Execute Python code in FreeCAD context with expression value capture (GUI-safe).

        Handles both statements and expressions, returning the value
        of the last expression if present (similar to IPython/Jupyter behavior).

        Examples:
            "1 + 1"                    -> "2"
            "x = 5"                    -> "Code executed successfully"
            "x = 5\\nx * 2"            -> "10"
            "FreeCAD.ActiveDocument"   -> "<Document object>"
            "result = 42"              -> "42" (explicit result variable)
        """
        code = args.get("code", "")
        if not code:
            return json.dumps({"error": "No code provided"})

        timeout = args.get("timeout", 30.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30.0

        return self._run_on_gui_thread(lambda: self._run_python_code(code), timeout=timeout)

    # -----------------------------------------------------------------
    # Restart FreeCAD
    # -----------------------------------------------------------------

    def _restart_freecad(self, args: Dict[str, Any]) -> str:
        """Restart FreeCAD: save documents, spawn new instance, exit current.

        The response is sent BEFORE the restart happens, so the MCP bridge
        gets a clean response. The new FreeCAD instance will start fresh
        with AICopilot reconnecting on the same socket path.
        """
        import subprocess

        save_docs = args.get("save_documents", True)
        reopen_docs = args.get("reopen_documents", True)

        doc_paths = []
        try:
            for doc_name, doc in FreeCAD.listDocuments().items():
                path = doc.FileName
                if path:
                    if save_docs:
                        doc.save()
                        FreeCAD.Console.PrintMessage(f"[MCP] Saved {doc_name}: {path}\n")
                    if reopen_docs:
                        doc_paths.append(path)
        except Exception as e:
            return json.dumps({"error": f"Failed to save documents: {e}"})

        # Build the command to restart FreeCAD
        # Use sys.executable for the Python, but we need the FreeCAD binary
        fc_bin = FreeCAD.getHomePath() + "bin/FreeCAD"
        if not os.path.exists(fc_bin):
            # Try platform-specific locations
            import shutil
            fc_bin = shutil.which("FreeCAD") or shutil.which("freecad")
        if not fc_bin:
            return json.dumps({"error": "Cannot find FreeCAD binary for restart"})

        # Schedule the restart on the GUI thread (after response is sent)
        def do_restart():
            try:
                cmd = [fc_bin] + doc_paths
                env = os.environ.copy()
                subprocess.Popen(cmd, env=env, start_new_session=True)
                FreeCAD.Console.PrintMessage("[MCP] New FreeCAD instance spawned, exiting...\n")
                # Give the response time to be sent, then quit
                if QtCore:
                    QtCore.QTimer.singleShot(500, lambda: FreeCADGui.getMainWindow().close())
            except Exception as e:
                FreeCAD.Console.PrintError(f"[MCP] Restart failed: {e}\n")

        if QtCore:
            QtCore.QTimer.singleShot(100, do_restart)

        return json.dumps({
            "result": f"Restarting FreeCAD. Saved {len(doc_paths)} documents. "
                      f"New instance will reconnect on same socket.",
            "saved_documents": doc_paths,
        })

    # -----------------------------------------------------------------
    # Debug logs
    # -----------------------------------------------------------------

    def _get_debug_logs(self, args: Dict[str, Any]) -> str:
        """Retrieve recent debug logs for analysis."""
        import glob

        try:
            log_dir = "/tmp/freecad_mcp_debug"
            count = args.get("count", 20)
            operation_filter = args.get("operation", None)

            if not os.path.exists(log_dir):
                return json.dumps({"result": "No debug logs available (logging may be disabled)"})

            log_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
            if not log_files:
                return json.dumps({"result": "No log files found in /tmp/freecad_mcp_debug/"})

            latest_log = max(log_files, key=os.path.getmtime)

            entries = []
            with open(latest_log, "r") as f:
                lines = f.readlines()
                for line in lines[-count:]:
                    try:
                        entry = json.loads(line)
                        if operation_filter and entry.get("operation") != operation_filter:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            return json.dumps({
                "result": f"Retrieved {len(entries)} log entries from {os.path.basename(latest_log)}",
                "log_file": latest_log,
                "entries": entries,
            })

        except Exception as e:
            return json.dumps({"error": f"Failed to retrieve debug logs: {e}"})

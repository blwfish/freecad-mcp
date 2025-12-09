# FreeCAD Socket Server for MCP Communication
# Runs inside FreeCAD to receive commands from external MCP bridge
#
# Version: 3.4.1 - Fixed GUI threading for all operations + delayed startup
# Requires: freecad_debug >= 1.1.0, freecad_health >= 1.0.1

__version__ = "3.4.1"
REQUIRED_VERSIONS = {
    "freecad_debug": ">=1.1.0",
    "freecad_health": ">=1.0.1",
}

import FreeCAD
import FreeCADGui
import socket
import threading
import json
import os
import time
import queue
import platform
import struct
import sys
from typing import Dict, Any, List, Optional
from PySide import QtCore

# Platform-specific socket handling
IS_WINDOWS = platform.system() == "Windows"

# Version validation (fail fast on startup)
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
        FreeCAD.Console.PrintError(f"❌ Version validation failed: {error}\n")
        FreeCAD.Console.PrintError("Component status:\n")
        FreeCAD.Console.PrintError(json.dumps(get_status(), indent=2) + "\n")
        sys.exit(1)
    FreeCAD.Console.PrintMessage(f"✓ socket_server v{__version__} validated\n")
except ImportError as e:
    FreeCAD.Console.PrintWarning(f"ℹ Version system not available (optional): {e}\n")

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
        enable_file=True
    )
    _monitor = init_monitor()

    _log_operation = _log_op_impl
    _capture_state = _capture_state_impl

    DEBUG_ENABLED = True
    FreeCAD.Console.PrintMessage("✅ MCP Debug infrastructure loaded\n")
    FreeCAD.Console.PrintMessage("   Logs: /tmp/freecad_mcp_debug/\n")
    FreeCAD.Console.PrintMessage("   Crashes: /tmp/freecad_mcp_crashes/\n")

except ImportError as e:
    FreeCAD.Console.PrintMessage(f"ℹ️  MCP Debug not available (optional): {e}\n")

except Exception as e:
    FreeCAD.Console.PrintError(f"❌ MCP Debug modules broken: {e}\n")
    FreeCAD.Console.PrintError("   Fix or remove freecad_debug.py/freecad_health.py\n")
    sys.exit(1)

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
        DraftOpsHandler,
        ViewOpsHandler,
        DocumentOpsHandler,
        MeasurementOpsHandler,
        SpreadsheetOpsHandler,
    )
    HANDLERS_AVAILABLE = True
    FreeCAD.Console.PrintMessage("✓ Modular handlers loaded successfully\n")
except ImportError as e:
    HANDLERS_AVAILABLE = False
    FreeCAD.Console.PrintError(f"❌ Modular handlers required but not available: {e}\n")
    sys.exit(1)

# =============================================================================
# GUI Task Queue for Thread-Safe Operations
# =============================================================================
gui_task_queue = queue.Queue()
gui_response_queue = queue.Queue()

def process_gui_tasks():
    """Process GUI tasks in the main Qt thread"""
    while not gui_task_queue.empty():
        try:
            task = gui_task_queue.get_nowait()
            result = task()
            gui_response_queue.put(result)
        except queue.Empty:
            break
        except Exception as e:
            gui_response_queue.put(f"GUI task error: {e}")

    QtCore.QTimer.singleShot(100, process_gui_tasks)

# =============================================================================
# Message Framing Protocol (v2.1.1)
# =============================================================================
# Length-prefixed protocol: [4-byte length][JSON message]
# Prevents message truncation and enables proper boundaries

def send_message(sock: socket.socket, message_str: str) -> bool:
    """Send length-prefixed message"""
    try:
        message_bytes = message_str.encode('utf-8')
        message_len = len(message_bytes)

        # Send 4-byte length prefix (big-endian)
        length_bytes = struct.pack('>I', message_len)
        sock.sendall(length_bytes)

        # Send actual message
        sock.sendall(message_bytes)
        return True

    except Exception as e:
        FreeCAD.Console.PrintWarning(f"Socket send error: {e}\n")
        return False

def receive_message(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive length-prefixed message"""
    try:
        sock.settimeout(timeout)

        # Read 4-byte length prefix
        length_bytes = _recv_exact(sock, 4)
        if not length_bytes:
            return None

        # Unpack length (big-endian)
        message_len = struct.unpack('>I', length_bytes)[0]

        # Validate message length
        MAX_MESSAGE_SIZE = 100 * 1024 * 1024  # 100MB
        if message_len > MAX_MESSAGE_SIZE:
            FreeCAD.Console.PrintError(f"Message too large: {message_len} bytes\n")
            return None

        # Read actual message
        message_bytes = _recv_exact(sock, message_len)
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
    """Receive exactly num_bytes from socket, handling partial reads"""
    buffer = bytearray()

    while len(buffer) < num_bytes:
        chunk = sock.recv(num_bytes - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)

    return bytes(buffer)

# =============================================================================
# Universal Selector System
# =============================================================================
class UniversalSelector:
    """Universal selection system for human-in-the-loop CAD operations"""

    def __init__(self):
        self.pending_operations = {}

    def request_selection(self, tool_name: str, selection_type: str, message: str,
                         object_name: str = "", hints: str = "", **kwargs) -> Dict[str, Any]:
        """Request user selection in FreeCAD GUI"""
        operation_id = f"{tool_name}_{int(time.time() * 1000)}"

        try:
            FreeCADGui.Selection.clearSelection()
        except:
            pass

        self.pending_operations[operation_id] = {
            "tool": tool_name,
            "type": selection_type,
            "object": object_name,
            "timestamp": time.time(),
            **kwargs
        }

        if object_name and selection_type in ["edges", "faces"]:
            self._highlight_elements(object_name, selection_type)

        full_message = message
        if hints:
            full_message += f"\n💡 Tip: {hints}"

        return {
            "status": "awaiting_selection",
            "operation_id": operation_id,
            "selection_type": selection_type,
            "message": full_message,
            "object_name": object_name
        }

    def complete_selection(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Complete selection and return parsed selection data"""
        if operation_id not in self.pending_operations:
            return None

        try:
            selection = FreeCADGui.Selection.getSelectionEx()
        except:
            return {"error": "Could not access FreeCAD selection"}

        context = self.pending_operations.pop(operation_id)
        selection_type = context["type"]

        parsed_data = self._parse_selection(selection, selection_type)

        return {
            "status": "completed",
            "selection": parsed_data,
            "context": context
        }

    def _parse_selection(self, selection: List, selection_type: str) -> Dict[str, Any]:
        """Parse FreeCAD selection based on type"""
        if not selection:
            return {"elements": []}

        if selection_type == "edges":
            edges = []
            for sel in selection:
                for sub in sel.SubElementNames:
                    if sub.startswith("Edge"):
                        edges.append({
                            "object": sel.ObjectName,
                            "element": sub
                        })
            return {"elements": edges}

        elif selection_type == "faces":
            faces = []
            for sel in selection:
                for sub in sel.SubElementNames:
                    if sub.startswith("Face"):
                        faces.append({
                            "object": sel.ObjectName,
                            "element": sub
                        })
            return {"elements": faces}

        elif selection_type == "objects":
            objects = [sel.ObjectName for sel in selection]
            return {"elements": objects}

        return {"elements": []}

    def _highlight_elements(self, object_name: str, element_type: str):
        """Highlight relevant elements in FreeCAD GUI"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return

            obj = doc.getObject(object_name)
            if obj:
                FreeCADGui.Selection.addSelection(obj)
        except:
            pass

    def cleanup_old_operations(self, max_age_seconds: int = 300):
        """Clean up operations older than max_age_seconds"""
        current_time = time.time()
        expired = [
            op_id for op_id, op in self.pending_operations.items()
            if current_time - op["timestamp"] > max_age_seconds
        ]
        for op_id in expired:
            del self.pending_operations[op_id]

# =============================================================================
# FreeCAD Socket Server - Main Class
# =============================================================================
class FreeCADSocketServer:
    """Socket server for FreeCAD MCP communication with modular handler architecture"""

    def __init__(self):
        self.running = False
        self.server_socket = None
        self.server_thread = None
        self.selector = UniversalSelector()

        # Initialize handlers (pass self for access to selector and other resources)
        self.primitives = PrimitivesHandler(self)
        self.boolean_ops = BooleanOpsHandler(self)
        self.transforms = TransformsHandler(self)
        self.sketch_ops = SketchOpsHandler(self)
        self.partdesign_ops = PartDesignOpsHandler(self)
        self.part_ops = PartOpsHandler(self)
        self.cam_ops = CAMOpsHandler(self)
        self.draft_ops = DraftOpsHandler(self)
        # GUI-sensitive handlers need task queues for thread safety
        self.view_ops = ViewOpsHandler(self, gui_task_queue, gui_response_queue)
        self.document_ops = DocumentOpsHandler(self, gui_task_queue, gui_response_queue)
        self.measurement_ops = MeasurementOpsHandler(self)
        self.spreadsheet_ops = SpreadsheetOpsHandler(self)

        FreeCAD.Console.PrintMessage("Socket server initialized with modular handlers\n")

    def start_server(self):
        """Start the socket server"""
        try:
            if IS_WINDOWS:
                self.socket_path = None
                self.host = 'localhost'
                self.port = 23456
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind((self.host, self.port))
                FreeCAD.Console.PrintMessage(f"Socket server started on {self.host}:{self.port} (Windows TCP)\n")
            else:
                self.socket_path = "/tmp/freecad_mcp.sock"
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

            QtCore.QTimer.singleShot(100, process_gui_tasks)

            return True

        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to start socket server: {e}\n")
            return False

    def stop_server(self):
        """Stop the socket server"""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        if not IS_WINDOWS and self.socket_path and os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except:
                pass
        FreeCAD.Console.PrintMessage("Socket server stopped\n")

    def _server_loop(self):
        """Main server loop - accept connections"""
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                try:
                    client_socket, address = self.server_socket.accept()
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket,),
                        daemon=True
                    )
                    client_thread.start()
                except socket.timeout:
                    continue
            except Exception as e:
                if self.running:
                    FreeCAD.Console.PrintError(f"Server loop error: {e}\n")

    def _handle_client(self, client_socket):
        """Handle client connection"""
        try:
            message_str = receive_message(client_socket)
            if message_str:
                response = self._process_command(message_str)
                send_message(client_socket, response)
            client_socket.close()
        except Exception as e:
            FreeCAD.Console.PrintError(f"Client handler error: {e}\n")
            if DEBUG_ENABLED:
                import traceback
                _log_operation(
                    operation="CLIENT_ERROR",
                    error=e,
                    parameters={"traceback": traceback.format_exc()}
                )
            try:
                error_response = json.dumps({"error": f"Server error: {e}"})
                send_message(client_socket, error_response)
                client_socket.close()
            except:
                pass

    def _process_command(self, command_str: str) -> str:
        """Process incoming command"""
        start_time = time.time()
        tool_name = "unknown"

        try:
            command = json.loads(command_str)
            tool_name = command.get("tool", "")
            args = command.get("args", {})

            if not tool_name:
                return json.dumps({"error": "No tool specified"})

            # Capture state before execution (if debug enabled)
            if DEBUG_ENABLED:
                before_state = _capture_state()
                _log_operation(
                    operation="COMMAND_START",
                    parameters={"tool": tool_name, "args": args}
                )

            result = self._execute_tool(tool_name, args)

            # Log successful execution
            if DEBUG_ENABLED:
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_SUCCESS",
                    parameters={"tool": tool_name},
                    result=result[:200] if len(result) > 200 else result,
                    duration=duration
                )

            return result

        except json.JSONDecodeError as e:
            if DEBUG_ENABLED:
                _log_operation(
                    operation="JSON_PARSE_ERROR",
                    error=e,
                    parameters={"command_preview": command_str[:500]}
                )
            return json.dumps({"error": f"Invalid JSON: {e}"})
        except Exception as e:
            if DEBUG_ENABLED:
                import traceback
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_ERROR",
                    error=e,
                    parameters={
                        "tool": tool_name,
                        "traceback": traceback.format_exc()
                    },
                    duration=duration
                )
                # Log crash details if monitor available
                if _monitor:
                    try:
                        _monitor.log_crash(
                            health_status={"tool": tool_name, "error": str(e)},
                            error_context=traceback.format_exc()
                        )
                    except:
                        pass
            return json.dumps({"error": f"Command processing error: {e}"})

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute tool by routing to appropriate handler"""

        # Handler routing map for direct tool calls
        handler_map = {
            # Primitives
            "create_box": self.primitives.create_box,
            "create_cylinder": self.primitives.create_cylinder,
            "create_sphere": self.primitives.create_sphere,
            "create_cone": self.primitives.create_cone,
            "create_torus": self.primitives.create_torus,
            "create_wedge": self.primitives.create_wedge,

            # Boolean Operations
            "fuse_objects": self.boolean_ops.fuse_objects,
            "cut_objects": self.boolean_ops.cut_objects,
            "common_objects": self.boolean_ops.common_objects,

            # Transformations
            "move_object": self.transforms.move_object,
            "rotate_object": self.transforms.rotate_object,
            "copy_object": self.transforms.copy_object,
            "array_object": self.transforms.array_object,

            # Sketch Operations
            "create_sketch": self.sketch_ops.create_sketch,
            "sketch_verify": self.sketch_ops.verify_sketch,
        }

        # Direct handler method lookup (GUI-safe execution)
        if tool_name in handler_map:
            # GUI-safe execution: queue task to main thread
            def handler_task():
                try:
                    result = handler_map[tool_name](args)
                    return {"success": True, "result": result}
                except Exception as e:
                    import traceback
                    return {"error": f"{tool_name} error: {e}", "traceback": traceback.format_exc()}

            gui_task_queue.put(handler_task)

            # Wait for result with timeout
            timeout_seconds = 30
            start_time = time.time()
            while time.time() - start_time < timeout_seconds:
                try:
                    result = gui_response_queue.get_nowait()
                    if isinstance(result, dict):
                        if "error" in result:
                            return json.dumps({"error": result["error"]})
                        elif "success" in result:
                            return json.dumps({"result": result["result"]})
                    return json.dumps({"result": str(result)})
                except:
                    time.sleep(0.05)

            return json.dumps({"error": f"{tool_name} timeout - GUI thread may be busy"})

        # Smart dispatchers - route operation to appropriate handler
        if tool_name == "partdesign_operations":
            return self._handle_partdesign_operations(args)
        elif tool_name == "part_operations":
            return self._handle_part_operations(args)
        elif tool_name == "view_control":
            return self._handle_view_control(args)
        elif tool_name == "cam_operations":
            return self._handle_cam_operations(args)
        elif tool_name == "draft_operations":
            return self._handle_draft_operations(args)
        elif tool_name == "spreadsheet_operations":
            return self._handle_spreadsheet_operations(args)
        elif tool_name == "execute_python":
            return self._execute_python(args)

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _handle_partdesign_operations(self, args: Dict[str, Any]) -> str:
        """Route PartDesign operations to handler (GUI-safe)"""
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

        # GUI-safe execution: queue task to main thread
        def partdesign_task():
            try:
                result = operation_map[operation](args)
                return {"success": True, "result": result}
            except Exception as e:
                import traceback
                return {"error": f"PartDesign {operation} error: {e}", "traceback": traceback.format_exc()}

        gui_task_queue.put(partdesign_task)

        # Wait for result with timeout
        timeout_seconds = 30
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                if isinstance(result, dict):
                    if "error" in result:
                        return json.dumps({"error": result["error"]})
                    elif "success" in result:
                        return json.dumps({"result": result["result"]})
                return json.dumps({"result": str(result)})
            except:
                time.sleep(0.05)

        return json.dumps({"error": f"PartDesign {operation} timeout - GUI thread may be busy"})

    def _handle_part_operations(self, args: Dict[str, Any]) -> str:
        """Route Part operations to handler (GUI-safe)"""
        operation = args.get("operation", "")

        # Build operation map
        method = None
        if operation in ["box", "cylinder", "sphere", "cone", "torus", "wedge"]:
            method = getattr(self.primitives, f"create_{operation}", None)
        elif operation in ["fuse", "cut", "common"]:
            method = getattr(self.boolean_ops, f"{operation}_objects", None)
        elif operation in ["move", "rotate", "copy", "array"]:
            method = getattr(self.transforms, f"{operation}_object", None)
        elif operation in ["extrude", "revolve", "loft", "sweep"]:
            method = getattr(self.part_ops, operation, None)

        if not method:
            return json.dumps({"error": f"Unknown Part operation: {operation}"})

        # GUI-safe execution: queue task to main thread
        def part_task():
            try:
                result = method(args)
                return {"success": True, "result": result}
            except Exception as e:
                import traceback
                return {"error": f"Part {operation} error: {e}", "traceback": traceback.format_exc()}

        gui_task_queue.put(part_task)

        # Wait for result with timeout
        timeout_seconds = 30
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                if isinstance(result, dict):
                    if "error" in result:
                        return json.dumps({"error": result["error"]})
                    elif "success" in result:
                        return json.dumps({"result": result["result"]})
                return json.dumps({"result": str(result)})
            except:
                time.sleep(0.05)

        return json.dumps({"error": f"Part {operation} timeout - GUI thread may be busy"})

    def _handle_view_control(self, args: Dict[str, Any]) -> str:
        """Route view control operations to handler"""
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

        if operation in operation_map:
            try:
                result = operation_map[operation](args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"View control {operation} error: {e}"})

        return json.dumps({"error": f"Unknown view control operation: {operation}"})

    def _handle_cam_operations(self, args: Dict[str, Any]) -> str:
        """Route CAM operations to handler"""
        operation = args.get("operation", "")
        method = getattr(self.cam_ops, operation, None)

        if method:
            try:
                result = method(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"CAM {operation} error: {e}"})

        return json.dumps({"error": f"Unknown CAM operation: {operation}"})

    def _handle_draft_operations(self, args: Dict[str, Any]) -> str:
        """Route Draft operations to handler"""
        operation = args.get("operation", "")
        method = getattr(self.draft_ops, operation, None)

        if method:
            try:
                result = method(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"Draft {operation} error: {e}"})

        return json.dumps({"error": f"Unknown Draft operation: {operation}"})

    def _handle_spreadsheet_operations(self, args: Dict[str, Any]) -> str:
        """Route Spreadsheet operations to handler"""
        operation = args.get("operation", "")
        method = getattr(self.spreadsheet_ops, operation, None)

        if method:
            try:
                result = method(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"Spreadsheet {operation} error: {e}"})

        return json.dumps({"error": f"Unknown Spreadsheet operation: {operation}"})

    def _execute_python(self, args: Dict[str, Any]) -> str:
        """Execute arbitrary Python code in FreeCAD context (GUI-safe)"""
        code = args.get("code", "")

        if not code:
            return json.dumps({"error": "No code provided"})

        # GUI-safe execution: queue task to main thread
        def execute_task():
            try:
                # Execute in FreeCAD namespace
                namespace = {
                    "FreeCAD": FreeCAD,
                    "FreeCADGui": FreeCADGui,
                    "App": FreeCAD,
                    "Gui": FreeCADGui,
                }

                exec(code, namespace)

                # Check if there's a return value
                if "result" in namespace:
                    return {"success": True, "result": str(namespace["result"])}

                return {"success": True, "result": "Code executed successfully"}

            except Exception as e:
                import traceback
                return {"error": f"Python execution error: {e}", "traceback": traceback.format_exc()}

        gui_task_queue.put(execute_task)

        # Wait for result with timeout
        timeout_seconds = 30
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                if isinstance(result, dict):
                    if "error" in result:
                        return json.dumps({"error": result["error"]})
                    elif "success" in result:
                        return json.dumps({"result": result["result"]})
                return json.dumps({"result": str(result)})
            except:
                time.sleep(0.05)

        return json.dumps({"error": "Python execution timeout - GUI thread may be busy"})

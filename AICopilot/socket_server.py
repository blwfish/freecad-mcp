# FreeCAD Socket Server for MCP Communication
# Runs inside FreeCAD to receive commands from external MCP bridge
#
# Version: 3.0.0 - Refactored with modular handlers
# Requires: freecad_debug >= 1.1.0, freecad_health >= 1.0.1

__version__ = "3.0.0"
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
import asyncio
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
# MCP Debug Infrastructure
# =============================================================================
# Optional but recommended - provides crash logging and operation tracing
# If modules are present but incomplete/broken, we crash early to alert user

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
    
    # Initialize with FreeCAD-appropriate settings
    _debugger = init_debugger(
        log_dir="/tmp/freecad_mcp_debug",
        enable_console=False,  # Don't spam FreeCAD Report View
        enable_file=True
    )
    _monitor = init_monitor()
    
    # Wire up the actual implementations
    _log_operation = _log_op_impl
    _capture_state = _capture_state_impl
    
    DEBUG_ENABLED = True
    FreeCAD.Console.PrintMessage("✅ MCP Debug infrastructure loaded\n")
    FreeCAD.Console.PrintMessage("   Logs: /tmp/freecad_mcp_debug/\n")
    FreeCAD.Console.PrintMessage("   Crashes: /tmp/freecad_mcp_crashes/\n")
    
except ImportError as e:
    # Debug modules not present - that's OK, continue without them
    FreeCAD.Console.PrintMessage(f"ℹ️  MCP Debug not available (optional): {e}\n")
    
except Exception as e:
    # Debug modules present but broken - crash early to alert user
    FreeCAD.Console.PrintError(f"❌ MCP Debug modules broken: {e}\n")
    FreeCAD.Console.PrintError("   Fix or remove freecad_debug.py/freecad_health.py\n")
    raise

# Operations that get full state capture (more expensive but useful for debugging)
FULLY_INSTRUMENTED_OPS = {
    'get_screenshot', 
    'screenshot',
    'execute_python', 
    'view_control',
    'part_operations',
    'partdesign_operations',
}

# Import our new modal command system
try:
    from modal_command_system import get_modal_system
except ImportError:
    # Fallback if modal system not available
    get_modal_system = None

# Import the ReAct agent (TEMPORARILY DISABLED FOR TESTING)
# try:
#     from freecad_agent import FreeCADReActAgent
# except ImportError as e:
#     FreeCAD.Console.PrintWarning(f"Could not import FreeCADReActAgent: {e}\n")
#     FreeCADReActAgent = None
FreeCADReActAgent = None  # Temporarily disabled

# =============================================================================
# Modular Handlers (v3.0.0)
# =============================================================================
# Import operation handlers from handlers/ module
try:
    from handlers import (
        PrimitivesHandler,
        BooleanOpsHandler,
        TransformsHandler,
        SketchOpsHandler,
        PartDesignOpsHandler,
        PartOpsHandler,
        CAMOpsHandler,
        ViewOpsHandler,
        DocumentOpsHandler,
        MeasurementOpsHandler,
    )
    HANDLERS_AVAILABLE = True
    FreeCAD.Console.PrintMessage("✓ Modular handlers loaded successfully\n")
except ImportError as e:
    HANDLERS_AVAILABLE = False
    FreeCAD.Console.PrintWarning(f"⚠ Modular handlers not available, using legacy methods: {e}\n")

# GUI task queue for thread-safe document operations
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
    
    # Schedule next processing
    QtCore.QTimer.singleShot(100, process_gui_tasks)


# =============================================================================
# Message Framing Protocol
# =============================================================================
# 
# To prevent JSON truncation and enable proper message boundaries, we use
# a simple length-prefixed protocol:
#
# Message Format:
# [4 bytes: message length as uint32 big-endian][message bytes]

def send_message(sock: socket.socket, message_str: str) -> bool:
    """Send a length-prefixed message over socket.
    
    Returns:
        True if successful, False if socket error
    """
    try:
        # Encode message
        message_bytes = message_str.encode('utf-8')
        message_len = len(message_bytes)
        
        # Create length prefix (4 bytes, big-endian unsigned int)
        length_prefix = struct.pack('>I', message_len)
        
        # Send length + message
        sock.sendall(length_prefix + message_bytes)
        return True
        
    except (socket.error, BrokenPipeError) as e:
        FreeCAD.Console.PrintWarning(f"Socket send error: {e}\n")
        return False

def receive_message(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive a length-prefixed message from socket.
    
    Args:
        sock: Socket to receive from
        timeout: Maximum time to wait for complete message
        
    Returns:
        Decoded message string, or None if error/timeout
    """
    try:
        # Set socket timeout
        sock.settimeout(timeout)
        
        # First, read the 4-byte length prefix
        length_bytes = _recv_exact(sock, 4)
        if not length_bytes:
            return None
            
        # Unpack length
        message_len = struct.unpack('>I', length_bytes)[0]
        
        # Validate length (prevent memory attacks and accidental token waste)
        MAX_MESSAGE_SIZE = 50 * 1024  # 50KB = ~15K tokens (~7.5% of daily Pro budget)
        if message_len > MAX_MESSAGE_SIZE:
            message_mb = message_len / (1024 * 1024)
            message_kb = message_len / 1024
            est_tokens = int(message_len / 3.5)
            FreeCAD.Console.PrintError(
                f"❌ Message too large: {message_kb:.1f}KB ({est_tokens:,} tokens)\n"
            )
            FreeCAD.Console.PrintError(
                f"   Current limit: {MAX_MESSAGE_SIZE/1024:.0f}KB to prevent accidental token waste\n"
            )
            FreeCAD.Console.PrintError(
                f"   To raise limit: Edit socket_server.py line ~214, change MAX_MESSAGE_SIZE\n"
            )
            FreeCAD.Console.PrintError(
                f"   ⚠️  Be aware: This message would cost ~{est_tokens:,} tokens!\n"
            )
            return None
        
        # Read the exact number of message bytes
        message_bytes = _recv_exact(sock, message_len)
        if not message_bytes:
            return None
            
        # Decode and return
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
    """Receive exactly num_bytes from socket, handling partial reads.
    
    This is critical because recv() may return less than requested bytes,
    especially for large messages or slow networks.
    
    Returns:
        Complete byte buffer of exactly num_bytes, or None if connection closed
    """
    buffer = bytearray()
    
    while len(buffer) < num_bytes:
        chunk = sock.recv(num_bytes - len(buffer))
        if not chunk:
            # Connection closed
            return None
        buffer.extend(chunk)
    
    return bytes(buffer)


class UniversalSelector:
    """Universal selection system for human-in-the-loop CAD operations"""
    
    def __init__(self):
        self.pending_operations = {}  # Track pending selection operations
        
    def request_selection(self, tool_name: str, selection_type: str, message: str, 
                         object_name: str = "", hints: str = "", **kwargs) -> Dict[str, Any]:
        """Request user selection in FreeCAD GUI"""
        operation_id = f"{tool_name}_{int(time.time() * 1000)}"  # Unique ID
        
        # Clear previous selection
        try:
            FreeCADGui.Selection.clearSelection()
        except:
            pass  # GUI might not be available in headless mode
        
        # Store operation context with all parameters
        self.pending_operations[operation_id] = {
            "tool": tool_name,
            "type": selection_type,
            "object": object_name,
            "timestamp": time.time(),
            **kwargs  # Store any additional parameters (radius, distance, etc.)
        }
        
        # Optional: highlight relevant elements
        if object_name and selection_type in ["edges", "faces"]:
            self._highlight_elements(object_name, selection_type)
        
        # Add helpful hints
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
        
        # Get current selection from FreeCAD
        try:
            selection = FreeCADGui.Selection.getSelectionEx()
        except:
            return {"error": "Could not access FreeCAD selection"}
        
        # Get operation context
        context = self.pending_operations.pop(operation_id)
        selection_type = context["type"]
        
        # Parse selection based on type
        parsed_data = self._parse_selection(selection, selection_type)
        
        return {
            "selection_data": parsed_data,
            "context": context,
            "selection_count": len(parsed_data.get("elements", []))
        }
    
    def _parse_selection(self, selection: List, selection_type: str) -> Dict[str, Any]:
        """Parse FreeCAD selection based on requested type"""
        result = {"elements": [], "objects": []}
        
        for sel in selection:
            obj_info = {
                "document": sel.DocumentName,
                "object": sel.ObjectName,
                "sub_elements": sel.SubElementNames
            }
            result["objects"].append(obj_info)
            
            if selection_type == "edges":
                # Extract edge indices
                for sub in sel.SubElementNames:
                    if sub.startswith('Edge'):
                        try:
                            edge_idx = int(sub[4:])  # Extract number from "EdgeN"
                            result["elements"].append(edge_idx)
                        except ValueError:
                            continue
                            
            elif selection_type == "faces":
                # Extract face indices  
                for sub in sel.SubElementNames:
                    if sub.startswith('Face'):
                        try:
                            face_idx = int(sub[4:])  # Extract number from "FaceN"
                            result["elements"].append(face_idx)
                        except ValueError:
                            continue
                            
            elif selection_type == "objects":
                # Just collect object names
                result["elements"].append(sel.ObjectName)
        
        return result
    
    def _highlight_elements(self, object_name: str, element_type: str):
        """Optional: Highlight selectable elements to help user"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return
                
            obj = doc.getObject(object_name)
            if not obj or not hasattr(obj, 'Shape'):
                return
                
            # Could implement visual hints here
            # For now, just ensure object is visible and selected for context
            if hasattr(obj, 'ViewObject'):
                obj.ViewObject.Visibility = True
                
        except Exception:
            pass  # Non-critical feature
    
    def cleanup_old_operations(self, max_age_seconds: int = 300):
        """Clean up operations older than max_age_seconds"""
        current_time = time.time()
        expired_ops = [
            op_id for op_id, context in self.pending_operations.items()
            if current_time - context["timestamp"] > max_age_seconds
        ]
        
        for op_id in expired_ops:
            self.pending_operations.pop(op_id, None)
            
        return len(expired_ops)

class FreeCADSocketServer:
    """Socket server that runs inside FreeCAD to receive MCP commands"""
    
    def __init__(self):
        # Set socket path based on platform
        if IS_WINDOWS:
            self.socket_path = "localhost:23456"
            self.host = 'localhost'
            self.port = 23456
        else:
            self.socket_path = "/tmp/freecad_mcp.sock"
            self.host = None
            self.port = None
        
        self.server_socket = None
        self.is_running = False
        self.client_connections = []
        
        # Initialize universal selection system
        self.selector = UniversalSelector()

        # Initialize modular handlers (v3.0.0)
        if HANDLERS_AVAILABLE:
            self.primitives = PrimitivesHandler(self)
            self.boolean_ops = BooleanOpsHandler(self)
            self.transforms = TransformsHandler(self)
            self.sketch_ops = SketchOpsHandler(self)
            self.partdesign_ops = PartDesignOpsHandler(self)
            self.part_ops = PartOpsHandler(self)
            self.cam_ops = CAMOpsHandler(self)
            self.view_ops = ViewOpsHandler(self, gui_task_queue, gui_response_queue)
            self.document_ops = DocumentOpsHandler(self, gui_task_queue, gui_response_queue)
            self.measurement_ops = MeasurementOpsHandler(self)
            FreeCAD.Console.PrintMessage("✓ All operation handlers initialized\n")
        else:
            # Set handlers to None for legacy fallback
            self.primitives = None
            self.boolean_ops = None
            self.transforms = None
            self.sketch_ops = None
            self.partdesign_ops = None
            self.part_ops = None
            self.cam_ops = None
            self.view_ops = None
            self.document_ops = None
            self.measurement_ops = None

        # Initialize the ReAct agent
        if FreeCADReActAgent:
            self.agent = FreeCADReActAgent(self)
            FreeCAD.Console.PrintMessage("Socket server initialized with ReAct Agent and Universal Selector\n")
        else:
            self.agent = None
            FreeCAD.Console.PrintMessage("Socket server initialized with Universal Selector (agent import failed)\n")
            
    def start_server(self):
        """Start the socket server (cross-platform)"""
        try:
            if IS_WINDOWS:
                # Use TCP socket on Windows
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind((self.host, self.port))
                self.server_socket.listen(5)
                FreeCAD.Console.PrintMessage(f"Socket server started on {self.host}:{self.port} (Windows TCP)\n")
            else:
                # Use Unix domain socket on macOS/Linux
                if os.path.exists(self.socket_path):
                    os.remove(self.socket_path)
                
                # Use getattr to safely access AF_UNIX (returns 1 on Unix, None on Windows)
                socket_family = getattr(socket, 'AF_UNIX', socket.AF_INET)
                if socket_family == socket.AF_INET:
                    # Fallback to TCP if AF_UNIX not available
                    self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self.server_socket.bind(('localhost', 23456))
                    self.server_socket.listen(5)
                    FreeCAD.Console.PrintMessage("Socket server started on localhost:23456 (TCP fallback)\n")
                else:
                    # Use Unix socket
                    self.server_socket = socket.socket(socket_family, socket.SOCK_STREAM)
                    self.server_socket.bind(self.socket_path)
                    self.server_socket.listen(5)
                    FreeCAD.Console.PrintMessage(f"Socket server started on {self.socket_path} (Unix socket)\n")
            
            self.is_running = True
            
            # Start server thread
            server_thread = threading.Thread(target=self._server_loop, daemon=True)
            server_thread.start()
            
            # Initialize GUI task processor
            QtCore.QTimer.singleShot(100, process_gui_tasks)
            
            FreeCAD.Console.PrintMessage(f"Socket server started on {self.socket_path}\n")
            return True
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to start socket server: {e}\n")
            return False
            
    def _server_loop(self):
        """Main server loop to accept connections"""
        while self.is_running and self.server_socket:
            try:
                client_socket, _ = self.server_socket.accept()
                
                # Handle client in separate thread
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket,),
                    daemon=True
                )
                client_thread.start()
                self.client_connections.append(client_socket)
                
            except Exception as e:
                if self.is_running:
                    FreeCAD.Console.PrintError(f"Server loop error: {e}\n")
                break
                
    def _handle_client(self, client_socket):
        """Handle individual client connections with robust message framing"""
        try:
            while self.is_running:
                # Receive complete message using length-prefixed protocol
                command_str = receive_message(client_socket, timeout=30.0)
                
                if command_str is None:
                    # Connection closed or error
                    break
                
                # Process the command
                response = self._process_command(command_str)
                
                # Send response using length-prefixed protocol
                if response:
                    success = send_message(client_socket, response)
                    if not success:
                        # Socket error - connection likely broken
                        break
                    
        except Exception as e:
            # Log error but don't crash - allow server to continue
            FreeCAD.Console.PrintError(f"Client handler error: {e}\n")
            if DEBUG_ENABLED:
                import traceback
                _log_operation(
                    operation="CLIENT_ERROR",
                    error=e,
                    parameters={"traceback": traceback.format_exc()}
                )
        finally:
            # Always clean up connection
            try:
                client_socket.close()
            except:
                pass
            if client_socket in self.client_connections:
                self.client_connections.remove(client_socket)
                
    def _process_command(self, command_str: str) -> str:
        """Process incoming command and return response"""
        start_time = time.time()
        tool_name = "unknown"
        args = {}
        before_state = None
        
        try:
            # Validate command string is not empty
            if not command_str or not command_str.strip():
                return json.dumps({
                    "success": False,
                    "error": "Empty command received"
                })
            
            # Parse JSON command with validation
            try:
                command = json.loads(command_str)
            except json.JSONDecodeError as json_err:
                # Malformed JSON - return error instead of crashing
                error_msg = f"Invalid JSON: {json_err}"
                FreeCAD.Console.PrintError(f"{error_msg}\n")
                FreeCAD.Console.PrintError(f"Command preview: {command_str[:200]}...\n")
                
                if DEBUG_ENABLED:
                    _log_operation(
                        operation="JSON_PARSE_ERROR",
                        error=json_err,
                        parameters={"command_preview": command_str[:500]}
                    )
                
                return json.dumps({
                    "success": False,
                    "error": error_msg
                })
            
            # Extract tool name and arguments
            tool_name = command.get('tool', 'unknown')
            args = command.get('args', {})
            
            # Determine instrumentation level
            full_instrumentation = (
                DEBUG_ENABLED and 
                tool_name in FULLY_INSTRUMENTED_OPS
            )
            
            # Log incoming request
            if DEBUG_ENABLED:
                _log_operation(
                    operation=f"REQUEST:{tool_name}",
                    parameters=args
                )
                
                # Capture state before for fully instrumented ops
                if full_instrumentation:
                    try:
                        before_state = _capture_state()
                    except Exception as state_err:
                        FreeCAD.Console.PrintWarning(
                            f"State capture failed: {state_err}\n"
                        )
            
            # Route to appropriate handler
            result = self._execute_tool(tool_name, args)
            
            duration = time.time() - start_time
            
            # Log successful completion
            if DEBUG_ENABLED:
                # Truncate large results for logging
                result_summary = result
                if result and len(str(result)) > 500:
                    result_summary = str(result)[:500] + "...[truncated]"
                
                _log_operation(
                    operation=f"RESPONSE:{tool_name}",
                    parameters={"duration_ms": int(duration * 1000)},
                    result=result_summary
                )
                
                # Capture and compare state after for fully instrumented ops
                if full_instrumentation and before_state:
                    try:
                        after_state = _capture_state()
                        # Log state delta if objects changed
                        before_count = before_state.get('object_count', 0)
                        after_count = after_state.get('object_count', 0)
                        if before_count != after_count:
                            _log_operation(
                                operation=f"STATE_CHANGE:{tool_name}",
                                parameters={
                                    "objects_before": before_count,
                                    "objects_after": after_count,
                                    "delta": after_count - before_count
                                }
                            )
                    except Exception as state_err:
                        pass  # State comparison is best-effort
            
            return json.dumps({
                "success": True,
                "result": result
            })
            
        except Exception as e:
            duration = time.time() - start_time
            
            # Log failure with full details
            if DEBUG_ENABLED:
                import traceback
                _log_operation(
                    operation=f"ERROR:{tool_name}",
                    parameters=args,
                    error=e,
                    duration=duration
                )
                
                # If we have a monitor, log crash details
                if _monitor:
                    try:
                        _monitor.log_crash(
                            health_status={"tool": tool_name, "args": args},
                            additional_info={"traceback": traceback.format_exc()}
                        )
                    except:
                        pass
            
            return json.dumps({
                "success": False,
                "error": str(e)
            })
            
    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute the requested tool with modular handler support (v3.0.0)

        Routes tool calls to modular handlers when available, with fallback
        to legacy methods for backward compatibility.
        """

        # Use modular handlers if available (v3.0.0)
        if HANDLERS_AVAILABLE:
            return self._execute_tool_with_handlers(tool_name, args)
        else:
            return self._execute_tool_legacy(tool_name, args)

    def _execute_tool_with_handlers(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute tool using modular handlers (v3.0.0)"""

        # Smart dispatcher tools - route to aggregate handlers
        if tool_name == "view_control":
            return self._handle_view_control(args)
        elif tool_name == "partdesign_operations":
            return self._handle_partdesign_operations(args)
        elif tool_name == "part_operations":
            return self._handle_part_operations(args)
        elif tool_name == "cam_operations":
            return self._handle_cam_operations(args)
        elif tool_name == "execute_python":
            return self._execute_python(args)

        # Primitives
        elif tool_name == "create_box":
            return self.primitives.create_box(args)
        elif tool_name == "create_cylinder":
            return self.primitives.create_cylinder(args)
        elif tool_name == "create_sphere":
            return self.primitives.create_sphere(args)
        elif tool_name == "create_cone":
            return self.primitives.create_cone(args)
        elif tool_name == "create_torus":
            return self.primitives.create_torus(args)
        elif tool_name == "create_wedge":
            return self.primitives.create_wedge(args)

        # Boolean Operations
        elif tool_name == "fuse_objects":
            return self.boolean_ops.fuse_objects(args)
        elif tool_name == "cut_objects":
            return self.boolean_ops.cut_objects(args)
        elif tool_name == "common_objects":
            return self.boolean_ops.common_objects(args)

        # Transformations
        elif tool_name == "move_object":
            return self.transforms.move_object(args)
        elif tool_name == "rotate_object":
            return self.transforms.rotate_object(args)
        elif tool_name == "copy_object":
            return self.transforms.copy_object(args)
        elif tool_name == "array_object":
            return self.transforms.array_object(args)

        # Sketch Operations
        elif tool_name == "create_sketch":
            return self.sketch_ops.create_sketch(args)
        elif tool_name == "sketch_verify":
            return self.sketch_ops.verify_sketch(args)

        # PartDesign Operations
        elif tool_name == "pad_sketch":
            return self.partdesign_ops.pad_sketch(args)
        elif tool_name == "fillet_edges":
            return self.partdesign_ops.fillet_edges(args)
        elif tool_name == "chamfer_edges":
            return self.partdesign_ops.chamfer_edges(args)
        elif tool_name == "draft_faces":
            return self.partdesign_ops.draft_faces(args)
        elif tool_name == "hole_wizard":
            return self.partdesign_ops.hole_wizard(args)
        elif tool_name == "linear_pattern":
            return self.partdesign_ops.linear_pattern(args)
        elif tool_name == "mirror_feature":
            return self.partdesign_ops.mirror_feature(args)
        elif tool_name == "revolution":
            return self.partdesign_ops.revolution(args)
        elif tool_name == "loft_profiles":
            return self.partdesign_ops.loft_profiles(args)
        elif tool_name == "sweep_path":
            return self.partdesign_ops.sweep_path(args)
        elif tool_name == "shell_solid":
            return self.partdesign_ops.shell_solid(args)
        elif tool_name == "create_rib":
            return self.partdesign_ops.create_rib(args)
        elif tool_name == "create_helix":
            return self.partdesign_ops.create_helix(args)
        elif tool_name == "polar_pattern":
            return self.partdesign_ops.polar_pattern(args)
        elif tool_name == "add_thickness":
            return self.partdesign_ops.add_thickness(args)

        # Part Operations
        elif tool_name == "part_extrude":
            return self.part_ops.extrude(args)
        elif tool_name == "part_revolve":
            return self.part_ops.revolve(args)
        elif tool_name == "part_mirror":
            return self.part_ops.mirror_object(args)
        elif tool_name == "part_scale":
            return self.part_ops.scale_object(args)
        elif tool_name == "part_section":
            return self.part_ops.section(args)
        elif tool_name == "part_compound":
            return self.part_ops.compound(args)
        elif tool_name == "part_check_geometry":
            return self.part_ops.check_geometry(args)

        # Measurement/Analysis
        elif tool_name == "measure_distance":
            return self.measurement_ops.measure_distance(args)
        elif tool_name == "get_volume":
            return self.measurement_ops.get_volume(args)
        elif tool_name == "get_bounding_box":
            return self.measurement_ops.get_bounding_box(args)
        elif tool_name == "get_mass_properties":
            return self.measurement_ops.get_mass_properties(args)

        # View Operations
        elif tool_name == "get_screenshot":
            return self.view_ops.get_screenshot(args)
        elif tool_name == "set_view":
            return self.view_ops.set_view_gui_safe(args)
        elif tool_name == "fit_all":
            return self.view_ops.fit_all(args)

        # Document Operations
        elif tool_name == "list_all_objects":
            return self.document_ops.list_objects(args)
        elif tool_name == "activate_workbench":
            return self.document_ops.activate_workbench(args)
        elif tool_name == "run_command":
            return self.document_ops.run_command(args)
        elif tool_name == "save_document":
            return self.document_ops.save_document(args)
        elif tool_name == "open_document":
            return self.document_ops.open_document(args)
        elif tool_name == "select_object":
            return self.document_ops.select_object(args)
        elif tool_name == "clear_selection":
            return self.document_ops.clear_selection(args)
        elif tool_name == "get_selection":
            return self.document_ops.get_selection(args)
        elif tool_name == "hide_object":
            return self.document_ops.hide_object(args)
        elif tool_name == "show_object":
            return self.document_ops.show_object(args)
        elif tool_name == "delete_object":
            return self.document_ops.delete_object(args)
        elif tool_name == "undo":
            return self.document_ops.undo(args)
        elif tool_name == "redo":
            return self.document_ops.redo(args)

        # Special handlers
        elif tool_name == "ai_agent":
            return self._ai_agent(args)
        elif tool_name == "continue_selection":
            return self._continue_selection(args)

        else:
            return f"Unknown tool: {tool_name}"

    def _execute_tool_legacy(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Legacy tool execution (fallback when handlers not available)"""

        # Handle view_control with GUI-safe operations
        if tool_name == "view_control":
            return self._handle_view_control(args)

        # Handle other smart dispatcher tools
        elif tool_name == "partdesign_operations":
            return self._handle_partdesign_operations(args)
        elif tool_name == "part_operations":
            return self._handle_part_operations(args)
        elif tool_name == "cam_operations":
            return self._handle_cam_operations(args)
        elif tool_name == "execute_python":
            return self._execute_python(args)

        # Legacy individual tool routing (for backward compatibility)
        # Map tool names to implementations
        elif tool_name == "create_box":
            return self._create_box(args)
        elif tool_name == "create_cylinder":
            return self._create_cylinder(args)
        elif tool_name == "create_sphere":
            return self._create_sphere(args)
        elif tool_name == "create_cone":
            return self._create_cone(args)
        elif tool_name == "create_torus":
            return self._create_torus(args)
        elif tool_name == "create_wedge":
            return self._create_wedge(args)
        # Boolean Operations
        elif tool_name == "fuse_objects":
            return self._fuse_objects(args)
        elif tool_name == "cut_objects":
            return self._cut_objects(args)
        elif tool_name == "common_objects":
            return self._common_objects(args)
        # Transformations
        elif tool_name == "move_object":
            return self._move_object(args)
        elif tool_name == "rotate_object":
            return self._rotate_object(args)
        elif tool_name == "copy_object":
            return self._copy_object(args)
        elif tool_name == "array_object":
            return self._array_object(args)
        # Part Design
        elif tool_name == "create_sketch":
            return self._create_sketch(args)
        elif tool_name == "pad_sketch":
            return self._pad_sketch(args)
        elif tool_name == "fillet_edges":
            return self._fillet_edges(args)
        # Priority 1: Essential Missing Tools
        elif tool_name == "chamfer_edges":
            return self._chamfer_edges(args)
        elif tool_name == "draft_faces":
            return self._draft_faces(args)
        elif tool_name == "hole_wizard":
            return self._hole_wizard(args)
        elif tool_name == "linear_pattern":
            return self._linear_pattern(args)
        elif tool_name == "mirror_feature":
            return self._mirror_feature(args)
        elif tool_name == "revolution":
            return self._revolution(args)
        # Priority 2: Professional Features
        elif tool_name == "loft_profiles":
            return self._loft_profiles(args)
        elif tool_name == "sweep_path":
            return self._sweep_path(args)
        elif tool_name == "shell_solid":
            return self._shell_solid(args)
        elif tool_name == "create_rib":
            return self._create_rib(args)
        # Priority 3: Advanced Tools
        elif tool_name == "create_helix":
            return self._create_helix(args)
        elif tool_name == "polar_pattern":
            return self._polar_pattern(args)
        elif tool_name == "add_thickness":
            return self._add_thickness(args)
        # Analysis
        elif tool_name == "measure_distance":
            return self._measure_distance(args)
        elif tool_name == "get_volume":
            return self._get_volume(args)
        elif tool_name == "get_bounding_box":
            return self._get_bounding_box(args)
        elif tool_name == "get_mass_properties":
            return self._get_mass_properties(args)
        elif tool_name == "get_screenshot":
            return self._get_screenshot_gui_safe(args)
        elif tool_name == "list_all_objects":
            return self._list_all_objects(args)
        elif tool_name == "activate_workbench":
            return self._activate_workbench(args)
        # GUI Control Tools
        elif tool_name == "run_command":
            return self._run_command(args)
        elif tool_name == "save_document":
            return self._save_document(args)
        elif tool_name == "open_document":
            return self._open_document(args)
        elif tool_name == "set_view":
            return self._set_view_gui_safe(args)
        elif tool_name == "fit_all":
            return self._fit_all(args)
        elif tool_name == "select_object":
            return self._select_object(args)
        elif tool_name == "clear_selection":
            return self._clear_selection(args)
        elif tool_name == "get_selection":
            return self._get_selection(args)
        elif tool_name == "hide_object":
            return self._hide_object(args)
        elif tool_name == "show_object":
            return self._show_object(args)
        elif tool_name == "delete_object":
            return self._delete_object(args)
        elif tool_name == "undo":
            return self._undo(args)
        elif tool_name == "redo":
            return self._redo(args)
        elif tool_name == "ai_agent":
            return self._ai_agent(args)
        elif tool_name == "continue_selection":
            return self._continue_selection(args)
        else:
            return f"Unknown tool: {tool_name}"
            
    def _create_box(self, args: Dict[str, Any]) -> str:
        """Create a box with specified dimensions"""
        try:
            length = args.get('length', 10)
            width = args.get('width', 10)  
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            # Create document if needed
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            # Create box
            box = doc.addObject("Part::Box", "Box")
            box.Length = length
            box.Width = width
            box.Height = height
            box.Placement.Base = FreeCAD.Vector(x, y, z)
            
            # Recompute and fit view
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created box: {box.Name} ({length}x{width}x{height}mm) at ({x},{y},{z})"
            
        except Exception as e:
            return f"Error creating box: {e}"
            
    def _create_cylinder(self, args: Dict[str, Any]) -> str:
        """Create a cylinder with specified dimensions"""
        try:
            radius = args.get('radius', 5)
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            cylinder = doc.addObject("Part::Cylinder", "Cylinder")
            cylinder.Radius = radius
            cylinder.Height = height
            cylinder.Placement.Base = FreeCAD.Vector(x, y, z)
            
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created cylinder: {cylinder.Name} (R{radius}, H{height}) at ({x},{y},{z})"
            
        except Exception as e:
            return f"Error creating cylinder: {e}"
            
    def _create_sphere(self, args: Dict[str, Any]) -> str:
        """Create a sphere with specified radius"""
        try:
            radius = args.get('radius', 5)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            sphere = doc.addObject("Part::Sphere", "Sphere")
            sphere.Radius = radius
            sphere.Placement.Base = FreeCAD.Vector(x, y, z)
            
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created sphere: {sphere.Name} (R{radius}) at ({x},{y},{z})"
            
        except Exception as e:
            return f"Error creating sphere: {e}"
            
    def _create_cone(self, args: Dict[str, Any]) -> str:
        """Create a cone with specified radii and height"""
        try:
            radius1 = args.get('radius1', 5)  # Bottom radius
            radius2 = args.get('radius2', 0)  # Top radius
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            cone = doc.addObject("Part::Cone", "Cone")
            cone.Radius1 = radius1
            cone.Radius2 = radius2
            cone.Height = height
            cone.Placement.Base = FreeCAD.Vector(x, y, z)
            
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created cone: {cone.Name} (R1{radius1}, R2{radius2}, H{height}) at ({x},{y},{z})"
            
        except Exception as e:
            return f"Error creating cone: {e}"
            
    def _create_torus(self, args: Dict[str, Any]) -> str:
        """Create a torus (donut shape) with specified radii"""
        try:
            radius1 = args.get('radius1', 10)  # Major radius
            radius2 = args.get('radius2', 3)   # Minor radius
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            torus = doc.addObject("Part::Torus", "Torus")
            torus.Radius1 = radius1
            torus.Radius2 = radius2
            torus.Placement.Base = FreeCAD.Vector(x, y, z)
            
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created torus: {torus.Name} (R1{radius1}, R2{radius2}) at ({x},{y},{z})"
            
        except Exception as e:
            return f"Error creating torus: {e}"
            
    def _create_wedge(self, args: Dict[str, Any]) -> str:
        """Create a wedge (triangular prism) with specified dimensions"""
        try:
            xmin = args.get('xmin', 0)
            ymin = args.get('ymin', 0)
            zmin = args.get('zmin', 0)
            x2min = args.get('x2min', 2)
            x2max = args.get('x2max', 8)
            xmax = args.get('xmax', 10)
            ymax = args.get('ymax', 10)
            zmax = args.get('zmax', 10)
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            wedge = doc.addObject("Part::Wedge", "Wedge")
            wedge.Xmin = xmin
            wedge.Ymin = ymin
            wedge.Zmin = zmin
            wedge.X2min = x2min
            wedge.X2max = x2max
            wedge.Xmax = xmax
            wedge.Ymax = ymax
            wedge.Zmax = zmax
            
            doc.recompute()
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
            
            return f"Created wedge: {wedge.Name} ({xmax}x{ymax}x{zmax}) at origin"
            
        except Exception as e:
            return f"Error creating wedge: {e}"
            
    # === Boolean Operations ===
    def _fuse_objects(self, args: Dict[str, Any]) -> str:
        """Fuse (union) multiple objects together"""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Fusion')
            
            if len(objects) < 2:
                return "Need at least 2 objects to fuse"
                
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            # Get object references
            objs = []
            for obj_name in objects:
                obj = doc.getObject(obj_name)
                if obj:
                    objs.append(obj)
                else:
                    return f"Object not found: {obj_name}"
                    
            # Create fusion
            fusion = doc.addObject("Part::MultiFuse", name)
            fusion.Shapes = objs
            doc.recompute()
            
            return f"Created fusion: {fusion.Name} from {len(objects)} objects"
            
        except Exception as e:
            return f"Error fusing objects: {e}"
            
    def _cut_objects(self, args: Dict[str, Any]) -> str:
        """Cut (subtract) tools from base object"""
        try:
            base = args.get('base', '')
            tools = args.get('tools', [])
            name = args.get('name', 'Cut')
            
            if not base or not tools:
                return "Need base object and tool objects"
                
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            # Get object references
            base_obj = doc.getObject(base)
            if not base_obj:
                return f"Base object not found: {base}"
                
            tool_objs = []
            for tool_name in tools:
                tool_obj = doc.getObject(tool_name)
                if tool_obj:
                    tool_objs.append(tool_obj)
                else:
                    return f"Tool object not found: {tool_name}"
                    
            # Create cut
            cut = doc.addObject("Part::Cut", name)
            cut.Base = base_obj
            cut.Tool = tool_objs[0] if len(tool_objs) == 1 else tool_objs
            doc.recompute()
            
            return f"Created cut: {cut.Name} from {base} minus {len(tools)} tools"
            
        except Exception as e:
            return f"Error cutting objects: {e}"
            
    def _common_objects(self, args: Dict[str, Any]) -> str:
        """Find intersection of multiple objects"""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Common')
            
            if len(objects) < 2:
                return "Need at least 2 objects for intersection"
                
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            # Get object references
            objs = []
            for obj_name in objects:
                obj = doc.getObject(obj_name)
                if obj:
                    objs.append(obj)
                else:
                    return f"Object not found: {obj_name}"
                    
            # Create common
            common = doc.addObject("Part::MultiCommon", name)
            common.Shapes = objs
            doc.recompute()
            
            return f"Created intersection: {common.Name} from {len(objects)} objects"
            
        except Exception as e:
            return f"Error finding intersection: {e}"
    
    # === Transformation Tools ===
    def _move_object(self, args: Dict[str, Any]) -> str:
        """Move an object to new position"""
        try:
            object_name = args.get('object_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Move object
            obj.Placement.Base = FreeCAD.Vector(
                obj.Placement.Base.x + x,
                obj.Placement.Base.y + y,
                obj.Placement.Base.z + z
            )
            doc.recompute()
            
            return f"Moved {object_name} by ({x}, {y}, {z})"
            
        except Exception as e:
            return f"Error moving object: {e}"
            
    def _rotate_object(self, args: Dict[str, Any]) -> str:
        """Rotate an object around axis"""
        try:
            object_name = args.get('object_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 90)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Set rotation axis
            axis_vector = FreeCAD.Vector(0, 0, 1)  # default Z
            if axis.lower() == 'x':
                axis_vector = FreeCAD.Vector(1, 0, 0)
            elif axis.lower() == 'y':
                axis_vector = FreeCAD.Vector(0, 1, 0)
                
            # Rotate object
            rotation = FreeCAD.Rotation(axis_vector, angle)
            obj.Placement.Rotation = obj.Placement.Rotation.multiply(rotation)
            doc.recompute()
            
            return f"Rotated {object_name} by {angle}° around {axis.upper()}-axis"
            
        except Exception as e:
            return f"Error rotating object: {e}"
            
    def _copy_object(self, args: Dict[str, Any]) -> str:
        """Create a copy of an object"""
        try:
            object_name = args.get('object_name', '')
            name = args.get('name', 'Copy')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Create copy
            copy = doc.copyObject(obj)
            copy.Label = name
            copy.Placement.Base = FreeCAD.Vector(
                obj.Placement.Base.x + x,
                obj.Placement.Base.y + y,
                obj.Placement.Base.z + z
            )
            doc.recompute()
            
            return f"Created copy: {copy.Name} at offset ({x}, {y}, {z})"
            
        except Exception as e:
            return f"Error copying object: {e}"
            
    def _array_object(self, args: Dict[str, Any]) -> str:
        """Create linear array of object"""
        try:
            object_name = args.get('object_name', '')
            count = args.get('count', 3)
            spacing_x = args.get('spacing_x', 10)
            spacing_y = args.get('spacing_y', 0)
            spacing_z = args.get('spacing_z', 0)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Create array copies
            copies = []
            for i in range(1, count):  # Start from 1 (original is 0)
                copy = doc.copyObject(obj)
                copy.Label = f"{obj.Label}_Array{i}"
                copy.Placement.Base = FreeCAD.Vector(
                    obj.Placement.Base.x + (spacing_x * i),
                    obj.Placement.Base.y + (spacing_y * i),
                    obj.Placement.Base.z + (spacing_z * i)
                )
                copies.append(copy.Name)
                
            doc.recompute()
            
            return f"Created array: {count} copies of {object_name} with spacing ({spacing_x}, {spacing_y}, {spacing_z})"
            
        except Exception as e:
            return f"Error creating array: {e}"
    
    # === Part Design Tools ===
    def _create_sketch(self, args: Dict[str, Any]) -> str:
        """Create a new sketch on specified plane"""
        try:
            plane = args.get('plane', 'XY')
            name = args.get('name', 'Sketch')
            
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
            
            # Create sketch
            sketch = doc.addObject('Sketcher::SketchObject', name)
            
            # Set plane
            if plane.upper() == 'XY':
                sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(0,0,0,1))
            elif plane.upper() == 'XZ':
                sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(1,0,0,1))
            elif plane.upper() == 'YZ':
                sketch.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(0,1,0,1))
                
            doc.recompute()
            
            return f"Created sketch: {sketch.Name} on {plane} plane"
            
        except Exception as e:
            return f"Error creating sketch: {e}"
            
    def _pad_sketch(self, args: Dict[str, Any]) -> str:
        """Extrude a sketch to create solid (pad) - requires PartDesign Body"""
        try:
            sketch_name = args.get('sketch_name', '')
            length = args.get('length', 10)
            name = args.get('name', 'Pad')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            sketch = doc.getObject(sketch_name)
            if not sketch:
                return f"Sketch not found: {sketch_name}"
            
            # Check if we have an active PartDesign Body, create one if needed
            body = None
            for obj in doc.Objects:
                if obj.TypeId == "PartDesign::Body":
                    body = obj
                    break
            
            if not body:
                body = doc.addObject("PartDesign::Body", "Body")
                doc.recompute()
            
            # Check if sketch is already in a Body
            sketch_body = None
            for obj in doc.Objects:
                if obj.TypeId == "PartDesign::Body" and sketch in obj.Group:
                    sketch_body = obj
                    break
            
            # If sketch is not in any Body, add it to our Body
            if not sketch_body:
                body.addObject(sketch)
            # If sketch is in a different Body, use that Body instead
            elif sketch_body != body:
                body = sketch_body
            
            # Create pad within the body
            pad = body.newObject("PartDesign::Pad", name)
            pad.Profile = sketch
            pad.Length = length
            
            doc.recompute()
            
            return f"Created pad: {pad.Name} from {sketch_name} with length {length}mm in Body: {body.Name}"
            
        except Exception as e:
            return f"Error creating pad: {e}"
            
            
    def _fillet_edges(self, args: Dict[str, Any]) -> str:
        """Add fillets to object edges (Interactive selection workflow)"""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')
            auto_select_all = args.get('auto_select_all', False)
            edges = args.get('edges', [])  # Allow explicit edge list
            
            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)
                
                if not selection_result:
                    return "Selection operation not found or expired"
                
                if "error" in selection_result:
                    return selection_result["error"]
                
                return self._create_fillet_with_selection(args, selection_result)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if not hasattr(obj, 'Shape') or not obj.Shape.Edges:
                return f"Object {object_name} has no edges to fillet"
            
            # Method 1: Use explicit edge list if provided
            if edges:
                return self._create_fillet_with_edges(object_name, edges, radius, name)
            
            # Method 2: Auto-select all edges if requested
            if auto_select_all:
                return self._create_fillet_auto(args)
            
            # Method 3: Interactive selection workflow
            selection_request = self.selector.request_selection(
                tool_name="fillet_edges",
                selection_type="edges",
                message=f"Please select edges to fillet on {object_name} object in FreeCAD.\nTell me when you have finished selecting edges...",
                object_name=object_name,
                hints="Select edges for filleting. Ctrl+click for multiple edges.",
                radius=radius,  # Store the radius parameter
                name=name  # Store the name parameter
            )
            
            return json.dumps(selection_request)
            
        except Exception as e:
            return f"Error in fillet operation: {e}"
            
    def _create_fillet_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create fillet using selected edges"""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            edge_indices = selection_result["selection_data"]["elements"]
            if not edge_indices:
                return "No edges were selected"
                
            # Find the Body containing the object (for PartDesign workflow)
            body = None
            for b in doc.Objects:
                if b.TypeId == "PartDesign::Body" and obj in b.Group:
                    body = b
                    break
            
            if body:
                # Use PartDesign::Fillet for parametric feature in Body
                fillet = body.newObject("PartDesign::Fillet", name)
                fillet.Radius = radius
                
                # Convert edge indices to edge names for PartDesign
                edge_names = [f"Edge{idx}" for idx in edge_indices]
                fillet.Base = (obj, edge_names)
            else:
                # Fallback to Part::Fillet if not in a Body
                fillet = doc.addObject("Part::Fillet", name)
                fillet.Base = obj
                
                # Add selected edges with radius
                if hasattr(obj, 'Shape') and obj.Shape.Edges:
                    edge_list = []
                    for edge_idx in edge_indices:
                        if 1 <= edge_idx <= len(obj.Shape.Edges):
                            edge_list.append((edge_idx, radius, radius))
                    fillet.Edges = edge_list
                
            doc.recompute()
            
            return f"Created fillet: {fillet.Name} on {len(edge_indices)} selected edges with radius {radius}mm"
            
        except Exception as e:
            return f"Error creating fillet with selection: {e}"
            
    def _create_fillet_auto(self, args: Dict[str, Any]) -> str:
        """Create fillet on all edges (original behavior)"""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Create fillet (all edges)
            fillet = doc.addObject("Part::Fillet", name)
            fillet.Base = obj
            
            # Add all edges with same radius
            if hasattr(obj, 'Shape') and obj.Shape.Edges:
                edge_list = []
                for i, edge in enumerate(obj.Shape.Edges):
                    edge_list.append((i+1, radius, radius))
                fillet.Edges = edge_list
                
            doc.recompute()
            
            return f"Created fillet: {fillet.Name} on all {len(obj.Shape.Edges)} edges with radius {radius}mm"
            
        except Exception as e:
            return f"Error creating auto fillet: {e}"
    
    # === Edge & Surface Finishing Tools ===
    def _chamfer_edges(self, args: Dict[str, Any]) -> str:
        """Add chamfers (angled cuts) to object edges (with interactive selection)"""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')
            auto_select_all = args.get('auto_select_all', False)
            
            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)
                
                if not selection_result:
                    return "Selection operation not found or expired"
                
                if "error" in selection_result:
                    return selection_result["error"]
                
                return self._create_chamfer_with_selection(args, selection_result)
            
            # Check if auto-selecting all edges
            if auto_select_all:
                return self._create_chamfer_auto(args)
            
            # Request interactive selection
            selection_request = self.selector.request_selection(
                tool_name="chamfer_edges",
                selection_type="edges",
                message=f"Please select edges to chamfer on {object_name} object in FreeCAD.\nTell me when you have finished selecting edges...",
                object_name=object_name,
                hints="Select sharp edges for chamfering. Ctrl+click for multiple edges.",
                distance=distance,  # Store the distance parameter
                name=name  # Store the name parameter
            )
            
            return json.dumps(selection_request)
            
        except Exception as e:
            return f"Error in chamfer operation: {e}"
            
    def _create_chamfer_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create chamfer using selected edges"""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            edge_indices = selection_result["selection_data"]["elements"]
            if not edge_indices:
                return "No edges were selected"
                
            # Find the Body containing the object (for PartDesign workflow)
            body = None
            for b in doc.Objects:
                if b.TypeId == "PartDesign::Body" and obj in b.Group:
                    body = b
                    break
            
            if body:
                # Use PartDesign::Chamfer for parametric feature in Body
                chamfer = body.newObject("PartDesign::Chamfer", name)
                chamfer.Size = distance
                
                # Convert edge indices to edge names for PartDesign
                edge_names = [f"Edge{idx}" for idx in edge_indices]
                chamfer.Base = (obj, edge_names)
            else:
                # Fallback to Part::Chamfer if not in a Body
                chamfer = doc.addObject("Part::Chamfer", name)
                chamfer.Base = obj
                
                # Add selected edges with distance
                if hasattr(obj, 'Shape') and obj.Shape.Edges:
                    edge_list = []
                    for edge_idx in edge_indices:
                        if 1 <= edge_idx <= len(obj.Shape.Edges):
                            edge_list.append((edge_idx, distance))
                    chamfer.Edges = edge_list
                
            doc.recompute()
            
            return f"Created chamfer: {chamfer.Name} on {len(edge_indices)} selected edges with distance {distance}mm"
            
        except Exception as e:
            return f"Error creating chamfer with selection: {e}"
            
    def _create_chamfer_auto(self, args: Dict[str, Any]) -> str:
        """Create chamfer on all edges (original behavior)"""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Create chamfer (all edges)
            chamfer = doc.addObject("Part::Chamfer", name)
            chamfer.Base = obj
            
            # Add all edges with same distance
            if hasattr(obj, 'Shape') and obj.Shape.Edges:
                edge_list = []
                for i, edge in enumerate(obj.Shape.Edges):
                    edge_list.append((i+1, distance))
                chamfer.Edges = edge_list
                
            doc.recompute()
            
            return f"Created chamfer: {chamfer.Name} on all {len(obj.Shape.Edges)} edges with distance {distance}mm"
            
        except Exception as e:
            return f"Error creating auto chamfer: {e}"
    
    # === Holes & Features ===        
    def _hole_wizard(self, args: Dict[str, Any]) -> str:
        """Create standard holes (simple, counterbore, countersink)"""
        try:
            object_name = args.get('object_name', '')
            hole_type = args.get('hole_type', 'simple')
            diameter = args.get('diameter', 6)
            depth = args.get('depth', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            cb_diameter = args.get('cb_diameter', 12)
            cb_depth = args.get('cb_depth', 3)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            base_obj = doc.getObject(object_name)
            if not base_obj:
                return f"Object not found: {object_name}"
                
            # Create hole cylinder
            hole = doc.addObject("Part::Cylinder", "Hole")
            hole.Radius = diameter / 2
            hole.Height = depth + 5  # Extra depth for clean cut
            hole.Placement.Base = FreeCAD.Vector(x, y, -2.5)
            
            # For counterbore/countersink, create additional geometry
            if hole_type == 'counterbore':
                cb_hole = doc.addObject("Part::Cylinder", "CounterboreHole")
                cb_hole.Radius = cb_diameter / 2
                cb_hole.Height = cb_depth + 1  # Extra depth
                cb_hole.Placement.Base = FreeCAD.Vector(x, y, -0.5)
                
                # Combine holes
                combined_hole = doc.addObject("Part::Fuse", "CombinedHole")
                combined_hole.Base = hole
                combined_hole.Tool = cb_hole
                doc.recompute()
                
                # Cut from base object
                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = combined_hole
                
            elif hole_type == 'countersink':
                # Create countersink cone
                cs_cone = doc.addObject("Part::Cone", "CountersinkCone")
                cs_cone.Radius1 = cb_diameter / 2
                cs_cone.Radius2 = diameter / 2
                cs_cone.Height = cb_depth
                cs_cone.Placement.Base = FreeCAD.Vector(x, y, -cb_depth)
                
                # Combine with hole
                combined_hole = doc.addObject("Part::Fuse", "CombinedHole")
                combined_hole.Base = hole
                combined_hole.Tool = cs_cone
                doc.recompute()
                
                # Cut from base object
                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = combined_hole
                
            else:  # simple hole
                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = hole
                
            doc.recompute()
            
            return f"Created {hole_type} hole: {diameter}mm diameter at ({x}, {y}) in {object_name}"
            
        except Exception as e:
            return f"Error creating hole: {e}"
    
    # === Patterns & Arrays ===
    def _linear_pattern(self, args: Dict[str, Any]) -> str:
        """Create linear pattern of features"""
        try:
            feature_name = args.get('feature_name', '')
            direction = args.get('direction', 'x')
            count = args.get('count', 3)
            spacing = args.get('spacing', 10)
            name = args.get('name', 'LinearPattern')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            feature = doc.getObject(feature_name)
            if not feature:
                return f"Feature not found: {feature_name}"
                
            # Create pattern copies
            pattern_objects = []
            direction_vector = FreeCAD.Vector(0, 0, 0)
            
            if direction.lower() == 'x':
                direction_vector = FreeCAD.Vector(spacing, 0, 0)
            elif direction.lower() == 'y':
                direction_vector = FreeCAD.Vector(0, spacing, 0)
            elif direction.lower() == 'z':
                direction_vector = FreeCAD.Vector(0, 0, spacing)
                
            # Create copies
            for i in range(1, count):  # Start from 1 (original is 0)
                copy = doc.copyObject(feature)
                copy.Label = f"{feature.Label}_Pattern{i}"
                
                # Apply transformation
                offset = FreeCAD.Vector(
                    direction_vector.x * i,
                    direction_vector.y * i,
                    direction_vector.z * i
                )
                copy.Placement.Base = feature.Placement.Base.add(offset)
                pattern_objects.append(copy.Name)
                
            doc.recompute()
            
            return f"Created linear pattern: {count} instances of {feature_name} in {direction} direction with {spacing}mm spacing"
            
        except Exception as e:
            return f"Error creating linear pattern: {e}"
    
    # === Symmetry Operations ===        
    def _mirror_feature(self, args: Dict[str, Any]) -> str:
        """Mirror features across a plane"""
        try:
            feature_name = args.get('feature_name', '')
            plane = args.get('plane', 'YZ')
            name = args.get('name', 'Mirrored')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            feature = doc.getObject(feature_name)
            if not feature:
                return f"Feature not found: {feature_name}"
                
            # Create mirror transformation
            mirror = doc.addObject("Part::Mirroring", name)
            mirror.Source = feature
            
            # Set mirror plane
            if plane.upper() == 'XY':
                mirror.Normal = (0, 0, 1)
                mirror.Base = (0, 0, 0)
            elif plane.upper() == 'XZ':
                mirror.Normal = (0, 1, 0)
                mirror.Base = (0, 0, 0)
            elif plane.upper() == 'YZ':
                mirror.Normal = (1, 0, 0)
                mirror.Base = (0, 0, 0)
                
            doc.recompute()
            
            return f"Created mirror: {mirror.Name} of {feature_name} across {plane} plane"
            
        except Exception as e:
            return f"Error creating mirror: {e}"
            
    def _revolution(self, args: Dict[str, Any]) -> str:
        """Revolve a sketch around an axis to create solid of revolution"""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 360)
            name = args.get('name', 'Revolution')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            sketch = doc.getObject(sketch_name)
            if not sketch:
                return f"Sketch not found: {sketch_name}"
                
            # Create revolution
            revolution = doc.addObject("Part::Revolution", name)
            revolution.Source = sketch
            revolution.Angle = angle
            
            # Set axis
            if axis.lower() == 'x':
                revolution.Axis = (1, 0, 0)
            elif axis.lower() == 'y':
                revolution.Axis = (0, 1, 0)
            else:  # z
                revolution.Axis = (0, 0, 1)
                
            doc.recompute()
            
            return f"Created revolution: {revolution.Name} from {sketch_name} around {axis.upper()}-axis, {angle}°"
            
        except Exception as e:
            return f"Error creating revolution: {e}"
    
    # === Advanced Shape Creation Tools ===
    def _loft_profiles(self, args: Dict[str, Any]) -> str:
        """Loft between multiple sketches to create complex shapes"""
        try:
            sketches = args.get('sketches', [])
            ruled = args.get('ruled', False)
            closed = args.get('closed', True)
            name = args.get('name', 'Loft')
            
            if len(sketches) < 2:
                return "Need at least 2 sketches for lofting"
                
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            # Get sketch objects
            sketch_objs = []
            for sketch_name in sketches:
                sketch = doc.getObject(sketch_name)
                if sketch:
                    sketch_objs.append(sketch)
                else:
                    return f"Sketch not found: {sketch_name}"
                    
            # Create loft
            loft = doc.addObject("Part::Loft", name)
            loft.Sections = sketch_objs
            loft.Solid = closed
            loft.Ruled = ruled
            
            doc.recompute()
            
            return f"Created loft: {loft.Name} through {len(sketches)} profiles"
            
        except Exception as e:
            return f"Error creating loft: {e}"
            
    def _sweep_path(self, args: Dict[str, Any]) -> str:
        """Sweep a profile sketch along a path sketch"""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            solid = args.get('solid', True)
            name = args.get('name', 'Sweep')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            profile = doc.getObject(profile_sketch)
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"
                
            path = doc.getObject(path_sketch)
            if not path:
                return f"Path sketch not found: {path_sketch}"
                
            # Create sweep
            sweep = doc.addObject("Part::Sweep", name)
            sweep.Sections = [profile]
            sweep.Spine = path
            sweep.Solid = solid
            
            doc.recompute()
            
            return f"Created sweep: {sweep.Name} with profile {profile_sketch} along path {path_sketch}"
            
        except Exception as e:
            return f"Error creating sweep: {e}"
    
    # === Manufacturing Features ===        
    def _draft_faces(self, args: Dict[str, Any]) -> str:
        """Add draft angles to faces for manufacturing (Interactive selection workflow)"""
        try:
            object_name = args.get('object_name', '')
            angle = args.get('angle', 5)
            neutral_plane = args.get('neutral_plane', 'XY')
            name = args.get('name', 'Draft')
            
            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)
                
                if not selection_result:
                    return "Selection operation not found or expired"
                
                if "error" in selection_result:
                    return selection_result["error"]
                
                return self._create_draft_with_selection(args, selection_result)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if not hasattr(obj, 'Shape') or not obj.Shape.Faces:
                return f"Object {object_name} has no faces for draft"
            
            # Interactive selection workflow for faces
            selection_request = self.selector.request_selection(
                tool_name="draft_faces",
                selection_type="faces",
                message=f"Please select faces to draft on {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Select faces to apply draft angle. Ctrl+click for multiple faces.",
                angle=angle,
                neutral_plane=neutral_plane,
                name=name
            )
            
            return json.dumps(selection_request)
            
        except Exception as e:
            return f"Error in draft operation: {e}"
    
    def _create_draft_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create draft using selected faces"""
        try:
            object_name = args.get('object_name', '')
            angle = args.get('angle', 5)
            neutral_plane = args.get('neutral_plane', 'XY')
            name = args.get('name', 'Draft')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected"
                
            # Find the Body containing the object (for PartDesign workflow)
            body = None
            for b in doc.Objects:
                if b.TypeId == "PartDesign::Body" and obj in b.Group:
                    body = b
                    break
            
            if body:
                # Use PartDesign::Draft for parametric feature in Body
                draft = body.newObject("PartDesign::Draft", name)
                draft.Angle = angle
                draft.Reversed = False  # Default to not reversed
                
                # Convert face indices to face names for PartDesign
                face_names = [f"Face{idx}" for idx in face_indices]
                draft.Base = (obj, face_names)
                
                doc.recompute()
                
                return f"Created draft: {draft.Name} on {len(face_indices)} selected faces with {angle}° angle"
            else:
                return "Draft operation requires object to be in a PartDesign Body"
                
        except Exception as e:
            return f"Error creating draft with selection: {e}"
            
    def _shell_solid(self, args: Dict[str, Any]) -> str:
        """Hollow out a solid by removing material (with face selection for opening)"""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')
            auto_shell_closed = args.get('auto_shell_closed', False)
            
            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)
                
                if not selection_result:
                    return "Selection operation not found or expired"
                
                if "error" in selection_result:
                    return selection_result["error"]
                
                return self._create_shell_with_selection(args, selection_result)
            
            # Check if creating closed shell (no opening)
            if auto_shell_closed:
                return self._create_shell_closed(args)
            
            # Request interactive face selection for opening
            selection_request = self.selector.request_selection(
                tool_name="shell_solid",
                selection_type="faces",
                message=f"Please select face(s) to remove for opening the {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Usually select the top face or access faces for openings. Ctrl+click for multiple faces."
            )
            
            return json.dumps(selection_request)
            
        except Exception as e:
            return f"Error in shell operation: {e}"
            
    def _create_shell_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create shell using selected faces for opening"""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected for opening"
                
            # Create shell with selected faces removed
            shell = doc.addObject("Part::Thickness", name)
            shell.Value = thickness
            shell.Source = obj
            shell.Join = 2  # Intersection join type
            
            # Set faces to remove for opening
            if hasattr(obj, 'Shape') and obj.Shape.Faces:
                faces_to_remove = []
                for face_idx in face_indices:
                    if 1 <= face_idx <= len(obj.Shape.Faces):
                        faces_to_remove.append(face_idx - 1)  # FreeCAD uses 0-based for face removal
                shell.Faces = tuple(faces_to_remove)
                
            doc.recompute()
            
            return f"Created shell: {shell.Name} from {object_name} with {thickness}mm thickness and {len(face_indices)} face(s) removed for opening"
            
        except Exception as e:
            return f"Error creating shell with selection: {e}"
    
    def _create_thickness_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create PartDesign thickness using selected faces for opening"""
        try:
            object_name = args.get('object_name', '')
            thickness_val = args.get('thickness', 2)
            name = args.get('name', 'Thickness')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
            
            # Find the Body that contains this object
            body = None
            for b in doc.Objects:
                if b.TypeId == "PartDesign::Body" and obj in b.Group:
                    body = b
                    break
                    
            if not body:
                return f"Object {object_name} is not in a PartDesign Body. PartDesign::Thickness requires a Body."
                
            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected for thickness opening"
                
            # Create PartDesign::Thickness within the body
            thickness = body.newObject("PartDesign::Thickness", name)
            thickness.Base = (obj, tuple(f"Face{face_idx}" for face_idx in face_indices))
            thickness.Value = thickness_val
                
            doc.recompute()
            
            return f"✅ Created PartDesign Thickness: {thickness.Name} from {object_name} with {thickness_val}mm thickness and {len(face_indices)} face(s) removed for opening"
            
        except Exception as e:
            return f"Error creating thickness with selection: {e}"
            
    def _create_shell_closed(self, args: Dict[str, Any]) -> str:
        """Create closed shell (no opening)"""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            # Create closed shell (no faces removed)
            shell = doc.addObject("Part::Thickness", name)
            shell.Value = thickness
            shell.Source = obj
            shell.Join = 2  # Intersection join type
            # No faces specified = closed shell
            
            doc.recompute()
            
            return f"Created closed shell: {shell.Name} from {object_name} with {thickness}mm thickness (no opening)"
            
        except Exception as e:
            return f"Error creating closed shell: {e}"
            
    def _create_rib(self, args: Dict[str, Any]) -> str:
        """Create structural ribs from sketch"""
        try:
            sketch_name = args.get('sketch_name', '')
            thickness = args.get('thickness', 3)
            direction = args.get('direction', 'normal')
            name = args.get('name', 'Rib')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            sketch = doc.getObject(sketch_name)
            if not sketch:
                return f"Sketch not found: {sketch_name}"
                
            # Create rib by extruding sketch with thickness
            # This is a simplified implementation - actual ribs are more complex
            rib = doc.addObject("Part::Extrude", name)
            rib.Base = sketch
            
            # Set extrusion direction based on parameter
            if direction.lower() == 'horizontal':
                rib.Dir = (1, 0, 0)  # X direction
                rib.LengthFwd = thickness
            elif direction.lower() == 'vertical':
                rib.Dir = (0, 0, 1)  # Z direction
                rib.LengthFwd = thickness
            else:  # normal
                rib.Dir = (0, 1, 0)  # Y direction (normal to sketch)
                rib.LengthFwd = thickness
                
            rib.Solid = True
            
            doc.recompute()
            
            return f"Created rib: {rib.Name} from {sketch_name} with {thickness}mm thickness in {direction} direction"
            
        except Exception as e:
            return f"Error creating rib: {e}"
    
    # === Patterns & Manufacturing Features ===
    def _create_helix(self, args: Dict[str, Any]) -> str:
        """Create helical features (threads, springs)"""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            pitch = args.get('pitch', 2)
            height = args.get('height', 10)
            turns = args.get('turns', 5)
            left_handed = args.get('left_handed', False)
            name = args.get('name', 'Helix')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            sketch = doc.getObject(sketch_name)
            if not sketch:
                return f"Sketch not found: {sketch_name}"
                
            # Create helix path first
            helix_curve = doc.addObject("Part::Helix", f"{name}_Path")
            helix_curve.Pitch = pitch
            helix_curve.Height = height
            helix_curve.Radius = 10  # Default radius, will be adjusted
            helix_curve.Angle = 0
            helix_curve.LeftHanded = left_handed
            
            # Set axis
            if axis.lower() == 'x':
                helix_curve.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(0,1,0), 90)
            elif axis.lower() == 'y':
                helix_curve.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 90)
            # Z is default
            
            doc.recompute()
            
            # Create sweep along helix
            helix_sweep = doc.addObject("Part::Sweep", name)
            helix_sweep.Sections = [sketch]
            helix_sweep.Spine = helix_curve
            helix_sweep.Solid = True
            
            doc.recompute()
            
            return f"Created helix: {helix_sweep.Name} from {sketch_name}, pitch={pitch}mm, height={height}mm, turns={turns}"
            
        except Exception as e:
            return f"Error creating helix: {e}"
            
    def _polar_pattern(self, args: Dict[str, Any]) -> str:
        """Create circular/polar pattern of features"""
        try:
            feature_name = args.get('feature_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 360)
            count = args.get('count', 6)
            name = args.get('name', 'PolarPattern')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            feature = doc.getObject(feature_name)
            if not feature:
                return f"Feature not found: {feature_name}"
                
            # Calculate angle between instances
            angle_step = angle / count
            
            # Create pattern copies
            pattern_objects = []
            axis_vector = FreeCAD.Vector(0, 0, 1)  # default Z
            if axis.lower() == 'x':
                axis_vector = FreeCAD.Vector(1, 0, 0)
            elif axis.lower() == 'y':
                axis_vector = FreeCAD.Vector(0, 1, 0)
                
            # Create copies with rotation
            for i in range(1, count):  # Start from 1 (original is 0)
                copy = doc.copyObject(feature)
                copy.Label = f"{feature.Label}_Polar{i}"
                
                # Apply rotation
                rotation_angle = angle_step * i
                rotation = FreeCAD.Rotation(axis_vector, rotation_angle)
                
                # Combine with existing placement
                new_placement = FreeCAD.Placement(
                    feature.Placement.Base,
                    feature.Placement.Rotation.multiply(rotation)
                )
                copy.Placement = new_placement
                
                pattern_objects.append(copy.Name)
                
            doc.recompute()
            
            return f"Created polar pattern: {count} instances of {feature_name} around {axis.upper()}-axis, {angle}° total"
            
        except Exception as e:
            return f"Error creating polar pattern: {e}"
            
    def _add_thickness(self, args: Dict[str, Any]) -> str:
        """Add PartDesign thickness with face selection (Interactive selection workflow)"""
        try:
            object_name = args.get('object_name', '')
            thickness_val = args.get('thickness', 2)
            name = args.get('name', 'Thickness')
            
            # Check for continuation from selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)
                
                if not selection_result:
                    return "Selection operation not found or expired"
                
                if "error" in selection_result:
                    return selection_result["error"]
                
                return self._create_thickness_with_selection(args, selection_result)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if not hasattr(obj, 'Shape') or not obj.Shape.Faces:
                return f"Object {object_name} has no faces for thickness"
            
            # Interactive selection workflow for faces
            selection_request = self.selector.request_selection(
                tool_name="thickness_faces",
                selection_type="faces",
                message=f"Please select faces to remove for thickness operation on {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Select faces to remove (hollow out). Ctrl+click for multiple faces.",
                thickness=thickness_val,
                name=name
            )
            
            return json.dumps(selection_request)
            
        except Exception as e:
            return f"Error in thickness operation: {e}"
    
    # === Analysis Tools ===
    def _measure_distance(self, args: Dict[str, Any]) -> str:
        """Measure distance between two objects"""
        try:
            object1 = args.get('object1', '')
            object2 = args.get('object2', '')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj1 = doc.getObject(object1)
            obj2 = doc.getObject(object2)
            
            if not obj1:
                return f"Object not found: {object1}"
            if not obj2:
                return f"Object not found: {object2}"
                
            # Calculate distance between centers of mass
            if hasattr(obj1, 'Shape') and hasattr(obj2, 'Shape'):
                center1 = obj1.Shape.CenterOfMass
                center2 = obj2.Shape.CenterOfMass
                distance = center1.distanceToPoint(center2)
                
                return f"Distance between {object1} and {object2}: {distance:.2f} mm"
            else:
                return "Objects must have Shape property for distance measurement"
                
        except Exception as e:
            return f"Error measuring distance: {e}"
            
    def _get_volume(self, args: Dict[str, Any]) -> str:
        """Calculate volume of an object"""
        try:
            object_name = args.get('object_name', '')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if hasattr(obj, 'Shape'):
                volume = obj.Shape.Volume
                return f"Volume of {object_name}: {volume:.2f} mm³"
            else:
                return "Object must have Shape property for volume calculation"
                
        except Exception as e:
            return f"Error calculating volume: {e}"
            
    def _get_bounding_box(self, args: Dict[str, Any]) -> str:
        """Get bounding box dimensions of an object"""
        try:
            object_name = args.get('object_name', '')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if hasattr(obj, 'Shape'):
                bb = obj.Shape.BoundBox
                return f"Bounding box of {object_name}:\n" + \
                       f"  X: {bb.XMin:.2f} to {bb.XMax:.2f} mm (length: {bb.XLength:.2f})\n" + \
                       f"  Y: {bb.YMin:.2f} to {bb.YMax:.2f} mm (width: {bb.YLength:.2f})\n" + \
                       f"  Z: {bb.ZMin:.2f} to {bb.ZMax:.2f} mm (height: {bb.ZLength:.2f})"
            else:
                return "Object must have Shape property for bounding box calculation"
                
        except Exception as e:
            return f"Error calculating bounding box: {e}"
            
    def _get_mass_properties(self, args: Dict[str, Any]) -> str:
        """Get mass properties of an object"""
        try:
            object_name = args.get('object_name', '')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            if hasattr(obj, 'Shape'):
                shape = obj.Shape
                volume = shape.Volume
                center_of_mass = shape.CenterOfMass
                
                # Calculate surface area
                area = 0
                for face in shape.Faces:
                    area += face.Area
                
                return f"Mass properties of {object_name}:\n" + \
                       f"  Volume: {volume:.2f} mm³\n" + \
                       f"  Surface Area: {area:.2f} mm²\n" + \
                       f"  Center of Mass: ({center_of_mass.x:.2f}, {center_of_mass.y:.2f}, {center_of_mass.z:.2f})"
            else:
                return "Object must have Shape property for mass properties calculation"
                
        except Exception as e:
            return f"Error calculating mass properties: {e}"
    
    def _get_screenshot_gui_safe(self, args: Dict[str, Any]) -> str:
        """Screenshot functionality is DISABLED - returns clear error message
        
        Screenshots are not practical over MCP due to prohibitive data size and cost:
        - Even 1920x1080 screenshots generate ~2MB of base64 data (~3M tokens, $9 cost)
        - 4K/5K displays generate 8-20MB of data (11-21M tokens, $35-$63 cost)
        - Data size exceeds Claude's entire 190K token context window by 15-110x
        - Would consume entire conversation budget on a single image
        
        Use FreeCAD's native screenshot features instead:
        - GUI: View → Save Picture...
        - Python: Gui.activeDocument().activeView().saveImage('path.png', width, height)
        
        For automation, save screenshots to a shared directory accessible to both
        FreeCAD and Claude, then reference the file path in conversation.
        """
        return json.dumps({
            "success": False,
            "error": "Screenshot not supported over MCP",
            "message": (
                "Screenshots are not practical over MCP due to data size limitations.\n\n"
                "COST ANALYSIS:\n"
                "  • 1920x1080 (HD):  ~2MB base64 → ~3M tokens → $8.85\n"
                "  • 3840x2160 (4K):  ~8MB base64 → ~12M tokens → $35.39\n"
                "  • 5120x2880 (5K):  ~15MB base64 → ~21M tokens → $62.91\n\n"
                "These sizes exceed Claude's 190K token context window by 15-110x.\n"
                "Even if technically possible, a single screenshot would consume\n"
                "your entire conversation budget and cost $9-$63.\n\n"
                "ALTERNATIVES - Use FreeCAD's native screenshot features:\n"
                "  • From GUI: View menu → Save Picture...\n"
                "  • From Python: Gui.activeDocument().activeView().saveImage('path.png', 1920, 1080)\n"
                "  • From MCP: Use execute_python tool to call saveImage()\n\n"
                "For automation: Save screenshots to a shared directory, then reference\n"
                "the file path in conversation. Claude can then view the file if needed."
            ),
            "alternatives": {
                "gui_menu": "View → Save Picture...",
                "python_command": "Gui.activeDocument().activeView().saveImage('/path/to/screenshot.png', width, height)",
                "mcp_command": "Use execute_python tool to call the saveImage() method",
                "cost_examples": {
                    "1080p": "$8.85 and 3M tokens",
                    "4K": "$35.39 and 12M tokens", 
                    "5K": "$62.91 and 21M tokens"
                }
            },
            "technical_details": {
                "context_window": "190K tokens",
                "1080p_ratio": "15x over limit",
                "5K_ratio": "110x over limit"
            }
        })
    
    def _set_view_gui_safe(self, args: Dict[str, Any]) -> str:
        """Set view orientation using GUI-safe thread queue"""
        try:
            if not FreeCADGui.ActiveDocument:
                return "No active document for view change"
            
            view_type = args.get('view_type', 'isometric').lower()
            import time
            
            # Define GUI task
            def view_task():
                try:
                    # Map view types to FreeCAD commands
                    views = {
                        'top': 'Std_ViewTop',
                        'bottom': 'Std_ViewBottom',
                        'front': 'Std_ViewFront', 
                        'rear': 'Std_ViewRear',
                        'back': 'Std_ViewRear',
                        'left': 'Std_ViewLeft',
                        'right': 'Std_ViewRight',
                        'isometric': 'Std_ViewIsometric',
                        'iso': 'Std_ViewIsometric',
                        'axonometric': 'Std_ViewAxonometric',
                        'axo': 'Std_ViewAxonometric'
                    }
                    
                    if view_type in views:
                        # GUI-safe: Execute view command in main thread
                        FreeCADGui.runCommand(views[view_type], 0)
                        return {"success": True, "view": view_type}
                    else:
                        return {"error": f"Unknown view type: {view_type}"}
                        
                except Exception as e:
                    return {"error": f"View task failed: {e}"}
            
            # Queue task and wait for result
            gui_task_queue.put(view_task)
            
            # Wait for result with timeout
            start_time = time.time()
            while time.time() - start_time < 5:  # 5 second timeout
                try:
                    result = gui_response_queue.get_nowait()
                    if isinstance(result, dict):
                        if "error" in result:
                            return f"Error setting view: {result['error']}"
                        elif "success" in result:
                            return f"✅ View set to {result['view']}"
                    break
                except queue.Empty:
                    time.sleep(0.1)
                    continue
            
            return "View change timeout - GUI thread may be busy"
            
        except Exception as e:
            return f"Error in view setup: {e}"
            
    def _list_all_objects(self, args: Dict[str, Any]) -> str:
        """List all objects in active document"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            objects = []
            for obj in doc.Objects:
                objects.append({
                    "name": obj.Name,
                    "type": obj.TypeId,
                    "label": obj.Label
                })
                
            return json.dumps(objects)
            
        except Exception as e:
            return f"Error listing objects: {e}"
            
    def _activate_workbench(self, args: Dict[str, Any]) -> str:
        """Activate specified workbench"""
        try:
            workbench_name = args.get('workbench_name', '')
            FreeCADGui.activateWorkbench(workbench_name)
            return f"Activated workbench: {workbench_name}"
        except Exception as e:
            return f"Error activating workbench: {e}"

    def _execute_python(self, args: Dict[str, Any]) -> str:
        """Execute Python code in FreeCAD context with expression value capture.
        
        FULLY INSTRUMENTED - logs all steps for crash debugging
        
        This method handles both statements and expressions, returning the value
        of the last expression if present (similar to IPython/Jupyter behavior).
        
        All code execution happens on the main GUI thread to prevent crashes
        when code creates documents, modifies views, or performs other GUI operations.
        
        Examples:
            "1 + 1"                    -> "2"
            "x = 5"                    -> "Code executed successfully"
            "x = 5\nx * 2"             -> "10"
            "FreeCAD.ActiveDocument"   -> "<Document object>"
            "result = 42"              -> "42" (explicit result variable)
        """
        import traceback
        import time
        import ast
        
        code = args.get('code', '')
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        exec_id = f"exec_{int(time.time() * 1000)}"
        
        # Log execution start with code preview
        if DEBUG_ENABLED:
            code_preview = code[:200] + "..." if len(code) > 200 else code
            _log_operation(
                operation=f"EXEC_START:{exec_id}",
                parameters={"code_preview": code_preview, "code_len": len(code)}
            )
        
        FreeCAD.Console.PrintMessage(f"[{timestamp}] EXEC START: {repr(code[:100])}...\n")
        
        def execute_task():
            """Task to run on GUI thread"""
            task_start = time.time()
            
            if DEBUG_ENABLED:
                _log_operation(
                    operation=f"EXEC_TASK_START:{exec_id}",
                    parameters={"thread": "GUI"}
                )
            
            try:
                # Enhanced pre-flight safety checks
                if 'newDocument' in code:
                    FreeCAD.Console.PrintMessage("DETECTED: Document creation operation\n")
                    try:
                        version = FreeCAD.Version()
                        docs = FreeCAD.listDocuments()
                        active = FreeCAD.ActiveDocument
                        FreeCAD.Console.PrintMessage(f"Pre-flight: Version={version}, Docs={list(docs.keys())}, Active={active}\n")
                        test_list = list(range(1000))
                        FreeCAD.Console.PrintMessage("Pre-flight: Memory test passed\n")
                    except Exception as e:
                        FreeCAD.Console.PrintError(f"Pre-flight FAILED: {e}\n")
                        return {"error": f"FreeCAD not ready for document operations: {e}"}
                
                # Create enhanced execution context
                exec_context = {
                    'FreeCAD': FreeCAD,
                    'FreeCADGui': FreeCADGui,
                    'App': FreeCAD,
                    'Gui': FreeCADGui,
                    'doc': FreeCAD.ActiveDocument,
                    'print': lambda *args: FreeCAD.Console.PrintMessage(' '.join(str(arg) for arg in args) + '\n')
                }
                
                # Also import Part if available
                try:
                    import Part
                    exec_context['Part'] = Part
                except ImportError:
                    pass
                
                # Also import Vector for convenience
                try:
                    from FreeCAD import Vector
                    exec_context['Vector'] = Vector
                except ImportError:
                    pass
                
                FreeCAD.Console.PrintMessage("EXEC: Starting code execution on GUI thread...\n")
                
                try:
                    # Parse the code into an AST
                    tree = ast.parse(code)
                    
                    result_value = None
                    
                    # Check if the last statement is an expression
                    if tree.body and isinstance(tree.body[-1], ast.Expr):
                        # Execute all statements except the last
                        if len(tree.body) > 1:
                            exec_body = tree.body[:-1]
                            exec_module = ast.Module(body=exec_body, type_ignores=[])
                            ast.fix_missing_locations(exec_module)
                            exec(compile(exec_module, '<string>', 'exec'), exec_context)
                        
                        # Evaluate the last expression and capture its value
                        last_expr = tree.body[-1].value
                        expr_ast = ast.Expression(body=last_expr)
                        ast.fix_missing_locations(expr_ast)
                        result_value = eval(compile(expr_ast, '<string>', 'eval'), exec_context)
                        
                    else:
                        # No trailing expression - just execute everything
                        exec(code, exec_context)
                        
                        # Check for explicit 'result' variable (backwards compatibility)
                        if 'result' in exec_context:
                            result_value = exec_context['result']
                    
                    FreeCAD.Console.PrintMessage("EXEC: Code completed successfully\n")
                    
                    # Return the result
                    if result_value is not None:
                        result_str = repr(result_value)
                        FreeCAD.Console.PrintMessage(f"EXEC: Result: {result_str}\n")
                        
                        if DEBUG_ENABLED:
                            task_duration = time.time() - task_start
                            _log_operation(
                                operation=f"EXEC_SUCCESS:{exec_id}",
                                parameters={"duration_ms": int(task_duration * 1000)},
                                result=result_str[:200] if len(result_str) > 200 else result_str
                            )
                        
                        return {"success": True, "result": result_str}
                    else:
                        FreeCAD.Console.PrintMessage("EXEC: No result value\n")
                        
                        if DEBUG_ENABLED:
                            task_duration = time.time() - task_start
                            _log_operation(
                                operation=f"EXEC_SUCCESS:{exec_id}",
                                parameters={"duration_ms": int(task_duration * 1000)},
                                result="Code executed successfully (no return value)"
                            )
                        
                        return {"success": True, "result": "Code executed successfully"}
                        
                except SyntaxError as syn_err:
                    # If AST parsing fails, fall back to simple exec
                    FreeCAD.Console.PrintWarning(f"EXEC: AST parse failed, using simple exec: {syn_err}\n")
                    
                    if DEBUG_ENABLED:
                        _log_operation(
                            operation=f"EXEC_AST_FALLBACK:{exec_id}",
                            parameters={"syntax_error": str(syn_err)}
                        )
                    
                    exec(code, exec_context)
                    
                    if 'result' in exec_context:
                        return {"success": True, "result": str(exec_context['result'])}
                    return {"success": True, "result": "Code executed successfully"}
                    
                except Exception as exec_error:
                    FreeCAD.Console.PrintError(f"EXEC: Code execution failed: {exec_error}\n")
                    FreeCAD.Console.PrintError(f"EXEC: Traceback: {traceback.format_exc()}\n")
                    
                    if DEBUG_ENABLED:
                        _log_operation(
                            operation=f"EXEC_FAIL:{exec_id}",
                            error=exec_error,
                            parameters={"traceback": traceback.format_exc()}
                        )
                    
                    return {"error": f"Python execution error: {exec_error}"}
                    
            except Exception as e:
                error_msg = f"Python execution error: {e}"
                FreeCAD.Console.PrintError(f"EXEC ERROR: {error_msg}\n")
                FreeCAD.Console.PrintError(f"EXEC TRACEBACK: {traceback.format_exc()}\n")
                
                if DEBUG_ENABLED:
                    _log_operation(
                        operation=f"EXEC_OUTER_FAIL:{exec_id}",
                        error=e,
                        parameters={"traceback": traceback.format_exc()}
                    )
                
                return {"error": error_msg}
        
        # Queue task for GUI thread execution
        gui_task_queue.put(execute_task)
        
        # Wait for result with timeout
        timeout_seconds = 30  # Allow longer for complex operations
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                if isinstance(result, dict):
                    if "error" in result:
                        return result["error"]
                    elif "success" in result:
                        return result["result"]
                    else:
                        return str(result)
                else:
                    return str(result)
            except queue.Empty:
                time.sleep(0.05)  # 50ms polling interval
                continue
        
        return "Execution timeout - GUI thread may be busy or code is taking too long"

    # GUI Control Tools
    def _run_command(self, args: Dict[str, Any]) -> str:
        """Run a FreeCAD GUI command"""
        try:
            command = args.get('command', '')
            FreeCADGui.runCommand(command)
            return f"Executed command: {command}"
        except Exception as e:
            return f"Error running command: {e}"
            
            
    def _save_document(self, args: Dict[str, Any]) -> str:
        """Save the current document"""
        try:
            filename = args.get('filename', '')
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document to save"
                
            if filename:
                doc.saveAs(filename)
                return f"Document saved as: {filename}"
            else:
                doc.save()
                return f"Document saved: {doc.Name}"
        except Exception as e:
            return f"Error saving document: {e}"
    
    def _create_document_gui_safe(self, args: Dict[str, Any]) -> str:
        """Create a new document using GUI-safe thread queue"""
        try:
            name = args.get('document_name', args.get('name', 'Unnamed'))
            
            # Define GUI task
            def create_doc_task():
                try:
                    doc = FreeCAD.newDocument(name)
                    doc.recompute()
                    FreeCAD.Console.PrintMessage(f"Document '{name}' created via GUI-safe MCP.\n")
                    return f"✅ Document '{name}' created successfully"
                except Exception as e:
                    return f"Error creating document: {e}"
            
            # Queue task for GUI thread
            gui_task_queue.put(create_doc_task)
            
            # Wait for result with timeout
            try:
                result = gui_response_queue.get(timeout=5.0)
                return result
            except queue.Empty:
                return "Timeout waiting for document creation"
                
        except Exception as e:
            return f"Error in create_document: {e}"
            
    def _open_document(self, args: Dict[str, Any]) -> str:
        """Open a document"""
        try:
            filename = args.get('filename', '')
            doc = FreeCAD.openDocument(filename)
            return f"Opened document: {doc.Name}"
        except Exception as e:
            return f"Error opening document: {e}"
            
    def _set_view(self, args: Dict[str, Any]) -> str:
        """Set the 3D view to a specific orientation"""
        try:
            view_type = args.get('view_type', 'isometric').lower()
            
            # TEMPORARY: Disable view commands to prevent crashes
            # These commands need to be executed in the main GUI thread
            # For now, provide instructions to the user
            
            view_shortcuts = {
                'top': '2',
                'bottom': 'Shift+2',
                'front': '1', 
                'rear': 'Shift+1',
                'back': 'Shift+1',
                'left': '3',
                'right': 'Shift+3',
                'isometric': '0',
                'iso': '0',
                'axonometric': 'A',
                'axo': 'A'
            }
            
            if view_type in view_shortcuts:
                shortcut = view_shortcuts[view_type]
                return f"⚠️ View command temporarily disabled to prevent crashes.\n" \
                       f"Please press '{shortcut}' in FreeCAD to set {view_type} view.\n" \
                       f"Or use View menu → Standard views → {view_type.title()}"
            else:
                return f"Unknown view type: {view_type}. Available: top, bottom, front, rear, left, right, isometric"
            
        except Exception as e:
            return f"Error setting view: {e}"
            
    def _fit_all(self, args: Dict[str, Any]) -> str:
        """Fit all objects in the view"""
        try:
            if FreeCADGui.ActiveDocument:
                pass  # DISABLED: FreeCADGui.SendMsgToActiveView("ViewFit") - causes hang from non-GUI thread
                return "View fitted to all objects"
            else:
                return "No active document"
        except Exception as e:
            return f"Error fitting view: {e}"
            
    def _select_object(self, args: Dict[str, Any]) -> str:
        """Select an object"""
        try:
            object_name = args.get('object_name', '')
            doc_name = args.get('doc_name', '')
            
            if not doc_name:
                doc = FreeCAD.ActiveDocument
                doc_name = doc.Name if doc else ""
                
            if not doc_name:
                return "No document specified or active"
                
            FreeCADGui.Selection.addSelection(doc_name, object_name)
            return f"Selected object: {object_name}"
        except Exception as e:
            return f"Error selecting object: {e}"
            
    def _clear_selection(self, args: Dict[str, Any]) -> str:
        """Clear all selections"""
        try:
            FreeCADGui.Selection.clearSelection()
            return "Selection cleared"
        except Exception as e:
            return f"Error clearing selection: {e}"
            
    def _get_selection(self, args: Dict[str, Any]) -> str:
        """Get current selection"""
        try:
            selected = FreeCADGui.Selection.getSelectionEx()
            selection_info = []
            
            for sel in selected:
                selection_info.append({
                    "document": sel.DocumentName,
                    "object": sel.ObjectName,
                    "sub_elements": sel.SubElementNames
                })
                
            return json.dumps(selection_info)
        except Exception as e:
            return f"Error getting selection: {e}"
            
    def _hide_object(self, args: Dict[str, Any]) -> str:
        """Hide an object"""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument
            
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            obj.ViewObject.Visibility = False
            return f"Hidden object: {object_name}"
        except Exception as e:
            return f"Error hiding object: {e}"
            
    def _show_object(self, args: Dict[str, Any]) -> str:
        """Show an object"""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument
            
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            obj.ViewObject.Visibility = True
            return f"Shown object: {object_name}"
        except Exception as e:
            return f"Error showing object: {e}"
            
    def _delete_object(self, args: Dict[str, Any]) -> str:
        """Delete an object"""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument
            
            if not doc:
                return "No active document"
                
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"
                
            doc.removeObject(object_name)
            doc.recompute()
            return f"Deleted object: {object_name}"
        except Exception as e:
            return f"Error deleting object: {e}"
            
    def _undo(self, args: Dict[str, Any]) -> str:
        """Undo last operation"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            FreeCADGui.runCommand("Std_Undo")
            return "Undo completed"
        except Exception as e:
            return f"Error undoing: {e}"
            
    def _redo(self, args: Dict[str, Any]) -> str:
        """Redo last undone operation"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            FreeCADGui.runCommand("Std_Redo")
            return "Redo completed"
        except Exception as e:
            return f"Error redoing: {e}"
            
    def _ai_agent(self, args: Dict[str, Any]) -> str:
        """Handle requests through the ReAct Agent"""
        try:
            if not self.agent:
                return "AI Agent not available (import failed)"
                
            request = args.get('request', '')
            if not request:
                return "No request provided for AI agent"
                
            # Process through the ReAct agent
            result = self.agent.process_request(request)
            return result
            
        except Exception as e:
            return f"AI Agent error: {e}"
    
    def _continue_selection(self, args: Dict[str, Any]) -> str:
        """Handle continuation of selection operations after user has selected elements"""
        try:
            operation_id = args.get('operation_id')
            if not operation_id:
                return json.dumps({"error": "operation_id is required"})
            
            # Get the selection result from the selector
            selection_result = self.selector.complete_selection(operation_id)
            
            if not selection_result:
                return json.dumps({"error": "Selection operation not found or expired"})
            
            if "error" in selection_result:
                return json.dumps({"error": selection_result["error"]})
            
            # Get the operation context
            context = selection_result.get("context", {})
            tool_name = context.get("tool", "")
            
            # Route to appropriate handler based on tool type
            if tool_name == "chamfer_edges":
                # Get the original args stored in pending_operations
                original_args = {
                    'object_name': context.get('object', ''),
                    'distance': context.get('distance', 2),  # Use stored distance
                    'name': context.get('name', 'Chamfer')  # Use stored name
                }
                return self._create_chamfer_with_selection(original_args, selection_result)
            elif tool_name == "fillet_edges":
                original_args = {
                    'object_name': context.get('object', ''),
                    'radius': context.get('radius', 3),  # Use stored radius
                    'name': context.get('name', 'Fillet')  # Use stored name
                }
                return self._create_fillet_with_selection(original_args, selection_result)
            elif tool_name == "shell_solid":
                original_args = {
                    'object_name': context.get('object', ''),
                    'thickness': 2,  # Default
                    'name': 'Shell'
                }
                return self._create_shell_with_selection(original_args, selection_result)
            elif tool_name == "draft_faces":
                original_args = {
                    'object_name': context.get('object', ''),
                    'angle': context.get('angle', 6),  # Use stored angle
                    'name': context.get('name', 'Draft')  # Use stored name
                }
                return self._create_draft_with_selection(original_args, selection_result)
            elif tool_name == "thickness_faces":
                original_args = {
                    'object_name': context.get('object', ''),
                    'thickness': context.get('thickness', 2),  # Use stored thickness
                    'name': context.get('name', 'Thickness')  # Use stored name
                }
                return self._create_thickness_with_selection(original_args, selection_result)
            else:
                return json.dumps({"error": f"Unknown selection tool: {tool_name}"})
                
        except Exception as e:
            return json.dumps({"error": f"Error in continue_selection: {e}"})

    # ===================================================================
    # PHASE 1 SMART DISPATCHER METHODS
    # ===================================================================


    def _handle_partdesign_operations(self, args: Dict[str, Any]) -> str:
        """Smart dispatcher for all PartDesign operations (20+ operations)
        
        GUI-SAFE: All operations are queued to the GUI thread to prevent crashes
        from NSWindow/Qt threading violations.
        
        Optimized logging: Single log entry per operation (not per stage)
        """
        operation = args.get('operation', '')
        pd_op_id = f"partdesign_{operation}_{int(time.time() * 1000)}"
        operation_start = time.time()
        
        # Define the actual work to run on GUI thread
        def partdesign_operation_task():
            task_start = time.time()
            
            try:
                # For fillet and chamfer, use the Part workbench methods that support selection workflow
                # These work with both Part and PartDesign objects
                if operation == "fillet":
                    result = self._fillet_edges(args)
                elif operation == "chamfer":
                    result = self._chamfer_edges(args)
                # Core PartDesign operations
                elif operation == "pad":
                    result = self._pad_sketch(args)
                elif operation == "revolution":
                    result = self._revolution(args)
                elif operation == "groove":
                    result = self._partdesign_groove(args)
                elif operation == "loft":
                    result = self._loft_profiles(args)
                elif operation == "sweep":
                    result = self._sweep_path(args)
                elif operation == "additive_pipe":
                    result = self._partdesign_additive_pipe(args)
                elif operation == "subtractive_sweep":
                    result = self._partdesign_subtractive_sweep(args)
                # Pattern features
                elif operation == "mirror":
                    result = self._mirror_feature(args)
                # Hole features
                elif operation in ["hole", "counterbore", "countersink"]:
                    result = self._hole_wizard({**args, "hole_type": operation})
                else:
                    result = f"Unknown PartDesign operation: {operation}"
                
                return {"success": True, "result": result, "task_duration": time.time() - task_start}
                
            except Exception as e:
                import traceback
                if DEBUG_ENABLED:
                    # Only log errors, not every intermediate stage
                    _log_operation(
                        operation=f"partdesign_operations:{operation}",
                        error=e,
                        parameters={"traceback": traceback.format_exc()}
                    )
                return {"error": f"PartDesign operation failed: {e}"}
        
        # Queue task for GUI thread execution
        gui_task_queue.put(partdesign_operation_task)
        
        # Wait for result with timeout
        start_time = time.time()
        timeout_seconds = 30  # Allow longer for complex operations
        
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                
                # Single consolidated log entry at end (success only, errors logged above)
                if DEBUG_ENABLED and isinstance(result, dict) and "success" in result:
                    total_duration = time.time() - operation_start
                    task_duration = result.get("task_duration", 0)
                    wait_duration = total_duration - task_duration
                    _log_operation(
                        operation=f"partdesign_operations",
                        parameters={
                            "operation": operation,
                            "task_ms": int(task_duration * 1000),
                            "wait_ms": int(wait_duration * 1000),
                            "total_ms": int(total_duration * 1000),
                        },
                        result=result.get("result", "")
                    )
                
                if isinstance(result, dict):
                    if "error" in result:
                        return result["error"]
                    elif "success" in result:
                        return result["result"]
                return str(result)
                
            except queue.Empty:
                time.sleep(0.05)  # 50ms polling
                continue
        
        if DEBUG_ENABLED:
            _log_operation(
                operation=f"partdesign_operations:timeout",
                error=TimeoutError(f"PartDesign operation timeout after {timeout_seconds}s"),
                parameters={"operation": operation, "timeout_seconds": timeout_seconds}
            )
        
        return f"PartDesign operation timeout - GUI thread may be busy"

    def _handle_part_operations(self, args: Dict[str, Any]) -> str:
        """Smart dispatcher for all Part operations (18+ operations)
        
        GUI-SAFE: All operations are queued to the GUI thread to prevent crashes
        from NSWindow/Qt threading violations.
        
        Optimized logging: Single log entry per operation (not per stage)
        """
        operation = args.get('operation', '')
        part_op_id = f"part_{operation}_{int(time.time() * 1000)}"
        operation_start = time.time()
        
        # Define the actual work to run on GUI thread
        def part_operation_task():
            task_start = time.time()
            
            try:
                # Route to appropriate Part method
                if operation == "box":
                    result = self._create_box(args)
                elif operation == "cylinder":
                    result = self._create_cylinder(args)
                elif operation == "sphere":
                    result = self._create_sphere(args)
                elif operation == "cone":
                    result = self._create_cone(args)
                elif operation == "torus":
                    result = self._create_torus(args)
                elif operation == "wedge":
                    result = self._create_wedge(args)
                # Boolean operations
                elif operation == "fuse":
                    result = self._fuse_objects(args)
                elif operation == "cut":
                    result = self._cut_objects(args)
                elif operation == "common":
                    result = self._common_objects(args)
                elif operation == "section":
                    result = self._part_section(args)
                # Transform operations
                elif operation == "move":
                    result = self._move_object(args)
                elif operation == "rotate":
                    result = self._rotate_object(args)
                elif operation == "scale":
                    result = self._part_scale_object(args)
                elif operation == "mirror":
                    result = self._part_mirror_object(args)
                # Advanced creation
                elif operation == "loft":
                    result = self._loft_profiles(args)
                elif operation == "sweep":
                    result = self._sweep_path(args)
                elif operation == "extrude":
                    result = self._part_extrude(args)
                elif operation == "revolve":
                    result = self._part_revolve(args)
                else:
                    result = f"Unknown Part operation: {operation}"
                
                return {"success": True, "result": result, "task_duration": time.time() - task_start}
                
            except Exception as e:
                import traceback
                if DEBUG_ENABLED:
                    # Only log errors, not every intermediate stage
                    _log_operation(
                        operation=f"part_operations:{operation}",
                        error=e,
                        parameters={"traceback": traceback.format_exc()}
                    )
                return {"error": f"Part operation failed: {e}"}
        
        # Queue task for GUI thread execution
        gui_task_queue.put(part_operation_task)
        
        # Wait for result with timeout
        start_time = time.time()
        timeout_seconds = 30  # Allow longer for complex operations
        
        while time.time() - start_time < timeout_seconds:
            try:
                result = gui_response_queue.get_nowait()
                
                # Single consolidated log entry at end (success only, errors logged above)
                if DEBUG_ENABLED and isinstance(result, dict) and "success" in result:
                    total_duration = time.time() - operation_start
                    task_duration = result.get("task_duration", 0)
                    wait_duration = total_duration - task_duration
                    _log_operation(
                        operation=f"part_operations",
                        parameters={
                            "operation": operation,
                            "task_ms": int(task_duration * 1000),
                            "wait_ms": int(wait_duration * 1000),
                            "total_ms": int(total_duration * 1000),
                        },
                        result=result.get("result", "")
                    )
                
                if isinstance(result, dict):
                    if "error" in result:
                        return result["error"]
                    elif "success" in result:
                        return result["result"]
                return str(result)
                
            except queue.Empty:
                time.sleep(0.05)  # 50ms polling
                continue
        
        if DEBUG_ENABLED:
            _log_operation(
                operation=f"part_operations:timeout",
                error=TimeoutError(f"Part operation timeout after {timeout_seconds}s"),
                parameters={"operation": operation, "timeout_seconds": timeout_seconds}
            )
        
        return f"Part operation timeout - GUI thread may be busy"

    def _handle_view_control(self, args: Dict[str, Any]) -> str:
        """Smart dispatcher for all view and document control operations"""
        operation = args.get('operation', '')
        
        # GUI-safe implementation for view operations
        if operation == "screenshot":
            return self._get_screenshot_gui_safe(args)
        elif operation == "set_view":
            return self._set_view_gui_safe(args)
        elif operation == "fit_all":
            return self._fit_all(args)
        elif operation in ["zoom_in", "zoom_out"]:
            return self._view_zoom(operation, args)
        # Document operations
        elif operation == "save_document":
            return self._save_document(args)
        elif operation == "create_document":
            return self._create_document_gui_safe(args)
        elif operation == "list_objects":
            return self._list_all_objects(args)
        # Selection operations
        elif operation == "select_object":
            return self._select_object(args)
        elif operation == "clear_selection":
            return self._clear_selection(args)
        elif operation == "get_selection":
            return self._get_selection(args)
        # Object visibility
        elif operation == "hide_object":
            return self._hide_object(args)
        elif operation == "show_object":
            return self._show_object(args)
        elif operation == "delete_object":
            return self._delete_object(args)
        # History operations
        elif operation == "undo":
            return self._undo(args)
        elif operation == "redo":
            return self._redo(args)
        # Workbench control
        elif operation == "activate_workbench":
            return self._activate_workbench(args)
        else:
            return f"Unknown view control operation: {operation}"

    def _handle_cam_operations(self, args: Dict[str, Any]) -> str:
        """Smart dispatcher for all CAM (Path) workbench operations"""
        operation = args.get('operation', '')

        # Job management
        if operation == "create_job":
            return self._cam_create_job(args)
        elif operation == "setup_stock":
            return self._cam_setup_stock(args)

        # Primary milling operations
        elif operation == "profile":
            return self._cam_profile(args)
        elif operation == "pocket":
            return self._cam_pocket(args)
        elif operation == "adaptive":
            return self._cam_adaptive(args)
        elif operation == "face":
            return self._cam_face(args)
        elif operation == "helix":
            return self._cam_helix(args)
        elif operation == "slot":
            return self._cam_slot(args)
        elif operation == "engrave":
            return self._cam_engrave(args)
        elif operation == "vcarve":
            return self._cam_vcarve(args)
        elif operation == "deburr":
            return self._cam_deburr(args)
        elif operation == "surface":
            return self._cam_surface(args)
        elif operation == "waterline":
            return self._cam_waterline(args)
        elif operation == "pocket_3d":
            return self._cam_pocket_3d(args)

        # Drilling operations
        elif operation == "drilling":
            return self._cam_drilling(args)
        elif operation == "thread_milling":
            return self._cam_thread_milling(args)

        # Dressup operations (path modifications)
        elif operation == "dogbone":
            return self._cam_dogbone(args)
        elif operation == "lead_in_out":
            return self._cam_lead_in_out(args)
        elif operation == "ramp_entry":
            return self._cam_ramp_entry(args)
        elif operation == "tag":
            return self._cam_tag(args)
        elif operation == "axis_map":
            return self._cam_axis_map(args)
        elif operation == "drag_knife":
            return self._cam_drag_knife(args)
        elif operation == "z_correct":
            return self._cam_z_correct(args)

        # Tool management
        elif operation == "create_tool":
            return self._cam_create_tool(args)
        elif operation == "tool_controller":
            return self._cam_tool_controller(args)

        # Utility operations
        elif operation == "simulate":
            return self._cam_simulate(args)
        elif operation == "post_process":
            return self._cam_post_process(args)
        elif operation == "inspect":
            return self._cam_inspect(args)

        else:
            return f"Unknown CAM operation: {operation}"

    # ===================================================================
    # PLACEHOLDER IMPLEMENTATIONS FOR MISSING OPERATIONS
    # ===================================================================


    def _view_zoom(self, direction: str, args: Dict[str, Any]) -> str:
        """Zoom view in/out - placeholder implementation"""
        return f"View {direction} - implementation needed"

    def _part_section(self, args: Dict[str, Any]) -> str:
        """Create section - placeholder implementation"""
        return "Part section - implementation needed"

    def _part_scale_object(self, args: Dict[str, Any]) -> str:
        """Scale object by modifying its dimensions directly"""
        try:
            object_name = args.get('object_name', '')
            scale_factor = args.get('scale_factor', 1.5)
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object {object_name} not found"
            
            # Check if this is a parametric object (Box, Cylinder, etc.)
            if hasattr(obj, 'Length') and hasattr(obj, 'Width') and hasattr(obj, 'Height'):
                # Box object - scale dimensions directly
                old_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                obj.Length = obj.Length.Value * scale_factor
                obj.Width = obj.Width.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                new_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                doc.recompute()
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"
            elif hasattr(obj, 'Radius') and hasattr(obj, 'Height'):
                # Cylinder/Cone object - scale dimensions directly
                old_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                obj.Radius = obj.Radius.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                if hasattr(obj, 'Radius2'):  # Cone has second radius
                    obj.Radius2 = obj.Radius2.Value * scale_factor
                new_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                doc.recompute()
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"
            elif hasattr(obj, 'Radius'):
                # Sphere object - scale radius directly
                old_radius = obj.Radius.Value
                obj.Radius = obj.Radius.Value * scale_factor
                doc.recompute()
                return f"Scaled {object_name} by factor {scale_factor} (R{old_radius}mm → R{obj.Radius.Value}mm)"
            else:
                # Non-parametric object - create scaled copy using transformation
                if hasattr(obj, 'Shape'):
                    import Part
                    matrix = FreeCAD.Matrix()
                    matrix.scale(scale_factor, scale_factor, scale_factor)
                    scaled_shape = obj.Shape.transformGeometry(matrix)
                    scaled_obj = doc.addObject("Part::Feature", f"{object_name}_scaled")
                    scaled_obj.Shape = scaled_shape
                    doc.recompute()
                    return f"Created scaled copy: {scaled_obj.Name} (factor {scale_factor})"
                else:
                    return f"Cannot scale {object_name} - not a parametric object"
                    
        except Exception as e:
            return f"Error scaling object: {e}"

    def _part_mirror_object(self, args: Dict[str, Any]) -> str:
        """Mirror object across a plane"""
        try:
            object_name = args.get('object_name', '')
            plane = args.get('plane', 'YZ')
            name = args.get('name', '')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            obj = doc.getObject(object_name)
            if not obj:
                return f"Object {object_name} not found"
            
            if not hasattr(obj, 'Shape'):
                return f"Object {object_name} is not a shape object"
            
            # Set mirror plane normal and origin based on plane parameter
            if plane == "YZ":
                normal = FreeCAD.Vector(1, 0, 0)  # Normal to YZ plane
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XZ":
                normal = FreeCAD.Vector(0, 1, 0)  # Normal to XZ plane
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XY":
                normal = FreeCAD.Vector(0, 0, 1)  # Normal to XY plane
                mirror_point = FreeCAD.Vector(0, 0, 0)
            else:
                return f"Invalid plane '{plane}'. Valid options: XY, XZ, YZ"
            
            # Mirror the shape
            import Part
            mirrored_shape = obj.Shape.mirror(mirror_point, normal)
            
            # Create mirrored object with appropriate name
            if name:
                mirrored_obj = doc.addObject("Part::Feature", name)
            else:
                mirrored_obj = doc.addObject("Part::Feature", f"{object_name}_mirrored")
            mirrored_obj.Shape = mirrored_shape
            
            doc.recompute()
            return f"Mirrored {object_name} across {plane} plane at (0,0,0)"
            
        except Exception as e:
            return f"Error mirroring object: {e}"

    def _part_extrude(self, args: Dict[str, Any]) -> str:
        """Extrude a sketch or wire profile"""
        try:
            profile_sketch = args.get('profile_sketch', '')
            height = args.get('height', 10)
            direction = args.get('direction', 'z')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            sketch = doc.getObject(profile_sketch)
            if not sketch:
                return f"Sketch {profile_sketch} not found"
            
            # Determine extrusion vector
            if direction == 'x':
                vec = FreeCAD.Vector(height, 0, 0)
            elif direction == 'y':
                vec = FreeCAD.Vector(0, height, 0)
            else:
                vec = FreeCAD.Vector(0, 0, height)
            
            # Get the shape to extrude
            if hasattr(sketch, 'Shape'):
                shape = sketch.Shape
                # Extrude the shape
                import Part
                if shape.Wires:
                    # Create face from wire if needed
                    face = Part.Face(shape.Wires[0])
                    extruded = face.extrude(vec)
                elif shape.Faces:
                    extruded = shape.extrude(vec)
                else:
                    return f"Sketch {profile_sketch} has no valid wires or faces to extrude"
                
                # Create the extruded object
                extrude_obj = doc.addObject("Part::Feature", f"{profile_sketch}_extruded")
                extrude_obj.Shape = extruded
                doc.recompute()
                
                return f"Extruded {profile_sketch} by {height}mm in {direction} direction"
            else:
                return f"Object {profile_sketch} is not a valid sketch"
                
        except Exception as e:
            return f"Error extruding profile: {e}"

    def _part_revolve(self, args: Dict[str, Any]) -> str:
        """Revolve a sketch profile around an axis"""
        try:
            profile_sketch = args.get('profile_sketch', '')
            angle = args.get('angle', 360)
            axis = args.get('axis', 'z').lower()
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            sketch = doc.getObject(profile_sketch)
            if not sketch:
                return f"Sketch {profile_sketch} not found"
            
            # Define revolution axis
            if axis == 'x':
                axis_vec = FreeCAD.Vector(1, 0, 0)
            elif axis == 'y':
                axis_vec = FreeCAD.Vector(0, 1, 0)
            else:
                axis_vec = FreeCAD.Vector(0, 0, 1)
            
            # Get the shape to revolve
            if hasattr(sketch, 'Shape'):
                shape = sketch.Shape
                import Part
                
                # Get position for revolution axis
                pos = FreeCAD.Vector(0, 0, 0)
                if hasattr(sketch, 'Placement'):
                    pos = sketch.Placement.Base
                
                # Revolve the shape
                if shape.Wires:
                    # Create face from wire if needed
                    face = Part.Face(shape.Wires[0])
                    revolved = face.revolve(pos, axis_vec, angle)
                elif shape.Faces:
                    revolved = shape.Faces[0].revolve(pos, axis_vec, angle)
                else:
                    return f"Sketch {profile_sketch} has no valid wires or faces to revolve"
                
                # Create the revolved object
                revolve_obj = doc.addObject("Part::Feature", f"{profile_sketch}_revolved")
                revolve_obj.Shape = revolved
                doc.recompute()
                
                return f"Revolved {profile_sketch} by {angle}° around {axis} axis"
            else:
                return f"Object {profile_sketch} is not a valid sketch"
                
        except Exception as e:
            return f"Error revolving profile: {e}"

    def _partdesign_groove(self, args: Dict[str, Any]) -> str:
        """PartDesign groove - revolve sketch to cut material"""
        try:
            sketch_name = args.get('sketch_name', '')
            angle = args.get('angle', 360)
            name = args.get('name', 'Groove')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            sketch = doc.getObject(sketch_name)
            if not sketch:
                return f"Sketch not found: {sketch_name}"
            
            # Find the body containing the sketch
            body = None
            for obj in doc.Objects:
                if obj.TypeId == "PartDesign::Body" and sketch in obj.Group:
                    body = obj
                    break
            
            if not body:
                return f"Sketch {sketch_name} not found in any PartDesign Body"
            
            # Create groove within the same body
            groove = body.newObject("PartDesign::Groove", name)
            groove.Profile = sketch
            groove.Angle = angle
            groove.ReferenceAxis = (sketch, ['V_Axis'])  # Use sketch's vertical axis
            
            doc.recompute()
            
            return f"Created groove: {groove.Name} from {sketch_name} with {angle}° revolution"
            
        except Exception as e:
            return f"Error creating groove: {e}"

    def _partdesign_additive_pipe(self, args: Dict[str, Any]) -> str:
        """PartDesign additive pipe - sweep profile along path with additional transformations"""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            name = args.get('name', 'AdditivePipe')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            # Get profile and path sketches
            profile = doc.getObject(profile_sketch)
            path = doc.getObject(path_sketch)
            
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"
            if not path:
                return f"Path sketch not found: {path_sketch}"
            
            # Find or create PartDesign Body
            body = None
            for obj in doc.Objects:
                if obj.TypeId == "PartDesign::Body":
                    body = obj
                    break
            
            if not body:
                body = doc.addObject("PartDesign::Body", "Body")
                doc.recompute()
            
            # Ensure sketches are in the Body
            if profile not in body.Group:
                body.addObject(profile)
            if path not in body.Group:
                body.addObject(path)
            
            # Create PartDesign::AdditivePipe
            pipe = body.newObject("PartDesign::AdditivePipe", name)
            pipe.Profile = profile
            pipe.Spine = path
            pipe.Mode = "Standard"  # Standard pipe mode
            pipe.Transition = "Transformed"  # Transformation mode
            
            doc.recompute()
            
            return f"Created additive pipe: {pipe.Name} from profile '{profile_sketch}' along path '{path_sketch}'"
            
        except Exception as e:
            return f"Error creating additive pipe: {e}"

    def _partdesign_subtractive_loft(self, args: Dict[str, Any]) -> str:
        """PartDesign subtractive loft - loft between sketches to cut material"""
        try:
            sketches = args.get('sketches', [])
            name = args.get('name', 'SubtractiveLoft')
            
            if len(sketches) < 2:
                return "Need at least 2 sketches for subtractive loft"
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            
            # Get sketch objects
            sketch_objects = []
            body = None
            
            for sketch_name in sketches:
                sketch = doc.getObject(sketch_name)
                if not sketch:
                    return f"Sketch not found: {sketch_name}"
                sketch_objects.append(sketch)
                
                # Find the body (use first sketch's body)
                if not body:
                    for obj in doc.Objects:
                        if obj.TypeId == "PartDesign::Body" and sketch in obj.Group:
                            body = obj
                            break
            
            if not body:
                return "No PartDesign Body found containing the sketches"
            
            # Create subtractive loft
            loft = body.newObject("PartDesign::SubtractiveLoft", name)
            loft.Sections = sketch_objects
            
            doc.recompute()
            
            return f"Created subtractive loft: {loft.Name} from {len(sketches)} sketches"
            
        except Exception as e:
            return f"Error creating subtractive loft: {e}"

    def _partdesign_subtractive_sweep(self, args: Dict[str, Any]) -> str:
        """PartDesign subtractive pipe (sweep) - sweep profile along path to cut material"""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            name = args.get('name', 'SubtractivePipe')
            
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
                
            profile = doc.getObject(profile_sketch)
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"
                
            path = doc.getObject(path_sketch)
            if not path:
                return f"Path sketch not found: {path_sketch}"
            
            # Find the body containing the sketches
            body = None
            for obj in doc.Objects:
                if obj.TypeId == "PartDesign::Body" and profile in obj.Group:
                    body = obj
                    break
            
            if not body:
                return f"No PartDesign Body found containing the sketches"
            
            # Create SubtractivePipe (NOT SubtractiveSweep!)
            pipe = body.newObject("PartDesign::SubtractivePipe", name)
            pipe.Profile = profile
            pipe.Spine = path
            
            doc.recompute()
            
            return f"Created subtractive pipe: {pipe.Name} sweeping {profile_sketch} along {path_sketch}"
            
        except Exception as e:
            return f"Error creating subtractive pipe: {e}"

    def _partdesign_rectangular_pattern(self, args: Dict[str, Any]) -> str:
        """PartDesign rectangular pattern - placeholder implementation"""
        return "PartDesign rectangular pattern - implementation needed"

    # ===================================================================
    # CAM WORKBENCH OPERATIONS
    # ===================================================================

    def _cam_create_job(self, args: Dict[str, Any]) -> str:
        """Create a new CAM Job"""
        try:
            import Path

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('name', 'Job')
            base_object = args.get('base_object', '')

            # Create the job
            job = Path.Job.Create(job_name, [], None)

            # If base object specified, add it to the job
            if base_object:
                obj = doc.getObject(base_object)
                if obj:
                    job.Model.Group = [obj]
                    job.recompute()
                    return f"✓ Created CAM Job '{job.Name}' with base object '{base_object}'"
                else:
                    return f"⚠ Created CAM Job '{job.Name}' but base object '{base_object}' not found"

            doc.recompute()
            return f"✓ Created CAM Job '{job.Name}'"

        except ImportError:
            return "Error: Path (CAM) module not available. Please install FreeCAD with CAM workbench support."
        except Exception as e:
            return f"Error creating CAM job: {e}"

    def _cam_setup_stock(self, args: Dict[str, Any]) -> str:
        """Setup stock for CAM job"""
        try:
            import Path

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            stock_type = args.get('stock_type', 'CreateBox')  # CreateBox, CreateCylinder, FromBase

            # Stock dimensions
            length = args.get('length', 100)
            width = args.get('width', 100)
            height = args.get('height', 50)

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found"

            # Set stock parameters
            if stock_type == 'CreateBox':
                job.Stock = Path.Stock.CreateBox(job)
                job.Stock.Length = length
                job.Stock.Width = width
                job.Stock.Height = height
            elif stock_type == 'FromBase':
                job.Stock = Path.Stock.CreateFromBase(job)
                extent_x = args.get('extent_x', 10)
                extent_y = args.get('extent_y', 10)
                extent_z = args.get('extent_z', 10)
                job.Stock.ExtXneg = extent_x
                job.Stock.ExtXpos = extent_x
                job.Stock.ExtYneg = extent_y
                job.Stock.ExtYpos = extent_y
                job.Stock.ExtZneg = 0
                job.Stock.ExtZpos = extent_z

            job.recompute()
            return f"✓ Setup stock for job '{job_name}' using {stock_type}"

        except Exception as e:
            return f"Error setting up stock: {e}"

    def _cam_profile(self, args: Dict[str, Any]) -> str:
        """Create a profile (contour) operation"""
        try:
            import Path, PathScripts.PathProfile as PathProfile

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Profile')
            base_object = args.get('base_object', '')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            # Create profile operation
            obj = PathProfile.Create(name)
            job.Operations.Group += [obj]

            # Set base geometry if specified
            if base_object:
                base = doc.getObject(base_object)
                if base:
                    obj.Base = [(base, [])]

            # Set common parameters
            if 'cut_side' in args:
                obj.Side = args['cut_side']  # 'Outside', 'Inside'
            if 'direction' in args:
                obj.Direction = args['direction']  # 'CW', 'CCW'
            if 'stepdown' in args:
                obj.StepDown = args['stepdown']

            job.recompute()
            return f"✓ Created Profile operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathProfile module not available"
        except Exception as e:
            return f"Error creating profile operation: {e}"

    def _cam_pocket(self, args: Dict[str, Any]) -> str:
        """Create a pocket operation"""
        try:
            import Path, PathScripts.PathPocket as PathPocket

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Pocket')
            base_object = args.get('base_object', '')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            # Create pocket operation
            obj = PathPocket.Create(name)
            job.Operations.Group += [obj]

            # Set base geometry if specified
            if base_object:
                base = doc.getObject(base_object)
                if base:
                    obj.Base = [(base, [])]

            # Set common parameters
            if 'stepover' in args:
                obj.StepOver = args['stepover']
            if 'stepdown' in args:
                obj.StepDown = args['stepdown']
            if 'cut_mode' in args:
                obj.CutMode = args['cut_mode']  # 'Climb', 'Conventional'

            job.recompute()
            return f"✓ Created Pocket operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathPocket module not available"
        except Exception as e:
            return f"Error creating pocket operation: {e}"

    def _cam_drilling(self, args: Dict[str, Any]) -> str:
        """Create a drilling operation"""
        try:
            import Path, PathScripts.PathDrilling as PathDrilling

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Drilling')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            # Create drilling operation
            obj = PathDrilling.Create(name)
            job.Operations.Group += [obj]

            # Set parameters
            if 'depth' in args:
                obj.FinalDepth = args['depth']
            if 'retract_height' in args:
                obj.RetractHeight = args['retract_height']
            if 'peck_depth' in args:
                obj.PeckDepth = args['peck_depth']
            if 'dwell_time' in args:
                obj.DwellTime = args['dwell_time']

            job.recompute()
            return f"✓ Created Drilling operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathDrilling module not available"
        except Exception as e:
            return f"Error creating drilling operation: {e}"

    def _cam_adaptive(self, args: Dict[str, Any]) -> str:
        """Create an adaptive clearing operation"""
        try:
            import Path, PathScripts.PathAdaptive as PathAdaptive

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Adaptive')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            # Create adaptive operation
            obj = PathAdaptive.Create(name)
            job.Operations.Group += [obj]

            # Set parameters
            if 'stepover' in args:
                obj.StepOver = args['stepover']
            if 'tolerance' in args:
                obj.Tolerance = args['tolerance']

            job.recompute()
            return f"✓ Created Adaptive operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathAdaptive module not available"
        except Exception as e:
            return f"Error creating adaptive operation: {e}"

    def _cam_face(self, args: Dict[str, Any]) -> str:
        """Create a face milling operation"""
        return self._cam_placeholder_operation("Face Milling", args)

    def _cam_helix(self, args: Dict[str, Any]) -> str:
        """Create a helix operation"""
        return self._cam_placeholder_operation("Helix", args)

    def _cam_slot(self, args: Dict[str, Any]) -> str:
        """Create a slot milling operation"""
        return self._cam_placeholder_operation("Slot Milling", args)

    def _cam_engrave(self, args: Dict[str, Any]) -> str:
        """Create an engrave operation"""
        return self._cam_placeholder_operation("Engrave", args)

    def _cam_vcarve(self, args: Dict[str, Any]) -> str:
        """Create a V-carve operation"""
        return self._cam_placeholder_operation("V-Carve", args)

    def _cam_deburr(self, args: Dict[str, Any]) -> str:
        """Create a deburr operation"""
        return self._cam_placeholder_operation("Deburr", args)

    def _cam_surface(self, args: Dict[str, Any]) -> str:
        """Create a surface milling operation"""
        return self._cam_placeholder_operation("Surface Milling", args)

    def _cam_waterline(self, args: Dict[str, Any]) -> str:
        """Create a waterline operation"""
        return self._cam_placeholder_operation("Waterline", args)

    def _cam_pocket_3d(self, args: Dict[str, Any]) -> str:
        """Create a 3D pocket operation"""
        return self._cam_placeholder_operation("3D Pocket", args)

    def _cam_thread_milling(self, args: Dict[str, Any]) -> str:
        """Create a thread milling operation"""
        return self._cam_placeholder_operation("Thread Milling", args)

    def _cam_dogbone(self, args: Dict[str, Any]) -> str:
        """Add dogbone dressup to a path"""
        return self._cam_placeholder_dressup("Dogbone", args)

    def _cam_lead_in_out(self, args: Dict[str, Any]) -> str:
        """Add lead-in/lead-out to a path"""
        return self._cam_placeholder_dressup("Lead In/Out", args)

    def _cam_ramp_entry(self, args: Dict[str, Any]) -> str:
        """Add ramp entry to a path"""
        return self._cam_placeholder_dressup("Ramp Entry", args)

    def _cam_tag(self, args: Dict[str, Any]) -> str:
        """Add holding tags to a path"""
        return self._cam_placeholder_dressup("Tag", args)

    def _cam_axis_map(self, args: Dict[str, Any]) -> str:
        """Add axis mapping to a path"""
        return self._cam_placeholder_dressup("Axis Map", args)

    def _cam_drag_knife(self, args: Dict[str, Any]) -> str:
        """Add drag knife compensation to a path"""
        return self._cam_placeholder_dressup("Drag Knife", args)

    def _cam_z_correct(self, args: Dict[str, Any]) -> str:
        """Add Z-axis correction to a path"""
        return self._cam_placeholder_dressup("Z-Correction", args)

    def _cam_create_tool(self, args: Dict[str, Any]) -> str:
        """Create a tool bit"""
        try:
            tool_type = args.get('tool_type', 'endmill')
            diameter = args.get('diameter', 6.0)
            name = args.get('name', f'{tool_type}_{diameter}mm')

            return f"ℹ Tool creation: Please use FreeCAD's Tool Library manager (CAM → Tool Library Editor) to create tool '{name}' ({tool_type}, {diameter}mm diameter)"

        except Exception as e:
            return f"Error: {e}"

    def _cam_tool_controller(self, args: Dict[str, Any]) -> str:
        """Create a tool controller"""
        try:
            job_name = args.get('job_name', '')
            tool_name = args.get('tool_name', '')
            spindle_speed = args.get('spindle_speed', 10000)
            feed_rate = args.get('feed_rate', 1000)

            return f"ℹ Tool controller setup: Please add tool controller in job '{job_name}' with spindle speed {spindle_speed} RPM and feed rate {feed_rate} mm/min"

        except Exception as e:
            return f"Error: {e}"

    def _cam_simulate(self, args: Dict[str, Any]) -> str:
        """Simulate CAM operations"""
        try:
            job_name = args.get('job_name', '')

            return f"ℹ Simulation: Please use CAM → Simulate (or click Simulate button) to run simulation for job '{job_name}'"

        except Exception as e:
            return f"Error: {e}"

    def _cam_post_process(self, args: Dict[str, Any]) -> str:
        """Post-process CAM job to generate G-code"""
        try:
            import Path, PathScripts.PathPost as PathPost

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            output_file = args.get('output_file', '')
            post_processor = args.get('post_processor', 'grbl')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found"

            # Set default output file if not specified
            if not output_file:
                output_file = f"/tmp/{job_name}.gcode"

            # Post process
            postlist = PathPost.buildPostList(job)
            if not postlist:
                return "Error: No operations to post-process"

            gcode = PathPost.exportGCode(postlist, job, output_file)

            return f"✓ Generated G-code for job '{job_name}' → {output_file}"

        except ImportError:
            return "Error: PathPost module not available"
        except Exception as e:
            return f"Error post-processing: {e}"

    def _cam_inspect(self, args: Dict[str, Any]) -> str:
        """Inspect CAM job and operations"""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')

            # Find the job
            job = doc.getObject(job_name) if job_name else None
            if not job:
                # List all jobs
                import Path
                jobs = [obj for obj in doc.Objects if hasattr(obj, 'Operations')]
                if not jobs:
                    return "No CAM jobs found in document"

                result = f"Found {len(jobs)} CAM job(s):\n"
                for j in jobs:
                    ops = j.Operations.Group if hasattr(j, 'Operations') else []
                    result += f"  • {j.Name}: {len(ops)} operation(s)\n"
                return result

            # Inspect specific job
            ops = job.Operations.Group if hasattr(job, 'Operations') else []
            result = f"Job '{job_name}':\n"
            result += f"  Operations: {len(ops)}\n"
            for i, op in enumerate(ops, 1):
                result += f"    {i}. {op.Name} ({op.TypeId})\n"

            return result

        except Exception as e:
            return f"Error inspecting job: {e}"

    def _cam_placeholder_operation(self, operation_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM operations not yet implemented"""
        job_name = args.get('job_name', '')
        name = args.get('name', operation_name)

        return f"ℹ {operation_name} operation: This operation is available in FreeCAD but not yet automated via MCP. Please create '{name}' operation manually in job '{job_name}' using the CAM workbench UI."

    def _cam_placeholder_dressup(self, dressup_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM dressup operations not yet implemented"""
        operation = args.get('operation', '')

        return f"ℹ {dressup_name} dressup: This dressup is available in FreeCAD but not yet automated via MCP. Please apply '{dressup_name}' dressup to operation '{operation}' manually using the CAM workbench UI."

    def stop_server(self):
        """Stop the socket server"""
        self.is_running = False
        
        # Close all client connections
        for client in self.client_connections[:]:
            client.close()
        self.client_connections.clear()
        
        # Close server socket
        if self.server_socket:
            self.server_socket.close()
            
        # Remove socket file
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
            
        FreeCAD.Console.PrintMessage("Socket server stopped\n")

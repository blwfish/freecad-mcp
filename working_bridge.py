#!/opt/homebrew/bin/python3.11
"""
FreeCAD MCP Bridge - Phase 1 Smart Dispatcher Architecture
Smart dispatchers aligned with FreeCAD workbench structure for optimal Claude Code integration
"""

import asyncio
import json
import os
import sys
import socket
import platform
from typing import Any

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import message framing for v2.1.1 protocol
from mcp_bridge_framing import send_message, receive_message

# Initialize debugging infrastructure (optional - works without it)
try:
    from freecad_debug import init_debugger, debug_deccorator
    from freecad_health import init_monitor
    import logging
    
    # Initialize with file-only logging (no console output for MCP)
    debugger = init_debugger(
        log_dir="/tmp/freecad_mcp_debug",
        level=logging.DEBUG,
        enable_console=False,  # CRITICAL: No console output for MCP!
        enable_file=True
    )
    monitor = init_monitor()
    
    # Log startup to file only
    debugger.logger.info("="*80)
    debugger.logger.info("FreeCAD MCP Bridge Starting with Debug Infrastructure")
    debugger.logger.info("="*80)
    DEBUG_ENABLED = True
except ImportError:
    debugger = None
    monitor = None
    DEBUG_ENABLED = False
    
    def debug_decorator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

async def main():
    """Run MCP server for FreeCAD integration"""
    try:
        # Import MCP components with correct API
        import mcp.types as types
        from mcp.server import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
    except ImportError as e:
        # MCP import failed - exit silently to avoid STDIO corruption
        sys.exit(1)

    # Create server with freecad naming
    server = Server("freecad")
    
    # Check if FreeCAD is available (cross-platform)
    if platform.system() == "Windows":
        socket_path = "localhost:23456"
        freecad_available = True  # We'll check connection when needed
    else:
        socket_path = "/tmp/freecad_mcp.sock"
        freecad_available = os.path.exists(socket_path)
    
    @debug_decorator(track_state=False, track_performance=True)
    async def send_to_freecad(tool_name: str, args: dict) -> str:
        """Send command to FreeCAD via socket (cross-platform)"""
        try:
            # Create socket connection based on platform
            if platform.system() == "Windows":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(('localhost', 23456))
            else:
                if not os.path.exists(socket_path):
                    return json.dumps({"error": "FreeCAD socket not available. Please start FreeCAD with AICopilot installed"})
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(socket_path)
            
            # Send command with length-prefixed protocol (v2.1.1)
            command = json.dumps({"tool": tool_name, "args": args})
            if not send_message(sock, command):
                sock.close()
                return json.dumps({"error": "Failed to send command to FreeCAD"})
            
            # Receive response with length-prefixed protocol (v2.1.1)
            response = receive_message(sock, timeout=30.0)
            sock.close()
            
            if response is None:
                return json.dumps({"error": "Failed to receive response from FreeCAD (timeout or connection error)"})
            
            # Check if this is a selection workflow response
            try:
                result = json.loads(response)
                if isinstance(result, dict) and result.get("status") == "awaiting_selection":
                    # Handle interactive selection workflow
                    return await handle_selection_workflow(tool_name, args, result)
            except json.JSONDecodeError:
                pass  # Not JSON, return as-is
            
            return response
            
        except Exception as e:
            # Log the exception if debugger is available
            if DEBUG_ENABLED and debugger:
                debugger.log_operation(
                    operation="send_to_freecad",
                    parameters={"tool_name": tool_name, "args": args},
                    error=e
                )
                # Check FreeCAD health after socket error
                if monitor:
                    status = monitor.perform_health_check()
                    if not status['is_healthy']:
                        monitor.log_crash(status, {
                            "triggered_by": "socket_error",
                            "tool_name": tool_name,
                            "args": args
                        })
            return json.dumps({"error": f"Socket communication error: {e}"})
    
    async def handle_selection_workflow(tool_name: str, original_args: dict, selection_request: dict) -> str:
        """Handle the interactive selection workflow - Claude Code style"""
        try:
            # Format the interactive message for Claude Code
            message = selection_request.get("message", "Please make selection in FreeCAD")
            selection_type = selection_request.get("selection_type", "elements")
            object_name = selection_request.get("object_name", "")
            operation_id = selection_request.get("operation_id", "")
            
            # Create Claude Code compatible interactive response
            interactive_response = {
                "interactive": True,
                "message": f"🎯 Interactive Selection Required\n\n{message}",
                "operation_id": operation_id,
                "selection_type": selection_type,
                "object_name": object_name,
                "tool_name": tool_name,
                "original_args": original_args,
                "instructions": f"1. Go to FreeCAD and select {selection_type} on {object_name}\n2. Return here and choose an option:"
            }
            
            return json.dumps(interactive_response)
            
        except Exception as e:
            return json.dumps({"error": f"Selection workflow error: {e}"})
    
    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        """List available Phase 1 smart dispatcher tools"""
        base_tools = [
            types.Tool(
                name="check_freecad_connection",
                description="Check if FreeCAD is running with AICopilot installed",
                inputSchema={
                    "type": "object",
                    "properties": {},
                }
            ),
            types.Tool(
                name="test_echo",
                description="Test tool that echoes back a message",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to echo back"
                        }
                    },
                    "required": ["message"]
                }
            )
        ]
        
        # Add Phase 1 Smart Dispatchers if socket is available
        if freecad_available:
            smart_dispatchers = [
                types.Tool(
                    name="partdesign_operations", 
                    description="⚠️ MODIFIES FreeCAD document: Smart dispatcher for parametric features. Operations like fillet/chamfer require edge selection and will permanently modify the 3D model.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "PartDesign operation to perform",
                                "enum": [
                                    # Additive features (5)
                                    "pad", "revolution", "loft", "sweep", "additive_pipe",
                                    # Subtractive features (2)
                                    "groove", "subtractive_sweep",
                                    # Dress-up features (2)
                                    "fillet", "chamfer",
                                    # Pattern features (1)
                                    "mirror",
                                    # Hole features (3)
                                    "hole", "counterbore", "countersink"
                                ]
                            },
                            "sketch_name": {"type": "string", "description": "Sketch name for operations"},
                            "object_name": {"type": "string", "description": "Object name for dress-up operations"},
                            "feature_name": {"type": "string", "description": "Feature name for pattern operations"},
                            # Common parameters
                            "length": {"type": "number", "description": "Length/depth for pad", "default": 10},
                            "radius": {"type": "number", "description": "Radius for fillet/holes", "default": 1},
                            "distance": {"type": "number", "description": "Distance for chamfer", "default": 1},
                            "angle": {"type": "number", "description": "Angle for revolution/draft", "default": 360},
                            "thickness": {"type": "number", "description": "Thickness value", "default": 2},
                            # Pattern parameters
                            "count": {"type": "integer", "description": "Pattern count", "default": 3},
                            "spacing": {"type": "number", "description": "Pattern spacing", "default": 10},
                            "axis": {"type": "string", "description": "Axis for patterns", "enum": ["x", "y", "z"], "default": "x"},
                            "plane": {"type": "string", "description": "Mirror plane", "enum": ["XY", "XZ", "YZ"], "default": "YZ"},
                            # Hole parameters
                            "diameter": {"type": "number", "description": "Hole diameter", "default": 6},
                            "depth": {"type": "number", "description": "Hole depth", "default": 10},
                            "x": {"type": "number", "description": "X position", "default": 0},
                            "y": {"type": "number", "description": "Y position", "default": 0},
                            # Advanced parameters
                            "name": {"type": "string", "description": "Name for result feature"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="part_operations",
                    description="Smart dispatcher for all basic solid and boolean operations (18+ operations)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Part operation to perform", 
                                "enum": [
                                    # Primitive creation (6)
                                    "box", "cylinder", "sphere", "cone", "torus", "wedge",
                                    # Boolean operations (4)
                                    "fuse", "cut", "common", "section",
                                    # Transform operations (4)
                                    "move", "rotate", "scale", "mirror",
                                    # Advanced creation (4)
                                    "loft", "sweep", "extrude", "revolve"
                                ]
                            },
                            # Primitive parameters
                            "length": {"type": "number", "description": "Box length", "default": 10},
                            "width": {"type": "number", "description": "Box width", "default": 10},
                            "height": {"type": "number", "description": "Box/cylinder height", "default": 10},
                            "radius": {"type": "number", "description": "Sphere/cylinder radius", "default": 5},
                            "radius1": {"type": "number", "description": "Major radius for torus/cone", "default": 10},
                            "radius2": {"type": "number", "description": "Minor radius for torus/cone", "default": 3},
                            # Position parameters
                            "x": {"type": "number", "description": "X position", "default": 0},
                            "y": {"type": "number", "description": "Y position", "default": 0},
                            "z": {"type": "number", "description": "Z position", "default": 0},
                            # Boolean operation parameters
                            "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names for boolean ops"},
                            "base": {"type": "string", "description": "Base object for cut operation"},
                            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool objects for cut"},
                            # Transform parameters
                            "object_name": {"type": "string", "description": "Object to transform"},
                            "axis": {"type": "string", "description": "Rotation axis", "enum": ["x", "y", "z"], "default": "z"},
                            "angle": {"type": "number", "description": "Rotation angle", "default": 90},
                            "scale_factor": {"type": "number", "description": "Scale factor", "default": 1.5},
                            # Advanced creation parameters
                            "sketches": {"type": "array", "items": {"type": "string"}, "description": "Sketches for loft"},
                            "profile_sketch": {"type": "string", "description": "Profile sketch for sweep"},
                            "path_sketch": {"type": "string", "description": "Path sketch for sweep"},
                            # Naming
                            "name": {"type": "string", "description": "Name for result object"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="view_control",
                    description="Smart dispatcher for all view, screenshot, and document operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "View control operation",
                                "enum": [
                                    # View operations
                                    "screenshot", "set_view", "fit_all", "zoom_in", "zoom_out",
                                    # Document operations  
                                    "create_document", "save_document", "list_objects",
                                    # Selection operations
                                    "select_object", "clear_selection", "get_selection",
                                    # Object visibility
                                    "hide_object", "show_object", "delete_object",
                                    # History operations
                                    "undo", "redo",
                                    # Workbench control
                                    "activate_workbench"
                                ]
                            },
                            # Screenshot parameters
                            "width": {"type": "integer", "description": "Screenshot width", "default": 800},
                            "height": {"type": "integer", "description": "Screenshot height", "default": 600},
                            # View parameters
                            "view_type": {"type": "string", "description": "View orientation", 
                                         "enum": ["top", "front", "left", "right", "isometric", "axonometric"], 
                                         "default": "isometric"},
                            # Document parameters
                            "document_name": {"type": "string", "description": "Document name", "default": "Unnamed"},
                            "filename": {"type": "string", "description": "File path to save"},
                            # Object parameters
                            "object_name": {"type": "string", "description": "Object name for operations"},
                            # Workbench parameters
                            "workbench_name": {"type": "string", "description": "Workbench name to activate"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="cam_operations",
                    description="Smart dispatcher for CAM (Path) workbench - CNC toolpath generation and machining operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "CAM operation to perform",
                                "enum": [
                                    # Job management (5)
                                    "create_job", "setup_stock", "configure_job", "inspect_job", "job_status", "delete_job",
                                    # Primary milling operations (12)
                                    "profile", "pocket", "adaptive", "face", "helix", "slot",
                                    "engrave", "vcarve", "deburr", "surface", "waterline", "pocket_3d",
                                    # Drilling operations (2)
                                    "drilling", "thread_milling",
                                    # Dressup operations (7)
                                    "dogbone", "lead_in_out", "ramp_entry", "tag", "axis_map",
                                    "drag_knife", "z_correct",
                                    # Operation management (4)
                                    "list_operations", "get_operation", "configure_operation", "delete_operation",
                                    # Tool management (2) - deprecated, use cam_tools and cam_tool_controllers instead
                                    "create_tool", "tool_controller",
                                    # Utility operations (4)
                                    "simulate", "simulate_job", "post_process", "export_gcode", "inspect"
                                ]
                            },
                            # Job parameters
                            "job_name": {"type": "string", "description": "CAM job name"},
                            "base_object": {"type": "string", "description": "Base 3D object for CAM operations"},
                            # Stock parameters
                            "stock_type": {"type": "string", "description": "Stock type", "enum": ["CreateBox", "CreateCylinder", "FromBase"], "default": "CreateBox"},
                            "length": {"type": "number", "description": "Stock length", "default": 100},
                            "width": {"type": "number", "description": "Stock width", "default": 100},
                            "height": {"type": "number", "description": "Stock height", "default": 50},
                            "extent_x": {"type": "number", "description": "Stock extent in X", "default": 10},
                            "extent_y": {"type": "number", "description": "Stock extent in Y", "default": 10},
                            "extent_z": {"type": "number", "description": "Stock extent in Z", "default": 10},
                            # Operation parameters
                            "faces": {"type": "array", "items": {"type": "string"}, "description": "Face names for profile/pocket base geometry e.g. ['Face1','Face3']. Omit for whole-model exterior contour."},
                            "edges": {"type": "array", "items": {"type": "string"}, "description": "Edge names for profile base geometry e.g. ['Edge1','Edge4']."},
                            "side": {"type": "string", "description": "Profile cut side: Outside (default) cuts outside the contour, Inside cuts inside", "enum": ["Outside", "Inside"], "default": "Outside"},
                            "cut_side": {"type": "string", "description": "Deprecated alias for side", "enum": ["Outside", "Inside"]},
                            "process_perimeter": {"type": "boolean", "description": "Profile: trace outer boundary of selected faces (default true)"},
                            "process_holes": {"type": "boolean", "description": "Profile: trace inner holes of selected faces (default false)"},
                            "process_circles": {"type": "boolean", "description": "Profile: treat circular holes as drillable (default false)"},
                            "direction": {"type": "string", "description": "Cut direction", "enum": ["CW", "CCW"]},
                            "stepdown": {"type": "number", "description": "Stepdown depth"},
                            "stepover": {"type": "number", "description": "Stepover percentage"},
                            "cut_mode": {"type": "string", "description": "Cutting mode", "enum": ["Climb", "Conventional"]},
                            # Drilling parameters
                            "depth": {"type": "number", "description": "Drilling depth"},
                            "retract_height": {"type": "number", "description": "Retract height"},
                            "peck_depth": {"type": "number", "description": "Peck drilling depth"},
                            "dwell_time": {"type": "number", "description": "Dwell time in seconds"},
                            # Tool parameters
                            "tool_type": {"type": "string", "description": "Tool type", "enum": ["endmill", "ballend", "bullnose", "chamfer", "drill"], "default": "endmill"},
                            "tool_name": {"type": "string", "description": "Tool name"},
                            "diameter": {"type": "number", "description": "Tool diameter", "default": 6.0},
                            "spindle_speed": {"type": "number", "description": "Spindle speed in RPM", "default": 10000},
                            "feed_rate": {"type": "number", "description": "Feed rate in mm/min", "default": 1000},
                            # Post-processing parameters
                            "output_file": {"type": "string", "description": "Output G-code file path"},
                            "post_processor": {"type": "string", "description": "Post processor name", "default": "grbl"},
                            "post_processor_args": {"type": "string", "description": "Post processor arguments (e.g. '--no-show-editor')"},
                            # Adaptive parameters
                            "tolerance": {"type": "number", "description": "Adaptive tolerance"},
                            # General
                            "name": {"type": "string", "description": "Name for the operation"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="cam_tools",
                    description="CAM Tool Library Management - CRUD operations for cutting tools",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Tool library operation",
                                "enum": ["create_tool", "list_tools", "get_tool", "update_tool", "delete_tool"]
                            },
                            "tool_name": {"type": "string", "description": "Name of the tool"},
                            "tool_type": {
                                "type": "string",
                                "description": "Type of tool",
                                "enum": ["endmill", "ballend", "bullnose", "chamfer", "drill", "v-bit"],
                                "default": "endmill"
                            },
                            "diameter": {"type": "number", "description": "Tool diameter in mm", "default": 6.0},
                            "flute_length": {"type": "number", "description": "Cutting edge length in mm"},
                            "shank_diameter": {"type": "number", "description": "Shank diameter in mm"},
                            "material": {"type": "string", "description": "Tool material (HSS, Carbide, etc.)"},
                            "number_of_flutes": {"type": "integer", "description": "Number of flutes"},
                            "name": {"type": "string", "description": "Tool name (for create operation)"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="cam_tool_controllers",
                    description="CAM Tool Controller Management - CRUD operations for tool controllers (link tools to jobs with speeds/feeds)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Tool controller operation",
                                "enum": ["add_tool_controller", "list_tool_controllers", "get_tool_controller", "update_tool_controller", "remove_tool_controller"]
                            },
                            "job_name": {"type": "string", "description": "CAM job name"},
                            "tool_name": {"type": "string", "description": "Name of the tool bit to use"},
                            "controller_name": {"type": "string", "description": "Name for the tool controller"},
                            "spindle_speed": {"type": "number", "description": "Spindle speed in RPM", "default": 10000},
                            "feed_rate": {"type": "number", "description": "Horizontal feed rate in mm/min", "default": 1000},
                            "vertical_feed_rate": {"type": "number", "description": "Vertical (plunge) feed rate in mm/min"},
                            "tool_number": {"type": "integer", "description": "Tool number for G-code", "default": 1}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="spreadsheet_operations",
                    description="Spreadsheet operations for data management and calculations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Spreadsheet operation to perform",
                                "enum": [
                                    "create_spreadsheet", "set_cell", "get_cell",
                                    "set_alias", "get_alias", "clear_cell",
                                    "set_cell_range", "get_cell_range"
                                ]
                            },
                            "name": {"type": "string", "description": "Spreadsheet name"},
                            "cell": {"type": "string", "description": "Cell address (e.g., 'A1')"},
                            "value": {"type": ["string", "number"], "description": "Cell value"},
                            "alias": {"type": "string", "description": "Cell alias name"},
                            "start_cell": {"type": "string", "description": "Range start cell"},
                            "end_cell": {"type": "string", "description": "Range end cell"},
                            "values": {"type": "array", "description": "Array of values for range"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="draft_operations",
                    description="Draft workbench operations for 2D annotations and arrays",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Draft operation to perform",
                                "enum": [
                                    "clone", "array", "polar_array", "path_array", "point_array"
                                ]
                            },
                            "object_name": {"type": "string", "description": "Object to operate on"},
                            "count": {"type": "integer", "description": "Array count"},
                            "spacing": {"type": "number", "description": "Array spacing"},
                            "angle": {"type": "number", "description": "Polar array angle"}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="mesh_operations",
                    description="Mesh import/export, mesh-to-solid conversion, validation, simplification, and CAD file I/O (STL, OBJ, STEP, IGES, BREP)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Mesh/file operation to perform",
                                "enum": [
                                    "import_mesh", "export_mesh", "mesh_to_solid",
                                    "get_mesh_info", "import_file", "export_file",
                                    "validate_mesh", "simplify_mesh"
                                ]
                            },
                            "file_path": {"type": "string", "description": "File path for import/export"},
                            "object_name": {"type": "string", "description": "Object name to operate on"},
                            "name": {"type": "string", "description": "Name for created object"},
                            "tolerance": {"type": "number", "description": "Mesh-to-solid sewing tolerance", "default": 0.1},
                            "linear_deflection": {"type": "number", "description": "Tessellation linear deflection for Part-to-mesh export", "default": 0.1},
                            "angular_deflection": {"type": "number", "description": "Tessellation angular deflection for Part-to-mesh export"},
                            "target_count": {"type": "integer", "description": "Target face count for mesh simplification"},
                            "reduction": {"type": "number", "description": "Reduction ratio 0-1 for mesh simplification (e.g., 0.5 = 50% fewer faces)"},
                            "auto_repair": {"type": "boolean", "description": "Auto-repair mesh issues during validation", "default": False}
                        },
                        "required": ["operation"]
                    }
                ),
                types.Tool(
                    name="get_debug_logs",
                    description="Retrieve recent debug logs for troubleshooting and analysis",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "count": {
                                "type": "integer",
                                "description": "Number of recent log entries to retrieve",
                                "default": 20
                            },
                            "operation": {
                                "type": "string",
                                "description": "Optional filter by operation name (e.g., 'execute_python', 'cam_operations')"
                            }
                        }
                    }
                ),
                types.Tool(
                    name="execute_python",
                    description="Execute arbitrary Python code in FreeCAD context for power users and advanced operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute in FreeCAD context"
                            }
                        },
                        "required": ["code"]
                    }
                ),
                types.Tool(
                    name="continue_selection",
                    description="Continue an interactive selection operation after selecting elements in FreeCAD",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation_id": {
                                "type": "string",
                                "description": "The operation ID from the awaiting_selection response"
                            }
                        },
                        "required": ["operation_id"]
                    }
                )
            ]
            return base_tools + smart_dispatchers
        
        return base_tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        """Handle tool calls with smart dispatcher routing"""
        
        if name == "check_freecad_connection":
            status = {
                "freecad_socket_exists": freecad_available,
                "socket_path": socket_path,
                "status": "FreeCAD running with AICopilot" if freecad_available
                         else "FreeCAD not running. Please start FreeCAD with AICopilot installed"
            }
            return [types.TextContent(
                type="text",
                text=json.dumps(status, indent=2)
            )]
            
        elif name == "test_echo":
            message = arguments.get("message", "No message provided") if arguments else "No arguments"
            return [types.TextContent(
                type="text", 
                text=f"Bridge received: {message}"
            )]
            
        # Handle continue_selection tool
        elif name == "continue_selection":
            operation_id = arguments.get("operation_id") if arguments else None
            if not operation_id:
                return [types.TextContent(
                    type="text",
                    text="Error: operation_id is required to continue selection"
                )]
            
            # Send continuation command to FreeCAD
            response = await send_to_freecad("continue_selection", {
                "operation_id": operation_id
            })
            
            return [types.TextContent(
                type="text",
                text=response
            )]
            
        # Route smart dispatcher tools to socket with enhanced routing
        elif name in ["partdesign_operations", "part_operations",
                      "view_control", "cam_operations", "cam_tools", "cam_tool_controllers",
                      "cam_machines", "mesh_operations", "measurement_operations",
                      "spreadsheet_operations", "draft_operations", "get_debug_logs", "execute_python"]:
            args = arguments or {}
            
            # Check if this is a continuation from interactive selection
            if args.get("_continue_from_interactive"):
                # Extract the original operation details
                operation_id = args.get("operation_id")
                tool_name = args.get("tool_name") 
                original_args = args.get("original_args", {})
                
                # Add continuation flag
                continue_args = {
                    **original_args,
                    "_continue_selection": True,
                    "_operation_id": operation_id
                }
                
                response = await send_to_freecad(tool_name, continue_args)
            else:
                response = await send_to_freecad(name, args)
            
            return [types.TextContent(
                type="text",
                text=response
            )]
            
        else:
            return [types.TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    # Optional: Start health monitoring if debugging enabled
    async def health_check_loop():
        """Periodic health check for FreeCAD"""
        if not DEBUG_ENABLED or not monitor:
            return
            
        while True:
            try:
                status = monitor.perform_health_check()
                if not status['is_healthy']:
                    debugger.logger.error("FreeCAD health check FAILED!")
                    monitor.log_crash(status)
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                if debugger:
                    debugger.logger.error(f"Health check error: {e}")
                await asyncio.sleep(30)
    
    # Start health monitoring in background if enabled
    if DEBUG_ENABLED and monitor:
        health_task = asyncio.create_task(health_check_loop())
    
    # Run the server
    import mcp.server.stdio
    
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="freecad",
                    server_version="2.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        # Export debug info on shutdown if debugging enabled
        if DEBUG_ENABLED and debugger:
            debugger.logger.info("="*80)
            debugger.logger.info("MCP Bridge shutting down - exporting debug info")
            debugger.logger.info("="*80)
            
            try:
                # Performance report
                perf_report = debugger.get_performance_report()
                debugger.logger.info(f"\n{perf_report}")
                
                # Export debug package
                debug_pkg = debugger.export_debug_package()
                debugger.logger.info(f"Debug package: {debug_pkg}")
                
                # Export crash report if there were crashes
                if monitor and monitor.crash_history:
                    crash_report = monitor.export_crash_report()
                    debugger.logger.info(f"Crash report: {crash_report}")
                    stats = monitor.get_crash_statistics()
                    debugger.logger.info(f"Crash statistics: {stats}")
            except Exception as e:
                if debugger:
                    debugger.logger.error(f"Error during shutdown export: {e}")

if __name__ == "__main__":
    asyncio.run(main())

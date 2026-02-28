# FreeCAD MCP — Instructions for Claude

You have access to 26 MCP tools for controlling FreeCAD. Follow these instructions when using them.

## Mandatory Rules

### ALWAYS check connection first

Call `check_freecad_connection` before any operation. FreeCAD must be running with the AICopilot workbench loaded. If it fails, tell the user to launch FreeCAD.

### Use execute_python as escape hatch

If a dedicated tool doesn't support what you need, use `execute_python`. You have full access to `FreeCAD`, `FreeCADGui`, `Part`, `Draft`, and `FreeCAD.Vector`. The last expression's value is returned automatically (like Jupyter).

### Handle interactive selection workflows

When `partdesign_operations` returns `{"status": "awaiting_selection"}`, the user must select edges or faces in FreeCAD's 3D view. Then call `continue_selection` with the `operation_id` from the response. Do NOT proceed without user confirmation that they've made a selection.

### Create documents before creating objects

Due to a GIL threading issue, always create the document first with `view_control(operation="create_document")`, then create objects in a separate call. Do NOT rely on auto-document-creation inside primitives — it can deadlock FreeCAD.

### NEVER modify socket_server.py and handlers/ in parallel

The socket server must stay under 750 lines. Business logic goes in `handlers/` — never embed operation implementations in `socket_server.py`.

## Workflow

### Creating a Parametric Part (Sketch → Pad workflow)

```
check_freecad_connection()
view_control(operation="create_document", document_name="MyPart")

# Create a constrained sketch
sketch_operations(operation="create_sketch", plane="XY", name="BaseSketch")
sketch_operations(operation="add_rectangle", sketch_name="BaseSketch", x=0, y=0, width=50, height=30)
sketch_operations(operation="add_constraint", sketch_name="BaseSketch", constraint_type="DistanceX",
    geo_id1=0, pos_id1=1, geo_id2=0, pos_id2=2, value=50)    # width = 50mm
sketch_operations(operation="add_constraint", sketch_name="BaseSketch", constraint_type="DistanceY",
    geo_id1=1, pos_id1=1, geo_id2=1, pos_id2=2, value=30)    # height = 30mm
sketch_operations(operation="close_sketch", sketch_name="BaseSketch")
sketch_operations(operation="verify_sketch", sketch_name="BaseSketch")

# Pad to solid
partdesign_operations(operation="pad", sketch_name="BaseSketch", length=10)

# Add features
partdesign_operations(operation="fillet", object_name="Pad", radius=2.0)
  → if awaiting_selection: user selects edges, then continue_selection(operation_id=...)

view_control(operation="fit_all")
view_control(operation="screenshot")
```

### Sketch Constraint Reference

```
# Geometry IDs: assigned sequentially from 0. Special: -1=X axis, -2=Y axis
# Point IDs: 0=edge, 1=start, 2=end, 3=center

# Geometric constraints (no value)
sketch_operations(operation="add_constraint", constraint_type="Horizontal", geo_id1=0)
sketch_operations(operation="add_constraint", constraint_type="Perpendicular", geo_id1=0, geo_id2=1)
sketch_operations(operation="add_constraint", constraint_type="Coincident",
    geo_id1=0, pos_id1=2, geo_id2=1, pos_id2=1)

# Dimensional constraints (require value)
sketch_operations(operation="add_constraint", constraint_type="Distance",
    geo_id1=0, pos_id1=1, geo_id2=0, pos_id2=2, value=25)    # 25mm between points
sketch_operations(operation="add_constraint", constraint_type="Radius", geo_id1=2, value=5)
sketch_operations(operation="add_constraint", constraint_type="Angle",
    geo_id1=0, geo_id2=1, value=45)    # 45 degrees

# Inspect
sketch_operations(operation="list_constraints", sketch_name="Sketch")
```

### CAM Toolpath Generation

```
cam_tools(operation="create_tool", tool_type="endmill", diameter=6.0, name="6mm Endmill")
cam_operations(operation="create_job", base_object="Body")
cam_tool_controllers(operation="add_tool_controller", tool_name="6mm Endmill", spindle_speed=12000)
cam_operations(operation="profile", ...) or "pocket", "adaptive", "drilling"
cam_operations(operation="post_process", filename="output.gcode")
```

### Modifying Existing Objects

```
view_control(operation="list_objects")                    # See what's in the document
execute_python(code="obj = FreeCAD.ActiveDocument.Box; obj.Length = 100; obj.recompute()")
view_control(operation="screenshot")
```

## Tool Selection

| I need to... | Use this |
|---|---|
| Create a sketch | `sketch_operations(operation="create_sketch", plane="XY")` |
| Add geometry to sketch | `sketch_operations(operation="add_rectangle\|add_line\|add_circle\|add_arc\|add_polygon\|add_slot")` |
| Constrain sketch geometry | `sketch_operations(operation="add_constraint", constraint_type="...")` |
| Inspect sketch constraints | `sketch_operations(operation="list_constraints")` |
| Close sketch profile | `sketch_operations(operation="close_sketch")` |
| Check sketch is valid | `sketch_operations(operation="verify_sketch")` |
| Create box / cylinder / sphere | `part_operations(operation="box\|cylinder\|sphere")` |
| Boolean union / subtract / intersect | `part_operations(operation="fuse\|cut\|common")` |
| Move / rotate / copy an object | `part_operations(operation="move\|rotate\|copy")` |
| Pad a sketch into a solid | `partdesign_operations(operation="pad")` |
| Cut a pocket from a solid | `partdesign_operations(operation="pocket")` |
| Fillet or chamfer edges | `partdesign_operations(operation="fillet\|chamfer")` — triggers selection |
| Shell / thickness a solid | `partdesign_operations(operation="shell\|thickness")` |
| Drill holes | `partdesign_operations(operation="hole")` — triggers selection |
| Mirror / pattern features | `partdesign_operations(operation="mirror\|linear_pattern\|polar_pattern")` |
| Create a datum plane | `partdesign_operations(operation="datum_plane", map_mode="FlatFace", reference="Face1", offset_z=10)` |
| Take a screenshot | `view_control(operation="screenshot")` |
| List objects in document | `view_control(operation="list_objects")` |
| Create / save document | `view_control(operation="create_document\|save_document")` |
| Undo / redo | `view_control(operation="undo\|redo")` |
| Generate CNC toolpaths | `cam_operations` + `cam_tools` + `cam_tool_controllers` |
| Store parametric values | `spreadsheet_operations` |
| Run arbitrary FreeCAD code | `execute_python` |
| Debug a failed operation | `get_debug_logs` |
| Spawn a headless FC instance | `spawn_freecad_instance` |
| List / switch instances | `list_freecad_instances` / `select_freecad_instance` |
| Stop a spawned instance | `stop_freecad_instance` |
| Hot-reload after code deploy | `reload_modules` |

## Known Issues

### GIL deadlock on document creation (FIXED)
`FreeCAD.newDocument()` called from the socket thread used to deadlock the Qt GUI. Fixed in `base.py` `get_document()` which calls `newDocument()` directly — safe because handlers always run on the GUI thread via `_call_on_gui_thread`.

### Large documents
`list_objects` paginates (default 100, max 500). For documents with 1000+ objects (DXF imports), use `offset`, `limit`, and `type_filter` parameters.

### Fillet/chamfer requires GUI selection
These operations cannot programmatically select edges — the user must click edges in FreeCAD's 3D view. The server returns `awaiting_selection` status, then you call `continue_selection` after the user picks.

## Technical Notes

- **Bridge** (`working_bridge.py`): 25 MCP tools, async, communicates via MCP protocol over stdio
- **Socket server** (`AICopilot/socket_server.py` v5.4.0): 25 dispatch routes, runs inside FreeCAD
- **Message protocol**: Length-prefixed JSON (4-byte uint32 BE + UTF-8), 50KB max message size
- **Socket**: Unix domain at `/tmp/freecad_mcp.sock` (TCP `localhost:23456` on Windows)
- **Handlers**: 14 classes in `AICopilot/handlers/`, each inherits `BaseHandler`, returns strings
- **GUI thread safety**: Operations touching FreeCAD GUI use `_run_on_gui_thread()` / Qt task queues; headless mode runs inline (no queue)
- **Instance management**: `_BridgeCtx` tracks spawned headless instances; `FREECAD_MCP_SOCKET` selects the active instance
- **Headless entry point**: `AICopilot/headless_server.py` — launched by bridge via `FreeCADCmd`
- **Debug logs**: `/tmp/freecad_mcp_debug/` (optional, auto-enabled if `freecad_debug.py` present)
- **Crash logs**: `/tmp/freecad_mcp_crashes/` (optional, auto-enabled if `freecad_health.py` present)

## Development

### Paths
- **Dev repo**: `/Volumes/Files/claude/freecad-mcp/`
- **FreeCAD module (actual load path)**: `/Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/` ← FreeCAD 1.2 actually loads from here (verified Feb 2026)
- **FreeCAD module (legacy path)**: `/Volumes/Files/claude/FreeCAD-prefs/Mod/AICopilot/` — NOT used by FreeCAD 1.2
- **Bridge install**: `~/.freecad-mcp/`

To verify which path FreeCAD loads: `execute_python("import os, AICopilot.handlers.cam_ops as m; os.path.realpath(m.__file__)")`

### Commands
```bash
python3 -m pytest                 # Run 174 unit tests (no FreeCAD needed)
rsync -av --delete AICopilot/ /Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/
cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
# After rsync, hot-reload without restarting FreeCAD:
#   reload_modules()
```

### Environment Variables
| Variable | Purpose | Default |
|---|---|---|
| `FREECAD_MCP_SOCKET` | Unix socket path for active FC instance | `/tmp/freecad_mcp.sock` |
| `FREECAD_MCP_FREECAD_BIN` | Override path to `FreeCADCmd` binary | auto-detected |
| `FREECAD_MCP_MODULE_DIR` | Override path to `AICopilot/` module dir | auto-detected |

### Versions
- socket_server.py: v5.4.0 (target 700–750 lines)
- freecad_debug.py: v1.1.0
- freecad_health.py: v1.0.1

Never regress version numbers. If `socket_server.py` exceeds 800 lines, extract logic to a handler.

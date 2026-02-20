# FreeCAD MCP — Instructions for Claude

You have access to 17 MCP tools for controlling FreeCAD. Follow these instructions when using them.

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

### Creating a Parametric Part

```
check_freecad_connection()
view_control(operation="create_document", document_name="MyPart")
part_operations(operation="box", length=50, width=30, height=10)
partdesign_operations(operation="fillet", object_name="Box", radius=2.0)
  → if awaiting_selection: user selects edges, then continue_selection(operation_id=...)
view_control(operation="fit_all")
view_control(operation="screenshot")
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
| Create box / cylinder / sphere | `part_operations(operation="box\|cylinder\|sphere")` |
| Boolean union / subtract / intersect | `part_operations(operation="fuse\|cut\|common")` |
| Move / rotate / copy an object | `part_operations(operation="move\|rotate\|copy")` |
| Pad a sketch into a solid | `partdesign_operations(operation="pad")` |
| Fillet or chamfer edges | `partdesign_operations(operation="fillet\|chamfer")` — triggers selection |
| Drill holes | `partdesign_operations(operation="hole")` — triggers selection |
| Mirror / pattern features | `partdesign_operations(operation="mirror\|linear_pattern")` |
| Take a screenshot | `view_control(operation="screenshot")` |
| List objects in document | `view_control(operation="list_objects")` |
| Create / save document | `view_control(operation="create_document\|save_document")` |
| Undo / redo | `view_control(operation="undo\|redo")` |
| Generate CNC toolpaths | `cam_operations` + `cam_tools` + `cam_tool_controllers` |
| Store parametric values | `spreadsheet_operations` |
| Run arbitrary FreeCAD code | `execute_python` |
| Debug a failed operation | `get_debug_logs` |

## Known Issues

### GIL deadlock on document creation
`FreeCAD.newDocument()` called from the socket thread deadlocks the Qt GUI. Fixed in `spreadsheet_ops.py`, still unfixed in `primitives.py` and `sketch_ops.py`. **Workaround:** always create documents via `view_control(operation="create_document")` first.

### Large documents
`list_objects` paginates (default 100, max 500). For documents with 1000+ objects (DXF imports), use `offset`, `limit`, and `type_filter` parameters.

### Fillet/chamfer requires GUI selection
These operations cannot programmatically select edges — the user must click edges in FreeCAD's 3D view. The server returns `awaiting_selection` status, then you call `continue_selection` after the user picks.

## Technical Notes

- **Bridge** (`working_bridge.py`): 17 MCP tools, async, communicates via MCP protocol over stdio
- **Socket server** (`AICopilot/socket_server.py` v5.0.0): 25 dispatch routes, 732 lines, runs inside FreeCAD
- **Message protocol**: Length-prefixed JSON (4-byte uint32 BE + UTF-8), 50KB max message size
- **Socket**: Unix domain at `/tmp/freecad_mcp.sock` (TCP `localhost:23456` on Windows)
- **Handlers**: 14 classes in `AICopilot/handlers/`, each inherits `BaseHandler`, returns strings
- **GUI thread safety**: Operations touching FreeCAD GUI use `_run_on_gui_thread()` / Qt task queues
- **Debug logs**: `/tmp/freecad_mcp_debug/` (optional, auto-enabled if `freecad_debug.py` present)
- **Crash logs**: `/tmp/freecad_mcp_crashes/` (optional, auto-enabled if `freecad_health.py` present)

## Development

### Paths
- **Dev repo**: `/Volumes/Files/claude/freecad-mcp/`
- **FreeCAD module (actual load path)**: `/Volumes/Files/claude/FreeCAD-prefs/Mod/AICopilot/` ← FreeCAD actually loads from here (verified Feb 2026)
- **FreeCAD module (v1-2 path)**: `/Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/`
- **Bridge install**: `~/.freecad-mcp/`

To verify which path FreeCAD loads: `execute_python("import os, AICopilot.handlers.cam_ops as m; os.path.realpath(m.__file__)")`

### Commands
```bash
python3 -m pytest                 # Run 127 unit tests (no FreeCAD needed)
rsync -av --delete AICopilot/ /Volumes/Files/claude/FreeCAD-prefs/Mod/AICopilot/
rsync -av --delete AICopilot/ /Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/
cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
```

### Versions
- socket_server.py: v5.0.0 (target 700–750 lines)
- freecad_debug.py: v1.1.0
- freecad_health.py: v1.0.1

Never regress version numbers. If `socket_server.py` exceeds 800 lines, extract logic to a handler.

# FreeCAD MCP API Guide for Claude

Quick reference for using the FreeCAD MCP tools effectively.

## Architecture Overview

```
Claude Code
    ↓ (MCP Protocol via stdio)
working_bridge.py (MCP Server)
    ↓ (JSON over Unix socket: /tmp/freecad_mcp.sock)
socket_server.py (runs inside FreeCAD)
    ↓ (routes to handlers)
handlers/*.py (13 handler classes)
    ↓
FreeCAD Python API
```

## Available Tools (9 Smart Dispatchers + 4 Utilities)

### 1. part_operations
**Purpose:** Create and manipulate Part workbench solids

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `box` | length, width, height, x, y, z, name | Create box primitive |
| `cylinder` | radius, height, x, y, z, name | Create cylinder |
| `sphere` | radius, x, y, z, name | Create sphere |
| `cone` | radius1, radius2, height, name | Create cone |
| `torus` | radius1, radius2, name | Create torus |
| `wedge` | name | Create wedge |
| `fuse` | objects (array), name | Boolean union |
| `cut` | base, tools (array), name | Boolean subtraction |
| `common` | objects (array), name | Boolean intersection |
| `section` | objects (array), name | Cross-section |
| `move` | object_name, x, y, z | Translate object |
| `rotate` | object_name, angle, axis | Rotate object |
| `scale` | object_name, scale_factor | Scale object |
| `mirror` | object_name, axis | Mirror object |
| `loft` | sketches (array), name | Loft between profiles |
| `sweep` | profile_sketch, path_sketch, name | Sweep along path |
| `extrude` | name | Extrude sketch |
| `revolve` | name | Revolve sketch |

**Example:**
```python
mcp__freecad__part_operations(operation="box", length=100, width=50, height=25, name="MyBox")
mcp__freecad__part_operations(operation="fuse", objects=["Box", "Cylinder"], name="Fused")
```

### 2. partdesign_operations
**Purpose:** PartDesign workbench parametric features (Body-based modeling)

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `pad` | sketch_name, length, name | Extrude sketch |
| `revolution` | sketch_name, angle, name | Revolve sketch |
| `loft` | sketches, name | Loft profiles |
| `sweep` | sketch_name, name | Sweep along spine |
| `additive_pipe` | sketch_name, name | Additive pipe |
| `groove` | sketch_name, length, name | Subtractive extrusion |
| `subtractive_sweep` | sketch_name, name | Subtractive sweep |
| `fillet` | object_name, radius, name | Round edges (requires selection) |
| `chamfer` | object_name, distance, name | Bevel edges (requires selection) |
| `mirror` | feature_name, plane, name | Mirror feature |
| `hole` | x, y, diameter, depth, name | Create hole |
| `counterbore` | x, y, diameter, depth, name | Counterbore hole |
| `countersink` | x, y, diameter, depth, name | Countersink hole |

**Note:** `fillet` and `chamfer` require edge selection - they return `awaiting_selection` status. Use `continue_selection` after user selects edges.

### 3. view_control
**Purpose:** View, document, and selection management

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `screenshot` | filename, width, height | Save screenshot |
| `set_view` | view_type | top/front/left/right/isometric/axonometric |
| `fit_all` | - | Fit view to all objects |
| `zoom_in` | - | Zoom in |
| `zoom_out` | - | Zoom out |
| `create_document` | document_name | Create new document |
| `save_document` | filename | Save document |
| `list_objects` | - | List all objects in document |
| `select_object` | object_name | Select object |
| `clear_selection` | - | Clear selection |
| `get_selection` | - | Get current selection |
| `hide_object` | object_name | Hide object |
| `show_object` | object_name | Show object |
| `delete_object` | object_name | Delete object |
| `undo` | - | Undo last operation |
| `redo` | - | Redo operation |
| `activate_workbench` | workbench_name | Switch workbench |

**Example:**
```python
mcp__freecad__view_control(operation="list_objects")
mcp__freecad__view_control(operation="screenshot", filename="/tmp/model.png", width=1920, height=1080)
```

### 4. spreadsheet_operations
**Purpose:** Spreadsheet data management

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `create_spreadsheet` | name | Create new spreadsheet |
| `set_cell` | name, cell, value | Set cell value |
| `get_cell` | name, cell | Get cell value |
| `set_alias` | name, cell, alias | Set cell alias |
| `get_alias` | name, cell | Get cell alias |
| `clear_cell` | name, cell | Clear cell |
| `set_cell_range` | name, start_cell, values | Set range of cells |
| `get_cell_range` | name, start_cell, end_cell | Get range of cells |

**Known Issue:** Spreadsheet lookup may fail. Use `execute_python` as fallback:
```python
mcp__freecad__execute_python(code="""
import FreeCAD
doc = FreeCAD.ActiveDocument
ss = doc.getObject('Spreadsheet')
ss.get('B3')
""")
```

### 5. cam_operations
**Purpose:** CAM/CNC toolpath generation

**Job Management:**
| Operation | Description |
|-----------|-------------|
| `create_job` | Create CAM job |
| `setup_stock` | Configure stock material |
| `configure_job` | Configure job settings |
| `inspect_job` | Inspect job details |
| `job_status` | Get job status |
| `delete_job` | Delete job |

**Toolpath Operations:**
| Operation | Description |
|-----------|-------------|
| `profile` | Profile/contour operation |
| `pocket` | 2D pocket operation |
| `adaptive` | Adaptive clearing |
| `face` | Face milling |
| `drilling` | Drilling operation |
| `surface` | 3D surface operation |
| `waterline` | Waterline finishing |

**Post-processing:**
| Operation | Description |
|-----------|-------------|
| `simulate` | Simulate toolpath |
| `post_process` | Generate G-code |
| `export_gcode` | Export G-code file |

### 6. cam_tools
**Purpose:** Tool library management

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `create_tool` | name, tool_type, diameter | Create cutting tool |
| `list_tools` | - | List all tools |
| `get_tool` | tool_name | Get tool details |
| `update_tool` | tool_name, ... | Update tool |
| `delete_tool` | tool_name | Delete tool |

**Tool types:** endmill, ballend, bullnose, chamfer, drill, v-bit

### 7. cam_tool_controllers
**Purpose:** Link tools to jobs with speeds/feeds

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `add_tool_controller` | job_name, tool_name, spindle_speed, feed_rate | Add controller |
| `list_tool_controllers` | job_name | List controllers |
| `get_tool_controller` | job_name, controller_name | Get controller |
| `update_tool_controller` | job_name, controller_name, ... | Update controller |
| `remove_tool_controller` | job_name, controller_name | Remove controller |

### 8. draft_operations
**Purpose:** 2D draft operations and arrays

| Operation | Key Parameters | Description |
|-----------|----------------|-------------|
| `clone` | object_name | Clone object |
| `array` | object_name, count, spacing | Linear array |
| `polar_array` | object_name, count, angle | Polar array |
| `path_array` | object_name | Array along path |
| `point_array` | object_name | Array at points |

### 9. execute_python
**Purpose:** Execute arbitrary Python in FreeCAD context

```python
mcp__freecad__execute_python(code="""
import FreeCAD
doc = FreeCAD.ActiveDocument

# Your Python code here
result = "some value"
result  # Last expression is returned
""")
```

**Tips:**
- Full access to `FreeCAD`, `FreeCADGui`, `Part`, etc.
- Last expression value is returned (like Jupyter)
- Use for operations not covered by other tools
- Use as fallback when other tools fail

### 10. get_debug_logs
**Purpose:** Retrieve operation logs for troubleshooting

```python
mcp__freecad__get_debug_logs(count=20)
mcp__freecad__get_debug_logs(count=50, operation="cam_operations")
```

### 11. continue_selection
**Purpose:** Complete interactive selection (for fillet, chamfer, etc.)

When a tool returns `{"status": "awaiting_selection", "operation_id": "..."}`:
1. User selects edges/faces in FreeCAD GUI
2. Call `continue_selection` with the operation_id

```python
mcp__freecad__continue_selection(operation_id="fillet_1234567890")
```

### 12. check_freecad_connection
**Purpose:** Verify FreeCAD is running with AI Copilot

```python
mcp__freecad__check_freecad_connection()
# Returns: {"freecad_socket_exists": true, "socket_path": "/tmp/freecad_mcp.sock", "status": "..."}
```

### 13. test_echo
**Purpose:** Simple connectivity test

```python
mcp__freecad__test_echo(message="Hello")
# Returns: {"echo": "Hello"}
```

## Common Patterns

### Reading Spreadsheet Parameters (Reliable Method)
```python
mcp__freecad__execute_python(code="""
import FreeCAD
doc = FreeCAD.ActiveDocument
ss = doc.getObject('Spreadsheet')  # Use object name, not label

# Get all aliased parameters
cells = ss.getUsedCells()
params = {}
for cell in cells:
    alias = ss.getAlias(cell)
    if alias:
        params[alias] = ss.get(cell)
params
""")
```

### Creating a Simple Model
```python
# 1. Check connection
mcp__freecad__check_freecad_connection()

# 2. Create document
mcp__freecad__view_control(operation="create_document", document_name="MyModel")

# 3. Create primitives
mcp__freecad__part_operations(operation="box", length=100, width=50, height=25, name="Base")
mcp__freecad__part_operations(operation="cylinder", radius=10, height=50, x=50, y=25, z=25, name="Pin")

# 4. Boolean union
mcp__freecad__part_operations(operation="fuse", objects=["Base", "Pin"], name="Combined")

# 5. Screenshot
mcp__freecad__view_control(operation="fit_all")
mcp__freecad__view_control(operation="screenshot", filename="/tmp/model.png")
```

### Listing Document Contents
```python
mcp__freecad__view_control(operation="list_objects")
# Returns: {"total": N, "objects": [{"name": "...", "type": "...", "label": "..."}]}
```

### Working with Active Document
```python
mcp__freecad__execute_python(code="""
import FreeCAD
doc = FreeCAD.ActiveDocument
f"Document: {doc.Name}, Objects: {len(doc.Objects)}"
""")
```

## Troubleshooting

### "Spreadsheet not found"
The `spreadsheet_operations` tool has lookup issues. Use `execute_python` instead:
```python
mcp__freecad__execute_python(code="""
import FreeCAD
ss = FreeCAD.ActiveDocument.getObject('Spreadsheet')
ss.get('A1') if ss else 'No spreadsheet found'
""")
```

### "No active document"
```python
mcp__freecad__view_control(operation="create_document", document_name="Untitled")
```

### Check What's Open
```python
mcp__freecad__execute_python(code="""
import FreeCAD
list(FreeCAD.listDocuments().keys())
""")
```

### Object Not Found
Use `list_objects` to see exact object names:
```python
mcp__freecad__view_control(operation="list_objects")
```

Note: Objects have both `Name` (internal, immutable) and `Label` (display name, can change). The API uses `Name`.

## Parameter Reference

### part_operations defaults
- `length`, `width`, `height`: 10mm
- `radius`: 5mm
- `radius1`, `radius2`: 10mm, 3mm
- `x`, `y`, `z`: 0
- `angle`: 90°
- `axis`: "z"
- `scale_factor`: 1.5

### partdesign_operations defaults
- `length`: 10mm
- `radius`: 1mm (fillet)
- `distance`: 1mm (chamfer)
- `angle`: 360° (revolution)
- `plane`: "YZ" (mirror)
- `diameter`: 6mm (hole)
- `depth`: 10mm (hole)

### view_control defaults
- `width`, `height`: 800, 600 (screenshot)
- `view_type`: "isometric"

### cam_operations defaults
- `diameter`: 6mm
- `feed_rate`: 1000 mm/min
- `spindle_speed`: 10000 RPM
- `stock_type`: "CreateBox"
- `post_processor`: "grbl"

## Version Info

- **socket_server.py**: v4.0.1
- **working_bridge.py**: v3.x
- **Message protocol**: Length-prefixed JSON (4-byte uint32 BE + UTF-8 payload)
- **Socket path**: `/tmp/freecad_mcp.sock` (Unix) or `localhost:23456` (Windows)

## Files Reference

| File | Location | Purpose |
|------|----------|---------|
| working_bridge.py | repo root | MCP server (Claude interface) |
| socket_server.py | AICopilot/ | FreeCAD socket server |
| handlers/*.py | AICopilot/handlers/ | Operation handlers (13 classes) |
| mcp_bridge_framing.py | repo root | Message framing protocol |
| Init.py, InitGui.py | AICopilot/ | FreeCAD workbench initialization |

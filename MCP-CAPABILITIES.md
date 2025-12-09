# FreeCAD MCP Server - Available Capabilities

**FOR CLAUDE DESKTOP: You have MCP access to FreeCAD! Read this file to know what you can do.**

## Connection Status

The FreeCAD MCP server is configured in your Claude Desktop config at:
`~/Library/Application Support/Claude/claude_desktop_config.json`

When FreeCAD is running with AI Copilot workbench, you have access to ALL the tools below.

## Available MCP Tools

### Smart Dispatchers (High-Level)

These are your main entry points - they route to specific operations:

#### `partdesign_operations`
**PartDesign workbench operations (parametric modeling)**
- `pad` - Extrude sketch into solid
- `fillet` - Round edges (uses UniversalSelector for edge selection)
- `chamfer` - Bevel edges (uses UniversalSelector)
- `hole` - Create holes with wizard
- `linear_pattern` - Linear array of features
- `mirror` - Mirror features across plane
- `revolution` - Revolve sketch around axis
- `loft` - Loft between multiple profiles
- `sweep` - Sweep profile along path
- `draft` - Add draft angle to faces
- `shell` - Hollow out solid

#### `part_operations`
**Part workbench operations (direct solid modeling)**
- **Primitives**: `box`, `cylinder`, `sphere`, `cone`, `torus`, `wedge`
- **Booleans**: `fuse`, `cut`, `common`
- **Transforms**: `move`, `rotate`, `copy`, `array`
- **Advanced**: `extrude`, `revolve`, `loft`, `sweep`

#### `view_control`
**View and document management**
- `screenshot` - Take screenshot, returns base64 image
- `set_view` - Set camera view (front, top, iso, etc.)
- `fit_all` - Fit all objects in view
- `zoom_in` / `zoom_out` - Camera zoom
- `create_document` - Create new FreeCAD document
- `save_document` - Save document
- `list_objects` - List all objects in document
- `select_object` - Select object in GUI
- `clear_selection` - Clear selection
- `get_selection` - Get current selection

#### `cam_operations`
**CAM/Path workbench (CNC toolpath generation)**
- `create_job` - Create CAM job
- `add_profile` - Profile operation
- `add_pocket` - Pocket operation
- `add_drilling` - Drilling operation
- `add_adaptive` - Adaptive clearing
- `export_gcode` - Export G-code

#### `draft_operations`
**Draft workbench (2D drafting, parametric 2D)**
- Various 2D shapes and annotation tools

#### `spreadsheet_operations`
**Spreadsheet workbench**
- Create and manipulate spreadsheets for parametric values

### Direct Operation Tools

You can also call primitives directly:
- `create_box` - Create box with dimensions
- `create_cylinder` - Create cylinder
- `create_sphere` - Create sphere
- `create_cone` - Create cone
- `create_torus` - Create torus
- `create_wedge` - Create wedge

Boolean operations:
- `fuse_objects` - Union/combine objects
- `cut_objects` - Subtract objects
- `common_objects` - Intersection of objects

### Python Execution

#### `execute_python`
**Execute arbitrary Python code in FreeCAD context**
- Full access to FreeCAD and FreeCADGui APIs
- Can inspect objects, modify parameters, create complex operations
- Use when operations aren't available via other tools

Example:
```python
{
  "tool": "execute_python",
  "args": {
    "code": "doc = FreeCAD.ActiveDocument; print([obj.Name for obj in doc.Objects])"
  }
}
```

## Universal Selector System

**Important**: Operations like `fillet` and `chamfer` use a **human-in-the-loop** workflow:

1. You call the tool with the object name
2. System responds with `"status": "awaiting_selection"`
3. User selects edges/faces in FreeCAD GUI
4. User tells you "done" or similar
5. You call `complete_selection` with the `operation_id`
6. System performs the operation with the selected elements

**You don't need to guess edge numbers!** The user selects them visually.

## Modal Command Workflow

For certain operations (especially fillet, chamfer, hole), the system can **open native FreeCAD dialogs**:

```
User: "Add 5mm fillet to TestBox"
You: Call partdesign_operations with fillet
System: Opens FreeCAD's native fillet tool
User: Selects edges in FreeCAD and clicks OK
System: Reports success
```

**This is the preferred workflow** - it uses familiar FreeCAD UI.

## Example Usage Patterns

### Creating a Simple Part
```
1. create_box (or part_operations with operation="box")
2. create_sketch on one face
3. pad_sketch to add material
4. fillet_edges to round it
```

### Boolean Operations
```
1. Create two primitives (boxes, cylinders, etc.)
2. Use fuse_objects, cut_objects, or common_objects
3. Result is a new combined object
```

### Taking Screenshots
```
1. fit_all to frame the view
2. set_view to get the desired angle
3. screenshot to capture image (returns base64)
```

### Inspecting the Model
```
1. list_objects to see what's in the document
2. execute_python to inspect properties
3. get_selection to see what user selected
```

## Common Parameters

Most operations accept these common parameters:
- `length`, `width`, `height` - Dimensions
- `radius`, `radius1`, `radius2` - Radii
- `x`, `y`, `z` - Position
- `object_name` - Name of object to operate on
- `sketch_name` - Name of sketch for PartDesign operations
- `angle` - Rotation angle
- `distance` - Offset distance

## Error Handling

Tools return JSON responses:
- Success: `{"result": "Success message..."}`
- Error: `{"error": "Error message..."}`
- Selection needed: `{"status": "awaiting_selection", "operation_id": "..."}`

## What You Can Do

### ✅ You CAN:
- Create 3D primitives
- Perform boolean operations
- Create parametric features (pad, pocket, fillet, etc.)
- Take screenshots and analyze them
- Execute arbitrary Python to inspect/modify models
- Work with the user in a human-in-the-loop workflow
- Generate CAM toolpaths
- Manage documents

### ❌ You CANNOT:
- Control the user's mouse/keyboard directly
- See what's on screen without taking a screenshot
- Know what objects exist without asking (use list_objects)
- Guess edge numbers (use UniversalSelector instead)

## Best Practices

1. **Start with list_objects** to see what's in the document
2. **Take screenshots** to understand the current state
3. **Use fit_all** before screenshots to frame properly
4. **Use UniversalSelector** for operations requiring edge/face selection
5. **Use execute_python** for complex queries or operations not covered by tools
6. **Ask the user** if you need them to select something specific
7. **Use modal workflow** for operations like fillet, chamfer, hole - it's more natural

## Server Version

**Current**: v3.4.0 (2024-12-09)
- Clean handler-based architecture
- 742 lines (down from 4,541)
- All operations properly routed to modular handlers

## Troubleshooting

**If tools aren't working:**
1. Check FreeCAD is running
2. Check AI Copilot workbench is activated
3. Check socket connection: `ls -la /tmp/freecad_mcp.sock` (should exist)
4. Check FreeCAD console for error messages

**If you get "handler not found" errors:**
- The server may need restarting (user can restart FreeCAD)
- Report to user so they can investigate

---

**Remember**: You're a powerful CAD assistant with full programmatic access to FreeCAD. Use it well!

**Last Updated**: 2024-12-09

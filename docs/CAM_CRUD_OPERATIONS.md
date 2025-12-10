# CAM CRUD Operations - Complete Reference

**FreeCAD MCP v3.5.0+ - Comprehensive CAM Tool and Operation Management**

## Overview

The FreeCAD MCP now provides complete CRUD (Create, Read, Update, Delete) operations for all CAM resources:

- **Tools** (`cam_tools`) - Manage cutting tool library
- **Tool Controllers** (`cam_tool_controllers`) - Manage job-specific tool configurations
- **Operations** (`cam_operations`) - Manage toolpath operations
- **Jobs** (`cam_operations`) - Manage complete CAM jobs

This follows a clean, consistent pattern across all resources, making it easy to build complete CNC workflows programmatically.

## Architecture

### Three-Tier Tool System

```
cam_tools              Create and manage tool bits (geometry, dimensions)
       ↓
cam_tool_controllers   Link tools to jobs with speeds/feeds
       ↓
cam_operations         Create operations using tool controllers
```

## Tool Management (`cam_tools`)

### Create Tool

```python
cam_tools(
    operation="create_tool",
    name="6mm_endmill",
    tool_type="endmill",
    diameter=6.0,
    flute_length=20,
    shank_diameter=6,
    number_of_flutes=4
)
```

**Parameters:**
- `name`: Tool name (auto-generated if not provided)
- `tool_type`: `endmill`, `ballend`, `bullnose`, `chamfer`, `drill`, `v-bit`
- `diameter`: Tool diameter in mm (required)
- `flute_length`: Cutting length in mm (optional)
- `shank_diameter`: Shank diameter in mm (optional)
- `number_of_flutes`: Number of flutes (optional)
- `material`: Tool material - HSS, Carbide, etc. (optional)

### List Tools

```python
cam_tools(operation="list_tools")
```

**Returns:**
```
Found 3 tool(s):
  1. 6mm_endmill (endmill, ⌀6.0 mm)
  2. 3mm_ballend (ballend, ⌀3.0 mm)
  3. 10mm_drill (drill, ⌀10.0 mm)
```

### Get Tool Details

```python
cam_tools(
    operation="get_tool",
    tool_name="6mm_endmill"
)
```

**Returns:**
```
Tool: 6mm_endmill
  Type: endmill
  Diameter: 6.0 mm
  Flute Length: 20 mm
  Shank Diameter: 6 mm
  Number of Flutes: 4
```

### Update Tool

```python
cam_tools(
    operation="update_tool",
    tool_name="6mm_endmill",
    diameter=6.2,
    flute_length=25,
    number_of_flutes=2
)
```

### Delete Tool

```python
cam_tools(
    operation="delete_tool",
    tool_name="6mm_endmill"
)
```

**Note:** Cannot delete tools that are in use by tool controllers.

## Tool Controller Management (`cam_tool_controllers`)

Tool controllers link tools to specific jobs with operating parameters.

### Add Tool Controller

```python
cam_tool_controllers(
    operation="add_tool_controller",
    job_name="MyJob",
    tool_name="6mm_endmill",
    controller_name="TC_6mm_endmill",
    spindle_speed=12000,
    feed_rate=1200,
    vertical_feed_rate=600,
    tool_number=1
)
```

**Parameters:**
- `job_name`: CAM job name (required)
- `tool_name`: Name of the tool bit (required)
- `controller_name`: Name for controller (auto-generated if not provided)
- `spindle_speed`: RPM (default: 10000)
- `feed_rate`: Horizontal feed in mm/min (default: 1000)
- `vertical_feed_rate`: Plunge rate in mm/min (default: half of feed_rate)
- `tool_number`: Tool number for G-code (default: 1)

### List Tool Controllers

```python
cam_tool_controllers(
    operation="list_tool_controllers",
    job_name="MyJob"
)
```

**Returns:**
```
Tool controllers in job 'MyJob' (2):
  1. TC_6mm_endmill (T1)
     Tool: 6mm_endmill
     Speed: 12000 RPM, Feed: 1200 mm/min
  2. TC_3mm_ballend (T2)
     Tool: 3mm_ballend
     Speed: 15000 RPM, Feed: 800 mm/min
```

### Get Tool Controller Details

```python
cam_tool_controllers(
    operation="get_tool_controller",
    job_name="MyJob",
    controller_name="TC_6mm_endmill"
)
```

### Update Tool Controller

```python
cam_tool_controllers(
    operation="update_tool_controller",
    job_name="MyJob",
    controller_name="TC_6mm_endmill",
    spindle_speed=15000,
    feed_rate=1500
)
```

### Remove Tool Controller

```python
cam_tool_controllers(
    operation="remove_tool_controller",
    job_name="MyJob",
    controller_name="TC_6mm_endmill"
)
```

**Note:** Cannot remove controllers that are in use by operations.

## Operation Management (`cam_operations`)

### List Operations

```python
cam_operations(
    operation="list_operations",
    job_name="MyJob"
)
```

**Returns:**
```
Operations in job 'MyJob' (3):

  1. Profile001 (Path::FeaturePython)
     Tool Controller: TC_6mm_endmill
     Step Down: 3.0
     Side: Outside
     Direction: CW

  2. Pocket001 (Path::FeaturePython)
     Tool Controller: TC_3mm_ballend
     Step Down: 2.0
     Step Over: 40%
     Cut Mode: Climb
```

### Get Operation Details

```python
cam_operations(
    operation="get_operation",
    job_name="MyJob",
    operation_name="Profile001"
)
```

**Returns detailed operation parameters including depths, feeds, tool controller info.**

### Configure Operation

Update existing operation parameters:

```python
cam_operations(
    operation="configure_operation",
    job_name="MyJob",
    operation_name="Profile001",
    stepdown=2.5,
    cut_mode="Conventional",
    tool_controller="TC_different_tool"
)
```

**Configurable Parameters:**
- `stepdown`: Step down per pass
- `stepover`: Step over percentage
- `cut_mode`: "Climb" or "Conventional"
- `cut_side`: "Inside" or "Outside"
- `direction`: "CW" or "CCW"
- `tool_controller`: Name of tool controller to use

### Delete Operation

```python
cam_operations(
    operation="delete_operation",
    job_name="MyJob",
    operation_name="Profile001"
)
```

## Job Management (`cam_operations`)

### Configure Job

```python
cam_operations(
    operation="configure_job",
    job_name="MyJob",
    output_file="/path/to/output.gcode",
    post_processor="linuxcnc"
)
```

**Parameters:**
- `output_file`: G-code output file path
- `post_processor`: Post processor name (grbl, linuxcnc, smoothie, etc.)

### Inspect Job (Detailed)

```python
cam_operations(
    operation="inspect_job",
    job_name="MyJob"
)
```

**Returns complete job structure:**
```
CAM Job: MyJob
==================================================

Base Model:
  - Part

Stock: Path::FeatureStock
  Dimensions: 100 x 100 x 50

Tool Controllers (2):
  - TC_6mm_endmill: 6mm_endmill @ 12000 RPM
  - TC_3mm_ballend: 3mm_ballend @ 15000 RPM

Operations (3):
  1. Profile001 (Path::FeaturePython)
     Tool Controller: TC_6mm_endmill
  2. Pocket001 (Path::FeaturePython)
     Tool Controller: TC_3mm_ballend
  3. Drilling001 (Path::FeaturePython)
     Tool Controller: TC_6mm_endmill

Output File: /tmp/output.gcode
Post Processor: grbl

Status:
  ✓ Ready for post-processing
```

### Job Status (Quick)

```python
cam_operations(
    operation="job_status",
    job_name="MyJob"
)
```

**Returns:**
```
Job 'MyJob': 3 operation(s), 2 tool(s) - Ready for export
```

### Simulate Job

```python
cam_operations(
    operation="simulate_job",
    job_name="MyJob"
)
```

**Note:** Returns instructions to use FreeCAD's built-in CAM simulator (manual UI required).

### Export G-code

```python
cam_operations(
    operation="export_gcode",
    job_name="MyJob",
    output_file="/path/to/output.gcode",
    post_processor="grbl"
)
```

**Alias for `post_process` operation.**

### Delete Job

```python
cam_operations(
    operation="delete_job",
    job_name="MyJob"
)
```

**Note:** Deletes job and all associated operations and tool controllers.

## Complete Workflow Example

Here's a complete CAM workflow using CRUD operations:

```python
# 1. Create base geometry (assumed already done)
# Box named "Part"

# 2. Create CAM job
cam_operations(
    operation="create_job",
    name="CutoutJob",
    base_object="Part"
)

# 3. Setup stock
cam_operations(
    operation="setup_stock",
    job_name="CutoutJob",
    stock_type="CreateBox",
    length=120,
    width=120,
    height=60
)

# 4. Create cutting tools
cam_tools(
    operation="create_tool",
    name="6mm_endmill",
    tool_type="endmill",
    diameter=6.0,
    flute_length=25,
    number_of_flutes=4
)

cam_tools(
    operation="create_tool",
    name="3mm_drill",
    tool_type="drill",
    diameter=3.0
)

# 5. Add tool controllers to job
cam_tool_controllers(
    operation="add_tool_controller",
    job_name="CutoutJob",
    tool_name="6mm_endmill",
    spindle_speed=12000,
    feed_rate=1200,
    tool_number=1
)

cam_tool_controllers(
    operation="add_tool_controller",
    job_name="CutoutJob",
    tool_name="3mm_drill",
    spindle_speed=8000,
    feed_rate=400,
    tool_number=2
)

# 6. Create profile operation
cam_operations(
    operation="profile",
    job_name="CutoutJob",
    name="ContourCut",
    base_object="Part",
    cut_side="Outside",
    stepdown=3
)

# 7. Assign tool controller to operation
cam_operations(
    operation="configure_operation",
    job_name="CutoutJob",
    operation_name="ContourCut",
    tool_controller="TC_6mm_endmill"
)

# 8. Create drilling operation
cam_operations(
    operation="drilling",
    job_name="CutoutJob",
    name="Holes",
    depth=10,
    peck_depth=2
)

cam_operations(
    operation="configure_operation",
    job_name="CutoutJob",
    operation_name="Holes",
    tool_controller="TC_3mm_drill"
)

# 9. Review job status
cam_operations(
    operation="inspect_job",
    job_name="CutoutJob"
)

# 10. Verify everything looks good
cam_operations(
    operation="list_operations",
    job_name="CutoutJob"
)

cam_tool_controllers(
    operation="list_tool_controllers",
    job_name="CutoutJob"
)

# 11. Generate G-code
cam_operations(
    operation="export_gcode",
    job_name="CutoutJob",
    output_file="/tmp/cutout.gcode",
    post_processor="grbl"
)

# 12. Check final status
cam_operations(
    operation="job_status",
    job_name="CutoutJob"
)
```

## Resource Dependencies

### Tool Dependencies

```
Tool
 └─ Tool Controller(s)
     └─ Operation(s)
```

**Deletion Rules:**
- Cannot delete a tool if it's used by any tool controller
- Cannot delete a tool controller if it's used by any operation
- Deleting a job deletes all its operations and tool controllers
- Deleting a job does NOT delete the tools themselves (they're reusable)

## Error Handling

All operations return clear error messages:

```
Error: Tool 'NonExistent' not found
Error: Cannot delete tool 'endmill' - it is used by tool controller(s): TC_endmill
Error: Job 'MyJob' not found
Error: No parameters to update. Provide stepdown, stepover, cut_mode, cut_side, direction, or tool_controller.
```

## Best Practices

### 1. Tool Library Management

Create a reusable tool library:

```python
# Create standard tools once
for tool_spec in STANDARD_TOOLS:
    cam_tools(operation="create_tool", **tool_spec)

# Reuse across multiple jobs
cam_tools(operation="list_tools")  # Verify library
```

### 2. Tool Controller Naming

Use consistent naming conventions:

```python
controller_name=f"TC_{tool_name}_{job_name}"
```

### 3. Operation Configuration

Configure operations after creation for better error handling:

```python
# Create operation first
cam_operations(operation="profile", ...)

# Then configure/update parameters
cam_operations(operation="configure_operation", ...)
```

### 4. Status Checks

Always verify job status before exporting:

```python
status = cam_operations(operation="job_status", job_name="MyJob")
if "Ready for export" in status:
    cam_operations(operation="export_gcode", ...)
```

### 5. Cleanup

Remove unused resources to keep the document clean:

```python
# Remove unused operations
cam_operations(operation="delete_operation", ...)

# Remove unused tool controllers
cam_tool_controllers(operation="remove_tool_controller", ...)

# Remove unused tools
cam_tools(operation="delete_tool", ...)
```

## Migration from Legacy API

### Old Way (Placeholders)

```python
# Old - returned placeholder messages
cam_operations(operation="create_tool", ...)  # "Please use Tool Library Editor..."
cam_operations(operation="tool_controller", ...)  # "Please add manually..."
```

### New Way (Full CRUD)

```python
# New - fully automated
cam_tools(operation="create_tool", ...)  # Creates tool immediately
cam_tool_controllers(operation="add_tool_controller", ...)  # Creates controller immediately
```

## Compatibility

- **FreeCAD 1.0+**: Full support with new CAM module structure
- **FreeCAD < 1.0**: Limited support with automatic fallback

## See Also

- [CAM_OPERATIONS.md](./CAM_OPERATIONS.md) - Original CAM operations documentation
- [CLAUDE_DESKTOP_CAM_GUIDE.md](./CLAUDE_DESKTOP_CAM_GUIDE.md) - Claude Desktop usage guide
- [CAM_QUICK_REFERENCE.md](./CAM_QUICK_REFERENCE.md) - Quick reference card

## Troubleshooting

### "Path.Tool module not available"

Requires FreeCAD 1.0+ with CAM workbench enabled.

### "Tool 'X' not found"

Verify tool name with `cam_tools(operation="list_tools")`.

### "Cannot delete - in use"

Check dependencies before deleting:
- Tools: List tool controllers
- Tool controllers: List operations

### "Job not ready"

Use `inspect_job` to see what's missing (tools, operations, etc.).

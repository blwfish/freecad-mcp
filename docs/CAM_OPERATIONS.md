# CAM Workbench Operations

The FreeCAD MCP now supports CAM (formerly Path) workbench operations for CNC toolpath generation and machining.

## Overview

The `cam_operations` tool provides access to 28+ CAM operations organized into 6 categories:

1. **Job Management** (2 operations)
2. **Primary Milling Operations** (12 operations)
3. **Drilling Operations** (2 operations)
4. **Dressup Operations** (7 operations)
5. **Tool Management** (2 operations)
6. **Utility Operations** (3 operations)

## Quick Start

### 1. Create a CAM Job

```python
cam_operations(
    operation="create_job",
    name="MyJob",
    base_object="Box"
)
```

### 2. Setup Stock

```python
cam_operations(
    operation="setup_stock",
    job_name="MyJob",
    stock_type="CreateBox",
    length=100,
    width=100,
    height=50
)
```

### 3. Create Toolpath Operations

```python
# Profile (contour) operation
cam_operations(
    operation="profile",
    job_name="MyJob",
    name="ProfileOp",
    base_object="Box",
    cut_side="Outside",
    direction="CW",
    stepdown=5
)

# Pocket operation
cam_operations(
    operation="pocket",
    job_name="MyJob",
    name="PocketOp",
    base_object="Sketch",
    stepover=50,
    stepdown=3,
    cut_mode="Climb"
)

# Drilling operation
cam_operations(
    operation="drilling",
    job_name="MyJob",
    name="DrillingOp",
    depth=10,
    peck_depth=2,
    dwell_time=0.5
)
```

### 4. Generate G-code

```python
cam_operations(
    operation="post_process",
    job_name="MyJob",
    output_file="/tmp/output.gcode",
    post_processor="grbl"
)
```

## Operation Categories

### Job Management

- **create_job** - Create a new CAM job
  - Parameters: `name`, `base_object`

- **setup_stock** - Setup stock material
  - Parameters: `job_name`, `stock_type`, `length`, `width`, `height`
  - Stock types: `CreateBox`, `CreateCylinder`, `FromBase`

### Primary Milling Operations

Fully Implemented:
- **profile** - Profile (contour) cutting
- **pocket** - Pocket milling
- **adaptive** - Adaptive clearing (advanced)
- **drilling** - Drilling operations

Placeholder (use FreeCAD UI):
- **face** - Face milling
- **helix** - Helical cutting
- **slot** - Slot milling
- **engrave** - Engraving
- **vcarve** - V-carving
- **deburr** - Deburring
- **surface** - 3D surface milling
- **waterline** - Waterline strategy
- **pocket_3d** - 3D pocket clearing

### Drilling Operations

- **drilling** - Standard drilling (fully implemented)
- **thread_milling** - Thread milling (placeholder)

### Dressup Operations

All dressups are placeholders - use FreeCAD UI to apply:
- **dogbone** - Dogbone corners
- **lead_in_out** - Lead-in/lead-out paths
- **ramp_entry** - Ramp entry
- **tag** - Holding tags
- **axis_map** - Axis mapping
- **drag_knife** - Drag knife compensation
- **z_correct** - Z-axis correction

### Tool Management

- **create_tool** - Create tool bit (instructions provided)
- **tool_controller** - Setup tool controller (instructions provided)

### Utility Operations

- **simulate** - Simulate toolpaths (instructions provided)
- **post_process** - Generate G-code (fully implemented)
- **inspect** - Inspect job and operations (fully implemented)

## Common Parameters

### Job Parameters
- `job_name` - Name of the CAM job
- `base_object` - Name of the 3D object to machine
- `name` - Name for the operation

### Cutting Parameters
- `stepdown` - Depth per pass
- `stepover` - Overlap percentage
- `cut_side` - "Outside" or "Inside"
- `direction` - "CW" or "CCW"
- `cut_mode` - "Climb" or "Conventional"

### Tool Parameters
- `tool_type` - "endmill", "ballend", "bullnose", "chamfer", "drill"
- `diameter` - Tool diameter in mm
- `spindle_speed` - RPM
- `feed_rate` - mm/min

## Implementation Status

### ✅ Fully Implemented
- Job creation and management
- Stock setup (box and from-base)
- Profile operation
- Pocket operation
- Drilling operation
- Adaptive operation
- Post-processing (G-code generation)
- Inspection

### ℹ️ Placeholder (Manual UI required)
- Face milling
- Helix, Slot, Engrave, V-carve
- Surface and waterline strategies
- All dressup operations
- Tool creation and controller setup
- Simulation

Placeholder operations provide helpful instructions to complete them manually in FreeCAD's CAM workbench UI.

## Example Workflow

```python
# 1. Create job with base object
cam_operations(operation="create_job", name="Job001", base_object="Part")

# 2. Setup stock
cam_operations(
    operation="setup_stock",
    job_name="Job001",
    stock_type="FromBase",
    extent_x=5, extent_y=5, extent_z=5
)

# 3. Add profile operation
cam_operations(
    operation="profile",
    job_name="Job001",
    name="Contour",
    cut_side="Outside",
    stepdown=3
)

# 4. Add pocket operation
cam_operations(
    operation="pocket",
    job_name="Job001",
    name="PocketClear",
    stepover=40,
    stepdown=2
)

# 5. Inspect job
cam_operations(operation="inspect", job_name="Job001")

# 6. Generate G-code
cam_operations(
    operation="post_process",
    job_name="Job001",
    output_file="~/output.gcode",
    post_processor="grbl"
)
```

## Notes

- The CAM workbench requires FreeCAD 1.0+ for best compatibility
- Some operations may require manual configuration in FreeCAD UI
- G-code post-processor options: grbl, linuxcnc, smoothie, etc.
- Always inspect and simulate toolpaths before machining

## Error Handling

If you see "Path (CAM) module not available", ensure:
1. FreeCAD is installed with CAM workbench
2. You're running FreeCAD 1.0 or later
3. The CAM workbench is enabled in Preferences

## Resources

- [FreeCAD CAM Documentation](https://wiki.freecad.org/CAM_Workbench/en)
- [CAM FAQ](https://wiki.freecad.org/Path_FAQ/en)
- [FreeCAD Forum - CAM](https://forum.freecad.org/viewforum.php?f=15)

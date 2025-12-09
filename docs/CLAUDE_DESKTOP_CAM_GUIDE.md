# Claude Desktop - FreeCAD CAM Operations Guide

**For Claude Desktop users working with FreeCAD 1.0+ CAM operations**

## ⚠️ Important: Use MCP Tools, Not Raw Python

FreeCAD 1.0+ has completely reorganized the CAM (formerly Path) workbench module structure. **Raw Python code with old imports will fail.**

### ❌ WRONG - Don't Do This

```python
# This FAILS in FreeCAD 1.0+
import PathScripts.PathPocket as PathPocket
from Path import Job

# These modules don't exist anymore!
```

### ✅ CORRECT - Do This Instead

```
Use the cam_operations MCP tool:

cam_operations(
    operation="pocket",
    job_name="Job",
    name="MyPocket",
    stepover=50
)
```

## Why This Matters

**Module Structure Changed in FreeCAD 1.0:**

| Old (Deprecated) | New (1.0+) |
|-----------------|------------|
| `Path.Job.Create()` | `Path.Main.Job.Create()` |
| `PathScripts.PathPocket` | `Path.Op.Pocket` |
| `PathScripts.PathProfile` | `Path.Op.Profile` |
| `PathScripts.PathDrilling` | `Path.Op.Drilling` |

The `cam_operations` tool handles all of this automatically!

## How to Work with CAM Operations

### 1. Creating a CAM Job

```
Create a new CAM job named "MyJob" for the object "Part"
```

Claude should use:
```
cam_operations(operation="create_job", name="MyJob", base_object="Part")
```

### 2. Setting Up Stock

```
Setup box stock for job "MyJob" with dimensions 100x100x50mm
```

Claude should use:
```
cam_operations(
    operation="setup_stock",
    job_name="MyJob",
    stock_type="CreateBox",
    length=100,
    width=100,
    height=50
)
```

### 3. Adding Operations

**Profile (Contour) Cutting:**
```
cam_operations(
    operation="profile",
    job_name="MyJob",
    name="ContourCut",
    base_object="Part",
    cut_side="Outside",
    stepdown=3
)
```

**Pocket Milling:**
```
cam_operations(
    operation="pocket",
    job_name="MyJob",
    name="PocketOp",
    stepover=50,
    stepdown=2
)
```

**Drilling:**
```
cam_operations(
    operation="drilling",
    job_name="MyJob",
    name="Holes",
    depth=10,
    peck_depth=2
)
```

**Adaptive Clearing:**
```
cam_operations(
    operation="adaptive",
    job_name="MyJob",
    name="AdaptiveClear",
    stepover=40,
    tolerance=0.1
)
```

### 4. Generating G-code

```
cam_operations(
    operation="post_process",
    job_name="MyJob",
    output_file="/tmp/output.gcode",
    post_processor="grbl"
)
```

## Available Operations

The `cam_operations` tool supports:

**Job Management:**
- `create_job` - Create new CAM job
- `setup_stock` - Configure stock material
- `inspect` - List jobs and operations

**Milling Operations:**
- `profile` - Profile/contour cutting (fully automated)
- `pocket` - Pocket milling (fully automated)
- `drilling` - Drilling operations (fully automated)
- `adaptive` - Adaptive clearing (fully automated)
- `face`, `helix`, `slot`, `engrave`, `vcarve` - Manual UI required

**Output:**
- `post_process` - Generate G-code

## Custom Instructions for Claude Desktop

Add this to your Claude Desktop custom instructions:

```
## FreeCAD CAM Operations

When working with FreeCAD CAM/CNC operations:

1. ALWAYS use the `cam_operations` MCP tool
2. NEVER generate raw Python code with PathScripts imports
3. PathScripts.* modules are DEPRECATED in FreeCAD 1.0+
4. The cam_operations tool handles all version compatibility

Available operations: create_job, setup_stock, profile, pocket,
drilling, adaptive, post_process, inspect

Example:
cam_operations(operation="pocket", job_name="Job", name="MyPocket")
```

## In-Chat Correction

If Claude generates wrong code, immediately correct it:

```
Stop! Don't use PathScripts - that's deprecated.
Use the cam_operations MCP tool instead.
```

Then show the correct approach:

```
Instead of raw Python, use:
cam_operations(operation="pocket", job_name="Job", ...)
```

## Common Mistakes to Avoid

### Mistake 1: Generating Raw Python
```python
# ❌ This will fail
import PathScripts.PathPocket as PathPocket
```

**Fix:** Use `cam_operations` tool

### Mistake 2: Using Old Path.Job Syntax
```python
# ❌ This will fail
from Path import Job
job = Path.Job.Create(...)
```

**Fix:** Use `cam_operations(operation="create_job", ...)`

### Mistake 3: Assuming Old Documentation is Current
```
# ❌ Old FreeCAD < 1.0 examples don't work
```

**Fix:** Refer to this guide and CAM_OPERATIONS.md

## Workflow Example

Here's a complete CAM workflow using the tool:

```
User: "Create a CAM job for cutting out the part with a 6mm end mill"

Claude should respond with:
1. cam_operations(operation="create_job", name="CutoutJob", base_object="Part")
2. cam_operations(operation="setup_stock", job_name="CutoutJob", stock_type="FromBase", extent_x=5, extent_y=5, extent_z=5)
3. cam_operations(operation="profile", job_name="CutoutJob", name="Contour", cut_side="Outside", stepdown=3)
4. cam_operations(operation="post_process", job_name="CutoutJob", output_file="/tmp/cutout.gcode")
```

## Troubleshooting

**Error: "No module named 'PathScripts.PathPocket'"**
- This means Claude generated raw Python with old imports
- Correct Claude to use cam_operations tool instead

**Error: "Job not found"**
- Create the job first with `cam_operations(operation="create_job", ...)`
- Make sure job_name matches exactly

**Operations list is empty**
- The job was created but operations failed to add
- Check that you're using cam_operations tool, not raw Python

## More Information

- See `docs/CAM_OPERATIONS.md` for detailed operation parameters
- See `CHANGELOG.md` for v3.0.0 module structure changes
- FreeCAD 1.0+ CAM workbench documentation: https://wiki.freecad.org/CAM_Workbench/en

---

**Remember:** The cam_operations MCP tool is the ONLY supported way to create CAM operations in FreeCAD 1.0+. Raw Python with PathScripts will fail!

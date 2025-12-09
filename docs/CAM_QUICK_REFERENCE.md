# FreeCAD CAM Operations - Quick Reference

**For Claude Desktop: Always use `cam_operations` tool, never raw Python!**

## Basic Workflow

```
1. Create Job:
   cam_operations(operation="create_job", name="Job", base_object="Part")

2. Setup Stock:
   cam_operations(operation="setup_stock", job_name="Job",
                  stock_type="CreateBox", length=100, width=100, height=50)

3. Add Operations:
   cam_operations(operation="profile", job_name="Job", name="Contour")
   cam_operations(operation="pocket", job_name="Job", name="Pocket")

4. Generate G-code:
   cam_operations(operation="post_process", job_name="Job",
                  output_file="/tmp/output.gcode")
```

## Common Operations

| Operation | Example |
|-----------|---------|
| **Job** | `cam_operations(operation="create_job", name="Job")` |
| **Stock** | `cam_operations(operation="setup_stock", job_name="Job", stock_type="CreateBox")` |
| **Profile** | `cam_operations(operation="profile", job_name="Job", cut_side="Outside")` |
| **Pocket** | `cam_operations(operation="pocket", job_name="Job", stepover=50)` |
| **Drilling** | `cam_operations(operation="drilling", job_name="Job", depth=10)` |
| **Adaptive** | `cam_operations(operation="adaptive", job_name="Job", stepover=40)` |
| **G-code** | `cam_operations(operation="post_process", job_name="Job")` |

## Key Parameters

**Stock Types:**
- `CreateBox` - Box with length/width/height
- `FromBase` - Based on model with extent offsets

**Profile Parameters:**
- `cut_side` - "Outside" or "Inside"
- `direction` - "CW" or "CCW"
- `stepdown` - Depth per pass

**Pocket Parameters:**
- `stepover` - Overlap percentage (e.g., 50)
- `stepdown` - Depth per pass
- `cut_mode` - "Climb" or "Conventional"

**Drilling Parameters:**
- `depth` - Final depth
- `peck_depth` - Incremental depth
- `retract_height` - Height to retract
- `dwell_time` - Pause at bottom (seconds)

## ⚠️ Don't Do This

```python
# ❌ WRONG - These fail in FreeCAD 1.0+
import PathScripts.PathPocket
from Path import Job
Path.Job.Create()
```

## ✅ Do This

```
# ✅ CORRECT - Use the MCP tool
cam_operations(operation="create_job", ...)
```

## Module Changes (FreeCAD 1.0+)

The cam_operations tool handles these automatically:

- `Path.Job.Create()` → `Path.Main.Job.Create()`
- `PathScripts.PathPocket` → `Path.Op.Pocket`
- `PathScripts.PathProfile` → `Path.Op.Profile`
- `PathScripts.PathDrilling` → `Path.Op.Drilling`

**You don't need to know these - just use cam_operations!**

---

For details: See `docs/CLAUDE_DESKTOP_CAM_GUIDE.md` and `docs/CAM_OPERATIONS.md`

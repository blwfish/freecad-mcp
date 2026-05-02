# Tool Reference

This MCP server exposes 33 MCP tools for controlling FreeCAD. They are grouped below by function.

---

## Connection & Lifecycle

| Tool | Description |
|---|---|
| `check_freecad_connection` | Check whether FreeCAD is running with AICopilot loaded. Call this before any operation. |
| `restart_freecad` | Save open documents, spawn a fresh FreeCAD instance, and exit the current one. Use when FreeCAD is unresponsive. |
| `reload_modules` | Hot-reload all handler modules without restarting FreeCAD. Use after deploying updated code. |
| `manage_connection` | Bridge-side diagnostics that work even when FreeCAD is down. Actions: `status` (connection health, crash-loop detection), `clear_recovery` (remove corrupt autosave files that cause crash loops), `validate_fcstd` (check a `.FCStd` file's ZIP integrity). |
| `test_echo` | Echo a message back. Useful for verifying the bridge is reachable. |

---

## Parametric Design

| Tool | Description |
|---|---|
| `sketch_operations` | All Sketcher workbench operations: create sketches, add geometry (rectangle, line, circle, arc, polygon, slot), add constraints (Coincident, Horizontal, Distance, Radius, Angle, …), close and verify sketches. |
| `partdesign_operations` | Parametric solid features: pad, pocket, fillet, chamfer, shell, hole, mirror, linear pattern, polar pattern, datum plane, and more. Fillet/chamfer/hole require interactive edge selection in FreeCAD. |
| `part_operations` | Basic Part workbench solids (box, cylinder, sphere, cone, torus) and boolean operations (fuse, cut, common), plus move, rotate, copy, scale, mirror, section, and geometry checking. |
| `draft_operations` | Draft workbench: ShapeString (extrudable 3D text), text annotations, clone, rectangular array, polar array, path array, point array. |
| `spreadsheet_operations` | Create spreadsheets, read/write cells, use named aliases as parametric model inputs. |

---

## CAM / CNC

| Tool | Description |
|---|---|
| `cam_operations` | CAM workbench: create jobs, add profile/pocket/adaptive/drilling operations, set depths and step-overs, post-process to G-code. |
| `cam_tools` | Tool library CRUD: create, list, update, and delete cutting tools (end mills, drills, ball nose, etc.). |
| `cam_tool_controllers` | Tool controller CRUD: link tools to jobs with spindle speed and feed rates. |

---

## Mesh & File I/O

| Tool | Description |
|---|---|
| `mesh_operations` | Import/export meshes (STL, OBJ), convert mesh to solid, validate, simplify, and work with STEP/IGES/BREP files. |

---

## Inspection & Measurement

| Tool | Description |
|---|---|
| `measurement_operations` | Inspect object geometry: list faces (index, normal, centroid, area), bounding box, volume, surface area, center of mass, element counts, solid check, distance between objects. |
| `spatial_query` | Analyze spatial relationships: interference/collision detection, clearance measurement, containment check, face-to-face relationship (parallel, coplanar), batch interference, alignment verification. |
| `run_inspector` | Run design-rule checks on the active document via the FC-tools inspector. |
| `view_control` | View management, screenshots, document operations (create, save, undo/redo), object listing, checkpoint/rollback, cross-document shape insertion, clip planes (section views). |

---

## Python Execution

| Tool | Description |
|---|---|
| `execute_python` | Run arbitrary Python in FreeCAD's context. Full access to `FreeCAD`, `FreeCADGui`, `Part`, `Draft`, `FreeCAD.Vector`. No timeout — safe for long OCCT operations. Use as an escape hatch when dedicated tools fall short. |
| `execute_python_async` | Submit Python code for async execution; returns a `job_id` immediately. Use for operations that would otherwise block (large CAM recomputes, mesh booleans, surface generation). |
| `poll_job` | Poll the status of an async job: `running` (with elapsed seconds), `done` (with result), or `error`. |
| `list_jobs` | List all tracked async jobs and their current status. |
| `cancel_operation` | Cancel the current long-running FreeCAD operation (boolean, thickness, geometry check, etc.). |
| `cancel_job` | Mark a running async job as cancelled. |

---

## Workflow Helpers

| Tool | Description |
|---|---|
| `build_sketch` | Validate and emit a parametric FreeCAD sketch from a JSON layout descriptor. Uses python-solvespace to pre-validate constraints before touching the document. Supports envelope, hline, arch, arch_array, door, and monitor element types. |
| `continue_selection` | Continue an interactive selection workflow after the user has selected edges or faces in FreeCAD's 3D view. Required after fillet, chamfer, hole, and similar operations. |
| `macro_operations` | List, read, and run macros from the user's FreeCAD macro directory. Lets the agent reuse existing automation scripts. |
| `api_introspection` | Live signature and docstring lookup against FreeCAD's running module tree, with fuzzy search across core and workbenches. Use before `execute_python` to avoid wrong-signature errors. |
| `get_debug_logs` | Retrieve structured operation logs from `/tmp/freecad_mcp_debug/`. Each log entry records before/after model state. |

---

## Multi-Instance / Headless

| Tool | Description |
|---|---|
| `spawn_freecad_instance` | Spawn a new headless FreeCAD instance managed by the bridge. Returns socket path and PID. Selects the new instance as the active target by default. |
| `list_freecad_instances` | List all bridge-managed FreeCAD instances (GUI and headless) with their socket paths, PIDs, and labels. |
| `select_freecad_instance` | Switch the active target to a different managed instance. |
| `stop_freecad_instance` | Terminate a headless instance and remove it from the instance registry. |

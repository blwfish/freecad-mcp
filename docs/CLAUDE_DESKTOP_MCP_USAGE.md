# FreeCAD MCP Server - Claude Desktop Usage Guide

**Document Version:** 1.0.0
**Component Versions:** Socket Server 4.0.1, Debug 1.1.0, Health 1.0.1
**Last Updated:** 2025-12-11
**For:** Claude Desktop Users

> **Version Alignment:** This documentation tracks the socket_server.py v4.0.1 release.
> When updating, ensure all component versions in this document match `VERSIONS.md`.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation & Configuration](#installation--configuration)
3. [Available Operations](#available-operations)
4. [Health Check & Debugging](#health-check--debugging)
5. [Advanced Features](#advanced-features)
6. [Troubleshooting](#troubleshooting)
7. [Architecture Overview](#architecture-overview)
8. [Updating This Document](#updating-this-document)

---

## Quick Start

### What is FreeCAD MCP?

FreeCAD MCP is a Model Context Protocol server that allows Claude Desktop to control FreeCAD, the open-source parametric 3D CAD modeler. It enables you to:

- Create and modify 3D models through conversation
- Generate parametric designs programmatically
- Control FreeCAD's native interface with intelligent pre-configuration
- Export G-code for CNC/3D printing
- Take screenshots and manipulate the view
- Execute Python code directly in FreeCAD

### Prerequisites

- **FreeCAD:** Version 1.0+ (1.2-dev recommended)
- **Python:** 3.11+ (must match FreeCAD's Python version)
- **Claude Desktop:** Latest version
- **Platform:** macOS, Linux, or Windows

### First Connection Test

Once configured (see [Installation](#installation--configuration)), test the connection:

**Claude Desktop:**
```
Can you take a screenshot of FreeCAD?
```

**Expected Response:**
```
✅ Screenshot captured successfully
[Base64 image displayed]
```

---

## Installation & Configuration

### Step 1: Install FreeCAD Addon

Copy the `AICopilot` directory to your FreeCAD Mod directory:

**macOS (Development v1.2):**
```bash
rsync -av AICopilot/ ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/
```

**macOS (Stable):**
```bash
rsync -av AICopilot/ ~/Library/Application\ Support/FreeCAD/Mod/AICopilot/
```

**Linux:**
```bash
rsync -av AICopilot/ ~/.local/share/FreeCAD/Mod/AICopilot/
```

**Windows:**
```powershell
xcopy /E /I AICopilot %APPDATA%\FreeCAD\Mod\AICopilot
```

### Step 2: Start FreeCAD

**Important:** The MCP server runs inside FreeCAD. You must:

1. Launch FreeCAD GUI (not `freecadcmd`)
2. Wait for the "AI Copilot Workbench initialized" message in the console
3. Verify socket creation:
   - **macOS/Linux:** `/tmp/freecad_mcp.sock` exists
   - **Windows:** Server listening on `localhost:23456`

### Step 3: Configure Claude Desktop

Edit the Claude Desktop configuration file:

**macOS:**
```bash
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

**Linux:**
```bash
nano ~/.config/Claude/claude_desktop_config.json
```

**Windows:**
```powershell
notepad %APPDATA%\Claude\claude_desktop_config.json
```

**Add this configuration:**

```json
{
  "mcpServers": {
    "freecad": {
      "type": "stdio",
      "command": "/opt/homebrew/bin/python3.11",
      "args": ["/path/to/freecad-mcp/working_bridge.py"],
      "env": {}
    }
  }
}
```

**Platform-Specific Notes:**

| Platform | Python Path | Bridge Path |
|----------|-------------|-------------|
| macOS (Homebrew) | `/opt/homebrew/bin/python3.11` | `~/.freecad-mcp/working_bridge.py` |
| Linux | `/usr/bin/python3.11` | `~/.freecad-mcp/working_bridge.py` |
| Windows | `C:\Python311\python.exe` | `%USERPROFILE%\.freecad-mcp\working_bridge.py` |

**Verify Python Version:**
```bash
/opt/homebrew/bin/python3.11 --version
# Should match FreeCAD's Python: 3.11.x
```

### Step 4: Restart Claude Desktop

After editing the configuration:

1. Quit Claude Desktop completely
2. Restart Claude Desktop
3. The FreeCAD MCP server should appear in the available tools
4. Test with: "List all objects in FreeCAD"

---

## Available Operations

### 1. Document Operations

**Create/Open Documents:**
```
Create a new FreeCAD document called "MyProject"
Open the document at /path/to/file.FCStd
Save the current document as "project.FCStd"
```

**Manage Objects:**
```
List all objects in the document
Delete the object named "Box001"
Hide the "Cylinder" object
Show all hidden objects
```

**Undo/Redo:**
```
Undo the last operation
Redo the last undone operation
```

**Workbenches:**
```
Switch to the PartDesign workbench
Activate the CAM workbench
```

### 2. Part Primitives

**Create Basic Shapes:**
```
Create a box 10mm x 20mm x 30mm
Create a cylinder with radius 5mm and height 10mm
Create a sphere with radius 15mm
Create a cone with radius 8mm and height 12mm
Create a torus with radius 10mm and tube radius 2mm
```

**Parameters:**
- **Box:** length, width, height, x, y, z
- **Cylinder:** radius, height, x, y, z
- **Sphere:** radius, x, y, z
- **Cone:** radius1, radius2, height, x, y, z
- **Torus:** radius1 (ring), radius2 (tube), x, y, z

### 3. Boolean Operations

**Combine Shapes:**
```
Fuse Box and Cylinder together
Cut Cylinder from Box
Find the common volume between Sphere and Box
```

**Operations:**
- **Fuse:** Union of two objects
- **Cut:** Subtract one object from another
- **Common:** Intersection of two objects

### 4. Transformations

**Move Objects:**
```
Move Box to position (10, 20, 30)
Move Cylinder by offset (5, 0, 10)
```

**Rotate Objects:**
```
Rotate Box 45 degrees around the Z axis
Rotate Cylinder 90 degrees around axis (1, 0, 0) at point (0, 0, 0)
```

**Copy & Array:**
```
Create a copy of Box
Create a linear array of Cylinder with 5 copies, spacing 10mm in X direction
```

### 5. Sketch Operations

**Create Sketches:**
```
Create a sketch on the XY plane
Create a sketch on the top face of Box
```

**Add Geometry:**
```python
# Via execute_python tool
sketch = App.ActiveDocument.addObject('Sketcher::SketchObject', 'Sketch')
sketch.addGeometry(Part.LineSegment(
    App.Vector(0, 0, 0),
    App.Vector(10, 0, 0)
))
sketch.addGeometry(Part.Circle(
    App.Vector(5, 5, 0),
    App.Vector(0, 0, 1),
    3
))
```

**Close & Validate:**
```
Verify the sketch is fully constrained
Close the sketch editing
```

### 6. PartDesign Operations

**Pad (Extrude):**
```
Pad Sketch 10mm
Create a pad from Sketch001 with length 20mm
```

**Pocket (Subtractive Extrusion):**
```
Create a pocket from Sketch with depth 5mm
Pocket through all
```

**Fillet (Round Edges):**
```
Add a 2mm fillet to Box
Round the edges of Cylinder with 1mm radius
```

**Workflow:**
1. System opens FreeCAD fillet dialog
2. Pre-selects the target object
3. You select edges in FreeCAD GUI
4. Set radius and click OK
5. System reports completion

**Chamfer (Bevel Edges):**
```
Add a 1mm chamfer to Box
Chamfer the edges of Cylinder with 0.5mm distance
```

**Hole Wizard:**
```
Create a M6 threaded hole through Box
Add a 5mm diameter hole with 10mm depth
```

**Patterns:**
```
Create a linear pattern of Fillet with 5 copies, 10mm spacing
Create a polar pattern of Pocket around Z axis, 8 copies
```

**Revolution:**
```
Revolve Sketch around the Y axis, 360 degrees
Create a revolution from Sketch001, angle 180 degrees
```

**Loft:**
```
Loft between Sketch and Sketch001
Create a loft through Sketch, Sketch001, and Sketch002
```

**Sweep:**
```
Sweep Sketch along Path
Create a sweep using Sketch as profile and Path as spine
```

**Mirror:**
```
Mirror Pad across the XY plane
Create a mirror of Pocket across the YZ plane
```

**Shell:**
```
Create a 2mm thick shell from Box, removing the top face
```

**Draft:**
```
Add a 5-degree draft angle to the faces of Box
```

### 7. CAM Operations

**Create Job:**
```
Create a CAM job for the current model
Setup a CAM job with stock size 100x100x50mm
```

**Configure Stock:**
```
Set the stock to bounding box + 5mm clearance
Use a custom stock size 150x150x60mm
```

**Add Operations:**
```
Add a profile operation with 3mm depth per pass
Add a pocket operation with 50% stepover
Add drilling at marked points with 1000 RPM
Add adaptive clearing with 2mm optimal load
```

**Tool Management:**
```
Create a 6mm end mill tool
Set spindle speed to 12000 RPM
Set feed rate to 1000 mm/min
```

**Export G-code:**
```
Export G-code to /path/to/output.nc
Generate G-code for the current job
```

### 8. View Control

**Camera Angles:**
```
Set view to front
Switch to isometric view
Show top view
```

**Available Views:**
- `front`, `back`, `top`, `bottom`, `left`, `right`
- `isometric`, `dimetric`, `trimetric`

**Zoom:**
```
Zoom in
Zoom out
Fit all objects in view
```

**Selection:**
```
Select the object named "Box"
Clear selection
Get the currently selected objects
```

**Screenshots:**
```
Take a screenshot of the current view
Capture the FreeCAD viewport
```

Returns base64-encoded PNG image that Claude can see.

### 9. Measurement Operations

**Measure Distance:**
```
Measure the distance between Box and Cylinder
Get the distance from point (0,0,0) to point (10,10,10)
```

**Measure Area/Volume:**
```
Calculate the surface area of Box
Get the volume of Sphere
```

### 10. Spreadsheet Operations

**Create Parametric Cells:**
```
Create a spreadsheet to define parameters
Set cell A1 to "length" and B1 to "=10mm"
Use spreadsheet values in feature dimensions
```

### 11. Execute Python Code

**Direct Python Execution:**
```
Execute this Python code in FreeCAD:
import FreeCAD as App
doc = App.ActiveDocument
box = doc.addObject("Part::Box", "MyBox")
box.Length = 10
box.Width = 20
box.Height = 30
doc.recompute()
```

**Available Namespaces:**
- `FreeCAD` / `App` - FreeCAD application
- `FreeCADGui` / `Gui` - GUI functions
- `Part`, `Sketcher`, `PartDesign` - Workbench modules
- All Python standard library

**Timeout:** 30 seconds per execution

---

## Health Check & Debugging

### Health Monitoring System

**Location:** `freecad_health.py` (v1.0.1)

**Features:**
- Heartbeat monitoring (5-second intervals)
- Crash detection and automatic recovery
- Max restart attempts: 3
- Cooldown between restarts: 10 seconds
- Crash logs stored in `/tmp/freecad_mcp_crashes/`

**Manual Health Check:**

```bash
# Check if socket exists
ls -la /tmp/freecad_mcp.sock

# View crash logs
cat /tmp/freecad_mcp_crashes/crash_*.json
```

**Health Check Response Format:**
```json
{
  "is_healthy": true,
  "socket_exists": true,
  "can_connect": true,
  "timestamp": "2025-12-11T18:34:00.123456",
  "errors": []
}
```

### Debug Logging System

**Location:** `freecad_debug.py` (v1.1.0)

**Log Directory:** `/tmp/freecad_mcp_debug/`

**What Gets Logged:**
- `COMMAND_START` - Tool name and arguments
- `COMMAND_SUCCESS` - Result and duration
- `COMMAND_ERROR` - Full traceback
- `JSON_PARSE_ERROR` - Malformed input
- `CLIENT_ERROR` - Connection errors

**Log Format (JSONL):**
```json
{
  "timestamp": "2025-12-11T18:34:00.123456",
  "operation": "COMMAND_START",
  "parameters": {"tool": "create_box", "args": {"length": 10}},
  "result": null,
  "error": null,
  "duration": null
}
```

**Retrieve Logs via Claude:**
```
Show me the last 20 debug log entries
Get debug logs for fillet operations
```

**Direct Log Access:**
```bash
# View recent logs
tail -n 50 /tmp/freecad_mcp_debug/freecad_mcp_debug.log

# Search for errors
grep "COMMAND_ERROR" /tmp/freecad_mcp_debug/freecad_mcp_debug.log

# Filter by operation
jq 'select(.parameters.tool == "create_box")' /tmp/freecad_mcp_debug/freecad_mcp_debug.log
```

**Logging Modes:**

**Verbose Mode** (`LEAN_LOGGING = False`):
- Logs every step of every operation
- Full state snapshots before/after
- Performance impact: ~40% slower
- Use for: Development, debugging specific issues

**Lean Mode** (`LEAN_LOGGING = True`):
- Only logs start, success, and errors
- 60% reduction in log volume
- Minimal performance impact
- Use for: Production, normal operation

**Toggle in `socket_server.py`:**
```python
LEAN_LOGGING = True  # Production (default)
LEAN_LOGGING = False # Development
```

### Version Management

**Location:** `mcp_versions.py` (v1.0.0)

**Current Versions:**
- `socket_server`: v4.0.1
- `freecad_debug`: v1.1.0
- `freecad_health`: v1.0.1

**Dependency Validation:**
```python
# Automatically validated at startup
socket_server requires:
  - freecad_debug >= 1.1.0
  - freecad_health >= 1.0.1
```

**Check Versions in FreeCAD Console:**
```python
from AICopilot.socket_server import __version__ as server_version
print(f"Socket Server: {server_version}")
```

### Common Debug Scenarios

**Issue: Claude says "Cannot connect to FreeCAD"**

1. Check FreeCAD is running with GUI
2. Verify socket exists:
   ```bash
   ls -la /tmp/freecad_mcp.sock
   ```
3. Check FreeCAD console for errors
4. Review crash logs:
   ```bash
   cat /tmp/freecad_mcp_crashes/crash_*.json
   ```

**Issue: Operations timeout**

1. Check debug logs for the operation:
   ```bash
   tail -n 100 /tmp/freecad_mcp_debug/freecad_mcp_debug.log | grep "timeout"
   ```
2. Increase timeout in handler (default: 30s)
3. Check FreeCAD isn't frozen (Qt event loop)

**Issue: "Invalid JSON" errors**

1. Check debug logs for `JSON_PARSE_ERROR`:
   ```bash
   grep "JSON_PARSE_ERROR" /tmp/freecad_mcp_debug/freecad_mcp_debug.log
   ```
2. Verify message size < 100MB
3. Check for truncated messages (socket buffer issues)

**Issue: Handler not found**

1. Verify handler is imported in `socket_server.py` (lines 364-379)
2. Check handler file exists in `handlers/` directory
3. Review initialization errors in FreeCAD console

---

## Advanced Features

### Modal Command System

**Purpose:** Open native FreeCAD dialogs with intelligent pre-configuration

**Available Modal Commands:**
- `fillet` - FreeCAD fillet dialog
- `chamfer` - FreeCAD chamfer dialog
- `hole` - FreeCAD hole wizard
- `pad` - FreeCAD pad dialog
- `pocket` - FreeCAD pocket dialog
- `pattern` - FreeCAD pattern dialog

**Example Workflow:**

**You:**
```
Add a 5mm fillet to TestBox
```

**Claude:**
```
✅ FreeCAD Fillet Tool Opened
📦 Pre-selected: TestBox
⚙️  Suggested radius: 5mm

👉 Complete in FreeCAD:
1. Select edges to fillet
2. Set radius (5mm)
3. Click OK
```

**You:** (In FreeCAD GUI)
1. Click edges to fillet
2. Confirm radius is 5mm
3. Click OK button

**Claude:**
```
✅ Fillet completed successfully
```

**Benefits:**
- Native FreeCAD interface (familiar to CAD users)
- Visual edge/face selection
- Real-time preview
- Professional CAD workflow

### Universal Selection System

**Purpose:** Interactive selection of edges, faces, or objects

**How It Works:**

1. **Request Selection:**
   ```
   Add fillet to the vertical edges of Box
   ```

2. **Claude Responds:**
   ```
   Please select the edges to fillet in FreeCAD.

   Selection mode: EDGES
   Target object: Box

   Instructions:
   1. Click edges in FreeCAD viewport
   2. Hold Ctrl to select multiple
   3. Tell me when you're done
   ```

3. **You Select in FreeCAD:** (Visual interaction)

4. **You Confirm:**
   ```
   Done selecting
   ```

5. **Claude Completes Operation:**
   ```
   ✅ Fillet created with 2mm radius
   Selected edges: Edge1, Edge3, Edge5
   ```

**Selection Types:**
- `edges` - Edge selection (fillet, chamfer)
- `faces` - Face selection (shell, draft)
- `objects` - Object selection (boolean ops)

**Auto-Cleanup:**
- Pending selections expire after 5 minutes
- Prevents memory leaks

### Threading & GUI Safety

**Problem:** FreeCAD GUI must run on Qt main thread

**Solution:** GUI Task Queue System

**How It Works:**
```
Claude Request → Socket Server → GUI Task Queue → Qt Main Thread → FreeCAD API
```

**Implications:**
- Operations are thread-safe
- GUI operations won't crash
- 30-second timeout per operation
- Operations queue if FreeCAD is busy

**Console Mode Limitations:**

FreeCAD can run without GUI (`freecadcmd`), but some operations are unavailable:
- Screenshot (requires viewport)
- Interactive selection (requires GUI)
- View control (requires 3D view)
- Modal dialogs (requires Qt)

**Checking GUI Availability:**
```python
import FreeCAD
if FreeCAD.GuiUp:
    # GUI operations available
else:
    # Console mode - limited operations
```

### Custom Python Execution

**Execute Arbitrary Python:**
```
Run this Python code in FreeCAD:
import Part
from FreeCAD import Vector

# Create a custom parametric shape
box1 = Part.makeBox(10, 10, 10)
box2 = Part.makeBox(5, 5, 20, Vector(2.5, 2.5, -5))
result = box1.fuse(box2)

App.ActiveDocument.addObject("Part::Feature", "CustomShape").Shape = result
App.ActiveDocument.recompute()
```

**Use Cases:**
- Complex geometries not supported by handlers
- Custom algorithms
- Batch operations
- Advanced parametric designs

**Safety:**
- 30-second timeout
- Runs in FreeCAD's Python namespace
- Full access to FreeCAD API
- No file system restrictions (use carefully)

### Smart Dispatcher Pattern

**Many tools use a smart dispatcher** for routing operations:

**Example: PartDesign Operations**
```json
{
  "tool": "partdesign_operations",
  "args": {
    "operation": "pad",
    "sketch_name": "Sketch",
    "length": 10
  }
}
```

**Available Dispatchers:**
- `partdesign_operations` - Pad, pocket, fillet, chamfer, patterns, etc.
- `part_operations` - Primitives, booleans, transforms
- `view_control` - Screenshots, view angles, selection
- `cam_operations` - CAM job, stock, operations, export
- `draft_operations` - 2D drafting
- `spreadsheet_operations` - Parametric cells

**Benefits:**
- Single tool for related operations
- Consistent parameter format
- Easier to maintain
- Reduced tool count in MCP

---

## Troubleshooting

### Connection Issues

**Problem: "Socket not found"**

**Cause:** FreeCAD not running or socket server failed to start

**Solution:**
1. Start FreeCAD GUI
2. Check FreeCAD console for errors:
   ```
   AI Copilot Workbench initialized
   Socket server listening on /tmp/freecad_mcp.sock
   ```
3. Verify socket:
   ```bash
   ls -la /tmp/freecad_mcp.sock
   ```
4. Check crash logs:
   ```bash
   cat /tmp/freecad_mcp_crashes/crash_*.json
   ```

**Problem: "Permission denied on socket"**

**Cause:** Socket file has wrong permissions

**Solution:**
```bash
# Remove old socket
rm /tmp/freecad_mcp.sock

# Restart FreeCAD
```

**Problem: "Connection refused (Windows)"**

**Cause:** TCP socket not listening on `localhost:23456`

**Solution:**
1. Check Windows Firewall settings
2. Verify no other process using port 23456:
   ```powershell
   netstat -an | findstr 23456
   ```
3. Check FreeCAD console for port binding errors

### Operation Failures

**Problem: "Timeout waiting for GUI task"**

**Cause:** FreeCAD GUI frozen or operation too slow

**Solution:**
1. Check FreeCAD is responsive (click around)
2. Check debug logs for the operation:
   ```bash
   grep "timeout" /tmp/freecad_mcp_debug/freecad_mcp_debug.log
   ```
3. Simplify the operation (reduce complexity)
4. Restart FreeCAD if frozen

**Problem: "Object not found"**

**Cause:** Typo in object name or object deleted

**Solution:**
1. List all objects:
   ```
   List all objects in FreeCAD
   ```
2. Use exact object name (case-sensitive)
3. Check object wasn't deleted by previous operation

**Problem: "Sketch not closed"**

**Cause:** Sketch has open contours

**Solution:**
1. Verify sketch geometry forms closed loops
2. Use sketch solver to check constraints
3. Manually close sketch in FreeCAD GUI

**Problem: "Handler error: [module] has no attribute [function]"**

**Cause:** Version mismatch or missing FreeCAD module

**Solution:**
1. Check FreeCAD version (1.0+ required)
2. Verify workbench is available:
   ```python
   import FreeCADGui
   FreeCADGui.listWorkbenches()
   ```
3. Install missing workbench if needed

### Claude Desktop Issues

**Problem: "MCP server not found"**

**Cause:** Configuration error in `claude_desktop_config.json`

**Solution:**
1. Verify JSON syntax is valid (no trailing commas)
2. Check Python path is correct:
   ```bash
   /opt/homebrew/bin/python3.11 --version
   ```
3. Check bridge path is absolute:
   ```bash
   ls -la /path/to/working_bridge.py
   ```
4. Restart Claude Desktop after editing config

**Problem: "MCP server crashed"**

**Cause:** Python error in bridge or socket connection failure

**Solution:**
1. Check Claude Desktop logs:
   - **macOS:** `~/Library/Logs/Claude/`
   - **Linux:** `~/.config/Claude/logs/`
   - **Windows:** `%APPDATA%\Claude\logs\`
2. Run bridge manually to see errors:
   ```bash
   /opt/homebrew/bin/python3.11 /path/to/working_bridge.py
   ```
3. Check for Python module errors (missing dependencies)

### Performance Issues

**Problem: "Operations are slow"**

**Cause:** Debug logging overhead or complex operations

**Solution:**
1. Enable lean logging mode in `socket_server.py`:
   ```python
   LEAN_LOGGING = True
   ```
2. Reduce recompute frequency (batch operations)
3. Simplify geometry (reduce face/edge count)

**Problem: "High memory usage"**

**Cause:** Large debug logs or many pending selections

**Solution:**
1. Clean up old debug logs:
   ```bash
   rm /tmp/freecad_mcp_debug/freecad_mcp_debug.log.*
   ```
2. Clean up old selections (auto-cleanup after 5 minutes)
3. Restart FreeCAD periodically for long sessions

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────┐
│             Claude Desktop / Claude Code            │
│         (MCP Client - stdio communication)          │
└──────────────────────┬──────────────────────────────┘
                       │ MCP Protocol (JSON-RPC 2.0)
                       │ via stdin/stdout
┌──────────────────────▼──────────────────────────────┐
│            MCP Bridge (working_bridge.py)           │
│  - Stdio → Socket translation                       │
│  - Interactive selection workflows                  │
│  - Debug/health monitoring hooks                    │
└──────────────────────┬──────────────────────────────┘
                       │ Length-prefixed socket messages
                       │ (4-byte length + JSON)
┌──────────────────────▼──────────────────────────────┐
│      FreeCAD Socket Server (socket_server.py)       │
│  v4.0.1 - 916 lines                                 │
│  - Unix socket (macOS/Linux): /tmp/freecad_mcp.sock │
│  - TCP socket (Windows): localhost:23456            │
│  - Tool routing & handler dispatch                  │
│  - GUI task queue for thread safety                 │
│  - Universal selection system                       │
└──────────────────────┬──────────────────────────────┘
                       │ Routes to handlers
┌──────────────────────▼──────────────────────────────┐
│         Modular Handlers (handlers/ directory)      │
│  - PrimitivesHandler (box, cylinder, sphere, ...)   │
│  - BooleanOpsHandler (fuse, cut, common)            │
│  - TransformsHandler (move, rotate, copy, array)    │
│  - SketchOpsHandler (create, geometry, verify)      │
│  - PartDesignOpsHandler (pad, pocket, fillet, ...)  │
│  - PartOpsHandler (extrude, revolve, loft, sweep)   │
│  - CAMOpsHandler (job, stock, operations, export)   │
│  - CAMToolsHandler (tool library management)        │
│  - CAMToolControllersHandler (spindle, feed, ...)   │
│  - ViewOpsHandler (screenshot, view, selection)     │
│  - DocumentOpsHandler (save, load, objects, ...)    │
│  - MeasurementOpsHandler (distance, area, volume)   │
│  - SpreadsheetOpsHandler (parametric cells)         │
│  - DraftOpsHandler (2D drafting)                    │
│  16 handlers total, ~300KB of handler code          │
└──────────────────────┬──────────────────────────────┘
                       │ FreeCAD API calls
┌──────────────────────▼──────────────────────────────┐
│        FreeCAD Process (with GUI/Qt event loop)     │
│  - FreeCAD Python API                               │
│  - Part, PartDesign, Sketcher, CAM workbenches      │
│  - Native modal dialogs                             │
│  - Interactive selection                            │
└──────────────────────┬──────────────────────────────┘
                       │ User interaction
┌──────────────────────▼──────────────────────────────┐
│                       User                          │
│  - Visual selection in viewport                     │
│  - Modal dialog interaction                         │
│  - Manual parameter adjustment                      │
└─────────────────────────────────────────────────────┘
```

### Communication Protocol

**Length-Prefixed Messages (v2.1.1):**

```
┌────────────────┬──────────────────────────────────┐
│ 4-byte length  │  JSON message (UTF-8)            │
│ (big-endian)   │                                  │
└────────────────┴──────────────────────────────────┘
```

**Example:**
```python
# Message: {"tool": "create_box", "args": {"length": 10}}
message_bytes = b'{"tool": "create_box", "args": {"length": 10}}'
length = len(message_bytes)  # 50 bytes
length_bytes = struct.pack('>I', length)  # b'\x00\x00\x00\x32'

# Sent over socket:
# b'\x00\x00\x00\x32{"tool": "create_box", "args": {"length": 10}}'
```

**Safety Features:**
- Prevents message truncation
- Handles partial reads/writes
- Max message size: 100MB
- UTF-8 validation
- 30-second timeout per message

### Handler Architecture

**Base Handler Class:**
```python
class BaseHandler:
    def __init__(self, server, log_operation, capture_state)

    # Common utilities
    def get_document(self, create_if_missing=False)
    def get_object(self, object_name, doc=None)
    def recompute(self, doc=None)
    def find_body(self, doc=None)

    # Logging
    def log_and_return(self, operation, parameters, result=None, error=None)
```

**Handler Initialization (socket_server.py:364-379):**
```python
self.primitives = PrimitivesHandler(...)
self.boolean_ops = BooleanOpsHandler(...)
self.transforms = TransformsHandler(...)
self.sketch_ops = SketchOpsHandler(...)
self.partdesign_ops = PartDesignOpsHandler(...)
self.part_ops = PartOpsHandler(...)
self.cam_ops = CAMOpsHandler(...)
# ... 16 handlers total
```

**Tool Routing:**
```python
def _execute_tool(self, tool_name, args):
    handler_map = {
        'create_box': lambda a: self.primitives.create_box(a),
        'fuse_objects': lambda a: self.boolean_ops.fuse_objects(a),
        'partdesign_operations': lambda a: self.partdesign_ops.dispatch(a),
        'view_control': lambda a: self.view_ops.dispatch(a),
        # ... 80+ tools
    }

    if tool_name in handler_map:
        return handler_map[tool_name](args)
    else:
        return {"error": f"Unknown tool: {tool_name}"}
```

### Threading Model

**Problem:** FreeCAD GUI operations must run on Qt main thread

**Solution:** GUI Task Queue System

```python
# Global queues
gui_task_queue = queue.Queue()       # Input: tasks to execute
gui_response_queue = queue.Queue()   # Output: results

# Qt timer callback (runs on main thread)
def process_gui_tasks():
    while not gui_task_queue.empty():
        task = gui_task_queue.get_nowait()
        result = task()  # Execute on main thread
        gui_response_queue.put(result)

    QtCore.QTimer.singleShot(100, process_gui_tasks)  # Re-schedule

# Handler submits task
def some_handler_operation(args):
    def task():
        # This runs on Qt main thread
        return FreeCADGui.activeDocument().activeView().saveImage(...)

    gui_task_queue.put(task)

    # Wait for result with timeout
    start = time.time()
    while time.time() - start < 30:
        try:
            return gui_response_queue.get_nowait()
        except:
            time.sleep(0.05)

    return {"error": "timeout"}
```

**Thread Safety Guarantees:**
- All GUI operations run on Qt main thread
- Socket communication runs on dedicated thread
- Handler dispatch runs on socket thread
- Qt timer ensures GUI queue is processed

---

## Updating This Document

**This document must be updated whenever:**

1. **New handlers are added** (update [Available Operations](#available-operations))
2. **Tool parameters change** (update operation examples)
3. **Version numbers change** (update version references)
4. **Debug/health systems change** (update [Health Check & Debugging](#health-check--debugging))
5. **Architecture changes** (update [Architecture Overview](#architecture-overview))
6. **Socket protocol changes** (update protocol documentation)
7. **Configuration requirements change** (update [Installation & Configuration](#installation--configuration))

**Update Process:**

1. **Identify what changed:**
   ```bash
   git diff HEAD~1 -- AICopilot/
   ```

2. **Update relevant sections:**
   - Version numbers
   - Tool descriptions
   - Configuration examples
   - Troubleshooting tips
   - Architecture diagrams

3. **Test the documentation:**
   - Verify all examples work
   - Check all file paths are correct
   - Validate configuration snippets

4. **Commit with the code changes:**
   ```bash
   git add CLAUDE_DESKTOP_MCP_USAGE.md
   git commit -m "Update MCP documentation for v4.0.2"
   ```

**Version Increment Rules:**

- **Major (4.x.x → 5.0.0):** Breaking API changes, protocol changes
- **Minor (4.0.x → 4.1.0):** New handlers, new tools, new features
- **Patch (4.0.0 → 4.0.1):** Bug fixes, documentation, logging improvements

**Current Versions to Track:**

| Component | Version | Location |
|-----------|---------|----------|
| Socket Server | 4.0.1 | `socket_server.py:__version__` |
| Debug System | 1.1.0 | `freecad_debug.py:__version__` |
| Health Monitor | 1.0.1 | `freecad_health.py:__version__` |
| MCP Versions | 1.0.0 | `mcp_versions.py:__version__` |
| Protocol | 2.1.1 | `socket_server.py` (length-prefixed) |

**Documentation Standards:**

- Use clear, concise language
- Provide copy-paste ready examples
- Include platform-specific notes where applicable
- Add troubleshooting entries for common issues
- Keep table of contents updated
- Use consistent formatting (markdown)

**Testing Checklist Before Release:**

- [ ] All version numbers updated
- [ ] All configuration examples tested
- [ ] All operation examples work
- [ ] Platform-specific paths verified
- [ ] Troubleshooting section covers known issues
- [ ] Architecture diagrams match current code
- [ ] Table of contents is accurate

---

---

**End of Document**

## Document Version History

### v1.0.0 (2025-12-11) - **CURRENT**
- **Initial Release**: Comprehensive MCP usage documentation for Claude Desktop
- **Tracks**: socket_server.py v4.0.1, freecad_debug.py v1.1.0, freecad_health.py v1.0.1
- **Sections**: 8 major sections covering all aspects of MCP usage
- **Operations Documented**: 80+ tools across 16 handlers
- **Size**: 31KB, 1237 lines

### When to Bump Document Version

**MAJOR (X.0.0):**
- Socket server major version changes (breaking API)
- Complete documentation restructure
- Architecture changes requiring full rewrite

**MINOR (1.X.0):**
- New handlers added (update Available Operations)
- New features added (update Advanced Features)
- Socket server minor version changes
- New troubleshooting sections

**PATCH (1.0.X):**
- Typo fixes
- Configuration example updates
- Component version updates (if no API changes)
- Clarifications and improvements

---

**Document Version:** 1.0.0
**Last Updated:** 2025-12-11
**Maintainer:** FreeCAD MCP Project
**License:** Same as FreeCAD MCP (check project root)

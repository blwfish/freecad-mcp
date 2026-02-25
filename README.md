# freecad-mcp

MCP server for FreeCAD — 24 tools for AI-assisted 3D CAD modeling via the [Model Context Protocol](https://modelcontextprotocol.io/).

Design parametric parts, generate CAM toolpaths, take screenshots, and execute Python scripts — all driven by an AI assistant like Claude.

## Quick Start

### Prerequisites

- **FreeCAD 1.2-dev** (required — CAM, PartDesign, and mesh APIs changed significantly in 1.2; earlier versions will appear to work but have broken behavior)
- Python 3.10+
- Claude Desktop or Claude Code

### Install

1. Copy the FreeCAD workbench:
   ```bash
   cp -r AICopilot ~/Library/Application\ Support/FreeCAD/v1-2/Mod/
   ```

2. Install the MCP bridge:
   ```bash
   mkdir -p ~/.freecad-mcp
   cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
   ```

3. Register with Claude Code:
   ```bash
   claude mcp add freecad python3 ~/.freecad-mcp/working_bridge.py
   ```

See [INSTALL.md](INSTALL.md) for Claude Desktop config, other platforms, and troubleshooting.

## Tools (20)

### Smart Dispatchers (8 tools)

Each dispatcher accepts an `operation` argument routing to many sub-operations.

| Tool | Operations | Description |
|------|-----------|-------------|
| `partdesign_operations` | 13 | Pad, pocket, fillet, chamfer, hole, revolution, loft, sweep, mirror, linear/polar pattern, draft, shell |
| `part_operations` | 18 | Primitives (box, cylinder, sphere, cone, torus, wedge), booleans (fuse, cut, common), transforms, extrude, revolve, loft, sweep |
| `view_control` | 16 | Screenshots, set view, fit all, zoom, create/list/save documents, list objects, undo/redo, selection |
| `cam_operations` | 37 | Full CNC toolpath: create job, profile, pocket, drilling, adaptive, contour, surface, surface_stl (OCL), engrave, export G-code |
| `cam_tools` | 5 | Cutting tool library CRUD |
| `cam_tool_controllers` | 5 | Tool controller CRUD (link tools to jobs with speeds/feeds) |
| `spreadsheet_operations` | 8 | Parametric spreadsheet data management |
| `draft_operations` | 5 | 2D drafting, clones, arrays |

### Utility Tools (12 tools)

| Tool | Description |
|------|-------------|
| `execute_python` | Run arbitrary Python in FreeCAD context — the escape hatch for anything not covered by dedicated tools |
| `execute_python_async` | Submit long-running Python for async execution; returns a job ID immediately |
| `poll_job` | Check status of an async job (running/done/error) |
| `list_jobs` | List all tracked async jobs |
| `cancel_operation` | Cancel a running FreeCAD operation (Thickness, boolean, etc.) |
| `cancel_job` | Cancel a running async job |
| `get_debug_logs` | Retrieve recent operation logs for troubleshooting |
| `continue_selection` | Complete interactive edge/face selection (fillet, chamfer, hole workflows) |
| `restart_freecad` | Restart FreeCAD, optionally saving and reopening documents |
| `check_freecad_connection` | Verify FreeCAD is running with AICopilot loaded |
| `mesh_operations` | Import/export meshes, mesh-to-solid conversion, validation, simplification |
| `test_echo` | Connectivity test |

## Architecture

```
Claude Desktop/Code
    | (MCP protocol over stdio)
working_bridge.py         14 MCP tools
    | (Unix socket: /tmp/freecad_mcp.sock)
socket_server.py          dispatch routes, v5.2.0
    | (Modular handlers)
handlers/*.py             14 handler classes
    | (FreeCAD Python API)
FreeCAD
```

The bridge translates MCP tool calls into length-prefixed JSON messages sent over a Unix domain socket (TCP on Windows) to the FreeCAD process, where the socket server dispatches them to modular handler classes.

## Known Issues

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for details:

- **GIL deadlock**: Creating documents from socket thread can crash FreeCAD. Create documents in FreeCAD GUI first, or use `view_control(operation="create_document")`.
- **Large documents**: `list_objects` paginates at 100 objects (max 500). Use `offset`/`limit`/`type_filter` for large documents.

## Development

```bash
# Run tests (no FreeCAD required)
python3 -m pytest

# Sync AICopilot workbench to FreeCAD's Mod directory
./deploy.sh

# Copy bridge updates
cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
```

### Project Structure

```
working_bridge.py         MCP bridge (Claude-facing, 14 tools)
mcp_bridge_framing.py     Length-prefixed message protocol
AICopilot/
  socket_server.py        FreeCAD socket server (v5.2.0)
  handlers/               Modular handler classes
  ocl_surface_op.py       OCL PathDropCutter surface op (Path::FeaturePython)
  freecad_debug.py        Debug logging (optional)
  freecad_health.py       Crash monitoring (optional)
tests/unit/               Unit tests (pytest, no FreeCAD required)
```

## License

LGPL-2.1-or-later

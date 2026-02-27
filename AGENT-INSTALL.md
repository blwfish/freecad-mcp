# FreeCAD MCP — Agent Installation & Usage Guide

This file is for you, the AI agent. It tells you what needs to be true on this system for the FreeCAD MCP server to work, how to make it true, and how to use it once it's running. Read it fully before taking any action.

## What This Is

freecad-mcp is a Model Context Protocol (MCP) server providing 24 tools for FreeCAD 3D CAD modeling — parametric part design, CNC toolpath generation (CAM), mesh operations, screenshots, and arbitrary Python execution inside FreeCAD. Once installed and registered, these tools appear in your tool list and you can design 3D parts conversationally.

**Origin:** Built by one person for personal use, on a Mac, with Claude Code. Other platforms *should* work (the code handles macOS, Windows, and Linux) but are less tested. PRs for other agents and platforms will be considered.

## Architecture

```
AI Agent ──(MCP over stdio)── working_bridge.py ──(Unix socket)── FreeCAD + AICopilot addon
```

There are two components to install:
1. **AICopilot workbench** — a FreeCAD addon that runs a socket server inside FreeCAD
2. **MCP bridge** — a Python script that translates MCP tool calls into socket messages

Both must be installed. FreeCAD must be running with the addon loaded for tools to work.

## Prerequisites

The following must be present on the system. Check each one. Install anything missing.

### 1. FreeCAD 1.2+ (required)

**Critical:** Version 1.2 or later is required. CAM, PartDesign, and mesh APIs changed significantly in 1.2 — earlier versions appear to work but have broken behavior.

**Check:** Launch FreeCAD and check Help → About. Or run `FreeCADCmd --version` if available on PATH.

**Install:**
- **macOS:** Download from https://www.freecad.org/downloads.php — drag to Applications
- **Linux:** AppImage from https://www.freecad.org/downloads.php, or PPA/Flatpak. Distro packages are usually outdated.
- **Windows:** Installer from https://www.freecad.org/downloads.php

### 2. Python 3.10+ (required)

The MCP bridge runs on the system Python (not FreeCAD's bundled Python).

**Check:** `python3 --version`

**Install:**
- **macOS:** `brew install python@3.12`
- **Linux:** `sudo apt install python3`
- **Windows:** https://www.python.org/downloads/

### 3. MCP Python package (required)

The bridge depends on the `mcp` package.

**Install:** `pip3 install mcp>=0.1.0`

## Installation

### Step 1: Clone the repo

```bash
git clone https://github.com/blwfish/freecad-mcp.git
```

Clone location: wherever repos live on this system. `~/freecad-mcp` is a safe default.

### Step 2: Install the AICopilot workbench into FreeCAD

Copy the `AICopilot` directory to FreeCAD's Mod folder:

- **macOS:** `cp -r AICopilot ~/Library/Application\ Support/FreeCAD/v1-2/Mod/`
- **Linux:** `cp -r AICopilot ~/.local/share/FreeCAD/Mod/`
- **Windows:** Copy `AICopilot` to `%APPDATA%\FreeCAD\Mod\`

If the Mod directory doesn't exist, create it. The `v1-2` path component on macOS matches FreeCAD 1.2; adjust if needed.

### Step 3: Install the MCP bridge

```bash
mkdir -p ~/.freecad-mcp
cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
pip3 install mcp>=0.1.0
```

### Step 4: Register as an MCP Server

**Claude Code:**
```bash
claude mcp add freecad python3 ~/.freecad-mcp/working_bridge.py
```

**Other agents** — the server speaks standard MCP over stdio:
```json
{
  "mcpServers": {
    "freecad": {
      "command": "python3",
      "args": ["/absolute/path/to/.freecad-mcp/working_bridge.py"]
    }
  }
}
```

Use absolute paths. Config format varies by agent platform.

### Step 5: Start FreeCAD

FreeCAD must be running with the AICopilot addon loaded. On first launch after installing the addon, FreeCAD will detect it automatically. The addon starts a socket server (default: `/tmp/freecad_mcp.sock` on Unix, TCP port 23456 on Windows).

## Verify Installation

Call the connection check tool:

```
check_freecad_connection()
```

This should confirm FreeCAD is running with AICopilot loaded. If it fails, ensure FreeCAD is running and the addon is enabled (check FreeCAD's addon manager).

A more thorough check:

```
view_control(operation="list_objects")
```

This queries FreeCAD for scene contents. If it returns without error, the full pipeline is working.

## Environment Variables

All optional. The server auto-detects sensible defaults.

| Variable | Purpose | Default |
|----------|---------|---------|
| `FREECAD_MCP_SOCKET` | Unix socket path | `/tmp/freecad_mcp.sock` |
| `FREECAD_MCP_PORT` | TCP port (Windows) | `23456` |
| `FREECAD_MCP_FREECAD_BIN` | Path to FreeCADCmd binary | auto-detected |
| `FREECAD_MCP_MODULE_DIR` | Path to AICopilot module | auto-detected |
| `FREECAD_MCP_TEST_MODE` | Disable auto-startup in GUI | unset |

## How to Use the Tools

### Read CLAUDE.md First

The file `CLAUDE.md` in the repo root is your primary reference for **using** the tools. It contains:

- **Mandatory rules** — check connection first, use execute_python as escape hatch, handle interactive selection workflows, create documents before objects
- **Workflow examples** — parametric part creation, CAM toolpath generation, modifying existing objects
- **Tool selection guide** — which tool and operation for each task
- **Known issues** — GIL deadlock workaround, large document handling

### Tool Overview

**8 Smart Dispatchers** — each accepts an `operation` parameter routing to many sub-operations:

| Tool | Operations | What it does |
|------|-----------|--------------|
| `partdesign_operations` | 13 | Pad, pocket, fillet, chamfer, hole, revolution, loft, sweep, mirror, patterns |
| `part_operations` | 18 | Primitives, booleans, transforms, extrude, revolve |
| `view_control` | 16 | Screenshots, views, document management, object selection |
| `cam_operations` | 37 | Full CNC: job setup, profiles, pockets, drilling, surface ops, G-code export |
| `cam_tools` | 5 | Cutting tool library CRUD |
| `cam_tool_controllers` | 5 | Tool controller management |
| `spreadsheet_operations` | 8 | Parametric data management |
| `draft_operations` | 5 | 2D drafting, clones, arrays |

**12 Utility Tools:**
- `execute_python` / `execute_python_async` — run arbitrary Python in FreeCAD (the escape hatch)
- `poll_job` / `list_jobs` / `cancel_job` / `cancel_operation` — async job management
- `check_freecad_connection` — verify FreeCAD is running
- `mesh_operations` — import/export, mesh-to-solid, validation
- `get_debug_logs` — retrieve operation logs
- `continue_selection` — complete interactive edge/face selection workflows
- `restart_freecad` — restart FreeCAD, optionally saving documents

### Critical Rules

1. **Always check connection first.** Call `check_freecad_connection()` before any other tool if you haven't verified the connection in this session.
2. **Create documents before objects.** Use `view_control(operation="create_document")` before creating any geometry.
3. **Use execute_python as escape hatch.** If a dedicated tool doesn't cover what you need, `execute_python` gives full access to FreeCAD's Python API.
4. **Handle interactive selection.** Fillet, chamfer, and hole operations require edge/face selection — follow the `continue_selection` workflow described in CLAUDE.md.

## Health and Debugging

| Symptom | What to do |
|---------|-----------|
| "Cannot connect to FreeCAD" | Ensure FreeCAD is running with AICopilot addon. Check `check_freecad_connection()` |
| Operation hangs | Use `cancel_operation()` or `cancel_job(job_id)`. If FreeCAD is unresponsive, `restart_freecad()` |
| Need to see what went wrong | `get_debug_logs(count=20)` returns recent operation logs with errors and timing |
| GIL deadlock on document creation | Create documents via `view_control(operation="create_document")` not via `execute_python` |
| Large document slow | Use `offset`/`limit`/`type_filter` parameters when listing objects |

## Known Issues

See `KNOWN_ISSUES.md` in the repo for full details:

- **GIL deadlock** — Creating documents from the socket thread can crash FreeCAD. Always use `view_control(operation="create_document")`.
- **Large documents** — `list_objects` paginates at 100 objects (max 500). Use filters for DXF imports with 1000+ objects.
- **Fillet/chamfer requires GUI** — Edge selection for dress-up operations needs the FreeCAD GUI running (not headless).

## Contributing

### Filing Issues

Include: platform and version, FreeCAD version, the tool call that failed, the complete error response, and `get_debug_logs()` output.

### Pull Requests

- Follow the handler pattern: add new handlers in `AICopilot/handlers/`
- The bridge and socket server use length-prefixed JSON protocol
- Run `python3 -m pytest` before submitting (174 tests, no FreeCAD required)
- Update tool counts in README.md and CLAUDE.md if adding tools

## License

LGPL-2.1-or-later

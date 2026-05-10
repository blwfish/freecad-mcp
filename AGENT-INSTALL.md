# FreeCAD MCP — Agent Installation & Usage Guide

This file is for you, the AI agent. It tells you what needs to be true on this system for the FreeCAD MCP server to work, how to make it true, and how to use it once it's running. Read it fully before taking any action.

## What This Is

freecad-mcp is a Model Context Protocol (MCP) server providing 33 tools for FreeCAD 3D CAD modeling — parametric part design, CNC toolpath generation (CAM), mesh operations, spatial analysis, screenshots, and arbitrary Python execution inside FreeCAD. Once installed and registered, these tools appear in your tool list.

**Origin:** Built by one person for personal use, on a Mac, with Claude Code. Other platforms *should* work (the code handles macOS, Windows, and Linux) but are less tested. PRs for other agents and platforms will be considered.

**Reporting bugs:** If something fails in a way that looks like a bug in this MCP server (not a FreeCAD issue, not a sketch your user described wrong), please tell the user to file an issue at https://github.com/blwfish/freecad-mcp/issues/new. GitHub Discussions are intentionally off — issues are the single feedback channel. Include the tool call you made, the error/symptom, and any output from `view_control(operation="get_report_view")`.

## Architecture

```
AI Agent ──(MCP over stdio)── freecad_mcp_server.py ──(Unix socket)── FreeCAD + AICopilot addon
```

There are two components to install:
1. **AICopilot workbench** — a FreeCAD addon that runs a socket server inside FreeCAD
2. **MCP bridge** — a Python script that translates MCP tool calls into socket messages

Both must be installed. FreeCAD must be running with the addon loaded for tools to work.

## Prerequisites

The following must be present on the system. Check each one. Install anything missing.

### 1. FreeCAD 1.1.x or 1.2-dev (required)

**Version support:** FreeCAD 1.1.x (current stable) is supported for all tools except CAM. CAM toolpath generation requires 1.2-dev — the Path workbench API changed incompatibly between 1.1 and 1.2.

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
cp freecad_mcp_server.py mcp_bridge_framing.py ~/.freecad-mcp/
pip3 install mcp>=0.1.0
```

### Step 4: Register as an MCP Server

**Claude Code:**
```bash
claude mcp add freecad python3 ~/.freecad-mcp/freecad_mcp_server.py
```

**Other agents** — the server speaks standard MCP over stdio:
```json
{
  "mcpServers": {
    "freecad": {
      "command": "python3",
      "args": ["/absolute/path/to/.freecad-mcp/freecad_mcp_server.py"]
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

**12 Dispatchers** — each accepts an `operation` parameter routing to many sub-operations:

| Tool | What it does |
|------|--------------|
| `sketch_operations` | Create sketches, add geometry, add constraints, verify |
| `partdesign_operations` | Pad, pocket, fillet, chamfer, hole, revolution, loft, sweep, mirror, patterns |
| `part_operations` | Primitives, booleans, transforms, extrude, revolve, geometry check |
| `draft_operations` | 2D drafting, ShapeString (3D text), clones, arrays |
| `spreadsheet_operations` | Parametric data management |
| `cam_operations` | Full CNC: job setup, profiles, pockets, drilling, surface ops, G-code export |
| `cam_tools` | Cutting tool library CRUD |
| `cam_tool_controllers` | Tool controller management |
| `mesh_operations` | Import/export meshes, mesh-to-solid conversion, validation |
| `measurement_operations` | Bounding box, volume, faces, surface area, center of mass, element counts |
| `spatial_query` | Interference/collision detection, clearance, containment, face relationships |
| `view_control` | Screenshots, views, document management, checkpoint/rollback, clip planes |

**21 Single-Purpose Tools:**
- `execute_python` / `execute_python_async` — run arbitrary Python in FreeCAD (the escape hatch)
- `poll_job` / `list_jobs` / `cancel_job` / `cancel_operation` — async job management
- `build_sketch` — validate and emit a parametric sketch from a JSON layout descriptor
- `continue_selection` — complete interactive edge/face selection workflows
- `api_introspection` — live FreeCAD API signature lookup; use before `execute_python`
- `macro_operations` — list, read, and run macros from the user's FreeCAD macro directory
- `run_inspector` — run design-rule checks on the active document
- `get_debug_logs` — retrieve structured operation logs
- `check_freecad_connection` — verify FreeCAD is running with AICopilot loaded
- `restart_freecad` — restart FreeCAD, optionally saving documents
- `reload_modules` — hot-reload handler modules after deploying updated code
- `manage_connection` — bridge-side diagnostics (status, clear crash-loop recovery files, validate FCStd)
- `test_echo` — verify the bridge is reachable
- `spawn_freecad_instance` / `list_freecad_instances` / `select_freecad_instance` / `stop_freecad_instance` — headless instance management (see Headless Mode below)

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

## Headless Mode

You can spawn and manage FreeCAD instances directly — no GUI required. The bridge launches `FreeCADCmd` (FreeCAD's console binary) with `headless_server.py` as its entry point, which starts the same socket server as the GUI workbench.

### When to use it

- No display available (server, CI environment)
- Batch modeling work that doesn't need a 3D view
- Running multiple FreeCAD instances in parallel for independent jobs

### How to use it

```
spawn_freecad_instance()                      # start headless, auto-selects it
spawn_freecad_instance(label="part-a")        # with a label for easy reference
list_freecad_instances()                      # see all running instances
select_freecad_instance(label="part-a")       # switch active target
stop_freecad_instance(label="part-a")         # shut it down
```

After `spawn_freecad_instance()`, all subsequent tool calls route to the new instance until you switch. The bridge manages the process — you do not need to start FreeCAD manually.

### Limitations

- **No screenshots** — `view_control(operation="screenshot")` requires a display
- **No interactive selection** — fillet, chamfer, and hole operations need the GUI for edge/face selection; the `continue_selection` workflow does not work headless
- **No 3D view** — any operation that renders to the viewport (clip planes, view orientation) has no effect

For work that needs screenshots or GUI selection, the user must have the FreeCAD GUI running. Both the GUI instance and any headless instances can run simultaneously; use `select_freecad_instance()` to switch between them.

---

## Known Issues

See `KNOWN_ISSUES.md` in the repo for full details:

- **GIL deadlock** — Creating documents from the socket thread can crash FreeCAD. Always use `view_control(operation="create_document")`.
- **Large documents** — `list_objects` paginates at 100 objects (max 500). Use filters for DXF imports with 1000+ objects.
- **Fillet/chamfer requires GUI** — Edge selection for dress-up operations needs the FreeCAD GUI running (not headless).

## Building Extensions: Prompt Caching and Direct API Calls

If you are a developer extending this MCP server or building agent applications that make direct calls to the Claude API (via the Anthropic SDK), you need to understand **prompt caching**.

### How Caching Works in Claude Code

Claude Code's desktop app, web interface, and CLI tools silently optimize repeated interactions by caching large context — file contents, tool definitions, system prompts — so subsequent requests reuse cached tokens instead of re-transmitting them. This optimization is transparent to users and built into the platform.

### If You Make Direct API Calls

When you call the Claude API directly (not through Claude Code or another platform), **caching is not automatic**. You must:

1. **Read the [Anthropic SDK documentation](https://docs.anthropic.com/en/docs/build-a-system-with-claude/prompt-caching)** on prompt caching before deploying any integration
2. **Understand the implications:**
   - Cache keys depend on model, system prompt, and exact token boundaries (small formatting changes bust the cache)
   - Cached tokens cost 20% of uncached tokens; cache lifetime is typically 5 minutes
   - Caching is worthwhile only if you're making repeated requests over the same context (e.g., refining a multi-turn conversation, running analysis on the same document)
   - Cache misses on every request waste compute for no benefit
3. **Test cache behavior** in your integration — verify that your assumptions about cache hits are correct before production

### Third-Party Integrations and Other Agents

If you're integrating other MCP servers or building agents that call external APIs:

- **Check their documentation** for caching behavior, rate limits, async job handling, and token limits
- Don't assume they work like Claude Code — each agent and service has different optimization strategies
- Some may buffer requests, some may require explicit polling, some may have quotas or cost implications
- Ask yourself: does this service cache? Does it support streaming? What happens on timeout?

If documentation is missing or unclear, read the source code or ask the maintainer.

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

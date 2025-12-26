# FreeCAD MCP Project Status

*Last updated: 2025-12-26*

## Current State

### Installation Locations

| Component | Location | Notes |
|-----------|----------|-------|
| **Canonical MCP Bridge** | `~/.freecad-mcp/` | Contains `working_bridge.py` and `mcp_bridge_framing.py` |
| **Dev Repo** | `/Volumes/Additional Files/development/freecad-mcp/` | Main development, push to origin and gitea |
| **FreeCAD Workbench** | `~/Library/Application Support/FreeCAD/Mod/AICopilot/` | The socket server runs inside FreeCAD |

### Git Remotes (dev repo)

| Remote | URL | Purpose |
|--------|-----|---------|
| `origin` | `github.com/blwfish/freecad-mcp.git` | Your fork |
| `upstream` | `github.com/contextform/freecad-mcp.git` | Original repo |
| `gitea` | `localhost:3000/blw/freecad-mcp` | Local backup |

### Configuration Files

- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Code:** `~/.claude.json` (mcpServers section)

Both point to: `python3 ~/.freecad-mcp/working_bridge.py`

---

## Open Items

### Pull Request to Upstream
- PR submitted to `contextform/freecad-mcp` with:
  - `74c120a Fix spreadsheet_ops threading crash from socket server`
  - `2f206c5 Add smoke tests for newly exposed MCP operations`
- Status: Awaiting review (no response yet)

### Recent Changes (not in upstream PR)
- `efd8ed1 Fix manual install to include mcp_bridge_framing.py`
- `5941042 Add installation directions`
- `61034e7 Add installation directions`

---

## Workflow

### To update the running MCP bridge:
```bash
cp "/Volumes/Additional Files/development/freecad-mcp/working_bridge.py" ~/.freecad-mcp/
cp "/Volumes/Additional Files/development/freecad-mcp/mcp_bridge_framing.py" ~/.freecad-mcp/
# Then restart Claude Desktop/Code
```

### To push changes:
```bash
cd "/Volumes/Additional Files/development/freecad-mcp"
git push origin main
git push gitea main
```

---

## Architecture

```
Claude Desktop/Code
    | (MCP protocol over stdio)
    v
~/.freecad-mcp/working_bridge.py
    | (Unix socket: /tmp/freecad_mcp.sock)
    v
FreeCAD: AICopilot/socket_server.py
    | (FreeCAD Python API)
    v
FreeCAD operations (create objects, modify geometry, etc.)
```

---

## Troubleshooting

### MCP not connecting
1. Ensure FreeCAD is running with AICopilot workbench
2. Check FreeCAD console for "MCP Socket Server started"
3. Verify socket exists: `ls -la /tmp/freecad_mcp.sock`
4. Restart Claude Desktop/Code after config changes

### After editing bridge code
Remember to copy changes from dev repo to `~/.freecad-mcp/` for them to take effect.

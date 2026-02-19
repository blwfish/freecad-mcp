# FreeCAD MCP Project Status

*Last updated: 2026-02-18*

## Current State

**socket_server.py v5.0.0** — Core rewrite complete. Upstream abandoned; we own the full codebase.

### Installation Locations

| Component | Location | Notes |
|-----------|----------|-------|
| **MCP Bridge** | `~/.freecad-mcp/` | `working_bridge.py` + `mcp_bridge_framing.py` |
| **Dev Repo** | `/Volumes/Files/claude/freecad-mcp/` | Main development |
| **FreeCAD Module** | `~/Library/Application Support/FreeCAD/v1-2/Mod/AICopilot/` | Socket server runs inside FreeCAD |

### Git Remotes

| Remote | URL | Purpose |
|--------|-----|---------|
| `origin` | `github.com/blwfish/freecad-mcp.git` | Our fork (primary) |
| `gitea` | `localhost:3000/blw/freecad-mcp` | Local backup |
| `upstream` | `github.com/contextform/freecad-mcp.git` | Original repo (abandoned) |

### Configuration

- **Claude Code:** `claude mcp add freecad python3 ~/.freecad-mcp/working_bridge.py`
- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`

---

## v5.0.0 Core Rewrite (2026-02-18)

Rewrote the server core and deleted all dead upstream code:

**Changed:**
- `AICopilot/socket_server.py` — 976→732 lines. Unified dispatch, `Queue.get(timeout)` replaces busy-wait polling, zero bare `except:` clauses.
- `AICopilot/InitGui.py` — 250→96 lines. Stripped workbench UI and event observer.

**Deleted (~14,000 lines):**
- `freecad_agent.py` — broken autonomous agent, never imported
- `memory_system.py` — unwired SQLite learning system
- `modal_command_system.py` — superseded by PartDesignOpsHandler
- `event_observer.py` — captured data that went nowhere
- `commands/` directory — workbench UI commands
- `archive/` directory — old socket_server snapshots

**Added:**
- `tests/unit/` — 74 unit tests (pytest), no FreeCAD required
- `.github/workflows/tests.yml` — GitHub Actions CI (Ubuntu + macOS, Python 3.10/3.12/3.13)
- `pyproject.toml` — project metadata + pytest config

### Branch: `rewrite-core`
Commit: `1c6350e` (rewrite) + uncommitted test/CI additions.

---

## Near-term TODO

- [ ] **Smoke test with live FreeCAD** — start FreeCAD, verify socket connects, run create_box / pad / fillet / list_objects / execute_python / spreadsheet
- [ ] Sync rewritten AICopilot/ to `~/Library/Application Support/FreeCAD/v1-2/Mod/AICopilot/`
- [ ] Merge `rewrite-core` to `main` after smoke test passes
- [ ] Push to origin and gitea

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
    | (FreeCAD Python API via modular handlers)
    v
FreeCAD operations (create objects, modify geometry, etc.)
```

---

## Workflow

### Update running MCP bridge:
```bash
cp /Volumes/Files/claude/freecad-mcp/working_bridge.py ~/.freecad-mcp/
cp /Volumes/Files/claude/freecad-mcp/mcp_bridge_framing.py ~/.freecad-mcp/
# Then restart Claude Desktop/Code
```

### Sync to FreeCAD:
```bash
rsync -av --delete /Volumes/Files/claude/freecad-mcp/AICopilot/ \
  ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/
```

### Run unit tests:
```bash
cd /Volumes/Files/claude/freecad-mcp && python3 -m pytest
```

### Push changes:
```bash
git push origin main && git push gitea main
```

---

## Troubleshooting

### MCP not connecting
1. Ensure FreeCAD is running with AICopilot loaded
2. Check FreeCAD console for "AI Socket Server started - Claude ready"
3. Verify socket exists: `ls -la /tmp/freecad_mcp.sock`
4. Restart Claude Desktop/Code after config changes

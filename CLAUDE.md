# FreeCAD MCP Integration

MCP server to control FreeCAD from Claude.

## Setup

Bridge files installed to: `~/.freecad-mcp/`
FreeCAD module installed to: `~/Library/Application Support/FreeCAD/v1-2/Mod/AICopilot/`

MCP registered with Claude Code via:
```bash
claude mcp add freecad python3 ~/.freecad-mcp/working_bridge.py
```

## Architecture

```
Claude Desktop/Code
    | (MCP protocol over stdio)
working_bridge.py
    | (Socket connection)
AICopilot/socket_server.py (inside FreeCAD)
    | (FreeCAD API via modular handlers)
FreeCAD operations
```

## Development

See `.claude-project-config.md` for paths, versions, architecture.

### Run unit tests
```bash
python3 -m pytest
```

### Sync to FreeCAD
```bash
rsync -av --delete AICopilot/ ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/
```

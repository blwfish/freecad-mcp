# FreeCAD MCP Installation Guide

Control FreeCAD with Claude AI through the Model Context Protocol (MCP).

**Requirements:**
- FreeCAD 1.0+ (1.2-dev recommended, download from [freecad.org](https://freecad.org/downloads.php))
- Python 3.10+
- Claude Desktop or Claude Code

---

## Quick Install (Recommended)

### For Claude Code Users

```bash
# Install the setup tool
npm install -g freecad-mcp-setup

# Run setup (installs workbench + registers MCP server)
freecad-mcp setup
```

That's it! The installer:
1. Downloads the AICopilot workbench to FreeCAD's Mod folder
2. Downloads the MCP bridge server to `~/.freecad-mcp/`
3. Registers the MCP server with Claude Code

### For Claude Desktop Users

```bash
# Install the setup tool
npm install -g freecad-mcp-setup

# Run setup (installs workbench only)
freecad-mcp setup
```

Then manually add to your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "freecad": {
      "command": "python3",
      "args": ["/Users/YOUR_USERNAME/.freecad-mcp/working_bridge.py"]
    }
  }
}
```

---

## Manual Installation

If the NPM installer doesn't work, install manually:

### Step 1: Install the FreeCAD Workbench

Clone or download this repository, then copy the `AICopilot` folder to FreeCAD's Mod directory:

| Platform | FreeCAD Mod Directory |
|----------|----------------------|
| macOS    | `~/Library/Application Support/FreeCAD/Mod/` |
| Linux    | `~/.local/share/FreeCAD/Mod/` |
| Windows  | `%APPDATA%\FreeCAD\Mod\` |

```bash
# Example for macOS
cp -r AICopilot ~/Library/Application\ Support/FreeCAD/Mod/
```

### Step 2: Install the MCP Bridge

Copy the bridge files somewhere permanent:

```bash
mkdir -p ~/.freecad-mcp
cp working_bridge.py mcp_bridge_framing.py ~/.freecad-mcp/
```

### Step 3: Register with Claude

**For Claude Code:**
```bash
claude mcp add freecad python3 ~/.freecad-mcp/working_bridge.py
```

**For Claude Desktop:**
Edit your config file (see paths above) and add:
```json
{
  "mcpServers": {
    "freecad": {
      "command": "python3",
      "args": ["/full/path/to/.freecad-mcp/working_bridge.py"]
    }
  }
}
```

---

## Verify Installation

1. **Start FreeCAD** - The AICopilot workbench should load automatically
2. **Check FreeCAD console** for: `MCP Socket Server started`
3. **Start Claude** and ask: "What FreeCAD tools are available?"
4. You should see tools like `mcp__freecad__create_box`, `mcp__freecad__view_control`, etc.

---

## Updating

```bash
freecad-mcp setup --update
```

Or manually replace the `AICopilot` folder and the files in `~/.freecad-mcp/` with the latest versions.

---

## Troubleshooting

### "MCP server not responding"
- Make sure FreeCAD is running
- Check FreeCAD console for errors
- Verify the socket server started: look for `MCP Socket Server started on /tmp/freecad_mcp.sock`

### "Tools not showing up in Claude"
- Restart Claude Desktop/Code after config changes
- Verify the path to `working_bridge.py` is correct in your config
- Check that Python 3 is in your PATH

### "FreeCAD workbench not loading"
- Ensure `AICopilot` folder is in the correct Mod directory
- Check FreeCAD version is 1.0 or higher
- Look at FreeCAD console for import errors

### Windows-specific issues
- Use `python` instead of `python3` in your config
- Use forward slashes or escaped backslashes in paths
- Try TCP socket if Unix socket fails (edit `working_bridge.py`)

---

## Uninstalling

1. Remove the workbench:
   ```bash
   rm -rf ~/Library/Application\ Support/FreeCAD/Mod/AICopilot  # macOS
   ```

2. Remove the MCP bridge:
   ```bash
   rm -rf ~/.freecad-mcp
   ```

3. Remove from Claude:
   ```bash
   claude mcp remove freecad  # For Claude Code
   ```
   Or remove the `freecad` entry from `claude_desktop_config.json`

---

## Architecture

```
Claude Desktop/Code
    ↓ (MCP protocol over stdio)
working_bridge.py
    ↓ (Socket connection)
AICopilot/socket_server.py (inside FreeCAD)
    ↓ (FreeCAD API)
FreeCAD operations
```

The bridge translates MCP tool calls into FreeCAD commands via a socket connection to the running FreeCAD instance.

---

## Support

- **Issues:** https://github.com/blwfish/freecad-mcp/issues
- **Docs:** See [CLAUDE.md](CLAUDE.md) for tool reference and usage

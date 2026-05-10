# FreeCAD MCP Installation

**Requirements:**
- FreeCAD 1.1.x or 1.2-dev ([freecad.org](https://freecad.org/downloads.php)) — CAM requires 1.2-dev
- Python 3.10+
- An MCP-compatible AI agent

---

## Recommended: Let Your Agent Install It

You're here because you have an AI agent. Have it do the installation.

Give your agent this prompt:

> Go to https://github.com/blwfish/freecad-mcp and read the AGENT-INSTALL.md file. Follow the instructions to install and configure the FreeCAD MCP server on this machine.

Your agent will handle the rest — installing prerequisites, cloning the repo, deploying the workbench, and registering itself with your agent platform.

---

## Manual Installation

If you need to install without an agent, or want to understand what the agent does, follow the steps below. This is not the recommended path.

### Step 1: Install the FreeCAD Workbench

Clone this repository, then copy the `AICopilot` folder to FreeCAD's Mod directory:

| Platform | FreeCAD Mod Directory |
|----------|----------------------|
| macOS    | `~/Library/Application Support/FreeCAD/Mod/` |
| Linux    | `~/.local/share/FreeCAD/Mod/` |
| Windows  | `%APPDATA%\FreeCAD\Mod\` |

```bash
git clone https://github.com/blwfish/freecad-mcp.git
cd freecad-mcp

# macOS example
cp -r AICopilot ~/Library/Application\ Support/FreeCAD/Mod/
```

If the Mod directory doesn't exist, create it.

### Step 2: Install the MCP Bridge

```bash
mkdir -p ~/.freecad-mcp
cp freecad_mcp_server.py mcp_bridge_framing.py ~/.freecad-mcp/
pip3 install mcp>=0.1.0
```

### Step 3: Register with Your Agent

The bridge speaks standard MCP over stdio. Registration syntax varies by agent platform.

**Generic MCP config** (works for any MCP-compatible agent):
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

Use absolute paths. On Windows, use `python` instead of `python3`.

**Claude Code** has a CLI shortcut:
```bash
claude mcp add freecad python3 ~/.freecad-mcp/freecad_mcp_server.py
```

### Step 4: Start FreeCAD

FreeCAD must be running with the AICopilot addon loaded. On first launch after installing the addon, FreeCAD detects it automatically. You should see `AI Socket Server started` in FreeCAD's Report View (menu: View → Panels → Report View).

---

## Verify Installation

Once FreeCAD is running and your agent is configured, ask your agent:

> Check if the FreeCAD MCP connection is working.

Your agent will call `check_freecad_connection()`. If it succeeds, the full pipeline is working. If it fails, FreeCAD is either not running or the AICopilot addon didn't load — check the Report View for errors.

---

## Updating

Pull the latest changes, then re-copy the workbench and bridge files:

```bash
git pull
cp -r AICopilot ~/Library/Application\ Support/FreeCAD/Mod/  # macOS
cp freecad_mcp_server.py mcp_bridge_framing.py ~/.freecad-mcp/
```

Or give your agent the same installation prompt again — it will update in place.

---

## Troubleshooting

### "Cannot connect to FreeCAD"
- FreeCAD must be running before your agent can connect
- Check Report View for `AI Socket Server started` — if missing, the addon didn't load
- Verify `AICopilot` is in the correct Mod directory

### Tools not showing up in your agent
- Restart your agent after registering the MCP server
- Verify the path to `freecad_mcp_server.py` is correct and absolute
- Check that Python 3.10+ is on your PATH

### FreeCAD workbench not loading
- Confirm `AICopilot` is in the correct Mod directory for your platform
- FreeCAD version must be 1.1 or higher
- Check the Report View or FreeCAD console for Python import errors

---

## Uninstalling

```bash
# Remove the workbench (macOS)
rm -rf ~/Library/Application\ Support/FreeCAD/Mod/AICopilot

# Remove the bridge
rm -rf ~/.freecad-mcp
```

Then remove the `freecad` MCP server entry from your agent's config.

---

## Architecture

```
AI Agent ──(MCP over stdio)── freecad_mcp_server.py ──(Unix socket)── FreeCAD + AICopilot addon
```

Two components: the **AICopilot workbench** (a FreeCAD addon that runs a socket server inside FreeCAD) and the **MCP bridge** (a Python script that translates MCP tool calls into socket messages). Both must be installed. FreeCAD must be running with the addon loaded for any tools to work.

---

## Issues

[github.com/blwfish/freecad-mcp/issues](https://github.com/blwfish/freecad-mcp/issues)

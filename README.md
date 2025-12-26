# FreeCAD MCP

Control FreeCAD with Claude AI through the Model Context Protocol.

## Quick Install

```bash
npm install -g freecad-mcp-setup
freecad-mcp setup
```

This installs the FreeCAD workbench and registers the MCP server with Claude.

**Requirements:** FreeCAD 1.0+, Python 3.8+, Claude Desktop or Claude Code

See [INSTALL.md](INSTALL.md) for detailed instructions and troubleshooting.

## What It Does

- Create and modify 3D models through natural language
- Full PartDesign workflow: sketches, pads, fillets, chamfers, holes, patterns
- CAM/CNC toolpath generation (FreeCAD 1.2+)
- Take screenshots and control views
- Execute Python scripts in FreeCAD

## Example

> "Create a 50x30x10mm box, then add a 5mm fillet to the top edges"

Claude will use the MCP tools to create the geometry directly in FreeCAD.

## Documentation

- [INSTALL.md](INSTALL.md) - Installation guide
- [MCP-CAPABILITIES.md](MCP-CAPABILITIES.md) - Available tools and operations
- [docs/CLAUDE_DESKTOP_MCP_USAGE.md](docs/CLAUDE_DESKTOP_MCP_USAGE.md) - Detailed usage guide

## License

LGPL-2.1-or-later

# freecad-mcp

This tool enables your AI agent to use [FreeCAD](https://www.freecad.org/) — the open-source parametric 3D CAD modeler — to design parts, generate CNC toolpaths, and produce manufacturing-ready files for you.

## What This Does

You describe what you need — "design a mounting bracket with these dimensions" or "generate G-code for this part" — and your AI agent does the rest: creating parametric sketches, padding and pocketing features, adding fillets, setting up CAM jobs, and exporting files. All using the same FreeCAD that engineers and makers use, with 26 tools covering parametric design, CNC toolpath generation, and mesh operations.

You don't need to know FreeCAD. You don't need to know what parametric CAD means. You just need an AI agent (like [Claude](https://claude.ai/)).

## Getting Started

Tell your AI agent:

> Go to https://github.com/blwfish/freecad-mcp and read the AGENT-INSTALL.md file. Follow the instructions to install and configure the FreeCAD MCP server on this machine.

Your agent will handle the rest — installing prerequisites, cloning the repo, setting up the FreeCAD addon, and registering itself. Once setup is complete, you can ask your agent to design parts.

## What You Can Ask Your Agent To Do

- **Design a 3D part** — "I need a mounting plate, 100x60mm, with four M3 mounting holes and rounded corners"
- **Modify an existing design** — "Add a 2mm fillet to all the edges and make it 5mm thicker"
- **Generate CNC toolpaths** — "Create a pocket operation for this part using a 6mm end mill"
- **Export for manufacturing** — "Export this as STEP and generate the G-code for my CNC router"
- **Work with meshes** — "Import this STL, convert it to a solid, and add mounting features"

## Background

I built this for myself. I use Claude Code on a Mac. Other platforms *should* work — the code handles macOS, Windows, and Linux — but are less tested. PRs for other agents and platforms will be considered.

### For Developers

```bash
# 174 tests, no FreeCAD required
python3 -m pytest

# Sync AICopilot workbench to FreeCAD's Mod directory
./deploy.sh
```

See [AGENT-INSTALL.md](AGENT-INSTALL.md) for full technical details, architecture, contributing guidelines, and how to add new tools.

## License

LGPL-2.1-or-later

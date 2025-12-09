# 👋 Hello Claude Desktop!

You have MCP access to FreeCAD running on this system.

## Quick Start

1. **Check if FreeCAD is running**: Ask the user or try a simple operation
2. **Read capabilities**: See `MCP-CAPABILITIES.md` for all available tools
3. **Common operations**: Listed below

## What You Can Do Right Now

### Take a Screenshot
```json
{
  "tool": "view_control",
  "args": {"operation": "screenshot"}
}
```

### List Objects in Current Document
```json
{
  "tool": "view_control",
  "args": {"operation": "list_objects"}
}
```

### Create a Simple Box
```json
{
  "tool": "part_operations",
  "args": {
    "operation": "box",
    "length": 10,
    "width": 10,
    "height": 10
  }
}
```

### Execute Python to Inspect
```json
{
  "tool": "execute_python",
  "args": {
    "code": "doc = FreeCAD.ActiveDocument; print([obj.Label for obj in doc.Objects])"
  }
}
```

## Important Files

- **`MCP-CAPABILITIES.md`** - Full list of tools and how to use them
- **`CLAUDE.md`** - User-facing workflow (modal commands)
- **`.claude-project-config.md`** - Developer info (you probably don't need this)

## Architecture Overview

```
User in FreeCAD
    ↕ (visual interaction)
FreeCAD GUI
    ↕ (socket)
Socket Server (runs in FreeCAD process)
    ↕ (unix socket /tmp/freecad_mcp.sock)
MCP Bridge (working_bridge.py)
    ↕ (MCP protocol)
YOU (Claude Desktop with MCP)
```

## Key Concepts

### 1. Universal Selector
Operations like fillet/chamfer need edge selection:
- You call the tool → returns `"awaiting_selection"`
- User selects in FreeCAD GUI
- User says "done"
- You call `complete_selection` with operation_id
- System performs operation

### 2. Modal Workflow
Some operations open native FreeCAD dialogs:
- More natural for users
- They configure in familiar UI
- System reports when done

### 3. Smart Dispatchers
Use high-level tools like:
- `partdesign_operations` - Parametric modeling
- `part_operations` - Direct solid modeling
- `view_control` - View and document management
- `cam_operations` - CNC toolpath generation

## Common Patterns

### Creating a Parametric Part
1. Create primitives or sketches
2. Use `partdesign_operations` with `pad`, `pocket`, etc.
3. Add dress-up features (fillet, chamfer)
4. Take screenshot to show result

### Working with User Selection
1. Ask user what they want to operate on
2. Use `list_objects` to see what's available
3. Call operation with object name
4. If it needs selection, guide user through it

### Inspecting Current State
1. `view_control` → `list_objects`
2. `execute_python` for detailed info
3. `screenshot` to see visually

## Tips

- **Start simple**: list_objects and screenshot
- **Be conversational**: Explain what you're doing
- **Use modal workflow**: More natural for CAD users
- **Don't guess**: Use UniversalSelector instead of guessing edge numbers
- **Execute Python**: Powerful fallback for anything not covered

## Version Info

**Socket Server**: v3.4.0 (2024-12-09)
- Clean handler-based architecture
- All tools working properly

---

**Ready to assist with FreeCAD!** 🚀

Read `MCP-CAPABILITIES.md` for comprehensive tool documentation.

# FreeCAD MCP v3.0.0
**Socket Server with Token Safety & FreeCAD 1.0+ CAM Support**

---

## ✨ Recent Updates

- **CAM Operations Fixed**: Full compatibility with FreeCAD 1.0+ CAM workbench
- **Module Structure**: Updated imports for new `Path.Main.*` and `Path.Op.*` modules
- **Backward Compatible**: Automatic fallback to older `PathScripts.*` for pre-1.0 versions

---

## 🚀 Quick Start

```bash
# 1. Extract
tar -xzf freecad_mcp_v3.0.0.tar.gz
cd freecad_mcp_v3.0.0

# 2. Install to FreeCAD
python3 freecad_installer.py

# 3. Stage to git
python3 git_populate.py

# 4. Restart FreeCAD
# Done!
```

For detailed instructions, see DEPLOYMENT_GUIDE.md

# Version History

## Current Versions (2025-12-10)

| Component | Version | Lines | Status |
|-----------|---------|-------|--------|
| socket_server.py | 4.0.0 | 742 | ✅ Active - CAM CRUD + FC 1.2 |
| freecad_debug.py | 1.1.0 | - | ✅ Active |
| freecad_health.py | 1.0.1 | - | ✅ Active |

## socket_server.py Version History

### v4.0.0 (2025-12-10) - **CURRENT**
- **Major**: FreeCAD 1.2.0 CAM API compatibility + Complete CRUD operations
- **Breaking Changes**:
  - Tool creation API changed (Factory.CreateBit → ToolBit.from_shape_id)
  - TypeId changed (Path::ToolBit → Part::FeaturePython)
- **New Features**:
  - 20 new CAM CRUD operations (tools, controllers, operations, jobs)
  - Complete regression testing framework (P0-P3 priorities)
  - Console mode support (FreeCAD.GuiUp check in InitGui.py)
  - Test mode (FREECAD_MCP_TEST_MODE environment variable)
- **New Handlers**:
  - cam_tools.py (5 operations)
  - cam_tool_controllers.py (5 operations)
  - cam_ops.py enhanced (10 new operations)
- **Documentation**:
  - CAM_CRUD_OPERATIONS.md
  - CAM_API_CHANGES_FC12.md
  - README_TESTING.md
- **Testing**: Manual regression testing caught 4 critical bugs before release

### v3.4.0 (2024-12-09)
- **Lines**: 742 (down from 4,541)
- **Size**: 27KB (down from 182KB)
- **Change**: Complete clean rewrite with proper handler architecture
- **Key**: ZERO embedded operation methods, all routing to handlers
- **Reason**: Previous versions had handlers imported but unused, massive code duplication

### v3.3.3 (2024-12-08)
- Refactored GUI-safe operations to handlers
- Still had embedded methods mixed with handler calls

### v3.3.1 (2024-12-08)
- Removed 11 placeholder inline methods
- Partial handler usage

### v3.3.0 (2024-12-08)
- First attempt at proper refactoring with handler delegation
- Had issues, some reverts

### v3.2.0 (2024-12-08)
- Integrated console output with execute_python
- Still monolithic

### v3.1.0 (2024-12-08)
- Added Console Observer for Report View access
- Growing larger

### v3.0.0 (2024-12-08)
- Fixed CAM workbench compatibility for FreeCAD 1.0+
- Fixed PySide imports
- **File size**: 4,541 lines, 182KB (bloated!)
- **Problem**: Handlers imported but not used, all operations inline

### v2.1.2 (Earlier)
- Root-level version (now archived, was not being used)
- 3,730 lines, 149KB

## Version Bumping Rules

**ALWAYS increment from the CURRENT version in this file!**

### When to bump MAJOR (X.0.0)
- Breaking API changes (✅ v4.0.0: Tool creation API changed)
- Complete architecture rewrites
- Incompatible changes to handler interface
- TypeId changes that affect existing code

### When to bump MINOR (3.X.0)
- New features added
- New handlers added
- Significant refactoring (like v3.4.0)
- New workbench support

### When to bump PATCH (3.4.X)
- Bug fixes
- Small improvements
- Performance optimizations
- Documentation updates

## Checking Current Version

```bash
# In Python files
grep "__version__" AICopilot/socket_server.py

# In git history
git log --oneline --all -10 | grep -i "release\|version"

# Check this file!
cat VERSIONS.md
```

## Archive Location

Archived versions are in `archive/` with descriptive names:
- `socket_server_v3.0.0_bloated.py` - The 4,541 line monster
- `socket_server_v2.1.2_root_unused.py` - Old root-level version

**Last Updated**: 2025-12-10

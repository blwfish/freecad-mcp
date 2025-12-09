# FreeCAD MCP Changelog

## v3.0.0 - CAM Workbench 1.0+ Compatibility

**Release Date**: December 8, 2024
**Type**: Major update - Breaking changes for FreeCAD 1.0+

### 🚀 Major Changes

**CAM Operations - FreeCAD 1.0+ Compatibility**

Fixed import errors when using CAM operations with FreeCAD 1.0+ and the reorganized CAM workbench:

- ✅ Fixed `ImportError: cannot import name 'Job' from 'Path'`
- ✅ Updated all CAM operation imports to use new FreeCAD 1.0+ module structure
- ✅ Added backward compatibility for older FreeCAD versions (automatic fallback)
- ✅ Added ViewProvider setup for proper GUI integration

**Module Structure Changes:**
- `Path.Job.Create()` → `Path.Main.Job.Create()`
- `Path.Stock.*` → `Path.Main.Stock.*`
- `PathScripts.PathProfile` → `Path.Op.Profile`
- `PathScripts.PathPocket` → `Path.Op.Pocket`
- `PathScripts.PathDrilling` → `Path.Op.Drilling`
- `PathScripts.PathAdaptive` → `Path.Op.Adaptive`
- `PathScripts.PathPost` → `Path.Post.Processor`

**Operations Fixed:**
- Job creation (`create_job`)
- Stock setup (`setup_stock`)
- Profile/contour operations (`profile`)
- Pocket milling (`pocket`)
- Drilling operations (`drilling`)
- Adaptive clearing (`adaptive`)
- G-code post-processing (`post_process`)

**Files Updated:**
- `AICopilot/handlers/cam_ops.py` - All CAM operation handlers with new imports
- `AICopilot/socket_server.py` - CAM operation methods with new imports
- `docs/CAM_OPERATIONS.md` - Updated documentation with compatibility notes and migration guide

### 📚 Documentation

- Added FreeCAD 1.0+ compatibility section to CAM_OPERATIONS.md
- Updated README with version 3.0.0 information
- Added migration guide for module structure changes

### ⚠️ Breaking Changes

This version requires FreeCAD 1.0+ for CAM operations. The code includes automatic fallback for older versions, but new features will target FreeCAD 1.0+.

---

# FreeCAD MCP v2.1.2 Release Notes

**Release Date**: December 5, 2025
**Type**: Deployment packaging update

---

## Changes from v2.1.1 → v2.1.2

### 📦 Proper Deployment Scripts

**Added deployment automation** following established conventions:

1. **freecad_installer.py** - Installs socket_server.py to FreeCAD AICopilot directory
   - Auto-detects FreeCAD installation (macOS, Linux, Windows)
   - Auto-detects versioned directories (v1-2, v1-1, etc.)
   - Creates backup of existing socket_server.py
   - Cross-platform support

2. **git_populate.py** - Copies files to git repository for version control
   - Default: `/Volumes/Additional Files/development/freecad-mcp`
   - Stages files but does NOT commit (review first!)
   - Clear instructions for git workflow

3. **Unversioned filenames** for cleaner git history:
   - `socket_server.py` (not socket_server_v2.1.2.py)
   - `working_bridge.py` (not working_bridge_v2.1.2.py)
   - Version tracking via `__version__` string inside files

### 🔧 Technical Notes

No functional changes from v2.1.1:
- ✅ 50KB message limit (same)
- ✅ Length-prefixed protocol (same)
- ✅ Token safety features (same)
- ✅ All bug fixes from v2.1.0/v2.1.1 (same)

Only difference: Proper deployment packaging.

---

## Installation

### Option 1: Automated Installation

```bash
# Extract deployment package
tar -xzf freecad_mcp_v2.1.2.tar.gz
cd freecad_mcp_v2.1.2

# Install to FreeCAD
python3 freecad_installer.py

# Stage to git repo
python3 git_populate.py

# Then commit from git repo
cd /Volumes/Additional\ Files/development/freecad-mcp
git status
git add socket_server.py working_bridge.py mcp_bridge_framing.py
git commit -m "Update to FreeCAD MCP v2.1.2"
git push
```

### Option 2: Manual Installation

See DEPLOYMENT_GUIDE.md for manual installation instructions.

---

## Upgrade from v2.1.1

If you're already running v2.1.1, you can:

**Option 1**: Just use the new scripts for future updates (no need to reinstall)

**Option 2**: Reinstall with proper automation:
```bash
python3 freecad_installer.py  # Will backup v2.1.1 first
```

The functionality is identical - this is purely about deployment workflow.

---

## Files in Package

### Core Files
- `socket_server.py` - FreeCAD server (v2.1.2)
- `working_bridge.py` - MCP bridge (v2.1.2)
- `mcp_bridge_framing.py` - Message framing utilities (v2.1.2)

### Deployment Scripts
- `freecad_installer.py` - Install to FreeCAD
- `git_populate.py` - Stage to git repository

### Documentation
- `README.md` - Overview and quick start
- `CHANGELOG.md` - This file
- `DEPLOYMENT_GUIDE.md` - Complete deployment procedures
- `QUICKSTART.md` - 30-second installation

---

## Testing

After installation:

1. **Verify version**:
   ```python
   # In FreeCAD console, check for:
   ✓ socket_server v2.1.2 validated
   ```

2. **Test MCP connection**:
   ```python
   # From Claude
   Use FreeCAD tools - should work normally
   ```

3. **Verify 50KB limit** (optional):
   ```python
   # Try to send >50KB message - should fail with helpful error
   ```

---

## Why This Version Matters

**For developers/maintainers**:
- Proper git workflow (no more manual file copying!)
- Automated installation (no more hunting for directories)
- Version tracking in files (not just filenames)
- Clean git history (unversioned filenames)

**For users**:
- Easier installation
- Clearer upgrade path
- Better documentation

---

## Compatibility

- ✅ Drop-in replacement for v2.1.1
- ✅ Same protocol, same features
- ✅ Works with existing bridges
- ✅ No FreeCAD restart needed (but recommended)

---

## What's Next

Future versions (v2.2.x+) may add:
- Configuration file for MAX_MESSAGE_SIZE
- Per-operation token limits
- Message compression
- Batch command support

These are optional enhancements - v2.1.2 is production-ready as-is.

---

**Summary**: v2.1.2 adds proper deployment automation with freecad_installer.py and git_populate.py scripts, following established conventions for cleaner workflow. No functional changes from v2.1.1.

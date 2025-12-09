# Quick Reference Card

## Before Making Changes

```bash
# 1. Check current version
grep "__version__" AICopilot/socket_server.py
# OR
cat VERSIONS.md

# 2. Check git history
git log --oneline --all -10

# 3. Read the rules
cat .claude-project-config.md
```

## Sync Commands (Copy-Paste Ready)

```bash
# Sync to FreeCAD v1-2 (YOU ARE RUNNING 1.2-dev!)
rsync -av --delete /Users/blw/.claude-worktrees/freecad-mcp/lucid-zhukovsky/AICopilot/ ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/

# Sync to main repo
rsync -av --delete /Users/blw/.claude-worktrees/freecad-mcp/lucid-zhukovsky/AICopilot/ "/Volumes/Additional Files/development/freecad-mcp/AICopilot/"
```

## Version Bumping

**Current: v3.4.0**

- Bug fix? → v3.4.1
- New feature? → v3.5.0
- Breaking change? → v4.0.0

## Architecture Check

```bash
# socket_server.py should be ~700-800 lines
wc -l AICopilot/socket_server.py

# If > 1000 lines, you probably added operations inline!
# Extract to handlers/ instead
```

## Handler Template

```python
from .base import BaseHandler

class MyHandler(BaseHandler):
    def my_operation(self, args):
        """Do something"""
        try:
            doc = self.get_document(create_if_missing=True)
            # ... implementation ...
            self.recompute(doc)
            return "Success message"
        except Exception as e:
            return f"Error: {e}"
```

## Common Mistakes

❌ `~/Library/Application Support/FreeCAD/Mod/AICopilot/`
✅ `~/Library/Application Support/FreeCAD/v1-2/Mod/AICopilot/`

❌ Version v3.1.0 (after v3.3.3)
✅ Version v3.4.0+

❌ Adding `_create_box()` to socket_server.py
✅ Using `self.primitives.create_box()` routing

## Files to Remember

| File | Purpose |
|------|---------|
| `.claude-project-config.md` | Full configuration details |
| `VERSIONS.md` | Version history and bumping rules |
| `CLAUDE.md` | User-facing workflow docs |
| `QUICK-REFERENCE.md` | This file - quick lookups |

---

**Need help?** Read `.claude-project-config.md`

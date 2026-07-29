#!/bin/bash
# deploy.sh — sync AICopilot workbench to the FC-clone FreeCAD prefs directory
#
# FreeCAD (FC-clone build) loads Mod/ from a versioned subdir of:
#   /Volumes/Files/claude/FreeCAD-prefs/
# named v<major>-<minor> (e.g. v1-2, v26-3), matching the running FreeCAD's
# version -- FreeCAD 26.3 loads v26-3, not v1-2. Hardcoding one of these
# directly bit us once already: this script deployed to v1-2 for weeks
# after the live version moved to v26-3, so every "fix" silently landed
# somewhere nothing read. Auto-selecting the newest versioned dir (same
# fix already applied in install.py's find_freecad_paths, commit d5ea20c)
# makes this forward-compatible with the next FreeCAD version bump instead
# of needing this hardcoded path updated by hand again.
#
# Run this after making changes to AICopilot/ to deploy them.
# Then restart the MCP server inside FreeCAD (or restart FreeCAD itself).

set -e

PREFS_BASE="/Volumes/Files/claude/FreeCAD-prefs"

DEST_MOD=$(python3 -c "
import pathlib, sys

base = pathlib.Path('$PREFS_BASE')

def ver_key(p):
    parts = p.name.lstrip('v').split('-')
    try:
        return tuple(int(x) for x in parts)
    except ValueError:
        return (0,)

versioned = sorted(
    (d for d in base.iterdir() if d.is_dir() and d.name.startswith('v') and (d / 'Mod').exists()),
    key=ver_key,
    reverse=True,
)
if not versioned:
    sys.exit(1)
print(versioned[0] / 'Mod')
")

if [ -z "$DEST_MOD" ]; then
    echo "Error: no versioned FreeCAD prefs dir with a Mod/ subdir found under $PREFS_BASE" >&2
    exit 1
fi

DEST="$DEST_MOD/AICopilot"

if [ ! -d "$DEST" ]; then
    echo "Error: destination not found: $DEST" >&2
    exit 1
fi

rsync -av --exclude='__pycache__' --exclude='*.pyc' \
    "$(dirname "$0")/AICopilot/" "$DEST/"

echo ""
echo "Deployed to: $DEST"
echo "Restart the FreeCAD MCP server to pick up changes."

#!/bin/bash
# deploy.sh — sync AICopilot workbench to the FC-clone FreeCAD prefs directory
#
# FreeCAD loads Mod/ from a versioned subdir of:
#   /Volumes/Files/claude/FreeCAD-prefs/
# named v<major>-<minor> (e.g. v1-2, v26-3), matching the running FreeCAD's
# version -- FreeCAD 26.3 loads v26-3, not v1-2. This is a real FreeCAD
# engine feature (appendVersionIfPossible/mostRecentAvailableConfigVersion
# in src/App/ApplicationDirectories.cpp, added Aug 2025), not something this
# project controls: on first launch against a given UserAppData base,
# FreeCAD creates and uses a vMAJOR-MINOR subdir; on later launches it walks
# downward from its own version looking for the newest vX-Y dir that already
# exists, using it even if it's not the current version. Hardcoding one of
# these directly bit us once already: this script deployed to v1-2 for
# weeks after the live version moved to v26-3, so every "fix" silently
# landed somewhere nothing read. Auto-selecting the newest versioned dir
# (same fix already applied in install.py's find_freecad_paths, commit
# d5ea20c) makes this forward-compatible with the next FreeCAD version bump
# instead of needing this hardcoded path updated by hand again.
#
# Older installs (from before FreeCAD versioned this directory) can also
# still have a bare, unversioned $PREFS_BASE/Mod/AICopilot left over from
# before the vX-Y scheme existed. FreeCAD's own dispatcher never reads it
# for the real socket server -- but it's still a real directory, and it
# still ends up on sys.path (FreeCAD appends the bare Mod/ dir in addition
# to each versioned addon dir), so `import AICopilot.<anything>` resolves
# there as a namespace package instead of erroring -- confirmed live
# 2026-09-09. That makes it a standing trap: any diagnostic or doc snippet
# that does a dotted `AICopilot.foo` import (as this repo's own CLAUDE.md
# used to) silently reads stale code and reports a false "not deployed"
# version. `import foo` / `import handlers.foo` (bare, no "AICopilot."
# prefix) is what the real dispatcher actually uses and is the only
# reliable way to check what's live. Rather than rely on nobody ever using
# the dotted form again, this script also keeps that bare directory in
# sync when present, so it can never again drift stale and mislead.
#
# Run this after making changes to AICopilot/ to deploy them.
# Then restart the MCP server inside FreeCAD (or restart FreeCAD itself).

set -e

PREFS_BASE="/Volumes/Files/claude/FreeCAD-prefs"
SRC="$(dirname "$0")/AICopilot/"

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

rsync -av --exclude='__pycache__' --exclude='*.pyc' "$SRC" "$DEST/"
echo ""
echo "Deployed (primary, live dispatch): $DEST"

# Defensive: keep a legacy bare Mod/AICopilot in sync too, if one exists.
# Never create it -- only sync it if some earlier install already put it
# there, so this doesn't manufacture a copy on a machine that never had one.
LEGACY_DEST="$PREFS_BASE/Mod/AICopilot"
if [ -d "$LEGACY_DEST" ] && [ "$LEGACY_DEST" != "$DEST" ]; then
    rsync -av --exclude='__pycache__' --exclude='*.pyc' "$SRC" "$LEGACY_DEST/"
    echo "Deployed (legacy bare dir, kept in sync defensively): $LEGACY_DEST"
fi

echo ""
echo "Restart the FreeCAD MCP server to pick up changes."

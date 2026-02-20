#!/bin/bash
# deploy.sh — sync AICopilot workbench to the FC-clone FreeCAD prefs directory
#
# FreeCAD (FC-clone build) loads the workbench from:
#   /Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/
#
# Run this after making changes to AICopilot/ to deploy them.
# Then restart the MCP server inside FreeCAD (or restart FreeCAD itself).

set -e

DEST="/Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot"

if [ ! -d "$DEST" ]; then
    echo "Error: destination not found: $DEST" >&2
    exit 1
fi

rsync -av --exclude='__pycache__' --exclude='*.pyc' \
    "$(dirname "$0")/AICopilot/" "$DEST/"

echo ""
echo "Deployed to: $DEST"
echo "Restart the FreeCAD MCP server to pick up changes."

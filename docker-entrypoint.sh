#!/bin/bash
set -e

SOCKET=/tmp/freecad_mcp.sock

# Start FreeCAD headless socket server in the background
"${FREECAD_MCP_FREECAD_BIN}" /opt/freecad-mcp/AICopilot/headless_server.py &
FC_PID=$!

# Wait up to 60s for the socket to appear
for i in $(seq 1 120); do
    [ -S "$SOCKET" ] && break
    sleep 0.5
done

if [ ! -S "$SOCKET" ]; then
    echo "ERROR: FreeCAD socket did not appear after 60s (pid $FC_PID)" >&2
    exit 1
fi

# Run MCP bridge as the foreground process (communicates with Glama via stdio)
exec python3 /opt/freecad-mcp/freecad_mcp_server.py

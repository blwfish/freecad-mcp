"""Headless FreeCAD MCP socket server entry point.

Runs inside FreeCADCmd (console/headless mode) to provide the same MCP socket
interface as the GUI workbench, without requiring a Qt display.

Usage:
    FreeCADCmd /path/to/headless_server.py [--socket-path /tmp/freecad_mcp_xxx.sock]

The socket path can also be set via the FREECAD_MCP_SOCKET environment variable.
If both are provided, --socket-path takes precedence.

FreeCAD's Init.py runs before this script, so the AICopilot module directory is
already on sys.path and `from freecad_mcp_handler import FreeCADSocketServer` works.
"""

import os
import sys
import signal
import time
import threading

# ---------------------------------------------------------------------------
# Ensure THIS script's directory is first on sys.path so we import the handler
# module from the same checkout. Without this we depend on FreeCAD's addon
# discovery happening to put the matching AICopilot dir first — which fails
# when running an out-of-tree worktree (e.g. integration tests).
#
# Also evict any pre-imported `freecad_mcp_handler` / `handlers` / `handlers.*`
# that FreeCAD's Init.py chain may have cached from the installed-addon path.
# Without eviction, our `from freecad_mcp_handler import ...` is a cache hit
# and silently uses the wrong copy.
# ---------------------------------------------------------------------------
_self_dir = os.path.dirname(os.path.abspath(__file__))
if sys.path and sys.path[0] != _self_dir:
    if _self_dir in sys.path:
        sys.path.remove(_self_dir)
    sys.path.insert(0, _self_dir)

# Evict any pre-imported `freecad_mcp_handler` / `handlers` that FreeCAD's
# Init.py chain may have cached from an installed-addon path. Without
# eviction, our `from freecad_mcp_handler import ...` is a cache hit and
# silently uses the wrong copy.
for _stale in [
    name for name in list(sys.modules)
    if name == "freecad_mcp_handler"
    or name == "handlers"
    or name.startswith("handlers.")
]:
    del sys.modules[_stale]

# ---------------------------------------------------------------------------
# Parse --socket-path early, before anything else touches sys.argv
# ---------------------------------------------------------------------------
def _parse_socket_path():
    """Extract --socket-path from sys.argv without disturbing other arg parsers."""
    argv = sys.argv[:]
    for i, arg in enumerate(argv):
        if arg == "--socket-path" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--socket-path="):
            return arg.split("=", 1)[1]
    return None


_socket_path_arg = _parse_socket_path()
if _socket_path_arg:
    os.environ["FREECAD_MCP_SOCKET"] = _socket_path_arg

# ---------------------------------------------------------------------------
# FreeCAD is already imported by FreeCADCmd context.  Import it here so we
# can use FreeCAD.Console for messages consistent with the rest of the server.
# ---------------------------------------------------------------------------
try:
    import FreeCAD
except ImportError:
    # Should never happen inside FreeCADCmd, but provide a fallback for tests.
    import types as _types
    FreeCAD = _types.SimpleNamespace(
        Console=_types.SimpleNamespace(
            PrintMessage=print,
            PrintError=lambda s: print(s, file=sys.stderr),
            PrintWarning=lambda s: print(s, file=sys.stderr),
        ),
        GuiUp=False,
    )

# ---------------------------------------------------------------------------
# Start the socket server
# ---------------------------------------------------------------------------
def main():
    requested_socket = os.environ.get("FREECAD_MCP_SOCKET")
    if requested_socket:
        FreeCAD.Console.PrintMessage(
            f"[Headless MCP] Starting socket server on {requested_socket}\n"
        )
    else:
        FreeCAD.Console.PrintMessage(
            "[Headless MCP] Starting socket server (path auto-generated)\n"
        )

    try:
        from freecad_mcp_handler import FreeCADSocketServer
    except ImportError as e:
        FreeCAD.Console.PrintError(
            f"[Headless MCP] Cannot import FreeCADSocketServer: {e}\n"
            "  Ensure AICopilot is installed as a FreeCAD addon.\n"
        )
        sys.exit(1)

    server = FreeCADSocketServer()
    if not server.start_server():
        FreeCAD.Console.PrintError("[Headless MCP] Failed to start socket server.\n")
        sys.exit(1)

    # Expose on FreeCAD module so execute_python code can reach it if needed
    FreeCAD.__ai_socket_server = server

    # Write discovery file (best-effort — never fatal).
    instance_uuid = None
    try:
        import instance_registry
        fc_version = None
        try:
            fc_version = ".".join(str(p) for p in FreeCAD.Version()[:3])
        except Exception:
            pass
        instance_registry.write_discovery(
            server.instance_uuid,
            server.socket_path,
            gui=False,
            label=os.environ.get("FREECAD_MCP_LABEL"),
            freecad_version=fc_version,
            freecad_binary=sys.executable,
        )
        instance_uuid = server.instance_uuid
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[Headless MCP] Discovery file not written: {e}\n")

    FreeCAD.Console.PrintMessage(
        f"[Headless MCP] Ready. Listening on {server.socket_path} "
        f"(uuid={server.instance_uuid})\n"
    )

    # ---------------------------------------------------------------------------
    # Keep the process alive until SIGTERM / SIGINT
    # ---------------------------------------------------------------------------
    _stop = threading.Event()

    def _on_signal(sig, frame):
        FreeCAD.Console.PrintMessage(
            f"[Headless MCP] Received signal {sig}, shutting down...\n"
        )
        _stop.set()

    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    except (OSError, ValueError):
        # signal.signal can fail in some environments (e.g. non-main thread)
        pass

    try:
        while not _stop.is_set():
            time.sleep(0.5)
    finally:
        FreeCAD.Console.PrintMessage("[Headless MCP] Stopping socket server.\n")
        server.stop_server()
        if instance_uuid:
            try:
                import instance_registry
                instance_registry.remove_discovery(instance_uuid)
            except Exception:
                pass
        if hasattr(FreeCAD, "__ai_socket_server"):
            del FreeCAD.__ai_socket_server


main()

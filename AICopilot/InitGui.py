# FreeCAD AI Copilot - MCP Socket Service
# Copyright (c) 2024
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Starts the MCP socket server automatically when FreeCAD GUI loads.
# The service runs globally across all workbenches.

import FreeCAD
import os
import sys

# Only load if GUI is available (skip in freecadcmd/console mode)
if not FreeCAD.GuiUp:
    FreeCAD.Console.PrintMessage("AICopilot: GUI not available, skipping initialization\n")
    __all__ = []
else:
    import FreeCADGui
    import inspect

    # Add our directory to Python path
    try:
        current_file = inspect.getfile(inspect.currentframe())
        path = os.path.dirname(current_file)
    except Exception:
        path = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "AICopilot")

    if path not in sys.path:
        sys.path.append(path)

    class GlobalAIService:
        """Global MCP socket service that runs across all workbenches."""

        def __init__(self):
            self.socket_server = None
            self.is_running = False

        @staticmethod
        def _connect_quit_cleanup(stop_callable):
            """Hook *stop_callable* to Qt's aboutToQuit so registry/socket
            cleanup runs on a normal quit (Cmd+Q, File>Quit), not just when
            something explicitly calls stop_freecad_instance/restart_freecad.

            Before this hook existed, GlobalAIService.stop() -- which removes
            the discovery JSON and unlinks the socket file -- only ran when an
            MCP tool call invoked it directly. A normal quit left both behind
            on disk indefinitely. aboutToQuit fires while the Qt event loop is
            still alive, so stop()'s cleanup can still run there. A
            crash/SIGKILL still can't run any cleanup code -- that case is
            already covered separately by
            instance_registry.scan_discovery's prune-on-next-scan.

            A staticmethod rather than a bare module-level function: FreeCAD's
            real Mod/ workbench loader executes InitGui.py with separate
            globals/locals dicts (like a class body), so a top-level `def`
            lands in locals while methods defined later in the same file only
            see globals -- calling it as a free name from inside start() then
            raises NameError, even though the exact same file loads fine
            through a normal single-namespace `exec_module()` (which is what
            this project's own test suite used, hence 1700 green tests never
            catching a bug that fired on every real FreeCAD launch). Scoping
            it to the class sidesteps that split entirely: class bodies
            capture their own namespace regardless of how the enclosing file
            was exec'd, so self._connect_quit_cleanup resolves via normal
            attribute lookup instead of a __globals__ lookup.

            Still unit-testable in isolation (the reason it was split out of
            start() to begin with) via GlobalAIService._connect_quit_cleanup(...)
            without needing the full start() dependency chain (which
            constructs a real FreeCADSocketServer).
            """
            try:
                from PySide import QtCore
                app = QtCore.QCoreApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(stop_callable)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not hook aboutToQuit cleanup: {e}\n")

        def start(self):
            """Start the MCP socket server."""
            if self.is_running:
                FreeCAD.Console.PrintMessage("AI Service already running\n")
                return True

            try:
                from freecad_mcp_handler import FreeCADSocketServer, __version__ as _handler_version
            except ImportError:
                _handler_version = "?"

            FreeCAD.Console.PrintMessage(f"Starting FreeCAD AI Copilot Service v{_handler_version}...\n")

            try:
                from freecad_mcp_handler import FreeCADSocketServer
                self.socket_server = FreeCADSocketServer()
                if self.socket_server.start_server():
                    FreeCAD.__ai_socket_server = self.socket_server
                    FreeCAD.Console.PrintMessage("AI Socket Server started - Claude ready\n")
                else:
                    FreeCAD.Console.PrintError("Failed to start AI socket server\n")
                    return False
            except Exception as e:
                FreeCAD.Console.PrintError(f"Socket server error: {e}\n")
                return False

            # Write discovery file so the bridge (and tools) can find this instance.
            try:
                import instance_registry
                fc_version = None
                try:
                    fc_version = ".".join(str(p) for p in FreeCAD.Version()[:3])
                except Exception:
                    pass
                instance_registry.write_discovery(
                    self.socket_server.instance_uuid,
                    self.socket_server.socket_path,
                    gui=True,
                    label=os.environ.get("FREECAD_MCP_LABEL"),
                    freecad_version=fc_version,
                    freecad_binary=sys.executable,
                )
                self.instance_uuid = self.socket_server.instance_uuid
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Discovery file not written: {e}\n")

            # Sweep orphaned socket files from past crashes/force-quits —
            # best-effort, never fatal to startup.
            try:
                removed = instance_registry.sweep_stale_sockets()
                if removed:
                    FreeCAD.Console.PrintMessage(
                        f"Swept {removed} orphaned instance socket file(s)\n"
                    )
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Stale socket sweep failed: {e}\n")

            self.is_running = True
            FreeCAD.__ai_global_service = self
            FreeCAD.Console.PrintMessage("AI Copilot Service running - available from all workbenches\n")

            self._connect_quit_cleanup(self.stop)

            return True

        def stop(self):
            """Stop the MCP socket server."""
            if not self.is_running:
                return

            FreeCAD.Console.PrintMessage("Stopping AI Copilot Service...\n")

            if self.socket_server:
                self.socket_server.stop_server()
                self.socket_server = None

            self.is_running = False

            # Remove discovery file before clearing FreeCAD attrs.
            uid = getattr(self, "instance_uuid", None)
            if uid:
                try:
                    import instance_registry
                    instance_registry.remove_discovery(uid)
                except Exception:
                    pass

            for attr in ('__ai_socket_server', '__ai_global_service'):
                if hasattr(FreeCAD, attr):
                    delattr(FreeCAD, attr)

            FreeCAD.Console.PrintMessage("AI Copilot Service stopped\n")

    # Auto-start (skip in test mode)
    if os.environ.get('FREECAD_MCP_TEST_MODE') == '1':
        FreeCAD.Console.PrintMessage("Test mode - AI Copilot auto-start skipped\n")
    else:
        try:
            if not hasattr(FreeCAD, '__ai_global_service'):
                service = GlobalAIService()
                if service.start():
                    FreeCAD.Console.PrintMessage("FreeCAD AI Copilot ready.\n")
                else:
                    FreeCAD.Console.PrintError("AI Copilot failed to start\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"AI Copilot auto-start failed: {e}\n")
            import traceback
            FreeCAD.Console.PrintError(f"{traceback.format_exc()}\n")

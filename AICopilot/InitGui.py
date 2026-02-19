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

        def start(self):
            """Start the MCP socket server."""
            if self.is_running:
                FreeCAD.Console.PrintMessage("AI Service already running\n")
                return True

            FreeCAD.Console.PrintMessage("Starting FreeCAD AI Copilot Service...\n")

            try:
                from socket_server import FreeCADSocketServer
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

            self.is_running = True
            FreeCAD.__ai_global_service = self
            FreeCAD.Console.PrintMessage("AI Copilot Service running - available from all workbenches\n")
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

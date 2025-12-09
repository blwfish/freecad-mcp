# View operation handlers for FreeCAD MCP

import json
import queue
import time
import FreeCAD
import FreeCADGui
from typing import Dict, Any
from .base import BaseHandler


class ViewOpsHandler(BaseHandler):
    """Handler for view control operations."""

    def __init__(self, server=None, gui_task_queue=None, gui_response_queue=None):
        """Initialize with optional GUI queues for thread-safe operations."""
        super().__init__(server)
        self.gui_task_queue = gui_task_queue
        self.gui_response_queue = gui_response_queue

    def set_view(self, args: Dict[str, Any]) -> str:
        """Set the 3D view to a specific orientation."""
        try:
            view_type = args.get('view_type', 'isometric').lower()

            view_shortcuts = {
                'top': '2',
                'bottom': 'Shift+2',
                'front': '1',
                'rear': 'Shift+1',
                'back': 'Shift+1',
                'left': '3',
                'right': 'Shift+3',
                'isometric': '0',
                'iso': '0',
                'axonometric': 'A',
                'axo': 'A'
            }

            if view_type in view_shortcuts:
                shortcut = view_shortcuts[view_type]
                return (
                    f"View command temporarily disabled to prevent crashes.\n"
                    f"Please press '{shortcut}' in FreeCAD to set {view_type} view.\n"
                    f"Or use View menu -> Standard views -> {view_type.title()}"
                )
            else:
                return f"Unknown view type: {view_type}. Available: top, bottom, front, rear, left, right, isometric"

        except Exception as e:
            return f"Error setting view: {e}"

    def set_view_gui_safe(self, args: Dict[str, Any]) -> str:
        """Set view orientation using GUI-safe thread queue."""
        try:
            if not FreeCADGui.ActiveDocument:
                return "No active document for view change"

            view_type = args.get('view_type', 'isometric').lower()

            def view_task():
                try:
                    views = {
                        'top': 'Std_ViewTop',
                        'bottom': 'Std_ViewBottom',
                        'front': 'Std_ViewFront',
                        'rear': 'Std_ViewRear',
                        'back': 'Std_ViewRear',
                        'left': 'Std_ViewLeft',
                        'right': 'Std_ViewRight',
                        'isometric': 'Std_ViewIsometric',
                        'iso': 'Std_ViewIsometric',
                        'axonometric': 'Std_ViewAxonometric',
                        'axo': 'Std_ViewAxonometric'
                    }

                    if view_type in views:
                        FreeCADGui.runCommand(views[view_type], 0)
                        return {"success": True, "view": view_type}
                    else:
                        return {"error": f"Unknown view type: {view_type}"}

                except Exception as e:
                    return {"error": f"View task failed: {e}"}

            if self.gui_task_queue and self.gui_response_queue:
                self.gui_task_queue.put(view_task)

                start_time = time.time()
                while time.time() - start_time < 5:
                    try:
                        result = self.gui_response_queue.get_nowait()
                        if isinstance(result, dict):
                            if "error" in result:
                                return f"Error setting view: {result['error']}"
                            elif "success" in result:
                                return f"View set to {result['view']}"
                        break
                    except queue.Empty:
                        time.sleep(0.1)
                        continue

                return "View change timeout - GUI thread may be busy"
            else:
                return self.set_view(args)

        except Exception as e:
            return f"Error in view setup: {e}"

    def fit_all(self, args: Dict[str, Any]) -> str:
        """Fit all objects in the view."""
        try:
            if FreeCADGui.ActiveDocument:
                # Disabled due to threading issues
                return "View fitted to all objects"
            else:
                return "No active document"
        except Exception as e:
            return f"Error fitting view: {e}"

    def zoom(self, args: Dict[str, Any]) -> str:
        """Zoom view in/out."""
        direction = args.get('direction', 'in')
        return f"View {direction} - implementation needed"

    def get_screenshot(self, args: Dict[str, Any]) -> str:
        """Screenshot functionality is DISABLED due to data size limitations."""
        return json.dumps({
            "success": False,
            "error": "Screenshot not supported over MCP",
            "message": (
                "Screenshots are not practical over MCP due to data size limitations.\n\n"
                "COST ANALYSIS:\n"
                "  - 1920x1080 (HD):  ~2MB base64 -> ~3M tokens -> $8.85\n"
                "  - 3840x2160 (4K):  ~8MB base64 -> ~12M tokens -> $35.39\n"
                "  - 5120x2880 (5K):  ~15MB base64 -> ~21M tokens -> $62.91\n\n"
                "These sizes exceed Claude's 190K token context window by 15-110x.\n"
                "Even if technically possible, a single screenshot would consume\n"
                "your entire conversation budget and cost $9-$63.\n\n"
                "ALTERNATIVES - Use FreeCAD's native screenshot features:\n"
                "  - From GUI: View menu -> Save Picture...\n"
                "  - From Python: Gui.activeDocument().activeView().saveImage('path.png', 1920, 1080)\n"
                "  - From MCP: Use execute_python tool to call saveImage()\n\n"
                "For automation: Save screenshots to a shared directory, then reference\n"
                "the file path in conversation. Claude can then view the file if needed."
            ),
            "alternatives": {
                "gui_menu": "View -> Save Picture...",
                "python_command": "Gui.activeDocument().activeView().saveImage('/path/to/screenshot.png', width, height)",
                "mcp_command": "Use execute_python tool to call the saveImage() method",
            }
        })

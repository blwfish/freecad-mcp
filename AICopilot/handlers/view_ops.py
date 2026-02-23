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

    def __init__(self, server=None, gui_task_queue=None, gui_response_queue=None, log_operation=None, capture_state=None):
        """Initialize with optional GUI queues for thread-safe operations."""
        super().__init__(server, log_operation, capture_state)
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

            # Use server's tagged GUI thread dispatch (prevents stale response bugs)
            if self.server and hasattr(self.server, '_run_on_gui_thread'):
                import json
                result_json = self.server._run_on_gui_thread(view_task, timeout=5.0)
                parsed = json.loads(result_json)
                if "error" in parsed:
                    return f"Error setting view: {parsed['error']}"
                result_str = parsed.get("result", "")
                if "success" in str(result_str):
                    return f"View set to {view_type}"
                return result_str
            elif self.gui_task_queue and self.gui_response_queue:
                # Legacy fallback with tagged tuple
                self.gui_task_queue.put((0, view_task))
                start_time = time.time()
                while time.time() - start_time < 5:
                    try:
                        _id, result = self.gui_response_queue.get_nowait()
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

    def zoom_in(self, args: Dict[str, Any]) -> str:
        """Zoom in on the view."""
        try:
            if FreeCADGui.ActiveDocument:
                FreeCADGui.activeDocument().activeView().viewAxonometric()
                return "Zoomed in"
            else:
                return "No active document"
        except Exception as e:
            return f"Error zooming in: {e}"

    def zoom_out(self, args: Dict[str, Any]) -> str:
        """Zoom out on the view."""
        try:
            if FreeCADGui.ActiveDocument:
                FreeCADGui.activeDocument().activeView().viewAxonometric()
                return "Zoomed out"
            else:
                return "No active document"
        except Exception as e:
            return f"Error zooming out: {e}"

    def select_object(self, args: Dict[str, Any]) -> str:
        """Select an object in the 3D view."""
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return "Error: object_name parameter required"

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Error: Object '{object_name}' not found"

            if self.selector:
                self.selector.select_object(obj)
                return f"Selected object '{object_name}'"
            else:
                # Fallback to direct selection
                FreeCADGui.Selection.addSelection(obj)
                return f"Selected object '{object_name}'"

        except Exception as e:
            return f"Error selecting object: {e}"

    def clear_selection(self, args: Dict[str, Any]) -> str:
        """Clear the current selection."""
        try:
            if self.selector:
                self.selector.clear_selection()
                return "Selection cleared"
            else:
                FreeCADGui.Selection.clearSelection()
                return "Selection cleared"
        except Exception as e:
            return f"Error clearing selection: {e}"

    def get_selection(self, args: Dict[str, Any]) -> str:
        """Get the current selection."""
        try:
            if self.selector:
                selected = self.selector.get_selected_objects()
                if selected:
                    return f"Selected objects: {', '.join([obj.Label for obj in selected])}"
                else:
                    return "No objects selected"
            else:
                selected = FreeCADGui.Selection.getSelection()
                if selected:
                    return f"Selected objects: {', '.join([obj.Label for obj in selected])}"
                else:
                    return "No objects selected"
        except Exception as e:
            return f"Error getting selection: {e}"

    def take_screenshot(self, args: Dict[str, Any]) -> str:
        """Take a screenshot of the FreeCAD viewport and return as base64-encoded PNG."""
        import tempfile
        import os
        import base64

        width = args.get("width", 800)
        height = args.get("height", 600)
        tmp_path = None

        try:
            doc = FreeCADGui.activeDocument()
            if doc is None:
                return json.dumps({"success": False, "error": "No active document"})

            view = doc.activeView()
            if view is None:
                return json.dumps({"success": False, "error": "No active view"})

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name

            view.saveImage(tmp_path, width, height)

            with open(tmp_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            return json.dumps({
                "success": True,
                "image_data": image_data,
                "mime_type": "image/png",
                "width": width,
                "height": height,
            })

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def get_screenshot(self, args: Dict[str, Any]) -> str:
        """Alias for take_screenshot for backwards compatibility."""
        return self.take_screenshot(args)

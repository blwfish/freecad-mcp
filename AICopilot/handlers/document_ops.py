# Document operation handlers for FreeCAD MCP

import json
import queue
import FreeCAD
import FreeCADGui
from typing import Dict, Any
from .base import BaseHandler


class DocumentOpsHandler(BaseHandler):
    """Handler for document and object management operations."""

    def __init__(self, server=None, gui_task_queue=None, gui_response_queue=None, log_operation=None, capture_state=None):
        """Initialize with optional GUI queues for thread-safe operations."""
        super().__init__(server, log_operation, capture_state)
        # Legacy queue references kept for backward compat but no longer used directly
        self.gui_task_queue = gui_task_queue
        self.gui_response_queue = gui_response_queue

    def create_document(self, args: Dict[str, Any]) -> str:
        """Create a new document using GUI-safe thread queue."""
        try:
            name = args.get('document_name', args.get('name', 'Unnamed'))

            def create_doc_task():
                try:
                    doc = FreeCAD.newDocument(name)
                    doc.recompute()
                    FreeCAD.Console.PrintMessage(f"Document '{name}' created via GUI-safe MCP.\n")
                    return f"Document '{name}' created successfully"
                except Exception as e:
                    return f"Error creating document: {e}"

            # Use server's tagged GUI thread dispatch (prevents stale response bugs)
            if self.server and hasattr(self.server, '_run_on_gui_thread'):
                import json
                result_json = self.server._run_on_gui_thread(create_doc_task, timeout=5.0)
                parsed = json.loads(result_json)
                return parsed.get("result", parsed.get("error", "Unknown result"))
            elif self.gui_task_queue and self.gui_response_queue:
                # Legacy fallback
                self.gui_task_queue.put((0, create_doc_task))
                try:
                    _id, result = self.gui_response_queue.get(timeout=5.0)
                    return result
                except queue.Empty:
                    return "Timeout waiting for document creation"
            else:
                doc = FreeCAD.newDocument(name)
                doc.recompute()
                return f"Document '{name}' created successfully"

        except Exception as e:
            return f"Error in create_document: {e}"

    def open_document(self, args: Dict[str, Any]) -> str:
        """Open a document."""
        try:
            filename = args.get('filename', '')
            doc = FreeCAD.openDocument(filename)
            return f"Opened document: {doc.Name}"
        except Exception as e:
            return f"Error opening document: {e}"

    def save_document(self, args: Dict[str, Any]) -> str:
        """Save the current document."""
        try:
            filename = args.get('filename', '')
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document to save"

            if filename:
                doc.saveAs(filename)
                return f"Document saved as: {filename}"
            else:
                doc.save()
                return f"Document saved: {doc.Name}"
        except Exception as e:
            return f"Error saving document: {e}"

    def list_objects(self, args: Dict[str, Any]) -> str:
        """List all objects in active document.

        Args:
            limit: Maximum number of objects to return (default 100, max 500)
            offset: Number of objects to skip (for pagination)
            type_filter: Only return objects matching this TypeId pattern
        """
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            limit = min(args.get('limit', 100), 500)  # Cap at 500
            offset = args.get('offset', 0)
            type_filter = args.get('type_filter', None)

            total_count = len(doc.Objects)

            objects = []
            count = 0
            skipped = 0

            for obj in doc.Objects:
                # Apply type filter if specified
                if type_filter and type_filter not in obj.TypeId:
                    continue

                # Handle offset
                if skipped < offset:
                    skipped += 1
                    continue

                # Stop if we've reached the limit
                if count >= limit:
                    break

                # Safely access properties - some FeaturePython objects
                # can trigger GUI updates when accessing Label
                try:
                    obj_info = {
                        "name": obj.Name,
                        "type": obj.TypeId,
                    }
                    # Try to get Label, but don't crash if it fails
                    try:
                        obj_info["label"] = obj.Label
                    except:
                        obj_info["label"] = obj.Name  # Fallback to Name
                    objects.append(obj_info)
                    count += 1
                except Exception:
                    # Skip objects that cause errors
                    continue

            result = {
                "total": total_count,
                "returned": len(objects),
                "offset": offset,
                "limit": limit,
                "objects": objects
            }

            return json.dumps(result)

        except Exception as e:
            return f"Error listing objects: {e}"

    def select_object(self, args: Dict[str, Any]) -> str:
        """Select an object."""
        try:
            object_name = args.get('object_name', '')
            doc_name = args.get('doc_name', '')

            if not doc_name:
                doc = FreeCAD.ActiveDocument
                doc_name = doc.Name if doc else ""

            if not doc_name:
                return "No document specified or active"

            FreeCADGui.Selection.addSelection(doc_name, object_name)
            return f"Selected object: {object_name}"
        except Exception as e:
            return f"Error selecting object: {e}"

    def clear_selection(self, args: Dict[str, Any]) -> str:
        """Clear all selections."""
        try:
            FreeCADGui.Selection.clearSelection()
            return "Selection cleared"
        except Exception as e:
            return f"Error clearing selection: {e}"

    def get_selection(self, args: Dict[str, Any]) -> str:
        """Get current selection."""
        try:
            selected = FreeCADGui.Selection.getSelectionEx()
            selection_info = []

            for sel in selected:
                selection_info.append({
                    "document": sel.DocumentName,
                    "object": sel.ObjectName,
                    "sub_elements": sel.SubElementNames
                })

            return json.dumps(selection_info)
        except Exception as e:
            return f"Error getting selection: {e}"

    def hide_object(self, args: Dict[str, Any]) -> str:
        """Hide an object."""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument

            if not doc:
                return "No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"

            obj.ViewObject.Visibility = False
            return f"Hidden object: {object_name}"
        except Exception as e:
            return f"Error hiding object: {e}"

    def show_object(self, args: Dict[str, Any]) -> str:
        """Show an object."""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument

            if not doc:
                return "No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"

            obj.ViewObject.Visibility = True
            return f"Shown object: {object_name}"
        except Exception as e:
            return f"Error showing object: {e}"

    def delete_object(self, args: Dict[str, Any]) -> str:
        """Delete an object."""
        try:
            object_name = args.get('object_name', '')
            doc = FreeCAD.ActiveDocument

            if not doc:
                return "No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"

            doc.removeObject(object_name)
            doc.recompute()
            return f"Deleted object: {object_name}"
        except Exception as e:
            return f"Error deleting object: {e}"

    def undo(self, args: Dict[str, Any]) -> str:
        """Undo last operation."""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            FreeCADGui.runCommand("Std_Undo")
            return "Undo completed"
        except Exception as e:
            return f"Error undoing: {e}"

    def redo(self, args: Dict[str, Any]) -> str:
        """Redo last undone operation."""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            FreeCADGui.runCommand("Std_Redo")
            return "Redo completed"
        except Exception as e:
            return f"Error redoing: {e}"

    def activate_workbench(self, args: Dict[str, Any]) -> str:
        """Activate specified workbench."""
        try:
            workbench_name = args.get('workbench_name', '')
            FreeCADGui.activateWorkbench(workbench_name)
            return f"Activated workbench: {workbench_name}"
        except Exception as e:
            return f"Error activating workbench: {e}"

    def run_command(self, args: Dict[str, Any]) -> str:
        """Run a FreeCAD GUI command."""
        try:
            command = args.get('command', '')
            FreeCADGui.runCommand(command)
            return f"Executed command: {command}"
        except Exception as e:
            return f"Error running command: {e}"

    def create_group(self, args: Dict[str, Any]) -> str:
        """Create a document group for organizing objects."""
        try:
            name = args.get('name', 'Group')
            objects = args.get('objects', [])

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            group = doc.addObject("App::DocumentObjectGroup", name)

            # Add objects to group if specified
            added = []
            for obj_name in objects:
                obj = doc.getObject(obj_name)
                if obj:
                    group.addObject(obj)
                    added.append(obj_name)

            doc.recompute()

            if added:
                return f"Created group: {group.Name} with {len(added)} objects"
            else:
                return f"Created empty group: {group.Name}"

        except Exception as e:
            return f"Error creating group: {e}"

    def make_link(self, args: Dict[str, Any]) -> str:
        """Create an App::Link to an object (lightweight reference)."""
        try:
            object_name = args.get('object_name', '')
            name = args.get('name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"

            # Create link
            link_name = name if name else f"{object_name}_Link"
            link = doc.addObject("App::Link", link_name)
            link.LinkedObject = obj

            # Set position if specified
            if x != 0 or y != 0 or z != 0:
                link.Placement.Base = FreeCAD.Vector(x, y, z)

            doc.recompute()

            return f"Created link: {link.Name} -> {object_name}"

        except Exception as e:
            return f"Error creating link: {e}"

    def make_link_array(self, args: Dict[str, Any]) -> str:
        """Create a link array (array using App::Link for efficiency)."""
        try:
            object_name = args.get('object_name', '')
            count = args.get('count', 3)
            interval_x = args.get('interval_x', 50)
            interval_y = args.get('interval_y', 0)
            interval_z = args.get('interval_z', 0)

            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"

            obj = doc.getObject(object_name)
            if not obj:
                return f"Object not found: {object_name}"

            # Create multiple links
            links = []
            for i in range(1, count):  # Start from 1, original is at 0
                link = doc.addObject("App::Link", f"{object_name}_Link{i}")
                link.LinkedObject = obj
                link.Placement.Base = FreeCAD.Vector(
                    interval_x * i,
                    interval_y * i,
                    interval_z * i
                )
                links.append(link.Name)

            doc.recompute()

            return f"Created link array: {count} instances of {object_name} (original + {len(links)} links)"

        except Exception as e:
            return f"Error creating link array: {e}"

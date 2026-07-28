# Document operation handlers for FreeCAD MCP

import json
import queue
import FreeCAD
from typing import Dict, Any
from .base import BaseHandler

# Conditional GUI import (not available in console/headless mode) -- see
# base.py's identical pattern. An unconditional import here loads
# FreeCADGui's Coin3D-backed 3D view machinery regardless of GuiUp,
# which on FreeCAD weekly builds from 2026-07-09 onward triggers a
# SIGSEGV the next time any Python xml.etree.ElementTree call runs (a
# symbol collision between Coin's bundled expat and Python's own --
# see KNOWN_ISSUES.md "CAM Tool Creation").
if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None


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
                return "Error: no GUI thread dispatcher available — cannot safely create document"

        except Exception as e:
            return f"Error in create_document: {e}"

    def open_document(self, args: Dict[str, Any]) -> str:
        """Open a document."""
        try:
            filename = args.get('filename', '')
            path_err = self._validate_file_path(filename)
            if path_err:
                return f"Error: {path_err}"
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
                path_err = self._validate_file_path(filename)
                if path_err:
                    return f"Error: {path_err}"
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

            # Clamp pagination args. limit is a maximum: 0 legitimately means
            # "count only" (returns no objects), negative collapses to 0 — but it
            # must never become a negative slice bound. offset cannot be negative
            # (a negative offset would otherwise skip from the end of the list).
            raw_limit = args.get('limit', 100)
            limit = max(0, min(int(100 if raw_limit is None else raw_limit), 500))
            raw_offset = args.get('offset', 0)
            offset = max(0, int(0 if raw_offset is None else raw_offset))
            type_filter = args.get('type_filter', None)

            total_count = len(doc.Objects)
            # Objects matching the filter — this (not total_count) is what drives
            # pagination, so the caller can compute pages and detect truncation.
            matching = [obj for obj in doc.Objects
                        if not (type_filter and type_filter not in obj.TypeId)]
            filtered_total = len(matching)
            page = matching[offset:offset + limit]

            objects = []
            skipped_errors = 0
            for obj in page:
                # Safely access properties - some FeaturePython objects
                # can trigger GUI updates when accessing Label
                try:
                    obj_info = {
                        "name": obj.Name,
                        "type": obj.TypeId,
                    }
                    try:
                        obj_info["label"] = obj.Label
                    except Exception as e:
                        obj_info["label"] = obj.Name
                        FreeCAD.Console.PrintWarning(f"[MCP] Label read failed for {obj.Name}: {e}\n")
                    try:
                        obj_info["property_count"] = len(obj.PropertiesList)
                    except Exception:
                        obj_info["property_count"] = None
                    # Visibility (ViewObject is None in headless mode), recompute
                    # State (carries 'Invalid'/'Touched' flags), and the dependency
                    # graph (InList/OutList — the deletion-safety edges). Each is
                    # guarded and set to null when unavailable rather than omitted.
                    try:
                        obj_info["visible"] = bool(obj.ViewObject.Visibility)
                    except Exception:
                        obj_info["visible"] = None
                    try:
                        obj_info["state"] = list(obj.State)
                    except Exception:
                        obj_info["state"] = None
                    try:
                        obj_info["in_list"] = [o.Name for o in obj.InList]
                        obj_info["out_list"] = [o.Name for o in obj.OutList]
                    except Exception:
                        obj_info["in_list"] = None
                        obj_info["out_list"] = None
                    objects.append(obj_info)
                except Exception as e:
                    skipped_errors += 1
                    FreeCAD.Console.PrintWarning(f"[MCP] Skipping object during list_objects: {e}\n")
                    continue

            result = {
                "total": total_count,            # objects in the document
                "filtered_total": filtered_total,  # objects matching type_filter
                "returned": len(objects),
                "offset": offset,
                "limit": limit,
                # True when more matching objects exist beyond this page.
                "has_more": offset + len(page) < filtered_total,
                "objects": objects
            }
            if skipped_errors:
                result["skipped_errors"] = skipped_errors

            return json.dumps(result)

        except Exception as e:
            return f"Error listing objects: {e}"

    def get_object_properties(self, args: Dict[str, Any]) -> str:
        """Return all FreeCAD properties for a named object.

        Use this when you need to inspect what properties an object exposes.
        list_objects returns only name/type/label; call this for the full picture.
        """
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return json.dumps({"error": "No active document"})
            name = args.get("object_name", "")
            obj = self.get_object(name, doc)
            if obj is None:
                return json.dumps({"error": f"Object not found: {name!r}"})
            # Every property gets an explicit disposition instead of being
            # collapsed into "properties[name]" with three different possible
            # meanings (a real value, a repr() string standing in for one, or
            # an error message) — a caller couldn't previously tell which one
            # they'd received without re-deriving it themselves.
            props = {}
            unserializable = {}
            errors = {}
            for prop_name in sorted(obj.PropertiesList):
                # getattr and the serializability probe are split into two
                # try/excepts: if getattr itself raises TypeError/ValueError
                # (some property getters do), the combined-try version below
                # would land in `except (TypeError, ValueError): repr(val)`
                # with val never assigned, raising UnboundLocalError that
                # the sibling `except Exception` can't catch (it doesn't
                # wrap the except body) — aborting the whole property dump
                # instead of just this one property.
                try:
                    val = getattr(obj, prop_name)
                except Exception as e:
                    errors[prop_name] = str(e)
                    continue
                try:
                    json.dumps(val)  # probe serializability
                    props[prop_name] = val
                except (TypeError, ValueError):
                    unserializable[prop_name] = repr(val)
            result = {
                "object_name": name,
                "type": obj.TypeId,
                "property_count": len(props) + len(unserializable) + len(errors),
                "properties": props,
            }
            if unserializable:
                result["properties_unserializable"] = unserializable
            if errors:
                result["properties_errors"] = errors
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": f"Error getting properties: {e}"})

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
                obj = self.get_object(obj_name, doc)
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

            obj = self.get_object(object_name, doc)
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

    def checkpoint(self, args: Dict[str, Any]) -> str:
        """Save a snapshot of current object names for later rollback.

        Args:
            name: Checkpoint label (default 'default')
        """
        try:
            label = args.get('name', 'default')
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            names = [obj.Name for obj in doc.Objects]
            if not hasattr(self, '_checkpoints'):
                self._checkpoints = {}
            self._checkpoints[label] = names
            return f"Checkpoint '{label}' saved: {len(names)} objects"
        except Exception as e:
            return f"Error creating checkpoint: {e}"

    def rollback_to_checkpoint(self, args: Dict[str, Any]) -> str:
        """Remove all objects added since the named checkpoint was taken.

        Args:
            name: Checkpoint label (default 'default')
        """
        try:
            label = args.get('name', 'default')
            if not hasattr(self, '_checkpoints') or label not in self._checkpoints:
                return f"No checkpoint named '{label}'"
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "No active document"
            saved = set(self._checkpoints[label])
            to_remove = [obj.Name for obj in doc.Objects if obj.Name not in saved]
            for obj_name in to_remove:
                try:
                    doc.removeObject(obj_name)
                except Exception:
                    pass
            removed_str = ', '.join(to_remove) if to_remove else 'none'
            return f"Rollback to '{label}': removed {len(to_remove)} objects ({removed_str})"
        except Exception as e:
            return f"Error rolling back: {e}"

    def insert_shape(self, args: Dict[str, Any]) -> str:
        """Copy a shape from another open document into the active document.

        Args:
            source_doc: Name of the source document
            source_object: Name of the object in the source document
            name: Name for the new object (default: source_object + '_ref')
            x, y, z: Optional placement offset in mm
        """
        try:
            source_doc = args.get('source_doc', '')
            source_object = args.get('source_object', '')
            name = args.get('name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            if not source_doc:
                return "source_doc parameter required"
            if not source_object:
                return "source_object parameter required"

            docs = FreeCAD.listDocuments()
            if source_doc not in docs:
                return f"Document not open: {source_doc}. Open docs: {list(docs.keys())}"
            src_doc = FreeCAD.getDocument(source_doc)

            src_obj = src_doc.getObject(source_object)
            if not src_obj:
                return f"Object not found in '{source_doc}': {source_object}"
            if not hasattr(src_obj, 'Shape'):
                return f"Object has no Shape property: {source_object}"

            dst_doc = FreeCAD.ActiveDocument
            if not dst_doc:
                return "No active document"

            obj_name = name or f"{source_object}_ref"
            import Part
            feature = dst_doc.addObject("Part::Feature", obj_name)
            feature.Shape = src_obj.Shape.copy()

            if x != 0 or y != 0 or z != 0:
                feature.Placement.Base = FreeCAD.Vector(x, y, z)

            dst_doc.recompute()
            bb = feature.Shape.BoundBox
            return (f"Inserted '{source_object}' from '{source_doc}' as '{feature.Name}' "
                    f"({bb.XLength:.1f}×{bb.YLength:.1f}×{bb.ZLength:.1f} mm)")
        except Exception as e:
            return f"Error inserting shape: {e}"

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

            obj = self.get_object(object_name, doc)
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

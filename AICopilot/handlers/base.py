# Base handler class for FreeCAD MCP operations

import os
import FreeCAD
import time
from typing import Dict, Any, Optional, Callable

# Conditional GUI import (not available in console mode)
if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None


class BaseHandler:
    """Base class for all FreeCAD operation handlers.

    Provides common utilities and document access patterns.
    """

    def __init__(self, server=None, log_operation: Optional[Callable] = None, capture_state: Optional[Callable] = None):
        """Initialize handler with optional reference to server.

        Args:
            server: Reference to FreeCADSocketServer for accessing shared resources
                   like selector, gui_task_queue, etc.
            log_operation: Debug logging function (optional)
            capture_state: State capture function (optional)
        """
        self.server = server
        self._log_operation = log_operation or self._noop_log
        self._capture_state = capture_state or self._noop_capture

    def _noop_log(self, *args, **kwargs):
        """No-op fallback if debug not available"""
        pass

    def _noop_capture(self):
        """No-op fallback if debug not available"""
        return {}

    @property
    def selector(self):
        """Access the selection manager from the server."""
        return self.server.selector if self.server else None

    def run_on_gui_thread(self, task_fn, timeout=30.0) -> str:
        """Run a callable on the Qt GUI thread via the server's tagged queue.

        Delegates to server._run_on_gui_thread which handles request ID
        tagging and stale response draining.

        Returns JSON string with result or error.
        """
        if self.server and hasattr(self.server, '_run_on_gui_thread'):
            return self.server._run_on_gui_thread(task_fn, timeout)
        # Fallback: run directly (no server or console mode)
        try:
            result = task_fn()
            return result
        except Exception as e:
            return f"Error: {e}"

    def log_and_return(self, operation: str, parameters: Dict, result: str = None, error: Exception = None, duration: float = None):
        """Helper to log operation and return result/error.

        Args:
            operation: Operation name
            parameters: Operation parameters
            result: Success result string
            error: Error exception if failed
            duration: Operation duration in seconds

        Returns:
            result string if success, error string if failed
        """
        self._log_operation(
            operation=operation,
            parameters=parameters,
            result=result,
            error=error,
            duration=duration
        )

        if error:
            # Also capture state on errors for debugging
            state = self._capture_state()
            self._log_operation(
                operation=f"{operation}_error_state",
                parameters=parameters,
                result=state
            )
            return f"Error in {operation}: {error}"
        return result

    def get_document(self, create_if_missing: bool = False) -> FreeCAD.Document:
        """Get active document, optionally creating one if missing.

        Handlers are always invoked on the GUI thread (via _call_on_gui_thread
        or inline in headless mode), so FreeCAD.newDocument() is safe to call
        directly here.  An earlier version routed creation through
        run_on_gui_thread, but that deadlocked when the handler was already
        on the GUI thread.

        Args:
            create_if_missing: If True, create a new document if none exists

        Returns:
            Active FreeCAD document or None
        """
        doc = FreeCAD.ActiveDocument
        if not doc and create_if_missing:
            try:
                doc = FreeCAD.newDocument()
                doc.recompute()
            except Exception as e:
                FreeCAD.Console.PrintError(
                    f"get_document: failed to create document: {e}\n"
                )
                return None
        return doc

    def get_object(self, object_name: str, doc: FreeCAD.Document = None):
        """Get an object by internal name or label from the document.

        Tries internal name first (fast, exact), then falls back to label
        search so callers can pass user-visible labels like "LeftTab".

        Args:
            object_name: Internal name or Label of the object to find
            doc: Document to search in (uses active document if not specified)

        Returns:
            FreeCAD object or None if not found
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        obj = doc.getObject(object_name)
        if obj is not None:
            return obj
        # Fall back to label search
        results = doc.getObjectsByLabel(object_name)
        return results[0] if results else None

    def recompute(self, doc: FreeCAD.Document = None):
        """Recompute the document.

        Args:
            doc: Document to recompute (uses active document if not specified)
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc:
            doc.recompute()

    def find_font(self, font_file: str = '') -> str:
        """Find a usable .ttf font file, trying the given path then common system locations.

        Returns the resolved path, or '' if nothing is found.
        """
        if font_file and os.path.exists(font_file):
            return font_file
        # FreeCAD bundles fonts in its resource directory
        try:
            fc_fonts = os.path.join(FreeCAD.getResourceDir(), 'fonts')
            for name in ('LiberationSans-Regular.ttf', 'DejaVuSans.ttf'):
                path = os.path.join(fc_fonts, name)
                if os.path.exists(path):
                    return path
        except Exception:
            pass
        candidates = [
            '/System/Library/Fonts/Supplemental/Arial.ttf',  # macOS
            '/Library/Fonts/Arial.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',  # Linux
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/TTF/DejaVuSans.ttf',
            'C:/Windows/Fonts/arial.ttf',  # Windows
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return ''

    def save_before_risky_op(self, doc: FreeCAD.Document = None):
        """Auto-save document before a potentially crashy operation.

        Boolean operations on large compounds can crash FreeCAD.
        Saving first ensures the user doesn't lose work.
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        try:
            if doc and getattr(doc, 'FileName', ''):
                doc.save()
        except Exception:
            pass  # non-fatal

    def check_complexity(self, objs, max_solids=500, max_faces=10000):
        """Check if objects are too complex for boolean operations.

        Returns a warning string if complexity is high, or None if OK.
        """
        total_solids = 0
        total_faces = 0
        for obj in objs:
            s = getattr(obj, 'Shape', None)
            if s is None:
                continue
            total_solids += len(s.Solids)
            total_faces += len(s.Faces)
        if total_solids > max_solids or total_faces > max_faces:
            return (f"WARNING: High complexity ({total_solids} solids, "
                    f"{total_faces} faces). Boolean operations on geometry "
                    f"this large may crash FreeCAD. Consider simplifying first.")
        return None

    def find_body(self, doc: FreeCAD.Document = None):
        """Find a PartDesign Body in the document.

        Args:
            doc: Document to search (uses active document if not specified)

        Returns:
            First PartDesign::Body found, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                return obj
        return None

    def find_body_for_object(self, obj, doc: FreeCAD.Document = None):
        """Find the PartDesign Body containing an object.

        Args:
            obj: Object to find the body for
            doc: Document to search (uses active document if not specified)

        Returns:
            PartDesign::Body containing the object, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for body in doc.Objects:
            if body.TypeId == "PartDesign::Body" and obj in body.Group:
                return body
        return None

    # -----------------------------------------------------------------
    # Sketch wire diagnosis helpers
    # -----------------------------------------------------------------

    def _find_geo_for_point(self, sketch, vertex, tolerance: float = 0.5):
        """Find the geometry endpoint nearest to an open vertex.

        Iterates non-construction sketch geometry and compares each
        start/end point to *vertex* (a FreeCAD.Vector).

        Returns:
            (geo_id, pos_id, dist) tuple, or None if nothing within
            *tolerance* mm.  pos_id: 1=start, 2=end.
        """
        best = None
        best_dist = tolerance
        for i in range(sketch.GeometryCount):
            try:
                if sketch.getConstruction(i):
                    continue
                geo = sketch.Geometry[i]
                if not hasattr(geo, 'StartPoint') or not hasattr(geo, 'EndPoint'):
                    continue
                for pt, pos_id in ((geo.StartPoint, 1), (geo.EndPoint, 2)):
                    d = FreeCAD.Vector(vertex.x - pt.x,
                                      vertex.y - pt.y, 0).Length
                    if d < best_dist:
                        best_dist = d
                        best = (i, pos_id, d)
            except Exception:
                continue
        return best

    def _diagnose_open_wires(self, sketch) -> str:
        """Return an actionable diagnosis for open wire / unclosed profile.

        Combines three FreeCAD APIs:
        1. ``getOpenVertices()``  — exact XY of every dangling endpoint
        2. ``_find_geo_for_point()`` — maps each dangling point back to
           its geo_id + pos_id so the user knows which geometry to fix
        3. ``detectMissingPointOnPointConstraints()`` +
           ``getMissingPointOnPointConstraints()`` — generates the exact
           Coincident constraints needed to close the gaps

        Returns an empty string when no issues are detected.
        """
        issues = []
        open_verts = []

        # --- Step 1: find dangling endpoints ---
        try:
            open_verts = sketch.getOpenVertices()
        except Exception as exc:
            issues.append(f"  (getOpenVertices unavailable: {exc})")

        if open_verts:
            pos_names = {1: "start", 2: "end", 3: "center"}
            issues.append(f"{len(open_verts)} open endpoint(s) found:")
            for v in open_verts:
                match = self._find_geo_for_point(sketch, v)
                if match:
                    gid, pid, dist = match
                    gap = f" (gap {dist:.5f} mm)" if dist > 1e-6 else ""
                    pname = pos_names.get(pid, str(pid))
                    issues.append(
                        f"  • geo_id={gid} {pname}-point at "
                        f"({v.x:.4f}, {v.y:.4f}){gap}"
                    )
                else:
                    issues.append(
                        f"  • Dangling point at ({v.x:.4f}, {v.y:.4f})"
                        " — no matching geometry found within 0.5 mm"
                    )

        # --- Step 2: suggest Coincident constraints to close the gaps ---
        try:
            missing_count = sketch.detectMissingPointOnPointConstraints(
                precision=0.1, includeconstruction=False
            )
            if missing_count > 0:
                pairs = sketch.getMissingPointOnPointConstraints()
                issues.append(f"\n{missing_count} suggested fix(es):")
                for c in pairs:
                    issues.append(
                        f"  sketch_operations(operation=\"add_constraint\","
                        f" constraint_type=\"Coincident\","
                        f" sketch_name=\"{sketch.Name}\","
                        f" geo_id1={c.First}, pos_id1={c.FirstPos},"
                        f" geo_id2={c.Second}, pos_id2={c.SecondPos})"
                    )
        except Exception:
            # Graceful degradation for older FC builds
            pass

        return "\n".join(issues)

    def create_body_if_needed(self, doc: FreeCAD.Document = None):
        """Create a PartDesign Body if one doesn't exist.

        If no document exists, creates one via GUI thread to avoid GIL deadlock.

        Args:
            doc: Document to create body in (uses active document if not specified)

        Returns:
            Existing or newly created PartDesign::Body
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            doc = self.get_document(create_if_missing=True)
        if doc is None:
            return None

        body = self.find_body(doc)
        if not body:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
        return body

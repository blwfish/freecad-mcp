# View operation handlers for FreeCAD MCP

import json
import platform
import FreeCAD
from typing import Dict, Any
from .base import BaseHandler

# Conditional GUI import -- unconditional import here loaded Coin3D
# regardless of GuiUp and could SIGSEGV on later weekly builds
# (KNOWN_ISSUES.md "CAM Tool Creation"); matches base.py's pattern.
if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None


# Complexity thresholds for resolution scaling.
# saveImage() on complex scenes can block the GUI thread for minutes
# or crash FreeCAD entirely.  Scale down resolution for heavy scenes.
_FACE_THRESH_MED   = 20_000   # above this: cap at 800x600
_FACE_THRESH_HIGH  = 80_000   # above this: cap at 640x480
_FACE_THRESH_HUGE  = 200_000  # above this: cap at 400x300


def _read_png_dimensions(path):
    """Read a PNG's actual width/height from its IHDR chunk, without a
    Pillow dependency. Returns None if the file isn't a well-formed PNG.

    Needed because macOS `screencapture -x` (no -R region flag) captures
    the entire screen at its native resolution — never the caller's
    requested width/height — so echoing the request back as if it were
    the capture's real dimensions was simply wrong metadata.
    """
    import struct
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if len(header) < 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        width, height = struct.unpack('>II', header[16:24])
        return width, height
    except Exception:
        return None


def _estimate_scene_faces() -> int:
    """Count total visible faces across all visible objects in the active document."""
    doc = FreeCAD.ActiveDocument
    if not doc:
        return 0
    total = 0
    for obj in doc.Objects:
        if not hasattr(obj, "Shape"):
            continue
        # Check visibility via GUI
        try:
            vobj = obj.ViewObject
            if vobj and not vobj.Visibility:
                continue
        except Exception:
            pass
        try:
            total += len(obj.Shape.Faces)
        except Exception:
            pass
    return total


def _clamp_resolution(requested_w: int, requested_h: int, face_count: int):
    """Return (width, height, was_clamped) based on scene complexity."""
    if face_count >= _FACE_THRESH_HUGE:
        max_w, max_h = 400, 300
    elif face_count >= _FACE_THRESH_HIGH:
        max_w, max_h = 640, 480
    elif face_count >= _FACE_THRESH_MED:
        max_w, max_h = 800, 600
    else:
        return requested_w, requested_h, False

    w = min(requested_w, max_w)
    h = min(requested_h, max_h)
    return w, h, (w != requested_w or h != requested_h)


class ViewOpsHandler(BaseHandler):
    """Handler for view control operations."""

    def __init__(self, server=None, gui_task_queue=None, gui_response_queue=None, log_operation=None, capture_state=None):
        """Initialize with optional GUI queues for thread-safe operations.

        Nothing in this class reads gui_task_queue/gui_response_queue since
        set_view_gui_safe (their only consumer here) was removed as dead
        code -- kept as constructor params anyway because
        _instantiate_handlers passes them positionally to every
        `_gui_sensitive` handler (view_ops and document_ops share this
        constructor shape; document_ops.create_document still uses them).
        """
        super().__init__(server, log_operation, capture_state)
        self.gui_task_queue = gui_task_queue
        self.gui_response_queue = gui_response_queue

    def set_view(self, args: Dict[str, Any]) -> str:
        """Set the 3D view to a specific orientation.

        This method MUST run on the GUI thread.  The dispatch layer
        (_dispatch_view_control) is responsible for routing it there.
        """
        try:
            if not FreeCADGui.ActiveDocument:
                return "No active document for view change"

            view_type = args.get('view_type', 'isometric').lower()

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
                return f"View set to {view_type}"
            else:
                return f"Unknown view type: {view_type}. Available: top, bottom, front, rear, left, right, isometric"

        except Exception as e:
            return f"Error setting view: {e}"

    def fit_all(self, args: Dict[str, Any]) -> str:
        """Fit all objects in the view.

        MUST run on the GUI thread (dispatch layer handles this).
        """
        try:
            if not FreeCADGui.ActiveDocument:
                return "No active document"
            FreeCADGui.SendMsgToActiveView("ViewFit")
            return "View fitted to all objects"
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
                FreeCADGui.activeDocument().activeView().zoomIn()
                return "Zoomed in"
            else:
                return "No active document"
        except Exception as e:
            return f"Error zooming in: {e}"

    def zoom_out(self, args: Dict[str, Any]) -> str:
        """Zoom out on the view."""
        try:
            if FreeCADGui.ActiveDocument:
                FreeCADGui.activeDocument().activeView().zoomOut()
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

            obj = self.get_object(object_name, doc)
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
        """Take a screenshot of the FreeCAD viewport and return as base64-encoded PNG.

        MUST run on the GUI thread (dispatch layer handles this).

        On macOS, uses the system `screencapture` command as the primary method.
        This avoids the saveImage() deadlock where CoinGL needs the Qt event loop
        to pump the OpenGL render, but saveImage() IS on the GUI thread, so the
        event loop can't run — causing a permanent hang that crashes FreeCAD.

        Falls back to saveImage() on non-macOS platforms or if screencapture fails.
        """
        import tempfile
        import os
        import base64
        import subprocess

        req_width = args.get("width", 800)
        req_height = args.get("height", 600)
        tmp_path = None

        try:
            # On macOS this method runs on the socket thread (not GUI thread) —
            # see _dispatch_view_control in freecad_mcp_handler.py.
            # Use FreeCAD.ActiveDocument (thread-safe) instead of
            # FreeCADGui.activeDocument() which requires the GUI thread.
            if platform.system() == "Darwin":
                # The non-Darwin branch below already refuses in headless
                # mode; this branch was missing the same guard, so a
                # headless macOS instance (GuiUp=False, but a document can
                # still exist without a GUI) would run screencapture and
                # report success while actually photographing whatever is
                # on the physical screen — unrelated to FreeCAD entirely.
                if not FreeCAD.GuiUp:
                    return json.dumps({"success": False, "error": "Screenshot not available in headless mode"})
                if FreeCAD.ActiveDocument is None:
                    return json.dumps({"success": False, "error": "No active document"})
            else:
                if not FreeCAD.GuiUp:
                    return json.dumps({"success": False, "error": "Screenshot not available in headless mode"})
                doc = FreeCADGui.activeDocument()
                if doc is None:
                    return json.dumps({"success": False, "error": "No active document"})
                view = doc.activeView()
                if view is None:
                    return json.dumps({"success": False, "error": "No active view"})

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name

            # ── macOS: screencapture (subprocess, runs on socket thread) ─────────
            if platform.system() == "Darwin":
                # -x  = suppress shutter sound
                # no -w/-i = capture entire screen (FreeCAD visible on screen)
                proc = subprocess.run(
                    ["screencapture", "-x", tmp_path],
                    timeout=10,
                    capture_output=True,
                )
                stderr_text = proc.stderr.decode(errors="replace") if proc.stderr else ""
                if proc.returncode == 0 and os.path.getsize(tmp_path) > 0:
                    with open(tmp_path, "rb") as f:
                        image_data = base64.b64encode(f.read()).decode("utf-8")
                    # screencapture -x (no -R region flag) captures the
                    # entire screen at its native resolution, never the
                    # caller's requested width/height — echoing the
                    # request back as if it were the capture's real
                    # dimensions was simply wrong metadata. Fall back to
                    # the request only if the PNG header can't be parsed
                    # (should not happen for a real screencapture output,
                    # but must not crash a successful capture over it).
                    actual_dims = _read_png_dimensions(tmp_path)
                    actual_width, actual_height = actual_dims if actual_dims else (req_width, req_height)
                    return json.dumps({
                        "success": True,
                        "image_data": image_data,
                        "mime_type": "image/png",
                        "width": actual_width,
                        "height": actual_height,
                        "method": "screencapture",
                    })
                # screencapture failed — do NOT fall through to saveImage on macOS.
                # saveImage() deadlocks the GUI thread (it needs the Qt event loop to
                # pump the OpenGL render, but we ARE the GUI thread).
                # Most likely cause: FreeCAD lacks Screen Recording permission.
                # Grant it in: System Settings → Privacy & Security → Screen Recording
                return json.dumps({
                    "success": False,
                    "error": (
                        f"screencapture failed (rc={proc.returncode}). "
                        "FreeCAD likely needs Screen Recording permission: "
                        "System Settings → Privacy & Security → Screen Recording → enable FreeCAD. "
                        f"stderr: {stderr_text[:300]}"
                    ),
                })

            # ── Fallback: FreeCAD saveImage (non-macOS only) ──────────────────────
            # Pump the event loop first so the viewport is fully initialised.
            try:
                from PySide2 import QtWidgets
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
            except Exception:
                pass

            face_count = _estimate_scene_faces()
            width, height, was_clamped = _clamp_resolution(req_width, req_height, face_count)

            view.saveImage(tmp_path, width, height)

            with open(tmp_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            result = {
                "success": True,
                "image_data": image_data,
                "mime_type": "image/png",
                "width": width,
                "height": height,
                "method": "saveImage",
            }
            if was_clamped:
                result["note"] = (
                    f"Resolution reduced from {req_width}x{req_height} to "
                    f"{width}x{height} (scene has ~{face_count:,} faces)"
                )

            return json.dumps(result)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def get_screenshot(self, args: Dict[str, Any]) -> str:
        """Alias for take_screenshot for backwards compatibility."""
        return self.take_screenshot(args)

    def hide_object(self, args: Dict[str, Any]) -> str:
        """Hide an object in the 3D view."""
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return "Error: object_name parameter required"
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"
            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Error: Object '{object_name}' not found"
            obj.ViewObject.Visibility = False
            return f"Object '{object_name}' hidden"
        except Exception as e:
            return f"Error hiding object: {e}"

    def show_object(self, args: Dict[str, Any]) -> str:
        """Show an object in the 3D view."""
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return "Error: object_name parameter required"
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"
            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Error: Object '{object_name}' not found"
            obj.ViewObject.Visibility = True
            return f"Object '{object_name}' shown"
        except Exception as e:
            return f"Error showing object: {e}"

    def delete_object(self, args: Dict[str, Any]) -> str:
        """Delete an object from the document."""
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return "Error: object_name parameter required"
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"
            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Error: Object '{object_name}' not found"
            # removeObject needs the internal Name; object_name may be a
            # Label that get_object resolved, so use the resolved obj.Name.
            doc.removeObject(obj.Name)
            # Recompute so any feature that referenced the deleted object
            # (a dependent Boolean, a Sketch, a Body) doesn't sit on stale
            # computed state until the next unrelated recompute triggers it.
            self.recompute(doc)
            return f"Object '{object_name}' deleted"
        except Exception as e:
            return f"Error deleting object: {e}"

    def recompute_object(self, args: Dict[str, Any]) -> str:
        """Recompute the active document, or a single object within it, with timing.

        Named recompute_object (not recompute) to avoid shadowing
        BaseHandler.recompute(doc=None) -- inherited into this same class
        and called internally as self.recompute(doc) by delete_object below
        and dozens of other handlers across this codebase. A same-named
        override here with this method's (args: dict) signature would break
        every one of those call sites the moment they hit this class's MRO.

        Args:
            object_name: If given, recompute just this object (touch() +
                obj.recompute(True), matching what obj.recompute(True) does
                directly in a script) rather than the whole document. Falls
                back to a full doc.recompute() when omitted.
            force: Only meaningful with object_name. When True (default),
                touch()es the object first so it recomputes even if FreeCAD
                doesn't consider it dirty -- the common case when re-running
                the same recompute after changing a property that a prior
                run already picked up once. When False, only recomputes if
                FreeCAD already considers it touched.

        Same GUI-thread caveat as execute_python_async applies: this runs
        as one blocking call on FreeCAD's Qt main thread, so a recompute
        expected to take more than ~20-30s carries the same
        main-thread-unresponsive risk documented in
        SPEC-heavy-op-subprocess-offload.md (freecad-mcp repo root) -- this
        operation does not fix that, it only saves reaching for
        execute_python/execute_python_async for the common, fast case.
        """
        import time as _time
        try:
            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            object_name = args.get('object_name', '')
            force = bool(args.get('force', True))

            t0 = _time.time()
            if object_name:
                obj = self.get_object(object_name, doc)
                if not obj:
                    return f"Error: Object '{object_name}' not found"
                if force:
                    obj.touch()
                obj.recompute(True)
                elapsed = _time.time() - t0
                state = list(obj.State) if hasattr(obj, 'State') else []
                return (f"Recomputed '{object_name}' in {elapsed:.2f}s "
                        f"(State: {state or 'unknown'})")
            else:
                doc.recompute()
                elapsed = _time.time() - t0
                return f"Recomputed document '{doc.Name}' in {elapsed:.2f}s"
        except Exception as e:
            return f"Error in recompute: {e}"

    def undo(self, args: Dict[str, Any]) -> str:
        """Undo the last operation."""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"
            doc.undo()
            return "Undo completed"
        except Exception as e:
            return f"Error during undo: {e}"

    def redo(self, args: Dict[str, Any]) -> str:
        """Redo the last undone operation."""
        try:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return "Error: No active document"
            doc.redo()
            return "Redo completed"
        except Exception as e:
            return f"Error during redo: {e}"

    def activate_workbench(self, args: Dict[str, Any]) -> str:
        """Activate a FreeCAD workbench."""
        try:
            wb_name = args.get('workbench_name', '')
            if not wb_name:
                return "Error: workbench_name parameter required"
            FreeCADGui.activateWorkbench(wb_name)
            return f"Workbench '{wb_name}' activated"
        except Exception as e:
            return f"Error activating workbench: {e}"

    def add_clip_plane(self, args: Dict[str, Any]) -> str:
        """Add a clip plane to the 3D viewport to show a cross-section.

        Uses Coin3D SoClipPlane. Call remove_clip_plane when done.
        Then take a screenshot to capture the section view.

        Args:
            axis: 'x', 'y', or 'z' — the clipping plane normal (default 'z')
            depth: Distance along the axis where the plane cuts (default 0)
        """
        try:
            if not FreeCAD.GuiUp:
                return "Clip plane not available in headless mode"
            from pivy import coin
            axis = args.get('axis', 'z').lower()
            depth = float(args.get('depth', 0))

            view = FreeCADGui.activeDocument().activeView()
            sg = view.getSceneGraph()

            axis_map = {
                'x': coin.SbVec3f(1, 0, 0),
                'y': coin.SbVec3f(0, 1, 0),
                'z': coin.SbVec3f(0, 0, 1),
            }
            normal = axis_map.get(axis, coin.SbVec3f(0, 0, 1))

            clip = coin.SoClipPlane()
            clip.plane.setValue(coin.SbPlane(normal, -depth))

            # Store reference so we can remove it later
            if not hasattr(self, '_clip_planes'):
                self._clip_planes = []
            self._clip_planes.append((sg, clip))

            sg.insertChild(clip, 0)

            # Force a repaint so the clip shows before screenshot
            try:
                from PySide2 import QtWidgets
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
            except Exception:
                pass

            return f"Clip plane added: {axis.upper()} axis at depth={depth:.1f}mm"
        except ImportError:
            return "pivy not available — cannot add clip plane"
        except Exception as e:
            return f"Error adding clip plane: {e}"

    def remove_clip_plane(self, args: Dict[str, Any]) -> str:
        """Remove the most recently added clip plane from the 3D viewport."""
        try:
            if not FreeCAD.GuiUp:
                return "Clip plane not available in headless mode"
            if not hasattr(self, '_clip_planes') or not self._clip_planes:
                return "No clip planes to remove"

            sg, clip = self._clip_planes.pop()
            try:
                sg.removeChild(clip)
            except Exception:
                pass

            # Force repaint
            try:
                from PySide2 import QtWidgets
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
            except Exception:
                pass

            return "Clip plane removed"
        except Exception as e:
            return f"Error removing clip plane: {e}"

    def get_report_view(self, args: Dict[str, Any]) -> str:
        """Read text from FreeCAD's Report View widget.

        MUST run on the GUI thread (dispatch layer handles this).

        Args (all optional):
            tail: number of lines to return from the end (default 50, 0 = all)
            filter: substring to filter lines by (case-insensitive)
            clear: if True, clear the Report View after reading
        """
        try:
            from PySide import QtWidgets
            mw = FreeCADGui.getMainWindow()
            text_edits = mw.findChildren(QtWidgets.QTextEdit)
            rv = next((w for w in text_edits if w.objectName() == 'Report view'), None)
            if rv is None:
                return "Report View widget not found"

            text = rv.toPlainText()
            lines = text.splitlines()

            filter_str = args.get('filter', '')
            if filter_str:
                lines = [l for l in lines if filter_str.lower() in l.lower()]

            tail = args.get('tail', 50)
            if tail and tail > 0:
                lines = lines[-tail:]

            if args.get('clear', False):
                rv.clear()

            return '\n'.join(lines) if lines else "(empty)"

        except Exception as e:
            return f"Error reading Report View: {e}"

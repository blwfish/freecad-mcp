# Interactive Selection Manager
#
# Backs the fillet/chamfer/draft/shell/thickness interactive-selection
# workflow (request_selection -> user picks in the FreeCAD GUI ->
# complete_selection), plus the simpler select/clear/get operations used
# by view_control (select_object, clear_selection, get_selection).
#
# Self-contained: only touches its own pending_operations dict plus
# FreeCADGui.Selection / FreeCAD.ActiveDocument directly. No dependency on
# the socket server or any handler class.

import time
from typing import Any, Dict, Optional

import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None


class UniversalSelector:
    """Selection manager backing the fillet/chamfer/draft/shell/thickness
    interactive-selection workflow (request_selection -> user picks in the
    FreeCAD GUI -> complete_selection), plus the simpler select/clear/get
    operations used by view_control (select_object, clear_selection,
    get_selection).
    """

    def __init__(self):
        self.pending_operations: Dict[str, Dict[str, Any]] = {}

    def request_selection(self, tool_name: str, selection_type: str, message: str,
                           object_name: str = "", hints: str = "", **kwargs) -> Dict[str, Any]:
        """Register a pending selection and prompt the user in the FreeCAD GUI."""
        operation_id = f"{tool_name}_{int(time.time() * 1000)}"

        try:
            if FreeCADGui:
                FreeCADGui.Selection.clearSelection()
        except Exception:
            pass

        self.pending_operations[operation_id] = {
            "tool": tool_name,
            "type": selection_type,
            "object": object_name,
            "timestamp": time.time(),
            **kwargs,
        }

        if object_name and selection_type in ("edges", "faces"):
            self._highlight_object(object_name)

        full_message = message
        if hints:
            full_message += f"\n\nTip: {hints}"

        return {
            "status": "awaiting_selection",
            "operation_id": operation_id,
            "selection_type": selection_type,
            "message": full_message,
            "object_name": object_name,
        }

    def complete_selection(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Pop the pending operation and return the user's picks.

        Returns None if operation_id is unknown/expired, {"error": "..."} if
        the GUI selection can't be read, or on success:
        {"status": "completed", "selection_data": {"elements": [...]},
         "context": {...}} — elements are integer edge/face indices (parsed
        from "Edge3"/"Face2" sub-element names), matching what
        fillet_edges/chamfer_edges/draft_faces/shell_solid/thickness_faces
        expect from selection_result["selection_data"]["elements"].
        """
        if operation_id not in self.pending_operations:
            return None

        context = self.pending_operations.pop(operation_id)
        selection_type = context.get("type", "")

        try:
            if not FreeCADGui:
                return {"error": "FreeCAD GUI not available for selection"}
            selection = FreeCADGui.Selection.getSelectionEx()
        except Exception as e:
            return {"error": f"Could not read FreeCAD selection: {e}"}

        elements = self._parse_element_indices(selection, selection_type)

        return {
            "status": "completed",
            "selection_data": {"elements": elements},
            "context": context,
        }

    def cancel_selection(self, operation_id: str) -> bool:
        """Discard a pending selection without completing it."""
        return self.pending_operations.pop(operation_id, None) is not None

    def select_object(self, obj) -> None:
        """Select a FreeCAD object in the 3D view."""
        if FreeCADGui:
            FreeCADGui.Selection.addSelection(obj)

    def clear_selection(self) -> None:
        """Clear the current 3D-view selection."""
        if FreeCADGui:
            FreeCADGui.Selection.clearSelection()

    def get_selected_objects(self) -> list:
        """Return the currently selected FreeCAD objects (whole-object, not sub-elements)."""
        if not FreeCADGui:
            return []
        return list(FreeCADGui.Selection.getSelection())

    def cleanup_old_operations(self, max_age_seconds: int = 300) -> None:
        """Drop pending operations older than max_age_seconds (abandoned selections)."""
        now = time.time()
        expired = [op_id for op_id, op in self.pending_operations.items()
                   if now - op["timestamp"] > max_age_seconds]
        for op_id in expired:
            del self.pending_operations[op_id]

    @staticmethod
    def _parse_element_indices(selection, selection_type: str) -> list:
        """Extract integer edge/face indices from a getSelectionEx() result.

        FreeCAD sub-element names are "Edge3", "Face2", etc (1-based). Callers
        build FreeCAD API calls like f"Edge{idx}" from these indices, so the
        parsed value must round-trip exactly.
        """
        prefix = {"edges": "Edge", "faces": "Face"}.get(selection_type)
        if prefix is None:
            return []
        indices = []
        for sel in selection:
            for sub in sel.SubElementNames:
                if sub.startswith(prefix):
                    try:
                        indices.append(int(sub[len(prefix):]))
                    except ValueError:
                        continue
        return indices

    def _highlight_object(self, object_name: str) -> None:
        """Pre-select the target object so the user can see what to pick edges/faces on."""
        try:
            if not FreeCADGui:
                return
            doc = FreeCAD.ActiveDocument
            if not doc:
                return
            obj = doc.getObject(object_name)
            if obj:
                FreeCADGui.Selection.addSelection(obj)
        except Exception:
            pass

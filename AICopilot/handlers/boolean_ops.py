# Boolean operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class BooleanOpsHandler(BaseHandler):
    """Handler for boolean operations (fuse, cut, common)."""

    def fuse_objects(self, args: Dict[str, Any]) -> str:
        """Fuse (union) multiple objects together."""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Fusion')

            if len(objects) < 2:
                return "Need at least 2 objects to fuse"

            doc = self.get_document()
            if not doc:
                return "No active document"

            # Get object references
            objs = []
            for obj_name in objects:
                obj = self.get_object(obj_name, doc)
                if obj:
                    objs.append(obj)
                else:
                    return f"Object not found: {obj_name}"

            # Safety: warn on high complexity, save before risky op
            warning = self.check_complexity(objs)
            if warning:
                FreeCAD.Console.PrintWarning(f"[MCP] {warning}\n")
            self.save_before_risky_op(doc)

            # Create fusion and hide sources
            fusion = doc.addObject("Part::MultiFuse", name)
            fusion.Label = name
            fusion.Shapes = objs
            self.recompute(doc)
            # Verify the boolean produced a real shape BEFORE hiding the sources —
            # OCCT can yield a null shape (non-manifold/coincident geometry) without
            # raising, which would otherwise leave the user with a hidden, empty result.
            if not getattr(fusion, 'Shape', None) or fusion.Shape.isNull():
                return (f"Fusion produced an empty/invalid shape — sources left visible "
                        f"(check for non-manifold or coincident geometry)")
            for obj in objs:
                obj.Visibility = False

            return f"Created fusion: {fusion.Name} from {len(objects)} objects"

        except Exception as e:
            return f"Error fusing objects: {e}"

    def cut_objects(self, args: Dict[str, Any]) -> str:
        """Cut (subtract) tools from base object."""
        try:
            base = args.get('base', '')
            tools = args.get('tools', [])
            name = args.get('name', 'Cut')

            if not base or not tools:
                return "Need base object and tool objects"

            doc = self.get_document()
            if not doc:
                return "No active document"

            # Get object references
            base_obj = self.get_object(base, doc)
            if not base_obj:
                return f"Base object not found: {base}"

            tool_objs = []
            for tool_name in tools:
                tool_obj = self.get_object(tool_name, doc)
                if tool_obj:
                    tool_objs.append(tool_obj)
                else:
                    return f"Tool object not found: {tool_name}"

            # Safety: save before risky op
            self.save_before_risky_op(doc)

            # Create cut and hide sources. Part::Cut.Tool is a single reference
            # (assigning a list raises "Type must be App.DocumentObject or None,
            # not list"), so fuse multiple tools into one before cutting.
            cut = doc.addObject("Part::Cut", name)
            cut.Label = name
            cut.Base = base_obj
            if len(tool_objs) == 1:
                cut.Tool = tool_objs[0]
            else:
                fusion = doc.addObject("Part::MultiFuse", f"{name}_Tools")
                fusion.Shapes = tool_objs
                cut.Tool = fusion
            self.recompute(doc)
            if not getattr(cut, 'Shape', None) or cut.Shape.isNull():
                return (f"Cut produced an empty/invalid shape — sources left visible "
                        f"(the tools may fully consume the base, or geometry is degenerate)")
            base_obj.Visibility = False
            for obj in tool_objs:
                obj.Visibility = False

            return f"Created cut: {cut.Name} from {base} minus {len(tools)} tools"

        except Exception as e:
            return f"Error cutting objects: {e}"

    def common_objects(self, args: Dict[str, Any]) -> str:
        """Find intersection of multiple objects."""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Common')

            if len(objects) < 2:
                return "Need at least 2 objects for intersection"

            doc = self.get_document()
            if not doc:
                return "No active document"

            # Get object references
            objs = []
            for obj_name in objects:
                obj = self.get_object(obj_name, doc)
                if obj:
                    objs.append(obj)
                else:
                    return f"Object not found: {obj_name}"

            # Safety: warn on high complexity, save before risky op
            warning = self.check_complexity(objs)
            if warning:
                FreeCAD.Console.PrintWarning(f"[MCP] {warning}\n")
            self.save_before_risky_op(doc)

            # Create common and hide sources
            common = doc.addObject("Part::MultiCommon", name)
            common.Label = name
            common.Shapes = objs
            self.recompute(doc)
            # An empty intersection is a legitimate geometric answer (no overlap),
            # but hiding the sources and reporting success would hide that fact.
            if not getattr(common, 'Shape', None) or common.Shape.isNull():
                return (f"Intersection is empty — the objects do not overlap; "
                        f"sources left visible")
            for obj in objs:
                obj.Visibility = False

            return f"Created intersection: {common.Name} from {len(objects)} objects"

        except Exception as e:
            return f"Error finding intersection: {e}"

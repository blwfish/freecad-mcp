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

            # Create fusion
            fusion = doc.addObject("Part::MultiFuse", name)
            fusion.Shapes = objs
            self.recompute(doc)

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

            # Create cut
            cut = doc.addObject("Part::Cut", name)
            cut.Base = base_obj
            cut.Tool = tool_objs[0] if len(tool_objs) == 1 else tool_objs
            self.recompute(doc)

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

            # Create common
            common = doc.addObject("Part::MultiCommon", name)
            common.Shapes = objs
            self.recompute(doc)

            return f"Created intersection: {common.Name} from {len(objects)} objects"

        except Exception as e:
            return f"Error finding intersection: {e}"

# Draft workbench operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class DraftOpsHandler(BaseHandler):
    """Handler for Draft workbench operations."""

    def clone(self, args: Dict[str, Any]) -> str:
        """Create a Draft clone of an object (parametric copy that updates with original)."""
        try:
            object_name = args.get('object_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            import Draft

            clone = Draft.make_clone(obj)
            if x != 0 or y != 0 or z != 0:
                clone.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created clone: {clone.Name} of {object_name}"

        except ImportError:
            return "Error: Draft module not available"
        except Exception as e:
            return f"Error creating clone: {e}"

    def array(self, args: Dict[str, Any]) -> str:
        """Create a Draft rectangular/ortho array."""
        try:
            object_name = args.get('object_name', '')
            count_x = args.get('count_x', 2)
            count_y = args.get('count_y', 1)
            count_z = args.get('count_z', 1)
            interval_x = args.get('interval_x', 100)
            interval_y = args.get('interval_y', 100)
            interval_z = args.get('interval_z', 100)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            import Draft

            array = Draft.make_ortho_array(
                obj,
                v_x=FreeCAD.Vector(interval_x, 0, 0),
                v_y=FreeCAD.Vector(0, interval_y, 0),
                v_z=FreeCAD.Vector(0, 0, interval_z),
                n_x=count_x,
                n_y=count_y,
                n_z=count_z,
                use_link=True
            )

            self.recompute(doc)

            total = count_x * count_y * count_z
            return f"Created array: {array.Name} with {total} instances ({count_x}x{count_y}x{count_z})"

        except ImportError:
            return "Error: Draft module not available"
        except Exception as e:
            return f"Error creating array: {e}"

    def polar_array(self, args: Dict[str, Any]) -> str:
        """Create a Draft polar (circular) array."""
        try:
            object_name = args.get('object_name', '')
            count = args.get('count', 6)
            angle = args.get('angle', 360)
            center_x = args.get('center_x', 0)
            center_y = args.get('center_y', 0)
            center_z = args.get('center_z', 0)
            axis = args.get('axis', 'z')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            import Draft

            center = FreeCAD.Vector(center_x, center_y, center_z)

            array = Draft.make_polar_array(
                obj,
                number=count,
                angle=angle,
                center=center,
                use_link=True
            )

            self.recompute(doc)

            return f"Created polar array: {array.Name} with {count} instances over {angle}°"

        except ImportError:
            return "Error: Draft module not available"
        except Exception as e:
            return f"Error creating polar array: {e}"

    def path_array(self, args: Dict[str, Any]) -> str:
        """Create a Draft path array (objects distributed along a path)."""
        try:
            object_name = args.get('object_name', '')
            path_name = args.get('path_name', '')
            count = args.get('count', 4)
            align = args.get('align', True)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            path_obj = self.get_object(path_name, doc)
            if not path_obj:
                return f"Path object not found: {path_name}"

            import Draft

            array = Draft.make_path_array(
                obj,
                path_obj,
                count=count,
                align=align,
                use_link=True
            )

            self.recompute(doc)

            return f"Created path array: {array.Name} with {count} instances along {path_name}"

        except ImportError:
            return "Error: Draft module not available"
        except Exception as e:
            return f"Error creating path array: {e}"

    def point_array(self, args: Dict[str, Any]) -> str:
        """Create a Draft point array (objects placed at point locations)."""
        try:
            object_name = args.get('object_name', '')
            point_object = args.get('point_object', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            points_obj = self.get_object(point_object, doc)
            if not points_obj:
                return f"Point object not found: {point_object}"

            import Draft

            array = Draft.make_point_array(obj, points_obj)

            self.recompute(doc)

            # Count points if possible
            point_count = "unknown"
            if hasattr(points_obj, 'Points'):
                point_count = len(points_obj.Points)
            elif hasattr(points_obj, 'Shape') and points_obj.Shape.Vertexes:
                point_count = len(points_obj.Shape.Vertexes)

            return f"Created point array: {array.Name} with instances at {point_count} points"

        except ImportError:
            return "Error: Draft module not available"
        except Exception as e:
            return f"Error creating point array: {e}"

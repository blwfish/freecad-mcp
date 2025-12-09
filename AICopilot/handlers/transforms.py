# Transform operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class TransformsHandler(BaseHandler):
    """Handler for transform operations (move, rotate, copy, array)."""

    def move_object(self, args: Dict[str, Any]) -> str:
        """Move an object to new position."""
        import time
        start_time = time.time()

        try:
            object_name = args.get('object_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("move_object", args, error=Exception("No active document"))

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("move_object", args, error=Exception(f"Object not found: {object_name}"))

            # Move object
            obj.Placement.Base = FreeCAD.Vector(
                obj.Placement.Base.x + x,
                obj.Placement.Base.y + y,
                obj.Placement.Base.z + z
            )
            self.recompute(doc)

            result = f"Moved {object_name} by ({x}, {y}, {z})"
            duration = time.time() - start_time
            return self.log_and_return("move_object", args, result=result, duration=duration)

        except Exception as e:
            duration = time.time() - start_time
            return self.log_and_return("move_object", args, error=e, duration=duration)

    def rotate_object(self, args: Dict[str, Any]) -> str:
        """Rotate an object around axis."""
        try:
            object_name = args.get('object_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 90)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            # Set rotation axis
            axis_vector = FreeCAD.Vector(0, 0, 1)  # default Z
            if axis.lower() == 'x':
                axis_vector = FreeCAD.Vector(1, 0, 0)
            elif axis.lower() == 'y':
                axis_vector = FreeCAD.Vector(0, 1, 0)

            # Rotate object
            rotation = FreeCAD.Rotation(axis_vector, angle)
            obj.Placement.Rotation = obj.Placement.Rotation.multiply(rotation)
            self.recompute(doc)

            return f"Rotated {object_name} by {angle}° around {axis.upper()}-axis"

        except Exception as e:
            return f"Error rotating object: {e}"

    def copy_object(self, args: Dict[str, Any]) -> str:
        """Create a copy of an object."""
        try:
            object_name = args.get('object_name', '')
            name = args.get('name', 'Copy')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            # Create copy
            copy = doc.copyObject(obj)
            copy.Label = name
            copy.Placement.Base = FreeCAD.Vector(
                obj.Placement.Base.x + x,
                obj.Placement.Base.y + y,
                obj.Placement.Base.z + z
            )
            self.recompute(doc)

            return f"Created copy: {copy.Name} at offset ({x}, {y}, {z})"

        except Exception as e:
            return f"Error copying object: {e}"

    def array_object(self, args: Dict[str, Any]) -> str:
        """Create linear array of object."""
        try:
            object_name = args.get('object_name', '')
            count = args.get('count', 3)
            spacing_x = args.get('spacing_x', 10)
            spacing_y = args.get('spacing_y', 0)
            spacing_z = args.get('spacing_z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            # Create array copies
            copies = []
            for i in range(1, count):  # Start from 1 (original is 0)
                copy = doc.copyObject(obj)
                copy.Label = f"{obj.Label}_Array{i}"
                copy.Placement.Base = FreeCAD.Vector(
                    obj.Placement.Base.x + (spacing_x * i),
                    obj.Placement.Base.y + (spacing_y * i),
                    obj.Placement.Base.z + (spacing_z * i)
                )
                copies.append(copy.Name)

            self.recompute(doc)

            return f"Created array: {count} copies of {object_name} with spacing ({spacing_x}, {spacing_y}, {spacing_z})"

        except Exception as e:
            return f"Error creating array: {e}"

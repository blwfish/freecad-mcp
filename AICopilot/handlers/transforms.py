# Transform operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class TransformsHandler(BaseHandler):
    """Handler for transform operations (move, rotate, copy, array)."""

    def move_object(self, args: Dict[str, Any]) -> str:
        """Move an object.

        By default moves by a relative offset. Pass relative=False to set
        an absolute position instead.
        """
        import time
        start_time = time.time()

        try:
            object_name = args.get('object_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            relative = args.get('relative', True)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("move_object", args, error=Exception(err))

            if relative:
                obj.Placement.Base = FreeCAD.Vector(
                    obj.Placement.Base.x + x,
                    obj.Placement.Base.y + y,
                    obj.Placement.Base.z + z
                )
                result = f"Moved {object_name} by ({x}, {y}, {z})"
            else:
                obj.Placement.Base = FreeCAD.Vector(x, y, z)
                result = f"Moved {object_name} to ({x}, {y}, {z})"

            self.recompute(doc)
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

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Set rotation axis
            axis_vectors = {
                'x': FreeCAD.Vector(1, 0, 0),
                'y': FreeCAD.Vector(0, 1, 0),
                'z': FreeCAD.Vector(0, 0, 1),
            }
            axis_vector = axis_vectors.get(axis.lower())
            if axis_vector is None:
                # No fallback — an unrecognized axis used to silently
                # rotate around Z while the success message still echoed
                # the requested axis string, e.g. axis="q" reporting
                # "around Q-axis" while actually rotating around Z.
                return f"Invalid axis '{axis}': must be 'x', 'y', or 'z'"

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

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Create copy. with_dependencies=True so parametric/body-backed
            # objects copy their dependency chain instead of referencing the
            # original's (which silently couples the "copy" to the source).
            copy = doc.copyObject(obj, True)
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

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

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

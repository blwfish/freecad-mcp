# Measurement operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class MeasurementOpsHandler(BaseHandler):
    """Handler for measurement and analysis operations."""

    def measure_distance(self, args: Dict[str, Any]) -> str:
        """Measure distance between two objects."""
        try:
            object1 = args.get('object1', '')
            object2 = args.get('object2', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj1 = self.get_object(object1, doc)
            obj2 = self.get_object(object2, doc)

            if not obj1:
                return f"Object not found: {object1}"
            if not obj2:
                return f"Object not found: {object2}"

            # Calculate distance between centers of mass
            if hasattr(obj1, 'Shape') and hasattr(obj2, 'Shape'):
                center1 = obj1.Shape.CenterOfMass
                center2 = obj2.Shape.CenterOfMass
                distance = center1.distanceToPoint(center2)

                return f"Distance between {object1} and {object2}: {distance:.2f} mm"
            else:
                return "Objects must have Shape property for distance measurement"

        except Exception as e:
            return f"Error measuring distance: {e}"

    def get_volume(self, args: Dict[str, Any]) -> str:
        """Calculate volume of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                volume = obj.Shape.Volume
                return f"Volume of {object_name}: {volume:.2f} mm³"
            else:
                return "Object must have Shape property for volume calculation"

        except Exception as e:
            return f"Error calculating volume: {e}"

    def get_bounding_box(self, args: Dict[str, Any]) -> str:
        """Get bounding box dimensions of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                bb = obj.Shape.BoundBox
                return (
                    f"Bounding box of {object_name}:\n"
                    f"  X: {bb.XMin:.2f} to {bb.XMax:.2f} mm (length: {bb.XLength:.2f})\n"
                    f"  Y: {bb.YMin:.2f} to {bb.YMax:.2f} mm (width: {bb.YLength:.2f})\n"
                    f"  Z: {bb.ZMin:.2f} to {bb.ZMax:.2f} mm (height: {bb.ZLength:.2f})"
                )
            else:
                return "Object must have Shape property for bounding box calculation"

        except Exception as e:
            return f"Error calculating bounding box: {e}"

    def get_mass_properties(self, args: Dict[str, Any]) -> str:
        """Get mass properties of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                shape = obj.Shape
                volume = shape.Volume
                center_of_mass = shape.CenterOfMass

                # Calculate surface area
                area = 0
                for face in shape.Faces:
                    area += face.Area

                return (
                    f"Mass properties of {object_name}:\n"
                    f"  Volume: {volume:.2f} mm³\n"
                    f"  Surface Area: {area:.2f} mm²\n"
                    f"  Center of Mass: ({center_of_mass.x:.2f}, {center_of_mass.y:.2f}, {center_of_mass.z:.2f})"
                )
            else:
                return "Object must have Shape property for mass properties calculation"

        except Exception as e:
            return f"Error calculating mass properties: {e}"

    def get_surface_area(self, args: Dict[str, Any]) -> str:
        """Calculate surface area of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                area = 0
                for face in obj.Shape.Faces:
                    area += face.Area
                return f"Surface area of {object_name}: {area:.2f} mm²"
            else:
                return "Object must have Shape property for surface area calculation"

        except Exception as e:
            return f"Error calculating surface area: {e}"

    def get_center_of_mass(self, args: Dict[str, Any]) -> str:
        """Get center of mass of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                com = obj.Shape.CenterOfMass
                return f"Center of mass of {object_name}: ({com.x:.2f}, {com.y:.2f}, {com.z:.2f}) mm"
            else:
                return "Object must have Shape property for center of mass calculation"

        except Exception as e:
            return f"Error calculating center of mass: {e}"

    def count_elements(self, args: Dict[str, Any]) -> str:
        """Count geometric elements (faces, edges, vertices) of an object."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                shape = obj.Shape
                return (
                    f"Element count for {object_name}:\n"
                    f"  Faces: {len(shape.Faces)}\n"
                    f"  Edges: {len(shape.Edges)}\n"
                    f"  Vertices: {len(shape.Vertexes)}\n"
                    f"  Wires: {len(shape.Wires)}\n"
                    f"  Shells: {len(shape.Shells)}\n"
                    f"  Solids: {len(shape.Solids)}"
                )
            else:
                return "Object must have Shape property for element counting"

        except Exception as e:
            return f"Error counting elements: {e}"

    def check_solid(self, args: Dict[str, Any]) -> str:
        """Check if object is a valid solid."""
        try:
            object_name = args.get('object_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if hasattr(obj, 'Shape'):
                shape = obj.Shape
                is_solid = shape.isClosed() and len(shape.Solids) > 0
                is_valid = shape.isValid()

                status = []
                if is_solid:
                    status.append("Is a closed solid")
                else:
                    status.append("Not a closed solid")

                if is_valid:
                    status.append("Shape is valid")
                else:
                    status.append("Shape has errors")

                return f"Solid check for {object_name}:\n  " + "\n  ".join(status)
            else:
                return "Object must have Shape property for solid check"

        except Exception as e:
            return f"Error checking solid: {e}"

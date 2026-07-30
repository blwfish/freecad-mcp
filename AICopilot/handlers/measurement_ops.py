# Measurement operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class MeasurementOpsHandler(BaseHandler):
    """Handler for measurement and analysis operations."""

    _ALLOWED_OPERATIONS = frozenset({
        "measure_distance", "get_volume", "get_bounding_box", "get_mass_properties",
        "get_surface_area", "get_center_of_mass", "count_elements", "list_faces",
        "check_solid",
    })

    def measure_distance(self, args: Dict[str, Any]) -> str:
        """Measure distance between two objects."""
        try:
            object1 = args.get('object1', '')
            object2 = args.get('object2', '')

            doc, obj1, err = self.resolve_object(object1)
            if err:
                return err
            _, obj2, err = self.resolve_object(object2, doc)
            if err:
                return err

            # Minimum surface-to-surface distance — NOT centroid distance, which
            # reports a positive value for touching/overlapping parts. distToShape
            # returns [dist, points, geom_info]; dist == 0 means touching/overlap.
            if hasattr(obj1, 'Shape') and hasattr(obj2, 'Shape'):
                distance = obj1.Shape.distToShape(obj2.Shape)[0]
                if distance < 1e-7:
                    return (f"Distance between {object1} and {object2}: "
                            f"{distance:.4f} mm (touching or overlapping)")
                return f"Distance between {object1} and {object2}: {distance:.3f} mm"
            else:
                return "Objects must have Shape property for distance measurement"

        except Exception as e:
            return f"Error measuring distance: {e}"

    def get_volume(self, args: Dict[str, Any]) -> str:
        """Calculate volume of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            volume = obj.Shape.Volume
            return f"Volume of {object_name}: {volume:.2f} mm³"

        except Exception as e:
            return f"Error calculating volume: {e}"

    def get_bounding_box(self, args: Dict[str, Any]) -> str:
        """Get bounding box dimensions of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            bb = obj.Shape.BoundBox
            return (
                f"Bounding box of {object_name}:\n"
                f"  X: {bb.XMin:.2f} to {bb.XMax:.2f} mm (length: {bb.XLength:.2f})\n"
                f"  Y: {bb.YMin:.2f} to {bb.YMax:.2f} mm (width: {bb.YLength:.2f})\n"
                f"  Z: {bb.ZMin:.2f} to {bb.ZMax:.2f} mm (height: {bb.ZLength:.2f})"
            )

        except Exception as e:
            return f"Error calculating bounding box: {e}"

    def get_mass_properties(self, args: Dict[str, Any]) -> str:
        """Get mass properties of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

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

        except Exception as e:
            return f"Error calculating mass properties: {e}"

    def get_surface_area(self, args: Dict[str, Any]) -> str:
        """Calculate surface area of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            area = 0
            for face in obj.Shape.Faces:
                area += face.Area
            return f"Surface area of {object_name}: {area:.2f} mm²"

        except Exception as e:
            return f"Error calculating surface area: {e}"

    def get_center_of_mass(self, args: Dict[str, Any]) -> str:
        """Get center of mass of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            com = obj.Shape.CenterOfMass
            return f"Center of mass of {object_name}: ({com.x:.2f}, {com.y:.2f}, {com.z:.2f}) mm"

        except Exception as e:
            return f"Error calculating center of mass: {e}"

    def count_elements(self, args: Dict[str, Any]) -> str:
        """Count geometric elements (faces, edges, vertices) of an object."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

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

        except Exception as e:
            return f"Error counting elements: {e}"

    def list_faces(self, args: Dict[str, Any]) -> str:
        """List all faces of an object with index, normal, centroid, and area."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            lines = [f"Faces of {object_name} ({len(obj.Shape.Faces)} total):"]
            for i, face in enumerate(obj.Shape.Faces):
                try:
                    n = face.normalAt(0, 0)
                    c = face.CenterOfMass
                    normal_str = f"({n.x:+.2f}, {n.y:+.2f}, {n.z:+.2f})"
                    centroid_str = f"({c.x:.2f}, {c.y:.2f}, {c.z:.2f})"
                    lines.append(
                        f"  Face{i+1}: normal={normal_str}  centroid={centroid_str}  area={face.Area:.2f}mm²"
                    )
                except Exception as fe:
                    lines.append(f"  Face{i+1}: error reading face — {fe}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error listing faces: {e}"

    def check_solid(self, args: Dict[str, Any]) -> str:
        """Check if object is a valid closed solid."""
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

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

        except Exception as e:
            return f"Error checking solid: {e}"

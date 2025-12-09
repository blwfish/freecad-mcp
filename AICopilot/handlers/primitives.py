# Primitive shape creation handlers for FreeCAD MCP

import FreeCAD
import FreeCADGui
from typing import Dict, Any
from .base import BaseHandler


class PrimitivesHandler(BaseHandler):
    """Handler for creating primitive shapes (Part workbench)."""

    def create_box(self, args: Dict[str, Any]) -> str:
        """Create a box with specified dimensions."""
        try:
            length = args.get('length', 10)
            width = args.get('width', 10)
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document(create_if_missing=True)

            box = doc.addObject("Part::Box", "Box")
            box.Length = length
            box.Width = width
            box.Height = height
            box.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created box: {box.Name} ({length}x{width}x{height}mm) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating box: {e}"

    def create_cylinder(self, args: Dict[str, Any]) -> str:
        """Create a cylinder with specified dimensions."""
        try:
            radius = args.get('radius', 5)
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document(create_if_missing=True)

            cylinder = doc.addObject("Part::Cylinder", "Cylinder")
            cylinder.Radius = radius
            cylinder.Height = height
            cylinder.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created cylinder: {cylinder.Name} (R{radius}, H{height}) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating cylinder: {e}"

    def create_sphere(self, args: Dict[str, Any]) -> str:
        """Create a sphere with specified radius."""
        try:
            radius = args.get('radius', 5)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document(create_if_missing=True)

            sphere = doc.addObject("Part::Sphere", "Sphere")
            sphere.Radius = radius
            sphere.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created sphere: {sphere.Name} (R{radius}) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating sphere: {e}"

    def create_cone(self, args: Dict[str, Any]) -> str:
        """Create a cone with specified radii and height."""
        try:
            radius1 = args.get('radius1', 5)  # Bottom radius
            radius2 = args.get('radius2', 0)  # Top radius
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document(create_if_missing=True)

            cone = doc.addObject("Part::Cone", "Cone")
            cone.Radius1 = radius1
            cone.Radius2 = radius2
            cone.Height = height
            cone.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created cone: {cone.Name} (R1{radius1}, R2{radius2}, H{height}) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating cone: {e}"

    def create_torus(self, args: Dict[str, Any]) -> str:
        """Create a torus (donut shape) with specified radii."""
        try:
            radius1 = args.get('radius1', 10)  # Major radius
            radius2 = args.get('radius2', 3)   # Minor radius
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc = self.get_document(create_if_missing=True)

            torus = doc.addObject("Part::Torus", "Torus")
            torus.Radius1 = radius1
            torus.Radius2 = radius2
            torus.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created torus: {torus.Name} (R1{radius1}, R2{radius2}) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating torus: {e}"

    def create_wedge(self, args: Dict[str, Any]) -> str:
        """Create a wedge (triangular prism) with specified dimensions."""
        try:
            xmin = args.get('xmin', 0)
            ymin = args.get('ymin', 0)
            zmin = args.get('zmin', 0)
            x2min = args.get('x2min', 2)
            x2max = args.get('x2max', 8)
            xmax = args.get('xmax', 10)
            ymax = args.get('ymax', 10)
            zmax = args.get('zmax', 10)

            doc = self.get_document(create_if_missing=True)

            wedge = doc.addObject("Part::Wedge", "Wedge")
            wedge.Xmin = xmin
            wedge.Ymin = ymin
            wedge.Zmin = zmin
            wedge.X2min = x2min
            wedge.X2max = x2max
            wedge.Xmax = xmax
            wedge.Ymax = ymax
            wedge.Zmax = zmax

            self.recompute(doc)

            return f"Created wedge: {wedge.Name} ({xmax}x{ymax}x{zmax}) at origin"

        except Exception as e:
            return f"Error creating wedge: {e}"

# Sketch operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class SketchOpsHandler(BaseHandler):
    """Handler for sketch operations (Sketcher workbench)."""

    def create_sketch(self, args: Dict[str, Any]) -> str:
        """Create a new sketch on specified plane."""
        try:
            plane = args.get('plane', 'XY')
            name = args.get('name', 'Sketch')

            doc = self.get_document(create_if_missing=True)

            # Create sketch
            sketch = doc.addObject('Sketcher::SketchObject', name)

            # Set plane
            if plane.upper() == 'XY':
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0),
                    FreeCAD.Rotation(0, 0, 0, 1)
                )
            elif plane.upper() == 'XZ':
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0),
                    FreeCAD.Rotation(1, 0, 0, 1)
                )
            elif plane.upper() == 'YZ':
                sketch.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, 0),
                    FreeCAD.Rotation(0, 1, 0, 1)
                )

            self.recompute(doc)

            return f"Created sketch: {sketch.Name} on {plane} plane"

        except Exception as e:
            return f"Error creating sketch: {e}"

    def add_line(self, args: Dict[str, Any]) -> str:
        """Add a line to a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            x1 = args.get('x1', 0)
            y1 = args.get('y1', 0)
            x2 = args.get('x2', 10)
            y2 = args.get('y2', 10)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part
            line = Part.LineSegment(
                FreeCAD.Vector(x1, y1, 0),
                FreeCAD.Vector(x2, y2, 0)
            )
            sketch.addGeometry(line)
            self.recompute(doc)

            return f"Added line to {sketch_name}: ({x1},{y1}) to ({x2},{y2})"

        except Exception as e:
            return f"Error adding line: {e}"

    def add_circle(self, args: Dict[str, Any]) -> str:
        """Add a circle to a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            radius = args.get('radius', 5)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part
            circle = Part.Circle(
                FreeCAD.Vector(x, y, 0),
                FreeCAD.Vector(0, 0, 1),
                radius
            )
            sketch.addGeometry(circle)
            self.recompute(doc)

            return f"Added circle to {sketch_name}: center ({x},{y}), radius {radius}"

        except Exception as e:
            return f"Error adding circle: {e}"

    def add_rectangle(self, args: Dict[str, Any]) -> str:
        """Add a rectangle to a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            width = args.get('width', 10)
            height = args.get('height', 10)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part
            # Create rectangle as 4 lines
            p1 = FreeCAD.Vector(x, y, 0)
            p2 = FreeCAD.Vector(x + width, y, 0)
            p3 = FreeCAD.Vector(x + width, y + height, 0)
            p4 = FreeCAD.Vector(x, y + height, 0)

            sketch.addGeometry(Part.LineSegment(p1, p2))
            sketch.addGeometry(Part.LineSegment(p2, p3))
            sketch.addGeometry(Part.LineSegment(p3, p4))
            sketch.addGeometry(Part.LineSegment(p4, p1))

            self.recompute(doc)

            return f"Added rectangle to {sketch_name}: origin ({x},{y}), size {width}x{height}"

        except Exception as e:
            return f"Error adding rectangle: {e}"

    def add_arc(self, args: Dict[str, Any]) -> str:
        """Add an arc to a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            center_x = args.get('center_x', 0)
            center_y = args.get('center_y', 0)
            radius = args.get('radius', 5)
            start_angle = args.get('start_angle', 0)
            end_angle = args.get('end_angle', 90)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part
            import math
            arc = Part.ArcOfCircle(
                Part.Circle(
                    FreeCAD.Vector(center_x, center_y, 0),
                    FreeCAD.Vector(0, 0, 1),
                    radius
                ),
                math.radians(start_angle),
                math.radians(end_angle)
            )
            sketch.addGeometry(arc)
            self.recompute(doc)

            return f"Added arc to {sketch_name}: center ({center_x},{center_y}), R{radius}, {start_angle}° to {end_angle}°"

        except Exception as e:
            return f"Error adding arc: {e}"

    def close_sketch(self, args: Dict[str, Any]) -> str:
        """Close/constrain a sketch (add coincident constraints to close the profile)."""
        try:
            sketch_name = args.get('sketch_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            # Try to auto-constrain
            geo_count = sketch.GeometryCount
            if geo_count < 2:
                return f"Sketch {sketch_name} needs at least 2 geometry elements to close"

            # Add coincident constraints between consecutive endpoints
            for i in range(geo_count - 1):
                sketch.addConstraint(
                    Sketcher.Constraint('Coincident', i, 2, i + 1, 1)
                )

            # Close the loop
            sketch.addConstraint(
                Sketcher.Constraint('Coincident', geo_count - 1, 2, 0, 1)
            )

            self.recompute(doc)

            return f"Closed sketch {sketch_name} with coincident constraints"

        except Exception as e:
            return f"Error closing sketch: {e}"

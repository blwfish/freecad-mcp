# Sketch operation handlers for FreeCAD MCP

import FreeCAD
import json
import math
from typing import Dict, Any
from .base import BaseHandler


class SketchOpsHandler(BaseHandler):
    """Handler for sketch operations (Sketcher workbench)."""

    # -----------------------------------------------------------------
    # Sketch lifecycle
    # -----------------------------------------------------------------

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

    def close_sketch(self, args: Dict[str, Any]) -> str:
        """Close/constrain a sketch (add coincident constraints to close the profile)."""
        try:
            import Sketcher
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

    def verify_sketch(self, args: Dict[str, Any]) -> str:
        """Verify sketch validity for extrusion/pad operations."""
        try:
            sketch_name = args.get('sketch_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            if sketch.TypeId != 'Sketcher::SketchObject':
                return f"Object {sketch_name} is not a sketch (type: {sketch.TypeId})"

            results = []

            # Basic info
            geo_count = sketch.GeometryCount
            constraint_count = sketch.ConstraintCount
            results.append(f"Geometry elements: {geo_count}")
            results.append(f"Constraints: {constraint_count}")

            # Check if fully constrained
            dof = sketch.solve()
            if dof == 0:
                results.append("Fully constrained: Yes")
            elif dof > 0:
                results.append(f"Under-constrained: {dof} degrees of freedom remaining")
            else:
                results.append("Over-constrained or conflicting constraints")

            # Check for open wires
            if hasattr(sketch, 'Shape') and sketch.Shape:
                shape = sketch.Shape
                wire_count = len(shape.Wires)
                results.append(f"Wires: {wire_count}")

                # Check if wires are closed
                closed_wires = 0
                open_wires = 0
                for wire in shape.Wires:
                    if wire.isClosed():
                        closed_wires += 1
                    else:
                        open_wires += 1

                if closed_wires > 0:
                    results.append(f"Closed wires (valid for extrusion): {closed_wires}")
                if open_wires > 0:
                    results.append(f"Open wires (cannot extrude): {open_wires}")

                # Check if can make face
                if wire_count > 0 and closed_wires > 0:
                    try:
                        import Part
                        face = Part.Face(shape.Wires[0])
                        results.append("Can create face: Yes")
                        results.append(f"Face area: {face.Area:.2f} mm²")
                    except Exception as e:
                        results.append(f"Can create face: No - {e}")
            else:
                results.append("No valid shape generated from sketch")

            # Check for construction geometry
            construction_count = 0
            for i in range(geo_count):
                if sketch.getConstruction(i):
                    construction_count += 1
            if construction_count > 0:
                results.append(f"Construction geometry: {construction_count} (not used in extrusion)")

            # Overall verdict
            if dof == 0 and closed_wires > 0 and open_wires == 0:
                verdict = "VALID - Ready for pad/pocket/extrusion"
            elif closed_wires > 0:
                verdict = "USABLE - Has closed profile but may have issues"
            else:
                verdict = "INVALID - No closed profile for solid operations"
            results.append(f"\nVerdict: {verdict}")

            return f"Sketch verification for {sketch_name}:\n  " + "\n  ".join(results)

        except Exception as e:
            return f"Error verifying sketch: {e}"

    # -----------------------------------------------------------------
    # Geometry: lines, circles, rectangles, arcs, polygons, slots
    # -----------------------------------------------------------------

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
            geo_id = sketch.addGeometry(line)
            self.recompute(doc)

            return f"Added line to {sketch_name}: ({x1},{y1}) to ({x2},{y2}), geo_id={geo_id}"

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
            geo_id = sketch.addGeometry(circle)
            self.recompute(doc)

            return f"Added circle to {sketch_name}: center ({x},{y}), radius {radius}, geo_id={geo_id}"

        except Exception as e:
            return f"Error adding circle: {e}"

    def add_rectangle(self, args: Dict[str, Any]) -> str:
        """Add a rectangle to a sketch (4 constrained lines).

        Returns the geo_ids of the 4 lines (bottom=0, right=1, top=2, left=3
        relative to the first geo_id returned). The rectangle is automatically
        constrained with coincident corners, horizontal/vertical edges.
        """
        try:
            import Sketcher
            sketch_name = args.get('sketch_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            width = args.get('width', 10)
            height = args.get('height', 10)
            constrain = args.get('constrain', True)

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

            g0 = sketch.addGeometry(Part.LineSegment(p1, p2))  # bottom
            g1 = sketch.addGeometry(Part.LineSegment(p2, p3))  # right
            g2 = sketch.addGeometry(Part.LineSegment(p3, p4))  # top
            g3 = sketch.addGeometry(Part.LineSegment(p4, p1))  # left

            if constrain:
                # Coincident corners
                sketch.addConstraint(Sketcher.Constraint('Coincident', g0, 2, g1, 1))
                sketch.addConstraint(Sketcher.Constraint('Coincident', g1, 2, g2, 1))
                sketch.addConstraint(Sketcher.Constraint('Coincident', g2, 2, g3, 1))
                sketch.addConstraint(Sketcher.Constraint('Coincident', g3, 2, g0, 1))
                # Horizontal/vertical
                sketch.addConstraint(Sketcher.Constraint('Horizontal', g0))
                sketch.addConstraint(Sketcher.Constraint('Horizontal', g2))
                sketch.addConstraint(Sketcher.Constraint('Vertical', g1))
                sketch.addConstraint(Sketcher.Constraint('Vertical', g3))

            self.recompute(doc)

            return (f"Added rectangle to {sketch_name}: origin ({x},{y}), "
                    f"size {width}x{height}, geo_ids=[{g0},{g1},{g2},{g3}]")

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
            arc = Part.ArcOfCircle(
                Part.Circle(
                    FreeCAD.Vector(center_x, center_y, 0),
                    FreeCAD.Vector(0, 0, 1),
                    radius
                ),
                math.radians(start_angle),
                math.radians(end_angle)
            )
            geo_id = sketch.addGeometry(arc)
            self.recompute(doc)

            return (f"Added arc to {sketch_name}: center ({center_x},{center_y}), "
                    f"R{radius}, {start_angle}° to {end_angle}°, geo_id={geo_id}")

        except Exception as e:
            return f"Error adding arc: {e}"

    def add_polygon(self, args: Dict[str, Any]) -> str:
        """Add a regular polygon to a sketch.

        Creates N line segments forming a regular polygon, with coincident
        constraints at corners and equal-length constraints on all edges.
        """
        try:
            import Sketcher
            sketch_name = args.get('sketch_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            radius = args.get('radius', 10)
            sides = args.get('sides', 6)
            constrain = args.get('constrain', True)

            if sides < 3:
                return "Polygon needs at least 3 sides"

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part

            # Calculate vertices
            points = []
            for i in range(sides):
                angle = 2 * math.pi * i / sides - math.pi / 2  # start at top
                px = x + radius * math.cos(angle)
                py = y + radius * math.sin(angle)
                points.append(FreeCAD.Vector(px, py, 0))

            # Add line segments
            geo_ids = []
            for i in range(sides):
                p_start = points[i]
                p_end = points[(i + 1) % sides]
                gid = sketch.addGeometry(Part.LineSegment(p_start, p_end))
                geo_ids.append(gid)

            if constrain:
                # Coincident corners
                for i in range(sides):
                    next_i = (i + 1) % sides
                    sketch.addConstraint(
                        Sketcher.Constraint('Coincident', geo_ids[i], 2, geo_ids[next_i], 1)
                    )
                # Equal length on all edges (constrain each to the first)
                for i in range(1, sides):
                    sketch.addConstraint(
                        Sketcher.Constraint('Equal', geo_ids[0], geo_ids[i])
                    )

            self.recompute(doc)

            return (f"Added {sides}-sided polygon to {sketch_name}: "
                    f"center ({x},{y}), radius {radius}, geo_ids={geo_ids}")

        except Exception as e:
            return f"Error adding polygon: {e}"

    def add_slot(self, args: Dict[str, Any]) -> str:
        """Add a slot (oblong/stadium shape) to a sketch.

        Creates a slot from two semicircular arcs connected by two tangent lines.
        The slot is oriented along the X axis by default. Use constraints to
        rotate or position it.
        """
        try:
            import Sketcher
            sketch_name = args.get('sketch_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            length = args.get('length', 20)
            width = args.get('width', 6)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            import Part

            r = width / 2.0
            half_len = length / 2.0 - r  # half distance between arc centers

            if half_len <= 0:
                return f"Slot length ({length}) must be greater than width ({width})"

            # Centers of the two arcs
            cx1 = x - half_len
            cx2 = x + half_len

            # Top line: left to right
            g0 = sketch.addGeometry(Part.LineSegment(
                FreeCAD.Vector(cx1, y + r, 0),
                FreeCAD.Vector(cx2, y + r, 0)
            ))
            # Right arc: 90° to -90° (top to bottom)
            g1 = sketch.addGeometry(Part.ArcOfCircle(
                Part.Circle(FreeCAD.Vector(cx2, y, 0), FreeCAD.Vector(0, 0, 1), r),
                math.radians(90), math.radians(-90)
            ))
            # Bottom line: right to left
            g2 = sketch.addGeometry(Part.LineSegment(
                FreeCAD.Vector(cx2, y - r, 0),
                FreeCAD.Vector(cx1, y - r, 0)
            ))
            # Left arc: -90° to 90° (bottom to top)
            g3 = sketch.addGeometry(Part.ArcOfCircle(
                Part.Circle(FreeCAD.Vector(cx1, y, 0), FreeCAD.Vector(0, 0, 1), r),
                math.radians(-90), math.radians(90)
            ))

            # Coincident constraints to close the shape
            sketch.addConstraint(Sketcher.Constraint('Coincident', g0, 2, g1, 1))
            sketch.addConstraint(Sketcher.Constraint('Coincident', g1, 2, g2, 1))
            sketch.addConstraint(Sketcher.Constraint('Coincident', g2, 2, g3, 1))
            sketch.addConstraint(Sketcher.Constraint('Coincident', g3, 2, g0, 1))
            # Tangent between lines and arcs
            sketch.addConstraint(Sketcher.Constraint('Tangent', g0, g1))
            sketch.addConstraint(Sketcher.Constraint('Tangent', g1, g2))
            sketch.addConstraint(Sketcher.Constraint('Tangent', g2, g3))
            sketch.addConstraint(Sketcher.Constraint('Tangent', g3, g0))

            self.recompute(doc)

            return (f"Added slot to {sketch_name}: center ({x},{y}), "
                    f"length {length}, width {width}, geo_ids=[{g0},{g1},{g2},{g3}]")

        except Exception as e:
            return f"Error adding slot: {e}"

    # -----------------------------------------------------------------
    # Sketch-level fillet
    # -----------------------------------------------------------------

    def add_fillet(self, args: Dict[str, Any]) -> str:
        """Add a fillet (rounded corner) at a sketch vertex.

        Replaces the sharp corner with a tangent arc of the given radius.
        The vertex is identified by a geometry index and point index:
        point 1 = start, 2 = end.
        """
        try:
            sketch_name = args.get('sketch_name', '')
            geo_id = args.get('geo_id', 0)
            pos_id = args.get('pos_id', 2)
            radius = args.get('radius', 1.0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            # fillet() returns the index of the new arc geometry
            result = sketch.fillet(geo_id, pos_id, radius)
            self.recompute(doc)

            return (f"Added fillet to {sketch_name} at geo_id={geo_id}, "
                    f"pos_id={pos_id}, radius={radius}")

        except Exception as e:
            return f"Error adding sketch fillet: {e}"

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    def add_constraint(self, args: Dict[str, Any]) -> str:
        """Add a geometric or dimensional constraint to a sketch.

        Constraint types and their required arguments:

        Geometric (no value):
          Coincident:     geo_id1, pos_id1, geo_id2, pos_id2
          PointOnObject:  geo_id1, pos_id1, geo_id2
          Horizontal:     geo_id1
          Vertical:       geo_id1
          Perpendicular:  geo_id1, geo_id2
          Parallel:       geo_id1, geo_id2
          Tangent:        geo_id1, geo_id2
          Equal:          geo_id1, geo_id2
          Block:          geo_id1

        Dimensional (requires value):
          Distance:       geo_id1, pos_id1, geo_id2, pos_id2, value
          DistanceX:      geo_id1, pos_id1, value   (or geo_id1, pos_id1, geo_id2, pos_id2, value)
          DistanceY:      geo_id1, pos_id1, value   (or geo_id1, pos_id1, geo_id2, pos_id2, value)
          Radius:         geo_id1, value
          Diameter:       geo_id1, value
          Angle:          geo_id1, geo_id2, value  (degrees)

        Special:
          Symmetric:      geo_id1, pos_id1, geo_id2, pos_id2, sym_geo, sym_pos
          Fix:            geo_id1, pos_id1

        Point indices: 0=edge, 1=start, 2=end, 3=center
        GeoId: 0+ = user geometry (in add order), -1 = X axis, -2 = Y axis
        """
        try:
            import Sketcher
            sketch_name = args.get('sketch_name', '')
            constraint_type = args.get('constraint_type', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            if not constraint_type:
                return "constraint_type is required"

            g1 = args.get('geo_id1', 0)
            p1 = args.get('pos_id1', 0)
            g2 = args.get('geo_id2', 0)
            p2 = args.get('pos_id2', 0)
            value = args.get('value', None)

            # Build constraint based on type
            ct = constraint_type

            # --- Single-geometry, no value ---
            if ct in ('Horizontal', 'Vertical', 'Block'):
                c = Sketcher.Constraint(ct, g1)

            # --- Two-geometry, no value ---
            elif ct in ('Perpendicular', 'Parallel', 'Tangent', 'Equal'):
                c = Sketcher.Constraint(ct, g1, g2)

            # --- Point-to-point, no value ---
            elif ct == 'Coincident':
                c = Sketcher.Constraint('Coincident', g1, p1, g2, p2)

            # --- Point on object ---
            elif ct == 'PointOnObject':
                c = Sketcher.Constraint('PointOnObject', g1, p1, g2)

            # --- Fix a point ---
            elif ct == 'Fix':
                c = Sketcher.Constraint('Lock', g1, p1)

            # --- Symmetric ---
            elif ct == 'Symmetric':
                sym_geo = args.get('sym_geo', -2)
                sym_pos = args.get('sym_pos', 0)
                c = Sketcher.Constraint('Symmetric', g1, p1, g2, p2, sym_geo, sym_pos)

            # --- Dimensional: single geometry + value ---
            elif ct in ('Radius', 'Diameter'):
                if value is None:
                    return f"{ct} constraint requires a value"
                c = Sketcher.Constraint(ct, g1, value)

            # --- Dimensional: distance between two points ---
            elif ct == 'Distance':
                if value is None:
                    return "Distance constraint requires a value"
                if args.get('geo_id2') is not None:
                    c = Sketcher.Constraint('Distance', g1, p1, g2, p2, value)
                else:
                    # Distance of a line segment (geo_id1 = line)
                    c = Sketcher.Constraint('Distance', g1, value)

            # --- Dimensional: horizontal/vertical distance ---
            elif ct in ('DistanceX', 'DistanceY'):
                if value is None:
                    return f"{ct} constraint requires a value"
                if args.get('geo_id2') is not None:
                    c = Sketcher.Constraint(ct, g1, p1, g2, p2, value)
                else:
                    c = Sketcher.Constraint(ct, g1, p1, value)

            # --- Angle ---
            elif ct == 'Angle':
                if value is None:
                    return "Angle constraint requires a value (degrees)"
                angle_rad = math.radians(value)
                if args.get('geo_id2') is not None:
                    c = Sketcher.Constraint('Angle', g1, g2, angle_rad)
                else:
                    # Angle of a single line from horizontal
                    c = Sketcher.Constraint('Angle', g1, angle_rad)

            else:
                return f"Unknown constraint type: {constraint_type}"

            idx = sketch.addConstraint(c)
            self.recompute(doc)

            # Report degrees of freedom
            dof = sketch.solve()
            dof_msg = f", DoF={dof}" if dof >= 0 else ", over-constrained"

            return (f"Added {constraint_type} constraint to {sketch_name}, "
                    f"index={idx}{dof_msg}")

        except Exception as e:
            return f"Error adding constraint: {e}"

    def delete_constraint(self, args: Dict[str, Any]) -> str:
        """Delete a constraint by its index."""
        try:
            sketch_name = args.get('sketch_name', '')
            index = args.get('index', None)

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            if index is None:
                return "index is required"

            sketch.delConstraint(int(index))
            self.recompute(doc)

            return f"Deleted constraint {index} from {sketch_name}"

        except Exception as e:
            return f"Error deleting constraint: {e}"

    def list_constraints(self, args: Dict[str, Any]) -> str:
        """List all constraints in a sketch with their types and values."""
        try:
            sketch_name = args.get('sketch_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            constraints = []
            for i in range(sketch.ConstraintCount):
                c = sketch.Constraints[i]
                info = {
                    "index": i,
                    "type": c.Type,
                    "first": c.First,
                    "firstPos": c.FirstPos,
                }
                if c.Second != -2000:  # sentinel for "not set"
                    info["second"] = c.Second
                    info["secondPos"] = c.SecondPos
                if c.Third != -2000:
                    info["third"] = c.Third
                    info["thirdPos"] = c.ThirdPos
                if hasattr(c, 'Value') and c.Value != 0:
                    val = c.Value
                    # Angle constraints store radians internally
                    if c.Type == 'Angle':
                        val = math.degrees(val)
                    info["value"] = round(val, 6)
                if hasattr(c, 'Name') and c.Name:
                    info["name"] = c.Name
                constraints.append(info)

            dof = sketch.solve()
            result = {
                "sketch": sketch_name,
                "constraint_count": sketch.ConstraintCount,
                "geometry_count": sketch.GeometryCount,
                "degrees_of_freedom": dof,
                "constraints": constraints,
            }
            return json.dumps(result)

        except Exception as e:
            return f"Error listing constraints: {e}"

    # -----------------------------------------------------------------
    # External geometry
    # -----------------------------------------------------------------

    def add_external_geometry(self, args: Dict[str, Any]) -> str:
        """Add external geometry reference to a sketch.

        References an edge from another object (e.g. a solid body face edge)
        so it can be used for constraints. External geometry gets negative
        geo_ids starting at -3 (first external), -4, etc.

        Args:
            sketch_name: Name of the sketch
            object_name: Name of the object containing the edge
            edge_name: Edge identifier, e.g. "Edge1", "Edge4"
        """
        try:
            sketch_name = args.get('sketch_name', '')
            object_name = args.get('object_name', '')
            edge_name = args.get('edge_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            if not object_name or not edge_name:
                return "object_name and edge_name are required"

            sketch.addExternal(object_name, edge_name)
            self.recompute(doc)

            # Count external geometry to report the geo_id
            ext_count = sketch.ExternalGeometryCount
            ext_geo_id = -(ext_count + 1)  # -3 for first, -4 for second, etc.

            return (f"Added external geometry reference to {sketch_name}: "
                    f"{object_name}.{edge_name}, geo_id≈{ext_geo_id}")

        except Exception as e:
            return f"Error adding external geometry: {e}"

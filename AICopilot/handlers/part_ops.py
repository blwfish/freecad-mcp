# Part workbench operation handlers for FreeCAD MCP

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class PartOpsHandler(BaseHandler):
    """Handler for Part workbench operations (extrude, revolve, mirror, section, scale)."""

    def extrude(self, args: Dict[str, Any]) -> str:
        """Extrude a sketch or wire profile."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            height = args.get('height', 10)
            direction = args.get('direction', 'z')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(profile_sketch, doc)
            if not sketch:
                return f"Sketch {profile_sketch} not found"

            # Determine extrusion vector
            if direction == 'x':
                vec = FreeCAD.Vector(height, 0, 0)
            elif direction == 'y':
                vec = FreeCAD.Vector(0, height, 0)
            else:
                vec = FreeCAD.Vector(0, 0, height)

            if hasattr(sketch, 'Shape'):
                shape = sketch.Shape
                import Part

                if shape.Wires:
                    face = Part.Face(shape.Wires[0])
                    extruded = face.extrude(vec)
                elif shape.Faces:
                    extruded = shape.extrude(vec)
                else:
                    return f"Sketch {profile_sketch} has no valid wires or faces to extrude"

                extrude_obj = doc.addObject("Part::Feature", f"{profile_sketch}_extruded")
                extrude_obj.Shape = extruded
                self.recompute(doc)

                return f"Extruded {profile_sketch} by {height}mm in {direction} direction"
            else:
                return f"Object {profile_sketch} is not a valid sketch"

        except Exception as e:
            return f"Error extruding profile: {e}"

    def revolve(self, args: Dict[str, Any]) -> str:
        """Revolve a sketch profile around an axis."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            angle = args.get('angle', 360)
            axis = args.get('axis', 'z').lower()

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(profile_sketch, doc)
            if not sketch:
                return f"Sketch {profile_sketch} not found"

            # Define revolution axis
            if axis == 'x':
                axis_vec = FreeCAD.Vector(1, 0, 0)
            elif axis == 'y':
                axis_vec = FreeCAD.Vector(0, 1, 0)
            else:
                axis_vec = FreeCAD.Vector(0, 0, 1)

            if hasattr(sketch, 'Shape'):
                shape = sketch.Shape
                import Part

                pos = FreeCAD.Vector(0, 0, 0)
                if hasattr(sketch, 'Placement'):
                    pos = sketch.Placement.Base

                if shape.Wires:
                    face = Part.Face(shape.Wires[0])
                    revolved = face.revolve(pos, axis_vec, angle)
                elif shape.Faces:
                    revolved = shape.Faces[0].revolve(pos, axis_vec, angle)
                else:
                    return f"Sketch {profile_sketch} has no valid wires or faces to revolve"

                revolve_obj = doc.addObject("Part::Feature", f"{profile_sketch}_revolved")
                revolve_obj.Shape = revolved
                self.recompute(doc)

                return f"Revolved {profile_sketch} by {angle}° around {axis} axis"
            else:
                return f"Object {profile_sketch} is not a valid sketch"

        except Exception as e:
            return f"Error revolving profile: {e}"

    def mirror_object(self, args: Dict[str, Any]) -> str:
        """Mirror object across a plane."""
        try:
            object_name = args.get('object_name', '')
            plane = args.get('plane', 'YZ')
            name = args.get('name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object {object_name} not found"

            if not hasattr(obj, 'Shape'):
                return f"Object {object_name} is not a shape object"

            # Set mirror plane normal and origin
            if plane == "YZ":
                normal = FreeCAD.Vector(1, 0, 0)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XZ":
                normal = FreeCAD.Vector(0, 1, 0)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XY":
                normal = FreeCAD.Vector(0, 0, 1)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            else:
                return f"Invalid plane '{plane}'. Valid options: XY, XZ, YZ"

            import Part
            mirrored_shape = obj.Shape.mirror(mirror_point, normal)

            if name:
                mirrored_obj = doc.addObject("Part::Feature", name)
            else:
                mirrored_obj = doc.addObject("Part::Feature", f"{object_name}_mirrored")
            mirrored_obj.Shape = mirrored_shape

            self.recompute(doc)
            return f"Mirrored {object_name} across {plane} plane at (0,0,0)"

        except Exception as e:
            return f"Error mirroring object: {e}"

    def scale_object(self, args: Dict[str, Any]) -> str:
        """Scale object by modifying its dimensions directly."""
        try:
            object_name = args.get('object_name', '')
            scale_factor = args.get('scale_factor', 1.5)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object {object_name} not found"

            # Check if this is a parametric object (Box, Cylinder, etc.)
            if hasattr(obj, 'Length') and hasattr(obj, 'Width') and hasattr(obj, 'Height'):
                # Box object
                old_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                obj.Length = obj.Length.Value * scale_factor
                obj.Width = obj.Width.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                new_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"

            elif hasattr(obj, 'Radius') and hasattr(obj, 'Height'):
                # Cylinder/Cone object
                old_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                obj.Radius = obj.Radius.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                if hasattr(obj, 'Radius2'):
                    obj.Radius2 = obj.Radius2.Value * scale_factor
                new_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"

            elif hasattr(obj, 'Radius'):
                # Sphere object
                old_radius = obj.Radius.Value
                obj.Radius = obj.Radius.Value * scale_factor
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} (R{old_radius}mm → R{obj.Radius.Value}mm)"

            else:
                # Non-parametric object - create scaled copy
                if hasattr(obj, 'Shape'):
                    import Part
                    matrix = FreeCAD.Matrix()
                    matrix.scale(scale_factor, scale_factor, scale_factor)
                    scaled_shape = obj.Shape.transformGeometry(matrix)
                    scaled_obj = doc.addObject("Part::Feature", f"{object_name}_scaled")
                    scaled_obj.Shape = scaled_shape
                    self.recompute(doc)
                    return f"Created scaled copy: {scaled_obj.Name} (factor {scale_factor})"
                else:
                    return f"Cannot scale {object_name} - not a parametric object"

        except Exception as e:
            return f"Error scaling object: {e}"

    def section(self, args: Dict[str, Any]) -> str:
        """Create section of object - placeholder."""
        try:
            object_name = args.get('object_name', '')
            plane = args.get('plane', 'XY')
            offset = args.get('offset', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object {object_name} not found"

            if not hasattr(obj, 'Shape'):
                return f"Object {object_name} is not a shape object"

            import Part

            # Define section plane
            if plane == 'XY':
                section_plane = Part.makePlane(1000, 1000, FreeCAD.Vector(-500, -500, offset))
            elif plane == 'XZ':
                section_plane = Part.makePlane(1000, 1000, FreeCAD.Vector(-500, offset, -500), FreeCAD.Vector(0, 1, 0))
            elif plane == 'YZ':
                section_plane = Part.makePlane(1000, 1000, FreeCAD.Vector(offset, -500, -500), FreeCAD.Vector(1, 0, 0))
            else:
                return f"Invalid plane '{plane}'. Valid options: XY, XZ, YZ"

            # Create section
            section_shape = obj.Shape.section(section_plane)
            section_obj = doc.addObject("Part::Feature", f"{object_name}_section")
            section_obj.Shape = section_shape

            self.recompute(doc)

            return f"Created section of {object_name} at {plane} plane, offset {offset}mm"

        except Exception as e:
            return f"Error creating section: {e}"

    def loft(self, args: Dict[str, Any]) -> str:
        """Loft between multiple sketches to create complex shapes."""
        try:
            sketches = args.get('sketches', [])
            ruled = args.get('ruled', False)
            closed = args.get('closed', True)
            name = args.get('name', 'Loft')

            if len(sketches) < 2:
                return "Need at least 2 sketches for lofting"

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch_objs = []
            for sketch_name in sketches:
                sketch = self.get_object(sketch_name, doc)
                if sketch:
                    sketch_objs.append(sketch)
                else:
                    return f"Sketch not found: {sketch_name}"

            loft = doc.addObject("Part::Loft", name)
            loft.Sections = sketch_objs
            loft.Solid = closed
            loft.Ruled = ruled

            self.recompute(doc)

            return f"Created loft: {loft.Name} through {len(sketches)} profiles"

        except Exception as e:
            return f"Error creating loft: {e}"

    def sweep(self, args: Dict[str, Any]) -> str:
        """Sweep a profile sketch along a path sketch."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            solid = args.get('solid', True)
            name = args.get('name', 'Sweep')

            doc = self.get_document()
            if not doc:
                return "No active document"

            profile = self.get_object(profile_sketch, doc)
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"

            path = self.get_object(path_sketch, doc)
            if not path:
                return f"Path sketch not found: {path_sketch}"

            sweep = doc.addObject("Part::Sweep", name)
            sweep.Sections = [profile]
            sweep.Spine = path
            sweep.Solid = solid

            self.recompute(doc)

            return f"Created sweep: {sweep.Name} with profile {profile_sketch} along path {path_sketch}"

        except Exception as e:
            return f"Error creating sweep: {e}"

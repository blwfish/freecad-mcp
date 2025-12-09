# PartDesign operation handlers for FreeCAD MCP

import json
import FreeCAD
import FreeCADGui
from typing import Dict, Any
from .base import BaseHandler


class PartDesignOpsHandler(BaseHandler):
    """Handler for PartDesign workbench operations."""

    def pad_sketch(self, args: Dict[str, Any]) -> str:
        """Extrude a sketch to create solid (pad) - requires PartDesign Body."""
        try:
            sketch_name = args.get('sketch_name', '')
            length = args.get('length', 10)
            name = args.get('name', 'Pad')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            # Get or create body
            body = self.create_body_if_needed(doc)

            # Check if sketch is already in a Body
            sketch_body = self.find_body_for_object(sketch, doc)

            # If sketch is not in any Body, add it to our Body
            if not sketch_body:
                body.addObject(sketch)
            # If sketch is in a different Body, use that Body instead
            elif sketch_body != body:
                body = sketch_body

            # Create pad within the body
            pad = body.newObject("PartDesign::Pad", name)
            pad.Profile = sketch
            pad.Length = length

            self.recompute(doc)

            return f"Created pad: {pad.Name} from {sketch_name} with length {length}mm in Body: {body.Name}"

        except Exception as e:
            return f"Error creating pad: {e}"

    def pocket(self, args: Dict[str, Any]) -> str:
        """Create a pocket (subtractive extrusion) from a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            depth = args.get('depth', 10)
            name = args.get('name', 'Pocket')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            # Get body containing sketch
            body = self.find_body_for_object(sketch, doc)
            if not body:
                return f"Sketch {sketch_name} must be in a PartDesign Body"

            # Create pocket
            pocket = body.newObject("PartDesign::Pocket", name)
            pocket.Profile = sketch
            pocket.Length = depth

            self.recompute(doc)

            return f"Created pocket: {pocket.Name} from {sketch_name} with depth {depth}mm"

        except Exception as e:
            return f"Error creating pocket: {e}"

    def fillet_edges(self, args: Dict[str, Any]) -> str:
        """Add fillets to object edges (Interactive selection workflow)."""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')
            auto_select_all = args.get('auto_select_all', False)
            edges = args.get('edges', [])

            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)

                if not selection_result:
                    return "Selection operation not found or expired"

                if "error" in selection_result:
                    return selection_result["error"]

                return self._create_fillet_with_selection(args, selection_result)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if not hasattr(obj, 'Shape') or not obj.Shape.Edges:
                return f"Object {object_name} has no edges to fillet"

            # Method 1: Use explicit edge list if provided
            if edges:
                return self._create_fillet_with_edges(object_name, edges, radius, name)

            # Method 2: Auto-select all edges if requested
            if auto_select_all:
                return self._create_fillet_auto(args)

            # Method 3: Interactive selection workflow
            selection_request = self.selector.request_selection(
                tool_name="fillet_edges",
                selection_type="edges",
                message=f"Please select edges to fillet on {object_name} object in FreeCAD.\nTell me when you have finished selecting edges...",
                object_name=object_name,
                hints="Select edges for filleting. Ctrl+click for multiple edges.",
                radius=radius,
                name=name
            )

            return json.dumps(selection_request)

        except Exception as e:
            return f"Error in fillet operation: {e}"

    def _create_fillet_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create fillet using selected edges."""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            edge_indices = selection_result["selection_data"]["elements"]
            if not edge_indices:
                return "No edges were selected"

            # Find the Body containing the object
            body = self.find_body_for_object(obj, doc)

            if body:
                # Use PartDesign::Fillet for parametric feature in Body
                fillet = body.newObject("PartDesign::Fillet", name)
                fillet.Radius = radius
                edge_names = [f"Edge{idx}" for idx in edge_indices]
                fillet.Base = (obj, edge_names)
            else:
                # Fallback to Part::Fillet if not in a Body
                fillet = doc.addObject("Part::Fillet", name)
                fillet.Base = obj
                if hasattr(obj, 'Shape') and obj.Shape.Edges:
                    edge_list = []
                    for edge_idx in edge_indices:
                        if 1 <= edge_idx <= len(obj.Shape.Edges):
                            edge_list.append((edge_idx, radius, radius))
                    fillet.Edges = edge_list

            self.recompute(doc)

            return f"Created fillet: {fillet.Name} on {len(edge_indices)} selected edges with radius {radius}mm"

        except Exception as e:
            return f"Error creating fillet with selection: {e}"

    def _create_fillet_with_edges(self, object_name: str, edges: list, radius: float, name: str) -> str:
        """Create fillet with explicit edge list."""
        try:
            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            body = self.find_body_for_object(obj, doc)

            if body:
                fillet = body.newObject("PartDesign::Fillet", name)
                fillet.Radius = radius
                edge_names = [f"Edge{idx}" for idx in edges]
                fillet.Base = (obj, edge_names)
            else:
                fillet = doc.addObject("Part::Fillet", name)
                fillet.Base = obj
                edge_list = [(edge_idx, radius, radius) for edge_idx in edges]
                fillet.Edges = edge_list

            self.recompute(doc)

            return f"Created fillet: {fillet.Name} on {len(edges)} edges with radius {radius}mm"

        except Exception as e:
            return f"Error creating fillet with edges: {e}"

    def _create_fillet_auto(self, args: Dict[str, Any]) -> str:
        """Create fillet on all edges."""
        try:
            object_name = args.get('object_name', '')
            radius = args.get('radius', 1)
            name = args.get('name', 'Fillet')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            fillet = doc.addObject("Part::Fillet", name)
            fillet.Base = obj

            if hasattr(obj, 'Shape') and obj.Shape.Edges:
                edge_list = [(i + 1, radius, radius) for i in range(len(obj.Shape.Edges))]
                fillet.Edges = edge_list

            self.recompute(doc)

            return f"Created fillet: {fillet.Name} on all {len(obj.Shape.Edges)} edges with radius {radius}mm"

        except Exception as e:
            return f"Error creating auto fillet: {e}"

    def chamfer_edges(self, args: Dict[str, Any]) -> str:
        """Add chamfers (angled cuts) to object edges (with interactive selection)."""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')
            auto_select_all = args.get('auto_select_all', False)

            # Check if this is continuing a selection
            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)

                if not selection_result:
                    return "Selection operation not found or expired"

                if "error" in selection_result:
                    return selection_result["error"]

                return self._create_chamfer_with_selection(args, selection_result)

            if auto_select_all:
                return self._create_chamfer_auto(args)

            selection_request = self.selector.request_selection(
                tool_name="chamfer_edges",
                selection_type="edges",
                message=f"Please select edges to chamfer on {object_name} object in FreeCAD.\nTell me when you have finished selecting edges...",
                object_name=object_name,
                hints="Select sharp edges for chamfering. Ctrl+click for multiple edges.",
                distance=distance,
                name=name
            )

            return json.dumps(selection_request)

        except Exception as e:
            return f"Error in chamfer operation: {e}"

    def _create_chamfer_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create chamfer using selected edges."""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            edge_indices = selection_result["selection_data"]["elements"]
            if not edge_indices:
                return "No edges were selected"

            body = self.find_body_for_object(obj, doc)

            if body:
                chamfer = body.newObject("PartDesign::Chamfer", name)
                chamfer.Size = distance
                edge_names = [f"Edge{idx}" for idx in edge_indices]
                chamfer.Base = (obj, edge_names)
            else:
                chamfer = doc.addObject("Part::Chamfer", name)
                chamfer.Base = obj
                if hasattr(obj, 'Shape') and obj.Shape.Edges:
                    edge_list = []
                    for edge_idx in edge_indices:
                        if 1 <= edge_idx <= len(obj.Shape.Edges):
                            edge_list.append((edge_idx, distance))
                    chamfer.Edges = edge_list

            self.recompute(doc)

            return f"Created chamfer: {chamfer.Name} on {len(edge_indices)} selected edges with distance {distance}mm"

        except Exception as e:
            return f"Error creating chamfer with selection: {e}"

    def _create_chamfer_auto(self, args: Dict[str, Any]) -> str:
        """Create chamfer on all edges."""
        try:
            object_name = args.get('object_name', '')
            distance = args.get('distance', 1)
            name = args.get('name', 'Chamfer')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            chamfer = doc.addObject("Part::Chamfer", name)
            chamfer.Base = obj

            if hasattr(obj, 'Shape') and obj.Shape.Edges:
                edge_list = [(i + 1, distance) for i in range(len(obj.Shape.Edges))]
                chamfer.Edges = edge_list

            self.recompute(doc)

            return f"Created chamfer: {chamfer.Name} on all {len(obj.Shape.Edges)} edges with distance {distance}mm"

        except Exception as e:
            return f"Error creating auto chamfer: {e}"

    def hole_wizard(self, args: Dict[str, Any]) -> str:
        """Create standard holes (simple, counterbore, countersink)."""
        try:
            object_name = args.get('object_name', '')
            hole_type = args.get('hole_type', 'simple')
            diameter = args.get('diameter', 6)
            depth = args.get('depth', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            cb_diameter = args.get('cb_diameter', 12)
            cb_depth = args.get('cb_depth', 3)

            doc = self.get_document()
            if not doc:
                return "No active document"

            base_obj = self.get_object(object_name, doc)
            if not base_obj:
                return f"Object not found: {object_name}"

            # Create hole cylinder
            hole = doc.addObject("Part::Cylinder", "Hole")
            hole.Radius = diameter / 2
            hole.Height = depth + 5
            hole.Placement.Base = FreeCAD.Vector(x, y, -2.5)

            if hole_type == 'counterbore':
                cb_hole = doc.addObject("Part::Cylinder", "CounterboreHole")
                cb_hole.Radius = cb_diameter / 2
                cb_hole.Height = cb_depth + 1
                cb_hole.Placement.Base = FreeCAD.Vector(x, y, -0.5)

                combined_hole = doc.addObject("Part::Fuse", "CombinedHole")
                combined_hole.Base = hole
                combined_hole.Tool = cb_hole
                self.recompute(doc)

                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = combined_hole

            elif hole_type == 'countersink':
                cs_cone = doc.addObject("Part::Cone", "CountersinkCone")
                cs_cone.Radius1 = cb_diameter / 2
                cs_cone.Radius2 = diameter / 2
                cs_cone.Height = cb_depth
                cs_cone.Placement.Base = FreeCAD.Vector(x, y, -cb_depth)

                combined_hole = doc.addObject("Part::Fuse", "CombinedHole")
                combined_hole.Base = hole
                combined_hole.Tool = cs_cone
                self.recompute(doc)

                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = combined_hole

            else:  # simple hole
                cut = doc.addObject("Part::Cut", f"{object_name}_WithHole")
                cut.Base = base_obj
                cut.Tool = hole

            self.recompute(doc)

            return f"Created {hole_type} hole: {diameter}mm diameter at ({x}, {y}) in {object_name}"

        except Exception as e:
            return f"Error creating hole: {e}"

    def linear_pattern(self, args: Dict[str, Any]) -> str:
        """Create linear pattern of features."""
        try:
            feature_name = args.get('feature_name', '')
            direction = args.get('direction', 'x')
            count = args.get('count', 3)
            spacing = args.get('spacing', 10)
            name = args.get('name', 'LinearPattern')

            doc = self.get_document()
            if not doc:
                return "No active document"

            feature = self.get_object(feature_name, doc)
            if not feature:
                return f"Feature not found: {feature_name}"

            direction_vector = FreeCAD.Vector(0, 0, 0)
            if direction.lower() == 'x':
                direction_vector = FreeCAD.Vector(spacing, 0, 0)
            elif direction.lower() == 'y':
                direction_vector = FreeCAD.Vector(0, spacing, 0)
            elif direction.lower() == 'z':
                direction_vector = FreeCAD.Vector(0, 0, spacing)

            for i in range(1, count):
                copy = doc.copyObject(feature)
                copy.Label = f"{feature.Label}_Pattern{i}"
                offset = FreeCAD.Vector(
                    direction_vector.x * i,
                    direction_vector.y * i,
                    direction_vector.z * i
                )
                copy.Placement.Base = feature.Placement.Base.add(offset)

            self.recompute(doc)

            return f"Created linear pattern: {count} instances of {feature_name} in {direction} direction with {spacing}mm spacing"

        except Exception as e:
            return f"Error creating linear pattern: {e}"

    def polar_pattern(self, args: Dict[str, Any]) -> str:
        """Create circular/polar pattern of features."""
        try:
            feature_name = args.get('feature_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 360)
            count = args.get('count', 6)
            name = args.get('name', 'PolarPattern')

            doc = self.get_document()
            if not doc:
                return "No active document"

            feature = self.get_object(feature_name, doc)
            if not feature:
                return f"Feature not found: {feature_name}"

            angle_step = angle / count

            axis_vector = FreeCAD.Vector(0, 0, 1)
            if axis.lower() == 'x':
                axis_vector = FreeCAD.Vector(1, 0, 0)
            elif axis.lower() == 'y':
                axis_vector = FreeCAD.Vector(0, 1, 0)

            for i in range(1, count):
                copy = doc.copyObject(feature)
                copy.Label = f"{feature.Label}_Polar{i}"
                rotation_angle = angle_step * i
                rotation = FreeCAD.Rotation(axis_vector, rotation_angle)
                new_placement = FreeCAD.Placement(
                    feature.Placement.Base,
                    feature.Placement.Rotation.multiply(rotation)
                )
                copy.Placement = new_placement

            self.recompute(doc)

            return f"Created polar pattern: {count} instances of {feature_name} around {axis.upper()}-axis, {angle}° total"

        except Exception as e:
            return f"Error creating polar pattern: {e}"

    def mirror_feature(self, args: Dict[str, Any]) -> str:
        """Mirror features across a plane."""
        try:
            feature_name = args.get('feature_name', '')
            plane = args.get('plane', 'YZ')
            name = args.get('name', 'Mirrored')

            doc = self.get_document()
            if not doc:
                return "No active document"

            feature = self.get_object(feature_name, doc)
            if not feature:
                return f"Feature not found: {feature_name}"

            mirror = doc.addObject("Part::Mirroring", name)
            mirror.Source = feature

            if plane.upper() == 'XY':
                mirror.Normal = (0, 0, 1)
                mirror.Base = (0, 0, 0)
            elif plane.upper() == 'XZ':
                mirror.Normal = (0, 1, 0)
                mirror.Base = (0, 0, 0)
            elif plane.upper() == 'YZ':
                mirror.Normal = (1, 0, 0)
                mirror.Base = (0, 0, 0)

            self.recompute(doc)

            return f"Created mirror: {mirror.Name} of {feature_name} across {plane} plane"

        except Exception as e:
            return f"Error creating mirror: {e}"

    def revolution(self, args: Dict[str, Any]) -> str:
        """Revolve a sketch around an axis to create solid of revolution."""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 360)
            name = args.get('name', 'Revolution')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            revolution = doc.addObject("Part::Revolution", name)
            revolution.Source = sketch
            revolution.Angle = angle

            if axis.lower() == 'x':
                revolution.Axis = (1, 0, 0)
            elif axis.lower() == 'y':
                revolution.Axis = (0, 1, 0)
            else:
                revolution.Axis = (0, 0, 1)

            self.recompute(doc)

            return f"Created revolution: {revolution.Name} from {sketch_name} around {axis.upper()}-axis, {angle}°"

        except Exception as e:
            return f"Error creating revolution: {e}"

    def groove(self, args: Dict[str, Any]) -> str:
        """Create a groove (subtractive revolution) from a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 360)
            name = args.get('name', 'Groove')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            body = self.find_body_for_object(sketch, doc)
            if not body:
                return f"Sketch {sketch_name} must be in a PartDesign Body"

            groove = body.newObject("PartDesign::Groove", name)
            groove.Profile = sketch
            groove.Angle = angle

            if axis.lower() == 'x':
                groove.ReferenceAxis = (sketch, ['N_Axis'])
            elif axis.lower() == 'y':
                groove.ReferenceAxis = (sketch, ['N_Axis'])
            else:
                groove.ReferenceAxis = (sketch, ['N_Axis'])

            self.recompute(doc)

            return f"Created groove: {groove.Name} from {sketch_name} around {axis.upper()}-axis, {angle}°"

        except Exception as e:
            return f"Error creating groove: {e}"

    def loft_profiles(self, args: Dict[str, Any]) -> str:
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

    def sweep_path(self, args: Dict[str, Any]) -> str:
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

    def additive_pipe(self, args: Dict[str, Any]) -> str:
        """Create additive pipe (sweep along path within PartDesign Body)."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            name = args.get('name', 'AdditivePipe')

            doc = self.get_document()
            if not doc:
                return "No active document"

            profile = self.get_object(profile_sketch, doc)
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"

            path = self.get_object(path_sketch, doc)
            if not path:
                return f"Path sketch not found: {path_sketch}"

            body = self.find_body_for_object(profile, doc)
            if not body:
                body = self.find_body_for_object(path, doc)
            if not body:
                return "Sketches must be in a PartDesign Body"

            pipe = body.newObject("PartDesign::AdditivePipe", name)
            pipe.Profile = profile
            pipe.Spine = path

            self.recompute(doc)

            return f"Created additive pipe: {pipe.Name} sweeping {profile_sketch} along {path_sketch}"

        except Exception as e:
            return f"Error creating additive pipe: {e}"

    def subtractive_loft(self, args: Dict[str, Any]) -> str:
        """Create subtractive loft through multiple profiles."""
        try:
            sketches = args.get('sketches', [])
            name = args.get('name', 'SubtractiveLoft')

            if len(sketches) < 2:
                return "Need at least 2 sketches for lofting"

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch_objs = []
            body = None
            for sketch_name in sketches:
                sketch = self.get_object(sketch_name, doc)
                if sketch:
                    sketch_objs.append(sketch)
                    if not body:
                        body = self.find_body_for_object(sketch, doc)
                else:
                    return f"Sketch not found: {sketch_name}"

            if not body:
                return "Sketches must be in a PartDesign Body"

            loft = body.newObject("PartDesign::SubtractiveLoft", name)
            loft.Sections = sketch_objs

            self.recompute(doc)

            return f"Created subtractive loft: {loft.Name} through {len(sketches)} profiles"

        except Exception as e:
            return f"Error creating subtractive loft: {e}"

    def subtractive_sweep(self, args: Dict[str, Any]) -> str:
        """Create subtractive pipe/sweep."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            name = args.get('name', 'SubtractivePipe')

            doc = self.get_document()
            if not doc:
                return "No active document"

            profile = self.get_object(profile_sketch, doc)
            if not profile:
                return f"Profile sketch not found: {profile_sketch}"

            path = self.get_object(path_sketch, doc)
            if not path:
                return f"Path sketch not found: {path_sketch}"

            body = self.find_body_for_object(profile, doc)
            if not body:
                body = self.find_body_for_object(path, doc)
            if not body:
                return "Sketches must be in a PartDesign Body"

            pipe = body.newObject("PartDesign::SubtractivePipe", name)
            pipe.Profile = profile
            pipe.Spine = path

            self.recompute(doc)

            return f"Created subtractive pipe: {pipe.Name} sweeping {profile_sketch} along {path_sketch}"

        except Exception as e:
            return f"Error creating subtractive pipe: {e}"

    def draft_faces(self, args: Dict[str, Any]) -> str:
        """Add draft angles to faces for manufacturing (Interactive selection workflow)."""
        try:
            object_name = args.get('object_name', '')
            angle = args.get('angle', 5)
            neutral_plane = args.get('neutral_plane', 'XY')
            name = args.get('name', 'Draft')

            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)

                if not selection_result:
                    return "Selection operation not found or expired"

                if "error" in selection_result:
                    return selection_result["error"]

                return self._create_draft_with_selection(args, selection_result)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if not hasattr(obj, 'Shape') or not obj.Shape.Faces:
                return f"Object {object_name} has no faces for draft"

            selection_request = self.selector.request_selection(
                tool_name="draft_faces",
                selection_type="faces",
                message=f"Please select faces to draft on {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Select faces to apply draft angle. Ctrl+click for multiple faces.",
                angle=angle,
                neutral_plane=neutral_plane,
                name=name
            )

            return json.dumps(selection_request)

        except Exception as e:
            return f"Error in draft operation: {e}"

    def _create_draft_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create draft using selected faces."""
        try:
            object_name = args.get('object_name', '')
            angle = args.get('angle', 5)
            name = args.get('name', 'Draft')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected"

            body = self.find_body_for_object(obj, doc)

            if body:
                draft = body.newObject("PartDesign::Draft", name)
                draft.Angle = angle
                draft.Reversed = False
                face_names = [f"Face{idx}" for idx in face_indices]
                draft.Base = (obj, face_names)

                self.recompute(doc)

                return f"Created draft: {draft.Name} on {len(face_indices)} selected faces with {angle}° angle"
            else:
                return "Draft operation requires object to be in a PartDesign Body"

        except Exception as e:
            return f"Error creating draft with selection: {e}"

    def shell_solid(self, args: Dict[str, Any]) -> str:
        """Hollow out a solid by removing material."""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')
            auto_shell_closed = args.get('auto_shell_closed', False)

            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)

                if not selection_result:
                    return "Selection operation not found or expired"

                if "error" in selection_result:
                    return selection_result["error"]

                return self._create_shell_with_selection(args, selection_result)

            if auto_shell_closed:
                return self._create_shell_closed(args)

            selection_request = self.selector.request_selection(
                tool_name="shell_solid",
                selection_type="faces",
                message=f"Please select face(s) to remove for opening the {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Usually select the top face or access faces for openings. Ctrl+click for multiple faces."
            )

            return json.dumps(selection_request)

        except Exception as e:
            return f"Error in shell operation: {e}"

    def _create_shell_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create shell using selected faces for opening."""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected for opening"

            shell = doc.addObject("Part::Thickness", name)
            shell.Value = thickness
            shell.Source = obj
            shell.Join = 2

            if hasattr(obj, 'Shape') and obj.Shape.Faces:
                faces_to_remove = []
                for face_idx in face_indices:
                    if 1 <= face_idx <= len(obj.Shape.Faces):
                        faces_to_remove.append(face_idx - 1)
                shell.Faces = tuple(faces_to_remove)

            self.recompute(doc)

            return f"Created shell: {shell.Name} from {object_name} with {thickness}mm thickness and {len(face_indices)} face(s) removed for opening"

        except Exception as e:
            return f"Error creating shell with selection: {e}"

    def _create_shell_closed(self, args: Dict[str, Any]) -> str:
        """Create closed shell (no opening)."""
        try:
            object_name = args.get('object_name', '')
            thickness = args.get('thickness', 2)
            name = args.get('name', 'Shell')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            shell = doc.addObject("Part::Thickness", name)
            shell.Value = thickness
            shell.Source = obj
            shell.Join = 2

            self.recompute(doc)

            return f"Created closed shell: {shell.Name} from {object_name} with {thickness}mm thickness (no opening)"

        except Exception as e:
            return f"Error creating closed shell: {e}"

    def add_thickness(self, args: Dict[str, Any]) -> str:
        """Add PartDesign thickness with face selection."""
        try:
            object_name = args.get('object_name', '')
            thickness_val = args.get('thickness', 2)
            name = args.get('name', 'Thickness')

            if args.get('_continue_selection'):
                operation_id = args.get('_operation_id')
                selection_result = self.selector.complete_selection(operation_id)

                if not selection_result:
                    return "Selection operation not found or expired"

                if "error" in selection_result:
                    return selection_result["error"]

                return self._create_thickness_with_selection(args, selection_result)

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            if not hasattr(obj, 'Shape') or not obj.Shape.Faces:
                return f"Object {object_name} has no faces for thickness"

            selection_request = self.selector.request_selection(
                tool_name="thickness_faces",
                selection_type="faces",
                message=f"Please select faces to remove for thickness operation on {object_name} object in FreeCAD.\nTell me when you have finished selecting faces...",
                object_name=object_name,
                hints="Select faces to remove (hollow out). Ctrl+click for multiple faces.",
                thickness=thickness_val,
                name=name
            )

            return json.dumps(selection_request)

        except Exception as e:
            return f"Error in thickness operation: {e}"

    def _create_thickness_with_selection(self, args: Dict[str, Any], selection_result: Dict[str, Any]) -> str:
        """Create PartDesign thickness using selected faces."""
        try:
            object_name = args.get('object_name', '')
            thickness_val = args.get('thickness', 2)
            name = args.get('name', 'Thickness')

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            body = self.find_body_for_object(obj, doc)
            if not body:
                return f"Object {object_name} is not in a PartDesign Body. PartDesign::Thickness requires a Body."

            face_indices = selection_result["selection_data"]["elements"]
            if not face_indices:
                return "No faces were selected for thickness opening"

            thickness = body.newObject("PartDesign::Thickness", name)
            thickness.Base = (obj, tuple(f"Face{face_idx}" for face_idx in face_indices))
            thickness.Value = thickness_val

            self.recompute(doc)

            return f"Created PartDesign Thickness: {thickness.Name} from {object_name} with {thickness_val}mm thickness and {len(face_indices)} face(s) removed for opening"

        except Exception as e:
            return f"Error creating thickness with selection: {e}"

    def create_helix(self, args: Dict[str, Any]) -> str:
        """Create helical features (threads, springs)."""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            pitch = args.get('pitch', 2)
            height = args.get('height', 10)
            turns = args.get('turns', 5)
            left_handed = args.get('left_handed', False)
            name = args.get('name', 'Helix')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            helix_curve = doc.addObject("Part::Helix", f"{name}_Path")
            helix_curve.Pitch = pitch
            helix_curve.Height = height
            helix_curve.Radius = 10
            helix_curve.Angle = 0
            helix_curve.LeftHanded = left_handed

            if axis.lower() == 'x':
                helix_curve.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)
            elif axis.lower() == 'y':
                helix_curve.Placement.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)

            self.recompute(doc)

            helix_sweep = doc.addObject("Part::Sweep", name)
            helix_sweep.Sections = [sketch]
            helix_sweep.Spine = helix_curve
            helix_sweep.Solid = True

            self.recompute(doc)

            return f"Created helix: {helix_sweep.Name} from {sketch_name}, pitch={pitch}mm, height={height}mm, turns={turns}"

        except Exception as e:
            return f"Error creating helix: {e}"

    def create_rib(self, args: Dict[str, Any]) -> str:
        """Create structural ribs from sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            thickness = args.get('thickness', 3)
            direction = args.get('direction', 'normal')
            name = args.get('name', 'Rib')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            rib = doc.addObject("Part::Extrude", name)
            rib.Base = sketch

            if direction.lower() == 'horizontal':
                rib.Dir = (1, 0, 0)
                rib.LengthFwd = thickness
            elif direction.lower() == 'vertical':
                rib.Dir = (0, 0, 1)
                rib.LengthFwd = thickness
            else:
                rib.Dir = (0, 1, 0)
                rib.LengthFwd = thickness

            rib.Solid = True

            self.recompute(doc)

            return f"Created rib: {rib.Name} from {sketch_name} with {thickness}mm thickness in {direction} direction"

        except Exception as e:
            return f"Error creating rib: {e}"

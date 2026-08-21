# PartDesign operation handlers for FreeCAD MCP

import json
import math
import FreeCAD
import Part
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
            reversed_dir = args.get('reversed', False)

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
            if reversed_dir:
                pad.Reversed = True

            self.recompute(doc)

            rev_msg = " (reversed)" if reversed_dir else ""
            base_msg = f"Created pad: {pad.Name} from {sketch_name} with length {length}mm{rev_msg} in Body: {body.Name}"

            err = self._check_feature_state(pad, "Pad", sketch=sketch)
            if err:
                return err

            return base_msg

        except Exception as e:
            err = f"Error creating pad: {e}"
            # Auto-diagnose the sketch profile so the user knows exactly
            # which endpoints are disconnected and what constraints to add.
            try:
                doc = FreeCAD.ActiveDocument
                sketch = self.get_object(sketch_name, doc) if doc else None
                if sketch and sketch.TypeId == 'Sketcher::SketchObject':
                    diagnosis = self._diagnose_open_wires(sketch)
                    if diagnosis:
                        err += f"\n\nSketch wire diagnosis:\n{diagnosis}"
            except Exception:
                pass
            return err

    def pocket(self, args: Dict[str, Any]) -> str:
        """Create a pocket (subtractive extrusion) from a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            # Accept 'length' (matching pad and MCP schema) or 'depth' for compat
            length = args.get('length', args.get('depth', 10))
            name = args.get('name', 'Pocket')
            reversed_dir = args.get('reversed', False)

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
            pocket.Length = length
            if reversed_dir:
                pocket.Reversed = True

            self.recompute(doc)

            rev_msg = " (reversed)" if reversed_dir else ""
            base_msg = f"Created pocket: {pocket.Name} from {sketch_name} with depth {length}mm{rev_msg}"

            err = self._check_feature_state(pocket, "Pocket", sketch=sketch)
            if err:
                return err

            return base_msg

        except Exception as e:
            err = f"Error creating pocket: {e}"
            try:
                doc = FreeCAD.ActiveDocument
                sketch = self.get_object(sketch_name, doc) if doc else None
                if sketch and sketch.TypeId == 'Sketcher::SketchObject':
                    diagnosis = self._diagnose_open_wires(sketch)
                    if diagnosis:
                        err += f"\n\nSketch wire diagnosis:\n{diagnosis}"
            except Exception:
                pass
            return err

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

            err = self._check_feature_state(fillet, "Fillet")
            if err:
                return err

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
                # Validate BEFORE creating anything -- an out-of-range index
                # used to be silently dropped here (edge_list built via
                # `if 1 <= idx <= n_edges`), creating a fillet on fewer
                # edges than requested with no indication any were
                # skipped. The Body path above has no such filter at all
                # (an invalid index there reaches OCCT directly and fails
                # loudly via _check_feature_state below) -- reject
                # up front here instead of silently under-filleting,
                # matching that same "fail loud, not quiet" contract
                # (fixed 2026-08-21).
                n_edges = len(obj.Shape.Edges) if hasattr(obj, 'Shape') else 0
                invalid = [idx for idx in edges if not (1 <= idx <= n_edges)]
                if invalid:
                    return (
                        f"Invalid edge index/indices {invalid} for {object_name} "
                        f"({n_edges} edges, valid range 1-{n_edges})"
                    )
                fillet = doc.addObject("Part::Fillet", name)
                fillet.Base = obj
                fillet.Edges = [(idx, radius, radius) for idx in edges]

            self.recompute(doc)

            err = self._check_feature_state(fillet, "Fillet")
            if err:
                return err

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

            # Guard before touching obj.Shape.Edges so a shapeless object gets a
            # clear message, not an AttributeError from the success-line below.
            if not hasattr(obj, 'Shape') or not obj.Shape.Edges:
                return f"Object {object_name} has no edges to fillet"
            n_edges = len(obj.Shape.Edges)

            fillet = doc.addObject("Part::Fillet", name)
            fillet.Base = obj
            fillet.Edges = [(i + 1, radius, radius) for i in range(n_edges)]

            self.recompute(doc)

            err = self._check_feature_state(fillet, "Fillet")
            if err:
                return err

            return f"Created fillet: {fillet.Name} on all {n_edges} edges with radius {radius}mm"

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

            err = self._check_feature_state(chamfer, "Chamfer")
            if err:
                return err

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

            # Guard before touching obj.Shape.Edges so a shapeless object gets a
            # clear message, not an AttributeError from the success line below —
            # mirrors _create_fillet_auto. (Without this, zero-edge objects also
            # report a false "all 0 edges" success.)
            if not hasattr(obj, 'Shape') or not obj.Shape.Edges:
                return f"Object {object_name} has no edges to chamfer"
            n_edges = len(obj.Shape.Edges)

            chamfer = doc.addObject("Part::Chamfer", name)
            chamfer.Base = obj
            chamfer.Edges = [(i + 1, distance) for i in range(n_edges)]

            self.recompute(doc)

            err = self._check_feature_state(chamfer, "Chamfer")
            if err:
                return err

            return f"Created chamfer: {chamfer.Name} on all {n_edges} edges with distance {distance}mm"

        except Exception as e:
            return f"Error creating auto chamfer: {e}"

    def hole_wizard(self, args: Dict[str, Any]) -> str:
        """Create standard holes (simple, counterbore, countersink).

        Body-aware like mirror_feature/linear_pattern/polar_pattern/
        create_helix: when face_index is given and the object is in a
        Body, creates a genuine PartDesign::Hole instead of the raw CSG
        cylinder-and-boolean-cut approach. PartDesign::Hole's Profile
        must be a sketch attached to a planar face -- there's no way to
        support the old "just x,y in space, no face needed" convenience
        without a face reference, so face_index (1-based, same
        convention as datum_from_face's face_index) is required to opt
        into this path; omit it (or target a non-Body object) and
        hole_wizard behaves exactly as it always has.

        The generated sketch's local (x, y) origin is recentered to the
        selected face's own centroid (confirmed live: FlatFace
        attachment's native local origin is the underlying surface's own
        parametric origin, which for a real solid is essentially
        arbitrary and NOT the visible face's center) -- so x=0, y=0 means
        "hole at the center of the selected face", matching
        datum_from_face's "positioned at the face centroid" convention.
        """
        try:
            object_name = args.get('object_name', '')
            # hole/counterbore/countersink all dispatch here as the same
            # method (freecad_mcp_handler.py's operation_map) -- hole_type
            # used to default to 'simple' whenever the caller didn't pass
            # it explicitly, silently ignoring which of the three
            # operations was actually called (confirmed live 2026-08-21:
            # operation="counterbore" with no hole_type created a plain
            # hole). args['operation'] is the original dispatched
            # operation name, still present in this same args dict --
            # infer hole_type from it when not given explicitly, so the
            # operation name itself is authoritative instead of a
            # same-value-for-all-three default.
            hole_type = args.get('hole_type') or {
                'counterbore': 'counterbore', 'countersink': 'countersink',
            }.get(args.get('operation', ''), 'simple')
            diameter = args.get('diameter', 6)
            depth = args.get('depth', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            cb_diameter = args.get('cb_diameter', 12)
            cb_depth = args.get('cb_depth', 3)
            face_index = args.get('face_index')

            doc = self.get_document()
            if not doc:
                return "No active document"

            base_obj = self.get_object(object_name, doc)
            if not base_obj:
                return f"Object not found: {object_name}"

            body = self.find_body_for_object(base_obj, doc) if face_index is not None else None

            if body:
                hole_cut_types = {'simple': 'None', 'counterbore': 'Counterbore', 'countersink': 'Countersink'}
                cut_type = hole_cut_types.get(hole_type.lower())
                if cut_type is None:
                    return f"Invalid hole_type '{hole_type}': must be 'simple', 'counterbore', or 'countersink'"

                if not hasattr(base_obj, 'Shape'):
                    return f"Object has no Shape: {object_name}"
                faces = base_obj.Shape.Faces
                face_index = int(face_index)
                if face_index < 1 or face_index > len(faces):
                    return f"Face index {face_index} out of range (object has {len(faces)} faces)"
                face = faces[face_index - 1]
                face_ref = f"Face{face_index}"

                hole_sketch = body.newObject("Sketcher::SketchObject", f"{object_name}_HoleSketch")
                hole_sketch.AttachmentSupport = [(base_obj, face_ref)]
                hole_sketch.MapMode = 'FlatFace'
                self.recompute(doc)

                # Recenter local (0,0) to the face's own centroid --
                # FlatFace's native local origin is the underlying
                # surface's own parametric origin, not the visible face's
                # center (confirmed live: for a face at global z=10, the
                # native local origin mapped to global (0,0,10), not the
                # face's actual centroid).
                centroid = face.CenterOfMass
                local_offset = hole_sketch.Placement.Rotation.inverted().multVec(
                    centroid - hole_sketch.Placement.Base
                )
                hole_sketch.AttachmentOffset = FreeCAD.Placement(local_offset, FreeCAD.Rotation())
                self.recompute(doc)

                hole_sketch.addGeometry(Part.Circle(FreeCAD.Vector(x, y, 0), FreeCAD.Vector(0, 0, 1), diameter / 2))
                self.recompute(doc)

                hole = body.newObject("PartDesign::Hole", f"{object_name}_Hole")
                hole.Profile = hole_sketch
                hole.Diameter = diameter
                hole.DepthType = "Dimension"
                hole.Depth = depth
                # 'Angled' (a realistic drill-bit point) is FreeCAD's own
                # default, but the CSG path this Body-aware branch mirrors
                # always cut a flat-bottomed cylinder -- match that
                # geometry instead of silently changing hole shape.
                hole.DrillPoint = 'Flat'
                hole.HoleCutType = cut_type

                if cut_type == 'Counterbore':
                    hole.HoleCutDiameter = cb_diameter
                    hole.HoleCutDepth = cb_depth
                elif cut_type == 'Countersink':
                    # PartDesign::Hole parameterizes a countersink by
                    # diameter + included angle, not diameter + depth like
                    # the old CSG cone did -- derive the equivalent angle
                    # from the same cb_diameter/diameter/cb_depth inputs
                    # so callers don't need new parameters. Verified live:
                    # cb_diameter=12, diameter=6, cb_depth=3 -> 90 degrees,
                    # a standard/sane countersink angle.
                    hole.HoleCutDiameter = cb_diameter
                    hole.HoleCutCountersinkAngle = 2 * math.degrees(
                        math.atan2((cb_diameter - diameter) / 2, cb_depth)
                    )

                self.recompute(doc)

                err = self._check_feature_state(hole, "Hole")
                if err:
                    return err

                return (
                    f"Created {hole_type} hole: {diameter}mm diameter at ({x}, {y}) "
                    f"on {face_ref} of {object_name} (PartDesign::Hole in {body.Name})"
                )

            # No Body (or no face_index given) — standalone CSG cylinder
            # cut approach (unchanged).
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

            err = self._check_feature_state(cut, "Hole cut")
            if err:
                return err

            return f"Created {hole_type} hole: {diameter}mm diameter at ({x}, {y}) in {object_name}"

        except Exception as e:
            return f"Error creating hole: {e}"

    def linear_pattern(self, args: Dict[str, Any]) -> str:
        """Create linear pattern of features.

        Body-aware like mirror_feature/create_helix: creates a genuine
        PartDesign::LinearPattern (chains onto the Body's feature tree)
        when the feature is in a Body, falling back to the standalone
        doc.copyObject approach otherwise (unchanged from before).
        Confirmed live via a real FreeCAD instance that
        PartDesign::LinearPattern requires a Body-resident Originals
        member -- pointing it at a plain, non-Body object throws
        BRepCheck_Analyzer::Init() - NULL shape -- so the standalone path
        stays as the only route for non-Body features. Unlike
        revolution/groove/create_helix, there's no degenerate-axis
        restriction here: Direction references the Body's global Origin
        axes (X_Axis/Y_Axis/Z_Axis), not the feature's own local axes, so
        all three of x/y/z remain valid (matching the old standalone
        path's own unrestricted x/y/z).
        """
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

            if count < 1:
                return f"count must be >= 1 (got {count})"

            body = self.find_body_for_object(feature, doc)

            if body:
                axis_name = direction.upper() + "_Axis"
                if axis_name not in ("X_Axis", "Y_Axis", "Z_Axis"):
                    return f"Invalid direction '{direction}': must be 'x', 'y', or 'z'"
                axis_obj = next(
                    (f for f in body.Origin.OriginFeatures if f.Name == axis_name), None
                )
                if axis_obj is None:
                    return f"Body {body.Name}'s Origin has no {axis_name} feature"

                linpat = body.newObject("PartDesign::LinearPattern", name)
                linpat.Originals = [feature]
                linpat.Direction = (axis_obj, [''])
                # Length is the total span from the first to the last
                # occurrence (confirmed live: spacing=10, count=4 ->
                # Length=30 produces occurrences exactly 10mm apart) --
                # not the old per-step "spacing" value directly.
                linpat.Length = spacing * (count - 1)
                linpat.Occurrences = count

                # PartDesign pattern/transform features don't auto-update
                # Body.Tip the way Pad/Pocket/Hole/AdditiveHelix do
                # (confirmed live in the mirror_feature/create_helix work)
                # -- must set it explicitly.
                body.Tip = linpat
                self.recompute(doc)

                err = self._check_feature_state(linpat, "Linear pattern")
                if err:
                    return err

                return (
                    f"Created linear pattern: {count} instances of {feature_name} "
                    f"in {direction} direction with {spacing}mm spacing "
                    f"(PartDesign::LinearPattern in {body.Name})"
                )

            # No Body — standalone doc.copyObject approach (unchanged).
            if direction.lower() == 'x':
                direction_vector = FreeCAD.Vector(spacing, 0, 0)
            elif direction.lower() == 'y':
                direction_vector = FreeCAD.Vector(0, spacing, 0)
            elif direction.lower() == 'z':
                direction_vector = FreeCAD.Vector(0, 0, spacing)
            else:
                # No fallback vector — an unrecognized direction used to
                # silently stay at (0,0,0), stacking every copy exactly on
                # top of the original (the coincident-geometry OCCT crash
                # pathology) while still reporting success.
                return f"Invalid direction '{direction}': must be 'x', 'y', or 'z'"

            copies = []
            for i in range(1, count):
                copy = doc.copyObject(feature)
                copy.Label = f"{feature.Label}_Pattern{i}"
                offset = FreeCAD.Vector(
                    direction_vector.x * i,
                    direction_vector.y * i,
                    direction_vector.z * i
                )
                copy.Placement.Base = feature.Placement.Base.add(offset)
                copies.append(copy)

            self.recompute(doc)

            invalid = [c.Name for c in copies if 'Invalid' in getattr(c, 'State', [])]
            if invalid:
                return (f"Linear pattern created but {len(invalid)} of {count - 1} "
                        f"copies failed to compute (State=Invalid): {', '.join(invalid)}")

            return f"Created linear pattern: {count} instances of {feature_name} in {direction} direction with {spacing}mm spacing"

        except Exception as e:
            return f"Error creating linear pattern: {e}"

    def polar_pattern(self, args: Dict[str, Any]) -> str:
        """Create circular/polar pattern of features.

        Body-aware like linear_pattern/mirror_feature/create_helix:
        creates a genuine PartDesign::PolarPattern (chains onto the
        Body's feature tree) when the feature is in a Body, falling back
        to the standalone doc.copyObject approach otherwise (unchanged
        from before). Confirmed live via a real FreeCAD instance that
        PartDesign::PolarPattern requires a Body-resident Originals
        member, same as its LinearPattern/Mirrored siblings. Axis
        references the Body's global Origin axes (X_Axis/Y_Axis/Z_Axis),
        so all three of x/y/z remain valid -- there's no degenerate-axis
        restriction here, unlike revolution/groove/create_helix.
        """
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

            if count < 1:
                return f"count must be >= 1 (got {count})"

            body = self.find_body_for_object(feature, doc)

            if body:
                axis_name = axis.upper() + "_Axis"
                if axis_name not in ("X_Axis", "Y_Axis", "Z_Axis"):
                    return f"Invalid axis '{axis}': must be 'x', 'y', or 'z'"
                axis_obj = next(
                    (f for f in body.Origin.OriginFeatures if f.Name == axis_name), None
                )
                if axis_obj is None:
                    return f"Body {body.Name}'s Origin has no {axis_name} feature"

                polpat = body.newObject("PartDesign::PolarPattern", name)
                polpat.Originals = [feature]
                polpat.Axis = (axis_obj, [''])
                polpat.Angle = angle
                polpat.Occurrences = count

                # PartDesign pattern/transform features don't auto-update
                # Body.Tip the way Pad/Pocket/Hole/AdditiveHelix do
                # (confirmed live in the mirror_feature/linear_pattern
                # work) -- must set it explicitly.
                body.Tip = polpat
                self.recompute(doc)

                err = self._check_feature_state(polpat, "Polar pattern")
                if err:
                    return err

                return (
                    f"Created polar pattern: {count} instances of {feature_name} "
                    f"around {axis.upper()}-axis, {angle}° total "
                    f"(PartDesign::PolarPattern in {body.Name})"
                )

            # No Body — standalone doc.copyObject approach (unchanged).
            angle_step = angle / count

            axis_vector = FreeCAD.Vector(0, 0, 1)
            if axis.lower() == 'x':
                axis_vector = FreeCAD.Vector(1, 0, 0)
            elif axis.lower() == 'y':
                axis_vector = FreeCAD.Vector(0, 1, 0)

            copies = []
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
                copies.append(copy)

            self.recompute(doc)

            invalid = [c.Name for c in copies if 'Invalid' in getattr(c, 'State', [])]
            if invalid:
                return (f"Polar pattern created but {len(invalid)} of {count - 1} "
                        f"copies failed to compute (State=Invalid): {', '.join(invalid)}")

            return f"Created polar pattern: {count} instances of {feature_name} around {axis.upper()}-axis, {angle}° total"

        except Exception as e:
            return f"Error creating polar pattern: {e}"

    def mirror_feature(self, args: Dict[str, Any]) -> str:
        """Mirror features across a plane.

        Body-aware like create_helix/revolution/groove: creates a genuine
        PartDesign::Mirrored (chains onto the Body's feature tree) when
        the feature is in a Body, falling back to standalone
        Part::Mirroring otherwise (unchanged from before). Confirmed live
        via a real FreeCAD instance that PartDesign::Mirrored requires a
        Body-resident Originals member -- pointing it at a plain,
        non-Body object throws BRepCheck_Analyzer::Init() - NULL shape --
        so the standalone path stays as the only route for non-Body
        features, exactly as it was.
        """
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

            body = self.find_body_for_object(feature, doc)

            if body:
                plane_name = plane.upper() + "_Plane"
                origin_planes = {"XY_Plane", "XZ_Plane", "YZ_Plane"}
                if plane_name not in origin_planes:
                    return f"Invalid plane '{plane}': must be 'XY', 'XZ', or 'YZ'"
                plane_obj = next(
                    (f for f in body.Origin.OriginFeatures if f.Name == plane_name), None
                )
                if plane_obj is None:
                    return f"Body {body.Name}'s Origin has no {plane_name} feature"

                mirror = body.newObject("PartDesign::Mirrored", name)
                mirror.Originals = [feature]
                mirror.MirrorPlane = (plane_obj, [''])

                # PartDesign pattern/transform features (Mirrored,
                # LinearPattern, PolarPattern) don't auto-update Body.Tip
                # the way Pad/Pocket/Hole/AdditiveHelix do -- confirmed
                # live: Body.Shape stays the pre-mirror shape until Tip is
                # set explicitly. Without this, the mirror exists in the
                # document but the Body's own visible/reported shape
                # silently doesn't include it.
                body.Tip = mirror
                self.recompute(doc)

                err = self._check_feature_state(mirror, "Mirror")
                if err:
                    return err

                return (
                    f"Created mirror: {mirror.Name} of {feature_name} across "
                    f"{plane} plane (PartDesign::Mirrored in {body.Name})"
                )

            # No Body — standalone Part::Mirroring (unchanged).
            plane_normals = {'XY': (0, 0, 1), 'XZ': (0, 1, 0), 'YZ': (1, 0, 0)}
            normal = plane_normals.get(plane.upper())
            if normal is None:
                # Validated before creating the object — an unrecognized
                # plane used to leave mirror.Normal/Base entirely unset on
                # an already-created Part::Mirroring while still reporting
                # "Created mirror: ... across {plane} plane" as success.
                return f"Invalid plane '{plane}': must be 'XY', 'XZ', or 'YZ'"

            mirror = doc.addObject("Part::Mirroring", name)
            mirror.Source = feature
            mirror.Normal = normal
            mirror.Base = (0, 0, 0)

            self.recompute(doc)

            err = self._check_feature_state(mirror, "Mirror")
            if err:
                return err

            return f"Created mirror: {mirror.Name} of {feature_name} across {plane} plane"

        except Exception as e:
            return f"Error creating mirror: {e}"

    def _sketch_plane_normal(self, sketch) -> "FreeCAD.Vector":
        """Global-space unit normal of sketch's own plane, from its Placement.

        A Sketcher::SketchObject's 2D geometry always lies in its own local
        XY plane (local Z is always the plane's normal) — this maps that
        local normal into global coordinates via the sketch's Placement.
        """
        return sketch.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

    def _axis_is_degenerate_for_sketch(self, axis_vector, sketch) -> bool:
        """True if revolving sketch around axis_vector (a global unit vector)
        cannot sweep any volume.

        A planar profile revolved around an axis (anti)parallel to its own
        plane's normal never leaves that plane — every point stays at the
        same coordinate along the axis, tracing flat circles instead of
        sweeping a solid. Confirmed empirically: identical near-zero volume
        came back from the MCP handler, from Part::Revolution with Solid
        explicitly set, and from raw OCCT Face.revolve() bypassing FreeCAD's
        App layer entirely — the degeneracy is in the geometry requested,
        not any one layer of code.
        """
        normal = self._sketch_plane_normal(sketch)
        axis_v = FreeCAD.Vector(*axis_vector)
        return abs(normal.dot(axis_v)) > 1 - 1e-6

    def revolution(self, args: Dict[str, Any]) -> str:
        """Revolve a sketch around an axis to create solid of revolution.

        Body-aware like fillet/chamfer/thickness elsewhere in this file:
        creates a genuine PartDesign::Revolution (chains onto the Body's
        feature tree, gains Midplane/Reversed/UpToFace) when the sketch is
        in a Body, falling back to standalone Part::Revolution otherwise.
        Previously this always created Part::Revolution regardless of Body
        membership — the only one of this operation family to skip that
        check — confirmed via a live FreeCAD instance that PartDesign::
        Revolution works cleanly and computes the same correct volume.
        """
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

            body = self.find_body_for_object(sketch, doc)

            if body:
                # PartDesign::Revolution's ReferenceAxis needs a sketch-local
                # reference (H_Axis/V_Axis), not a global vector — the same
                # mapping groove() already uses. N_Axis (axis='z') is always
                # the sketch's own plane normal by construction, so it's
                # always degenerate here too, for the identical reason
                # groove() rejects it — no per-sketch computation needed.
                axis_refs = {'x': 'H_Axis', 'y': 'V_Axis'}
                ref_axis = axis_refs.get(axis.lower())
                if ref_axis is None:
                    if axis.lower() == 'z':
                        return (
                            "Invalid axis 'z': the sketch's own normal "
                            "(N_Axis) cannot be used as a revolution axis — "
                            "revolving a planar profile around its own "
                            "plane's normal always sweeps zero volume, for "
                            "any sketch. Use 'x' (H_Axis) or 'y' (V_Axis), "
                            "which lie in the sketch's plane."
                        )
                    return f"Invalid axis '{axis}': must be 'x' or 'y'"

                revolution = body.newObject("PartDesign::Revolution", name)
                revolution.Profile = sketch
                revolution.ReferenceAxis = (sketch, [ref_axis])
                revolution.Angle = angle

                self.recompute(doc)

                err = self._check_feature_state(revolution, "Revolution", sketch=sketch)
                if err:
                    return err

                return (
                    f"Created revolution: {revolution.Name} from {sketch_name} "
                    f"around {axis.upper()}-axis, {angle}° "
                    f"(PartDesign::Revolution in {body.Name})"
                )

            # No Body — standalone Part::Revolution, axis is a global vector.
            axis_vectors = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}
            axis_vector = axis_vectors.get(axis.lower())
            if axis_vector is None:
                # Validated before creating the object — an unrecognized
                # axis used to silently fall through to Z while the success
                # message still echoed the requested axis string, e.g.
                # axis="q" reporting "around Q-axis" while actually
                # revolving around Z.
                return f"Invalid axis '{axis}': must be 'x', 'y', or 'z'"

            if self._axis_is_degenerate_for_sketch(axis_vector, sketch):
                return (
                    f"Axis '{axis.upper()}' is perpendicular to {sketch_name}'s "
                    "plane (i.e. it's that plane's own normal) — revolving a "
                    "planar profile around its own plane's normal cannot sweep "
                    "any volume, since every point stays within that plane. "
                    "Choose an axis that lies within the sketch's plane instead."
                )

            revolution = doc.addObject("Part::Revolution", name)
            revolution.Source = sketch
            revolution.Angle = angle
            revolution.Axis = axis_vector
            revolution.Solid = True

            self.recompute(doc)

            err = self._check_feature_state(revolution, "Revolution", sketch=sketch)
            if err:
                return err

            return f"Created revolution: {revolution.Name} from {sketch_name} around {axis.upper()}-axis, {angle}°"

        except Exception as e:
            return f"Error creating revolution: {e}"

    def groove(self, args: Dict[str, Any]) -> str:
        """Create a groove (subtractive revolution) from a sketch."""
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'x')
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

            # Map the requested revolution axis to the sketch's local axes:
            #   x -> H_Axis (horizontal), y -> V_Axis (vertical), z -> N_Axis (normal).
            # N_Axis is excluded (see below) — unlike revolution()'s global
            # axis choice, N_Axis is *unconditionally* the sketch's own plane
            # normal for every sketch, by construction, so it can never sweep
            # volume; there's no sketch placement that makes it valid.
            axis_refs = {'x': 'H_Axis', 'y': 'V_Axis'}
            ref_axis = axis_refs.get(axis.lower())
            if ref_axis is None:
                if axis.lower() == 'z':
                    return (
                        "Invalid axis 'z': the sketch's own normal (N_Axis) "
                        "cannot be used as a groove axis — revolving a planar "
                        "profile around its own plane's normal always sweeps "
                        "zero volume, for any sketch. Use 'x' (H_Axis) or 'y' "
                        "(V_Axis), which lie in the sketch's plane."
                    )
                # Validated before creating the object — see revolution()
                # for the identical silent-default/message-lie bug this
                # mirrors.
                return f"Invalid axis '{axis}': must be 'x' or 'y'"

            groove = body.newObject("PartDesign::Groove", name)
            groove.Profile = sketch
            groove.Angle = angle
            groove.ReferenceAxis = (sketch, [ref_axis])

            self.recompute(doc)

            err = self._check_feature_state(groove, "Groove", sketch=sketch)
            if err:
                return err

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

            err = self._check_feature_state(loft, "Loft")
            if err:
                return err

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

            err = self._check_feature_state(sweep, "Sweep")
            if err:
                return err

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

            err = self._check_feature_state(pipe, "Additive pipe")
            if err:
                return err

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
            # PartDesign::SubtractiveLoft (like every other PartDesign
            # additive/subtractive feature) has its own required Profile
            # property distinct from Sections -- unlike the standalone
            # Part::Loft this method used to be modeled after, which only
            # has Sections. Leaving Profile unset (the bug: this used to
            # just do `loft.Sections = sketch_objs` with every sketch,
            # including the first) leaves the feature permanently
            # State=Invalid with a null Shape -- confirmed live, reproduced
            # identically even on the additive sibling PartDesign::AdditiveLoft
            # with no subtraction involved at all, so it was never a
            # subtraction-specific issue. Profile takes the first sketch;
            # Sections takes the rest.
            loft.Profile = sketch_objs[0]
            loft.Sections = sketch_objs[1:]

            self.recompute(doc)

            err = self._check_feature_state(loft, "Subtractive loft")
            if err:
                return err

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

            err = self._check_feature_state(pipe, "Subtractive pipe")
            if err:
                return err

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

                err = self._check_feature_state(draft, "Draft")
                if err:
                    return err

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

            # Part::Thickness has no Source property at all - confirmed live
            # it raises AttributeError unconditionally ('Part.Feature'
            # object has no attribute 'Source'). The .Faces assignment
            # below already carries the base-object reference via its
            # (object, subelement_names) tuple; this line was both invalid
            # and redundant.
            shell = doc.addObject("Part::Thickness", name)
            shell.Value = thickness
            shell.Join = 2

            if hasattr(obj, 'Shape') and obj.Shape.Faces:
                # Part::Thickness.Faces is an App::PropertyLinkSubList — it takes
                # (object, ("Face1", ...)) with 1-based FaceN sub-names, NOT raw
                # integer indices (the sibling _create_thickness_with_selection
                # does it this way). Passing ints silently produces a wrong/closed
                # shell.
                valid = [fi for fi in face_indices if 1 <= fi <= len(obj.Shape.Faces)]
                shell.Faces = (obj, tuple(f"Face{fi}" for fi in valid))

            self.recompute(doc)

            err = self._check_feature_state(shell, "Shell")
            if err:
                return err

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

            # Part::Thickness (BRepOffsetAPI_MakeThickSolid) genuinely
            # requires at least one face to remove - confirmed live that
            # zero faces raises "shape is invalid" regardless of Join mode,
            # so it can never produce a fully sealed (no-opening) shell.
            # This also means shell.Source = obj below was never valid:
            # Part::Thickness has no Source property at all - confirmed
            # live it raises AttributeError unconditionally on every call.
            # A truly closed hollow shape is instead built directly: cut an
            # inward-offset copy of the solid from itself, wrapped in a
            # generic Part::Feature. Confirmed correct live against a 20mm
            # box at 2mm wall thickness: expected 20^3 - 16^3 = 3904 mm^3,
            # got exactly that.
            inner = obj.Shape.makeOffsetShape(-thickness, 1e-3, fill=False)
            hollow_shape = obj.Shape.cut(inner)

            shell = doc.addObject("Part::Feature", name)
            shell.Shape = hollow_shape

            self.recompute(doc)

            err = self._check_feature_state(shell, "Shell")
            if err:
                return err

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

            err = self._check_feature_state(thickness, "PartDesign Thickness")
            if err:
                return err

            return f"Created PartDesign Thickness: {thickness.Name} from {object_name} with {thickness_val}mm thickness and {len(face_indices)} face(s) removed for opening"

        except Exception as e:
            return f"Error creating thickness with selection: {e}"

    def create_helix(self, args: Dict[str, Any]) -> str:
        """Create helical features (threads, springs).

        Body-aware like revolution()/groove(): creates a genuine
        PartDesign::AdditiveHelix (chains onto the Body's feature tree,
        gains LeftHanded/Reversed/Midplane) when the sketch is in a Body,
        falling back to the standalone Part::Sweep-along-a-computed-path
        approach otherwise (unchanged from before -- still the only route
        for a sketch with no Body).

        Previously this always took the standalone path regardless of
        Body membership, on the belief that no parametric FreeCAD type
        supports LeftHanded. That's true of Part::Helix (the primitive
        curve type the standalone path still uses below) but NOT of
        PartDesign::AdditiveHelix -- confirmed via a live FreeCAD instance
        that it has a real LeftHanded property and produces a valid shape.
        """
        try:
            sketch_name = args.get('sketch_name', '')
            axis = args.get('axis', 'z')
            pitch = args.get('pitch', 2)
            height = args.get('height')
            turns = args.get('turns')
            # A helix is pitch + (turns OR height). turns was read but never used,
            # so height ignored it and the success message lied. If turns is given,
            # it drives the height (height = pitch * turns); otherwise derive turns
            # from height so the reported value is real.
            turns_given = turns is not None
            if turns_given:
                height = pitch * turns
            else:
                if height is None:
                    height = 10
                turns = round(height / pitch, 2) if pitch else 0
            left_handed = args.get('left_handed', False)
            name = args.get('name', 'Helix')

            doc = self.get_document()
            if not doc:
                return "No active document"

            sketch = self.get_object(sketch_name, doc)
            if not sketch:
                return f"Sketch not found: {sketch_name}"

            body = self.find_body_for_object(sketch, doc)

            if body:
                # Same in-plane-axis mapping as revolution()/groove(): a
                # helix's ReferenceAxis must lie in the sketch's own plane
                # -- geometrically a helix is a revolution with
                # progressive translation added along that same axis.
                # N_Axis (the sketch's own normal) is degenerate for the
                # identical reason it is for revolution/groove: winding a
                # planar profile around its own plane's normal sweeps zero
                # volume, for any sketch.
                axis_refs = {'x': 'H_Axis', 'y': 'V_Axis'}
                ref_axis = axis_refs.get(axis.lower())
                if ref_axis is None:
                    if axis.lower() == 'z':
                        return (
                            "Invalid axis 'z': the sketch's own normal "
                            "(N_Axis) cannot be used as a helix axis — "
                            "winding a planar profile around its own "
                            "plane's normal always sweeps zero volume, for "
                            "any sketch. Use 'x' (H_Axis) or 'y' (V_Axis), "
                            "which lie in the sketch's plane."
                        )
                    return f"Invalid axis '{axis}': must be 'x' or 'y'"

                helix = body.newObject("PartDesign::AdditiveHelix", name)
                helix.Profile = sketch
                helix.ReferenceAxis = (sketch, [ref_axis])
                # Mode must be set before Pitch/Turns/Height -- confirmed
                # live that AdditiveHelix only treats the Mode-selected
                # pair as live input; the third value is a read-only
                # derived output, and setting Turns/Height while Mode
                # still pointed at the other pair silently no-ops.
                if turns_given:
                    helix.Mode = 'pitch-turns-angle'
                    helix.Pitch = pitch
                    helix.Turns = turns
                else:
                    helix.Mode = 'pitch-height-angle'
                    helix.Pitch = pitch
                    helix.Height = height
                helix.LeftHanded = left_handed

                self.recompute(doc)

                err = self._check_feature_state(helix, "Helix", sketch=sketch)
                if err:
                    return err

                return (
                    f"Created helix: {helix.Name} from {sketch_name} around "
                    f"{axis.upper()}-axis, pitch={pitch}mm, height={height}mm, "
                    f"turns={turns} (PartDesign::AdditiveHelix in {body.Name})"
                )

            # No Body — standalone Part::Sweep-along-computed-path (unchanged).
            # Part::Helix (the parametric document-object type) has no
            # LeftHanded property at all - setting it raised AttributeError
            # unconditionally on every call ('PrimitivePy' object has no
            # attribute 'LeftHanded'), confirmed against a real FreeCAD
            # instance. Handedness is only exposed on the plain shape-
            # computing function Part.makeLongHelix(pitch, height, radius,
            # angle, hand), so the curve is built as a computed shape
            # wrapped in a generic Part::Feature instead - the same
            # wrap-a-computed-shape pattern already used elsewhere in this
            # file (e.g. the compound-shape helpers), and Part::Sweep's
            # Spine only needs an object with a .Shape, parametric or not.
            helix_shape = Part.makeLongHelix(pitch, height, 10, 0, left_handed)
            helix_curve = doc.addObject("Part::Feature", f"{name}_Path")
            helix_curve.Shape = helix_shape

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

            err = self._check_feature_state(helix_sweep, "Helix")
            if err:
                return err

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

            # "Part::Extrude" is not a real FreeCAD document-object type -
            # doc.addObject raised "not a document object type" on every
            # call. Confirmed live: the real type is "Part::Extrusion",
            # which has the exact same Base/Dir/LengthFwd/Solid properties
            # this method already sets.
            rib = doc.addObject("Part::Extrusion", name)
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

            err = self._check_feature_state(rib, "Rib")
            if err:
                return err

            return f"Created rib: {rib.Name} from {sketch_name} with {thickness}mm thickness in {direction} direction"

        except Exception as e:
            return f"Error creating rib: {e}"

    # -----------------------------------------------------------------
    # Datum features
    # -----------------------------------------------------------------

    def create_datum_plane(self, args: Dict[str, Any]) -> str:
        """Create a datum plane in the active PartDesign Body.

        A datum plane provides a reference surface for sketching at
        arbitrary positions/orientations in the body. Common uses:
        - Offset from a face: map_mode="FlatFace", reference="FaceN", offset_z=10
        - Standard plane: map_mode="ObjectXY" (or ObjectXZ, ObjectYZ)
        - Through three points, edge and point, etc.

        Args:
            name: Name for the datum plane (default "DatumPlane")
            map_mode: Attachment mode (default "FlatFace"). Common modes:
                FlatFace, ObjectXY, ObjectXZ, ObjectYZ, Translate, NormalToEdge
            reference: Face or edge reference, e.g. "Face1", "Edge3"
            reference_object: Object name containing the reference (default: tip feature)
            offset_x: Offset in X from attached position (mm)
            offset_y: Offset in Y from attached position (mm)
            offset_z: Offset in Z / normal direction (mm)
        """
        try:
            name = args.get('name', 'DatumPlane')
            map_mode = args.get('map_mode', 'FlatFace')
            reference = args.get('reference', '')
            reference_object = args.get('reference_object', '')
            offset_x = args.get('offset_x', 0)
            offset_y = args.get('offset_y', 0)
            offset_z = args.get('offset_z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            body = self.create_body_if_needed(doc)
            if not body:
                return "Could not find or create PartDesign Body"

            dp = body.newObject('PartDesign::Plane', name)

            # Set attachment mode
            dp.MapMode = map_mode

            # Set reference if provided
            if reference:
                ref_obj = None
                if reference_object:
                    ref_obj = self.get_object(reference_object, doc)
                else:
                    # Default: use the tip (most recent feature) of the body
                    if body.Tip:
                        ref_obj = body.Tip

                if ref_obj:
                    dp.AttachmentSupport = [(ref_obj, reference)]

            # Set offset
            if offset_x or offset_y or offset_z:
                dp.AttachmentOffset = FreeCAD.Placement(
                    FreeCAD.Vector(offset_x, offset_y, offset_z),
                    FreeCAD.Rotation(0, 0, 0, 1)
                )

            self.recompute(doc)

            return (f"Created datum plane: {dp.Name}, mode={map_mode}"
                    + (f", reference={reference}" if reference else "")
                    + (f", offset=({offset_x},{offset_y},{offset_z})" if any([offset_x, offset_y, offset_z]) else ""))

        except Exception as e:
            return f"Error creating datum plane: {e}"

    def create_datum_line(self, args: Dict[str, Any]) -> str:
        """Create a datum line (axis) in the active PartDesign Body.

        A datum line provides a reference axis for patterns, revolutions, etc.

        Args:
            name: Name for the datum line (default "DatumLine")
            map_mode: Attachment mode (default "ObjectX"). Common modes:
                ObjectX, ObjectY, ObjectZ, TwoPointLine, NormalToFace, Intersection
            reference: Edge or face reference, e.g. "Edge1", "Face3"
            reference_object: Object name containing the reference
        """
        try:
            name = args.get('name', 'DatumLine')
            map_mode = args.get('map_mode', 'ObjectX')
            reference = args.get('reference', '')
            reference_object = args.get('reference_object', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            body = self.create_body_if_needed(doc)
            if not body:
                return "Could not find or create PartDesign Body"

            dl = body.newObject('PartDesign::Line', name)
            dl.MapMode = map_mode

            if reference:
                ref_obj = None
                if reference_object:
                    ref_obj = self.get_object(reference_object, doc)
                elif body.Tip:
                    ref_obj = body.Tip

                if ref_obj:
                    dl.AttachmentSupport = [(ref_obj, reference)]

            self.recompute(doc)

            return (f"Created datum line: {dl.Name}, mode={map_mode}"
                    + (f", reference={reference}" if reference else ""))

        except Exception as e:
            return f"Error creating datum line: {e}"

    def create_datum_point(self, args: Dict[str, Any]) -> str:
        """Create a datum point in the active PartDesign Body.

        A datum point provides a reference location for constraints,
        attachments, and measurements.

        Args:
            name: Name for the datum point (default "DatumPoint")
            map_mode: Attachment mode (default "ObjectOrigin"). Common modes:
                ObjectOrigin, CenterOfCurvature, CenterOfMass, ProximityPoint1
            reference: Reference element, e.g. "Vertex1", "Edge5", "Face2"
            reference_object: Object name containing the reference
            offset_x: Offset in X from attached position (mm)
            offset_y: Offset in Y from attached position (mm)
            offset_z: Offset in Z from attached position (mm)
        """
        try:
            name = args.get('name', 'DatumPoint')
            map_mode = args.get('map_mode', 'ObjectOrigin')
            reference = args.get('reference', '')
            reference_object = args.get('reference_object', '')
            offset_x = args.get('offset_x', 0)
            offset_y = args.get('offset_y', 0)
            offset_z = args.get('offset_z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            body = self.create_body_if_needed(doc)
            if not body:
                return "Could not find or create PartDesign Body"

            dp = body.newObject('PartDesign::Point', name)
            dp.MapMode = map_mode

            if reference:
                ref_obj = None
                if reference_object:
                    ref_obj = self.get_object(reference_object, doc)
                elif body.Tip:
                    ref_obj = body.Tip

                if ref_obj:
                    dp.AttachmentSupport = [(ref_obj, reference)]

            if offset_x or offset_y or offset_z:
                dp.AttachmentOffset = FreeCAD.Placement(
                    FreeCAD.Vector(offset_x, offset_y, offset_z),
                    FreeCAD.Rotation(0, 0, 0, 1)
                )

            self.recompute(doc)

            return (f"Created datum point: {dp.Name}, mode={map_mode}"
                    + (f", reference={reference}" if reference else ""))

        except Exception as e:
            return f"Error creating datum point: {e}"

    def datum_from_face(self, args: Dict[str, Any]) -> str:
        """Create a datum plane aligned to a specific face of an object.

        Shortcut that combines list_faces + create_datum_plane: given an object
        and a face index (1-based, as returned by measurement_operations/list_faces),
        creates a datum plane positioned at the face centroid oriented to the face normal.

        Args:
            object_name: Name of the object containing the face
            face_index: 1-based face index (from list_faces output, e.g. 3 for Face3)
            name: Name for the new datum plane (default: 'Datum_<FaceN>')
            offset: Additional offset along the face normal in mm (default 0)
        """
        try:
            object_name = args.get('object_name', '')
            face_index = int(args.get('face_index', 1))
            name = args.get('name', '')
            offset = float(args.get('offset', 0))

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"
            if not hasattr(obj, 'Shape'):
                return f"Object has no Shape: {object_name}"

            faces = obj.Shape.Faces
            if face_index < 1 or face_index > len(faces):
                return f"Face index {face_index} out of range (object has {len(faces)} faces)"

            face = faces[face_index - 1]
            face_ref = f"Face{face_index}"

            # Delegate to create_datum_plane with FlatFace attachment
            if not name:
                name = f"Datum_{face_ref}"

            new_args = {
                'name': name,
                'map_mode': 'FlatFace',
                'reference': face_ref,
                'reference_object': object_name,
                'offset_z': offset,
            }
            result = self.create_datum_plane(new_args)

            # Append face geometry info
            try:
                c = face.CenterOfMass
                n = face.normalAt(0, 0)
                result += (f"\n  Face centroid: ({c.x:.2f}, {c.y:.2f}, {c.z:.2f})"
                           f"\n  Face normal: ({n.x:+.2f}, {n.y:+.2f}, {n.z:+.2f})"
                           f"\n  Face area: {face.Area:.2f} mm²")
            except Exception:
                pass

            return result

        except Exception as e:
            return f"Error creating datum from face: {e}"

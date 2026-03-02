# Spatial relationship query handlers for FreeCAD MCP
#
# Answers questions about how two or more objects relate in 3D space:
# interference, clearance, containment, face relationships, batch checks.
#
# All operations are read-only — they inspect geometry without modifying it.
# This handler can live standalone or be folded into measurement_ops later;
# every method takes (self, args) and returns a string, same as all handlers.

import json
import math
import FreeCAD
from typing import Dict, Any, List, Tuple
from .base import BaseHandler

# OCCT Precision::Confusion() — the linear tolerance below which OCCT's boolean
# operations consider two points identical.  Overlaps thinner than this in any
# dimension may be silently dropped by common() and returned as zero volume.
# We use this as the distance threshold for sliver detection.
_OCCT_LIN_TOL = 1e-7   # mm

# Volume reporting threshold: anything below this is treated as zero.
# This filters floating-point noise from OCCT boolean results.
# 1e-9 mm³ ≈ (0.001 mm)³ — well below any manufacturable feature but safely
# above numerical noise.  OCCT may drop real overlaps before we see them if
# they are smaller than _OCCT_LIN_TOL in any dimension.
_VOL_TOL = 1e-9   # mm³


class SpatialOpsHandler(BaseHandler):
    """Handler for spatial relationship queries between objects."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_two_shapes(self, args: Dict[str, Any]):
        """Extract and validate two shapes from args.

        Returns (shape1, shape2, name1, name2) or (None, None, None, error_string).
        """
        obj1_name = args.get('object1', '') or args.get('obj1', '')
        obj2_name = args.get('object2', '') or args.get('obj2', '')

        if not obj1_name or not obj2_name:
            return None, None, None, "Both object1 and object2 are required"

        doc = self.get_document()
        if not doc:
            return None, None, None, "No active document"

        obj1 = self.get_object(obj1_name, doc)
        obj2 = self.get_object(obj2_name, doc)

        if not obj1:
            return None, None, None, f"Object not found: {obj1_name}"
        if not obj2:
            return None, None, None, f"Object not found: {obj2_name}"

        if not hasattr(obj1, 'Shape'):
            return None, None, None, f"{obj1_name} has no Shape"
        if not hasattr(obj2, 'Shape'):
            return None, None, None, f"{obj2_name} has no Shape"

        return obj1.Shape, obj2.Shape, obj1_name, obj2_name

    def _solid_warning(self, shape, name: str) -> str:
        """Return a warning string if shape has no solids, else empty string."""
        if not shape.Solids:
            stype = shape.ShapeType
            return f"  WARNING: {name} is a {stype} (not a solid) — interference volume may be zero even if shapes overlap"
        return ""

    def _fmt_vec(self, v, decimals=2) -> str:
        """Format a FreeCAD Vector as a compact string."""
        return f"({v.x:.{decimals}f}, {v.y:.{decimals}f}, {v.z:.{decimals}f})"

    def _fmt_bb(self, bb, decimals=2) -> str:
        """Format a BoundBox as ranges."""
        d = decimals
        return (f"X[{bb.XMin:.{d}f}, {bb.XMax:.{d}f}] "
                f"Y[{bb.YMin:.{d}f}, {bb.YMax:.{d}f}] "
                f"Z[{bb.ZMin:.{d}f}, {bb.ZMax:.{d}f}]")

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def interference_check(self, args: Dict[str, Any]) -> str:
        """Check whether two objects intersect (collide).

        Returns intersection volume and bounding box of the intersection.
        """
        try:
            s1, s2, n1, n2 = self._get_two_shapes(args)
            if s1 is None:
                return n2  # error string

            common = s1.common(s2)
            vol = common.Volume
            intersects = vol > _VOL_TOL

            lines = [f"Interference check: {n1} vs {n2}"]
            for w in [self._solid_warning(s1, n1), self._solid_warning(s2, n2)]:
                if w:
                    lines.append(w)
            lines.append(f"  Intersects: {intersects}")

            if intersects:
                lines.append(f"  Intersection volume: {vol:.4f} mm³")
                bb = common.BoundBox
                lines.append(f"  Intersection bounds: {self._fmt_bb(bb)}")
                lines.append(f"  Intersection size: "
                             f"{bb.XLength:.2f} × {bb.YLength:.2f} × {bb.ZLength:.2f} mm")
            else:
                # Report minimum distance when no intersection
                dist_result = s1.distToShape(s2)
                min_dist = dist_result[0]
                lines.append(f"  Minimum clearance: {min_dist:.4f} mm")
                # Sliver detection: BB overlaps but common() returned zero volume.
                # If distance is also sub-tolerance, OCCT may have silently dropped
                # a real contact thinner than Precision::Confusion() (~1e-7 mm).
                if s1.BoundBox.intersect(s2.BoundBox) and min_dist < _OCCT_LIN_TOL:
                    lines.append(f"  WARNING: bounding boxes overlap and distance is "
                                 f"~{min_dist:.2e} mm — possible sub-tolerance sliver "
                                 f"below OCCT detection threshold ({_OCCT_LIN_TOL:.0e} mm)")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in interference_check: {e}"

    def clearance(self, args: Dict[str, Any]) -> str:
        """Measure minimum distance between two objects.

        Returns distance, closest point pairs, and gap direction.
        """
        try:
            s1, s2, n1, n2 = self._get_two_shapes(args)
            if s1 is None:
                return n2

            dist_result = s1.distToShape(s2)
            min_dist = dist_result[0]
            point_pairs = dist_result[1]

            lines = [f"Clearance: {n1} vs {n2}"]
            lines.append(f"  Minimum distance: {min_dist:.4f} mm")

            if min_dist < _VOL_TOL:
                lines.append(f"  Status: TOUCHING (zero clearance)")
            else:
                lines.append(f"  Status: {min_dist:.4f} mm gap")

            # Report closest point pairs (limit to first 4)
            if point_pairs:
                lines.append(f"  Closest point pairs ({len(point_pairs)} total):")
                for i, (p1, p2) in enumerate(point_pairs[:4]):
                    gap_vec = FreeCAD.Vector(p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)
                    lines.append(f"    [{i+1}] {n1}={self._fmt_vec(p1)} → "
                                 f"{n2}={self._fmt_vec(p2)}  "
                                 f"gap=({gap_vec.x:+.3f}, {gap_vec.y:+.3f}, {gap_vec.z:+.3f})")
                if len(point_pairs) > 4:
                    lines.append(f"    ... and {len(point_pairs) - 4} more")

            # Determine dominant gap direction from first pair
            if point_pairs and min_dist > _VOL_TOL:
                p1, p2 = point_pairs[0]
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                dz = abs(p2.z - p1.z)
                dominant = max(('X', dx), ('Y', dy), ('Z', dz), key=lambda t: t[1])
                lines.append(f"  Dominant gap axis: {dominant[0]} ({dominant[1]:.4f} mm)")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in clearance: {e}"

    def containment(self, args: Dict[str, Any]) -> str:
        """Check if one object is fully contained within another.

        Tests both bounding-box containment and actual geometric containment.
        Reports overhang per axis if not contained.
        """
        try:
            s1, s2, n1, n2 = self._get_two_shapes(args)
            if s1 is None:
                return n2

            # Convention: object1 = inner (should be contained), object2 = outer (container)
            inner_name, outer_name = n1, n2
            inner_bb, outer_bb = s1.BoundBox, s2.BoundBox

            lines = [f"Containment: is {inner_name} inside {outer_name}?"]

            # Bounding box containment check
            bb_contained = (outer_bb.XMin <= inner_bb.XMin and inner_bb.XMax <= outer_bb.XMax
                            and outer_bb.YMin <= inner_bb.YMin and inner_bb.YMax <= outer_bb.YMax
                            and outer_bb.ZMin <= inner_bb.ZMin and inner_bb.ZMax <= outer_bb.ZMax)

            lines.append(f"  Bounding box contained: {bb_contained}")

            # Per-axis overhang
            overhangs = {
                'X-': max(0, outer_bb.XMin - inner_bb.XMin),
                'X+': max(0, inner_bb.XMax - outer_bb.XMax),
                'Y-': max(0, outer_bb.YMin - inner_bb.YMin),
                'Y+': max(0, inner_bb.YMax - outer_bb.YMax),
                'Z-': max(0, outer_bb.ZMin - inner_bb.ZMin),
                'Z+': max(0, inner_bb.ZMax - outer_bb.ZMax),
            }

            any_overhang = any(v > _VOL_TOL for v in overhangs.values())
            if any_overhang:
                lines.append(f"  Overhangs:")
                for axis, val in overhangs.items():
                    if val > _VOL_TOL:
                        lines.append(f"    {axis}: {val:.4f} mm")
            else:
                lines.append(f"  No bounding-box overhang")

            # Geometric containment: inner cut by outer should have same volume as inner
            if bb_contained:
                common = s1.common(s2)
                inner_vol = s1.Volume
                common_vol = common.Volume
                geom_contained = abs(inner_vol - common_vol) < max(1e-6, inner_vol * 1e-6)
                lines.append(f"  Geometric containment: {geom_contained}")
                if not geom_contained:
                    protruding_vol = inner_vol - common_vol
                    lines.append(f"  Protruding volume: {protruding_vol:.4f} mm³")

            lines.append(f"  {inner_name} bounds: {self._fmt_bb(inner_bb)}")
            lines.append(f"  {outer_name} bounds: {self._fmt_bb(outer_bb)}")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in containment: {e}"

    def face_relationship(self, args: Dict[str, Any]) -> str:
        """Analyze relationship between two specific faces on two objects.

        Checks if faces are coplanar, parallel, or at an angle.
        Reports distance between face planes and overlap area.
        """
        try:
            s1, s2, n1, n2 = self._get_two_shapes(args)
            if s1 is None:
                return n2

            face1_id = args.get('face1', '')
            face2_id = args.get('face2', '')
            if not face1_id or not face2_id:
                return "Both face1 and face2 are required (e.g., 'Face1', 'Face6')"

            # Extract face index from "FaceN" string
            try:
                idx1 = int(face1_id.replace('Face', '')) - 1
                idx2 = int(face2_id.replace('Face', '')) - 1
                f1 = s1.Faces[idx1]
                f2 = s2.Faces[idx2]
            except (ValueError, IndexError) as e:
                return f"Invalid face reference: {e}"

            n1_vec = f1.normalAt(0, 0)
            n2_vec = f2.normalAt(0, 0)
            c1 = f1.CenterOfMass
            c2 = f2.CenterOfMass

            # Angle between normals
            dot = n1_vec.x * n2_vec.x + n1_vec.y * n2_vec.y + n1_vec.z * n2_vec.z
            dot = max(-1.0, min(1.0, dot))  # clamp for acos safety
            angle_rad = math.acos(abs(dot))
            angle_deg = math.degrees(angle_rad)

            # Check relationships
            parallel = angle_deg < 0.1  # < 0.1° = parallel
            facing = dot < -0.999  # normals opposing = faces face each other
            same_dir = dot > 0.999  # normals same direction

            lines = [f"Face relationship: {n1}.{face1_id} vs {n2}.{face2_id}"]
            lines.append(f"  {n1}.{face1_id}: normal={self._fmt_vec(n1_vec, 4)} "
                         f"center={self._fmt_vec(c1)} area={f1.Area:.2f}mm²")
            lines.append(f"  {n2}.{face2_id}: normal={self._fmt_vec(n2_vec, 4)} "
                         f"center={self._fmt_vec(c2)} area={f2.Area:.2f}mm²")
            lines.append(f"  Angle between normals: {angle_deg:.2f}°")
            lines.append(f"  Parallel: {parallel}")

            if parallel:
                lines.append(f"  Facing each other: {facing}")
                lines.append(f"  Same direction: {same_dir}")

                # Distance between face planes (project center-to-center onto normal)
                center_vec = FreeCAD.Vector(c2.x - c1.x, c2.y - c1.y, c2.z - c1.z)
                plane_dist = abs(center_vec.x * n1_vec.x + center_vec.y * n1_vec.y + center_vec.z * n1_vec.z)
                lines.append(f"  Plane distance: {plane_dist:.4f} mm")

                coplanar = plane_dist < 0.01
                lines.append(f"  Coplanar: {coplanar}")

            # Try to compute overlap area via section
            try:
                section = f1.section(f2)
                if section.Edges:
                    # Section produced edges — faces share area
                    try:
                        # Try to build a face from the section wires
                        import Part
                        if section.Wires:
                            overlap_face = Part.Face(section.Wires[0])
                            lines.append(f"  Overlap area: {overlap_face.Area:.4f} mm²")
                        else:
                            lines.append(f"  Overlap: edges found ({len(section.Edges)}) but no closed wire")
                    except Exception:
                        lines.append(f"  Overlap: {len(section.Edges)} shared edges (area computation failed)")
                else:
                    lines.append(f"  Overlap: none (faces don't share area)")
            except Exception:
                lines.append(f"  Overlap: could not compute")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in face_relationship: {e}"

    def batch_interference(self, args: Dict[str, Any]) -> str:
        """Check all pairs from a list of objects for interference.

        Returns all colliding pairs with intersection volumes.
        """
        try:
            object_names = args.get('objects', [])
            if not object_names or len(object_names) < 2:
                return "Provide a list of at least 2 object names in 'objects'"

            doc = self.get_document()
            if not doc:
                return "No active document"

            # Resolve all objects, collecting non-solid warnings
            shapes = {}
            warnings = []
            for name in object_names:
                obj = self.get_object(name, doc)
                if not obj:
                    return f"Object not found: {name}"
                if not hasattr(obj, 'Shape'):
                    return f"{name} has no Shape"
                shapes[name] = obj.Shape
                w = self._solid_warning(obj.Shape, name)
                if w:
                    warnings.append(w)

            names = list(shapes.keys())
            collisions = []
            clear_pairs = 0

            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    n1, n2 = names[i], names[j]
                    # Quick bounding box pre-check
                    if not shapes[n1].BoundBox.intersect(shapes[n2].BoundBox):
                        clear_pairs += 1
                        continue
                    common = shapes[n1].common(shapes[n2])
                    vol = common.Volume
                    if vol > _VOL_TOL:
                        collisions.append((n1, n2, vol))
                    else:
                        # Sliver detection: BB overlaps but common() returned zero.
                        # Check if shapes are actually touching (sub-tolerance contact).
                        try:
                            dist = shapes[n1].distToShape(shapes[n2])[0]
                            if dist < _OCCT_LIN_TOL:
                                collisions.append((n1, n2, 0.0, "SUB-TOL"))
                            else:
                                clear_pairs += 1
                        except Exception:
                            clear_pairs += 1

            total_pairs = len(names) * (len(names) - 1) // 2
            lines = [f"Batch interference: {len(names)} objects, {total_pairs} pairs checked"]
            lines.extend(warnings)
            lines.append(f"  Collisions: {len(collisions)}")
            lines.append(f"  Clear: {clear_pairs}")

            if collisions:
                lines.append(f"  Colliding pairs:")
                for entry in collisions:
                    if len(entry) == 4 and entry[3] == "SUB-TOL":
                        n1, n2 = entry[0], entry[1]
                        lines.append(f"    {n1} ↔ {n2}: POSSIBLE SUB-TOLERANCE CONTACT "
                                     f"(BB overlap, zero volume — sliver below OCCT threshold)")
                    else:
                        n1, n2, vol = entry
                        lines.append(f"    {n1} ↔ {n2}: {vol:.4f} mm³")
            else:
                lines.append(f"  No collisions detected")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in batch_interference: {e}"

    def alignment_check(self, args: Dict[str, Any]) -> str:
        """Check how well two objects are aligned along a given axis.

        Reports center-of-mass offset and angular misalignment.
        """
        try:
            s1, s2, n1, n2 = self._get_two_shapes(args)
            if s1 is None:
                return n2

            axis = args.get('axis', 'Z').upper()
            if axis not in ('X', 'Y', 'Z'):
                return "axis must be 'X', 'Y', or 'Z'"

            c1 = s1.CenterOfMass
            c2 = s2.CenterOfMass

            # Offset perpendicular to the specified axis
            if axis == 'X':
                lateral_offset = math.sqrt((c2.y - c1.y)**2 + (c2.z - c1.z)**2)
                axial_offset = c2.x - c1.x
                perp_desc = "YZ"
            elif axis == 'Y':
                lateral_offset = math.sqrt((c2.x - c1.x)**2 + (c2.z - c1.z)**2)
                axial_offset = c2.y - c1.y
                perp_desc = "XZ"
            else:  # Z
                lateral_offset = math.sqrt((c2.x - c1.x)**2 + (c2.y - c1.y)**2)
                axial_offset = c2.z - c1.z
                perp_desc = "XY"

            lines = [f"Alignment check: {n1} vs {n2} along {axis} axis"]
            lines.append(f"  {n1} center: {self._fmt_vec(c1)}")
            lines.append(f"  {n2} center: {self._fmt_vec(c2)}")
            lines.append(f"  Axial offset ({axis}): {axial_offset:+.4f} mm")
            lines.append(f"  Lateral offset ({perp_desc}): {lateral_offset:.4f} mm")

            if lateral_offset < 0.01:
                lines.append(f"  Status: ALIGNED (< 0.01mm lateral offset)")
            elif lateral_offset < 0.1:
                lines.append(f"  Status: NEARLY ALIGNED (< 0.1mm)")
            else:
                lines.append(f"  Status: MISALIGNED by {lateral_offset:.4f} mm")

            # Bounding box center alignment (sometimes more useful than CoM)
            bb1, bb2 = s1.BoundBox, s2.BoundBox
            bbc1 = FreeCAD.Vector((bb1.XMin + bb1.XMax) / 2,
                                  (bb1.YMin + bb1.YMax) / 2,
                                  (bb1.ZMin + bb1.ZMax) / 2)
            bbc2 = FreeCAD.Vector((bb2.XMin + bb2.XMax) / 2,
                                  (bb2.YMin + bb2.YMax) / 2,
                                  (bb2.ZMin + bb2.ZMax) / 2)
            bb_offset = FreeCAD.Vector(bbc2.x - bbc1.x, bbc2.y - bbc1.y, bbc2.z - bbc1.z)
            lines.append(f"  BBox center offset: {self._fmt_vec(bb_offset)}")

            return "\n".join(lines)

        except Exception as e:
            return f"Error in alignment_check: {e}"

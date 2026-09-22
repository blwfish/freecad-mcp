# Measurement operation handlers for FreeCAD MCP

import re
import FreeCAD
from typing import Any, Dict
from .base import BaseHandler


class MeasurementOpsHandler(BaseHandler):
    """Handler for measurement and analysis operations."""

    _ALLOWED_OPERATIONS = frozenset({
        "measure_distance", "get_volume", "get_bounding_box", "get_mass_properties",
        "get_surface_area", "get_center_of_mass", "count_elements", "list_faces",
        "check_solid", "diagnose_invalid_shape", "find_root_cause",
    })

    # OCCT's own enum-like BOP error class names (e.g. "BOPAlgo_SelfIntersect"),
    # embedded in the text shape.check(True) raises on failure as
    # "Error in <ElementType>: <ErrorClass>" lines. Extracting this token is
    # reading a structured identifier out of an exception message, not
    # inferring meaning from prose -- see CLAUDE.md's "No Log Scraping" rule.
    # The class name itself is NOT reliably one whitespace-free token --
    # confirmed live 2026-09-15: the same FreeCAD build raises
    # "BOPAlgo_InvalidCurveOnSurface" (underscore) from one call site and
    # "BOPAlgo SelfIntersect" (space) from another -- so this captures the
    # whole rest of the line rather than stopping at the first space, or
    # "BOPAlgo SelfIntersect" silently truncates to just "BOPAlgo" and
    # collides with every other space-separated class.
    _BOP_ERROR_LINE = re.compile(r'Error in \w+:\s*(.+?)\s*$')

    def diagnose_invalid_shape(self, args: Dict[str, Any]) -> str:
        """Check whether object_name's Shape is topologically valid
        (Shape.isValid(), OCCT's BRepCheck_Analyzer) and, if not, walk its
        dependency graph back to every upstream sketch and run a full
        sketch_operations(operation="health_check") against each.

        This exists because a shape can be silently invalid with zero
        visible symptoms at the object that's actually broken -- no error,
        no tree marker, no console line, Shape.isValid() just quietly False
        -- and the first sign of trouble is often several features
        downstream and out of context (a boolean op failing with an
        unrelated-looking error, or a Measure tool throwing an opaque OCCT
        message when a specific face is selected). Run this on whatever
        object the symptom actually showed up on; it traces backward from
        there rather than requiring the caller to already know which sketch
        to suspect.
        """
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            shape = obj.Shape
            if shape.isNull():
                return f"{object_name}: Shape is null (recompute likely failed) -- nothing to check"

            is_valid = shape.isValid()
            lines = [f"{object_name}: Shape.isValid() = {is_valid}"]

            if is_valid:
                lines.append("No further diagnosis needed.")
                return "\n".join(lines)

            sketches = self._find_upstream_sketches(obj)
            if not sketches:
                lines.append(
                    "\nShape is invalid, but no Sketcher::SketchObject was found "
                    "upstream in the dependency graph -- the cause isn't a sketch "
                    "profile. Check the feature's own parameters/geometry directly."
                )
                return "\n".join(lines)

            lines.append(
                f"\nWalking {len(sketches)} upstream sketch(es) for the likely cause:"
            )
            for sk in sketches:
                lines.append(f"\n{'=' * 60}")
                lines.append(self._sketch_health_check(sk))

            return "\n".join(lines)

        except Exception as e:
            return f"Error diagnosing invalid shape: {e}"

    def _classify_shape_findings(self, obj) -> Dict[str, Any]:
        """Return every anomaly obj's OWN Shape shows, independent of its
        inputs. Empty dict means clean. Keys are structured kinds:
          'sketch_open_wire' -- str diagnosis (Sketcher::SketchObject only)
          'null_shape'       -- str (Shape.isNull())
          'invalid_topology' -- str (Shape.isValid() False)
          'not_solid'        -- str (has Faces but zero Solids -- a Shell
                                 or Face masquerading as a solid feature)
          'bop_errors'       -- {error_class: count}, parsed from
                                 shape.check(True) -- self-intersections,
                                 invalid-curve-on-surface, etc.

        Checks every anomaly independently rather than stopping at the
        first one found -- a single object can be simultaneously not-solid
        AND carrying inherited BOP errors, and find_root_cause needs both
        signals to attribute each to the right place in the tree.
        """
        findings: Dict[str, Any] = {}

        if obj.TypeId == 'Sketcher::SketchObject':
            open_diag = self._diagnose_open_wires(obj)
            if open_diag.strip():
                findings['sketch_open_wire'] = open_diag
            return findings

        shape = getattr(obj, 'Shape', None)
        if shape is None:
            return findings
        if shape.isNull():
            findings['null_shape'] = "Shape is null (recompute likely failed)"
            return findings

        if not shape.isValid():
            findings['invalid_topology'] = "Shape.isValid() == False (BRepCheck_Analyzer failure)"

        if len(shape.Faces) > 0 and len(shape.Solids) == 0:
            findings['not_solid'] = (
                f"{len(shape.Faces)} face(s) / {len(shape.Shells)} shell(s) "
                f"but 0 Solids -- produced a Shell/Face, not a Solid"
            )

        try:
            shape.check(True)
        except Exception as e:
            counts: Dict[str, int] = {}
            for line in str(e).splitlines():
                m = self._BOP_ERROR_LINE.search(line)
                if m:
                    counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            findings['bop_errors'] = counts or {'unparsed': str(e)[:200]}

        return findings

    def find_root_cause(self, args: Dict[str, Any]) -> str:
        """Walk object_name's full dependency subtree (via OutList) and
        report which object(s) FIRST introduce each defect, instead of just
        the symptom at object_name itself.

        Exists because a boolean/CAM/export failure almost always surfaces
        at the top of a dependency tree, but the actual defect is usually
        several features upstream and out of context -- diagnosing it by
        hand means running check_solid/check_geometry on every object in
        the subtree one at a time until something flags (see
        reference_revolve_shell_validity_open_wire.md and the "Compound"
        investigation this operation grew out of). This automates that
        walk: every shape-bearing object and every sketch in the subtree is
        checked independently with the same primitives check_solid /
        diagnose_invalid_shape / check_geometry already use, then an
        object is only reported as a ROOT CAUSE for a given defect if none
        of its own DIRECT inputs already show that same defect -- otherwise
        it's reported as inheriting/propagating it, so fixing one upstream
        object doesn't get reported N more times as N separate problems.

        A subtree can have more than one INDEPENDENT root cause -- e.g. one
        feature with a bad fillet, and separately, a downstream Compound
        whose siblings don't quite meet at the seam. Both are reported,
        each with their own propagation chain.
        """
        try:
            object_name = args.get('object_name', '')

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            order = []
            findings_by_name: Dict[str, Any] = {}
            objects_by_name: Dict[str, Any] = {}
            visited = set()

            def walk(o, depth=0):
                if o is None or o.Name in visited or depth > 60:
                    return
                visited.add(o.Name)
                for upstream in getattr(o, 'OutList', []):
                    walk(upstream, depth + 1)
                if o.TypeId == 'Sketcher::SketchObject' or hasattr(o, 'Shape'):
                    objects_by_name[o.Name] = o
                    findings_by_name[o.Name] = self._classify_shape_findings(o)
                    order.append(o.Name)

            walk(obj)

            flagged_names = [n for n in order if findings_by_name[n]]
            if not flagged_names:
                return (f"{object_name}: no anomalies found across "
                        f"{len(order)} shape-bearing object(s) in its dependency subtree.")

            def immediate_flagged_inputs(name):
                return [u.Name for u in getattr(objects_by_name[name], 'OutList', [])
                        if u.Name in findings_by_name and findings_by_name[u.Name]]

            root_blocks = []
            propagating_lines = []

            for name in flagged_names:
                own = findings_by_name[name]
                input_findings = [findings_by_name[i] for i in immediate_flagged_inputs(name)]

                new_kinds: Dict[str, Any] = {}
                for kind, detail in own.items():
                    if kind == 'bop_errors':
                        upstream_classes = set()
                        for inp_f in input_findings:
                            upstream_classes.update(inp_f.get('bop_errors', {}).keys())
                        new_classes = {c: n for c, n in detail.items() if c not in upstream_classes}
                        if new_classes:
                            new_kinds['bop_errors'] = new_classes
                    elif not any(kind in inp_f for inp_f in input_findings):
                        new_kinds[kind] = detail

                if new_kinds:
                    o = objects_by_name[name]
                    label2 = getattr(o, 'Label2', '') or ''
                    header = f"{name} ({o.TypeId}" + (f", Label2='{label2}'" if label2 else "") + ")"
                    lines = [f"ROOT CAUSE: {header}"]
                    for kind, detail in new_kinds.items():
                        if kind == 'bop_errors':
                            for cls, count in detail.items():
                                lines.append(f"  bop_errors: {cls} x{count}")
                        else:
                            lines.append(f"  {kind}: {detail}")
                    root_blocks.append("\n".join(lines))
                else:
                    propagating_lines.append(
                        f"  {name} ({objects_by_name[name].TypeId}) -- inherits: "
                        + ", ".join(own.keys())
                    )

            lines = [f"Root-cause scan of {object_name}'s dependency subtree "
                     f"({len(order)} object(s) checked, {len(flagged_names)} flagged):", ""]
            lines.append("\n\n".join(root_blocks))
            if propagating_lines:
                lines.append(f"\n{len(propagating_lines)} downstream object(s) inheriting "
                              f"the above (fix root cause(s) first, then recheck):")
                lines.extend(propagating_lines)

            return "\n".join(lines)

        except Exception as e:
            return f"Error finding root cause: {e}"

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

            result = f"Solid check for {object_name}:\n  " + "\n  ".join(status)

            # This check only asks "does a Solid exist somewhere in the
            # shape", not "is every child actually a Solid" -- on an object
            # built from more than one upstream input (Part::Compound or any
            # other multi-child container), a clean result here can still
            # hide a Shell masquerading as a Solid among the children (see
            # the "find-root-cause-not-symptom" Note in usage_guidance.py).
            # Only surface
            # this when the check reported clean -- if it already found a
            # problem, the model doesn't need redirecting.
            #
            # Empirically the strongest lever found for getting a model to
            # actually use find_root_cause: escalation embedded in the
            # result of the tool it just called (9/10, same-task, directly
            # relevant) beat escalation redirecting from an unrelated tool
            # toward unrelated general guidance (0/10, 0/10, 1/10 across
            # three tries) -- see other-llms/README.md's "check_solid
            # response escalation" experiment, sibling claude/ directory.
            #
            # OutList is guaranteed to be a real list on any live FreeCAD
            # object; the isinstance guard exists only because unit-test
            # mocks (tests/unit/_freecad_mocks.py) don't all set it
            # explicitly, and an unconfigured MagicMock attribute would
            # otherwise blow up len() here.
            outlist = getattr(obj, 'OutList', None)
            if is_solid and is_valid and isinstance(outlist, (list, tuple)) and len(outlist) > 1:
                result += (
                    "\n\nNote: this object has more than one upstream input. "
                    "This check only verifies a Solid exists somewhere in the "
                    "shape, not that every child is one -- for a boolean/CAM/"
                    "export failure involving this object, "
                    "measurement_operations(operation=\"find_root_cause\") "
                    "checks each upstream object independently and is more "
                    "likely to find the actual defect."
                )

            return result

        except Exception as e:
            return f"Error checking solid: {e}"

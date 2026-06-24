# Primitive shape creation handlers for FreeCAD MCP
#
# ───────────────────────────────────────────────────────────────────────────
# primitives-validation — status
#
# (b) DONE: zero/negative dimensions are now rejected with the parameter name
#     in the error (see _validate_positive and the per-handler checks). A null
#     Part::Box/::Cylinder no longer silently lands in the document to crash a
#     downstream OCCT Boolean. The Test*DegenerateDimensions classes in
#     tests/unit/test_primitives.py assert the rejection.
#
# (a) DONE: unknown/misspelled arg keys ('lenght' → was silent 10mm default)
#     are now rejected via _check_unknown_keys. The injected-key envelope is:
#     `operation` (always), `_continue_selection` + `_operation_id` (continuation
#     path only). These are tolerated by _INJECTED_KEYS; everything else unknown
#     returns an explicit error naming the offending keys.
# ───────────────────────────────────────────────────────────────────────────

import FreeCAD
import FreeCADGui
from typing import Dict, Any, Optional
from .base import BaseHandler


_INJECTED_KEYS = frozenset({"operation", "_continue_selection", "_operation_id"})


def _check_unknown_keys(primitive: str, args: dict, allowed: frozenset) -> Optional[str]:
    unknown = set(args) - allowed - _INJECTED_KEYS
    if unknown:
        return f"Error creating {primitive}: unknown argument(s) {sorted(unknown)} — check for typos"
    return None


def _validate_positive(prim: str, **dims) -> Optional[str]:
    """Return an 'Error creating <prim>: ...' string if any named dimension is
    not a number > 0; otherwise None. Names the offending parameter so the MCP
    caller (often an LLM) can correct it."""
    for name, val in dims.items():
        try:
            if float(val) <= 0:
                return f"Error creating {prim}: {name} must be > 0 (got {val})"
        except (TypeError, ValueError):
            return f"Error creating {prim}: {name} must be a number (got {val!r})"
    return None


class PrimitivesHandler(BaseHandler):
    """Handler for creating primitive shapes (Part workbench)."""

    def create_box(self, args: Dict[str, Any]) -> str:
        """Create a box with specified dimensions."""
        try:
            err = _check_unknown_keys('box', args, frozenset({'length', 'width', 'height', 'x', 'y', 'z', 'name'}))
            if err:
                return err
            length = args.get('length', 10)
            width = args.get('width', 10)
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            name = args.get('name', 'Box')

            err = _validate_positive('box', length=length, width=width, height=height)
            if err:
                return err

            doc = self.get_document()
            if not doc:
                return "Error creating box: No active document. Call view_control(operation='create_document') first."

            box = doc.addObject("Part::Box", name)
            box.Label = name
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
            err = _check_unknown_keys('cylinder', args, frozenset({'radius', 'height', 'x', 'y', 'z', 'name'}))
            if err:
                return err
            radius = args.get('radius', 5)
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            name = args.get('name', 'Cylinder')

            err = _validate_positive('cylinder', radius=radius, height=height)
            if err:
                return err

            doc = self.get_document()
            if not doc:
                return "Error creating cylinder: No active document. Call view_control(operation='create_document') first."

            cylinder = doc.addObject("Part::Cylinder", name)
            cylinder.Label = name
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
            err = _check_unknown_keys('sphere', args, frozenset({'radius', 'x', 'y', 'z', 'name'}))
            if err:
                return err
            radius = args.get('radius', 5)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            name = args.get('name', 'Sphere')

            err = _validate_positive('sphere', radius=radius)
            if err:
                return err

            doc = self.get_document()
            if not doc:
                return "Error creating sphere: No active document. Call view_control(operation='create_document') first."

            sphere = doc.addObject("Part::Sphere", name)
            sphere.Label = name
            sphere.Radius = radius
            sphere.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return f"Created sphere: {sphere.Name} (R{radius}) at ({x},{y},{z})"

        except Exception as e:
            return f"Error creating sphere: {e}"

    def create_cone(self, args: Dict[str, Any]) -> str:
        """Create a cone with specified radii and height."""
        try:
            err = _check_unknown_keys('cone', args, frozenset({'radius1', 'radius2', 'height', 'x', 'y', 'z', 'name'}))
            if err:
                return err
            radius1 = args.get('radius1', 5)  # Bottom radius
            radius2 = args.get('radius2', 0)  # Top radius
            height = args.get('height', 10)
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            name = args.get('name', 'Cone')

            # height must be positive; radii non-negative and not both zero —
            # radius2=0 is a valid pointed cone, radius1==radius2 a valid cylinder.
            err = _validate_positive('cone', height=height)
            if err:
                return err
            try:
                if float(radius1) < 0 or float(radius2) < 0:
                    return f"Error creating cone: radii must be >= 0 (got R1={radius1}, R2={radius2})"
                if float(radius1) == 0 and float(radius2) == 0:
                    return "Error creating cone: at least one of radius1/radius2 must be > 0"
            except (TypeError, ValueError):
                return f"Error creating cone: radii must be numbers (got R1={radius1!r}, R2={radius2!r})"

            doc = self.get_document()
            if not doc:
                return "Error creating cone: No active document. Call view_control(operation='create_document') first."

            cone = doc.addObject("Part::Cone", name)
            cone.Label = name
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
            err = _check_unknown_keys('torus', args, frozenset({'radius1', 'radius2', 'x', 'y', 'z', 'name'}))
            if err:
                return err
            radius1 = args.get('radius1', 10)  # Major radius
            radius2 = args.get('radius2', 3)   # Minor radius
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            name = args.get('name', 'Torus')

            err = _validate_positive('torus', radius1=radius1, radius2=radius2)
            if err:
                return err

            doc = self.get_document()
            if not doc:
                return "Error creating torus: No active document. Call view_control(operation='create_document') first."

            torus = doc.addObject("Part::Torus", name)
            torus.Label = name
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
            err = _check_unknown_keys('wedge', args, frozenset({'xmin', 'ymin', 'zmin', 'x2min', 'x2max', 'xmax', 'ymax', 'zmax', 'name'}))
            if err:
                return err
            xmin = args.get('xmin', 0)
            ymin = args.get('ymin', 0)
            zmin = args.get('zmin', 0)
            x2min = args.get('x2min', 2)
            x2max = args.get('x2max', 8)
            xmax = args.get('xmax', 10)
            ymax = args.get('ymax', 10)
            zmax = args.get('zmax', 10)
            name = args.get('name', 'Wedge')

            doc = self.get_document()
            if not doc:
                return "Error creating wedge: No active document. Call view_control(operation='create_document') first."

            wedge = doc.addObject("Part::Wedge", name)
            wedge.Label = name
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

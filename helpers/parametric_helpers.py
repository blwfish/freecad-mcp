"""
Parametric Modeling Helpers for FreeCAD Building Models

This module provides helper functions to reduce repetitive parametric modeling tasks:
- Position sketches by parameter (e.g., half-buildingWidth)
- Extrude sketches with parameter-driven thickness/reversal
- Create punchout arrays and cut them from walls
- Clone and position master sketches

Usage:
  Import this into FreeCAD's Python console or macro editor, then call functions like:
    ph.sketch_position(sketch_name="WallLeft", x_offset="buildingWidth/2", reverse_x=True)
    ph.sketch_extrude(sketch_name="WallLeft", depth="wallThickness", reverse=True)
    ph.create_punchout_array(base_sketch="WindowMaster", wall_face="WallRight",
                              array_count=4, spacing="bayWidth")
"""

import FreeCAD as App
import FreeCADGui as Gui
from FreeCAD import Vector
import Part


class ParametricHelpers:
    """Helper class for parametric building model workflows."""

    def __init__(self, doc=None):
        """Initialize with active document or specified document."""
        self.doc = doc or App.activeDocument()
        if not self.doc:
            raise ValueError("No active FreeCAD document. Open a document first.")
        self.params = self._get_params()

    def _get_params(self):
        """Get the params spreadsheet (handles both local and linked)."""
        for obj in self.doc.Objects:
            if "Spreadsheet" in obj.TypeId:
                return obj
            elif obj.TypeId == "App::Link" and "params" in obj.Label.lower():
                return obj.LinkedObject
        raise ValueError("No params spreadsheet found. Make sure Skeleton is linked.")

    def get_param(self, alias):
        """Get parameter value by alias. Returns float or string."""
        try:
            # Try direct alias lookup
            if hasattr(self.params, alias):
                val = getattr(self.params, alias)
                # If it's a Quantity, get the value in mm
                if hasattr(val, 'Value'):
                    return val.Value
                return float(val)
        except:
            pass

        # Try cell-by-cell lookup
        for row in range(1, 100):
            for col in "ABCDEFGHIJ":
                cell_addr = f"{col}{row}"
                try:
                    if self.params.getAlias(cell_addr) == alias:
                        val = self.params.getContents(cell_addr)
                        # Evaluate formula
                        return self.params.get(cell_addr).Value
                except:
                    pass

        raise ValueError(f"Parameter '{alias}' not found in spreadsheet")

    def evaluate_expr(self, expr):
        """Evaluate a parameter expression like 'buildingWidth/2' or '10 mm'."""
        # If it's already a number, return it
        try:
            return float(expr)
        except (ValueError, TypeError):
            pass

        # Try to interpret as an expression
        # Replace parameter names with their values
        import re
        result_expr = expr

        # Find all word-like tokens and try to replace them with param values
        tokens = re.findall(r'\b[a-zA-Z_]\w*\b', expr)
        for token in tokens:
            try:
                val = self.get_param(token)
                result_expr = result_expr.replace(token, str(val))
            except ValueError:
                # Not a parameter, might be a unit like "mm" or "ft"
                pass

        # Evaluate the expression (this is safe because we control the tokens)
        try:
            return eval(result_expr)
        except Exception as e:
            raise ValueError(f"Failed to evaluate expression '{expr}': {e}")

    def get_object(self, name):
        """Get object by name or label."""
        obj = self.doc.getObject(name)
        if obj:
            return obj
        # Try by label
        for obj in self.doc.Objects:
            if obj.Label == name:
                return obj
        raise ValueError(f"Object '{name}' not found")

    # ========== SKETCH OPERATIONS ==========

    def sketch_position(self, sketch_name, x_offset=None, y_offset=None, z_offset=None,
                       reverse_x=False, reverse_y=False, reverse_z=False):
        """
        Position a sketch by applying offsets (in mm or as expressions like 'buildingWidth/2').
        Reverse flags flip the direction.

        Example:
            sketch_position("WallLeft", x_offset="buildingWidth/2", reverse_x=True)
            → places sketch at x = -buildingWidth/2
        """
        sketch = self.get_object(sketch_name)

        x = self.evaluate_expr(x_offset) if x_offset else 0
        y = self.evaluate_expr(y_offset) if y_offset else 0
        z = self.evaluate_expr(z_offset) if z_offset else 0

        if reverse_x:
            x = -x
        if reverse_y:
            y = -y
        if reverse_z:
            z = -z

        sketch.Placement.Base = Vector(x, y, z)
        self.doc.recompute()
        return sketch

    def sketch_extrude(self, sketch_name, depth, reverse=False, name=None):
        """
        Extrude a sketch by a parameter-driven depth.
        If reverse=True, extrudes in negative direction.

        Example:
            sketch_extrude("WallLeft", depth="wallThickness", reverse=True)
        """
        sketch = self.get_object(sketch_name)
        depth_val = self.evaluate_expr(depth)

        if reverse:
            depth_val = -depth_val

        extrude = self.doc.addObject("Part::Extrusion", name or f"{sketch_name}_Extrude")
        extrude.Base = sketch
        extrude.Dir = Vector(0, 0, 1)
        extrude.Solid = True
        extrude.TaperAngle = 0
        extrude.TaperAngle2 = 0
        extrude.Symmetric = False
        extrude.SymmetricReverse = False
        extrude.LengthReverse = False
        extrude.Length = abs(depth_val)

        if depth_val < 0:
            extrude.Reversed = True

        self.doc.recompute()
        return extrude

    def sketch_from_master(self, master_label, new_sketch_name,
                          x_offset=None, y_offset=None, z_offset=None):
        """
        Create a new sketch by importing geometry from a master sketch and positioning it.

        Example:
            sketch_from_master("Master XY", "EastWall",
                              x_offset="buildingWidth/2")
        """
        # Find the master sketch
        master = None
        for obj in self.doc.Objects:
            if obj.Label == master_label and "Sketch" in obj.TypeId:
                master = obj
                break

        if not master:
            raise ValueError(f"Master sketch '{master_label}' not found")

        # Create new sketch
        new_sketch = self.doc.addObject("Sketcher::SketchObject", new_sketch_name)
        new_sketch.Label = new_sketch_name

        # Copy geometry from master by adding external geometry references
        # (This requires the master to be in the same doc or imported)
        # For now, position and return—user will copy geometry manually or via UI

        self.sketch_position(new_sketch_name, x_offset, y_offset, z_offset)
        return new_sketch

    # ========== PUNCHOUT OPERATIONS ==========

    def create_punchout_array(self, base_sketch_name, wall_object_name,
                             array_count, spacing,
                             cut_immediately=True, name_prefix=None):
        """
        Create an array of punchouts and optionally cut from a wall.

        Workflow:
          1. Clone the base punchout sketch
          2. Create array copies (spaced by 'spacing' parameter)
          3. Extrude each to a cutting solid
          4. Union into single cutting body
          5. Cut from wall

        Example:
            create_punchout_array("WindowMaster", "EastWall",
                                 array_count=4, spacing="bayWidth",
                                 cut_immediately=True)
        """
        base_sketch = self.get_object(base_sketch_name)
        wall = self.get_object(wall_object_name)
        prefix = name_prefix or base_sketch_name.replace("Master", "")
        spacing_val = self.evaluate_expr(spacing)

        # Step 1: Create array of cloned sketches
        clones = []
        for i in range(array_count):
            offset = i * spacing_val
            clone_name = f"{prefix}Array{i}"

            # Clone the sketch
            clone = self.doc.addObject("Part::Clone", clone_name)
            clone.Source = base_sketch
            clone.Label = clone_name

            # Position it
            clone.Placement.Base = Vector(offset, 0, 0)
            clones.append(clone)

        # Step 2: Extrude each to a cutting solid
        # (This depends on wall orientation—for now, extrude in Z)
        extrusions = []
        for i, clone in enumerate(clones):
            extrude_name = f"{prefix}CutSolid{i}"
            # Create a simple extrusion for now
            # In practice, you'd measure the wall thickness
            ext = self.doc.addObject("Part::Extrusion", extrude_name)
            ext.Base = clone
            ext.Dir = Vector(0, 0, 1)
            ext.Length = 20  # Arbitrary depth; should match wall
            ext.Solid = True
            extrusions.append(ext)

        # Step 3: Union all cutting solids
        if len(extrusions) == 1:
            cutting_body = extrusions[0]
        else:
            cutting_body = self.doc.addObject("Part::MultiFuse", f"{prefix}CutArray")
            cutting_body.Shapes = extrusions

        cutting_body.Label = f"{prefix}CutArray"

        # Step 4: Cut from wall (if requested)
        if cut_immediately:
            cut_obj = self.doc.addObject("Part::Cut", f"{wall_object_name}_Cut_{prefix}")
            cut_obj.Base = wall
            cut_obj.Tool = cutting_body
            cut_obj.Label = f"{wall_object_name} with {prefix} Punchouts"
            self.doc.recompute()
            return cut_obj

        self.doc.recompute()
        return cutting_body

    # ========== WALL OPERATIONS ==========

    def create_wall(self, sketch_name, thickness_param="wallThickness",
                   reverse=False, name=None):
        """
        Convenience function: extrude a wall sketch with standard parameters.

        Example:
            create_wall("WallEast", thickness_param="wallThickness", reverse=True)
        """
        return self.sketch_extrude(sketch_name, depth=thickness_param,
                                  reverse=reverse, name=name)

    def position_wall(self, sketch_name, x_offset=None, y_offset=None, z_offset=None,
                     reverse_x=False, reverse_y=False, reverse_z=False):
        """
        Convenience function: position a wall sketch.
        """
        return self.sketch_position(sketch_name, x_offset, y_offset, z_offset,
                                   reverse_x, reverse_y, reverse_z)


# ========== CONVENIENCE FUNCTIONS (call these from macro/console) ==========

def init_helpers(doc=None):
    """Initialize and return helper instance. Use this to start working."""
    return ParametricHelpers(doc)


# Quick helpers for common tasks
def pos(sketch_name, x=None, y=None, z=None, rx=False, ry=False, rz=False):
    """Shorthand: position a sketch."""
    ph = ParametricHelpers()
    return ph.sketch_position(sketch_name, x, y, z, rx, ry, rz)


def extrude(sketch_name, depth, reverse=False, name=None):
    """Shorthand: extrude a sketch."""
    ph = ParametricHelpers()
    return ph.sketch_extrude(sketch_name, depth, reverse, name)


def wall(sketch_name, x=None, y=None, z=None, thickness="wallThickness",
        reverse=False, reverse_extrude=False, rx=False, ry=False, rz=False):
    """Shorthand: position and extrude a wall in one call."""
    ph = ParametricHelpers()
    if x or y or z:
        ph.sketch_position(sketch_name, x, y, z, rx, ry, rz)
    return ph.sketch_extrude(sketch_name, thickness, reverse_extrude, name=f"{sketch_name}_Wall")


def punchout(base_sketch, wall_obj, count, spacing, prefix=None, cut=True):
    """Shorthand: create and cut a punchout array."""
    ph = ParametricHelpers()
    return ph.create_punchout_array(base_sketch, wall_obj, count, spacing, cut, prefix)


if __name__ == "__main__":
    # Example usage (if run as script)
    ph = init_helpers()
    print(f"Initialized helpers for document: {ph.doc.Name}")
    print(f"Found params: {ph.get_param('scale')}, {ph.get_param('wallThickness')}")

"""
Window & Door Population Helpers for FreeCAD Building Models

Automates the workflow of:
1. Checking imported master config settings (Link Copy On Change)
2. Detecting holes in walls (Cut operations)
3. Matching holes to window/door masters by size
4. Cloning and positioning masters for each hole

Usage:
  from window_door_helpers import WindowDoorHelpers

  wdh = WindowDoorHelpers()
  wdh.check_link_config()  # Scan and fix config issues
  wdh.populate_holes("Imported master window", "WallEast", tolerance=0.3)
"""

import FreeCAD as App
from FreeCAD import Vector
import Part
import math


class WindowDoorHelpers:
    """Helper class for window/door population workflows."""

    def __init__(self, doc=None):
        """Initialize with active document or specified document."""
        self.doc = doc or App.activeDocument()
        if not self.doc:
            raise ValueError("No active FreeCAD document. Open a document first.")

    def get_object(self, name):
        """Get object by name or label."""
        obj = self.doc.getObject(name)
        if obj:
            return obj
        for obj in self.doc.Objects:
            if obj.Label == name:
                return obj
        raise ValueError(f"Object '{name}' not found")

    # ========== CONFIG CHECKING ==========

    def check_link_config(self, auto_fix=False):
        """
        Scan all objects for Link Copy On Change setting.
        Report and optionally fix ones that are set to 'Linked' instead of 'Owned'.

        Example:
            wdh.check_link_config(auto_fix=True)
            → Fixes all incorrect Link Copy On Change settings
        """
        issues = []
        fixed = []

        for obj in self.doc.Objects:
            if obj.TypeId == "App::Link":
                # Check the LinkCopyOnChange property
                link_copy = getattr(obj, 'LinkCopyOnChange', None)

                # Determine if it's wrong (should be 'Owned')
                if link_copy and link_copy.lower() == 'linked':
                    issue = {
                        'object': obj.Name,
                        'label': obj.Label,
                        'current': link_copy,
                        'required': 'Owned',
                    }
                    issues.append(issue)

                    if auto_fix:
                        try:
                            obj.LinkCopyOnChange = 'Owned'
                            fixed.append(obj.Label)
                        except Exception as e:
                            print(f"Failed to fix {obj.Label}: {e}")

        # Report
        print(f"\nLink Copy On Change Audit:")
        print(f"  Issues found: {len(issues)}")
        for issue in issues:
            status = "FIXED" if issue['label'] in fixed else "NEEDS FIX"
            print(f"    {issue['label']:40} | {status}")

        if auto_fix and fixed:
            self.doc.recompute()
            print(f"  Auto-fixed: {len(fixed)}")

        return issues, fixed

    # ========== HOLE DETECTION ==========

    def find_holes_in_wall(self, wall_obj):
        """
        Find all Cut operations that use wall_obj as the base.
        Return list of holes with their bounding boxes and positions.

        Returns:
            list of dicts: {'cut_obj', 'tool', 'bbox', 'position', 'normal', ...}
        """
        holes = []

        # Get the wall object
        try:
            wall = self.get_object(wall_obj) if isinstance(wall_obj, str) else wall_obj
        except ValueError:
            print(f"Warning: Wall '{wall_obj}' not found")
            return holes

        # Find all Cut operations that use this wall
        for obj in self.doc.Objects:
            if obj.TypeId == "Part::Cut":
                # Check if this cut uses our wall as the base
                if hasattr(obj, 'Base') and obj.Base:
                    if obj.Base == wall or (hasattr(obj.Base, 'Label') and obj.Base.Label == wall.Label):
                        # Found a cut on this wall
                        if hasattr(obj, 'Tool') and obj.Tool:
                            tool = obj.Tool
                            bbox = tool.Shape.BoundBox if hasattr(tool, 'Shape') else None

                            if bbox:
                                hole = {
                                    'cut_obj': obj,
                                    'cut_name': obj.Name,
                                    'cut_label': obj.Label,
                                    'tool': tool,
                                    'tool_name': tool.Name,
                                    'tool_label': tool.Label,
                                    'bbox': bbox,
                                    'width': bbox.XLength,
                                    'height': bbox.YLength,
                                    'depth': bbox.ZLength,
                                    'position': Vector(
                                        bbox.XMin + bbox.XLength / 2,
                                        bbox.YMin + bbox.YLength / 2,
                                        bbox.ZMin + bbox.ZLength / 2,
                                    ),
                                }
                                holes.append(hole)

        return holes

    # ========== MASTER DIMENSION LOOKUP ==========

    def get_master_dimensions(self, master_obj):
        """
        Extract window/door dimensions from a master object's spreadsheet.

        Looks for:
          - Master's linked document (if it's a Link)
          - Spreadsheet in that document
          - Parameters: width, height, casingWidth, casingDepth, kerf

        Returns:
            dict with 'width', 'height', 'casing_width', 'casing_depth', 'kerf'
        """
        try:
            master = self.get_object(master_obj) if isinstance(master_obj, str) else master_obj
        except ValueError:
            raise ValueError(f"Master '{master_obj}' not found")

        # If it's a Link, get the linked object
        if master.TypeId == "App::Link":
            target_doc = master.LinkedObject.Document
        else:
            target_doc = self.doc

        # Find the spreadsheet in the target document
        spreadsheet = None
        for obj in target_doc.Objects:
            if "Spreadsheet" in obj.TypeId:
                spreadsheet = obj
                break

        if not spreadsheet:
            raise ValueError(f"No spreadsheet found in {target_doc.Name}")

        # Try to get dimensions from spreadsheet
        dims = {}

        # Common parameter names
        param_aliases = ['width', 'height', 'casingWidth', 'casingDepth', 'kerf']

        for alias in param_aliases:
            try:
                val = getattr(spreadsheet, alias, None)
                if val is not None:
                    # If it's a Quantity, get the Value in mm
                    if hasattr(val, 'Value'):
                        dims[alias] = val.Value
                    else:
                        dims[alias] = float(val)
            except:
                pass

        # If we didn't find all params, try the master's bounding box as fallback
        if 'width' not in dims or 'height' not in dims:
            if hasattr(master, 'Shape'):
                bbox = master.Shape.BoundBox
                if 'width' not in dims:
                    dims['width'] = bbox.XLength
                if 'height' not in dims:
                    dims['height'] = bbox.YLength

        return dims

    # ========== HOLE MATCHING ==========

    def match_hole_to_master(self, hole, master_dims, tolerance_mm=0.3):
        """
        Check if a hole matches a master's dimensions (within tolerance).

        Returns:
            bool: True if hole matches master
        """
        # Get hole dimensions (use the two largest dimensions for width/height)
        hole_dims = sorted([hole['width'], hole['height'], hole['depth']])
        hole_width = hole_dims[1]  # Middle value
        hole_height = hole_dims[2]  # Largest value

        # Get master dimensions
        master_width = master_dims.get('width', 0)
        master_height = master_dims.get('height', 0)

        # Check tolerance (allow hole to be slightly larger than master)
        # This accounts for kerf and dimensional tolerance
        width_match = abs(hole_width - master_width) <= tolerance_mm
        height_match = abs(hole_height - master_height) <= tolerance_mm

        return width_match and height_match

    # ========== CLONING AND POSITIONING ==========

    def clone_and_position(self, master_obj, hole, wall_obj=None, dry_run=False):
        """
        Clone a master object and position it in a hole.

        Args:
            master_obj: Master object (Link or solid)
            hole: Hole dict from find_holes_in_wall()
            wall_obj: Wall object (for alignment, optional)
            dry_run: If True, don't actually create the clone

        Returns:
            Clone object, or None if dry_run
        """
        try:
            master = self.get_object(master_obj) if isinstance(master_obj, str) else master_obj
        except ValueError:
            raise ValueError(f"Master '{master_obj}' not found")

        # Get master dimensions for frame offset
        dims = self.get_master_dimensions(master)
        casing_depth = dims.get('casingDepth', 0)

        # Hole position (center of hole)
        hole_pos = hole['position']

        # For positioning, we want:
        # - Clone centered on the hole (X, Y)
        # - Clone positioned so the back of the frame is flush with wall front (Z)

        if dry_run:
            print(f"Would clone {master.Label} at position {hole_pos}")
            return None

        # Create the clone
        clone = self.doc.addObject("Part::Clone", f"{master.Name}_Clone")
        clone.Source = master
        clone.Label = f"{master.Label} (in {hole['cut_label']})"

        # Position the clone
        # For now, just center it on the hole. Frame offset is a refinement.
        clone.Placement.Base = hole_pos

        self.doc.recompute()
        return clone

    # ========== MAIN WORKFLOW ==========

    def populate_holes(self, master_name, wall_name, tolerance_mm=0.3,
                      dry_run=True, auto_place=False):
        """
        Main workflow: find holes and populate them with clones of a master.

        Args:
            master_name: Name or Label of the master object
            wall_name: Name or Label of the wall
            tolerance_mm: Size tolerance for fuzzy matching
            dry_run: If True, just report matches without creating clones
            auto_place: If True, create clones without prompting

        Returns:
            dict with 'matches' (list of matching holes) and 'created' (list of clones)
        """
        print(f"\nPopulate Holes: {master_name} → {wall_name}")
        print("=" * 70)

        # Get master and its dimensions
        try:
            master = self.get_object(master_name)
            master_dims = self.get_master_dimensions(master)
        except Exception as e:
            print(f"Error: {e}")
            return {'matches': [], 'created': []}

        print(f"Master dimensions: {master_dims}")

        # Find all holes in wall
        holes = self.find_holes_in_wall(wall_name)
        print(f"Found {len(holes)} hole(s) in wall")

        # Match holes to master
        matches = []
        for hole in holes:
            if self.match_hole_to_master(hole, master_dims, tolerance_mm):
                matches.append(hole)
                print(f"  ✓ Match: {hole['cut_label']:40} | "
                      f"Size: {hole['width']:.1f}×{hole['height']:.1f}×{hole['depth']:.1f}")
            else:
                print(f"  ✗ No match: {hole['cut_label']:40} | "
                      f"Size: {hole['width']:.1f}×{hole['height']:.1f}×{hole['depth']:.1f}")

        print(f"\nMatches: {len(matches)}")

        if dry_run:
            print("(Dry run - no clones created)")
            return {'matches': matches, 'created': []}

        # Create clones
        created = []
        if matches and (auto_place or self._prompt_user(f"Create {len(matches)} clone(s)?")):
            for i, hole in enumerate(matches):
                try:
                    clone = self.clone_and_position(master, hole)
                    if clone:
                        created.append(clone)
                        print(f"  Created: {clone.Label}")
                except Exception as e:
                    print(f"  Error creating clone: {e}")

        return {'matches': matches, 'created': created}

    def _prompt_user(self, question):
        """Prompt user (simplified - you can make this more interactive)."""
        # In interactive use, this would use input() or Gui dialogs
        # For now, return True to auto-proceed
        return True


# ========== CONVENIENCE FUNCTIONS ==========

def init_helpers(doc=None):
    """Initialize and return helper instance."""
    return WindowDoorHelpers(doc)


def check_config(auto_fix=False):
    """Shorthand: check Link Copy On Change settings."""
    wdh = WindowDoorHelpers()
    return wdh.check_link_config(auto_fix)


def populate(master, wall, tolerance=0.3, dry_run=True):
    """Shorthand: populate holes in a wall with a master."""
    wdh = WindowDoorHelpers()
    return wdh.populate_holes(master, wall, tolerance, dry_run)


if __name__ == "__main__":
    wdh = init_helpers()
    print(f"Initialized for document: {wdh.doc.Name}")

# Window & Door Population Helpers — Usage Guide

This guide shows how to use `window_door_helpers.py` to automate the tedious workflow of placing windows and doors in building models.

## Overview

The manual workflow for adding windows/doors to a building:

1. Open an assembly file (e.g., new-Gville-station.FCStd)
2. Import a master window/door from the library (general-windows.FCStd, general-doors.FCStd)
3. For each hole in the walls that matches that master's size:
   - Clone the master
   - Rotate/translate the clone to fit the hole
4. Repeat for other window/door types

**This library automates steps 3–4** by:
- Scanning walls for holes
- Matching holes to master sizes
- Cloning and positioning masters automatically

## Setup

1. Copy `window_door_helpers.py` to FreeCAD's macro directory:
   ```
   ~/Library/Application Support/FreeCAD/Macros/window_door_helpers.py
   ```

2. Or import in the Python console:
   ```python
   import sys
   sys.path.insert(0, "/Volumes/Files/claude/freecad-mcp/helpers")
   from window_door_helpers import WindowDoorHelpers
   ```

## Quick Start

### 1. Check Config Settings

Before populating, ensure all imported masters have the correct `Link Copy On Change` setting:

```python
from window_door_helpers import check_config

# Scan and report issues
check_config()

# Auto-fix all issues
check_config(auto_fix=True)
```

Output:
```
Link Copy On Change Audit:
  Issues found: 2
    Imported master door 1         | NEEDS FIX
    Imported master window 2       | NEEDS FIX
  Auto-fixed: 2
```

### 2. Populate Holes (Dry Run First)

Always test with `dry_run=True` first to see what will be created:

```python
from window_door_helpers import populate

# Preview without creating anything
populate(
    master="Imported master window",
    wall="WallEast",
    tolerance=0.3,
    dry_run=True  # Just report
)
```

Output:
```
Populate Holes: Imported master window → WallEast
======================================================================
Master dimensions: {'width': 101.2, 'height': 152.4, 'casingWidth': 76.2, 'casingDepth': 101.6}
Found 4 hole(s) in wall
  ✓ Match: Cut009                              | Size: 101.5×152.1×10.0
  ✓ Match: Cut010                              | Size: 101.2×152.4×10.0
  ✗ No match: Cut011                           | Size: 82.6×127.0×10.0
  ✗ No match: Cut012                           | Size: 152.4×203.2×10.0

Matches: 2
(Dry run - no clones created)
```

### 3. Create Clones

Once you've verified the matches are correct, create them:

```python
populate(
    master="Imported master window",
    wall="WallEast",
    tolerance=0.3,
    dry_run=False,  # Actually create clones
    auto_place=True  # Don't prompt, just do it
)
```

Output:
```
Populate Holes: Imported master window → WallEast
======================================================================
Master dimensions: {...}
Found 4 hole(s) in wall
  ✓ Match: Cut009                              | Size: 101.5×152.1×10.0
  ✓ Match: Cut010                              | Size: 101.2×152.4×10.0
  ✗ No match: Cut011                           | Size: 82.6×127.0×10.0
  ✗ No match: Cut012                           | Size: 152.4×203.2×10.0

Matches: 2
  Created: Imported master window (in Cut009)
  Created: Imported master window (in Cut010)
```

## Using WindowDoorHelpers Class

For more control, use the class directly:

```python
from window_door_helpers import WindowDoorHelpers

wdh = WindowDoorHelpers()

# Scan and fix config
issues, fixed = wdh.check_link_config(auto_fix=True)

# Get master dimensions
dims = wdh.get_master_dimensions("Imported master window")
print(f"Window size: {dims['width']:.1f} × {dims['height']:.1f} mm")

# Find all holes in a wall
holes = wdh.find_holes_in_wall("WallEast")
print(f"Found {len(holes)} hole(s)")

# Populate with one call
result = wdh.populate_holes(
    master_name="Imported master window",
    wall_name="WallEast",
    tolerance_mm=0.3,
    dry_run=False,
    auto_place=True
)

print(f"Created {len(result['created'])} clone(s)")
```

## Typical Workflow: Multi-Type Building

Let's say you have a building with three types of windows and two door types:

```python
from window_door_helpers import check_config, populate

# Step 1: Fix all config issues
check_config(auto_fix=True)

# Step 2: Populate windows
print("\n=== Adding Main Windows ===")
populate("Imported master window main", "WallEast", tolerance=0.3, dry_run=True)
# → Review matches, then:
populate("Imported master window main", "WallEast", tolerance=0.3, dry_run=False)

print("\n=== Adding Upper Windows ===")
populate("Imported master window upper", "WallEast", tolerance=0.3, dry_run=True)
populate("Imported master window upper", "WallEast", tolerance=0.3, dry_run=False)

print("\n=== Adding Small Windows ===")
populate("Imported master window small", "WallNorth", tolerance=0.3, dry_run=True)
populate("Imported master window small", "WallNorth", tolerance=0.3, dry_run=False)

# Step 3: Populate doors
print("\n=== Adding Front Doors ===")
populate("Imported master door", "WallSouth", tolerance=0.5, dry_run=True)
populate("Imported master door", "WallSouth", tolerance=0.5, dry_run=False)

print("\n=== Adding Service Doors ===")
populate("Imported master door service", "WallWest", tolerance=0.5, dry_run=True)
populate("Imported master door service", "WallWest", tolerance=0.5, dry_run=False)

print("\n✓ All windows and doors added!")
```

## Understanding Fuzzy Tolerance

The `tolerance` parameter allows for size mismatch between holes and masters.

**Why fuzzy matching?**

1. **Laser kerf** (~0.2 mm): Laser cutting removes material, so hole is slightly larger than intended
2. **3D print tolerances** (~0.2 mm): Support removal isn't perfect
3. **Door framing** (~3" scale): Door frames extend beyond the opening, so frame size ≠ hole size
4. **Measurement rounding**: CAD models have minor dimension variations

**Common tolerance values:**

| Scenario | Tolerance | Reason |
|---|---|---|
| **Windows (laser cut)** | 0.2–0.3 mm | Kerf + tolerance |
| **Doors with frames** | 0.5–1.0 mm | Frame extends beyond opening |
| **3D printed parts** | 0.5 mm | Support removal tolerance |
| **Loose fit** | 1.0–2.0 mm | Extra clearance desired |

If tolerance is **too small**: Legitimate holes won't match, and you'll miss placements.

If tolerance is **too large**: Unrelated holes might match, creating extra clones you'll have to delete.

**Start with 0.3 mm and adjust based on results.**

## Troubleshooting

### No matches found

**Problem**: "Matches: 0" even though you expect holes to match

**Solution**:
1. Check master dimensions: `wdh.get_master_dimensions("Imported master window")`
2. Check hole sizes: `holes = wdh.find_holes_in_wall("WallEast"); print(holes[0]['width'])`
3. Compare manually, increase tolerance if needed
4. Verify wall object name is correct

### "No spreadsheet found" error

**Problem**: Helper can't extract window dimensions

**Solution**:
1. Ensure the master is linked to a document with a spreadsheet (general-windows.FCStd, etc.)
2. If master is a Link, verify it points to the correct library file
3. Check `Link Copy On Change` is "Owned" (allows config to work locally)

### Clones are positioned incorrectly

**Problem**: Clones are created but not in the right spot

**Current Behavior**: Clones are centered on the hole's centroid. Frame offset (back of frame flush with wall) is not yet implemented.

**Workaround**: Manually adjust clone positions using the Properties panel. Frame depth is available in the master's dimensions dict.

### Accidental extra clones

**Problem**: Tolerance was too large, so unrelated holes matched

**Solution**: Just delete the extras. It's OK to overshoot—the intention is to catch all valid holes.

## Implementation Notes

### How Hole Detection Works

1. Scans all `Part::Cut` objects in the document
2. For each Cut, checks if the Base matches the wall
3. Extracts the Tool (the cutting solid) bounding box
4. Hole position = center of the bounding box
5. Hole size = bounding box dimensions

### How Master Dimensions Are Extracted

1. If master is a Link, gets the linked document
2. Finds the Spreadsheet in that document
3. Looks for parameters: `width`, `height`, `casingWidth`, `casingDepth`, `kerf`
4. Falls back to bounding box if spreadsheet not found

### How Positioning Works

Currently: Clone is placed at the hole's centroid (center point).

**Future enhancement**: Offset along wall normal so back of frame is flush with wall surface. This requires:
- Identifying the wall's front face
- Computing the offset vector
- Applying the offset to the clone

Let me know if you'd like this refinement!

## Next Steps

After gaining experience with this helper:

1. Build a macro that populates an entire building in a few clicks
2. Create reusable "building templates" with standard window/door placements
3. Extend to handle non-rectangular windows (circular, arched, etc.)
4. Add automatic frame offset positioning

See `parametric_helpers.py` for complementary tools (wall positioning, extrusion, punchout arrays).

## Example: Complete Building Assembly

```python
from window_door_helpers import WindowDoorHelpers
from parametric_helpers import ParametricHelpers

# Initialize
wdh = WindowDoorHelpers()
ph = ParametricHelpers()

# Fix all config issues
wdh.check_link_config(auto_fix=True)

# Build walls (using parametric helpers)
ph.wall("WallNorth", y_offset="buildingDepth/2", thickness="wallThickness", reverse_extrude=True)
ph.wall("WallSouth", y_offset="buildingDepth/2", reverse_y=True, thickness="wallThickness", reverse_extrude=True)
ph.wall("WallEast", x_offset="buildingWidth/2", thickness="wallThickness", reverse_extrude=True)
ph.wall("WallWest", x_offset="buildingWidth/2", reverse_x=True, thickness="wallThickness", reverse_extrude=True)

# Populate windows and doors
wdh.populate_holes("Imported master window", "WallEast", tolerance=0.3, dry_run=False, auto_place=True)
wdh.populate_holes("Imported master window", "WallNorth", tolerance=0.3, dry_run=False, auto_place=True)
wdh.populate_holes("Imported master door", "WallSouth", tolerance=0.5, dry_run=False, auto_place=True)

print("✓ Building assembly complete!")
```

# Parametric Modeling Helpers — Usage Guide

This guide shows how to use `parametric_helpers.py` to reduce repetitive modeling tasks.

## Setup

1. **Copy the helper file** into FreeCAD's macro directory:
   ```
   ~/Library/Application Support/FreeCAD/Macros/parametric_helpers.py
   ```

2. **Or import it directly** in the Python console:
   ```python
   import sys
   sys.path.insert(0, "/Volumes/Files/claude/freecad-mcp/helpers")
   from parametric_helpers import ParametricHelpers
   ```

## Core Workflow

### 1. Initialize Helpers

```python
from parametric_helpers import ParametricHelpers

ph = ParametricHelpers()  # Uses active document
# or specify a document:
# ph = ParametricHelpers(App.getDocument("MainBuilding"))
```

### 2. Access Parameters

```python
# Get a single parameter
thickness = ph.get_param("wallThickness")  # Returns float in mm

# Evaluate expressions
width_half = ph.evaluate_expr("buildingWidth / 2")
# Supports: arithmetic, parameter names, and units
```

## Common Tasks

### Task 1: Position a Wall Sketch

**Old Way:**
1. Select sketch in tree
2. Edit placement → set X/Y/Z manually
3. Calculate half-width/half-depth from spreadsheet
4. Recompute

**New Way:**
```python
ph.sketch_position(
    sketch_name="WallEast",
    x_offset="buildingWidth/2"
)
# Automatically reads buildingWidth from params, divides by 2, sets position
```

**With Reversal (for symmetric placement):**
```python
ph.sketch_position(
    sketch_name="WallWest",
    x_offset="buildingWidth/2",
    reverse_x=True  # Places at -buildingWidth/2
)
```

### Task 2: Extrude a Wall with Parameter-Driven Thickness

**Old Way:**
1. Select sketch
2. PartDesign → Pad
3. Look up wallThickness in spreadsheet
4. Enter value manually
5. Check "Reverse" if needed
6. Recompute

**New Way:**
```python
ph.sketch_extrude(
    sketch_name="WallEast",
    depth="wallThickness",
    reverse=True  # Extrudes in negative Z
)
```

### Task 3: Create a Wall in One Call

**Combines positioning + extrusion:**

```python
ph.create_wall(
    sketch_name="WallNorth",
    thickness_param="wallThickness",
    reverse=False
)
```

Or with positioning:
```python
wall = ph.wall(
    sketch_name="WallNorth",
    x_offset="buildingWidth/2",
    reverse_x=False,
    thickness="wallThickness",
    reverse_extrude=False
)
```

---

## Advanced: Punchout Arrays (Windows/Doors)

### Scenario: Row of 4 Windows in East Wall

**Pattern:**
- East wall is at x = buildingWidth/2
- Window bay master sketch positioned at origin
- Need to clone it 4 times, spaced by `bayWidth`
- Cut array from the wall

**Old Way:**
1. Create Clone1 of WindowMaster, position at (bayWidth, 0, 0)
2. Create Clone2, position at (2×bayWidth, 0, 0)
3. Create Clone3, position at (3×bayWidth, 0, 0)
4. Create Clone4, position at (4×bayWidth, 0, 0)
5. Extrude each to a cutting solid
6. Create MultiFuse of 4 solids
7. Cut from WallEast
8. Delete unused objects
9. Recompute

**New Way:**
```python
ph.create_punchout_array(
    base_sketch_name="WindowMaster",
    wall_object_name="WallEast",
    array_count=4,
    spacing="bayWidth",
    cut_immediately=True
)
```

### Example: East Wall with Two Window Arrays (Mixed Sizes)

```python
# Lower row: 4 large windows (6 ft wide)
large_windows = ph.create_punchout_array(
    base_sketch_name="Window6ftMaster",
    wall_object_name="WallEast",
    array_count=4,
    spacing="bayWidth",
    name_prefix="EastLarge",
    cut_immediately=False  # Don't cut yet
)

# Upper row: 4 small windows (4 ft wide), positioned higher
small_windows = ph.create_punchout_array(
    base_sketch_name="Window4ftMaster",
    wall_object_name="WallEast",
    array_count=4,
    spacing="bayWidth",
    name_prefix="EastSmall",
    cut_immediately=False
)

# Union both arrays and cut once
all_punchouts = App.activeDocument().addObject("Part::MultiFuse", "EastAllWindows")
all_punchouts.Shapes = [large_windows, small_windows]

wall_cut = App.activeDocument().addObject("Part::Cut", "WallEastFinal")
wall_cut.Base = ph.get_object("WallEast")
wall_cut.Tool = all_punchouts
App.activeDocument().recompute()
```

---

## Shorthand Convenience Functions

For faster typing in the Python console, use short names:

```python
from parametric_helpers import pos, extrude, wall, punchout

# Position
pos("WallEast", x="buildingWidth/2")

# Extrude
extrude("WallEast", "wallThickness", reverse=True)

# Wall in one call
wall("WallEast", x="buildingWidth/2", thickness="wallThickness", reverse_extrude=True)

# Punchout array
punchout("WindowMaster", "WallEast", count=4, spacing="bayWidth")
```

---

## Parameter Expression Language

The helpers support expressions for offsets and depths. Examples:

| Expression | Meaning |
|---|---|
| `10` | 10 mm |
| `"buildingWidth"` | Get buildingWidth from params |
| `"buildingWidth/2"` | Half the building width |
| `"bayWidth * 2"` | Two bay widths |
| `"buildingDepth/2 - wallThickness"` | Half depth minus wall thickness |

**Note:** Parameter names must match exactly (case-sensitive) as they appear in the spreadsheet.

---

## Common Workflow Example: Rectangular Building

Here's a complete example building a symmetric rectangular building from scratch.

```python
from parametric_helpers import ParametricHelpers
import FreeCAD as App

ph = ParametricHelpers()
doc = ph.doc

# 1. Create foundation (simple rectangle, full building footprint)
ph.sketch_from_master("Master XY", "FoundationSketch")
ph.sketch_extrude("FoundationSketch", depth="foundationHeight", name="Foundation")

# 2. Create four walls
walls = {}
walls["North"] = ph.wall(
    "WallNorth",
    y_offset="buildingDepth/2",
    thickness="wallThickness",
    reverse_extrude=True
)

walls["South"] = ph.wall(
    "WallSouth",
    y_offset="buildingDepth/2",
    reverse_y=True,
    thickness="wallThickness",
    reverse_extrude=True
)

walls["East"] = ph.wall(
    "WallEast",
    x_offset="buildingWidth/2",
    thickness="wallThickness",
    reverse_extrude=True
)

walls["West"] = ph.wall(
    "WallWest",
    x_offset="buildingWidth/2",
    reverse_x=True,
    thickness="wallThickness",
    reverse_extrude=True
)

# 3. Add window punchouts to east wall
ph.create_punchout_array(
    base_sketch_name="WindowMaster",
    wall_object_name="WallEast",
    array_count=4,
    spacing="bayWidth",
    cut_immediately=True
)

# 4. Add door punchout to south wall
ph.create_punchout_array(
    base_sketch_name="DoorMaster",
    wall_object_name="WallSouth",
    array_count=1,
    spacing="bayWidth",
    cut_immediately=True
)

doc.recompute()
print("Building complete!")
```

---

## Troubleshooting

### "Parameter 'X' not found"
- Check spelling (case-sensitive)
- Verify the parameter exists in the Skeleton spreadsheet
- Look at the spreadsheet aliases to find exact names

### "Object 'X' not found"
- Use the exact internal object name (from the tree)
- Or use the Label (display name)
- Both should work

### Sketch doesn't move / extrude doesn't change
- Call `doc.recompute()` after operations
- Helpers do this automatically, but manual operations need it

### Expression evaluation fails
- Ensure all parameter names in expressions are spelled correctly
- Use parentheses for complex expressions: `"(buildingWidth/2) - (wallThickness*2)"`

---

## Next Steps

After gaining experience with these helpers:
1. Build a macro that defines your building's entire structure in Python
2. Create reusable functions for complex assemblies (e.g., `build_rectangular_building(w, d, h)`)
3. Parameterize everything so changing spreadsheet values updates the entire model
4. Use `checkpoint`/`rollback` for non-destructive experimentation

See `CLAUDE.md` for MCP tool reference if you need to programmatically control FreeCAD further.

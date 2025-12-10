# FreeCAD 1.2 CAM API Changes

## Summary
FreeCAD 1.2 has significantly refactored the CAM (Path) workbench API, particularly around tool management. Our handlers need updates to work with the new structure.

## Key Changes

### 1. Tool Bit Creation

**OLD API (FreeCAD 1.0):**
```python
from Path.Tool.Bit import Factory
tool_bit = Factory.CreateBit(tool_type)
```

**NEW API (FreeCAD 1.2):**
```python
from Path.Tool.toolbit import ToolBitEndmill, ToolBitDrill, etc.
# OR use the base class with shape_id
from Path.Tool.Bit import ToolBit
tool_bit = ToolBit.from_shape_id("endmill.fcstd")
```

### 2. Available Tool Bit Classes

FreeCAD 1.2 provides specific classes for each tool type:
- `ToolBitBallend`
- `ToolBitBullnose`
- `ToolBitChamfer`
- `ToolBitCustom`
- `ToolBitDovetail`
- `ToolBitDrill`
- `ToolBitEndmill`
- `ToolBitRadius`
- `ToolBitProbe`
- `ToolBitReamer`
- `ToolBitSlittingSaw`
- `ToolBitTap`
- `ToolBitThreadMill`
- `ToolBitVBit`

### 3. Tool Bit Creation Methods

The `ToolBit` base class provides several factory methods:

```python
# From shape ID (most common)
tool_bit = ToolBit.from_shape_id("endmill.fcstd", label="My Endmill")

# From dictionary
tool_bit = ToolBit.from_dict({"shape": "endmill.fcstd", "Diameter": "5mm"})

# From file
tool_bit = ToolBit.from_file("/path/to/tool.fctb")

# From shape object
from Path.Tool.shape import ToolBitShapeEndmill
shape = ToolBitShapeEndmill(...)
tool_bit = ToolBit.from_shape(shape)
```

### 4. Tool Bit Properties

After creation, properties are accessed via:
```python
tool_bit.label = "5mm Endmill"
tool_bit.set_property("Diameter", "5 mm")
diameter = tool_bit.get_property("Diameter")
```

### 5. Attaching to Document

Tools must be attached to a document before use:
```python
tool_bit = ToolBit.from_shape_id("endmill.fcstd")
tool_obj = tool_bit.attach_to_doc(doc=FreeCAD.ActiveDocument)
```

### 6. Tool Controller Creation (Unchanged)

The controller creation API remains similar:
```python
from Path.Tool.Controller import Create as CreateController
controller = CreateController(
    name="TC: 5mm Endmill",
    tool=tool_obj,  # The document object, not the ToolBit proxy
    toolNumber=1
)
```

## Shape IDs

Common shape IDs used in from_shape_id():
- "endmill.fcstd" - Standard endmill
- "ballend.fcstd" - Ball end mill
- "drill.fcstd" - Drill bit
- "chamfer.fcstd" - Chamfer tool
- "vbit.fcstd" - V-bit engraving tool
- "bullnose.fcstd" - Bullnose endmill

## Migration Strategy

1. Replace `Factory.CreateBit()` with `ToolBit.from_shape_id()`
2. Update property setting to use `tool_bit.set_property()`
3. Ensure tools are attached to document before creating controllers
4. Add proper error handling for invalid shape IDs

## Testing Priority

Given the active CAM development, these handlers should be:
- **P0_CRITICAL** priority in regression testing
- Tested after every FreeCAD build update
- Validated against multiple tool types

# Fixture: topo_check

**Saved:** 2026-05-20 17:43:06
**FreeCAD version:** 
**Document:** TestDoc
**Object:** MyBox

## Topology summary

- Faces: 10
- Edges: 20
- Vertices: 12
- Volume: 500.000000 mm³
- Is solid: True
- Is closed: True
- Bounding box X: [0.0000, 10.0000] mm
- Bounding box Y: [0.0000, 10.0000] mm
- Bounding box Z: [0.0000, 10.0000] mm

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL for visual reference
- `screenshot.png` — viewport screenshot at save time (if GUI available)

## Usage

```python
compare_to_fixture(shape="MyBox", fixture_name="topo_check")
```

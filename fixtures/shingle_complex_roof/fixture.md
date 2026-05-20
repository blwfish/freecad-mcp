# Fixture: shingle_complex_roof

**Saved:** 2026-05-20 (reference fixture, hand-authored)
**FreeCAD version:** 0.22.0
**Document:** depot_main
**Object:** ShingleSheet_MainRoof

## Description

Full main-roof shingle sheet for the HO-scale depot building. 120 mm wide ×
90 mm run, three planes meeting at two ridges. This is the primary generator
smoke test: it exercises the full surface, stagger logic, and multi-course
layout that the shingle generator was designed for.

864 faces = 288 shingles × 3 faces each (top face + 2 long sides; short sides
share edges). Face count is sensitive to shingle count, which is determined by
`shingleLength`, `exposure`, and the surface dimensions — any parameter change
will shift this count.

## Topology summary

- Faces: 864
- Edges: 1728
- Vertices: 866
- Volume: 1296.0000 mm³
- Is solid: False
- Is closed: False
- Bounding box X: [0.0000, 120.0000] mm
- Bounding box Y: [0.0000, 90.0000] mm
- Bounding box Z: [0.0000, 0.3000] mm

## Generator parameters

| Parameter    | Value  |
|---|---|
| shingleLength | 5.0 mm |
| shingleWidth  | 7.0 mm |
| exposure      | 3.5 mm |
| randomOffset  | 0.5 mm |

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL placeholder; regenerate against live FreeCAD to populate
- `fixture.md` — this file

## Usage

```python
compare_to_fixture(shape="ShingleSheet_MainRoof", fixture_name="shingle_complex_roof")
```

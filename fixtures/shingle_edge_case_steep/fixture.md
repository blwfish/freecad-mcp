# Fixture: shingle_edge_case_steep

**Saved:** 2026-05-20
**FreeCAD version:** 1.2.0
**Document:** ShingleEdgeCaseSteep
**Object:** ShingledRoof_RoofSurface

## Description

30×18 mm flat surface (Z-normal). Generator: shingle_generator v5.3.4.
Params: shingleWidth=7.0, shingleHeight=5.0, exposure=3.5, randomOffset=0.0.
38 shingles generated and clipped to face bounds. Small surface edge case —
exercises the boundary clipping on a surface where only a few complete
courses fit (18 mm run / 3.5 mm exposure ≈ 5 courses).

## Topology summary

- Faces: 259
- Edges: 543
- Vertices: 362
- Volume: 344.544975 mm³
- Is solid: false
- Is closed: true
- Bounding box X: [0.0000, 30.0000] mm
- Bounding box Y: [0.0000, 18.0000] mm
- Bounding box Z: [0.0000, 0.7600] mm

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL (real generator output)

## Usage

```python
compare_to_fixture(shape="ShingledRoof_RoofSurface", fixture_name="shingle_edge_case_steep")
```

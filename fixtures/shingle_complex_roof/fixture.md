# Fixture: shingle_complex_roof

**Saved:** 2026-05-20
**FreeCAD version:** 1.2.0
**Document:** ShingleComplexRoof
**Object:** ShingledRoof_RoofSurface

## Description

120×90 mm flat surface (Z-normal). Generator: shingle_generator v5.3.4.
Params: shingleWidth=7.0, shingleHeight=5.0, exposure=3.5, randomOffset=0.5.
508 shingles generated and clipped to face bounds. Primary smoke test for
the generator: exercises the full surface, stagger logic, and multi-course
layout on a large surface.

## Topology summary

- Faces: 3511
- Edges: 7479
- Vertices: 4986
- Volume: 6902.162484 mm³
- Is solid: false
- Is closed: true
- Bounding box X: [0.0000, 120.0000] mm
- Bounding box Y: [0.0000, 90.0000] mm
- Bounding box Z: [0.0000, 0.7600] mm

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL (real generator output)

## Usage

```python
compare_to_fixture(shape="ShingledRoof_RoofSurface", fixture_name="shingle_complex_roof")
```

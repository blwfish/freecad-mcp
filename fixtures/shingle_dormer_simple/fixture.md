# Fixture: shingle_dormer_simple

**Saved:** 2026-05-20
**FreeCAD version:** 1.2.0
**Document:** ShingleDormerSimple
**Object:** ShingledRoof_RoofSurface

## Description

60×40 mm flat surface (Z-normal). Generator: shingle_generator v5.3.4.
Params: shingleWidth=7.0, shingleHeight=5.0, exposure=3.5, randomOffset=0.5.
131 shingles generated and clipped to face bounds.

## Topology summary

- Faces: 901
- Edges: 1911
- Vertices: 1274
- Volume: 1533.682005 mm³
- Is solid: false
- Is closed: true
- Bounding box X: [0.0000, 60.0000] mm
- Bounding box Y: [0.0000, 40.0000] mm
- Bounding box Z: [0.0000, 0.7600] mm

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL (real generator output)

## Usage

```python
compare_to_fixture(shape="ShingledRoof_RoofSurface", fixture_name="shingle_dormer_simple")
```
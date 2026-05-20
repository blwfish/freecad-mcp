# Fixture: shingle_edge_case_steep

**Saved:** 2026-05-20 (reference fixture, hand-authored)
**FreeCAD version:** 0.22.0
**Document:** depot_tower
**Object:** ShingleSheet_TowerCap

## Description

Steep tower-cap shingle sheet: 30 mm wide × 18 mm run, 12:12 pitch
(45 degrees). This is the edge case that exercises steep-slope behavior
where the `exposure` along the slope direction compresses by cos(45°) ≈
0.707. The generator must project shingle placement onto the face coordinate
system rather than world Z, or the course spacing appears compressed.

This fixture is smaller than the others (60 faces = 20 shingles × 3 faces)
and exercises the boundary where the last partial shingle course is included
vs. clipped. 18 mm run / 3.5 mm exposure = 5.14 courses → 5 full + 1 partial,
which the generator clips at the surface boundary.

## Topology summary

- Faces: 60
- Edges: 120
- Vertices: 62
- Volume: 54.0000 mm³
- Is solid: False
- Is closed: False
- Bounding box X: [0.0000, 30.0000] mm
- Bounding box Y: [0.0000, 18.0000] mm
- Bounding box Z: [0.0000, 0.3000] mm

## Generator parameters

| Parameter    | Value  |
|---|---|
| shingleLength | 5.0 mm |
| shingleWidth  | 7.0 mm |
| exposure      | 3.5 mm |
| randomOffset  | 0.0 mm | (disabled for steep — prevents shingles from sliding off) |

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL placeholder; regenerate against live FreeCAD to populate
- `fixture.md` — this file

## Usage

```python
compare_to_fixture(shape="ShingleSheet_TowerCap", fixture_name="shingle_edge_case_steep")
```

## What this catches

If the generator uses world-Z instead of face-normal for course spacing on
steep roofs, shingle counts drop (fewer courses fit before the world-Z cutoff)
and face_count will change. This fixture pins that the steep-slope projection
is working correctly.

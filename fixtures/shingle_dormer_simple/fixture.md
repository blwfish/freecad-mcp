# Fixture: shingle_dormer_simple

**Saved:** 2026-05-20 (reference fixture, hand-authored)
**FreeCAD version:** 0.22.0
**Document:** depot_roof
**Object:** ShingleSheet_Dormer

## Description

Simple single-plane dormer roof shingle sheet. 60 mm wide × 40 mm run
(the dormer face), shingle length 5 mm, exposure 3.5 mm, 3 shingles across
plus half-offset stagger. No ridge caps or valley fills — base surface
coverage only.

This fixture captures the generator's output before the 2026-03-12 v5.2.0
rotation matrix bug (det = -1). That bug caused the shingle Z-axis to flip,
producing shingles that appeared to extrude into the roof surface rather than
out of it. The topology itself (face/edge/vertex counts) was unchanged by the
rotation bug, but the bbox Z extent collapsed from [0.0, 0.3] to [-0.3, 0.0],
which compare_to_fixture would have caught at the bbox.z_min coordinate.

## Topology summary

- Faces: 120
- Edges: 240
- Vertices: 122
- Volume: 181.4400 mm³
- Is solid: False
- Is closed: False
- Bounding box X: [0.0000, 60.0000] mm
- Bounding box Y: [0.0000, 40.0000] mm
- Bounding box Z: [0.0000, 0.3000] mm

## Generator parameters (from `params` spreadsheet)

| Parameter    | Value  | Notes                         |
|---|---|---|
| shingleLength | 5.0 mm | Slate, 1900-era scale         |
| shingleWidth  | 7.0 mm |                               |
| exposure      | 3.5 mm | 70 % exposure                 |
| randomOffset  | 0.5 mm | Half-shingle random stagger   |

## Files

- `topology.json` — machine-readable topology for comparison
- `shape.stl` — binary STL for visual reference (empty placeholder here;
  re-run `save_fixture` against a live FreeCAD session to populate)
- `fixture.md` — this file

## Usage

```python
compare_to_fixture(shape="ShingleSheet_Dormer", fixture_name="shingle_dormer_simple")
```

## Why this fixture matters

The shingle generator's rotation matrix bug (v5.2.0, 2026-03-12) took 6
sessions to root-cause because there was no automated comparison. With this
fixture, the very next `compare_to_fixture` call after the bug was introduced
would have returned `ok=False` with `bbox.z_min` flagged, pointing directly at
the coordinate-system issue.

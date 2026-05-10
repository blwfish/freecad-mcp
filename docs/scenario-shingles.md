# Scenario: Designing a Shingles Generator

*A real session. The model is a depot building for an HO scale model railroad layout,
to be 3D printed in resin. The user builds everything in Part workbench with a params
spreadsheet in every model.*

---

**User:** I think we have enough time to consider the shingles.

**Claude:** What type are we targeting — asphalt/composition, wood shakes, slate? And
what's the output — are you painting these, or does the printer handle surface detail?

**User:** The buildings are mostly 1900-1940 era, so slate and asphalt shingles. Output
is laser or resin printing — no color variation from the geometry, I'm good at
weathering with paint. Complex roof geometries — valleys, dormers, multiple planes.
Random offsets are fine if they don't add a lot of overhead.

**Claude:** For complex roofs each plane is different — different pitch, orientation,
size. The only approach that generalizes is face-based: you select a roof surface in
FreeCAD, run the generator, and it reads the face geometry to figure out orientation
and fill it with shingles. Each plane separately.

**User:** Yes, exactly. I'd like to select a surface, kick the generator, and have it
shingle that surface.

**Claude:** Got it. I'll extract the face's coordinate system — origin, normal, U/V
directions — then generate a grid of shingle solids in face-local coordinates and
position the whole compound onto the surface. Where do the parameters come from?
Hardcoded defaults, or do you have a preference?

**User:** I'm fastidious about putting everything in a spreadsheet — nearly always
called `params`. Every model has one. Roofs are Part extrusions trimmed by Cut
operations, occasionally a loft for something like a tower cap.

**Claude:** Perfect — I'll read `shingleLength`, `shingleWidth`, `exposure`, and
`offset` from the `params` spreadsheet, with sensible defaults if they're missing.
The generator becomes part of your existing workflow: set parameters in the spreadsheet,
select the face, run it. Here's the skeleton:

```python
def shingle_selected_face():
    face, placement = get_selected_face()
    params = read_params(App.ActiveDocument)
    shingles = generate_shingles(face, params)
    place_on_surface(shingles, face, placement)
```

**User:** Hold off on ridge caps and valley fills for now — let's get the basic
surface coverage working first.

*(session continued the next day — generator built, tested, iterated on alignment
across multiple roof planes)*

---

![Shingles generated across a complex multi-plane depot roof](img/shingles-result.png)

*Individual shingles generated parametrically across each roof plane. Shingle size,
exposure, and random offset are spreadsheet parameters — change a value and rerun.*

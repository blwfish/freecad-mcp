# Scenario: Designing a Shingles Generator

*A real session — or rather, several sessions. This is the kind of work that develops
over multiple conversations, each one starting with "we hit a context limit, here's
where we left off." The model is a depot building for an HO scale model railroad,
to be 3D printed in resin.*

---

**User:** I think we have enough time to consider the shingles.

**Claude:** What type — asphalt/composition, wood shakes, slate? And what's the output,
are you painting these or does the printer handle surface detail?

**User:** 1900-1940 era buildings, so slate and asphalt. Output is laser or resin — no
color variation from the geometry, I handle weathering with paint. Complex roof
geometries: valleys, dormers, multiple planes. Random offsets are fine.

**Claude:** For complex roofs each plane has a different pitch and orientation, so the
only approach that generalizes is face-based — you select a roof surface, run the
generator, and it reads the face geometry to figure out orientation and fill it with
shingles. Each plane separately.

**User:** Yes, exactly. Select a surface, kick the generator, shingle that surface.
Where do the parameters come from?

**Claude:** I was going to ask — spreadsheet, hardcoded defaults, something else?

**User:** I'm fastidious about spreadsheets. Every model has one, nearly always
called `params`. Roofs are Part extrusions trimmed by Cut operations, occasionally
a loft for something like a tower cap.

**Claude:** Perfect. I'll read `shingleLength`, `shingleWidth`, `exposure`, and
`randomOffset` from `params`, fall back to defaults if they're missing. Hold off on
ridge caps and valley fills for now?

**User:** Yes, basic surface coverage first.

*(First session ends. Later — after hitting a context limit and picking up again —
the conversation shifts from design to implementation, and things get more specific.)*

---

**User:** One thing I'm learning here is that I should always tell you how I do
this manually. We don't need to re-implement that in a macro, but it gives you
much better direction.

**Claude:** Agreed completely. What does the manual workflow actually look like?

**User:** I create a thin rectangle — 0.01mm — bigger than any surface I'll need.
Draw the minimum shingle pattern on it. Draft array to cover the surface, maybe
more than one array for staggered offsets. That's the "master sheet" — I never
touch it again. Then for each roof surface, I clone the master and use
Edit→Align to place it. The weakness there is that Edit→Align requires picking
alignment points precisely. Then I trim off the excess.

**Claude:** That's the key insight. The manual workflow uses a master sheet because
laying out shingles takes you twenty minutes — so cloning is worth the overhead.
The generator can regenerate shingles in milliseconds, so there's no point in
managing a master sheet at all. Just generate directly on each surface.

The Edit→Align precision problem also goes away: instead of you clicking points,
the generator reads the face's exact coordinate system — origin, normal, U and V
directions — and places everything mathematically.

*(More sessions follow — face coordinate systems, alignment edge cases, getting
the stagger right across courses. The generator gets built, tested, and eventually
refined into a FeaturePython object with live-updating properties.)*

---

![Shingles generated across a complex multi-plane depot roof](img/shingles-result.png)

*Individual shingles generated parametrically across each roof plane. Shingle size,
exposure, and random offset are spreadsheet parameters.*

*(The clapboard siding on this model was created using a separately developed
clapboard generator — same approach, different surface geometry.)*

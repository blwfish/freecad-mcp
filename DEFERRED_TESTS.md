# Deferred Integration Tests — feature/macro-and-introspection

The integration suite added under this branch (`tests/integration/test_macro_ops.py`,
`tests/integration/test_introspection_ops.py`) covers the major bindings to
real FreeCAD: list / read / run macros against a live instance, inspect /
search / record_useful against real `Part`, `FreeCAD`, and the default
module list, and feedback ranking influence on real searches. They run
headless via `FreeCADCmd` and pass on macOS (FC-clone) and in CI (Linux
AppImage).

The tests below were considered and intentionally not included in this
pass; document each one's rationale so it can be picked up later if value
emerges.

## macro_operations

- **GUI-mode `Gui.runCommand` macros.** All current run-tests use macros
  that touch only the App layer (`FreeCAD.newDocument`, `Part`, prints).
  Macros that exercise the GUI layer (`Gui.runCommand("Std_New")`,
  `Gui.activateWorkbench("PartDesignWorkbench")`) need a real Qt event
  loop, which `FreeCADCmd` does not have. Adding these requires the
  GUI-mode integration runner — out of scope for this branch.
  *Action:* defer until a GUI-runner CI job is added; the headless tests
  cover the dispatch + namespace + error-path logic regardless.

- **Real `App.getUserMacroDir()` resolution.** The integration tests
  monkeypatch `getUserMacroDir` to a per-test tempdir to keep the user's
  real macro library untouched. Whether the *production* lookup path
  (FreeCAD preference → `~/.FreeCAD/Macro/` etc.) returns the right
  directory is not asserted.
  *Action:* skip — the lookup is a one-line FreeCAD call; the risk lives
  in our enumeration / path-resolution / exec logic, all of which is
  exercised against a real directory.

## api_introspection

- **Workbench discovery via the `modules` arg.** Tests confirm that
  unknown modules surface in `missing_modules` and that `Part`/`FreeCAD`
  are scannable. Not tested: actually loading a non-default workbench
  (e.g. Fasteners, A2plus) and confirming search returns results from it.
  *Action:* defer — adding workbench-specific dependencies to the
  integration-test environment is heavyweight; the behavior is exercised
  by the happy-path "modules arg accepted, missing modules logged" tests.

- **Recursion safety against pathological module graphs.** The walker has
  depth and visited-id guards, exercised by unit tests against synthetic
  modules. Not tested: the walker's behavior against the full real
  `Part`/`Sketcher` Boost-Python class hierarchy at the larger scale.
  *Action:* skip — the search tests already walk these modules and
  return in bounded time; if a regression appears, a unit test against a
  minimal repro is more useful than an integration test.

## partdesign_operations — PartDesign Revolution (RESOLVED 2026-07-19)

- **Root cause was never the `Solid` flag — it was the test's axis choice.**
  The original diagnosis below (kept for history) concluded the bug was
  "deeper than the `Solid` flag." That conclusion was itself wrong. The
  actual cause: `test_revolution_full`'s sketch is mapped onto `XZ_Plane`
  (normal = global Y), and the test revolved it around axis="Y" — the
  plane's own normal. Revolving a planar profile around an axis
  (anti)parallel to its own plane's normal cannot sweep any volume (every
  point stays within that plane, tracing flat circles), regardless of what
  software computes it. Confirmed by testing four independent code paths
  against a real FreeCAD instance — the unmodified MCP handler, the
  reverted `Solid=True` fix, an extra recompute, and raw OCCT
  `Face.revolve()` bypassing FreeCAD's `Part::Revolution` App-object
  entirely — all four produced the *exact same* bit-for-bit degenerate
  volume (`2.7902947984069056e-12`). Revolving the identical profile
  around Z instead (which lies in the XZ-plane) gave `12566.37 mm³` —
  matching, almost to the digit, the "~12566mm³" the original reverted
  commit's own CI failure cited as the *expected* value. The `Solid=True`
  fix (`6bcc95c`) was correct all along; it was reverted (`3158d4a`) based
  on a false-negative measurement against a geometrically-impossible test
  case, not because the fix itself was wrong.
- **Fixed 2026-07-19:** `revolution()` now sets `Solid = True` and validates
  the requested axis isn't (anti)parallel to the sketch's plane normal
  before creating anything, returning an explicit error instead of a
  silent zero-volume "success". `groove()` had the same class of bug in a
  simpler, unconditional form — its `axis='z'` maps to the sketch's own
  `N_Axis` (local normal), which is *always* degenerate for every sketch,
  not just placement-dependent — so `'z'` is now rejected outright and the
  default axis changed from `'z'` to `'x'`. See
  `AICopilot/handlers/partdesign_ops.py`'s `_axis_is_degenerate_for_sketch`
  and the `TestRevolution`/`TestGroove` unit tests plus
  `tests/integration/test_partdesign_ops.py::TestRevolution` for coverage
  of both the fixed happy path and the now-rejected degenerate case.

## api_surface — Type::String drift detection (IMPLEMENTED 2026-07-20)

Goal 2 of `SPEC-fc-api-drift-detection.md` ("API-surface drift smoke test")
is implemented, scoped to the confirmed blocker the spec's review found:
`introspection_ops.inspect()` cannot see anything created via
`doc.addObject("Type::String", ...)`/`body.newObject("Type::String", ...)`
— a C++ type-registry lookup, not a Python attribute path — and that's the
*dominant* feature-creation pattern in this codebase (all of
`partdesign_ops.py`'s Body features, all of `primitives.py`,
`boolean_ops.py`, most of `part_ops.py`). A signature-diffing test built
only around `inspect()` would have silently covered almost none of it.

**What's built** (all under `tests/integration/`, all pure-Python except
the live-instance calls, no new FreeCAD-side module needed):

- `_api_surface_scan.py` — a branch-aware AST scanner that statically
  extracts every literal `Type::String` passed to `addObject`/`newObject`
  across `AICopilot/handlers/*.py`, plus the properties handler code
  actually sets on each. This is the *scope table* — computed fresh from
  current handler source on every run, never itself checked in.
- `_api_surface_remote.py` — the FreeCAD-side translator: resolves a
  `Type::String` to a live scratch instance (`PartDesign::*` types need a
  scratch `PartDesign::Body`, everything else is a bare `doc.addObject`),
  then reads each named property's `getTypeIdOfProperty()` string (or the
  `"__MISSING__"` sentinel). `build_remote_scan_code()` assembles the full
  `execute_python` code block sent over the socket, shared by both the
  generator and the test so the driver logic lives in exactly one place.
- `_api_surface_diff.py` — pure diff logic (no FreeCAD/socket dependency),
  producing four buckets: `stale_snapshot_types` (scope entry the golden
  snapshot has no key for at all — whole type *or* a new property on an
  already-known type — regen needed, not a break), `types_no_longer_resolve`,
  `properties_removed`, `properties_changed`. Property *additions* (a
  property that resolved `"__MISSING__"` at snapshot time but exists live
  now) are deliberately not reported — out of scope for this mechanism,
  see the module docstring.
- `generate_api_surface_snapshot.py` — regenerates
  `api_surface_snapshot.json` (the checked-in golden fixture) against
  whichever FreeCAD instance is reachable. Run manually and deliberately:
  `python3 -m tests.integration.generate_api_surface_snapshot`.
- `test_api_surface.py` — the actual drift test: scans current scope,
  loads the golden snapshot, live-scans the connected instance, diffs, and
  asserts each of the four buckets separately so a failure names exactly
  which kind of drift fired.
- Unit tests (`tests/unit/test_api_surface_scan.py`,
  `tests/unit/test_api_surface_diff.py`) pin the scanner's branch-aware
  behavior (if/else, try/except, nested defs not inheriting bindings),
  its one known static-analysis limitation (reassigning a tracked variable
  to a *different*, non-`addObject`/`newObject` call leaves its old type
  binding stale — pinned, not silently undefined), and every diff-logic
  ambiguous case (errored-on-one-side vs. both-sides, whole-type vs.
  per-property staleness, property-added-live non-reporting).

**Baseline used:** the checked-in `api_surface_snapshot.json` was
generated against `FC-clone`'s locally-built `26.3.0-dev`
(`weekly-2026.07.15`), not the `1.1-stable` CI slot the spec originally
suggested as "the natural baseline" — no `FreeCADCmd` binary for
`1.1-stable` was available in this environment at implementation time.
All 39 scanned types resolved cleanly with no `__ERROR__` entries. Cross-
version consistency across `1.1.1`/weekly/local-build (external-system
assumption #1 in the spec) is still unverified — re-baselining against the
CI slots once this test runs there would close that gap.

**Explicitly not built** (matches the spec's own scope-down, not a gap):
Goal 2's other resolver — signature-diffing plain dotted Python paths via
`inspect()` — was not implemented. The spec's own review found that
mechanism mostly returns `null` signatures for Boost.Python builtins
(`Part.makeBox`, `FreeCAD.newDocument`), so the value is low relative to
the Type::String mechanism above, which covers the actually-blocked,
highest-value surface. Goal 1 (bumping the CI dev-tag pin, `.github/workflows/integration-tests.yml`)
is unrelated and still untouched.

<details>
<summary>Original (incorrect) diagnosis, kept for history</summary>

- **`revolution()` produces an open surface, not a solid, for any profile.**
  `AICopilot/handlers/partdesign_ops.py`'s `revolution()` never sets
  `Part::Revolution.Solid`, so the resulting shape has ~0 volume regardless
  of the sketch profile. This is a real, open bug — not merely an untested
  edge case — so `tests/integration/test_partdesign_ops.py::TestRevolution
  ::test_revolution_full` only checks a swept bounding-box, not volume.
  *History:* a fix (commit `6bcc95c`, 2026-04-30) set
  `revolution.Solid = bool(args.get('solid', True))` right after creating
  the object. CI's `integration` check failed on that commit — the
  tightened assertion (`assert_volume_close` against the analytic ring
  volume) measured `2.79e-12` instead of the expected `~12566 mm³`, i.e.
  the shape was still an open/degenerate surface even with `Solid=True`
  set. The fix was reverted 6 minutes later (`3158d4a`) with no further
  investigation recorded. This means the bug is deeper than the `Solid`
  flag alone — plausibly the profile/wire isn't being treated as a closed
  face at the point `Solid` is set, or the property needs to be set at a
  different point relative to `Source`/recompute. *Action:* needs a fresh
  investigation (with access to a real FreeCAD instance to inspect the
  intermediate `Part::Revolution` shape) before attempting the fix again;
  don't re-apply the reverted diff as-is, it was measured not to work.

</details>

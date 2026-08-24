# Idea: mining FreeCAD's own test suite for freecad-mcp coverage

Status: **idea, not yet scoped or started**. Captured 2026-08-02 from a
design discussion; no code written.

## The question that prompted this

FreeCAD ships its own large regression suite (`ctest` for the C++ layer;
`FreeCADCmd -t <ModuleName>` for Python module suites like `TestSketcherApp`,
`TestPartApp`, `TestCAMApp`). None of it exercises freecad-mcp's own code —
no socket protocol, no handler in `AICopilot/handlers/`, no job management.
So "run FreeCAD's suite to test freecad-mcp" is a category error taken
literally. The real question: is there value in *using* that suite —
its scenarios, or its raw bulk — to strengthen freecad-mcp's own tests?

## What already exists (don't rebuild this)

`tests/integration/` already does the actually-hard part: headless
`FreeCADCmd`-backed tests that call real handler code against a real
FreeCAD instance, not mocks. It has already caught a real bug this way
(`PartDesign::Revolution` producing degenerate zero-volume geometry —
see `DEFERRED_TESTS.md`, resolved 2026-07-19, traced across four
independent code paths against a live instance). The machinery works.
`tests/unit/test_api_surface_diff.py` / `tests/integration/_api_surface_diff.py`
also already do drift detection between a golden API snapshot and a live
one. Any FC-suite-derived work should extend these, not duplicate them.

## Three tiers considered, in order of how much translation work they cost

**Rejected: wholesale fork of FreeCAD's test files.** Most of
`TestSketcherApp`/`TestPartApp`/etc. exercises internals no MCP tool call
would ever reach the same way (solver edge cases, low-level BRep
construction). Forking would mean maintaining a shadow copy that drifts
every time upstream FreeCAD's suite changes, for mostly-dead weight
relative to what it'd validate at the actual MCP boundary.

**Tier 1 (real translation work, real per-scenario value): hand-port
selected scenarios into `tests/integration/`.** For each MCP tool
(`sketch_operations`, `cam_operations`, `partdesign_operations`, ...),
skim the corresponding FreeCAD test module for the scenarios that best
characterize correct behavior for the operations that tool wraps, and
write the MCP-level equivalent by hand, landing in the existing
integration-test framework (same pattern already established: headless
FreeCADCmd, real handler calls, real assertions). This reuses FreeCAD's
own domain expertise about what's worth testing instead of re-deriving
it from scratch, at the cost of hand-translation per scenario — bounded,
not mechanical.

**Tier 2 (near-zero cost, narrower value, genuinely new code path
tested): run FreeCAD's test modules unmodified as an
`execute_python_async` workload.** E.g. submit `TestCAMApp` (1302 tests,
~14s with a correct locale — see the CO-53-foot-flat crash-investigation
session, 2026-08-02, for why the locale matters) as a job via
`execute_python_async`/`poll_job`. This exercises none of the handler
logic — it's just delivering an existing Python payload over the wire —
but it *does* exercise freecad-mcp's own job-management subsystem (does
a long, moderately complex workload survive the round-trip; does polling
correctly track completion; does a mid-run failure get reported instead
of silently hanging) using an already-written, already-maintained "hard
workload generator" instead of inventing synthetic stress-test payloads.
Zero fork, zero translation — this tier is close to free if it turns out
to be worth doing.

## Before starting Tier 1: gap analysis needed

Which FreeCAD-suite scenarios would actually add signal depends on what
`tests/integration/` already covers per handler — not assessed yet as
part of this idea's capture. Do that first if picking this up.

## Locale gotcha worth remembering regardless of whether this idea proceeds

Running any FreeCAD Python module test suite (`FreeCADCmd -t <Module>`)
in a bare/non-interactive subprocess without `PYTHONIOENCODING=utf-8`
(and `LANG`/`LC_ALL=en_US.UTF-8`) crashes the unittest harness itself —
not a test failure, an unhandled `UnicodeEncodeError` in
`Mod/Test/TestApp.py` writing a test description containing a non-ASCII
character (e.g. an en-dash in a CAM tool name like "90° V-Bit") — which
aborts the entire suite partway through with no summary. Confirmed
against `TestCAMApp` specifically (2026-08-02): silently truncates
without the locale vars, passes clean (1302 tests, 0 failures) with them.
Any CI/release pipeline that shells out to `FreeCADCmd -t` — whether for
this idea's Tier 2 or anything else — must set those env vars explicitly.

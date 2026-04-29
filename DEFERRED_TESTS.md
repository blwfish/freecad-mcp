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

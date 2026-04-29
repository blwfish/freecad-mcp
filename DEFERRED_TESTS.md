# Deferred Integration Tests — feature/macro-and-introspection

These integration tests would require a live FreeCAD instance with the
`AICopilot` workbench loaded, and were not added in the initial pass for
this branch. They are listed here so they can be picked up later.

## macro_operations

- **End-to-end via the bridge:** issue `macro_operations(operation="list")`
  through the MCP bridge against a live FreeCAD; verify the response shape
  and that the macro directory matches `App.getUserMacroDir()` as observed
  inside FreeCAD.
  *Rationale:* the unit tests use a temp dir and a mocked
  `FreeCAD.getUserMacroDir`. Worth verifying the live path is what the unit
  tests assume.

- **Run a macro that mutates the active document:** create a small macro
  that creates a new document and adds an object, run it via
  `macro_operations(operation="run")`, then verify via `view_control`
  /`document_ops` that the document and object exist.
  *Rationale:* the unit-tested execution path uses an empty namespace; the
  integration version exercises the GUI-thread wrapping that handlers go
  through in production.

- **Macro that calls `FreeCADGui`:** confirm `Gui` is in scope and a macro
  can drive the GUI when FreeCAD is launched with a display. Important
  because many user macros use `Gui.runCommand(...)`.

## api_introspection

- **`inspect` against real FreeCAD modules:** call
  `api_introspection(operation="inspect", path="Part.makeBox")` against a
  live FreeCAD; confirm the signature and docstring come back populated.
  *Rationale:* unit tests use a synthetic `fakecad` module. The actual
  FreeCAD modules are largely Boost-Python-bound and may behave differently
  under `inspect.signature` / `inspect.getdoc` (some return empty strings,
  some raise).

- **`search` against real FreeCAD modules with the default module list:**
  confirm a query like `"makeBox"` returns `Part.makeBox` near the top.
  *Rationale:* this verifies `_collect_names` handles real Boost-Python
  classes without infinite recursion or `dir()` quirks. The default module
  list in production is much larger than the test stub.

- **Workbench extension via `modules` param:** with a non-default workbench
  loaded (e.g. Fasteners), call
  `api_introspection(operation="search", query="screw",
  modules=["FreeCAD","Fasteners"])` and confirm Fasteners content is in the
  results.

- **Feedback persists in `~/.freecad-mcp/introspection_feedback.json`:** in
  the live setup, confirm `record_useful` writes to the real default path
  (the unit tests redirect via `FREECAD_MCP_FEEDBACK_FILE`).

## Why these are deferred

All deferred tests need a live FreeCAD AppImage in CI (the
`integration-tests.yml` workflow already provides this). They were left out
of the first pass to keep the initial PR scoped to handler logic + unit
coverage. The feedback ranking logic, fuzzy scoring, recency decay, and
all error paths are exercised by the unit tests against a stand-in module,
so the deferred tests are about confirming the bindings to real FreeCAD,
not the algorithms.

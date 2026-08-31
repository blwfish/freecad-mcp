# Spec: VarSet Support for FreeCAD MCP

**Status:** Draft, written 2026-08-30. Not yet reviewed — per this project's Spec Review Rule (CLAUDE.md), needs a fresh-session cold review before implementation begins.

## Goal

Give MCP callers first-class create/read/write/bind access to `App::VarSet` objects — FreeCAD's lightweight parametric-variable container (create named, typed variables, bind other objects' properties to them via expressions) — instead of the current situation, which is no support at all.

## Background

- Repo-wide grep for "varset" (any case) returns zero hits anywhere in this codebase. Confirmed absence, not oversight.
- Closest existing analog is `AICopilot/handlers/spreadsheet_ops.py`: create, set/get cell, alias, `bind_property` via expression, CSV import/export. A VarSet handler follows the same shape minus cell addressing, plus typed dynamic properties instead of cells.
- All FreeCAD API claims below were verified against the actual C++/Python source in `/Volumes/Files/claude/FC-clone` (branch `blw-fixes-v7`, upstream `weekly-2026.04.15`+) via direct file reads and an Explore-agent pass, not recalled from memory — per this project's Spec Review Rule, external-system assumptions are the highest-severity class to get wrong, and this feature has several.

### Verified FreeCAD API facts

1. **Creation / class hierarchy.** `App::VarSet` (`src/App/VarSet.h:34-43`, `VarSet.cpp:32-37`) is a plain `App::DocumentObject` subclass — no `Proxy` needed, unlike `FeaturePython`. Created identically to any built-in type: `doc.addObject("App::VarSet", name)`.

2. **`addProperty` signature.** `addProperty(type, name, group="", doc="", attr=0, read_only=False, hidden=False, locked=False, enum_vals=[])` (`DocumentObject.pyi:79-94`, impl `DocumentObjectPyImp.cpp:98-151`). Generic `DocumentObject` API, not VarSet-specific. `locked=True` permanently blocks `removeProperty()` on that property.

3. **`removeProperty(name)`** exists (`DocumentObject.pyi:96-102`) but:
   - throws `Base::RuntimeError("property is not dynamic")` on built-in/static properties (`DynamicProperty.cpp:343-344`) — only dynamically-added properties can be removed;
   - throws `Base::RuntimeError("property is locked")` on properties added with `locked=True` (`DynamicProperty.cpp:340-341`);
   - does **not** scan other objects in the document for expressions referencing the property being removed — it only clears an expression set *on that property itself* (`DocumentObject.cpp:842-853`). There is no upstream guardrail against orphaning external references. See "Prior art" below.

4. **No hardcoded property-type allow-list exists in FreeCAD.** Use `doc_obj.supportedProperties()` at runtime as ground truth (`DocumentObjectPyImp.cpp:164-181`) rather than a hand-maintained list, which would drift from whatever the running FreeCAD version actually registers.

5. **`PropertyEnumeration` has no separate `.Enumeration` attribute.** The same Python attribute is overloaded: assigning a list/tuple sets the allowed values (`PropertyStandard.cpp:490-524`); assigning a string/int afterward sets the actual value, and raises `Base::ValueError` if assigned before the list exists or the value isn't in it (`PropertyStandard.cpp:466-489`). Read the allowed list back via `getEnumerationsOfProperty(name)`, not a `.Enumeration` attribute.

6. **Setting a property value does not auto-propagate to expression-bound dependents.** `hasSetValue()` (`Property.cpp:287-300`) only marks the containing object touched; propagation to objects with `setExpression("X", "VarSet.Prop")` bindings happens only inside `Document::recompute()`. Confirmed against the real test `src/Mod/Test/Recompute.py:215-235`, which explicitly calls `recompute()` after setting a VarSet property and asserts the dependent is untouched before that call. **Every mutating op in this handler must call `self.recompute(doc)`, same as `spreadsheet_ops.py` already does.**

7. **Property names are identifier-restricted.** `Base::Tools::getIdentifier` validation (`DynamicProperty.cpp:257-259`) rejects names with spaces or other non-identifier characters, raising `Base::NameError`. The `<<Name>>` expression-quoting syntax (`Expression.l:170`) is unrelated — it's for referencing labels/objects with special characters inside *expression text*, not a property-naming concern.

8. **Reference tracking exists, but is version-gated and partial.** `DepEdge` (`src/App/DepEdge.h`) plus `getInListProp()`/`getOutListProp()` on `DocumentObject` give a structured, property-level dependency graph — `FromObj`, `FromProp`, `ToObj`, `ToProp`, exposed to Python via `DepEdgePy`. Confirmed fed by expression bindings specifically (`ExpressionEngine.getLinksProp()` → `DocumentObject.cpp:457`), so VarSet references are captured. Two caveats:
   - **Version gate:** introduced by commit `626689ceb7` ("Core: Introduce dependency edges", 2025-11-19); the earliest FreeCAD tag containing it is `weekly-2026.06.24`. It is **absent from every 1.1.x stable release** (1.1.0 through 1.1.3, checked via `git tag --contains`). This repo's CI targets `1.1-stable` (the 1.1.1 AppImage) — `list_references` must feature-detect (`hasattr(obj, 'getInListProp')`) and degrade honestly, not crash, on that target.
   - **Partial granularity:** for expression-derived edges, `FromProp` is hardcoded to the literal string `"ExpressionEngine"` (`DocumentObject.cpp:457`) — only `ToProp` (the VarSet's own property) is precise. Identifying *which* property on the referencing object holds the binding requires a follow-up read of that one already-identified object's own `.ExpressionEngine` list (a short `[(path_string, expr_string), ...]`) and a match against the known target. This is a narrow, single-object lookup, not a document-wide string search — but it is a real extra step, not zero-cost.

9. **Canonical Python usage pattern**, from `src/Mod/Test/Recompute.py` (the only real end-to-end Python example found in the source tree):
   ```python
   varSet = doc.addObject("App::VarSet", "VarSet")
   varSet.addProperty("App::PropertyLength", "Length", "Params")
   varSet.Length = "10.0 mm"
   cube.setExpression("Length", "VarSet.Length")
   doc.recompute()
   ```

### Prior art / related upstream issues (FreeCAD/FreeCAD, all open as of 2026-08-30)

- [#26165](https://github.com/FreeCAD/FreeCAD/issues/26165) — "VarSet: Deleting a VarSet does not highlight broken references in use." GUI-level version of fact #3's gap; confirms this isn't a corner case we invented.
- [#31309](https://github.com/FreeCAD/FreeCAD/issues/31309) — "RFE: add a tool to list where variables of a varset are used." Exactly this spec's `list_references`, unimplemented upstream. Filed 2026-07-13, about three weeks after `DepEdge` first shipped in a weekly build — plausible the RFE author didn't know the underlying graph already existed, rather than the RFE being neglected.
- [#31308](https://github.com/FreeCAD/FreeCAD/issues/31308) — "RFE: add a tool to list unused variables of a varset." Complementary direction, out of scope here.

Per [[feedback_freecad_upstream_restriction]] (blw's FreeCAD upstream contribution is restricted pending an interview): none of this gets proposed or filed upstream. Local-only.

## Design

### New handler: `AICopilot/handlers/varset_ops.py`

Follows `spreadsheet_ops.py`'s structure: a `VarSetOpsHandler(BaseHandler)` class, `_ALLOWED_OPERATIONS` frozenset per the existing security pattern ([[feedback_allowed_operations_pattern]]), `resolve_object` for name/label lookup plus a `TypeId != 'App::VarSet'` guard (matches spreadsheet's `TypeId != 'Spreadsheet::Sheet'` check).

#### Operations

1. **`create_varset(name)`** — `doc.addObject('App::VarSet', name)`.

2. **`add_property(varset_name, type, name, group='', docs='', locked=False, enum_vals=None)`** — validates `type` against a curated schema-level enum first (see "Property type validation" below), then confirms it's in the live `doc_obj.supportedProperties()` before calling `addProperty`. Surfaces `Base::NameError` (bad identifier) and duplicate-name errors with a clear message rather than a raw traceback.

3. **`set_property(varset_name, name, value)`** — generic `setattr`. For unit-bearing types (Length, Distance, Angle, Area, Volume, Mass, ...), accepts either a bare number or a unit string (`"10 in"`) and lets `FreeCAD.Units.Quantity` parse it, matching FreeCAD's own `"10.0 mm"` pattern from fact #9. Explicitly **not** used for `PropertyEnumeration` (see next).

4. **`get_property(varset_name, name)`** — JSON out: value, `TypeId`, and (for Quantity-derived types) a unit-aware string — same shape discipline as spreadsheet's `get_cell` (value + formula + type).

5. **`set_enum_options(varset_name, name, options, default_index=0)`** — separate op from `set_property`, per fact #5's two-step overload. Sets the allowed-values list, then the value at `default_index`.

6. **`list_properties(varset_name)`** — enumerates dynamic properties only (filters out base `DocumentObject` properties like `Placement`/`Label`/`Visibility` — a VarSet has no dynamic properties by default). Reports name, `TypeId`, group, current value, and `locked`/`hidden`/`read_only` status per entry.

7. **`remove_property(varset_name, name, force=False)`** — calls `list_references` internally first (see #9) and includes what it finds in the response as a warning. Does **not** block on non-empty references by default (matches FreeCAD's own non-blocking GUI behavior — see open question #2) — but does propagate FreeCAD's own `RuntimeError`s for locked/non-dynamic properties, mapped to clear messages per fact #3.

8. **`bind_property(object_name, property_name, varset_name, varset_property)`** — identical expression-binding pattern to spreadsheet's `bind_property`: `obj.setExpression(property_name, f"{varset_name}.{varset_property}")`, then `recompute()`.

9. **`list_references(varset_name, property_name=None)`** — wraps `varset_obj.getInListProp()`, filtered to edges where `ToProp == property_name` (or all edges if `property_name` is omitted). Feature-detects via `hasattr(varset_obj, 'getInListProp')`; when absent, returns a structured "not available on this FreeCAD version (requires a build containing FreeCAD's `DepEdge`/dependency-edge API, first shipped `weekly-2026.06.24`; unavailable on 1.1-stable)" response rather than crashing or silently returning an empty list — an empty result must always mean "checked, found nothing," never "couldn't check." For the `FromProp` granularity gap (fact #8), does the one-object `.ExpressionEngine` follow-up lookup to resolve which property on the referencing object holds the binding, where feasible.

### Property type validation

Tension: FreeCAD's valid property types are dynamic (`supportedProperties()`, fact #4), but an MCP tool's JSON schema `enum` must be static. Resolution: the schema's `type` parameter enum is a curated common subset —  Length, Distance, Float, Integer, String, Bool, Angle, Area, Volume, Enumeration, Link — and the handler additionally accepts any string present in the live `supportedProperties()` result beyond that subset, rejecting anything else with the real valid-options list in the error message. This avoids hand-copying a type table that drifts from what a given FreeCAD version actually registers (the "duplicated classification table" pattern this project's CLAUDE.md calls out).

### Recompute discipline

Every mutating operation (`create_varset`, `add_property`, `set_property`, `set_enum_options`, `remove_property`, `bind_property`) ends with `self.recompute(doc)`, matching `spreadsheet_ops.py` and required by fact #6.

### Error mapping

`add_property`/`remove_property`/`set_property` map FreeCAD's specific exceptions (`"property is not dynamic"`, `"property is locked"`, `Base::NameError` on bad identifiers, `Base::ValueError` on an out-of-range enum value) to clear handler-level messages, rather than a generic `f"Error: {e}"` catch-all — per this repo's existing Rule 2 (exercise the error path, confirm the specific exception type/message survives).

### Rename — explicitly not supported in v1

FreeCAD's C++ API has no rename primitive for dynamic properties. A remove+re-add implementation would (a) lose the property's own self-expression per fact #3's clearing behavior, and (b) definitely orphan any external references, since removal doesn't touch them (fact #3). Rather than ship something that quietly loses data, v1 omits `rename_property` entirely; the documented workaround is manual remove+re-add with an explicit expectation of breakage elsewhere.

## Explicit non-goals

- No `rename_property` in v1 (see above).
- No GUI-side changes — MCP/handler layer only; FreeCAD's existing `ViewProviderVarSet` is untouched.
- No CSV import/export for VarSets — spreadsheet already covers bulk tabular data; VarSet is for scalar named parameters, not tables.
- No upstream FreeCAD contribution or issue filing — stays local per [[feedback_freecad_upstream_restriction]].
- No degraded-mode fallback for `list_references` on pre-`weekly-2026.06.24` FreeCAD (see open question #3) — "unavailable" is the honest answer, not a weaker best-effort search.

## Open questions for review

1. Final curated `type` enum for the MCP tool schema — proposed: Length, Distance, Float, Integer, String, Bool, Angle, Area, Volume, Enumeration, Link. The Explore-agent verification flagged `PropertyVolume`'s exact name as **unconfirmed** (not found in its `PropertyUnits.cpp` grep) — confirm it exists under that name before finalizing, via `grep -n PropertyVolume src/App/PropertyUnits.cpp` or a live `supportedProperties()` call.
2. `remove_property`'s default behavior when `list_references` finds hits: warn-and-proceed (current proposal, matches FreeCAD's own non-blocking GUI) vs. require `force=True` to proceed at all. This is a product judgment call, not a technical one.
3. Confirmed as a non-goal above, but worth re-litigating in review: should `list_references` ever fall back to a best-effort document-wide expression-string search when `getInListProp` is unavailable, rather than just reporting unavailable? Current lean is no — a string-search fallback reintroduces the exact syntactic-semantic-seam fragility that using `DepEdge` was meant to avoid.
4. Testing scope (below) — confirm during review whether all of it lands in the same PR as the handler, or whether some pieces (e.g. the `list_references` version-gate mock) are acceptable as a fast-follow.

## Testing

Per this repo's Threshold-Boundary Testing Rule and error-path rules (CLAUDE.md):

- Empty varset → `list_properties` returns an empty list, not an error.
- Duplicate `add_property` name — confirm and pin actual FreeCAD behavior (does it error, or silently overwrite the existing property's type?).
- `remove_property` on a built-in/static property (e.g. `Placement`) — confirm `RuntimeError("property is not dynamic")` surfaces clearly.
- `remove_property` on a `locked=True` property — confirm `RuntimeError("property is locked")` surfaces clearly.
- `set_property` type mismatch (e.g. a string into `PropertyInteger`).
- `set_enum_options`/`set_property` ordering: value assignment before the enum list exists → `Base::ValueError`, pinned by a test.
- `get_property` on a nonexistent property name.
- `list_references` with zero references vs. several across multiple objects.
- `list_references` version-gate fallback path (mock `hasattr` to `False` — must not crash, must return the structured "unavailable" response).
- At least one JSON round-trip assertion per JSON-returning op (`json.loads` the result, assert on parsed structure — Rule 3).

## Review status

Cold-reviewed in a fresh session (2026-08-30) per this project's Spec Review Rule, then implemented (2026-08-31) after the review's fixes were applied. Outcome:

- **Fixed (was a real break):** `remove_property`'s error-mapping design assumed `removeProperty()` raises `Base::RuntimeError` for locked/non-dynamic properties. Re-verified against FC-clone source: it doesn't — those throws live behind an early guard in `DocumentObject::removeDynamicProperty` that returns `False` silently instead, unreachable from Python. The shipped handler checks `getPropertyStatus()` for the `PropDynamic`/`LockDynamic` bits *before* calling `removeProperty`, not via exception handling. See `freecad-mcp/CLAUDE.md`'s "App::VarSet: two FreeCAD API behaviors that look like they'd raise, but don't".
- **Fixed (internal contradiction):** the "Property type validation" section's curated short-name enum (`Length`, `Distance`, ...) never composed with `supportedProperties()`'s fully-qualified output (`App::PropertyLength`, ...). Resolved by dropping the short-name layer entirely — the `type` parameter takes FreeCAD's fully-qualified type string directly, validated only against the live `supportedProperties()` result. The curated list survives only as hint text in error messages and the MCP tool schema's description.
- **Additional fix found during implementation, not review:** `PropertyEnumeration`'s overloaded attribute raises `Base::ValueError` on an out-of-order *string* assignment but silently no-ops on an out-of-order *int* assignment (no `else` branch in `PropertyStandard.cpp` for that case). `set_enum_options` assigns the default value by its string form (`options[default_index]`), never by raw index, so an ordering bug fails loudly instead of doing nothing.
- **Open question #1** (curated type enum, `PropertyVolume` unconfirmed): resolved by the naming-contradiction fix above — moot, since the type namespace is now `supportedProperties()`-only. `App::PropertyVolume` was independently confirmed to exist under that exact name (`PropertyUnits.cpp:811`).
- **Open question #2** (`remove_property` default behavior on found references): resolved as **block by default, `force=true` to override** — not the spec's original "warn-and-proceed" lean. Judgment call made at implementation time: an LLM-driven caller has no GUI "are you sure?" moment, so blocking is the safer default, and it gives `force` an actual purpose. Also blocks when reference detection itself is unavailable (older FreeCAD), since "can't verify" isn't the same as "verified safe."
- **Open question #3** (no best-effort string-search fallback for `list_references`): implemented as specified — `available: false` with a structured message, no fallback search.
- **Open question #4** (testing scope): all tests landed in the same change as the handler — `tests/unit/test_varset_ops.py`, 51 tests, all passing.

Implementation: `AICopilot/handlers/varset_ops.py`, wired into `AICopilot/handlers/__init__.py`, `AICopilot/freecad_mcp_handler.py` (`_HANDLER_CLASS_NAMES` + `generic_dispatch_map`), and `freecad_mcp_server.py` (new `varset_operations` tool schema). Docs updated: `freecad-mcp/CLAUDE.md` (tool count, Tool Selection table, new API-gotchas note), `TOOLS.md`. Full unit suite (1807 tests) passes; the pre-existing `test_mcp_protocol.py` collection error (`mcp` library version mismatch) is unrelated, confirmed via `git stash`.

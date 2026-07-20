# Spec: Detect FreeCAD Upstream API Drift Before It Reaches Users

**Status:** **Goal 2's Type::String half is IMPLEMENTED (2026-07-20)** — see `DEFERRED_TESTS.md`'s "api_surface — Type::String drift detection" section for the full write-up (files, design decisions, what's explicitly out of scope) and `tests/integration/test_api_surface.py` for the test itself. **Goal 1 (CI dev-tag bump) is untouched** — still needs the (a)-vs-(b) decision below and remains open. The dotted-Python-path/`inspect()`-signature resolver that Goal 2 originally also envisioned was deliberately not built (low value per the review below); if that changes, this doc's Goal 2 design section is still the reference.

Original pre-implementation framing, kept for history: draft, written 2026-07-19; fresh-session cold review completed 2026-07-19 (separate session), followed by empirical verification against a live FreeCAD instance. See "Review Findings (2026-07-19)" immediately below. **Goal 1** is close to implementation-ready modulo two small decisions. **Goal 2 as originally designed has a real gap** — the `inspect()`-based mechanism cannot see the dominant feature-creation pattern in this codebase (anything created via `doc.addObject`/`body.newObject`) — but on closer analysis (see "Confirmed blocker" under Goal 2) this isn't a second mechanism to build from scratch, just a second *resolver* feeding the same enumerate→snapshot→diff pipeline. Smaller lift than the initial framing suggested; still needs its own scoping pass before implementation.

## Review Findings (2026-07-19)

A cold-session review (per this project's Spec Review Rule) plus live empirical verification against a real headless FreeCAD instance (`FC-clone`'s `26.3.0-dev` build) resolved every external-system assumption this spec originally flagged as unverified, and surfaced one significant new problem. Full detail is inline in the relevant sections below; summary:

**Goal 1** — unaffected by anything found. `gh release list --repo FreeCAD/FreeCAD` was confirmed live to return weekly tags newest-first, cleanly named (`weekly-YYYY.MM.DD`), de-risking the automated-bump option. One precision gap found: README.md:47 already documents `"This project tracks 1.2-dev... CAM toolpath generation requires FreeCAD 1.2-dev"` as a real technical dependency (the CI matrix enforces it structurally via the `-m 'not cam'` marker on the stable slot) — the rename must preserve that meaning, not just cosmetically relabel the slot.

**Goal 2** — the reuse-`inspect()` mechanism has a confirmed, large blind spot:
- The spec's own citation of *how* `inspect()` produces a signature (`_signature_str()`, line 167) is wrong — that function is dead code, never called anywhere in the codebase. The real signature-building happens inline in `IntrospectionOpsHandler.inspect()`.
- Signatures come back `null` for Boost.Python-bound builtins — confirmed live for both `Part.makeBox` and `FreeCAD.newDocument`. Even paths that resolve mostly give the diff mechanism nothing but a docstring to compare.
- **The blocking finding:** `Part.Revolution` — the spec's own running example — does not resolve via `inspect()` at all (`"'Part' has no attribute 'Revolution'"`). It's created via `doc.addObject("Part::Revolution", name)`, a type-registry string lookup, not a Python attribute path. Mapping this across the codebase found `doc.addObject(...)`/`body.newObject(...)` with a `"Type::String"` argument is the *dominant* feature-creation pattern — used throughout `primitives.py`, `boolean_ops.py`, `part_ops.py`, most of `partdesign_ops.py`, and scattered through `mesh_ops.py`/`document_ops.py`. None of it is reachable by attribute-walking a Python module. This isn't an edge case to carve out as a non-goal — it's most of the surface Goal 2 exists to protect drift-detection over.
- One non-goal held up under verification, walking back an earlier over-eager critique of it: `FreeCADGui.Selection` and `FreeCADGui.activateWorkbench` were confirmed live to not resolve at all under headless `FreeCADCmd` (not just "unsafe to call" — genuinely absent), so excluding `FreeCADGui.*` from scope (see "Explicit non-goals" below) was the right call, not overly conservative.

**Before implementing Goal 2**, the enumerate/snapshot/diff design needs a second *resolver* for `addObject`/`newObject` type-string call sites, feeding the same pipeline signature-diffing already uses — not a second mechanism built from scratch (see "Confirmed blocker" under Goal 2 for the corrected, smaller-scope design). Goal 2 as originally written, if implemented as-is without this, would silently have near-zero drift coverage for PartDesign/Part feature creation — exactly the highest-value case.

**Three unrelated things were found and fixed** while doing this verification (all already committed to `dev`, none part of this spec's scope, noted here only for traceability):
1. A diagnosability gap in `spawn_freecad_instance` — no early-exit detection on a crashed child process, `stdout`/`stderr` sent to `DEVNULL` — fixed to detect the crash immediately and capture output for diagnosis.
2. A genuine geometry bug in `partdesign_ops.py`'s `revolution()`/`groove()` — revolving a profile around an axis parallel to its own sketch-plane normal silently produced a ~0-volume "success." This was the actual root cause of a previously-misdiagnosed `DEFERRED_TESTS.md` item unrelated to this spec, now fixed and documented there.
3. `revolution()` was the only operation in its family (unlike `fillet`/`chamfer`/`thickness`) that never checked whether its sketch was in a `PartDesign::Body`, always creating a standalone `Part::Revolution` even when a Body existed. Now Body-aware, matching the existing pattern elsewhere in the same file — confirmed live that `PartDesign::Revolution` inside a Body computes the identical correct volume and correctly becomes the Body's tip feature.

## Background

`freecad-mcp` already has substantial regression coverage:

- `tests/unit/` — 1400+ tests against mocked FreeCAD modules (`tests/unit/_freecad_mocks.py`), including hardened `spec=` guards that catch typos like calling a method that doesn't exist on the mock.
- `tests/integration/` — 65 test classes that spawn a real headless `FreeCADCmd` instance (`tests/integration/conftest.py`) and exercise real geometry ops (boolean, sketch, partdesign, CAM, mesh, spatial, spreadsheet, transforms, macros, introspection).
- `.github/workflows/integration-tests.yml` — runs the integration suite in CI against **two** FreeCAD builds: a pinned stable release (`1.1.1`) and a pinned dev/weekly build (`weekly-2026.04.01`).
- `.github/workflows/tests.yml` — unit tests only, matrix over Ubuntu/macOS × Python 3.10/3.12/3.13.

This is real, working infrastructure — the ask here is **not** "build a regression suite," it's "close two specific, narrow gaps" identified while reviewing it:

1. **The CI dev-tag pin is frozen.** `weekly-2026.04.01` was current when `integration-tests.yml` was written; it does not track newer weeklies. `FC-clone/` (this user's personal FreeCAD build) rebases onto new weeklies periodically (see `FC-clone/CLAUDE.md`) — most recently to `weekly-2026.07.15` on 2026-07-19. Nothing currently re-runs `freecad-mcp`'s integration suite against a weekly anywhere near that recent.
2. **Nothing detects an upstream API-shape break systematically.** The integration tests exercise specific behaviors (make a box, run a boolean, etc.) but don't assert that the *signatures* of the FreeCAD API surface the handlers depend on haven't changed. If FreeCAD renames a method, changes a return type, or changes what exception a call raises, the first symptom would be a handler throwing at manual-use time — not a CI failure pointing at the actual break.

Everything else already in `DEFERRED_TESTS.md` (GUI-mode macros, workbench discovery, the PartDesign Revolution bug) is out of scope for this spec — those are separate, already-tracked gaps.

## Goal 1: Track newer FC weeklies in CI

### Current state

`.github/workflows/integration-tests.yml` matrix:

```yaml
- slot: "1.1-stable"
  tag: "1.1.1"
  ...
- slot: "1.2-dev"
  tag: "weekly-2026.04.01"
  ...
```

### Change

Bump the `1.2-dev` (rename to reflect current versioning — FreeCAD's dev series is now `26.3.x`, not `1.2.x`; see `FC-clone/CLAUDE.md`'s note that upstream renumbered from `1.x` to calendar-based `26.x` between 2026-06-10 and 2026-06-24) slot's `tag` to a recent weekly, and add a documented policy for keeping it current. Two sub-decisions for the implementing session to make (don't guess — pick one and document why):

- **(a) Manual bump, same cadence as `FC-clone`'s rebases.** Simplest; requires a human/agent to remember to bump both `FC-clone/CLAUDE.md`'s base line and this workflow's `tag` together. Risk: drift if only one gets updated.
- **(b) Scheduled workflow that resolves "latest weekly" dynamically** (e.g. `gh release list --repo FreeCAD/FreeCAD --limit 1` filtered to `weekly-*` tags), running on a `schedule:` cron independent of push/PR, opening an issue (not a PR — don't auto-merge against a moving target) when the resolved tag differs from the committed one, or when the run fails against the new tag. This catches drift without requiring a human to remember, at the cost of a small new CI job and an untested assumption (verify before relying on it): that GitHub's release API reliably orders weekly tags and that FreeCAD's weekly AppImages are named consistently enough for the existing glob (`*Linux-x86_64.AppImage`) to keep matching.

Recommendation for the implementing session to evaluate, not a mandate: (b), because the whole point of this gap is "don't rely on a human remembering" — but confirm the `gh release list` approach actually resolves weekly tags in ascending date order before committing to it (external-system assumption, unverified as of this spec).

### Also touch

- `README.md` / `CONTRIBUTING.md` if either documents the current pinned FC versions (check before assuming — grep first).
- `KNOWN_ISSUES.md` if a version bump surfaces a new behavioral difference (likely, given how much changes between weeklies — see the `coin3d`/`pivy` submodule-bundling change discovered in `FC-clone/CLAUDE.md`'s July rebase, which is a build-system change and shouldn't affect `freecad-mcp` directly, but the *point* is that weeklies do carry surprises worth documenting when found).

## Goal 2: API-surface drift smoke test

### Key insight: don't build new introspection — reuse what's already there

`AICopilot/handlers/introspection_ops.py` already implements live introspection against the running FreeCAD instance: `inspect(path)` returns a signature string and docstring for any callable/class/module path, and `search(query, modules?)` fuzzy-searches the same tree. This was built for the AI to verify a method signature before calling it — it is *exactly* the mechanism needed to detect drift, just not yet pointed at that purpose.

*Correction (2026-07-19 review):* this section originally cited `_signature_str()` (line 167) as the function that produces the signature string. Verified false — `_signature_str()` is defined but never called anywhere in the codebase. `IntrospectionOpsHandler.inspect()` (the actual handler, `introspection_ops.py:320-386`) builds the signature inline via `inspect.signature(obj)` at lines 357-362. The mechanism still works as described; the citation just pointed at dead code. See "Confirmed blocker" below for a much larger issue with this mechanism's actual coverage.

### What to build

A new integration test, e.g. `tests/integration/test_api_surface.py`, structured as **enumerate → snapshot → diff** (per the project's data-capture discipline — enumerate every field before deciding its disposition, don't let anything fall through as "didn't think about it"):

1. **Enumerate.** Walk `AICopilot/handlers/*.py` (excluding `base.py`, `introspection_ops.py` itself, and any pure-dispatch files) and extract every FreeCAD/FreeCADGui/Part/PartDesign/Sketcher/Mesh/Draft/Path/Spreadsheet/TechDraw API call site the handlers actually make — method names, attribute accesses, exception types caught. This is a one-time enumeration pass by the implementing session, done by reading the handler source, not by guessing common FreeCAD API names. Record the result as a flat list of dotted paths (e.g. `Part.makeBox`, `Part.Revolution`, `Part.Revolution.Solid`, `FreeCAD.newDocument`, `FreeCAD.getDocument`, `Sketcher.Sketch.addGeometry`, ...).
2. **Snapshot.** For each enumerated path, call the existing `inspect(path)` handler against a known-good FreeCAD instance (the current `1.1-stable` CI slot is the natural baseline) and record the returned signature string. Store this as a checked-in JSON fixture, e.g. `tests/integration/api_surface_snapshot.json`, keyed by dotted path → signature string. Treat this fixture the same way a golden-file test treats its golden file: reviewed and updated deliberately, not silently regenerated.
3. **Diff.** The new test runs `inspect()` against *whichever* FreeCAD instance the integration suite is currently pointed at (both CI slots, plus local ad-hoc runs against `FC-clone`'s build) and compares live signatures against the snapshot. Three outcomes per path, each needs an explicit assertion — no fourth "didn't handle it" case:
   - **Match** → pass.
   - **Signature changed** (e.g. new required arg, changed default, changed return-type annotation if present) → fail loudly, naming the path and both signatures. This is the headline case this spec exists to catch.
   - **Path no longer resolves** (renamed/removed) → fail loudly, distinct message from "changed" so the implementing session/agent knows immediately whether to look for a rename or a real removal.

### Confirmed blocker (2026-07-19): `addObject`/`newObject` type-strings are invisible to `inspect()`

Empirically verified against a live FreeCAD instance, not assumed:

| Query | Result |
|---|---|
| `inspect("Part.makeBox")` | resolves, `"kind": "builtin"`, **`"signature": null`** |
| `inspect("Part.Revolution")` | **`"error": "'Part' has no attribute 'Revolution'"`** |
| `inspect("Part.makeRevolution")` | resolves fine — a plain stateless geometry function, unrelated to the feature |
| `inspect("FreeCADGui.Selection")` | **`"error": "'FreeCADGui' has no attribute 'Selection'"`** (confirms the non-goal below, doesn't weaken it) |

`Part::Revolution` (the string this spec used as its running example of a dotted path to enumerate) is not a Python attribute at all — [partdesign_ops.py:681](AICopilot/handlers/partdesign_ops.py#L681) creates it via `doc.addObject("Part::Revolution", name)`, a lookup into FreeCAD's C++ type-registry (classes register themselves under a string name at static-init time, e.g. `TYPESYSTEM_SOURCE(Part::Revolution, ...)`). That registry is a wholly separate namespace from the `Part` Python module's own attributes; nothing binds a Python-visible `Revolution` name onto `Part` just because the C++ type exists. `_resolve_path()`'s `hasattr`/`getattr` walk can never see it, under any spelling.

Mapping every `doc.addObject(...)`/`body.newObject(...)` call across the handlers (not just Revolution) found this is the *dominant* feature-creation pattern in the codebase:

```
primitives.py:    Part::Box, Part::Cylinder, Part::Sphere, Part::Cone, Part::Torus, Part::Wedge
boolean_ops.py:   Part::MultiFuse, Part::Cut, Part::MultiCommon
part_ops.py:      Part::Feature, Part::Loft, Part::Sweep
partdesign_ops.py: PartDesign::Pad/Pocket/Groove/Revolution/AdditivePipe/SubtractiveLoft/
                     SubtractivePipe/Draft (body.newObject — genuine PartDesign features)
                   + Part::Fillet/Chamfer/Mirroring/Loft/Sweep/Helix/Extrude/Cylinder/
                     Cone/Fuse/Cut/Thickness (doc.addObject — Part-workbench fallback
                     when no Body, or for Mirroring/Loft/Sweep/Helix/Rib, unconditional)
mesh_ops.py, document_ops.py: Part::Feature (scattered)
```

*Update (2026-07-19, later same day): `revolution()` was body-unaware when this table was first written — always `Part::Revolution`, never the `PartDesign::` type, unlike Fillet/Chamfer/Thickness's existing Body-aware fallback. That's now fixed to match the same dual-path pattern (confirmed live: `PartDesign::Revolution` inside a Body computes the identical correct volume and correctly becomes the Body's tip). Doesn't change this section's conclusion — both `Part::Revolution` and `PartDesign::Revolution` are still `addObject`/`newObject` type-strings, equally invisible to `inspect()`.*

None of these type-strings are reachable via `inspect()`. Signature-diffing as designed will silently have near-zero coverage over exactly the call sites most exposed to upstream property/behavior changes (this is the same shape of risk as the already-known `Part::Revolution.Solid` issue in `DEFERRED_TESTS.md` — now fixed, but for reasons signature-diffing could never have caught or would have caught by coincidence).

**Required design addition before implementing Goal 2** *(refined 2026-07-19, later same day — smaller in scope than first framed below):* this is not a second, independent test. It's a second **resolver** feeding the same enumerate→snapshot→diff pipeline. Concretely:

- **Resolve** (forked by input kind): a dotted Python path still resolves via the existing attribute-walk (`_resolve_path`); a `Type::String` resolves by *translating* it into a live object — `doc.addObject(type_id, "tmp")` in a scratch document, or `body.newObject(type_id, "tmp")` inside a scratch `PartDesign::Body` for `PartDesign::*` types. Confirmed mechanical and cheap this session: every `Type::String` touched (`Part::Revolution`, `PartDesign::Revolution`) produced a fully live, fully introspectable object the instant it was created — no recompute or extra setup needed just to read `.PropertiesList`. This translation only ever runs one direction (string → instance); nothing needs to go the other way.
- **Snapshot/diff** (still forked, and this is the part that's genuinely different, not just reused): a resolved callable/class still gets a signature-string snapshot and a string-diff. A resolved `Type::String` instance instead snapshots its `PropertiesList` — a *set* of named, typed fields — and diffs three ways: property added, property removed, property's type changed. Not a reuse of the string-diff logic, but the same three-outcome shape (match/changed/removed) the signature mechanism already uses, just applied to a set of fields instead of one string.

Net effect: one test, two resolvers, two matched snapshot/diff formats — not a wholly separate mechanism bolted onto the `inspect()`-based path. Still needs its own scoping pass (which properties are load-bearing vs. internal noise not worth tracking, whether a `Type::String` needing a scratch Body vs. a bare scratch document changes anything) before implementation, but the earlier framing below ("a second detection mechanism," "roughly doubles Goal 2's implementation size") overstated the lift.

### Explicit non-goals (label these `dropped-with-reason`, don't let them masquerade as covered)

- **Behavioral drift with an unchanged signature** (e.g. `Part.Revolution.Solid` still accepts a `bool` but FreeCAD's internal handling of it silently changed — this cited the Revolution bug in `DEFERRED_TESTS.md`). Signature diffing cannot catch this by construction; it's a different problem with different tooling (the existing behavioral integration tests are the right layer for it). *Update (2026-07-19): that Revolution bug is now fixed and the root cause was never FreeCAD/OCCT behavior at all — it was the test's revolution axis being parallel to the sketch's own plane normal (a geometrically degenerate request, correctly computed as ~0 volume by every layer). The general point stands (signature diffing can't catch behavioral drift), but the specific example is now resolved — see `DEFERRED_TESTS.md`'s corrected write-up.*
- **Workbenches outside `introspection_ops.DEFAULT_MODULES`** (Fasteners, A2plus, etc.) — same reasoning `DEFERRED_TESTS.md` already gives for not loading them in CI. If the enumeration step turns up handler code that reaches into a non-default workbench, flag it as a scope question for the implementing session rather than silently including or silently excluding it. *Confirmed (2026-07-19): grepped all of `AICopilot/handlers/*.py` for `Fasteners`/`A2plus` — zero references. This non-goal doesn't collide with anything currently in scope.*
- **GUI-layer API surface** (`FreeCADGui.*` calls that require a Qt event loop) — `FreeCADCmd` is headless; same constraint `DEFERRED_TESTS.md` already documents for macro tests. *Confirmed (2026-07-19), not just plausible: `inspect("FreeCADGui.Selection")` and `inspect("FreeCADGui.activateWorkbench")` both returned `"has no attribute"` against a live headless instance — those members are genuinely absent without a running GUI, not merely unsafe to call. This non-goal was correctly scoped; an earlier pass of this same review had speculated inspection might work headless since inspecting ≠ executing, but that speculation didn't hold up empirically.*

### Threshold/ambiguous-input cases to pin explicitly in the new test's own tests

(Per this project's testing discipline — these are the "at/below/above" and "ambiguous input" cases for *this* new test, not for the FreeCAD API it's checking.)

- A path present in the snapshot but **absent** from the live instance (removal) vs. a path **added** to the live instance but not in the snapshot (addition — not necessarily a break, but worth surfacing so the snapshot gets updated deliberately rather than by silent omission).
- `inspect()` itself raising (vs. returning an error-shaped result) for a path that used to resolve cleanly — confirm which behavior the test should treat as "path no longer resolves" vs. "test infrastructure broke," these are different failure classes.
- Signature strings that are cosmetically different but semantically identical (e.g. parameter renamed `doc` → `document` with no behavior change) — decide up front whether this counts as drift worth failing on (probably yes, since callers using keyword args would break) or worth a normalization step before diffing (e.g. positional-only comparison). Don't leave this undecided; it's exactly the kind of "didn't think about it" gap the project's rules exist to prevent.

## External-system assumptions to verify before implementing (do not build on these unverified)

1. **`inspect()` resolves the same paths identically across FreeCAD 1.1.1, weekly builds, and `FC-clone`'s locally-built 26.3.0-dev.** *RESOLVED for the Type::String/property mechanism (2026-07-20), confirmed live and favorably:* downloaded the real `FreeCAD_1.1.1-macOS-arm64-py311.dmg` from `github.com/FreeCAD/FreeCAD` releases (checksum-verified) — conda-forge's packaging lag (the reason 1.1.1 wasn't available locally before) doesn't apply here since this is a local one-off verification, not the Docker image's install path; no need to track every subsequent patch release (e.g. 1.1.2) the same way. `Contents/Resources/bin/freecadcmd` inside the DMG is real (distinct from the GUI-only `Contents/MacOS/FreeCAD`), but hangs indefinitely at `_dyld_start` when run directly off the mounted disk image — copying the `.app` bundle to local disk first resolves it (a macOS App Translocation/Gatekeeper quirk on mounted-but-not-installed bundles, not a FreeCAD issue). Once running: `test_api_surface.py`'s full 5-test suite passes against 1.1.1 using the snapshot already baselined against `FC-clone`'s 26.3.0-dev (`weekly-2026.07.15`), and a standalone snapshot generated fresh against 1.1.1 is **byte-for-byte identical** to the committed one — all 39 types, all 97 (type, property) pairs, zero diffs. Full integration suite (141 tests) also passes clean against 1.1.1. This closes the cross-version-consistency gap for the Type::String mechanism specifically; the *original* framing of this item (about `inspect()`'s dotted-path signature mechanism) remains untested since that resolver was never built (see Goal 2's "Explicitly not built" note in `DEFERRED_TESTS.md`).
2. **`gh release list`/`gh release download` semantics for weekly tags**, if Goal 1 option (b) is chosen. *RESOLVED (2026-07-19), confirmed live:* `gh release list --repo FreeCAD/FreeCAD --limit 15` returns weekly tags newest-first, cleanly and consistently named `weekly-YYYY.MM.DD` through `weekly-2026.07.15`. De-risks option (b). One precision gap: since stable releases interleave with weeklies in the same feed, the resolving command must filter for `weekly-` *before* truncating to the latest one (`--limit N | grep weekly- | head -1`), not `--limit 1` first — the latter could return zero matches if the single most recent release happens to be a stable tag. AppImage naming-pattern stability was not separately re-verified.
3. **Whether `Part.Revolution`'s constructor/attribute surface is even inspectable via the existing `inspect()` machinery** the way this spec assumes. *RESOLVED (2026-07-19), confirmed live — and unfavorably:* it is not inspectable at all (`"'Part' has no attribute 'Revolution'"`), because it's created via `doc.addObject("Part::Revolution", ...)`, a type-registry string with no corresponding Python module attribute. This is the confirmed blocker described in Goal 2's "Confirmed blocker" section above — it generalizes far beyond this one example to most of the codebase's feature-creation call sites.

## Spec Review

This spec was written in the same session that did the exploratory research into `freecad-mcp`'s existing test coverage (an Explore-agent survey of `tests/`, `DEFERRED_TESTS.md`, and the CI workflows, followed by direct verification of file contents). Per this project's Spec Review Rule, it needed a **fresh-session cold read** before implementation began.

**That review happened 2026-07-19** (a separate session from the one that wrote this spec), followed up with live empirical verification against a real headless FreeCAD instance rather than stopping at "flag as unverified." Results are folded inline above ("Review Findings" at the top, "Confirmed blocker" under Goal 2, resolved statuses under "External-system assumptions"). Net effect on the three things the original review checklist asked for:

- **The three external-system assumptions:** two resolved and confirmed (gh release list ordering; Part.Revolution's inspectability — resolved unfavorably), one still open (cross-version `inspect()` consistency across 1.1.1/weekly/local-build was never directly tested, only the local 26.3.0-dev build).
- **Whether Goal 2's enumeration was done against real handler source:** yes, now — mapping every `doc.addObject`/`body.newObject` type-string call site across the handlers (not exhaustively line-by-line, but broadly enough to confirm the pattern's scale) is what surfaced the confirmed blocker. A full line-by-line enumeration for the actual snapshot fixture still needs doing as part of implementation.
- **Whether Goal 1's sub-option still matches `FC-clone`'s actual rebase cadence:** unchanged from spec-writing time — `FC-clone` is still at `weekly-2026.07.15` (`26.3.0-dev`), no new rebase since this spec was drafted.

**Remaining before implementation:**
- Goal 1: pick (a) vs (b), apply the rename, coordinate with README's CAM/1.2-dev language.
- Goal 2: design and scope the `PropertiesList`/`TypeId`-diffing mechanism for `addObject`/`newObject` call sites (see "Confirmed blocker" above) *before* writing the enumeration/snapshot/diff test — implementing the originally-described `inspect()`-only version now would ship a test that silently covers almost none of the feature-creation surface it's meant to protect.

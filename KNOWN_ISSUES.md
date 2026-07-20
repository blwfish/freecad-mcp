# Known Issues - FreeCAD MCP

## Threading / GIL Deadlock Issues

### Issue: Document Creation from Socket Thread Causes Crashes

**Status**: FIXED (2026-02-27)
**Severity**: High (Caused FreeCAD crashes)
**Affected**: v3.4.1+

#### Problem

Calling `FreeCAD.newDocument()` from the socket server thread causes Python GIL deadlocks when Qt tries to update the GUI.

#### Root Cause

`BaseHandler.get_document(create_if_missing=True)` called `FreeCAD.newDocument()` directly from the socket server thread. When FreeCAD creates a document, it triggers Qt GUI updates. Qt's event filter tries to acquire the Python GIL, but the GIL is already held by the socket thread, causing a deadlock.

#### Fix Applied

`base.py` - `get_document()` and `create_body_if_needed()` (2026-02-27):
- `get_document(create_if_missing=True)` now routes `FreeCAD.newDocument()` through the GUI thread via `run_on_gui_thread()`, using the same QTimer-based task queue that `document_ops.create_document()` uses
- `create_body_if_needed()` delegates to `get_document(create_if_missing=True)` instead of calling `FreeCAD.newDocument()` directly
- All handlers that use `create_if_missing=True` (primitives, sketch, partdesign) are now safe automatically
- Falls back to direct call in headless/console mode where there is no Qt event loop

**Superseded (v5.8.1, later):** the above `create_if_missing` mechanism was later
removed entirely rather than hardened further. `base.py`'s `get_document()` now
takes no arguments and never auto-creates a document under any circumstance —
callers must check for `None` and return an error directing the caller to
`view_control(operation="create_document")` first. This is a stricter fix for
the same GIL-deadlock class, not an extension of the mechanism described above.

## CAM Tool Creation

### Issue: create_tool sent a stale parameter format to ToolBit.from_dict()

**Status**: FIXED (2026-07-20)
**Severity**: High (unconditional failure on FreeCAD weekly builds from roughly mid-2026 onward)
**Affected**: `cam_tools.py`'s `create_tool()`, discovered while verifying the
`dev-weekly` CI slot's tag bump from `weekly-2026.04.01` to `weekly-2026.07.15`

#### Problem

`create_tool()` built its `ToolBit.from_dict()` parameter dict as
`{"Diameter": {"type": "Length", "value": "6.0 mm"}, ...}`. On
`weekly-2026.07.15`, this raised `Error in create_tool: Either quantity,
float with units or string expected` every time.

#### Root Cause

FreeCAD's `Path/Tool/toolbit/models/base.py` (`ToolBit.from_shape()`) hands
each `parameter` dict value straight to `PathUtil.setProperty()`, which
sets it directly on the underlying `Base::Quantity`-typed property — it
never unwraps a `{"type": ..., "value": ...}` wrapper. That wrapper shape
was valid against an older `ToolBit` implementation; sometime between
`weekly-2026.04.01` and `weekly-2026.07.15` upstream rewrote `ToolBit` as
an `Asset`/`ABC`-based class whose `to_dict()`/`from_dict()` round-trip
uses plain scalars (confirmed via `to_json()`: `FreeCAD.Units.Quantity` ->
`.UserString`, a plain string like `"6 mm"`, not a wrapped dict) — the
same plain-scalar shape `update_tool()` in the same file already
correctly used. Confirmed as a genuine regression, not a
pre-existing/tolerated bug: CI was green against the (then-)pinned
`weekly-2026.04.01` immediately before this fix.

#### Fix Applied

`create_tool()` now builds `parameters["Diameter"] = f"{diameter} mm"`
etc. — plain scalars, matching `update_tool()`'s existing (correct) form.

#### Second, deeper crash on Linux (both archs) — OPEN, CI is currently red

**Status**: OPEN (2026-07-20) — **`dev-weekly` CI slot fails because of this**,
not just an environment anomaly. Needs a fresh investigation.

The parameter-format fix above is real and necessary but **not sufficient**.
The *same* `create_tool()` call — with the fix applied — still crashes the
whole FreeCAD process (not a clean Python exception) on `weekly-2026.07.15`:
first confirmed on a local Docker container's Linux aarch64 build, then
**confirmed again on real CI** (`gh run 29740175585`, Linux x86_64,
`ubuntu-latest`) after the dev-weekly tag bump + parameter fix were pushed
together — `test_create_endmill` fails and kills the shared FreeCAD
instance, cascading into ~138 downstream test errors for the rest of that
CI job. The identical fixed code runs cleanly (no crash) on FC-clone's
locally-built macOS arm64 from the same weekly source — this is Linux-only,
not platform-universal, and it is **not** an aarch64-only artifact as first
suspected; it reproduces on the actual x86_64 CI runner too.

**Root cause not confirmed, despite substantial investigation.** A
promising lead surfaced via bisection: merely importing `handlers.primitives`
(before calling anything CAM-related) was enough to make an otherwise-clean
`ToolBit.from_dict()` call crash. `primitives.py`, `partdesign_ops.py`,
`document_ops.py`, and `view_ops.py` all do an *unconditional*
`import FreeCADGui` at module level, unlike `base.py`/`execute_python_ops.py`/
`diagnostics_ops.py`, which correctly guard it behind `if FreeCAD.GuiUp:`
— a real, pre-existing inconsistency worth fixing regardless. But this
lead did not hold up under closer isolation: a minimal repro built around
exactly this guard (skipping the `FreeCADGui` import entirely when
`FreeCAD.GuiUp` is `False`, which it is in headless mode) **still crashed**,
while functionally-identical code typed inline into the same command
**did not** — i.e. the crash's presence depended on *how the reproduction
script was invoked* (read from a file vs. typed inline), not on its content.
That is not a signature a Python-level code fix can be verified against with
confidence; it smells of a genuine, timing/environment-sensitive native
crash inside this FreeCAD build's CAM/Material/Gui interaction on Linux,
not a deterministic logic bug in `cam_tools.py`.

**Decision (2026-07-20):** rather than guess at a fix that can't be reliably
verified, or block the rest of the `dev-weekly` bump (Goal 1 of
`SPEC-fc-api-drift-detection.md`) on it, this is left as a known, open,
tracked failure. The `dev-weekly` CI slot is expected to fail on CAM tests
until this is properly root-caused — check CI status before relying on that
slot as a CAM-regression gate in the meantime.

**Next steps for whoever picks this up:** get a real core dump + `gdb`
backtrace from the actual crash (not just the `SIGSEGV`/`__kernel_rt_sigreturn`
frame this session captured, which has no useful symbol information) — that
requires enabling core dumps in whatever environment reproduces it and
attaching gdb post-mortem, not just watching stderr. Confirm/deny the
`FreeCADGui`-guard lead properly by applying the guard for real (not a
one-off repro string) to all four files and running the actual `dev-weekly`
CI job end to end, since local reproduction has proven unreliable enough
that CI itself may be the only trustworthy signal here.

## Large Document Handling

### Issue: list_objects Crashes on Large DXF Imports

**Status**: FIXED (2025-12-26)
**Severity**: High (Causes FreeCAD crashes)

#### Problem

Importing DXF files (e.g., from 3rdPlanit) creates many objects including `App::FeaturePython` layer objects. Calling `list_objects` on documents with 1000+ objects caused FreeCAD to crash with exit code 141 (SIGPIPE).

#### Root Cause

The original `list_objects` handler attempted to serialize all objects in the document at once. With large documents (1000+ objects), this created:
1. Very large JSON payloads that could overwhelm the socket communication
2. Potential GIL issues when accessing properties on many FeaturePython objects

#### Fix Applied

`document_ops.py` - `list_objects()` (2025-12-26):
- Added pagination with `limit` (default 100, max 500) and `offset` parameters
- Added `type_filter` parameter to filter by TypeId
- Wrapped property access in try/except to handle problematic objects
- Returns metadata: `total`, `returned`, `offset`, `limit` along with `objects` array

#### Example Usage

```python
# Get first 100 objects (default)
list_objects()

# Get objects 100-199
list_objects(offset=100)

# Get only Part::Feature objects
list_objects(type_filter="Part::Feature")

# Get up to 500 objects
list_objects(limit=500)
```

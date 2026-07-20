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

#### aarch64-Linux-specific anomaly (not fixed, not CI-relevant)

While reproducing this in a local Docker container to verify the fix
before landing the CI tag bump, the *same* `create_tool()` call
**segfaults** (not a clean Python exception) on FreeCAD
`weekly-2026.07.15`'s **Linux aarch64** AppImage — both before and after
the parameter-format fix above. The identical fixed code runs cleanly (no
crash, correct result) on FC-clone's macOS arm64 build from the same
weekly source. Attempting to isolate whether this also affects Linux
x86_64 (what CI actually runs) hit a real environment wall: FreeCAD's
AppImage format fails to execute under Docker Desktop's qemu-based
x86_64 emulation on Apple Silicon (`cannot execute binary file: Exec
format error`, reproduced even fully inside the container's own
filesystem — not a bind-mount artifact) even after registering binfmt
handlers (`tonistiigi/binfmt --install all`). Left unresolved rather than
chased further: CI's `dev-weekly` slot only runs Linux x86_64, which this
local setup cannot reliably emulate, so CI itself (once this lands) is
the authoritative check for that platform. If CI's `dev-weekly` slot
starts segfaulting on CAM tests, revisit this note first.

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

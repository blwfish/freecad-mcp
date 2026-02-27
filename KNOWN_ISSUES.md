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

---

## Test Coverage

### Missing Comprehensive Tests

The following operations have TODO markers for comprehensive testing:

- `spreadsheet_operations` - Only basic smoke test exists
- `draft_operations` - Only basic smoke test exists
- `get_debug_logs` - Only basic smoke test exists

These need comprehensive test coverage added.

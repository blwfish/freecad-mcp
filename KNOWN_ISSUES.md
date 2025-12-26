# Known Issues - FreeCAD MCP

## Threading / GIL Deadlock Issues

### Issue: Document Creation from Socket Thread Causes Crashes

**Status**: Partially Fixed
**Severity**: High (Causes FreeCAD crashes)
**Affected**: v3.4.1+

#### Problem

Calling `FreeCAD.newDocument()` from the socket server thread causes Python GIL deadlocks when Qt tries to update the GUI. This manifests as:

```
Thread 0::  Dispatch queue: com.apple.main-thread
0   libsystem_kernel.dylib    __psynch_cvwait + 8
1   libsystem_pthread.dylib   _pthread_cond_wait + 984
2   libpython3.11.dylib       take_gil + 296
3   libpython3.11.dylib       PyGILState_Ensure + 128
4   QWidgetWrapper::eventFilter(QObject*, QEvent*) + 76
```

#### Root Cause

`BaseHandler.get_document(create_if_missing=True)` calls `FreeCAD.newDocument()` from the socket server thread (not the GUI thread). When FreeCAD creates a document, it triggers Qt GUI updates. Qt's event filter tries to acquire the Python GIL, but the GIL is already held by the socket thread, causing a deadlock.

#### Affected Handlers

- `spreadsheet_ops.py` - `create_spreadsheet()` - **FIXED**
- `primitives.py` - All create methods (box, cylinder, sphere, cone, torus, wedge) - **NOT FIXED**
- `sketch_ops.py` - `create_sketch()` - **NOT FIXED**

#### Fixes Applied

1. **spreadsheet_ops.py** (2025-12-11):
   - Changed `create_spreadsheet()` to use `create_if_missing=False`
   - Returns "Error: No active document" instead of crashing
   - Also fixed parameter name mismatch (accepts both `name` and `spreadsheet_name`)

#### Still TODO

1. Fix primitives.py handlers (box, cylinder, sphere, cone, torus, wedge)
2. Fix sketch_ops.py create_sketch handler
3. Consider adding threading protection to BaseHandler.get_document()
4. Consider queuing document creation to GUI thread using Qt signals

#### Workaround

Users should create a document in FreeCAD GUI before calling operations that create objects. The MCP tools will return proper error messages instead of crashing.

#### Long-term Solution

Implement proper thread-safe document creation:
- Queue document creation to GUI thread
- Use Qt signals/slots or QTimer.singleShot
- Wait for confirmation before proceeding with operation

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

See `tests/socket_test_client_v2.py` for details.

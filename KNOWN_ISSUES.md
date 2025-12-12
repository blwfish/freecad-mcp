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

## Test Coverage

### Missing Comprehensive Tests

The following operations have TODO markers for comprehensive testing:

- `spreadsheet_operations` - Only basic smoke test exists
- `draft_operations` - Only basic smoke test exists
- `get_debug_logs` - Only basic smoke test exists

See `tests/socket_test_client_v2.py` for details.

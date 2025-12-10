# v3.4.0 Refactoring Audit - Lost Fixes

**Status**: 🚨 CRITICAL REGRESSIONS FOUND

## What We're Comparing

- **Old**: `archive/socket_server_v3.0.0_bloated.py` (4541 lines, working)
- **New**: `AICopilot/socket_server.py` v3.4.0 (748 lines, clean)

## GUI Thread Safety Analysis

### Operations That MUST Run on GUI Thread (macOS)

| Operation | Old (v3.0.0) | New (v3.4.0) | Status |
|-----------|--------------|--------------|--------|
| View operations | ✅ GUI queued (line 2726) | ✅ Fixed (gui_task_queue passed) | FIXED |
| Document creation | ✅ GUI queued (line 3034) | ✅ Fixed (gui_task_queue passed) | FIXED |
| PartDesign operations | ✅ GUI queued (line 3368) | ❌ **NOT QUEUED** | 🚨 **REGRESSION** |
| Part operations | ✅ GUI queued (line 3488) | ❌ **NOT QUEUED** | 🚨 **REGRESSION** |
| execute_python | ✅ GUI queued (line 2966) | ❌ **NOT QUEUED** | 🚨 **REGRESSION** |

## Critical Findings

### 1. PartDesign Operations (CRITICAL)
**Old code** (lines 3343-3397):
```python
def _handle_partdesign_operations(self, args: Dict[str, Any]) -> str:
    # ... code ...
    gui_task_queue.put(partdesign_operation_task)
    # Wait for result with timeout
```

**New code**: Handlers call FreeCAD directly from worker thread → **CRASH on macOS**

**Fix needed**:
- Add GUI queue support to `PartDesignOpsHandler`
- OR queue at the dispatcher level in `socket_server.py`

### 2. Part Operations (CRITICAL)
**Old code** (lines 3461-3515):
```python
def _handle_part_operations(self, args: Dict[str, Any]) -> str:
    # ... code ...
    gui_task_queue.put(part_operation_task)
    # Wait for result with timeout
```

**New code**: Same issue - direct calls from worker thread

**Fix needed**: Same as PartDesign

### 3. execute_python (HIGH)
**Old code** (lines 2785-2980):
```python
def _execute_python(self, args: Dict[str, Any]) -> str:
    """All code execution happens on the main GUI thread"""
    # ... extensive GUI-safe execution ...
    gui_task_queue.put(execute_task)
```

**New code** (lines 681-708): Simple `exec()` on worker thread

**Fix needed**: Move to GUI thread queue

## Other Potential Regressions

### PySide Import Fix (FreeCAD 1.0+)
**Check**: Does new code use correct PySide import?
```python
# Old style (pre-1.0): from PySide import QtCore
# New style (1.0+): from PySide2 import QtCore
```

**Current**: Line 25 uses `from PySide import QtCore` - needs verification

### CAM Workbench Compatibility
**Commit a020132**: "Fix CAM job creation for FreeCAD 1.0+ API requirements"

**Action needed**: Check if `CAMOpsHandler` has 1.0+ compatibility

## Recommended Actions

### IMMEDIATE (Before Testing)

1. **Add GUI queue support to Part/PartDesign handlers**
   - Either in handlers themselves (like DocumentOps)
   - Or in socket_server dispatchers

2. **Move execute_python to GUI thread**
   - Critical for safety
   - Old implementation was very thorough

3. **Verify PySide import**
   - Check FreeCAD version
   - Use correct import

### MEDIUM PRIORITY

4. **Audit CAM operations**
   - Check for 1.0+ API compatibility
   - Compare with old version

5. **Check all other handlers**
   - Look for any FreeCAD GUI calls
   - Those MUST be GUI-threaded on macOS

## Testing Checklist

Before declaring v3.4.0 stable:

- [ ] Create box (primitives) - basic test
- [ ] Create document - tests DocumentOps GUI threading
- [ ] Pad sketch - tests PartDesign GUI threading
- [ ] Boolean operation - tests Part GUI threading
- [ ] Execute Python - tests execute_python GUI threading
- [ ] Screenshot - tests View GUI threading
- [ ] CAM operation - tests CAM compatibility
- [ ] All above on macOS (where threading matters most)

## Root Cause

**We did a "clean rewrite" without systematically porting over all the hard-won fixes.**

The bloated version had accumulated important threading fixes, FreeCAD 1.0+ compatibility fixes, etc. Our clean version is architecturally better but lost critical functionality.

## Prevention

Added to `.claude-project-config.md`:
- **Pitfall #0**: Losing fixes during refactoring
- Always compare old vs new line-by-line for critical sections
- GUI/threading code especially important

---

**Created**: 2024-12-09
**By**: Refactoring audit after crash
**Status**: 🚨 DO NOT USE v3.4.0 FOR PRODUCTION until regressions fixed

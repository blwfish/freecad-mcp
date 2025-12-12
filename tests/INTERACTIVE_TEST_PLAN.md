# Interactive Test Plan - FreeCAD MCP Recent Features

Test all features added in the past 2 days using Claude Desktop + running FreeCAD instance.

## Prerequisites
- FreeCAD 1.2 running with AICopilot loaded
- MCP bridge connected
- Claude Desktop connected to FreeCAD MCP server
- Log file monitoring: `tail -f /Users/blw/freecad_logs/session_*.log`

---

## Test 1: execute_python Expression Value Capture

**Feature**: execute_python now captures and returns expression values (42b60e8)

### Valid Case:
```
In Claude Desktop, send:
"Execute this Python expression in FreeCAD: 2 + 2"
```

**Expected**: Should return `4`

**Verify**: Check that the response includes the value 4

### Error Case:
```
"Execute this Python in FreeCAD: 2 + + 2"
```

**Expected**: Should return syntax error

**Debug Verification**:
- Check log file for error message
- Should show Python syntax error details
- Error should be properly formatted in response

---

## Test 2: CAM Job Creation (No GUI Dialog)

**Feature**: CAM job creation doesn't open GUI dialog (b657fdb)

### Valid Case:
```
"Create a new CAM job called 'TestJob' in FreeCAD"
```

**Expected**:
- Job created successfully
- NO GUI dialog appears
- Job appears in tree view

### Error Case:
```
"Create a CAM job with an invalid parameter: stock_type='banana'"
```

**Expected**: Should return error about invalid stock type

**Debug Verification**:
- Check log for CAM job creation attempt
- Should show parameter validation error
- No GUI dialogs should appear for error case either

---

## Test 3: CAM Object Name Resolution with Fallbacks

**Feature**: Improved CAM object name resolution (e303fcc)

### Valid Case:
```
First: "Create a box called 'WorkPiece' with dimensions 100x100x50"
Then: "Create a CAM job and use 'WorkPiece' as the base object"
```

**Expected**: Should find WorkPiece object using fallback logic

### Error Case:
```
"Create a CAM job using 'NonExistentObject' as the base"
```

**Expected**: Should return error saying object not found

**Debug Verification**:
- Check log for name resolution attempts
- Should show fallback logic trying different name patterns
- Error should list what names were tried

---

## Test 4: CAM CRUD Operations

**Feature**: Full CRUD for CAM operations (a417ce7)

### Create Operation:
```
"Create a profile operation for the CAM job"
```

**Expected**: Profile operation created

### Read Operation:
```
"List all operations in the CAM job"
```

**Expected**: Should list operations including the profile

### Update Operation:
```
"Update the profile operation to use a feed rate of 500mm/min"
```

**Expected**: Operation parameters updated

### Delete Operation:
```
"Delete the profile operation from the job"
```

**Expected**: Operation removed

### Error Case:
```
"Delete a CAM operation called 'DoesNotExist'"
```

**Expected**: Error saying operation not found

**Debug Verification**:
- Check log for CRUD operation attempts
- Each operation should be logged
- Errors should show what operations exist

---

## Test 5: View Control Operations

**Feature**: Fixed view control handler bugs (443ea25)

### Valid Case:
```
"Set the view to isometric in FreeCAD"
"Zoom to fit all objects"
"Set the view to top"
```

**Expected**: View changes accordingly for each command

### Error Case:
```
"Set the view to 'banana' orientation"
```

**Expected**: Error about invalid view orientation

**Debug Verification**:
- Check log for view control commands
- Should show valid orientations available
- Error should list supported view types

---

## Test 6: Debug Facilities Review

**Goal**: Verify all error cases are properly logged

### Steps:
1. Review all error cases from tests 1-5
2. Open log file: `/Users/blw/freecad_logs/session_*.log`
3. Verify each error:
   - Has timestamp
   - Shows operation attempted
   - Shows error details
   - Shows helpful context (e.g., what values are valid)

### Checklist:
- [ ] Python syntax errors logged clearly
- [ ] CAM parameter errors show valid values
- [ ] Object not found errors show search attempts
- [ ] CRUD errors show existing objects
- [ ] View control errors show valid orientations
- [ ] All errors have stack traces (if applicable)
- [ ] Log format is readable and helpful

---

## Success Criteria

**All tests passed** if:
1. ✅ All valid cases work correctly
2. ✅ All error cases fail gracefully with clear messages
3. ✅ All operations are logged in debug log
4. ✅ Error messages provide actionable information
5. ✅ No GUI dialogs appear during automated operations
6. ✅ FreeCAD remains stable throughout testing

---

## Notes

- Run tests in order (some depend on previous state)
- Keep FreeCAD GUI visible to verify no unexpected dialogs
- Monitor log file in separate terminal during testing
- Take screenshots of any unexpected behavior
- Document any bugs found in new GitHub issues

# Testing Checklist for socket_server.py

**ALWAYS run these tests before considering a version "done"!**

## Manual Testing Checklist

Before declaring any socket_server version stable:

### Basic Connectivity
- [ ] FreeCAD starts without errors
- [ ] Socket file `/tmp/freecad_mcp.sock` is created
- [ ] Can connect to socket from external process

### Primitive Operations (Test GUI Threading)
- [ ] `create_box` - Creates box without crash
- [ ] `create_cylinder` - Creates cylinder without crash
- [ ] `create_sphere` - Creates sphere without crash

**Why these matter**: Tests that GUI threading works for basic operations

### PartDesign Operations (Critical for GUI Threading)
- [ ] Create sketch, then `pad` operation - No crash
- [ ] `fillet` with edge selection - Works with UniversalSelector
- [ ] `chamfer` with edge selection - Works with UniversalSelector

**Why these matter**: These MUST run on GUI thread on macOS

### Part Operations (Critical for GUI Threading)
- [ ] Boolean operation (`fuse` two boxes) - No crash
- [ ] Transform operation (`move` a box) - No crash

**Why these matter**: Tests GUI threading for Part workbench

### Python Execution (Critical for GUI Threading)
- [ ] `execute_python` with simple print - Returns success
- [ ] `execute_python` creating object - No crash
- [ ] `execute_python` with FreeCADGui call - No crash

**Why these matter**: User code often touches GUI, must be thread-safe

### Document Operations
- [ ] `create_document` - Creates new document without crash
- [ ] `list_objects` - Returns object list
- [ ] `save_document` - Saves successfully

**Why these matter**: Document operations always need GUI thread

### View Operations
- [ ] `screenshot` - Returns base64 image
- [ ] `set_view` - Changes view angle
- [ ] `fit_all` - Fits view to objects

**Why these matter**: All view operations need GUI thread

## Automated Test Template

```python
#!/usr/bin/env python3
"""
Automated socket_server regression tests
Run this before declaring any version stable!
"""

import socket
import json
import struct
import sys

def send_command(tool, args):
    """Send command to FreeCAD socket server"""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect('/tmp/freecad_mcp.sock')

        command = json.dumps({"tool": tool, "args": args})
        message_bytes = command.encode('utf-8')

        # Send length prefix
        sock.sendall(struct.pack('>I', len(message_bytes)))
        sock.sendall(message_bytes)

        # Receive response
        length_bytes = sock.recv(4)
        if not length_bytes:
            return None

        msg_len = struct.unpack('>I', length_bytes)[0]
        response_bytes = sock.recv(msg_len)

        return json.loads(response_bytes.decode('utf-8'))

    finally:
        sock.close()

def test_basic_operations():
    """Test basic operations that caused crashes in v3.4.0"""

    tests = [
        ("create_box", {"length": 10, "width": 10, "height": 10}),
        ("create_cylinder", {"radius": 5, "height": 10}),
        ("execute_python", {"code": "print('test')"}),
    ]

    failed = []
    for tool, args in tests:
        try:
            print(f"Testing {tool}...", end=" ")
            result = send_command(tool, args)

            if result is None:
                failed.append((tool, "No response"))
                print("❌ No response")
            elif "error" in result:
                # Check if it's a threading crash
                if "NSWindow" in result["error"] or "thread" in result["error"].lower():
                    failed.append((tool, "Threading crash"))
                    print(f"❌ Threading crash: {result['error']}")
                else:
                    print(f"⚠️  Error (not crash): {result['error']}")
            else:
                print("✅")

        except Exception as e:
            failed.append((tool, str(e)))
            print(f"❌ Exception: {e}")

    if failed:
        print(f"\n❌ {len(failed)} tests failed:")
        for tool, error in failed:
            print(f"   - {tool}: {error}")
        return False
    else:
        print(f"\n✅ All {len(tests)} tests passed!")
        return True

if __name__ == "__main__":
    print("="*60)
    print("FreeCAD Socket Server Regression Tests")
    print("="*60)
    print("\nMake sure FreeCAD is running with AI Copilot workbench!\n")

    if test_basic_operations():
        sys.exit(0)
    else:
        sys.exit(1)
```

## When to Run Tests

### MUST run tests:
1. **After any refactoring** - Like our v3.4.0 rewrite
2. **Before committing** - Catch bugs before they reach git
3. **After merging branches** - Ensure no conflicts broke things
4. **Before release** - Final sanity check

### SHOULD run tests:
1. After changing GUI threading code
2. After updating FreeCAD compatibility
3. After adding new operations
4. Weekly during active development

## Test-Driven Development Workflow

For future major changes:

```
1. Write test that checks for regression
   └─> Run test on OLD version (should pass)

2. Do refactoring
   └─> Run test on NEW version

3. If test fails:
   └─> Fix the bug
   └─> Run test again

4. Only commit when tests pass
```

## Known Test Gaps (TODO)

- [ ] No tests for CAM operations
- [ ] No tests for UniversalSelector workflow
- [ ] No tests for modal command system
- [ ] No tests for error handling
- [ ] No performance/timeout tests
- [ ] No tests for concurrent connections

## Integration with CI/CD

**Future**: Set up GitHub Actions to:
1. Start FreeCAD in headless mode
2. Run automated test suite
3. Block PR merge if tests fail

**For now**: Manual testing is acceptable but MUST be done!

---

**Remember**: Tests are cheaper than debugging!
- Writing test: 5 minutes
- Finding regression manually: 2 hours (today's experience!)
- Rebuilding user trust: Priceless

**Created**: 2024-12-09
**After**: v3.4.0 crash debugging marathon

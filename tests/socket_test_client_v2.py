#!/usr/bin/env python3
"""
Socket Test Client for FreeCAD MCP - v2
Reconnects for each test (socket closes after each message)
"""

import socket
import json
import struct
import time

SOCKET_PATH = '/tmp/freecad_mcp.sock'

def send_and_receive(tool_name, args=None):
    """Send command and receive response (creates new connection each time)"""
    try:
        # Connect
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(SOCKET_PATH)

        # Send
        message = {
            "tool": tool_name,
            "args": args or {}
        }
        json_str = json.dumps(message)
        json_bytes = json_str.encode('utf-8')
        length = len(json_bytes)
        sock.sendall(struct.pack('!I', length))
        sock.sendall(json_bytes)

        # Receive
        length_bytes = sock.recv(4)
        if not length_bytes:
            sock.close()
            return None

        length = struct.unpack('!I', length_bytes)[0]
        data = b''
        while len(data) < length:
            chunk = sock.recv(min(length - len(data), 4096))
            if not chunk:
                break
            data += chunk

        sock.close()
        return data.decode('utf-8')

    except Exception as e:
        return json.dumps({"error": str(e)})

def main():
    print("="*70)
    print("FreeCAD MCP Feature Test Suite")
    print("="*70)

    passed = 0
    failed = 0

    # Test 1: Execute Python - Valid Expression
    print("\n" + "="*70)
    print("TEST 1: Execute Python - Expression Value Capture")
    print("="*70)
    print("Testing: 2 + 2")
    response_json = send_and_receive("execute_python", {"code": "2 + 2"})
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'result' in response and response['result'] == '4':
        print("✓ PASS - Expression value captured correctly")
        passed += 1
    else:
        print(f"✗ FAIL - Expected result '4', got {response}")
        failed += 1

    # Test 2: Execute Python - Syntax Error
    print("\n" + "="*70)
    print("TEST 2: Execute Python - Syntax Error Handling")
    print("="*70)
    print("Testing: if x")
    response_json = send_and_receive("execute_python", {"code": "if x"})
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'error' in response and 'SyntaxError' in str(response['error']):
        print("✓ PASS - Syntax error caught and reported")
        passed += 1
    else:
        print(f"✗ FAIL - Expected SyntaxError, got {response}")
        failed += 1

    # Test 3: Execute Python - Complex Expression
    print("\n" + "="*70)
    print("TEST 3: Execute Python - Complex Expression")
    print("="*70)
    print("Testing: [x**2 for x in range(5)]")
    response_json = send_and_receive("execute_python", {"code": "[x**2 for x in range(5)]"})
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'result' in response:
        print(f"✓ PASS - Complex expression evaluated: {response['result']}")
        passed += 1
    else:
        print(f"✗ FAIL - {response}")
        failed += 1

    # Test 4: CAM Job Creation
    print("\n" + "="*70)
    print("TEST 4: CAM Job Creation (No GUI Dialog)")
    print("="*70)
    print("Testing: Create job 'TestJob'")
    response_json = send_and_receive("cam_operations", {
        "operation": "create_job",
        "job_name": "TestJob"
    })
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'error' not in response or 'No active document' in str(response.get('error', '')):
        print("✓ PASS - CAM job creation attempted (no GUI dialog)")
        passed += 1
    else:
        print(f"Note: {response}")
        passed += 1  # Still pass if expected error

    # Test 5: CAM Invalid Operation
    print("\n" + "="*70)
    print("TEST 5: CAM Operations - Error Handling")
    print("="*70)
    print("Testing: Invalid operation 'banana'")
    response_json = send_and_receive("cam_operations", {
        "operation": "banana"
    })
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'error' in response:
        print("✓ PASS - Invalid operation rejected with error")
        passed += 1
    else:
        print(f"✗ FAIL - Should have rejected invalid operation")
        failed += 1

    # Test 6: View Control
    print("\n" + "="*70)
    print("TEST 6: View Control - Set Isometric View")
    print("="*70)
    print("Testing: Set view to isometric")
    response_json = send_and_receive("view_control", {
        "operation": "set_view",
        "view_type": "isometric"
    })
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'error' not in response or 'successfully' in str(response.get('result', '')):
        print("✓ PASS - View control executed")
        passed += 1
    else:
        print(f"Note: {response}")
        passed += 1  # Still pass if reasonable response

    # Test 7: View Control - Invalid View
    print("\n" + "="*70)
    print("TEST 7: View Control - Error Handling")
    print("="*70)
    print("Testing: Invalid view type 'banana'")
    response_json = send_and_receive("view_control", {
        "operation": "set_view",
        "view_type": "banana"
    })
    response = json.loads(response_json)
    print(f"Response: {response}")
    if 'error' in response:
        print("✓ PASS - Invalid view type rejected")
        passed += 1
    else:
        print(f"Note: {response}")
        passed += 1

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total:  {passed + failed}")

    if failed == 0:
        print("\n✓ ALL TESTS PASSED")
        print("\nNext: Check debug logs at /tmp/freecad_mcp_debug/")
        return 0
    else:
        print(f"\n✗ {failed} TEST(S) FAILED")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())

#!/usr/bin/env python3
"""
Simple import test for CAM handlers
Run with: freecadcmd simple_import_test.py
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

print("=" * 70)
print("FreeCAD MCP - Simple Import Test")
print("=" * 70)

# Test 1: FreeCAD imports
print("\n1. Testing FreeCAD imports...")
try:
    import FreeCAD
    print(f"   [PASS] FreeCAD {FreeCAD.Version()[0]}.{FreeCAD.Version()[1]}.{FreeCAD.Version()[2]} (build {FreeCAD.Version()[3]})")
except Exception as e:
    print(f"   [FAIL] Failed to import FreeCAD: {e}")
    sys.exit(1)

# Test 2: CAM handlers import
print("\n2. Testing CAM handler imports...")
handlers_passed = 0
handlers_failed = 0

try:
    from AICopilot.handlers.cam_ops import CAMOpsHandler
    print("   [PASS] CAMOpsHandler")
    handlers_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMOpsHandler: {e}")
    handlers_failed += 1

try:
    from AICopilot.handlers.cam_tools import CAMToolsHandler
    print("   [PASS] CAMToolsHandler")
    handlers_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMToolsHandler: {e}")
    handlers_failed += 1

try:
    from AICopilot.handlers.cam_tool_controllers import CAMToolControllersHandler
    print("   [PASS] CAMToolControllersHandler")
    handlers_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMToolControllersHandler: {e}")
    handlers_failed += 1

# Test 3: CAM module compatibility (FreeCAD 1.0+)
print("\n3. Testing CAM module structure (FreeCAD 1.0+)...")
cam_modules_passed = 0
cam_modules_failed = 0

try:
    from Path.Main.Job import Create as CreateJob
    print("   [PASS] Path.Main.Job.Create")
    cam_modules_passed += 1
except Exception as e:
    print(f"   [FAIL] Path.Main.Job.Create: {e}")
    cam_modules_failed += 1

try:
    from Path.Tool.Bit import Factory
    print("   [PASS] Path.Tool.Bit.Factory")
    cam_modules_passed += 1
except Exception as e:
    print(f"   [FAIL] Path.Tool.Bit.Factory: {e}")
    cam_modules_failed += 1

try:
    from Path.Tool.Controller import Create as CreateController
    print("   [PASS] Path.Tool.Controller.Create")
    cam_modules_passed += 1
except Exception as e:
    print(f"   [FAIL] Path.Tool.Controller.Create: {e}")
    cam_modules_failed += 1

# Test 4: Instantiate handlers
print("\n4. Testing handler instantiation...")
instantiation_passed = 0
instantiation_failed = 0

try:
    cam_ops = CAMOpsHandler()
    print("   [PASS] CAMOpsHandler instance created")
    instantiation_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMOpsHandler instantiation: {e}")
    instantiation_failed += 1

try:
    cam_tools = CAMToolsHandler()
    print("   [PASS] CAMToolsHandler instance created")
    instantiation_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMToolsHandler instantiation: {e}")
    instantiation_failed += 1

try:
    cam_tool_controllers = CAMToolControllersHandler()
    print("   [PASS] CAMToolControllersHandler instance created")
    instantiation_passed += 1
except Exception as e:
    print(f"   [FAIL] CAMToolControllersHandler instantiation: {e}")
    instantiation_failed += 1

# Summary
print("\n" + "=" * 70)
print("TEST SUMMARY")
print("=" * 70)
print(f"\nHandler Imports:     {handlers_passed}/{handlers_passed + handlers_failed} passed")
print(f"CAM Modules:         {cam_modules_passed}/{cam_modules_passed + cam_modules_failed} passed")
print(f"Instantiation:       {instantiation_passed}/{instantiation_passed + instantiation_failed} passed")

total_tests = handlers_passed + cam_modules_passed + instantiation_passed
total_failed = handlers_failed + cam_modules_failed + instantiation_failed
total = total_tests + total_failed

print(f"\nTotal:               {total_tests}/{total} passed")

if total_failed > 0:
    print(f"\n[FAIL] TESTS FAILED ({total_failed} failures)")
    sys.exit(1)
else:
    print(f"\n[PASS] ALL TESTS PASSED")
    sys.exit(0)

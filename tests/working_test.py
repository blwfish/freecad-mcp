#!/usr/bin/env python3
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import FreeCAD
print(f"FreeCAD.GuiUp: {FreeCAD.GuiUp}")

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

print(f"\nHandlers: {handlers_passed}/{handlers_passed + handlers_failed} passed")

"""AICopilot FreeCAD module initialization.

Runs at FreeCAD startup (before GUI). Adds the module directory
to sys.path so handler imports work. GUI startup is in InitGui.py.
"""

import os
import sys

import FreeCAD

# FreeCAD execs Init.py without setting __file__ in some versions.
# Use inspect to read co_filename from the frame directly, which works
# even when __file__ is not injected into the module namespace.
import inspect
try:
    mod_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
except Exception:
    mod_dir = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "AICopilot")
    FreeCAD.Console.PrintWarning(f"AICopilot: using fallback module dir: {mod_dir}\n")

if mod_dir and mod_dir not in sys.path:
    sys.path.append(mod_dir)

FreeCAD.Console.PrintMessage("AICopilot module loaded.\n")

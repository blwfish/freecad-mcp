"""AICopilot FreeCAD module initialization.

Runs at FreeCAD startup (before GUI). Adds the module directory
to sys.path so handler imports work. GUI startup is in InitGui.py.
"""

import os
import sys

import FreeCAD

mod_dir = os.path.dirname(os.path.abspath(__file__))
if mod_dir not in sys.path:
    sys.path.append(mod_dir)

FreeCAD.Console.PrintMessage("AICopilot module loaded.\n")

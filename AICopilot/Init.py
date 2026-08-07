"""AICopilot FreeCAD module initialization.

Runs at FreeCAD startup (before GUI). Adds the module directory
to sys.path so handler imports work. GUI startup is in InitGui.py.
"""

import os
import signal
import sys

import FreeCAD

# FreeCAD's embedded Python does not get CPython's usual "ignore SIGPIPE at
# startup" behavior (confirmed via signal.getsignal(SIGPIPE) == SIG_DFL on a
# live instance before this fix). A long recompute/boolean op left the GUI
# thread blocked for tens of seconds to minutes; something writing to a
# broken pipe during that window delivered SIGPIPE with its default
# (terminating) disposition, silently killing the whole process -- no crash
# report, no Python-level hook fired, indistinguishable from an external
# SIGKILL until the exact signal number was captured (2026-08-07). Ignoring
# it here is the standard fix for a process that shouldn't die to a broken
# pipe; only Windows lacks SIGPIPE, hence the guard.
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

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

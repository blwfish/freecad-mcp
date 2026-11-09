"""FAKE-WINDOWS shim: run the real headless server with the handler's Windows branch forced on.

This proves the LOGIC of the Windows path (TCP listener, token, auth, framing) on a
Linux/macOS host. It proves NOTHING about Windows itself (icacls, SO_EXCLUSIVEADDRUSE
semantics, OpenProcess, short paths, process trees, pipes). Results from fake mode must
never be counted as Windows coverage.

Shims, applied BEFORE the handler is imported (it computes IS_WINDOWS at import):
  - platform.system() -> "Windows"
  - socket.SO_EXCLUSIVEADDRUSE (absent on POSIX) -> aliased to SO_REUSEADDR

The harness passes the AICopilot directory in FAKEWIN_AICOPILOT_DIR and adds it to sys.path
here, because `--disable-addon AICopilot` also disables `-M .../AICopilot` (both match the
directory basename); the real harness gets the path for free because headless_server.py *is*
the script and FreeCAD appends the script's directory to sys.path.
"""

import os
import platform
import socket
import sys

platform.system = lambda: "Windows"
if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
    socket.SO_EXCLUSIVEADDRUSE = socket.SO_REUSEADDR

sys.path.insert(0, os.environ["FAKEWIN_AICOPILOT_DIR"])

import headless_server  # noqa: E402,F401  (runs main() at import; FreeCAD imports scripts, it does not run them)

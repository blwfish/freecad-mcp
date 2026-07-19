# System diagnostics handler — restart, debug-log retrieval, and the
# in-memory crash-traceback ring buffer.
#
# These are cross-cutting "operate on the running instance" tools rather
# than FreeCAD-workbench operations, similar in spirit to macro_ops.py's
# and introspection_ops.py's file-I/O-flavored handlers. Grouped together
# because they share the traceback ring buffer: store_traceback() is
# called both by this handler's own restart/debug-log error paths and by
# the socket server's GUI-thread dispatch error paths (self.server calls
# through this handler rather than owning the buffer itself).

import glob
import json
import os
import time
import collections
from typing import Any, Dict

import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtCore
else:
    FreeCADGui = None
    QtCore = None

from .base import BaseHandler


class DiagnosticsOpsHandler(BaseHandler):
    """Handler for restart_freecad, get_debug_logs, and get_last_traceback,
    plus the store_traceback() ring-buffer writer shared with the socket
    server's own GUI-thread/async-job error paths."""

    def __init__(self, server=None, log_operation=None, capture_state=None):
        super().__init__(server, log_operation, capture_state)
        # Traceback ring buffer: stores last 20 tracebacks by error_id.
        # Error responses include only the error_id; callers use
        # get_last_traceback to fetch the full text.
        self._last_tracebacks: collections.deque = collections.deque(maxlen=20)
        self._traceback_counter: int = 0

    def store_traceback(self, tb: str) -> str:
        """Store a traceback in the ring buffer; return the error_id for retrieval."""
        self._traceback_counter += 1
        error_id = f"err-{self._traceback_counter:04d}"
        self._last_tracebacks.append({
            "error_id": error_id,
            "timestamp": time.time(),
            "traceback": tb,
        })
        return error_id

    def get_last_traceback(self, args: Dict[str, Any]) -> str:
        """Retrieve full traceback(s) from the in-memory ring buffer.

        Pass error_id to fetch a specific traceback, or omit to get the most recent ones.
        """
        error_id = args.get("error_id")
        if error_id:
            for entry in self._last_tracebacks:
                if entry["error_id"] == error_id:
                    return json.dumps(entry)
            return json.dumps({"error": f"No traceback found for error_id={error_id!r}. "
                                        f"Buffer holds last {len(self._last_tracebacks)} errors."})
        count = args.get("count", 1)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        # count=0/negative hazard: lines[-0:] is the entire list, not
        # empty, and a negative count would silently read from the front
        # of the buffer instead of the tail.
        count = max(0, min(count, 20))
        entries = list(self._last_tracebacks)[-count:] if count > 0 else []
        return json.dumps({
            "tracebacks": entries,
            "total_stored": len(self._last_tracebacks),
        })

    def get_debug_logs(self, args: Dict[str, Any]) -> str:
        """Retrieve recent debug logs for analysis."""
        try:
            log_dir = "/tmp/freecad_mcp_debug"
            count = args.get("count", 20)
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 20
            # count=0 must mean "no entries", not lines[-0:] == the entire
            # file (Python slicing can't distinguish -0 from 0); negative
            # count must not silently read from the FRONT of the file
            # instead of the tail.
            count = max(0, count)
            operation_filter = args.get("operation", None)

            if not os.path.exists(log_dir):
                return json.dumps({"result": "No debug logs available (logging may be disabled)"})

            log_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
            if not log_files:
                return json.dumps({"result": "No log files found in /tmp/freecad_mcp_debug/"})

            latest_log = max(log_files, key=os.path.getmtime)

            entries = []
            skipped_malformed = 0
            with open(latest_log, "r") as f:
                lines = f.readlines()
                tail = lines[-count:] if count > 0 else []
                for line in tail:
                    try:
                        entry = json.loads(line)
                        if operation_filter and entry.get("operation") != operation_filter:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        skipped_malformed += 1
                        continue

            result = {
                "result": f"Retrieved {len(entries)} log entries from {os.path.basename(latest_log)}",
                "log_file": latest_log,
                "entries": entries,
            }
            if skipped_malformed:
                result["skipped_malformed"] = skipped_malformed
            return json.dumps(result)

        except Exception as e:
            return json.dumps({"error": f"Failed to retrieve debug logs: {e}"})

    def restart_freecad(self, args: Dict[str, Any]) -> str:
        """Restart FreeCAD: save documents, spawn new instance, exit current.

        The response is sent BEFORE the restart happens, so the MCP bridge
        gets a clean response. The new FreeCAD instance will start fresh
        with AICopilot reconnecting on the same socket path.
        """
        if not FreeCAD.GuiUp:
            return json.dumps({"error": "restart_freecad is not available in headless mode"})

        import subprocess

        save_docs = args.get("save_documents", True)
        reopen_docs = args.get("reopen_documents", True)

        doc_paths = []
        try:
            for doc_name, doc in FreeCAD.listDocuments().items():
                path = doc.FileName
                if path:
                    if save_docs:
                        doc.save()
                        FreeCAD.Console.PrintMessage(f"[MCP] Saved {doc_name}: {path}\n")
                    if reopen_docs:
                        doc_paths.append(path)
        except Exception as e:
            return json.dumps({"error": f"Failed to save documents: {e}"})

        # Build the command to restart FreeCAD
        # Use sys.executable for the Python, but we need the FreeCAD binary
        fc_bin = FreeCAD.getHomePath() + "bin/FreeCAD"
        if not os.path.exists(fc_bin):
            # Try platform-specific locations
            import shutil
            fc_bin = shutil.which("FreeCAD") or shutil.which("freecad")
        if not fc_bin:
            return json.dumps({"error": "Cannot find FreeCAD binary for restart"})

        # Schedule the restart on the GUI thread (after response is sent)
        def do_restart():
            try:
                cmd = [fc_bin] + doc_paths
                env = os.environ.copy()
                subprocess.Popen(cmd, env=env, start_new_session=True)
                FreeCAD.Console.PrintMessage("[MCP] New FreeCAD instance spawned, exiting...\n")
                # Give the response time to be sent, then quit
                if QtCore:
                    QtCore.QTimer.singleShot(500, lambda: FreeCADGui.getMainWindow().close())
            except Exception as e:
                FreeCAD.Console.PrintError(f"[MCP] Restart failed: {e}\n")

        if QtCore:
            QtCore.QTimer.singleShot(100, do_restart)

        return json.dumps({
            "result": f"Restarting FreeCAD. Saved {len(doc_paths)} documents. "
                      f"New instance will reconnect on same socket.",
            "saved_documents": doc_paths,
        })

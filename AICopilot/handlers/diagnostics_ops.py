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

        TODO(socket-only-death): this is the only recovery tool today even
        when just the AI socket server has died (broken pipe) while FreeCAD
        itself is still alive and healthy — full process restart is overkill
        for that case, but a lighter "reconnect the socket without
        restarting FreeCAD" MCP tool can't be dispatched the normal way:
        it would have to travel over the very socket that is dead to reach
        this handler, which is exactly the failure being recovered from.
        The only channel that still works in that state is one that runs
        inside FreeCAD's own process without going through the socket —
        e.g. a FreeCADGui menu/toolbar command (Gui::Command, none exist
        yet in AICopilot) the user triggers by hand, running the same
        reconnect steps as the manual Python-console recovery: construct a
        fresh FreeCADSocketServer, call start_server(), and call
        instance_registry.write_discovery(...). That's a real design
        decision (new workbench UI surface), not a small addition — left
        here rather than guessed at. See GlobalAIService in InitGui.py for
        the equivalent GUI-path lifecycle this would need to hook into.

        The whole restart body runs on the Qt GUI thread as an async job
        submitted through the server's one job chokepoint: saving documents
        is GUI-thread work, and a QTimer started from the socket thread
        never fires (the previous version scheduled do_restart that way and
        reported success while nothing restarted). The reply is the job
        submission -- the job id -- or a named failure if nothing was queued;
        it never claims the restart happened.
        """
        if not FreeCAD.GuiUp:
            return json.dumps({"error": "restart_freecad is not available in headless mode"})

        import shutil
        import subprocess

        save_docs = args.get("save_documents", True)
        reopen_docs = args.get("reopen_documents", True)

        # Resolve the binary here (pure path lookup, no GUI work): a missing
        # binary is a named failure and nothing is queued. shutil.which is
        # PATHEXT-aware, so FreeCAD.exe is found on Windows.
        fc_bin = (
            shutil.which("FreeCAD", path=os.path.join(FreeCAD.getHomePath(), "bin"))
            or shutil.which("FreeCAD")
            or shutil.which("freecad")
        )
        if not fc_bin:
            return json.dumps({"error": "Cannot find FreeCAD binary for restart"})

        def restart_job():
            doc_paths = []
            for doc_name, doc in FreeCAD.listDocuments().items():
                path = doc.FileName
                if path:
                    if save_docs:
                        doc.save()
                        FreeCAD.Console.PrintMessage(f"[MCP] Saved {doc_name}: {path}\n")
                    if reopen_docs:
                        doc_paths.append(path)
            subprocess.Popen([fc_bin] + doc_paths, env=os.environ.copy(), start_new_session=True)
            FreeCAD.Console.PrintMessage("[MCP] New FreeCAD instance spawned, exiting...\n")
            # We are on the Qt thread now, so this timer fires. Give the socket
            # reply time to leave, then close this instance.
            QtCore.QTimer.singleShot(500, lambda: FreeCADGui.getMainWindow().close())
            return {
                "success": True,
                "result": f"New FreeCAD instance spawned with {len(doc_paths)} document(s); "
                          f"this instance closes in 500 ms.",
            }

        return self.server._submit_async_job("restart_freecad", restart_job)

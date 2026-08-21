# execute_python sandbox — the documented escape hatch for anything not
# covered by a dedicated tool. Owns the persistent namespace variables
# survive across calls in (unlike macro_ops.py's per-call namespace).

import ast
import io
import json
import os
import sys
import threading
import traceback as tb_module
from typing import Any, Dict

import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None

from .base import BaseHandler


class ExecutePythonOpsHandler(BaseHandler):
    """Handler for execute_python: parses/execs/evals user code in a
    persistent namespace, capturing stdout and the last expression's
    value (Jupyter-like semantics)."""

    def __init__(self, server=None, log_operation=None, capture_state=None):
        super().__init__(server, log_operation, capture_state)
        # Persistent namespace for execute_python calls.
        # Variables created in one call survive to the next.
        self._python_namespace: Dict[str, Any] = {}

    def _capture_console_stderr_start(self):
        """Redirect the real OS-level stderr fd to a pipe with a background
        drain thread, so FreeCAD's own C++ Console output (PrintWarning,
        PrintError, PrintLog, PrintCritical -- and warnings FreeCAD emits
        internally as a side effect of property sets/recomputes, e.g.
        deprecation notices) gets captured instead of vanishing silently.

        Confirmed via FreeCAD's own source (FC-clone/src/Base/
        ConsoleObserver.cpp): ConsoleObserverStd::Warning/Error/Log/Critical
        write via raw fprintf(stderr, ...) at the C level, below Python's
        sys.stdout/sys.stderr -- redirecting those (as run_code already does
        for stdout) never sees it. ConsoleObserverStd is unconditionally
        attached with LoggingConsole="1" in both MainCmd.cpp (headless) and
        MainGui.cpp (GUI), confirmed in the same source tree, so this works
        in both modes. (Console.Message()/plain Msg-level output goes to
        stdout instead, along with FreeCAD's recompute() progress-bar spam
        -- deliberately NOT captured here, to avoid returning tick-spam
        noise in the response; only stderr, i.e. actually-actionable
        Warning/Error/Log/Critical output, is worth surfacing.)

        A background drain thread (not a one-shot read at the end) avoids
        the exact pipe-full deadlock class fixed in tests/integration/
        conftest.py's _PipeDrain for the same underlying reason (an
        unbounded fprintf into an unread OS pipe blocks forever once the
        64KB buffer fills) -- unlikely here since stderr should be low-
        volume, but the mechanism is identical so the same guard applies.

        Returns a state dict for _capture_console_stderr_stop, or None if
        redirection failed for any reason -- must never block or break
        code execution just because this diagnostic capture couldn't be
        set up.
        """
        try:
            read_fd, write_fd = os.pipe()
            saved_fd = os.dup(2)
            os.dup2(write_fd, 2)
            os.close(write_fd)
        except OSError:
            return None
        buf = bytearray()
        lock = threading.Lock()

        def _drain():
            try:
                while True:
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        break
                    with lock:
                        buf.extend(chunk)
            except OSError:
                pass

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        return {"read_fd": read_fd, "saved_fd": saved_fd, "buf": buf, "lock": lock, "thread": thread}

    def _capture_console_stderr_stop(self, state) -> str:
        """Restore the real stderr fd and return whatever was captured."""
        if state is None:
            return ""
        try:
            # Closes the pipe's write end (fd 2 was its only remaining
            # reference -- the original write_fd number was already closed
            # in _capture_console_stderr_start), which lets the drain
            # thread's os.read() see EOF and exit.
            os.dup2(state["saved_fd"], 2)
            os.close(state["saved_fd"])
        except OSError:
            pass
        state["thread"].join(timeout=1.0)
        try:
            os.close(state["read_fd"])
        except OSError:
            pass
        with state["lock"]:
            return bytes(state["buf"]).decode("utf-8", errors="replace").strip()

    def run_code(self, code: str) -> dict:
        """Core Python execution: runs code on the GUI thread, captures stdout.

        Returns a result dict suitable for _run_on_gui_thread / _run_on_gui_thread_async.

        Uses a persistent namespace so variables survive across calls.

        Output priority:
          - stdout lines (from print() calls) are always included when present
          - the last-expression value (or `result` variable) is appended when present
          - if neither, returns "Code executed successfully"
        """
        # Ensure base modules are always available (even if user overwrites them)
        self._python_namespace["FreeCAD"] = FreeCAD
        self._python_namespace["FreeCADGui"] = FreeCADGui
        self._python_namespace["App"] = FreeCAD
        self._python_namespace["Gui"] = FreeCADGui
        try:
            import Part
            self._python_namespace["Part"] = Part
        except ImportError:
            pass
        try:
            from FreeCAD import Vector
            self._python_namespace["Vector"] = Vector
        except ImportError:
            pass
        namespace = self._python_namespace

        # Auto-save active document before executing user code.
        # If the code triggers a crash (e.g., .check() on huge compounds,
        # boolean ops on 1000+ solids), the saved file survives.
        try:
            doc = FreeCAD.ActiveDocument
            if doc and getattr(doc, 'FileName', ''):
                doc.save()
        except Exception:
            pass  # non-fatal; proceed with execution

        result_value = None
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        console_stderr_state = self._capture_console_stderr_start()
        try:
            try:
                tree = ast.parse(code)
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    # Execute all statements except the last
                    if len(tree.body) > 1:
                        exec_module = ast.Module(body=tree.body[:-1], type_ignores=[])
                        ast.fix_missing_locations(exec_module)
                        exec(compile(exec_module, "<string>", "exec"), namespace)
                    # Evaluate the last expression
                    expr_ast = ast.Expression(body=tree.body[-1].value)
                    ast.fix_missing_locations(expr_ast)
                    result_value = eval(compile(expr_ast, "<string>", "eval"), namespace)
                else:
                    exec(code, namespace)
                    if "result" in namespace:
                        result_value = namespace["result"]
            except SyntaxError as e:
                return {"error": f"SyntaxError: {e}", "error_id": self.server.diagnostics_ops.store_traceback(tb_module.format_exc())}
        except Exception as e:
            return {"error": f"Python execution error: {e}", "error_id": self.server.diagnostics_ops.store_traceback(tb_module.format_exc())}
        finally:
            sys.stdout = old_stdout
            console_stderr = self._capture_console_stderr_stop(console_stderr_state)

        stdout_output = captured.getvalue().rstrip("\n")
        parts = []
        if stdout_output:
            parts.append(stdout_output)
        if console_stderr:
            parts.append(f"[FreeCAD Console]\n{console_stderr}")
        if result_value is not None:
            parts.append(repr(result_value))

        if parts:
            return {"success": True, "result": "\n".join(parts)}
        return {"success": True, "result": "Code executed successfully"}

    def execute(self, args: Dict[str, Any]) -> str:
        """Execute Python code in FreeCAD context with expression value capture (GUI-safe).

        Handles both statements and expressions, returning the value
        of the last expression if present (similar to IPython/Jupyter behavior).

        Examples:
            "1 + 1"                    -> "2"
            "x = 5"                    -> "Code executed successfully"
            "x = 5\\nx * 2"            -> "10"
            "FreeCAD.ActiveDocument"   -> "<Document object>"
            "result = 42"              -> "42" (explicit result variable)
        """
        code = args.get("code", "")
        if not code:
            return json.dumps({"error": "No code provided"})

        timeout = args.get("timeout", 30.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30.0

        return self.run_on_gui_thread(lambda: self.run_code(code), timeout=timeout)

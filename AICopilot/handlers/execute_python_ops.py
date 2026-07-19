# execute_python sandbox — the documented escape hatch for anything not
# covered by a dedicated tool. Owns the persistent namespace variables
# survive across calls in (unlike macro_ops.py's per-call namespace).

import ast
import io
import json
import sys
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

        stdout_output = captured.getvalue().rstrip("\n")
        parts = []
        if stdout_output:
            parts.append(stdout_output)
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

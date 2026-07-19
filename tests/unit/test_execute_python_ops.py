"""Unit tests for ExecutePythonOpsHandler.

Moved from tests/unit/test_freecad_mcp_handler.py's TestExecutePython class
when _run_python_code/_execute_python were extracted out of
freecad_mcp_handler.py's FreeCADSocketServer into their own handler
(AICopilot/handlers/execute_python_ops.py). Rewritten to use the standard
make_handler() pattern instead of constructing the real socket server.

execute_python is the most-used tool (the documented escape hatch) with a
persistent namespace — variables set in one call survive to the next. That
persistence is the whole point of the extraction living on the handler
instance rather than being recomputed fresh each call, so it gets its own
dedicated test alongside the behavior tests ported from the original file.
"""

import json
import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    reset_mocks,
    make_handler,
)

from handlers.execute_python_ops import ExecutePythonOpsHandler


def _run_on_gui_thread_headless(fn, timeout=30.0):
    """Mirror freecad_mcp_handler.py's real _run_on_gui_thread headless
    branch: run_code() returns a plain dict, and the real server always
    JSON-encodes it before returning to the caller. make_handler()'s
    generic server stub just returns fn()'s raw result, which is correct
    for handlers whose methods already return JSON strings directly, but
    wrong here since execute()'s contract is a JSON string and it relies
    entirely on this wrapping to produce one."""
    try:
        result = fn()
        if isinstance(result, dict):
            if "error" in result:
                return json.dumps({"error": result["error"]})
            if "result" in result:
                return json.dumps({"result": result["result"]})
        return json.dumps({"result": str(result)})
    except Exception as e:
        return json.dumps({"error": f"Headless task error: {e}"})


class TestExecutePython(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        server = MagicMock()
        server.selector = MagicMock()
        server._run_on_gui_thread = MagicMock(side_effect=_run_on_gui_thread_headless)
        self.handler = make_handler(ExecutePythonOpsHandler, server=server)

    def _run_python(self, code):
        response = self.handler.execute({"code": code})
        return json.loads(response)

    def test_expression_evaluation(self):
        result = self._run_python("1 + 1")
        self.assertEqual(result["result"], "2")

    def test_string_expression(self):
        result = self._run_python("'hello' + ' world'")
        self.assertEqual(result["result"], "'hello world'")

    def test_statement_then_expression(self):
        result = self._run_python("x = 5\nx * 2")
        self.assertEqual(result["result"], "10")

    def test_pure_statement(self):
        result = self._run_python("x = 42")
        self.assertEqual(result["result"], "Code executed successfully")

    def test_result_variable(self):
        """If code sets 'result' variable, that should be returned."""
        result = self._run_python("result = 42")
        self.assertEqual(result["result"], "42")

    def test_syntax_error(self):
        result = self._run_python("def (broken")
        self.assertIn("error", result)
        self.assertIn("SyntaxError", result["error"])

    def test_runtime_error(self):
        result = self._run_python("1 / 0")
        self.assertIn("error", result)
        self.assertTrue(
            "ZeroDivisionError" in result["error"] or "execution error" in result["error"]
        )

    def test_empty_code(self):
        result = self.handler.execute({"code": ""})
        parsed = json.loads(result)
        self.assertIn("No code provided", parsed["error"])

    def test_no_code_key(self):
        result = self.handler.execute({})
        parsed = json.loads(result)
        self.assertIn("No code provided", parsed["error"])

    def test_multiline_code(self):
        result = self._run_python("a = 10\nb = 20\na + b")
        self.assertEqual(result["result"], "30")

    def test_list_comprehension(self):
        result = self._run_python("[i**2 for i in range(5)]")
        self.assertEqual(result["result"], "[0, 1, 4, 9, 16]")

    def test_namespace_has_freecad(self):
        """FreeCAD should be available in the execution namespace."""
        result = self._run_python("type(FreeCAD).__name__")
        self.assertNotIn("error", result)

    def test_namespace_persists_variables_across_calls(self):
        """Variables set in one call survive to the next — the documented
        point of the persistent namespace living on the handler instance
        rather than being recreated fresh each call."""
        first = self._run_python("persisted_var = 99")
        self.assertEqual(first["result"], "Code executed successfully")
        second = self._run_python("persisted_var * 2")
        self.assertEqual(second["result"], "198")


if __name__ == "__main__":
    unittest.main()

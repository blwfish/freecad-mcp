"""Unit tests for DiagnosticsOpsHandler.

Moved from tests/unit/test_freecad_mcp_handler.py's TestGetDebugLogs and
TestTracebackRingBuffer classes when restart_freecad/get_debug_logs/
get_last_traceback/store_traceback were extracted out of
freecad_mcp_handler.py's FreeCADSocketServer into their own handler
(AICopilot/handlers/diagnostics_ops.py). Rewritten to use the standard
make_handler() pattern instead of constructing the real socket server.

restart_freecad previously had only a routing test (no dedicated behavior
test) — added coverage for both the headless short-circuit and the
GUI-available subprocess-spawn path while the code was being touched.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    assert_error_contains,
)

from handlers.diagnostics_ops import DiagnosticsOpsHandler


class TestGetDebugLogs(unittest.TestCase):
    """M1: get_debug_logs shares the same count<=0 slice hazard as
    get_last_traceback. glob.glob and os.path.exists are patched (rather
    than writing into the real hardcoded /tmp/freecad_mcp_debug) so the
    method reads a real tmp_path file without touching the real path."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DiagnosticsOpsHandler)

    def _write_log(self, tmp_path):
        log_file = tmp_path / "ops.jsonl"
        log_file.write_text(
            '{"operation": "a"}\n{"operation": "b"}\n{"operation": "c"}\n'
        )
        return log_file

    def test_count_zero_returns_no_entries(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(pathlib.Path(tmp))
            with patch("os.path.exists", return_value=True), \
                 patch("glob.glob", return_value=[str(log_file)]):
                out = json.loads(self.handler.get_debug_logs({"count": 0}))
            self.assertEqual(out["entries"], [])

    def test_negative_count_returns_no_entries_not_reversed(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(pathlib.Path(tmp))
            with patch("os.path.exists", return_value=True), \
                 patch("glob.glob", return_value=[str(log_file)]):
                out = json.loads(self.handler.get_debug_logs({"count": -5}))
            self.assertEqual(out["entries"], [])

    def test_positive_count_returns_tail_entries(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(pathlib.Path(tmp))
            with patch("os.path.exists", return_value=True), \
                 patch("glob.glob", return_value=[str(log_file)]):
                out = json.loads(self.handler.get_debug_logs({"count": 2}))
            self.assertEqual([e["operation"] for e in out["entries"]], ["b", "c"])

    def test_non_numeric_count_falls_back_to_default(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(pathlib.Path(tmp))
            with patch("os.path.exists", return_value=True), \
                 patch("glob.glob", return_value=[str(log_file)]):
                out = json.loads(self.handler.get_debug_logs({"count": "not-a-number"}))
            # default is 20, larger than the 3 available lines -> all 3
            self.assertEqual(len(out["entries"]), 3)


class TestTracebackRingBuffer(unittest.TestCase):
    """Round-trip + boundary coverage for store_traceback / get_last_traceback
    (the error_id mechanism that get_last_traceback depends on)."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DiagnosticsOpsHandler)

    def test_store_then_retrieve_by_id(self):
        eid = self.handler.store_traceback("Traceback: boom")
        self.assertEqual(eid, "err-0001")
        out = json.loads(self.handler.get_last_traceback({"error_id": eid}))
        self.assertEqual(out["error_id"], eid)
        self.assertEqual(out["traceback"], "Traceback: boom")

    def test_unknown_error_id_returns_error(self):
        self.handler.store_traceback("x")
        out = json.loads(self.handler.get_last_traceback({"error_id": "err-9999"}))
        self.assertIn("No traceback found", out["error"])

    def test_ring_buffer_evicts_past_20(self):
        for i in range(25):
            self.handler.store_traceback(f"tb{i}")
        # Oldest (err-0001) evicted; newest (err-0025) retained; size capped at 20.
        self.assertIn("error", json.loads(self.handler.get_last_traceback({"error_id": "err-0001"})))
        newest = json.loads(self.handler.get_last_traceback({"error_id": "err-0025"}))
        self.assertEqual(newest["traceback"], "tb24")
        self.assertEqual(json.loads(self.handler.get_last_traceback({}))["total_stored"], 20)

    def test_count_zero_returns_none_not_all(self):
        """M1: count=0 used to hit the `[-0:]` == `[0:]` slice and return
        ALL stored entries instead of zero — fixed to treat count<=0 as
        'return nothing' explicitly rather than relying on slice sign."""
        for i in range(3):
            self.handler.store_traceback(f"tb{i}")
        out = json.loads(self.handler.get_last_traceback({"count": 0}))
        self.assertEqual(out["tracebacks"], [])

    def test_negative_count_returns_none_not_reversed(self):
        """A negative count must not silently read from the FRONT of the
        buffer instead of the tail."""
        for i in range(5):
            self.handler.store_traceback(f"tb{i}")
        out = json.loads(self.handler.get_last_traceback({"count": -2}))
        self.assertEqual(out["tracebacks"], [])

    def test_count_one_returns_single_most_recent(self):
        for i in range(3):
            self.handler.store_traceback(f"tb{i}")
        out = json.loads(self.handler.get_last_traceback({"count": 1}))
        self.assertEqual(len(out["tracebacks"]), 1)
        self.assertEqual(out["tracebacks"][0]["traceback"], "tb2")

    def test_non_numeric_count_falls_back_to_default(self):
        for i in range(3):
            self.handler.store_traceback(f"tb{i}")
        out = json.loads(self.handler.get_last_traceback({"count": "not-a-number"}))
        self.assertEqual(len(out["tracebacks"]), 1)


class TestRestartFreecad(unittest.TestCase):
    """restart_freecad previously had only a routing test (server.
    _restart_freecad mocked out entirely) — no test exercised the actual
    headless short-circuit or the subprocess-spawn/document-save path."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DiagnosticsOpsHandler)

    def test_headless_mode_returns_error_not_available(self):
        mock_FreeCAD.GuiUp = False
        result = self.handler.restart_freecad({})
        assert_error_contains(self, result, "headless")

    def test_gui_mode_saves_documents_and_spawns_subprocess(self):
        # Patch restart_freecad's OWN __globals__ dict directly rather than
        # whatever `sys.modules['handlers.diagnostics_ops']` currently
        # holds. If a reload test elsewhere in the session
        # (TestReloadHandlersPreservesState) already re-exec'd
        # handlers/diagnostics_ops.py via spec_from_file_location, that
        # produces a brand-new module/class/globals-dict; self.handler
        # here was built from whichever class this test FILE's own
        # collection-time `from handlers.diagnostics_ops import
        # DiagnosticsOpsHandler` bound, which may be a DIFFERENT (now
        # orphaned) globals dict than the current sys.modules entry —
        # patching the sys.modules one would silently no-op.
        func_globals = self.handler.restart_freecad.__func__.__globals__

        doc = MagicMock()
        doc.FileName = "/tmp/fake.FCStd"
        mock_FreeCAD.GuiUp = True
        mock_FreeCAD.listDocuments = MagicMock(return_value={"fake": doc})
        mock_FreeCAD.getHomePath = MagicMock(return_value="/opt/freecad/")
        mock_qtcore = MagicMock()
        mock_qtcore.QTimer.singleShot = MagicMock(
            side_effect=lambda delay, fn: fn()
        )

        with patch.object(func_globals["os"].path, "exists", return_value=True), \
             patch.dict(func_globals, {"QtCore": mock_qtcore}), \
             patch("subprocess.Popen") as mock_popen:
            result = json.loads(self.handler.restart_freecad({}))

        doc.save.assert_called_once()
        mock_popen.assert_called_once()
        spawned_cmd = mock_popen.call_args.args[0]
        self.assertEqual(spawned_cmd[0], "/opt/freecad/bin/FreeCAD")
        self.assertIn("/tmp/fake.FCStd", spawned_cmd)
        self.assertEqual(result["saved_documents"], ["/tmp/fake.FCStd"])

    def test_gui_mode_save_documents_false_skips_save(self):
        func_globals = self.handler.restart_freecad.__func__.__globals__

        doc = MagicMock()
        doc.FileName = "/tmp/fake.FCStd"
        mock_FreeCAD.GuiUp = True
        mock_FreeCAD.listDocuments = MagicMock(return_value={"fake": doc})
        mock_FreeCAD.getHomePath = MagicMock(return_value="/opt/freecad/")
        mock_qtcore = MagicMock()
        mock_qtcore.QTimer.singleShot = MagicMock(
            side_effect=lambda delay, fn: fn()
        )

        with patch.object(func_globals["os"].path, "exists", return_value=True), \
             patch.dict(func_globals, {"QtCore": mock_qtcore}), \
             patch("subprocess.Popen"):
            self.handler.restart_freecad({"save_documents": False})

        doc.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

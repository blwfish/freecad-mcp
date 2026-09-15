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
    """restart_freecad runs its whole body on the Qt GUI thread as an async job
    submitted through the server's one chokepoint (_submit_async_job).

    The defect this pins (blwfish/freecad-mcp#75): QTimer.singleShot called
    from the socket thread never fires, so the tool reported success and
    nothing restarted; it also saved every document on the socket thread."""

    def setUp(self):
        reset_mocks()
        self.submitted = []          # (tool, task) pairs the server chokepoint received
        server = MagicMock()
        server.selector = MagicMock()

        def _submit(tool, task):
            self.submitted.append((tool, task))
            return json.dumps({"job_id": "abcd1234", "status": "submitted"})

        server._submit_async_job = MagicMock(side_effect=_submit)
        # A bare MagicMock() return value is truthy (not None): without this,
        # `if self.server._gui_unresponsive_error() is not None:` always took
        # the unresponsive-GUI fallback branch (real subprocess.Popen + a
        # real threading.Timer -> os._exit(0) half a second later, which
        # silently killed the whole pytest process). Default to the healthy
        # case, matching a real fresh FreeCAD instance.
        server._gui_unresponsive_error = MagicMock(return_value=None)
        self.handler = make_handler(DiagnosticsOpsHandler, server=server)
        # Patch restart_freecad's OWN __globals__ dict rather than whatever
        # sys.modules['handlers.diagnostics_ops'] currently holds: a reload test
        # elsewhere in the session may have re-exec'd the module, orphaning the
        # globals this handler's class was bound to.
        self.func_globals = self.handler.restart_freecad.__func__.__globals__
        self.qtcore = MagicMock()
        self.gui = MagicMock()

    def _gui(self, tmp):
        """A GUI-mode FreeCAD whose home has a real bin/FreeCAD(.exe) and one
        file-backed document."""
        import os, pathlib
        bin_dir = pathlib.Path(tmp) / "bin"
        bin_dir.mkdir()
        exe = bin_dir / ("FreeCAD.exe" if os.name == "nt" else "FreeCAD")
        exe.write_text("")
        exe.chmod(0o755)
        doc = MagicMock()
        doc.FileName = str(pathlib.Path(tmp) / "part.FCStd")
        mock_FreeCAD.GuiUp = True
        mock_FreeCAD.listDocuments = MagicMock(return_value={"part": doc})
        mock_FreeCAD.getHomePath = MagicMock(return_value=str(tmp) + os.sep)
        return doc, str(exe)

    def _patched(self):
        return patch.dict(self.func_globals, {"QtCore": self.qtcore, "FreeCADGui": self.gui})

    @staticmethod
    def _norm(paths):
        import os
        return [os.path.normcase(p) for p in paths]

    def test_headless_mode_returns_error_not_available(self):
        mock_FreeCAD.GuiUp = False
        result = self.handler.restart_freecad({})
        assert_error_contains(self, result, "headless")
        self.assertEqual(self.submitted, [])

    def test_missing_binary_is_a_named_failure_and_nothing_is_submitted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, _exe = self._gui(tmp)
            with self._patched(), patch("shutil.which", return_value=None):
                result = json.loads(self.handler.restart_freecad({}))
        self.assertEqual(result["error"], "Cannot find FreeCAD binary for restart")
        self.assertEqual(self.submitted, [])
        doc.save.assert_not_called()

    def test_submits_the_restart_as_a_gui_job_and_fires_no_timer_from_the_socket_thread(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, _exe = self._gui(tmp)
            with self._patched():
                result = json.loads(self.handler.restart_freecad({}))
        self.assertEqual(result["status"], "submitted")
        self.assertEqual([t for t, _ in self.submitted], ["restart_freecad"])
        self.qtcore.QTimer.singleShot.assert_not_called()
        doc.save.assert_not_called()          # saving is GUI-thread work: it lives in the job

    def test_the_gui_job_saves_spawns_and_schedules_the_close(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, exe = self._gui(tmp)
            with self._patched(), patch("subprocess.Popen") as popen:
                self.handler.restart_freecad({})
                _tool, task = self.submitted[0]
                result = task()
                # The scheduled close runs while the patched QtCore/FreeCADGui are still in place.
                self.qtcore.QTimer.singleShot.assert_called_once()
                delay, close = self.qtcore.QTimer.singleShot.call_args.args
                self.assertEqual(delay, 500)
                close()
                self.gui.getMainWindow.return_value.close.assert_called_once()
            doc.save.assert_called_once()
            popen.assert_called_once()
            self.assertEqual(self._norm(popen.call_args.args[0]), self._norm([exe, doc.FileName]))
            self.assertTrue(result["success"])
            self.assertIn("1 document", result["result"])

    def test_save_documents_false_skips_the_save_but_reopens(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, exe = self._gui(tmp)
            with self._patched(), patch("subprocess.Popen") as popen:
                self.handler.restart_freecad({"save_documents": False})
                self.submitted[0][1]()
            doc.save.assert_not_called()
            self.assertEqual(self._norm(popen.call_args.args[0]), self._norm([exe, doc.FileName]))

    def test_reopen_documents_false_spawns_bare(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _doc, exe = self._gui(tmp)
            with self._patched(), patch("subprocess.Popen") as popen:
                self.handler.restart_freecad({"reopen_documents": False})
                self.submitted[0][1]()
            self.assertEqual(self._norm(popen.call_args.args[0]), self._norm([exe]))

    def test_a_save_failure_propagates_from_the_job_and_spawns_nothing(self):
        """The server's job runner records a raising task as status "error"
        with its traceback; the job must raise, not swallow, and must not
        spawn or schedule the close once a save has failed."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, _exe = self._gui(tmp)
            doc.save.side_effect = RuntimeError("disk full")
            with self._patched(), patch("subprocess.Popen") as popen:
                self.handler.restart_freecad({})
                with self.assertRaises(RuntimeError):
                    self.submitted[0][1]()
            popen.assert_not_called()
            self.qtcore.QTimer.singleShot.assert_not_called()


class TestRestartFreecadUnresponsiveGuiFallback(unittest.TestCase):
    """restart_freecad's fallback for a genuinely hung Qt GUI thread.

    Submitting through _submit_async_job (TestRestartFreecad above) depends
    on the GUI thread being alive to drain the queue -- but restart_freecad
    is documented elsewhere (poll_job/cancel_job) as THE recovery tool for
    exactly the case where it isn't. This exercises the branch that runs
    entirely on the calling thread instead: real subprocess.Popen and a real
    threading.Timer -> os._exit(0) half a second later, so EVERY test here
    must patch both before calling restart_freecad, or it kills the pytest
    process outright (confirmed the hard way while writing these)."""

    def setUp(self):
        reset_mocks()
        server = MagicMock()
        server.selector = MagicMock()
        server._gui_unresponsive_error = MagicMock(
            return_value="GUI thread appears unresponsive"
        )
        server._submit_async_job = MagicMock(
            side_effect=AssertionError("should not submit a job on the unresponsive-GUI path")
        )
        self.server = server
        self.handler = make_handler(DiagnosticsOpsHandler, server=server)
        self.func_globals = self.handler.restart_freecad.__func__.__globals__

    def _gui(self, tmp):
        import os, pathlib
        bin_dir = pathlib.Path(tmp) / "bin"
        bin_dir.mkdir()
        exe = bin_dir / ("FreeCAD.exe" if os.name == "nt" else "FreeCAD")
        exe.write_text("")
        exe.chmod(0o755)
        doc = MagicMock()
        doc.FileName = str(pathlib.Path(tmp) / "part.FCStd")
        mock_FreeCAD.GuiUp = True
        mock_FreeCAD.listDocuments = MagicMock(return_value={"part": doc})
        mock_FreeCAD.getHomePath = MagicMock(return_value=str(tmp) + os.sep)
        return doc, str(exe)

    @staticmethod
    def _norm(paths):
        import os
        return [os.path.normcase(p) for p in paths]

    def _call(self, args=None):
        """restart_freecad() with subprocess.Popen and the exit timer both
        patched -- never call the method under test outside this helper."""
        with patch("subprocess.Popen") as popen, patch("threading.Timer") as timer_cls:
            result = json.loads(self.handler.restart_freecad(args or {}))
        return result, popen, timer_cls

    def test_skips_the_job_queue_entirely(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._gui(tmp)
            result, popen, _timer = self._call()
        self.server._submit_async_job.assert_not_called()
        popen.assert_called_once()
        self.assertTrue(result["gui_was_unresponsive"])

    def test_does_not_save_documents(self):
        """Saving from off the GUI thread while it may be mid-mutation of
        that same document is the exact risk this path exists to avoid."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, _exe = self._gui(tmp)
            result, _popen, _timer = self._call()
        doc.save.assert_not_called()
        self.assertEqual(result["saved_documents"], [])

    def test_reopens_documents_by_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc, exe = self._gui(tmp)
            result, popen, _timer = self._call()
        self.assertEqual(self._norm(popen.call_args.args[0]), self._norm([exe, doc.FileName]))
        self.assertEqual(result["reopened_documents"], [doc.FileName])

    def test_reopen_documents_false_spawns_bare(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _doc, exe = self._gui(tmp)
            result, popen, _timer = self._call({"reopen_documents": False})
        self.assertEqual(self._norm(popen.call_args.args[0]), self._norm([exe]))
        self.assertEqual(result["reopened_documents"], [])

    def test_exit_is_scheduled_via_a_plain_timer_and_really_calls_os_exit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._gui(tmp)
            _result, _popen, timer_cls = self._call()
        timer_cls.assert_called_once()
        delay, callback = timer_cls.call_args.args
        self.assertEqual(delay, 0.5)
        with patch("os._exit") as exit_mock:
            callback()
        exit_mock.assert_called_once_with(0)

    def test_spawn_failure_is_a_real_error_and_does_not_schedule_exit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._gui(tmp)
            with patch("subprocess.Popen", side_effect=OSError("no such file")), \
                 patch("threading.Timer") as timer_cls:
                result = json.loads(self.handler.restart_freecad({}))
        assert_error_contains(self, result, "failed to spawn")
        timer_cls.assert_not_called()

    def test_document_path_read_failure_still_restarts(self):
        """A read failure while enumerating documents must not block the
        restart -- this path exists because the process is already in
        trouble; best-effort reopening beats refusing to recover at all."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._gui(tmp)
            mock_FreeCAD.listDocuments = MagicMock(side_effect=RuntimeError("GIL contention"))
            result, popen, timer_cls = self._call()
        popen.assert_called_once()
        timer_cls.assert_called_once()
        self.assertEqual(result["reopened_documents"], [])


if __name__ == "__main__":
    unittest.main()

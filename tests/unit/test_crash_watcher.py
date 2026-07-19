"""Unit tests for AICopilot/crash_watcher.py.

Previously zero test coverage. Focused on M10: a persistent write failure
(disk full, permissions) in set_current_op's atomic write was silently
swallowed by a bare `except Exception: pass` with no counter, no log —
the whole crash-diagnosis mechanism this module exists for could stop
working with zero observable signal.

crash_watcher.py has no module-level FreeCAD import (it's designed to be
lightweight and never crash), so most of this file runs without any
FreeCAD mocking. Only the console-warning path needs FreeCAD mocked,
since it does a local `import FreeCAD` inside the except block.
"""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

import crash_watcher


@pytest.fixture(autouse=True)
def reset_module_state():
    """crash_watcher's failure counter/flag are module-level globals — must
    not leak between tests. Also removes any file a test writes."""
    crash_watcher._write_failures = 0
    crash_watcher._last_write_failed = False
    yield
    crash_watcher._write_failures = 0
    crash_watcher._last_write_failed = False
    for path in (crash_watcher.LAST_OP_FILE, crash_watcher.LAST_OP_FILE + ".tmp"):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


class TestSetCurrentOpSuccess:
    def test_writes_readable_file(self):
        crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        with open(crash_watcher.LAST_OP_FILE) as f:
            data = json.load(f)
        assert data["tool"] == "execute_python"
        assert data["args"]["code"] == "1+1"
        assert data["pid"] == os.getpid()

    def test_success_does_not_increment_failure_counter(self):
        crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        assert crash_watcher.get_write_failure_count() == 0

    def test_large_arg_is_truncated(self):
        huge = "x" * 5000
        crash_watcher.set_current_op("execute_python", {"code": huge})
        with open(crash_watcher.LAST_OP_FILE) as f:
            data = json.load(f)
        assert len(data["args"]["code"].encode("utf-8")) <= crash_watcher._MAX_ARG_BYTES + 20
        assert "[truncated]" in data["args"]["code"]


class TestSetCurrentOpWriteFailure:
    """M10: write failures were completely invisible before this fix."""

    def test_write_failure_increments_counter(self):
        with patch("builtins.open", side_effect=OSError("disk full")):
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        assert crash_watcher.get_write_failure_count() == 1

    def test_write_failure_does_not_raise(self):
        """The module's own contract: never crash the caller."""
        with patch("builtins.open", side_effect=OSError("disk full")):
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})  # must not raise

    def test_repeated_failures_keep_incrementing(self):
        with patch("builtins.open", side_effect=OSError("disk full")):
            for _ in range(5):
                crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        assert crash_watcher.get_write_failure_count() == 5

    def test_console_warning_fires_once_per_failure_streak(self):
        """Rate-limited: a full disk shouldn't spam a warning on every
        single operation while it stays full."""
        fake_freecad = MagicMock()
        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch.dict(sys.modules, {"FreeCAD": fake_freecad}):
            for _ in range(5):
                crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        assert fake_freecad.Console.PrintWarning.call_count == 1

    def test_console_warning_fires_again_after_recovery(self):
        """A success in between two failure streaks resets the
        rate-limit flag, so the operator is re-notified on the next
        failure instead of staying silent forever after the first one."""
        fake_freecad = MagicMock()
        with patch.dict(sys.modules, {"FreeCAD": fake_freecad}):
            with patch("builtins.open", side_effect=OSError("disk full")):
                crash_watcher.set_current_op("a", {})
            crash_watcher.set_current_op("b", {})  # succeeds, resets the flag
            with patch("builtins.open", side_effect=OSError("disk full")):
                crash_watcher.set_current_op("c", {})
        assert fake_freecad.Console.PrintWarning.call_count == 2

    def test_console_warning_failure_does_not_crash(self):
        """The console-warning attempt is itself defensively wrapped —
        even if FreeCAD.Console.PrintWarning raises, set_current_op must
        not propagate that."""
        fake_freecad = MagicMock()
        fake_freecad.Console.PrintWarning.side_effect = RuntimeError("console unavailable")
        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch.dict(sys.modules, {"FreeCAD": fake_freecad}):
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})  # must not raise
        assert crash_watcher.get_write_failure_count() == 1

    def test_freecad_not_importable_does_not_crash(self):
        """FreeCAD may not always be importable when this runs; the local
        `import FreeCAD` inside the except block is itself guarded."""
        with patch("builtins.open", side_effect=OSError("disk full")), \
             patch.dict(sys.modules, {"FreeCAD": None}):
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})  # must not raise
        assert crash_watcher.get_write_failure_count() == 1


class TestClearCurrentOp:
    def test_removes_existing_file(self):
        crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        assert os.path.exists(crash_watcher.LAST_OP_FILE)
        crash_watcher.clear_current_op()
        assert not os.path.exists(crash_watcher.LAST_OP_FILE)

    def test_missing_file_does_not_raise(self):
        assert not os.path.exists(crash_watcher.LAST_OP_FILE)
        crash_watcher.clear_current_op()  # must not raise


class TestReadCurrentOp:
    def test_reads_back_what_was_written(self):
        crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        result = crash_watcher.read_current_op()
        assert result["tool"] == "execute_python"

    def test_missing_file_returns_none(self):
        assert not os.path.exists(crash_watcher.LAST_OP_FILE)
        assert crash_watcher.read_current_op() is None

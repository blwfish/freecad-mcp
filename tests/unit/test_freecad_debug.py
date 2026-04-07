"""Unit tests for FreeCADDebugger (freecad_debug.py).

Tests all public methods, lean/verbose modes, decorator behavior,
performance tracking, state capture, log file creation, and module-level
convenience functions.

Run with: python3 -m pytest tests/unit/test_freecad_debug.py -v
"""

import json
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py auto-mocks FreeCAD, so we can import directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

import freecad_debug


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_debugger(tmp_path, **kwargs):
    """Create a FreeCADDebugger pointing at a temp log dir."""
    defaults = dict(
        log_dir=str(tmp_path),
        level=logging.DEBUG,
        enable_console=False,   # suppress stdout in tests
        enable_file=True,
        lean_logging=False,
    )
    defaults.update(kwargs)
    return freecad_debug.FreeCADDebugger(**defaults)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        make_debugger(tmp_path, log_dir=str(log_dir))
        assert log_dir.is_dir()

    def test_default_lean_mode(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        assert d.lean_logging is True

    def test_verbose_mode(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)
        assert d.lean_logging is False

    def test_logger_handlers_cleared(self, tmp_path):
        """Creating two debuggers should not accumulate handlers."""
        d1 = make_debugger(tmp_path)
        handler_count_1 = len(d1.logger.handlers)
        d2 = make_debugger(tmp_path)
        handler_count_2 = len(d2.logger.handlers)
        # Both should have the same number (file handler only, console disabled)
        assert handler_count_1 == handler_count_2

    def test_console_handler_added(self, tmp_path):
        d = make_debugger(tmp_path, enable_console=True, enable_file=False)
        assert len(d.logger.handlers) == 1
        assert isinstance(d.logger.handlers[0], logging.StreamHandler)

    def test_file_handler_added(self, tmp_path):
        from logging.handlers import RotatingFileHandler
        d = make_debugger(tmp_path, enable_console=False, enable_file=True)
        assert len(d.logger.handlers) == 1
        assert isinstance(d.logger.handlers[0], RotatingFileHandler)

    def test_no_handlers_when_both_disabled(self, tmp_path):
        d = make_debugger(tmp_path, enable_console=False, enable_file=False)
        assert len(d.logger.handlers) == 0

    def test_propagate_disabled(self, tmp_path):
        d = make_debugger(tmp_path)
        assert d.logger.propagate is False

    def test_log_file_created_on_init(self, tmp_path):
        """The RotatingFileHandler creates the log file lazily on first write,
        but init logs a message, so the file should exist."""
        make_debugger(tmp_path)
        assert (tmp_path / "freecad_mcp.log").exists()

    def test_custom_max_log_size(self, tmp_path):
        d = make_debugger(tmp_path, max_log_size=1024)
        assert d.max_log_size == 1024

    def test_custom_backup_count(self, tmp_path):
        d = make_debugger(tmp_path, backup_count=3)
        assert d.backup_count == 3


# ---------------------------------------------------------------------------
# log_operation
# ---------------------------------------------------------------------------

class TestLogOperation:
    def test_error_always_logged(self, tmp_path):
        """Errors are logged in both lean and verbose mode."""
        d = make_debugger(tmp_path, lean_logging=True)
        exc = ValueError("something broke")
        d.log_operation("failing_op", error=exc)

        # Check JSON log file was written
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["success"] is False
        assert entry["error"]["type"] == "ValueError"
        assert entry["error"]["message"] == "something broke"

    def test_error_includes_operation_name(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        d.log_operation("my_op", error=RuntimeError("oops"))
        json_files = list(tmp_path.glob("operations_*.json"))
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["operation"] == "my_op"

    def test_lean_mode_skips_start_operations(self, tmp_path):
        """In lean mode, operations containing START are skipped (no JSON file)."""
        d = make_debugger(tmp_path, lean_logging=True)
        d.log_operation("my_op START", parameters={"a": 1})
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 0

    def test_lean_mode_skips_queue_operations(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        d.log_operation("my_op QUEUE", parameters={"a": 1})
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 0

    def test_lean_mode_logs_done_operations(self, tmp_path):
        """In lean mode, operations without START/QUEUE are logged (info level)."""
        d = make_debugger(tmp_path, lean_logging=True)
        # This should go through the lean branch (no JSON, just logger.info)
        d.log_operation("my_op DONE", result="ok")
        # No JSON file in lean mode for non-error
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 0

    def test_verbose_mode_writes_json(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)
        d.log_operation("my_op", parameters={"x": 42}, result="done", duration=0.5)

        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["operation"] == "my_op"
        assert entry["parameters"] == {"x": 42}
        assert entry["success"] is True
        assert entry["duration_seconds"] == 0.5
        assert entry["result"] == "done"

    def test_verbose_mode_without_result(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)
        d.log_operation("my_op", duration=0.1)
        json_files = list(tmp_path.glob("operations_*.json"))
        entry = json.loads(json_files[0].read_text().strip())
        assert "result" not in entry

    def test_verbose_multiple_entries_appended(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)
        d.log_operation("op1", result="a")
        d.log_operation("op2", result="b")
        json_files = list(tmp_path.glob("operations_*.json"))
        lines = json_files[0].read_text().strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_serialize_params_none(self, tmp_path):
        d = make_debugger(tmp_path)
        assert d._serialize_params(None) is None

    def test_serialize_params_json_safe(self, tmp_path):
        d = make_debugger(tmp_path)
        result = d._serialize_params({"a": 1, "b": "hello"})
        assert result == {"a": 1, "b": "hello"}

    def test_serialize_params_non_json_safe(self, tmp_path):
        d = make_debugger(tmp_path)
        obj = object()
        result = d._serialize_params({"thing": obj})
        assert result["thing"] == str(obj)

    def test_serialize_result_none(self, tmp_path):
        d = make_debugger(tmp_path)
        assert d._serialize_result(None) is None

    def test_serialize_result_json_safe(self, tmp_path):
        d = make_debugger(tmp_path)
        assert d._serialize_result([1, 2, 3]) == [1, 2, 3]

    def test_serialize_result_non_json_safe(self, tmp_path):
        d = make_debugger(tmp_path)
        obj = object()
        assert d._serialize_result(obj) == str(obj)


# ---------------------------------------------------------------------------
# capture_freecad_state
# ---------------------------------------------------------------------------

class TestCaptureState:
    def test_no_active_document(self, tmp_path, mock_freecad):
        mock_freecad.ActiveDocument = None
        d = make_debugger(tmp_path)
        state = d.capture_freecad_state()
        assert state["has_active_document"] is False
        assert "document_name" not in state

    def test_with_active_document(self, tmp_path, mock_freecad):
        doc = MagicMock()
        doc.Name = "TestDoc"
        doc.Label = "Test Label"
        obj1 = MagicMock()
        obj1.Name = "Box"
        obj1.TypeId = "Part::Box"
        obj1.Label = "MyBox"
        doc.Objects = [obj1]
        mock_freecad.ActiveDocument = doc

        d = make_debugger(tmp_path)
        state = d.capture_freecad_state()

        assert state["has_active_document"] is True
        assert state["document_name"] == "TestDoc"
        assert state["document_label"] == "Test Label"
        assert state["object_count"] == 1
        assert state["objects"][0]["name"] == "Box"
        assert state["objects"][0]["type"] == "Part::Box"

    def test_stores_last_state(self, tmp_path, mock_freecad):
        mock_freecad.ActiveDocument = None
        d = make_debugger(tmp_path)
        state = d.capture_freecad_state()
        assert d.last_freecad_state is state

    def test_exception_returns_error_dict(self, tmp_path, mock_freecad):
        """When FreeCAD state capture fails, return an error dict."""
        # Make ActiveDocument a mock whose attribute access raises
        bad_doc = MagicMock()
        bad_doc.Name = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        type(bad_doc).Name = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        mock_freecad.ActiveDocument = bad_doc
        d = make_debugger(tmp_path)
        state = d.capture_freecad_state()
        assert "error" in state
        # Restore
        mock_freecad.ActiveDocument = None


# ---------------------------------------------------------------------------
# log_state_change / compare_states
# ---------------------------------------------------------------------------

class TestStateChange:
    def test_lean_mode_returns_none(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        assert d.log_state_change("some_op") is None

    def test_verbose_mode_captures_state(self, tmp_path, mock_freecad):
        mock_freecad.ActiveDocument = None
        d = make_debugger(tmp_path, lean_logging=False)
        state = d.log_state_change("some_op")
        assert state is not None
        assert "has_active_document" in state

    def test_compare_states_lean_noop(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        # Should not raise
        d.compare_states({"object_count": 1}, "op")

    def test_compare_states_none_before(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)
        # Should not raise when before_state is None
        d.compare_states(None, "op")

    def test_compare_states_detects_change(self, tmp_path, mock_freecad):
        mock_freecad.ActiveDocument = None
        d = make_debugger(tmp_path, lean_logging=False)
        before = {"object_count": 1, "has_active_document": False}
        # After state will have no object_count (no active doc)
        d.compare_states(before, "test_op")
        # Just verify it doesn't crash; the detection logs via logger.info


# ---------------------------------------------------------------------------
# track_performance
# ---------------------------------------------------------------------------

class TestPerformance:
    def test_tracks_single_operation(self, tmp_path):
        d = make_debugger(tmp_path)
        d.track_performance("op1", 0.5)
        assert "op1" in d.operation_times
        assert d.operation_times["op1"] == [0.5]

    def test_tracks_multiple_measurements(self, tmp_path):
        d = make_debugger(tmp_path)
        d.track_performance("op1", 0.1)
        d.track_performance("op1", 0.2)
        d.track_performance("op1", 0.3)
        assert len(d.operation_times["op1"]) == 3

    def test_caps_at_100_samples(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)
        for i in range(150):
            d.track_performance("op1", float(i))
        assert len(d.operation_times["op1"]) == 100
        # Should keep the last 100
        assert d.operation_times["op1"][0] == 50.0

    def test_separate_operations(self, tmp_path):
        d = make_debugger(tmp_path)
        d.track_performance("op1", 0.1)
        d.track_performance("op2", 0.2)
        assert len(d.operation_times) == 2


# ---------------------------------------------------------------------------
# get_performance_report
# ---------------------------------------------------------------------------

class TestPerformanceReport:
    def test_empty_report(self, tmp_path):
        d = make_debugger(tmp_path)
        assert d.get_performance_report() == "No performance data available"

    def test_report_contains_stats(self, tmp_path):
        d = make_debugger(tmp_path)
        d.track_performance("my_op", 0.1)
        d.track_performance("my_op", 0.3)
        report = d.get_performance_report()
        assert "my_op" in report
        assert "Samples: 2" in report
        assert "Average: 0.200s" in report
        assert "Min: 0.100s" in report
        assert "Max: 0.300s" in report

    def test_report_multiple_operations(self, tmp_path):
        d = make_debugger(tmp_path)
        d.track_performance("alpha", 1.0)
        d.track_performance("beta", 2.0)
        report = d.get_performance_report()
        assert "alpha" in report
        assert "beta" in report


# ---------------------------------------------------------------------------
# debug_decorator
# ---------------------------------------------------------------------------

class TestDebugDecorator:
    def test_wraps_successful_function(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator()
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5

    def test_preserves_function_name(self, tmp_path):
        d = make_debugger(tmp_path)

        @d.debug_decorator()
        def my_function():
            pass

        assert my_function.__name__ == "my_function"

    def test_logs_operation_on_success(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator()
        def my_op(x):
            return x * 2

        my_op(5)

        # Should have written a JSON log entry
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["operation"] == "my_op"
        assert entry["success"] is True

    def test_logs_error_on_exception(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator()
        def failing_op():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            failing_op()

        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["success"] is False
        assert entry["error"]["type"] == "RuntimeError"

    def test_exception_is_reraised(self, tmp_path):
        d = make_debugger(tmp_path)

        @d.debug_decorator()
        def bad():
            raise ValueError("test")

        with pytest.raises(ValueError, match="test"):
            bad()

    def test_lean_mode_disables_tracking(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)

        @d.debug_decorator(track_state=True, track_performance=True)
        def my_op():
            return 42

        my_op()
        # In lean mode, performance tracking is disabled
        assert len(d.operation_times) == 0

    def test_verbose_mode_with_performance_tracking(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator(track_performance=True)
        def my_op():
            return 42

        my_op()
        assert "my_op" in d.operation_times
        assert len(d.operation_times["my_op"]) == 1

    def test_performance_not_tracked_on_error(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator(track_performance=True)
        def my_op():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            my_op()

        # Performance should NOT be tracked when error occurs
        assert "my_op" not in d.operation_times

    def test_duration_is_positive(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator()
        def my_op():
            return 1

        my_op()
        json_files = list(tmp_path.glob("operations_*.json"))
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["duration_seconds"] >= 0

    def test_parameters_captured_in_verbose(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=False)

        @d.debug_decorator()
        def my_op(x, y=10):
            return x + y

        my_op(5, y=20)
        json_files = list(tmp_path.glob("operations_*.json"))
        entry = json.loads(json_files[0].read_text().strip())
        assert entry["parameters"]["x"] == 5
        assert entry["parameters"]["y"] == 20

    def test_parameters_none_in_lean(self, tmp_path):
        d = make_debugger(tmp_path, lean_logging=True)

        @d.debug_decorator()
        def my_op(x):
            return x

        my_op(5)
        # In lean mode, no JSON file for success
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 0


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    def test_get_debugger_returns_instance(self, tmp_path):
        # Reset global state
        freecad_debug._debugger = None
        with patch.object(freecad_debug, "LEAN_LOGGING", True):
            d = freecad_debug.get_debugger()
            assert isinstance(d, freecad_debug.FreeCADDebugger)

    def test_get_debugger_returns_same_instance(self, tmp_path):
        freecad_debug._debugger = None
        d1 = freecad_debug.get_debugger()
        d2 = freecad_debug.get_debugger()
        assert d1 is d2

    def test_init_debugger_creates_new(self, tmp_path):
        freecad_debug._debugger = None
        d = freecad_debug.init_debugger(
            log_dir=str(tmp_path),
            enable_console=False,
            lean_logging=True,
        )
        assert isinstance(d, freecad_debug.FreeCADDebugger)
        assert freecad_debug._debugger is d

    def test_init_debugger_replaces_existing(self, tmp_path):
        freecad_debug._debugger = None
        d1 = freecad_debug.init_debugger(log_dir=str(tmp_path), enable_console=False)
        d2 = freecad_debug.init_debugger(log_dir=str(tmp_path), enable_console=False)
        assert d1 is not d2
        assert freecad_debug._debugger is d2

    def test_init_debugger_defaults_lean_from_module(self, tmp_path):
        freecad_debug._debugger = None
        with patch.object(freecad_debug, "LEAN_LOGGING", False):
            d = freecad_debug.init_debugger(
                log_dir=str(tmp_path), enable_console=False
            )
            assert d.lean_logging is False

    def test_log_operation_convenience(self, tmp_path):
        freecad_debug._debugger = None
        d = freecad_debug.init_debugger(
            log_dir=str(tmp_path), enable_console=False, lean_logging=False
        )
        freecad_debug.log_operation("test_op", result="ok")
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1

    def test_debug_decorator_convenience(self, tmp_path):
        freecad_debug._debugger = None
        freecad_debug.init_debugger(
            log_dir=str(tmp_path), enable_console=False, lean_logging=False
        )

        @freecad_debug.debug_decorator()
        def helper():
            return 99

        assert helper() == 99

    def test_capture_state_convenience(self, tmp_path, mock_freecad):
        mock_freecad.ActiveDocument = None
        freecad_debug._debugger = None
        freecad_debug.init_debugger(
            log_dir=str(tmp_path), enable_console=False
        )
        state = freecad_debug.capture_state()
        assert "has_active_document" in state

    def test_performance_report_convenience(self, tmp_path):
        freecad_debug._debugger = None
        freecad_debug.init_debugger(
            log_dir=str(tmp_path), enable_console=False
        )
        report = freecad_debug.performance_report()
        assert report == "No performance data available"


# ---------------------------------------------------------------------------
# Log file rotation
# ---------------------------------------------------------------------------

class TestLogRotation:
    def test_rotation_parameters_passed(self, tmp_path):
        from logging.handlers import RotatingFileHandler
        d = make_debugger(tmp_path, max_log_size=2048, backup_count=2)
        file_handlers = [
            h for h in d.logger.handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 2048
        assert file_handlers[0].backupCount == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_log_operation_json_write_failure(self, tmp_path):
        """If JSON log write fails, it should not raise."""
        d = make_debugger(tmp_path, lean_logging=False)
        # Make the log dir read-only to trigger write failure
        read_only_dir = tmp_path / "readonly"
        read_only_dir.mkdir()
        d.log_dir = read_only_dir
        read_only_dir.chmod(0o444)
        try:
            # Should not raise even though file write fails
            d.log_operation("test_op", result="ok")
        finally:
            read_only_dir.chmod(0o755)

    def test_error_log_json_write_failure(self, tmp_path):
        """If JSON log write fails during error logging, should not raise."""
        d = make_debugger(tmp_path, lean_logging=True)
        read_only_dir = tmp_path / "readonly2"
        read_only_dir.mkdir()
        d.log_dir = read_only_dir
        read_only_dir.chmod(0o444)
        try:
            d.log_operation("fail_op", error=RuntimeError("boom"))
        finally:
            read_only_dir.chmod(0o755)

    def test_version_attribute(self):
        assert freecad_debug.__version__ == "1.1.0"

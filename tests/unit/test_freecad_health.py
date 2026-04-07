"""Unit tests for FreeCADHealthMonitor (freecad_health.py).

Tests all public methods, lean/verbose modes, crash logging, socket checks,
process detection, restart logic, crash statistics, and global convenience
functions.

Run with: python3 -m pytest tests/unit/test_freecad_health.py -v
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# conftest.py auto-mocks FreeCAD, so we can import directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

import freecad_health


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_monitor(tmp_path, **kwargs):
    """Create a FreeCADHealthMonitor with temp dirs."""
    defaults = dict(
        socket_path=str(tmp_path / "test.sock"),
        crash_log_dir=str(tmp_path / "crashes"),
        heartbeat_interval=1.0,
        max_restart_attempts=3,
        restart_cooldown=0.0,
        lean_logging=True,
    )
    defaults.update(kwargs)
    return freecad_health.FreeCADHealthMonitor(**defaults)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_params(self, tmp_path):
        m = make_monitor(tmp_path)
        assert m.socket_path == tmp_path / "test.sock"
        assert m.heartbeat_interval == 1.0
        assert m.max_restart_attempts == 3
        assert m.lean_logging is True
        assert m.is_healthy is False
        assert m.last_heartbeat is None
        assert m.consecutive_failures == 0
        assert m.restart_attempts == 0
        assert m.crash_history == []
        assert m.freecad_pid is None

    def test_custom_params(self, tmp_path):
        m = make_monitor(
            tmp_path,
            heartbeat_interval=10.0,
            max_restart_attempts=5,
            restart_cooldown=20.0,
            lean_logging=False,
        )
        assert m.heartbeat_interval == 10.0
        assert m.max_restart_attempts == 5
        assert m.restart_cooldown == 20.0
        assert m.lean_logging is False

    def test_creates_crash_log_dir(self, tmp_path):
        crash_dir = tmp_path / "nested" / "crash_logs"
        make_monitor(tmp_path, crash_log_dir=str(crash_dir))
        assert crash_dir.is_dir()

    def test_lean_vs_verbose_logging(self, tmp_path):
        m_lean = make_monitor(tmp_path, lean_logging=True)
        m_verbose = make_monitor(tmp_path, lean_logging=False)
        assert m_lean.lean_logging is True
        assert m_verbose.lean_logging is False


# ---------------------------------------------------------------------------
# check_socket_exists
# ---------------------------------------------------------------------------

class TestCheckSocketExists:
    def test_socket_exists(self, tmp_path):
        sock = tmp_path / "test.sock"
        sock.touch()
        m = make_monitor(tmp_path, socket_path=str(sock))
        assert m.check_socket_exists() is True

    def test_socket_missing(self, tmp_path):
        m = make_monitor(tmp_path, socket_path=str(tmp_path / "missing.sock"))
        assert m.check_socket_exists() is False

    def test_verbose_logs_debug(self, tmp_path):
        m = make_monitor(tmp_path, lean_logging=False)
        m.check_socket_exists()
        # Just verify no exceptions in verbose mode


# ---------------------------------------------------------------------------
# check_socket_responsive
# ---------------------------------------------------------------------------

class TestCheckSocketResponsive:
    def test_socket_file_missing(self, tmp_path):
        m = make_monitor(tmp_path, socket_path=str(tmp_path / "missing.sock"))
        responsive, error = m.check_socket_responsive()
        assert responsive is False
        assert error == "Socket file does not exist"

    @patch("freecad_health.socket.socket")
    def test_responsive_success(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path))

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.return_value = b'{"status": "ok"}'

        responsive, error = m.check_socket_responsive(timeout=1.0)
        assert responsive is True
        assert error is None
        mock_sock.settimeout.assert_called_once_with(1.0)
        mock_sock.connect.assert_called_once_with(str(sock_path))
        mock_sock.close.assert_called_once()

    @patch("freecad_health.socket.socket")
    def test_timeout(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path))

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        import socket as real_socket
        mock_sock.connect.side_effect = real_socket.timeout("timed out")

        responsive, error = m.check_socket_responsive()
        assert responsive is False
        assert error == "Socket connection timeout"
        mock_sock.close.assert_called_once()

    @patch("freecad_health.socket.socket")
    def test_connection_refused(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path))

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError()

        responsive, error = m.check_socket_responsive()
        assert responsive is False
        assert error == "Connection refused"
        mock_sock.close.assert_called_once()

    @patch("freecad_health.socket.socket")
    def test_no_response(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path))

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.return_value = b""

        responsive, error = m.check_socket_responsive()
        assert responsive is False
        assert error == "Socket connected but no response"

    @patch("freecad_health.socket.socket")
    def test_general_exception(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path))

        mock_socket_cls.side_effect = OSError("bad socket")

        responsive, error = m.check_socket_responsive()
        assert responsive is False
        assert "Socket check failed" in error

    @patch("freecad_health.socket.socket")
    def test_verbose_logs_on_success(self, mock_socket_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        m = make_monitor(tmp_path, socket_path=str(sock_path), lean_logging=False)

        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.return_value = b'{"ok": true}'

        responsive, _ = m.check_socket_responsive()
        assert responsive is True


# ---------------------------------------------------------------------------
# check_freecad_process
# ---------------------------------------------------------------------------

class TestCheckFreecadProcess:
    @patch("freecad_health.subprocess.run")
    def test_process_found_single_pid(self, mock_run, tmp_path):
        m = make_monitor(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="12345\n")

        running, pid = m.check_freecad_process()
        assert running is True
        assert pid == 12345
        assert m.freecad_pid == 12345

    @patch("freecad_health.subprocess.run")
    def test_process_found_multiple_pids(self, mock_run, tmp_path):
        m = make_monitor(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="12345\n67890\n")

        running, pid = m.check_freecad_process()
        assert running is True
        assert pid == 12345  # returns first PID

    @patch("freecad_health.subprocess.run")
    def test_process_not_found(self, mock_run, tmp_path):
        m = make_monitor(tmp_path)
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        running, pid = m.check_freecad_process()
        assert running is False
        assert pid is None

    @patch("freecad_health.subprocess.run")
    def test_process_empty_stdout(self, mock_run, tmp_path):
        m = make_monitor(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")

        running, pid = m.check_freecad_process()
        assert running is False
        assert pid is None

    @patch("freecad_health.subprocess.run")
    def test_subprocess_exception(self, mock_run, tmp_path):
        m = make_monitor(tmp_path)
        mock_run.side_effect = Exception("pgrep not found")

        running, pid = m.check_freecad_process()
        assert running is False
        assert pid is None

    @patch("freecad_health.subprocess.run")
    def test_verbose_logging(self, mock_run, tmp_path):
        m = make_monitor(tmp_path, lean_logging=False)
        mock_run.return_value = MagicMock(returncode=0, stdout="999\n")

        running, pid = m.check_freecad_process()
        assert running is True
        assert pid == 999


# ---------------------------------------------------------------------------
# perform_health_check
# ---------------------------------------------------------------------------

class TestPerformHealthCheck:
    def test_healthy(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=True), \
             patch.object(m, "check_socket_responsive", return_value=(True, None)), \
             patch.object(m, "check_freecad_process", return_value=(True, 42)):
            status = m.perform_health_check()

        assert status["is_healthy"] is True
        assert status["socket_exists"] is True
        assert status["socket_responsive"] is True
        assert status["process_running"] is True
        assert status["freecad_pid"] == 42
        assert status["error"] is None
        assert m.is_healthy is True
        assert m.consecutive_failures == 0
        assert m.last_heartbeat is not None

    def test_unhealthy_socket_missing(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=False), \
             patch.object(m, "check_freecad_process", return_value=(False, None)):
            status = m.perform_health_check()

        assert status["is_healthy"] is False
        assert status["socket_exists"] is False
        assert status["socket_responsive"] is False
        assert m.consecutive_failures == 1

    def test_unhealthy_socket_unresponsive(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=True), \
             patch.object(m, "check_socket_responsive", return_value=(False, "timeout")), \
             patch.object(m, "check_freecad_process", return_value=(True, 100)):
            status = m.perform_health_check()

        assert status["is_healthy"] is False
        assert status["socket_responsive"] is False
        assert status["error"] == "timeout"

    def test_unhealthy_no_process(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=True), \
             patch.object(m, "check_socket_responsive", return_value=(True, None)), \
             patch.object(m, "check_freecad_process", return_value=(False, None)):
            status = m.perform_health_check()

        assert status["is_healthy"] is False

    def test_consecutive_failures_increment(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=False), \
             patch.object(m, "check_freecad_process", return_value=(False, None)):
            m.perform_health_check()
            m.perform_health_check()
            m.perform_health_check()

        assert m.consecutive_failures == 3

    def test_consecutive_failures_reset_on_healthy(self, tmp_path):
        m = make_monitor(tmp_path)
        m.consecutive_failures = 5

        with patch.object(m, "check_socket_exists", return_value=True), \
             patch.object(m, "check_socket_responsive", return_value=(True, None)), \
             patch.object(m, "check_freecad_process", return_value=(True, 1)):
            m.perform_health_check()

        assert m.consecutive_failures == 0

    def test_verbose_logging_format(self, tmp_path):
        m = make_monitor(tmp_path, lean_logging=False)
        with patch.object(m, "check_socket_exists", return_value=False), \
             patch.object(m, "check_freecad_process", return_value=(False, None)):
            status = m.perform_health_check()
        assert "timestamp" in status

    def test_returns_timestamp(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "check_socket_exists", return_value=False), \
             patch.object(m, "check_freecad_process", return_value=(False, None)):
            status = m.perform_health_check()
        assert "timestamp" in status
        # Should be ISO format
        from datetime import datetime
        datetime.fromisoformat(status["timestamp"])


# ---------------------------------------------------------------------------
# log_crash
# ---------------------------------------------------------------------------

class TestLogCrash:
    def test_creates_crash_file(self, tmp_path):
        m = make_monitor(tmp_path)
        m.debugger.capture_freecad_state = MagicMock(return_value={"doc": "test"})

        health_status = {"socket_responsive": False, "process_running": False}
        m.log_crash(health_status)

        crash_files = list((tmp_path / "crashes").glob("crash_*.json"))
        assert len(crash_files) == 1

        data = json.loads(crash_files[0].read_text())
        assert data["health_status"] == health_status
        assert data["freecad_state"] == {"doc": "test"}

    def test_appends_to_crash_history(self, tmp_path):
        m = make_monitor(tmp_path)
        m.debugger.capture_freecad_state = MagicMock(return_value={})

        m.log_crash({"a": 1})
        m.log_crash({"b": 2})
        assert len(m.crash_history) == 2

    def test_additional_info_included(self, tmp_path):
        m = make_monitor(tmp_path)
        m.debugger.capture_freecad_state = MagicMock(return_value={})

        m.log_crash({"x": 1}, additional_info={"reason": "oom"})

        crash_files = list((tmp_path / "crashes").glob("crash_*.json"))
        data = json.loads(crash_files[0].read_text())
        assert data["additional_info"] == {"reason": "oom"}

    def test_state_capture_failure(self, tmp_path):
        m = make_monitor(tmp_path)
        m.debugger.capture_freecad_state = MagicMock(
            side_effect=RuntimeError("no FreeCAD")
        )

        m.log_crash({"err": True})

        crash_files = list((tmp_path / "crashes").glob("crash_*.json"))
        data = json.loads(crash_files[0].read_text())
        assert "error" in data["freecad_state"]
        assert "no FreeCAD" in data["freecad_state"]["error"]

    def test_records_consecutive_failures(self, tmp_path):
        m = make_monitor(tmp_path)
        m.debugger.capture_freecad_state = MagicMock(return_value={})
        m.consecutive_failures = 7
        m.restart_attempts = 2

        m.log_crash({})

        data = json.loads(
            list((tmp_path / "crashes").glob("crash_*.json"))[0].read_text()
        )
        assert data["consecutive_failures"] == 7
        assert data["restart_attempts"] == 2


# ---------------------------------------------------------------------------
# cleanup_socket
# ---------------------------------------------------------------------------

class TestCleanupSocket:
    def test_deletes_existing_socket(self, tmp_path):
        sock = tmp_path / "test.sock"
        sock.touch()
        m = make_monitor(tmp_path, socket_path=str(sock))

        m.cleanup_socket()
        assert not sock.exists()

    def test_missing_socket_noop(self, tmp_path):
        m = make_monitor(tmp_path, socket_path=str(tmp_path / "missing.sock"))
        # Should not raise
        m.cleanup_socket()

    def test_permission_error(self, tmp_path):
        sock = tmp_path / "test.sock"
        sock.touch()
        m = make_monitor(tmp_path, socket_path=str(sock))

        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            # Should not raise, just log warning
            m.cleanup_socket()


# ---------------------------------------------------------------------------
# attempt_restart
# ---------------------------------------------------------------------------

class TestAttemptRestart:
    def test_max_attempts_exceeded(self, tmp_path):
        m = make_monitor(tmp_path, max_restart_attempts=2)
        m.restart_attempts = 2

        result = m.attempt_restart()
        assert result is False
        assert m.restart_attempts == 2  # not incremented

    @patch("freecad_health.time.sleep")
    @patch("freecad_health.os.kill")
    def test_kills_running_process(self, mock_kill, mock_sleep, tmp_path):
        m = make_monitor(tmp_path, restart_cooldown=0.0)

        # First call: process running (pid 123). Second call (after SIGTERM): not running.
        with patch.object(m, "check_freecad_process", side_effect=[
            (True, 123),   # initial check
            (False, None), # after SIGTERM
        ]):
            with patch.object(m, "cleanup_socket"):
                result = m.attempt_restart()

        mock_kill.assert_called_once_with(123, __import__("signal").SIGTERM)
        assert m.restart_attempts == 1
        assert result is False  # restart not implemented yet

    @patch("freecad_health.time.sleep")
    @patch("freecad_health.os.kill")
    def test_force_kills_stubborn_process(self, mock_kill, mock_sleep, tmp_path):
        m = make_monitor(tmp_path, restart_cooldown=0.0)

        # Process still running after SIGTERM
        with patch.object(m, "check_freecad_process", side_effect=[
            (True, 456),  # initial check
            (True, 456),  # still running after SIGTERM
        ]):
            with patch.object(m, "cleanup_socket"):
                m.attempt_restart()

        import signal
        assert mock_kill.call_args_list == [
            call(456, signal.SIGTERM),
            call(456, signal.SIGKILL),
        ]

    @patch("freecad_health.time.sleep")
    def test_no_process_to_kill(self, mock_sleep, tmp_path):
        m = make_monitor(tmp_path, restart_cooldown=0.0)

        with patch.object(m, "check_freecad_process", return_value=(False, None)), \
             patch.object(m, "cleanup_socket"):
            result = m.attempt_restart()

        assert m.restart_attempts == 1
        assert result is False

    @patch("freecad_health.time.sleep")
    @patch("freecad_health.os.kill", side_effect=ProcessLookupError("no such process"))
    def test_kill_exception_handled(self, mock_kill, mock_sleep, tmp_path):
        m = make_monitor(tmp_path, restart_cooldown=0.0)

        with patch.object(m, "check_freecad_process", return_value=(True, 789)), \
             patch.object(m, "cleanup_socket"):
            # Should not raise
            m.attempt_restart()

    @patch("freecad_health.time.sleep")
    def test_cooldown_sleep(self, mock_sleep, tmp_path):
        m = make_monitor(tmp_path, restart_cooldown=5.0)

        with patch.object(m, "check_freecad_process", return_value=(False, None)), \
             patch.object(m, "cleanup_socket"):
            m.attempt_restart()

        # Should sleep for cooldown (and possibly 2s/1s for kill waits, but no process here)
        mock_sleep.assert_called_with(5.0)


# ---------------------------------------------------------------------------
# get_crash_statistics
# ---------------------------------------------------------------------------

class TestGetCrashStatistics:
    def test_empty_history(self, tmp_path):
        m = make_monitor(tmp_path)
        stats = m.get_crash_statistics()
        assert stats["total_crashes"] == 0
        assert "message" in stats

    def test_with_crashes(self, tmp_path):
        m = make_monitor(tmp_path)
        m.crash_history = [
            {"timestamp": "2026-01-01T00:00:00", "consecutive_failures": 1},
            {"timestamp": "2026-01-02T00:00:00", "consecutive_failures": 3},
            {"timestamp": "2026-01-03T00:00:00", "consecutive_failures": 2},
        ]
        m.restart_attempts = 2

        stats = m.get_crash_statistics()
        assert stats["total_crashes"] == 3
        assert stats["first_crash"] == "2026-01-01T00:00:00"
        assert stats["last_crash"] == "2026-01-03T00:00:00"
        assert stats["restart_attempts"] == 2
        assert stats["max_consecutive_failures"] == 3


# ---------------------------------------------------------------------------
# export_crash_report
# ---------------------------------------------------------------------------

class TestExportCrashReport:
    def test_default_output_path(self, tmp_path):
        m = make_monitor(tmp_path)
        with patch.object(m, "perform_health_check", return_value={"is_healthy": False}):
            path = m.export_crash_report()

        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert "generated_at" in data
        assert "statistics" in data
        assert "crash_history" in data
        assert "current_health" in data

    def test_custom_output_path(self, tmp_path):
        m = make_monitor(tmp_path)
        output = tmp_path / "my_report.json"

        with patch.object(m, "perform_health_check", return_value={"ok": True}):
            path = m.export_crash_report(output_file=str(output))

        assert path == str(output)
        assert output.exists()
        data = json.loads(output.read_text())
        assert "statistics" in data

    def test_includes_crash_history(self, tmp_path):
        m = make_monitor(tmp_path)
        m.crash_history = [
            {"timestamp": "2026-04-07T12:00:00", "consecutive_failures": 1}
        ]

        with patch.object(m, "perform_health_check", return_value={}):
            path = m.export_crash_report()

        data = json.loads(Path(path).read_text())
        assert len(data["crash_history"]) == 1


# ---------------------------------------------------------------------------
# Global functions
# ---------------------------------------------------------------------------

class TestGlobalFunctions:
    def setup_method(self):
        # Reset global singleton before each test
        freecad_health._monitor = None

    def test_get_monitor_creates_singleton(self, tmp_path):
        m = freecad_health.get_monitor()
        assert m is not None
        assert freecad_health.get_monitor() is m  # same instance

    def test_init_monitor_replaces_singleton(self, tmp_path):
        m1 = freecad_health.get_monitor()
        m2 = freecad_health.init_monitor(
            socket_path=str(tmp_path / "new.sock"),
            crash_log_dir=str(tmp_path / "crashes"),
        )
        assert m2 is not m1
        assert freecad_health.get_monitor() is m2

    def test_init_monitor_default_lean_logging(self, tmp_path):
        m = freecad_health.init_monitor(
            socket_path=str(tmp_path / "x.sock"),
            crash_log_dir=str(tmp_path / "c"),
        )
        assert m.lean_logging == freecad_health.LEAN_LOGGING

    def test_init_monitor_explicit_lean_logging(self, tmp_path):
        m = freecad_health.init_monitor(
            socket_path=str(tmp_path / "x.sock"),
            crash_log_dir=str(tmp_path / "c"),
            lean_logging=False,
        )
        assert m.lean_logging is False

    def test_health_check_delegates(self, tmp_path):
        m = freecad_health.init_monitor(
            socket_path=str(tmp_path / "x.sock"),
            crash_log_dir=str(tmp_path / "c"),
        )
        with patch.object(m, "perform_health_check", return_value={"ok": True}) as mock_hc:
            result = freecad_health.health_check()
        assert result == {"ok": True}
        mock_hc.assert_called_once()

    def test_crash_statistics_delegates(self, tmp_path):
        m = freecad_health.init_monitor(
            socket_path=str(tmp_path / "x.sock"),
            crash_log_dir=str(tmp_path / "c"),
        )
        with patch.object(m, "get_crash_statistics", return_value={"total_crashes": 0}) as mock_cs:
            result = freecad_health.crash_statistics()
        assert result == {"total_crashes": 0}
        mock_cs.assert_called_once()

    def test_export_crash_report_delegates(self, tmp_path):
        m = freecad_health.init_monitor(
            socket_path=str(tmp_path / "x.sock"),
            crash_log_dir=str(tmp_path / "c"),
        )
        with patch.object(m, "export_crash_report", return_value="/tmp/report.json") as mock_er:
            result = freecad_health.export_crash_report("/tmp/out.json")
        assert result == "/tmp/report.json"
        mock_er.assert_called_once_with("/tmp/out.json")

    def teardown_method(self):
        freecad_health._monitor = None

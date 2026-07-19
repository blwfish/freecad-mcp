"""Unit tests for the OpLog poll-noise fix (M5).

freecad_mcp_server.py's send_to_freecad() previously called _record_op()
and _complete_op() unconditionally for every tool, including poll_job —
called ~1/sec by poll_job_until_done while an async job runs. That filled
the 10-slot OpLog ring buffer with poll noise within ~7s (evicting the
entry for the actual long-running operation diagnose() needs) and marked
that operation "completed" on the first successful poll response, even
when the poll only reported "still running".

_record_op/_complete_op/_POLL_NOISE_TOOLS are plain module-level
functions/constants in freecad_mcp_server.py (not nested inside the
send_to_freecad closure), so they're directly testable without needing
the full main() coroutine machinery.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import freecad_mcp_server as bridge


def _oplog_available():
    return bridge._op_log is not None


class TestPollNoiseToolsSet:
    """Single source of truth both _record_op and send_to_freecad's
    internal _complete_op guard read from — pin its exact contents so a
    future edit can't silently widen or narrow it without a test noticing."""

    def test_contains_exactly_poll_job(self):
        assert bridge._POLL_NOISE_TOOLS == {"poll_job"}


class TestRecordOpSkipsPollJob:
    def setup_method(self):
        if not _oplog_available():
            import pytest
            pytest.skip("freecad_crash_report.py not loadable in this environment")
        # Snapshot and clear so each test starts from a known state,
        # restored in teardown rather than depending on test order.
        self._snapshot = list(bridge._op_log._ops)
        bridge._op_log._ops.clear()

    def teardown_method(self):
        if not _oplog_available():
            return
        bridge._op_log._ops.clear()
        bridge._op_log._ops.extend(self._snapshot)

    def test_poll_job_does_not_create_ring_buffer_entry(self):
        bridge._record_op("poll_job", {"job_id": "abc123"})
        assert len(bridge._op_log._ops) == 0

    def test_other_tools_still_create_ring_buffer_entry(self):
        bridge._record_op("execute_python_async", {"code": "1+1"})
        assert len(bridge._op_log._ops) == 1
        assert bridge._op_log._ops[0]["tool"] == "execute_python_async"

    def test_repeated_poll_job_calls_never_evict_the_real_operation(self):
        """Reproduces the exact motivating scenario: one real long-running
        operation submitted, followed by many poll_job calls (more than
        MAX_OPLOG) while it's in flight. The real operation must still be
        findable afterward — before this fix, 10 poll_job calls alone
        would have evicted it entirely."""
        bridge._record_op("execute_python_async", {"code": "long_running_boolean_op()"})
        for _ in range(25):  # far more than MAX_OPLOG=10
            bridge._record_op("poll_job", {"job_id": "abc123"})

        assert len(bridge._op_log._ops) == 1
        assert bridge._op_log._ops[0]["tool"] == "execute_python_async"
        incomplete = bridge._op_log.last_incomplete()
        assert incomplete is not None
        assert incomplete["tool"] == "execute_python_async"

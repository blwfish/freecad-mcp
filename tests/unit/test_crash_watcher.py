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
import stat
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


class TestSetCurrentOpFilePermissions:
    """F6: LAST_OP_FILE sits directly under the shared, world-writable/
    world-traversable /tmp with a fixed, predictable name. Its content
    (tool args, which can include a real credential embedded in
    execute_python source) must never be readable by another local
    account regardless of the process umask."""

    def test_file_created_owner_only(self):
        crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        mode = stat.S_IMODE(os.stat(crash_watcher.LAST_OP_FILE).st_mode)
        assert mode == 0o600

    def test_file_created_owner_only_even_with_permissive_umask(self):
        """A 000 umask would normally leave a freshly-created file at
        whatever mode the open() call requested, unmasked — confirming the
        mode comes from the explicit opener, not merely "got lucky" with
        the ambient umask on a typical dev/CI box (022 or 002)."""
        old_umask = os.umask(0o000)
        try:
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        finally:
            os.umask(old_umask)
        mode = stat.S_IMODE(os.stat(crash_watcher.LAST_OP_FILE).st_mode)
        assert mode == 0o600

    def test_restrictive_umask_does_not_widen_permissions(self):
        """A umask that would ordinarily narrow permissions further (e.g.
        clearing the owner-write bit) is respected, not overridden back
        up to 0600 -- the opener requests 0600 as a ceiling, not a floor."""
        old_umask = os.umask(0o200)  # clears owner-write
        try:
            crash_watcher.set_current_op("execute_python", {"code": "1+1"})
        finally:
            os.umask(old_umask)
        mode = stat.S_IMODE(os.stat(crash_watcher.LAST_OP_FILE).st_mode)
        assert mode == 0o400, "umask narrows the requested 0600, never widens it"
        assert mode & 0o077 == 0, "must never be group/world readable"


class TestRedactSecrets:
    """F6: args are persisted to a shared /tmp path purely for crash
    diagnosis, but may legitimately contain a real credential a user
    embedded in execute_python source. Common recognizable secret shapes
    must be redacted before the file is written."""

    def test_aws_access_key_redacted(self):
        assert crash_watcher._redact_secrets("AKIAABCDEFGHIJKLMNOP") == "[REDACTED]"

    def test_openai_style_key_redacted(self):
        s = "api_key=sk-abcdef1234567890ABCDEFGHIJ"
        out = crash_watcher._redact_secrets(s)
        assert "sk-abcdef1234567890ABCDEFGHIJ" not in out
        assert "[REDACTED]" in out

    def test_bearer_token_redacted(self):
        s = "Authorization: Bearer abc123.def456-ghi789"
        out = crash_watcher._redact_secrets(s)
        assert "abc123.def456-ghi789" not in out

    def test_jwt_redacted(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        out = crash_watcher._redact_secrets(f"token = {jwt}")
        assert jwt not in out

    def test_password_assignment_redacted_key_preserved(self):
        out = crash_watcher._redact_secrets("password: 'hunter2222'")
        assert "hunter2222" not in out
        assert out.startswith("password: ")

    def test_url_embedded_credential_redacted_but_user_and_host_kept(self):
        out = crash_watcher._redact_secrets("postgres://svc_user:hunter2@db.internal/prod")
        assert "hunter2" not in out
        assert "svc_user" in out
        assert "db.internal" in out

    def test_ordinary_prose_mentioning_password_is_untouched(self):
        """No '=' or ':' assignment shape -- must not be mangled."""
        s = "this handles the password reset flow for the user"
        assert crash_watcher._redact_secrets(s) == s

    def test_short_non_secret_assignment_is_untouched(self):
        """Below the minimum value length -- avoids over-eager redaction
        of ordinary short variable assignments that merely contain one of
        the watched key names."""
        s = "x=1234"
        assert crash_watcher._redact_secrets(s) == s

    def test_set_current_op_writes_redacted_args_not_raw_secret(self):
        secret = "sk-abcdef1234567890ABCDEFGHIJ"
        crash_watcher.set_current_op(
            "execute_python",
            {"code": f"import requests\nrequests.get(url, headers={{'api_key': '{secret}'}})"},
        )
        with open(crash_watcher.LAST_OP_FILE, "rb") as f:
            raw = f.read()
        assert secret.encode() not in raw
        data = json.loads(raw)
        assert "[REDACTED]" in data["args"]["code"]


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

    def test_refuses_planted_symlink_at_tmp_path(self, tmp_path):
        """M14: LAST_OP_FILE is a fixed, predictable /tmp path (PID-based,
        but PIDs are guessable/observable). Previously open(tmp, "wb")
        would silently follow a pre-planted symlink at the .tmp path
        instead of refusing it, writing crash-diagnosis data to wherever
        the attacker pointed it."""
        attacker_target = tmp_path / "attacker_controlled"
        attacker_target.write_text("")
        planted_tmp = crash_watcher.LAST_OP_FILE + ".tmp"
        os.symlink(str(attacker_target), planted_tmp)

        crash_watcher.set_current_op("execute_python", {"code": "1+1"})

        assert crash_watcher.get_write_failure_count() == 1
        assert attacker_target.read_text() == "", "must not have written through the symlink"

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

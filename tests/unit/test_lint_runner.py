"""Unit tests for AICopilot/lint_runner.py with FAKE FreeCADCmd binaries.

Process handling, path allowlist and the failure vocabulary are shared with the
regression runner (tests/unit/test_regression_runner.py covers them in depth);
these pin the lint-specific contract: env passed to the worker, the temp copy,
validation of the lint's own arguments, typed worker failures, and cleanup.
Deaths use SIGKILL, not SIGSEGV: macOS raises a CrashReporter notification for
every real segfault.

Run with: python3 -m pytest tests/unit/test_lint_runner.py -v
"""

import glob
import hashlib
import json
import os
import stat
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if 'FreeCAD' not in sys.modules:
    for _m in ('FreeCAD', 'FreeCADGui', 'Part', 'Sketcher'):
        sys.modules[_m] = MagicMock()
sys.modules['FreeCAD'].ActiveDocument = None

MODULE_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))
sys.path.insert(0, MODULE_DIR)

import lint_runner as lr  # noqa: E402
import regression_runner as rr  # noqa: E402

_PRELUDE = f"#!{sys.executable}\nimport json, os, sys, signal, time\n" + textwrap.dedent("""
    OUT = os.environ["FREECAD_MCP_LINT_OUT"]
    IN = os.environ["FREECAD_MCP_LINT_IN"]
    BOP = os.environ["FREECAD_MCP_LINT_BOP"]
    MODDIR = os.environ["FREECAD_MCP_LINT_MODDIR"]
    def report(extra=None):
        r = {"format_version": 1, "objects_checked": 3, "root_findings": 0, "roots": [], "inherited": []}
        r.update(extra or {})
        json.dump(r, open(OUT, "w"))
""")


def make_fake(tmp_path, body, name="fakecmd"):
    p = tmp_path / name
    p.write_text(_PRELUDE + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


@pytest.fixture
def fcstd(tmp_path):
    f = tmp_path / "model.FCStd"
    f.write_bytes(b"PK-not-really-a-zip but the runner only copies it")
    return str(f)


def lint(fcstd, binary, **kw):
    return lr.run_lint(fcstd, binary, MODULE_DIR, **kw)


CLEAN = "report()"


class TestValidation:

    def test_path_outside_the_allowlist(self, tmp_path):
        r = lint("/etc/passwd", make_fake(tmp_path, CLEAN))
        assert r["error_kind"] == "invalid_argument" and "outside allowed" in r["error"]

    def test_non_fcstd_extension(self, tmp_path):
        f = tmp_path / "model.step"
        f.write_text("x")
        assert lint(str(f), make_fake(tmp_path, CLEAN))["error_kind"] == "invalid_argument"

    def test_missing_file(self, tmp_path):
        assert "not found" in lint(str(tmp_path / "no.FCStd"), make_fake(tmp_path, CLEAN))["error"]

    def test_missing_binary(self, fcstd, tmp_path):
        assert lint(fcstd, str(tmp_path / "nope"))["error_kind"] == "invalid_argument"

    def test_module_dir_without_the_lint_worker(self, fcstd, tmp_path):
        r = lr.run_lint(fcstd, make_fake(tmp_path, CLEAN), str(tmp_path))
        assert r["error_kind"] == "invalid_argument" and "lint_worker.py" in r["error"]

    @pytest.mark.parametrize("bop", [0, 1, "yes", None])
    def test_non_boolean_bop_check_rejected(self, fcstd, tmp_path, bop):
        r = lint(fcstd, make_fake(tmp_path, CLEAN), bop_check=bop)
        assert r["error_kind"] == "invalid_argument" and "bop_check" in r["error"]

    @pytest.mark.parametrize("t", [9.99, 0, -1, 1800.01, True, "300", None])
    def test_bad_timeout_rejected(self, fcstd, tmp_path, t):
        r = lint(fcstd, make_fake(tmp_path, CLEAN), timeout_s=t)
        assert r["error_kind"] == "invalid_argument" and "timeout_s" in r["error"]

    @pytest.mark.parametrize("t", [10, 1800])
    def test_timeout_boundaries_accepted(self, fcstd, tmp_path, t):
        assert "error" not in lint(fcstd, make_fake(tmp_path, CLEAN), timeout_s=t)


class TestHappyPath:

    def test_report_is_returned_with_the_checked_files_path(self, fcstd, tmp_path):
        r = lint(fcstd, make_fake(tmp_path, 'report({"root_findings": 2})'))
        assert r["root_findings"] == 2 and r["file"] == os.path.realpath(fcstd)

    @pytest.mark.parametrize("bop,expected", [(False, "0"), (True, "1")])
    def test_env_contract(self, fcstd, tmp_path, bop, expected):
        side = tmp_path / "env.json"
        body = f"""
            json.dump({{"bop": BOP, "moddir": MODDIR, "in": IN}}, open({str(side)!r}, "w"))
            report()
        """
        lint(fcstd, make_fake(tmp_path, body), bop_check=bop)
        seen = json.loads(side.read_text())
        assert seen["bop"] == expected and seen["moddir"] == MODULE_DIR and os.path.basename(seen["in"]) == "model.FCStd"

    def test_bop_is_off_by_default(self, fcstd, tmp_path):
        side = tmp_path / "bop.txt"
        lint(fcstd, make_fake(tmp_path, f'open({str(side)!r}, "w").write(BOP)\nreport()'))
        assert side.read_text() == "0"

    def test_the_worker_gets_a_copy_and_the_original_is_untouched(self, fcstd, tmp_path):
        side = tmp_path / "in.txt"
        body = f"""
            open({str(side)!r}, "w").write(IN)
            open(IN, "ab").write(b"WORKER SCRIBBLED ON ITS COPY")
            report()
        """
        before, mtime = hashlib.sha256(Path(fcstd).read_bytes()).hexdigest(), os.stat(fcstd).st_mtime_ns
        lint(fcstd, make_fake(tmp_path, body))
        assert side.read_text() != fcstd
        assert hashlib.sha256(Path(fcstd).read_bytes()).hexdigest() == before and os.stat(fcstd).st_mtime_ns == mtime

    def test_temp_dir_is_removed(self, fcstd, tmp_path):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_lint_*")))
        lint(fcstd, make_fake(tmp_path, CLEAN))
        assert set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_lint_*"))) == before

    def test_stdout_flood_does_not_wedge_the_worker(self, fcstd, tmp_path):
        body = """
            sys.stdout.write("x" * (8 * 1024 * 1024)); sys.stdout.flush()
            report()
        """
        assert "error" not in lint(fcstd, make_fake(tmp_path, body), timeout_s=60)


class TestWorkerFailures:

    def test_death_by_signal_is_a_typed_failure(self, fcstd, tmp_path):
        r = lint(fcstd, make_fake(tmp_path, 'print("dying", flush=True)\nos.kill(os.getpid(), signal.SIGKILL)'))
        assert r["error_kind"] == "worker_failed"
        assert r["failure"]["status"] == rr.STATUS_CRASHED and r["failure"]["signal"] == "SIGKILL"
        assert "dying" in r["failure"]["log_tail"]

    def test_worker_reported_error_is_passed_through(self, fcstd, tmp_path):
        r = lint(fcstd, make_fake(tmp_path, 'json.dump({"format_version": 1, "error": "RuntimeError: bad file"}, open(OUT, "w"))'))
        assert r["failure"]["status"] == rr.STATUS_WORKER_ERROR and r["failure"]["detail"] == "RuntimeError: bad file"

    def test_exit_without_a_report(self, fcstd, tmp_path):
        assert lint(fcstd, make_fake(tmp_path, "pass"))["failure"]["status"] == rr.STATUS_NO_OUTPUT

    def test_garbled_report(self, fcstd, tmp_path):
        assert lint(fcstd, make_fake(tmp_path, 'open(OUT, "w").write("{not json")'))["failure"]["status"] == rr.STATUS_UNREADABLE_OUTPUT

    def test_hung_worker_is_killed_at_the_timeout(self, fcstd, tmp_path, monkeypatch):
        monkeypatch.setattr(rr, "MIN_TIMEOUT_S", 0.1)
        monkeypatch.setattr(rr, "_GRACE_S", 1)
        r = lint(fcstd, make_fake(tmp_path, "time.sleep(60)"), timeout_s=0.5)
        assert r["failure"]["status"] == rr.STATUS_TIMEOUT

    def test_temp_dir_is_removed_after_a_failure(self, fcstd, tmp_path):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_lint_*")))
        lint(fcstd, make_fake(tmp_path, "os.kill(os.getpid(), signal.SIGKILL)"))
        assert set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_lint_*"))) == before


class TestSharedValidation:
    """The shared helpers must keep the regression runner's behavior unchanged."""

    def test_validate_file_and_binary_ok(self, fcstd, tmp_path):
        assert rr.validate_file_and_binary(fcstd, make_fake(tmp_path, CLEAN), MODULE_DIR, ("lint_worker.py",)) is None

    def test_validate_timeout_bounds(self):
        assert rr.validate_timeout(10) is None and rr.validate_timeout(1800) is None
        assert rr.validate_timeout(9.99) is not None and rr.validate_timeout(1800.01) is not None

"""Unit tests for AICopilot/regression_runner.py.

The runner shells out to FreeCADCmd, so these tests use FAKE binaries -- small
executable Python scripts -- that behave the ways the real one can: succeed,
segfault, hang, ignore SIGTERM, flood stdout, exit without output, write
garbage. Those are the consumer pathologies that matter: a crashing worker
must come back as a typed failure, a hung one must be killed, and the user's
file and the temp dir must come out untouched/clean either way.

Run with: python3 -m pytest tests/unit/test_regression_runner.py -v
"""

import glob
import hashlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if 'FreeCAD' not in sys.modules:
    _fc = MagicMock()
    _fc.GuiUp = False
    _fc.Console = MagicMock()
    sys.modules['FreeCAD'] = _fc
    sys.modules['FreeCADGui'] = MagicMock()
    sys.modules['Part'] = MagicMock()
    sys.modules['Sketcher'] = MagicMock()
sys.modules['FreeCAD'].ActiveDocument = None

MODULE_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))
sys.path.insert(0, MODULE_DIR)

import regression_runner as rr  # noqa: E402
from handlers.base import BaseHandler  # noqa: E402

# ---------------------------------------------------------------------------
# Fake FreeCADCmd binaries
# ---------------------------------------------------------------------------
_PRELUDE = f"#!{sys.executable}\nimport json, os, sys, signal, time\n" + textwrap.dedent("""
    OUT = os.environ["FREECAD_MCP_REGRESSION_OUT"]
    IN = os.environ["FREECAD_MCP_REGRESSION_IN"]
    JUNK = int(os.environ["FREECAD_MCP_REGRESSION_JUNK"])
    TOL = os.environ["FREECAD_MCP_REGRESSION_TOL"]
    def report(findings, extra=None):
        r = {"format_version": 1, "tolerance": float(TOL), "junk": JUNK, "freecad_version": "fake 1.0",
             "occt_version": "0", "saved_by_program_version": "x", "features_compared": 2,
             "skipped_non_feature": 0, "snapshot_errors": {}, "order": ["Pad", "Fillet"],
             "inputs": {"Fillet": ["Pad"]}, "findings": findings}
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


def run(fcstd, binary, **kw):
    return rr.run_check(fcstd, binary, MODULE_DIR, **kw)


CLEAN = """
    report({"Pad": {}, "Fillet": {}})
"""

FLAKY = """
    # validity lost only when the allocation perturbation is 0 or 100
    bad = {"validity_lost": "valid as stored, invalid after recompute"} if JUNK in (0, 100) else {}
    report({"Pad": {}, "Fillet": bad})
"""


# ---------------------------------------------------------------------------
# Input validation and thresholds
# ---------------------------------------------------------------------------
class TestValidateFilePathParity:
    """validate_file_path is a copy of BaseHandler._validate_file_path (the bridge
    side cannot import handlers.base, which needs FreeCAD). Same verdict on
    every path, or the two allowlists have drifted."""

    @pytest.mark.parametrize("path", [
        "", "~/model.FCStd", "/tmp/x.FCStd", "/private/tmp/x.FCStd", "/var/tmp/x", "/Volumes/Files/a.FCStd",
        "/etc/passwd", "/private/etc/passwd", "/usr/local/x", "/", "/tmpfoo/x.FCStd",
        os.path.join(tempfile.gettempdir(), "a.FCStd"), "relative/path.FCStd", "/tmp/../etc/passwd",
    ])
    def test_same_verdict_as_the_handler_allowlist(self, path):
        assert (rr.validate_file_path(path) is None) == (BaseHandler._validate_file_path(path) is None), path

    @pytest.mark.parametrize("allowed", ["/tmp", "/var/tmp", "/Volumes", "~"])
    def test_prefix_lookalike_is_rejected(self, allowed):
        """A sibling directory whose name merely STARTS with an allowed
        prefix (/private/tmpfoo, /Users/blwevil) must not pass. Built from the
        resolved prefix, because /tmp is /private/tmp on macOS and a literal
        '/tmpfoo' would never look like it."""
        prefix = os.path.realpath(os.path.expanduser(allowed))
        lookalike = prefix + "foo/x.FCStd"
        assert rr.validate_file_path(lookalike) is not None
        assert BaseHandler._validate_file_path(lookalike) is not None
        assert rr.validate_file_path(prefix + "/ok/x.FCStd") is None


class TestArgumentValidation:

    @pytest.fixture
    def good(self, tmp_path, fcstd):
        return fcstd, make_fake(tmp_path, CLEAN)

    def _err(self, good, **kw):
        fcstd, binary = good
        r = run(fcstd, binary, **kw)
        return r

    @pytest.mark.parametrize("runs", [0, -1, 11, True, False, 2.5, "3", None])
    def test_bad_runs_rejected(self, good, runs):
        r = self._err(good, runs=runs)
        assert r["error_kind"] == "invalid_argument" and "runs" in r["error"]

    @pytest.mark.parametrize("runs", [1, 10])
    def test_runs_boundaries_accepted(self, good, runs):
        r = self._err(good, runs=runs)
        assert "error" not in r and r["runs_used"] == runs

    @pytest.mark.parametrize("tol", [0, -0.1, 1.0001, float("nan"), float("inf"), True, "0.1", None])
    def test_bad_tolerance_rejected(self, good, tol):
        r = self._err(good, tolerance=tol)
        assert r["error_kind"] == "invalid_argument" and "tolerance" in r["error"]

    @pytest.mark.parametrize("tol", [1e-12, 0.5, 1, 1.0])
    def test_tolerance_boundaries_accepted(self, good, tol):
        assert "error" not in self._err(good, tolerance=tol, runs=1)

    @pytest.mark.parametrize("t", [9.99, 0, -5, 1800.01, 10 ** 6, True, "300", None])
    def test_bad_timeout_rejected(self, good, t):
        r = self._err(good, timeout_s=t)
        assert r["error_kind"] == "invalid_argument" and "timeout_s" in r["error"]

    @pytest.mark.parametrize("t", [10, 1800])
    def test_timeout_boundaries_accepted(self, good, t):
        assert "error" not in self._err(good, timeout_s=t, runs=1)

    def test_path_outside_allowlist_rejected(self, tmp_path):
        r = run("/etc/passwd", make_fake(tmp_path, CLEAN))
        assert r["error_kind"] == "invalid_argument" and "outside allowed" in r["error"]

    def test_non_fcstd_extension_rejected(self, tmp_path):
        f = tmp_path / "model.step"
        f.write_text("x")
        r = run(str(f), make_fake(tmp_path, CLEAN))
        assert r["error_kind"] == "invalid_argument" and ".FCStd" in r["error"]

    def test_extension_check_is_case_insensitive(self, tmp_path):
        f = tmp_path / "model.fcstd"
        f.write_text("x")
        assert "error" not in run(str(f), make_fake(tmp_path, CLEAN), runs=1)

    def test_missing_file_rejected(self, tmp_path):
        r = run(str(tmp_path / "nope.FCStd"), make_fake(tmp_path, CLEAN))
        assert r["error_kind"] == "invalid_argument" and "not found" in r["error"]

    def test_empty_path_rejected(self, tmp_path):
        assert run("", make_fake(tmp_path, CLEAN))["error_kind"] == "invalid_argument"

    def test_binary_missing_rejected(self, fcstd, tmp_path):
        r = run(fcstd, str(tmp_path / "no-such-binary"))
        assert r["error_kind"] == "invalid_argument" and "binary" in r["error"]

    def test_binary_not_executable_rejected(self, fcstd, tmp_path):
        p = tmp_path / "plain"
        p.write_text("x")
        p.chmod(0o644)
        assert run(fcstd, str(p))["error_kind"] == "invalid_argument"

    def test_binary_none_rejected(self, fcstd):
        assert run(fcstd, None)["error_kind"] == "invalid_argument"

    def test_module_dir_missing_worker_rejected(self, fcstd, tmp_path):
        r = rr.run_check(fcstd, make_fake(tmp_path, CLEAN), str(tmp_path))
        assert r["error_kind"] == "invalid_argument" and "regression_worker.py" in r["error"]


# ---------------------------------------------------------------------------
# Behaviour with a working worker
# ---------------------------------------------------------------------------
class TestHappyPath:

    def test_clean_file_reports_nothing(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, CLEAN))
        assert r["regression_count"] == 0 and r["unstable_count"] == 0
        assert r["runs_used"] == 3 and r["runs_failed"] == 0 and r["failures"] == []

    def test_flaky_worker_is_reported_unstable_not_regression(self, fcstd, tmp_path):
        """Default junk sequence starts 0, 10, 100 -> FLAKY fails on runs 0 and 2."""
        r = run(fcstd, make_fake(tmp_path, FLAKY), runs=3)
        assert r["regression_count"] == 0 and r["unstable_count"] == 1
        assert r["unstable"]["roots"][0]["new"] == {"validity_lost": {"runs_with": 2, "of": 3}}

    def test_same_worker_with_one_run_cannot_see_the_instability(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, FLAKY), runs=1)  # junk=0 -> bad
        assert r["regression_count"] == 1 and r["unstable_count"] == 0

    def test_each_run_gets_a_different_perturbation(self, fcstd, tmp_path):
        side = tmp_path / "junks.txt"  # the fake records the perturbation it was given
        body = f"""
            open({str(side)!r}, "a").write(str(JUNK) + "\\n")
            report({{"Pad": {{}}, "Fillet": {{}}}})
        """
        run(fcstd, make_fake(tmp_path, body), runs=10)
        junks = [int(x) for x in side.read_text().split()]
        assert junks == list(rr.JUNK_SEQUENCE[:10]) and len(set(junks)) == 10

    def test_env_contract_the_worker_relies_on(self, fcstd, tmp_path):
        side = tmp_path / "env.json"
        body = f"""
            json.dump({{"in": IN, "out": OUT, "tol": TOL, "argv": sys.argv[1:],
                       "moddir": os.environ["FREECAD_MCP_REGRESSION_MODDIR"]}}, open({str(side)!r}, "w"))
            report({{"Pad": {{}}, "Fillet": {{}}}})
        """
        run(fcstd, make_fake(tmp_path, body), runs=1, tolerance=0.25)
        seen = json.loads(side.read_text())
        assert seen["tol"] == "0.25" and seen["moddir"] == MODULE_DIR
        assert seen["argv"][0] == "-c" and "regression_worker.py" in seen["argv"][1] and len(seen["argv"]) == 2

    def test_worker_gets_a_copy_never_the_original(self, fcstd, tmp_path):
        side = tmp_path / "in.txt"
        body = f"""
            open({str(side)!r}, "w").write(IN)
            open(IN, "ab").write(b"WORKER SCRIBBLED ON ITS COPY")
            report({{"Pad": {{}}, "Fillet": {{}}}})
        """
        before = hashlib.sha256(Path(fcstd).read_bytes()).hexdigest()
        mtime = os.stat(fcstd).st_mtime_ns
        run(fcstd, make_fake(tmp_path, body), runs=1)
        assert side.read_text() != fcstd
        assert os.path.basename(side.read_text()) == "model.FCStd"
        assert hashlib.sha256(Path(fcstd).read_bytes()).hexdigest() == before
        assert os.stat(fcstd).st_mtime_ns == mtime

    def test_report_names_the_checked_file_and_failure_counters(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, CLEAN), runs=1)
        assert r["file"] == os.path.realpath(fcstd) and "runs_failed" in r and "failures" in r

    def test_temp_dir_is_removed_after_success(self, fcstd, tmp_path):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_regress_*")))
        run(fcstd, make_fake(tmp_path, CLEAN))
        assert set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_regress_*"))) == before

    def test_stdout_flood_does_not_wedge_the_worker(self, fcstd, tmp_path):
        """FreeCAD's console chatter filled an unread 64KB pipe and hung CI for
        weeks (CLAUDE.md). Output must go to a file, not a pipe."""
        body = """
            sys.stdout.write("x" * (8 * 1024 * 1024)); sys.stdout.flush()
            report({"Pad": {}, "Fillet": {}})
        """
        r = run(fcstd, make_fake(tmp_path, body), runs=1, timeout_s=60)
        assert "error" not in r and r["runs_used"] == 1


# ---------------------------------------------------------------------------
# Behaviour with a misbehaving worker
# ---------------------------------------------------------------------------
class TestSignalNameMapping:
    """A worker ended by a signal reports a negative return code; the runner
    turns it into the signal's name. Driven with a stub process so no real
    segfault (and no macOS crash notification) is needed."""

    class _StubProc:
        pid = 1

        def __init__(self, returncode):
            self._rc = returncode

        def wait(self, timeout=None):
            return self._rc

    @pytest.mark.parametrize("returncode,name", [
        (-11, "SIGSEGV"), (-6, "SIGABRT"), (-9, "SIGKILL"), (-15, "SIGTERM"), (-10000, "10000"),
    ])
    def test_negative_returncode_becomes_signal_name(self, monkeypatch, fcstd, tmp_path, returncode, name):
        monkeypatch.setattr(rr.subprocess, "Popen", lambda *a, **k: self._StubProc(returncode))
        r = run(fcstd, make_fake(tmp_path, CLEAN), runs=1)
        f = r["failures"][0]
        assert f["status"] == rr.STATUS_CRASHED and f["signal"] == name

    def test_positive_returncode_is_not_a_crash(self, monkeypatch, fcstd, tmp_path):
        monkeypatch.setattr(rr.subprocess, "Popen", lambda *a, **k: self._StubProc(3))
        r = run(fcstd, make_fake(tmp_path, CLEAN), runs=1)
        assert r["failures"][0]["status"] == rr.STATUS_NONZERO_EXIT


class TestWorkerFailures:

    def test_death_by_signal_is_a_typed_crash_with_the_signal_name(self, fcstd, tmp_path):
        # SIGKILL, not SIGSEGV: macOS raises a CrashReporter notification for every
        # real segfault/abort, and this suite kills dozens of fake workers. The
        # SIGSEGV/SIGABRT name mapping is covered without a real crash below.
        body = """
            print("about to die", flush=True)
            os.kill(os.getpid(), signal.SIGKILL)
        """
        r = run(fcstd, make_fake(tmp_path, body), runs=1)
        assert r["error_kind"] == "all_runs_failed"
        f = r["failures"][0]
        assert f["status"] == rr.STATUS_CRASHED and f["signal"] == "SIGKILL"
        assert "about to die" in f["log_tail"]

    def test_one_crash_among_three_runs_still_yields_a_report(self, fcstd, tmp_path):
        counter = tmp_path / "n"
        body = f"""
            n = int(open({str(counter)!r}).read()) if os.path.exists({str(counter)!r}) else 0
            open({str(counter)!r}, "w").write(str(n + 1))
            if n == 1:
                os.kill(os.getpid(), signal.SIGKILL)
            report({{"Pad": {{}}, "Fillet": {{}}}})
        """
        r = run(fcstd, make_fake(tmp_path, body), runs=3)
        assert "error" not in r
        assert r["runs_requested"] == 3 and r["runs_used"] == 2 and r["runs_failed"] == 1
        assert r["failures"][0]["status"] == rr.STATUS_CRASHED and r["failures"][0]["run"] == 1

    def test_nonzero_exit_without_output(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, "sys.exit(3)"), runs=1)
        f = r["failures"][0]
        assert f["status"] == rr.STATUS_NONZERO_EXIT and f["returncode"] == 3

    def test_clean_exit_without_output(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, "pass"), runs=1)
        assert r["failures"][0]["status"] == rr.STATUS_NO_OUTPUT

    def test_garbage_output(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, 'open(OUT, "w").write("{not json")'), runs=1)
        assert r["failures"][0]["status"] == rr.STATUS_UNREADABLE_OUTPUT

    def test_output_that_is_not_an_object(self, fcstd, tmp_path):
        r = run(fcstd, make_fake(tmp_path, 'open(OUT, "w").write("[1, 2]")'), runs=1)
        assert r["failures"][0]["status"] == rr.STATUS_WORKER_ERROR

    def test_worker_reported_error_is_passed_through(self, fcstd, tmp_path):
        body = 'json.dump({"format_version": 1, "error": "RuntimeError: bad file"}, open(OUT, "w"))'
        r = run(fcstd, make_fake(tmp_path, body), runs=1)
        f = r["failures"][0]
        assert f["status"] == rr.STATUS_WORKER_ERROR and f["detail"] == "RuntimeError: bad file"

    def test_hung_worker_is_killed_at_the_timeout(self, fcstd, tmp_path, monkeypatch):
        monkeypatch.setattr(rr, "MIN_TIMEOUT_S", 0.1)
        monkeypatch.setattr(rr, "_GRACE_S", 1)
        r = run(fcstd, make_fake(tmp_path, "time.sleep(60)"), runs=1, timeout_s=0.5)
        assert r["failures"][0]["status"] == rr.STATUS_TIMEOUT

    def test_worker_ignoring_sigterm_is_escalated_to_sigkill(self, fcstd, tmp_path, monkeypatch):
        monkeypatch.setattr(rr, "MIN_TIMEOUT_S", 0.1)
        monkeypatch.setattr(rr, "_GRACE_S", 0.3)
        body = """
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
        """
        t0 = time.monotonic()
        r = run(fcstd, make_fake(tmp_path, body), runs=1, timeout_s=0.5)
        assert r["failures"][0]["status"] == rr.STATUS_TIMEOUT
        assert time.monotonic() - t0 < 20  # not the worker's 60 s

    def test_timeout_bounds_the_whole_check_not_each_run(self, fcstd, tmp_path, monkeypatch):
        """Run 0 eats the entire budget; later runs must not start fresh clocks."""
        monkeypatch.setattr(rr, "MIN_TIMEOUT_S", 0.1)
        monkeypatch.setattr(rr, "_GRACE_S", 0.3)
        t0 = time.monotonic()
        r = run(fcstd, make_fake(tmp_path, "time.sleep(60)"), runs=4, timeout_s=0.6)
        assert r["error_kind"] == "all_runs_failed" and len(r["failures"]) == 4
        assert time.monotonic() - t0 < 15
        assert any("overall timeout" in f.get("detail", "") for f in r["failures"])

    def test_temp_dir_is_removed_after_a_crash(self, fcstd, tmp_path):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_regress_*")))
        run(fcstd, make_fake(tmp_path, "os.kill(os.getpid(), signal.SIGKILL)"), runs=2)
        assert set(glob.glob(os.path.join(tempfile.gettempdir(), "fcmcp_regress_*"))) == before

    def test_original_untouched_after_a_crash(self, fcstd, tmp_path):
        before = hashlib.sha256(Path(fcstd).read_bytes()).hexdigest()
        run(fcstd, make_fake(tmp_path, "os.kill(os.getpid(), signal.SIGKILL)"), runs=1)
        assert hashlib.sha256(Path(fcstd).read_bytes()).hexdigest() == before

    def test_log_tail_is_bounded(self, fcstd, tmp_path):
        body = """
            sys.stdout.write("y" * 100000); sys.stdout.flush()
            os.kill(os.getpid(), signal.SIGKILL)
        """
        r = run(fcstd, make_fake(tmp_path, body), runs=1)
        assert len(r["failures"][0]["log_tail"]) == rr._LOG_TAIL_CHARS


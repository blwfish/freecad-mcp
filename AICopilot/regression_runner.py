# Orchestrates the regression check: validate inputs, copy the file to a temp
# dir, run regression_worker.py under FreeCADCmd K times (each with a different
# allocation perturbation), and aggregate. Pure Python -- no FreeCAD import --
# so it is unit-tested with fake FreeCADCmd binaries (including ones that crash
# or hang), and the bridge calls it without loading FreeCAD itself.
#
# Failure handling follows the repo's structured-signalling rule: every way a
# run can fail is a typed `status` (never inferred from text); a crashed
# worker's console output is handed whole as `log_tail` for a human/model to
# read, and nothing branches on it.

import contextlib
import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time

import regression_analysis as analysis

MAX_RUNS = 10
MIN_TIMEOUT_S = 10
MAX_TIMEOUT_S = 1800
DEFAULT_RUNS = 3
DEFAULT_TIMEOUT_S = 300

# Allocation perturbations for successive runs (regression_worker's JUNK).
# Mixed small/large values: in the 2026-10-23 sweep, state-dependent fillet
# outcomes flipped across this kind of spread.
JUNK_SEQUENCE = (0, 10, 100, 1, 1000, 3, 30, 300, 2000, 20000)

# Failure statuses for one run -- closed vocabulary.
STATUS_TIMEOUT = "timeout"
STATUS_CRASHED = "crashed"                  # process ended by a signal
STATUS_NONZERO_EXIT = "nonzero_exit"
STATUS_NO_OUTPUT = "no_output"              # exited 0 but wrote no report
STATUS_UNREADABLE_OUTPUT = "unreadable_output"
STATUS_WORKER_ERROR = "worker_error"        # worker caught an exception and said so

_LOG_TAIL_CHARS = 2000
_GRACE_S = 5


def validate_file_path(path: str) -> "str | None":
    """Error string if `path` is outside the same user-writable locations
    handlers/base.py's BaseHandler._validate_file_path allows, else None.
    Kept in step with it by tests/unit/test_regression_runner.py."""
    import sys as _sys
    if not path:
        return "file path is required"
    resolved = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    safe = [home]
    if _sys.platform == "win32":
        safe.append(os.path.realpath(tempfile.gettempdir()))
    else:
        safe += [os.path.realpath(p) for p in ("/tmp", "/var/folders", "/var/tmp", "/Volumes")]
    for prefix in safe:
        if resolved == prefix or resolved.startswith(prefix + os.sep):
            return None
    return (f"Path is outside allowed directories (home dir, /tmp, /Volumes). "
            f"Resolved path: {resolved}")


def validate_file_and_binary(file_path, freecad_binary, module_dir, needed_files) -> "str | None":
    """Checks shared by every worker-based tool: an allowed, existing .FCStd, an
    executable FreeCAD binary, and the worker's own files present in module_dir."""
    err = validate_file_path(file_path)
    if err:
        return err
    resolved = os.path.realpath(os.path.expanduser(file_path))
    if not resolved.lower().endswith(".fcstd"):
        return f"file_path must be a .FCStd file: {resolved}"
    if not os.path.isfile(resolved):
        return f"file not found: {resolved}"
    if not (freecad_binary and os.path.isfile(freecad_binary) and os.access(freecad_binary, os.X_OK)):
        return f"FreeCAD binary not found or not executable: {freecad_binary!r}"
    for needed in needed_files:
        if not os.path.isfile(os.path.join(module_dir, needed)):
            return f"{needed} not found in {module_dir}"
    return None


def validate_timeout(timeout_s) -> "str | None":
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not MIN_TIMEOUT_S <= timeout_s <= MAX_TIMEOUT_S):
        return f"timeout_s must be between {MIN_TIMEOUT_S} and {MAX_TIMEOUT_S}, got {timeout_s!r}"
    return None


def _validate_args(file_path, freecad_binary, module_dir, tolerance, runs, timeout_s,
                   check_validity=True) -> "str | None":
    err = validate_file_and_binary(file_path, freecad_binary, module_dir,
                                   ("regression_worker.py", "regression_analysis.py"))
    if err:
        return err
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= MAX_RUNS:
        return f"runs must be an integer between 1 and {MAX_RUNS}, got {runs!r}"
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or not 0 < tolerance <= 1):
        return f"tolerance must be a number with 0 < tolerance <= 1, got {tolerance!r}"
    err = validate_timeout(timeout_s)
    if err:
        return err
    if not isinstance(check_validity, bool):
        return f"check_validity must be a boolean, got {check_validity!r}"
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the worker's process group, escalate to SIGKILL after a grace
    period. The worker is started in its own session, so the group id is its pid."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=_GRACE_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _tail(path: str) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    return data[-_LOG_TAIL_CHARS:].decode("utf-8", errors="replace")


def run_worker_process(binary: str, script: str, env: dict, out_path: str, log_path: str,
                       deadline: float, base: dict) -> dict:
    """Run one FreeCAD worker script and collect its JSON report. Shared by the
    regression and lint runners. Returns {"ok": True, "report": {...}} or
    {"ok": False, "failure": {**base, "status": ...}} -- never raises for a bad
    worker (crash, timeout, no/garbled output, worker-reported error)."""
    # `-c <code>` rather than a script argument: accepted by FreeCADCmd and the
    # GUI binary alike, and some builds reject unknown flags.
    code = f"exec(compile(open({script!r}).read(), {script!r}, 'exec'))"

    # Output goes to a file, not a pipe: FreeCAD's console chatter can fill an
    # unread pipe and wedge the process (the CI hang documented in CLAUDE.md).
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(  # noqa: S603 -- binary validated above, args are constants
            [binary, "-c", code], env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        remaining = max(1.0, deadline - time.monotonic())
        try:
            returncode = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return {"ok": False, "failure": {**base, "status": STATUS_TIMEOUT, "log_tail": _tail(log_path)}}

    if returncode < 0:
        try:
            sig = signal.Signals(-returncode).name
        except ValueError:
            sig = str(-returncode)
        return {"ok": False, "failure": {**base, "status": STATUS_CRASHED, "signal": sig,
                                         "log_tail": _tail(log_path)}}
    if not os.path.isfile(out_path):
        status = STATUS_NONZERO_EXIT if returncode else STATUS_NO_OUTPUT
        return {"ok": False, "failure": {**base, "status": status, "returncode": returncode,
                                         "log_tail": _tail(log_path)}}
    try:
        with open(out_path) as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        return {"ok": False, "failure": {**base, "status": STATUS_UNREADABLE_OUTPUT,
                                         "detail": f"{type(e).__name__}: {e}", "log_tail": _tail(log_path)}}
    if not isinstance(report, dict) or "error" in report:
        detail = report.get("error") if isinstance(report, dict) else "report is not a JSON object"
        return {"ok": False, "failure": {**base, "status": STATUS_WORKER_ERROR, "detail": detail,
                                         "log_tail": _tail(log_path)}}
    return {"ok": True, "report": report}


def _run_one(index: int, binary: str, module_dir: str, work_copy: str, tmp: str,
             tolerance: float, deadline: float, check_validity: bool = True) -> dict:
    """Run one regression worker. Returns {"ok": True, "report": {...}} or
    {"ok": False, "failure": {...}} -- never raises for a bad worker."""
    out_path = os.path.join(tmp, f"run{index}.json")
    log_path = os.path.join(tmp, f"run{index}.log")
    junk = JUNK_SEQUENCE[index % len(JUNK_SEQUENCE)]
    env = dict(os.environ)
    env.update({
        "FREECAD_MCP_REGRESSION_IN": work_copy,
        "FREECAD_MCP_REGRESSION_OUT": out_path,
        "FREECAD_MCP_REGRESSION_MODDIR": module_dir,
        "FREECAD_MCP_REGRESSION_TOL": repr(float(tolerance)),
        "FREECAD_MCP_REGRESSION_JUNK": str(junk),
        "FREECAD_MCP_REGRESSION_VALIDITY": "1" if check_validity else "0",
    })
    result = run_worker_process(binary, os.path.join(module_dir, "regression_worker.py"), env,
                                out_path, log_path, deadline, {"run": index, "junk": junk})
    if result["ok"]:
        result["report"]["junk"] = junk
    return result


def run_check(file_path: str, freecad_binary: str, module_dir: str,
              tolerance: float = analysis.DEFAULT_TOLERANCE, runs: int = DEFAULT_RUNS,
              timeout_s: float = DEFAULT_TIMEOUT_S, check_validity: bool = True) -> dict:
    """Run the regression check. Returns the aggregated report, or
    {"error": ..., ...} when it could not run at all. `timeout_s` bounds the
    whole check, not each run."""
    err = _validate_args(file_path, freecad_binary, module_dir, tolerance, runs, timeout_s, check_validity)
    if err:
        return {"error": err, "error_kind": "invalid_argument"}

    source = os.path.realpath(os.path.expanduser(file_path))
    tmp = tempfile.mkdtemp(prefix="fcmcp_regress_")
    try:
        # The only copy anything opens; the user's file is never touched.
        work_copy = os.path.join(tmp, os.path.basename(source))
        try:
            shutil.copy2(source, work_copy)
        except OSError as e:
            return {"error": f"could not copy {source}: {e}", "error_kind": "copy_failed"}

        deadline = time.monotonic() + timeout_s
        reports: list = []
        failures: list = []
        for i in range(runs):
            if time.monotonic() >= deadline:
                failures.append({"run": i, "junk": JUNK_SEQUENCE[i % len(JUNK_SEQUENCE)],
                                 "status": STATUS_TIMEOUT, "detail": "overall timeout reached before this run started"})
                continue
            result = _run_one(i, freecad_binary, module_dir, work_copy, tmp, tolerance, deadline, check_validity)
            if result["ok"]:
                reports.append(result["report"])
            else:
                failures.append(result["failure"])

        if not reports:
            return {"error": "every run failed", "error_kind": "all_runs_failed",
                    "runs_requested": runs, "failures": failures}
        report = analysis.aggregate_runs(reports, runs)
        report["file"] = source
        report["runs_failed"] = len(failures)
        report["failures"] = failures
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# Runs the model lint: validate inputs, copy the file to a temp dir, run
# lint_worker.py under FreeCADCmd once, return its report. Pure Python (no
# FreeCAD import), so it is unit-tested with fake FreeCADCmd binaries. Process
# handling, path allowlist and failure vocabulary are regression_runner's: one
# implementation, two tools.

import os
import shutil
import tempfile
import time

import regression_runner as rr

DEFAULT_TIMEOUT_S = rr.DEFAULT_TIMEOUT_S


def run_lint(file_path: str, freecad_binary: str, module_dir: str,
             bop_check: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Lint a saved .FCStd. Returns the report, or {"error", "error_kind", ...}
    when it could not run. `timeout_s` bounds the whole run."""
    err = rr.validate_file_and_binary(file_path, freecad_binary, module_dir,
                                      ("lint_worker.py", "lint_analysis.py"))
    if not err and not isinstance(bop_check, bool):
        err = f"bop_check must be a boolean, got {bop_check!r}"
    if not err:
        err = rr.validate_timeout(timeout_s)
    if err:
        return {"error": err, "error_kind": "invalid_argument"}

    source = os.path.realpath(os.path.expanduser(file_path))
    tmp = tempfile.mkdtemp(prefix="fcmcp_lint_")
    try:
        # The only copy anything opens; the user's file is never touched.
        work_copy = os.path.join(tmp, os.path.basename(source))
        try:
            shutil.copy2(source, work_copy)
        except OSError as e:
            return {"error": f"could not copy {source}: {e}", "error_kind": "copy_failed"}

        out_path = os.path.join(tmp, "lint.json")
        env = dict(os.environ)
        env.update({
            "FREECAD_MCP_LINT_IN": work_copy,
            "FREECAD_MCP_LINT_OUT": out_path,
            "FREECAD_MCP_LINT_MODDIR": module_dir,
            "FREECAD_MCP_LINT_BOP": "1" if bop_check else "0",
        })
        result = rr.run_worker_process(freecad_binary, os.path.join(module_dir, "lint_worker.py"), env,
                                       out_path, os.path.join(tmp, "lint.log"),
                                       time.monotonic() + timeout_s, {})
        if not result["ok"]:
            return {"error": "the lint worker failed", "error_kind": "worker_failed",
                    "failure": result["failure"]}
        report = result["report"]
        report["file"] = source
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

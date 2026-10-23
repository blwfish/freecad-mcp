"""Integration tests for the model_lint tool against a REAL FreeCADCmd.

The bridge runs AICopilot/lint_worker.py in a separate headless FreeCAD on a temp
copy of the file (no recompute, no live instance needed). Two documents are
built with FreeCADCmd: a clean one (a box) and one with an empty, unsupported
Flat-face sketch -- the exact state of MillingMachine's Sketch052. The lint must
report nothing for the first, report that sketch for the second, and leave the
file on disk untouched in both cases.

Run with: python3 -m pytest tests/integration/test_model_lint.py -v
"""

import hashlib
import os
import subprocess
import sys
import textwrap

import pytest

# The bridge imports `mcp`, which the plain integration job doesn't install; this
# file runs in the instance-lifecycle job (uv sync from the lockfile).
pytest.importorskip("mcp", reason="bridge needs the mcp package; run via the instance-lifecycle job / `uv run`")
pytest.importorskip("mcp_agent_notes", reason="bridge imports mcp_agent_notes first; run via the instance-lifecycle job / `uv run`")

from . import conftest as _conftest  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import freecad_mcp_server  # noqa: E402

BINARY = _conftest._find_freecadcmd()
pytestmark = pytest.mark.skipif(BINARY is None, reason="no FreeCADCmd binary found")

_BUILD = textwrap.dedent("""
    import os, FreeCAD
    doc = FreeCAD.newDocument("L")
    box = doc.addObject("Part::Box", "Box"); box.Length = box.Width = box.Height = 10
    doc.recompute()
    if os.environ.get("IT_ORPHAN") == "1":
        sk = doc.addObject("Sketcher::SketchObject", "Orphan")
        sk.MapMode = "FlatFace"   # a mode that needs a face, with no support: the lost-support state
    doc.saveAs(os.environ["IT_OUT_FCSTD"])
""")


def _build(tmp_path, orphan):
    out = tmp_path / ("orphan.FCStd" if orphan else "clean.FCStd")
    script = tmp_path / "build_model.py"
    script.write_text(_BUILD)
    env = dict(os.environ, IT_OUT_FCSTD=str(out), IT_ORPHAN="1" if orphan else "0")
    code = f"exec(compile(open({str(script)!r}).read(), {str(script)!r}, 'exec'))"
    proc = subprocess.run([BINARY, "-c", code], env=env, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0 and out.exists(), (
        f"could not build the test document (exit {proc.returncode}): {(proc.stdout + proc.stderr)[-800:]}")
    return out


def _lint(path, **kw):
    args = {"file_path": str(path), "freecad_binary": BINARY, "timeout_s": 300}
    args.update(kw)
    return freecad_mcp_server._run_model_lint(args)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestModelLintLive:

    def test_clean_document_reports_nothing(self, tmp_path):
        model = _build(tmp_path, orphan=False)
        before, mtime = _sha(model), model.stat().st_mtime_ns
        r = _lint(model)
        assert "error" not in r, r
        assert r["root_findings"] == 0 and r["roots"] == [] and r["objects_checked"] == 1
        assert r["findings_by_severity"] == {"error": 0, "warning": 0, "info": 0}
        assert r["freecad_version"] and r["saved_by_program_version"] and r["file"] == os.path.realpath(model)
        assert _sha(model) == before and model.stat().st_mtime_ns == mtime

    def test_empty_unsupported_flat_face_sketch_is_reported_twice_on_one_object(self, tmp_path):
        r = _lint(_build(tmp_path, orphan=True))
        assert "error" not in r, r
        roots = {x["name"]: x for x in r["roots"]}
        assert set(roots) == {"Orphan"}
        assert set(roots["Orphan"]["findings"]) == {"lost_support", "orphan_empty_sketch"}
        assert roots["Orphan"]["severity"] == {"lost_support": "warning", "orphan_empty_sketch": "info"}
        assert r["findings_by_severity"] == {"error": 0, "warning": 1, "info": 1}

    def test_the_file_is_not_modified_by_linting_a_document_with_findings(self, tmp_path):
        model = _build(tmp_path, orphan=True)
        before, mtime = _sha(model), model.stat().st_mtime_ns
        _lint(model)
        assert _sha(model) == before and model.stat().st_mtime_ns == mtime

    def test_bop_option_runs_and_a_clean_box_has_no_self_intersections(self, tmp_path):
        r = _lint(_build(tmp_path, orphan=False), bop_check=True)
        assert "error" not in r and r["bop_check"] is True and r["root_findings"] == 0

    def test_a_binary_that_dies_comes_back_as_a_typed_failure(self, tmp_path):
        bad = tmp_path / "crashing-freecadcmd"
        bad.write_text(f"#!{sys.executable}\nimport os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        bad.chmod(0o755)
        r = _lint(_build(tmp_path, orphan=False), freecad_binary=str(bad))
        assert r["error_kind"] == "worker_failed" and r["failure"]["signal"] == "SIGKILL"

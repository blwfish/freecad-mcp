"""Integration tests for the regression_check tool against a REAL FreeCADCmd.

The bridge runs AICopilot/regression_worker.py in separate headless FreeCAD
processes on a temp copy of the file (the tool needs no live instance, so these
don't use the session's spawned server). Two behaviours matter:

  * a document that recomputes exactly as saved reports nothing, and the file
    on disk is untouched;
  * a document whose STORED shape was altered after saving (simulating "the
    saving FreeCAD version produced something the current one doesn't") reports
    that feature as the root cause -- while the features computed from the
    original still match.

Run with: python3 -m pytest tests/integration/test_regression_check.py -v
"""

import hashlib
import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
import zipfile

import pytest

# The bridge imports `mcp`, which the plain integration job doesn't install; this
# file runs in the instance-lifecycle job (uv sync from the lockfile) -- see
# test_instance_lifecycle.py, which skips the same way.
pytest.importorskip("mcp", reason="bridge needs the mcp package; run via the instance-lifecycle job / `uv run`")
pytest.importorskip("mcp_agent_notes", reason="bridge imports mcp_agent_notes first; run via the instance-lifecycle job / `uv run`")

from . import conftest as _conftest  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import freecad_mcp_server  # noqa: E402

BINARY = _conftest._find_freecadcmd()
pytestmark = pytest.mark.skipif(BINARY is None, reason="no FreeCADCmd binary found")

_BUILD = textwrap.dedent("""
    import os, FreeCAD, Part
    doc = FreeCAD.newDocument("M")
    box = doc.addObject("Part::Box", "Box"); box.Length = box.Width = box.Height = 10
    cyl = doc.addObject("Part::Cylinder", "Cyl"); cyl.Radius = 2; cyl.Height = 30
    cut = doc.addObject("Part::Cut", "Cut"); cut.Base = box; cut.Tool = cyl
    doc.recompute()
    doc.saveAs(os.environ["IT_OUT_FCSTD"])
    Part.makeBox(20, 20, 20).exportBrep(os.environ["IT_OUT_BIG_BREP"])
""")


@pytest.fixture
def saved_model(tmp_path):
    out = tmp_path / "model.FCStd"
    big = tmp_path / "big.brep"
    script = tmp_path / "build_model.py"
    script.write_text(_BUILD)
    env = dict(os.environ, IT_OUT_FCSTD=str(out), IT_OUT_BIG_BREP=str(big))
    # Same invocation form the runner uses: `-c exec(...)`, accepted by every build.
    code = f"exec(compile(open({str(script)!r}).read(), {str(script)!r}, 'exec'))"
    proc = subprocess.run([BINARY, "-c", code], env=env, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0 and out.exists() and big.exists(), (
        f"could not build the test document with FreeCADCmd (exit {proc.returncode}): "
        f"{(proc.stdout + proc.stderr)[-800:]}")
    return out, big


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(path, **kw):
    args = {"file_path": str(path), "freecad_binary": BINARY, "timeout_s": 300}
    args.update(kw)
    return freecad_mcp_server._run_regression_check(args)


def _stored_shape_entry(zf, object_name):
    """Zip entry holding object_name's Shape, found via Document.xml (the entry
    name differs between FreeCAD versions: 'Box.Shape.brp' vs 'PartShape.brp')."""
    root = ET.fromstring(zf.read("Document.xml"))
    for obj in root.iter("Object"):
        if obj.get("name") == object_name:
            for prop in obj.iter("Property"):
                if prop.get("name") == "Shape":
                    part = prop.find("Part")
                    if part is not None and part.get("file"):
                        return part.get("file")
    pytest.fail(f"no stored Shape entry for {object_name} in Document.xml")


def _replace_entry(src, dst, entry, new_bytes):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, new_bytes if item.filename == entry else zin.read(item.filename))


class TestRegressionCheckLive:

    def test_document_that_recomputes_as_saved_reports_nothing(self, saved_model):
        model, _ = saved_model
        before, mtime = _sha(model), model.stat().st_mtime_ns
        r = _check(model, runs=2)
        assert "error" not in r, r
        assert r["regression_count"] == 0 and r["unstable_count"] == 0
        assert r["runs_used"] == 2 and r["runs_failed"] == 0
        assert r["features_compared"] == 3  # Box, Cyl, Cut
        assert r["freecad_version"] and r["file"] == os.path.realpath(model)
        assert _sha(model) == before and model.stat().st_mtime_ns == mtime

    def test_altered_stored_shape_is_reported_as_the_root_cause(self, saved_model, tmp_path):
        model, big = saved_model
        doctored = tmp_path / "doctored.FCStd"
        with zipfile.ZipFile(model) as zf:
            entry = _stored_shape_entry(zf, "Box")
        _replace_entry(model, doctored, entry, big.read_bytes())

        r = _check(doctored, runs=2)
        assert "error" not in r, r
        roots = {x["name"]: x for x in r["regressions"]["roots"]}
        assert set(roots) == {"Box"}, r["regressions"]
        # A 20^3 stored box vs the recomputed 10^3 one: volume and extent differ.
        assert {"volume_changed", "extent_changed"} <= set(roots["Box"]["new"])
        # Cut and Cyl were stored from the ORIGINAL geometry and recompute identically.
        assert r["unstable_count"] == 0

    def test_single_run_is_accepted_and_cannot_report_instability(self, saved_model):
        model, _ = saved_model
        r = _check(model, runs=1)
        assert "error" not in r and r["runs_used"] == 1 and r["unstable_count"] == 0

    def test_a_binary_that_dies_comes_back_as_a_typed_failure(self, saved_model, tmp_path):
        """Real subprocess plumbing with a binary that is killed immediately (SIGKILL: a real
        segfault would raise a macOS CrashReporter notification)."""
        bad = tmp_path / "crashing-freecadcmd"
        bad.write_text(f"#!{sys.executable}\nimport os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        bad.chmod(0o755)
        model, _ = saved_model
        r = _check(model, runs=1, freecad_binary=str(bad))
        assert r["error_kind"] == "all_runs_failed" and r["failures"][0]["signal"] == "SIGKILL"

"""Unit tests for AICopilot/regression_worker.py with a fake FreeCAD.

The worker is the one piece that needs a real FreeCAD for its full behaviour
(tests/integration/test_regression_check.py covers that); these pin the logic
around it: what counts as a snapshot target, dependency ordering, defensive
snapshotting, and -- the property everything else depends on -- that STORED
shapes are captured before the recompute that replaces them.

Run with: python3 -m pytest tests/unit/test_regression_worker.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import regression_analysis as analysis
import regression_worker as w


def fake_shape(valid=True, volume=10.0, faces=6, solids=1, shells=1, null=False, bbox_valid=True):
    s = MagicMock()
    s.isNull.return_value = null
    s.isValid.return_value = valid
    s.Volume = volume
    s.Faces = [object()] * faces
    s.Solids = [object()] * solids
    s.Shells = [object()] * shells
    bb = MagicMock()
    bb.isValid.return_value = bbox_valid
    bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax = 0, 0, 0, 1, 2, 3
    type(bb).DiagonalLength = property(lambda self: (_ for _ in ()).throw(AssertionError("DiagonalLength must not be read")))
    s.BoundBox = bb
    return s


def fake_obj(name, shape=None, part_feature=True, out=(), state=("Up-to-date",), status="Valid"):
    o = MagicMock()
    o.Name = name
    o.Shape = shape if shape is not None else fake_shape()
    o.isDerivedFrom = lambda cls: part_feature if cls == "Part::Feature" else False
    o.OutList = list(out)
    o.State = list(state)
    o.getStatusString.return_value = status
    return o


class TestTakeSnapshot:

    def test_ordinary_shape(self):
        snap = w.take_snapshot(fake_obj("A", fake_shape(volume=3.5, faces=25, solids=1, shells=2)))
        assert snap == {"null": False, "valid": True, "solids": 1, "shells": 2, "faces": 25, "volume": 3.5,
                        "bbox": [0, 0, 0, 1, 2, 3], "recompute_error": None}

    def test_null_shape_reads_nothing_else(self):
        s = fake_shape(null=True)
        snap = w.take_snapshot(fake_obj("A", s))
        assert snap["null"] is True and snap["bbox"] is None
        s.isValid.assert_not_called()

    def test_invalid_bounding_box_is_none_and_diagonal_never_read(self):
        """An empty shape's BoundBox.DiagonalLength raises FloatingPointError;
        fake_shape makes reading it an AssertionError."""
        snap = w.take_snapshot(fake_obj("A", fake_shape(bbox_valid=False)))
        assert snap["bbox"] is None

    def test_invalid_shape_is_recorded_not_raised(self):
        assert w.take_snapshot(fake_obj("A", fake_shape(valid=False)))["valid"] is False


class TestSnapshotTarget:

    def test_part_feature_is_a_target(self):
        assert w._is_snapshot_target(fake_obj("Pad")) is True

    def test_origin_like_object_with_a_shape_is_not(self):
        assert w._is_snapshot_target(fake_obj("Y_Axis", part_feature=False)) is False

    def test_object_without_a_shape_is_not(self):
        o = fake_obj("Sheet", part_feature=False)
        del o.Shape
        assert w._is_snapshot_target(o) is False
        assert w._has_shape_attribute(o) is False

    def test_target_detection_never_reads_the_shape(self):
        """A getter that raises must not escape target detection."""
        o = fake_obj("Weird")
        type(o).Shape = property(lambda self: (_ for _ in ()).throw(RuntimeError("deleted object")))
        assert w._is_snapshot_target(o) is True

    def test_shape_getter_that_raises_counts_as_no_shape_attribute(self):
        o = fake_obj("Weird", part_feature=False)
        type(o).Shape = property(lambda self: (_ for _ in ()).throw(RuntimeError("deleted object")))
        assert w._has_shape_attribute(o) is False

    def test_shapeless_non_feature_is_not_counted_as_skipped(self):
        sheet = fake_obj("Sheet", part_feature=False)
        del sheet.Shape
        rep = run_worker(make_doc([fake_obj("Pad"), sheet], after={}))
        assert rep["skipped_non_feature"] == 0


class TestTopoOrder:

    def test_inputs_come_before_dependents(self):
        a = fake_obj("A")
        b = fake_obj("B", out=[a])
        c = fake_obj("C", out=[b, a])
        doc = MagicMock()
        doc.Objects = [c, b, a]  # deliberately reversed
        assert w._topo_order(doc, {"A", "B", "C"}) == ["A", "B", "C"]

    def test_names_outside_the_set_are_walked_through_but_omitted(self):
        a = fake_obj("A")
        mid = fake_obj("Mid", out=[a])
        top = fake_obj("Top", out=[mid])
        doc = MagicMock()
        doc.Objects = [top]
        assert w._topo_order(doc, {"A", "Top"}) == ["A", "Top"]

    def test_cycle_terminates(self):
        a = fake_obj("A")
        b = fake_obj("B", out=[a])
        a.OutList = [b]
        doc = MagicMock()
        doc.Objects = [a, b]
        assert set(w._topo_order(doc, {"A", "B"})) == {"A", "B"}


class FakeFreeCAD:
    """Stands in for the `FreeCAD` module. openDocument returns a doc whose
    recompute() swaps each object's shape to its 'after' shape, so a worker
    that snapshots AFTER recomputing would see no difference."""

    def __init__(self, doc):
        self._doc = doc

    @staticmethod
    def Version():
        return ["26", "3", "0", "49042 (Git)"]

    def openDocument(self, path):
        self.opened = path
        return self._doc


def make_doc(objs, after):
    doc = MagicMock()
    doc.Objects = objs
    doc.ProgramVersion = "1.0R39109 (Git)"
    doc.getObject = lambda n: next(o for o in objs if o.Name == n)
    calls = []
    for o in objs:
        o.touch = MagicMock(side_effect=lambda n=o.Name: calls.append(("touch", n)))

    def recompute():
        calls.append(("recompute", None))
        for o in objs:
            if o.Name in after:
                o.Shape = after[o.Name]
    doc.recompute = recompute
    doc.calls = calls
    return doc


def run_worker(doc, tolerance=0.001, junk=0):
    fake = FakeFreeCAD(doc)
    with patch.dict(sys.modules, {"FreeCAD": fake, "Part": MagicMock(OCC_VERSION="8.0.1")}):
        return w.run("/tmp/x.FCStd", tolerance, junk, analysis)


class TestRun:

    def test_stored_shape_is_captured_before_recompute(self):
        pad = fake_obj("Pad", fake_shape(valid=True))
        doc = make_doc([pad], after={"Pad": fake_shape(valid=False)})
        rep = run_worker(doc)
        assert rep["findings"]["Pad"] == {"validity_lost": "valid as stored, Shape.isValid() == False after recompute"}

    def test_every_object_is_touched_before_the_single_recompute(self):
        pad, fil = fake_obj("Pad"), fake_obj("Fillet")
        doc = make_doc([pad, fil], after={})
        run_worker(doc)
        kinds = [c[0] for c in doc.calls]
        assert kinds.count("recompute") == 1 and kinds[-1] == "recompute"
        assert {n for k, n in doc.calls if k == "touch"} == {"Pad", "Fillet"}

    def test_unchanged_document_has_no_findings(self):
        doc = make_doc([fake_obj("Pad")], after={})
        assert run_worker(doc)["findings"] == {"Pad": {}}

    def test_object_flagged_invalid_by_freecad_gets_a_recompute_error(self):
        pad = fake_obj("Pad", state=("Invalid",), status="Resulting shape is invalid")
        doc = make_doc([pad], after={})
        assert run_worker(doc)["findings"]["Pad"]["recompute_error"] == "Resulting shape is invalid"

    def test_origin_features_are_skipped_and_counted(self):
        pad, axis = fake_obj("Pad"), fake_obj("Y_Axis", part_feature=False)
        doc = make_doc([pad, axis], after={})
        rep = run_worker(doc)
        assert rep["order"] == ["Pad"] and rep["skipped_non_feature"] == 1

    def test_unreadable_shape_is_counted_not_silently_dropped(self):
        broken = fake_obj("Broken")
        type(broken).Shape = property(lambda self: (_ for _ in ()).throw(RuntimeError("no shape")))
        pad = fake_obj("Pad")
        rep = run_worker(make_doc([pad, broken], after={}))
        assert "Broken" in rep["snapshot_errors"] and "RuntimeError" in rep["snapshot_errors"]["Broken"]
        assert rep["order"] == ["Pad"] and rep["features_compared"] == 1

    def test_inputs_are_restricted_to_compared_features(self):
        origin = fake_obj("Y_Axis", part_feature=False)
        pad = fake_obj("Pad", out=[origin])
        fil = fake_obj("Fillet", out=[pad, origin])
        rep = run_worker(make_doc([pad, fil, origin], after={}))
        assert rep["inputs"] == {"Pad": [], "Fillet": ["Pad"]}

    def test_metadata_is_recorded(self):
        rep = run_worker(make_doc([fake_obj("Pad")], after={}), tolerance=0.25, junk=0)
        assert rep["tolerance"] == 0.25 and rep["freecad_version"] == "26.3.0 49042 (Git)"
        assert rep["saved_by_program_version"] == "1.0R39109 (Git)" and rep["occt_version"] == "8.0.1"

    def test_junk_allocations_are_kept_alive_for_the_whole_run(self):
        before = len(w._JUNK_KEEPALIVE)
        part = MagicMock(OCC_VERSION="8.0.1")
        part.makeBox.side_effect = lambda *a: object()
        doc = make_doc([fake_obj("Pad")], after={})
        with patch.dict(sys.modules, {"FreeCAD": FakeFreeCAD(doc), "Part": part}):
            w.run("/tmp/x.FCStd", 0.001, 7, analysis)
        assert len(w._JUNK_KEEPALIVE) - before == 7
        del w._JUNK_KEEPALIVE[before:]

    def test_zero_junk_allocates_nothing(self):
        before = len(w._JUNK_KEEPALIVE)
        run_worker(make_doc([fake_obj("Pad")], after={}), junk=0)
        assert len(w._JUNK_KEEPALIVE) == before


class TestMain:

    def _env(self, tmp_path, **extra):
        env = {"FREECAD_MCP_REGRESSION_IN": "/tmp/x.FCStd", "FREECAD_MCP_REGRESSION_OUT": str(tmp_path / "out.json"),
               "FREECAD_MCP_REGRESSION_MODDIR": os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot')}
        env.update(extra)
        return env

    @pytest.mark.parametrize("missing", ["FREECAD_MCP_REGRESSION_IN", "FREECAD_MCP_REGRESSION_OUT",
                                         "FREECAD_MCP_REGRESSION_MODDIR"])
    def test_missing_env_is_exit_code_2_and_writes_nothing(self, tmp_path, missing):
        env = self._env(tmp_path)
        del env[missing]
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(missing, None)
            assert w.main() == 2
        assert not (tmp_path / "out.json").exists()

    def test_exception_in_run_becomes_a_typed_error_report(self, tmp_path):
        import json
        env = self._env(tmp_path)
        with patch.dict(os.environ, env), patch.object(w, "run", side_effect=RuntimeError("open failed")):
            assert w.main() == 0
        rep = json.loads((tmp_path / "out.json").read_text())
        assert rep["error"] == "RuntimeError: open failed"

    def test_unparseable_tolerance_and_junk_fall_back_to_defaults(self, tmp_path):
        import json
        env = self._env(tmp_path, FREECAD_MCP_REGRESSION_TOL="abc", FREECAD_MCP_REGRESSION_JUNK="x")
        seen = {}

        def fake_run(path, tol, junk, ana):
            seen.update(tol=tol, junk=junk)
            return {"format_version": 1, "ok": True}
        with patch.dict(os.environ, env), patch.object(w, "run", side_effect=fake_run):
            w.main()
        assert seen == {"tol": analysis.DEFAULT_TOLERANCE, "junk": 0}
        assert json.loads((tmp_path / "out.json").read_text())["ok"] is True

    def test_report_is_written_atomically_no_tmp_left_behind(self, tmp_path):
        env = self._env(tmp_path)
        with patch.dict(os.environ, env), patch.object(w, "run", return_value={"format_version": 1}):
            w.main()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]

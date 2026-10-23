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

LIMIT = analysis.MAX_FACES_FOR_VALIDITY_CHECK


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
        snap = w.take_snapshot(fake_obj("A", fake_shape(volume=3.5, faces=25, solids=1, shells=2)), LIMIT)
        assert snap == {"null": False, "valid": True, "solids": 1, "shells": 2, "faces": 25, "volume": 3.5,
                        "bbox": [0, 0, 0, 1, 2, 3], "recompute_error": None}

    def test_null_shape_reads_nothing_else(self):
        s = fake_shape(null=True)
        snap = w.take_snapshot(fake_obj("A", s), LIMIT)
        assert snap["null"] is True and snap["bbox"] is None
        s.isValid.assert_not_called()

    def test_invalid_bounding_box_is_none_and_diagonal_never_read(self):
        """An empty shape's BoundBox.DiagonalLength raises FloatingPointError;
        fake_shape makes reading it an AssertionError."""
        snap = w.take_snapshot(fake_obj("A", fake_shape(bbox_valid=False)), LIMIT)
        assert snap["bbox"] is None

    def test_invalid_shape_is_recorded_not_raised(self):
        assert w.take_snapshot(fake_obj("A", fake_shape(valid=False)), LIMIT)["valid"] is False


class TestValidityFaceLimit:
    """isValid() (BRepCheck_Analyzer) took 33 s on a 22,545-face array and never
    finished on a 143,400-face one, wedging whole checks. Shapes above the limit
    are not validity-checked; exactly at the limit still is."""

    def test_limit_is_five_thousand(self):
        assert LIMIT == 5000

    def test_exactly_at_the_limit_is_checked(self):
        s = fake_shape(faces=LIMIT)
        snap = w.take_snapshot(fake_obj("A", s), LIMIT)
        s.isValid.assert_called_once()
        assert snap["valid"] is True and snap["faces"] == LIMIT

    def test_just_below_the_limit_is_checked(self):
        s = fake_shape(faces=LIMIT - 1)
        assert w.take_snapshot(fake_obj("A", s), LIMIT)["valid"] is True
        s.isValid.assert_called_once()

    def test_just_above_the_limit_is_not_checked_and_never_calls_isvalid(self):
        s = fake_shape(faces=LIMIT + 1)
        snap = w.take_snapshot(fake_obj("A", s), LIMIT)
        s.isValid.assert_not_called()
        assert snap["valid"] is None and snap["faces"] == LIMIT + 1

    def test_other_measurements_are_still_taken_above_the_limit(self):
        snap = w.take_snapshot(fake_obj("A", fake_shape(faces=LIMIT + 1, volume=7.5, solids=3)), LIMIT)
        assert snap["volume"] == 7.5 and snap["solids"] == 3 and snap["bbox"] == [0, 0, 0, 1, 2, 3]

    def test_a_limit_of_zero_skips_every_nonempty_shape(self):
        s = fake_shape(faces=1)
        assert w.take_snapshot(fake_obj("A", s), 0)["valid"] is None

    def test_run_reports_unchecked_and_still_compares_volume(self):
        big_before = fake_shape(faces=LIMIT + 1, volume=100.0)
        big_after = fake_shape(faces=LIMIT + 1, volume=50.0)
        rep = run_worker(make_doc([fake_obj("Array", big_before)], after={"Array": big_after}))
        f = rep["findings"]["Array"]
        assert set(f) == {"validity_unchecked", "volume_changed"}
        assert f["validity_unchecked"]["limit"] == LIMIT and rep["validity_face_limit"] == LIMIT
        big_before.isValid.assert_not_called()
        big_after.isValid.assert_not_called()


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


class TestCheckValidityOff:
    """check_validity=False: nothing with faces is validity-checked (limit 0), but
    everything else is still compared. For files whose isValid() never returns."""

    def test_isvalid_is_never_called_on_a_shape_with_faces(self):
        before, after = fake_shape(faces=6, volume=10.0), fake_shape(faces=6, volume=5.0)
        rep = run_worker_validity(make_doc([fake_obj("Pad", before)], after={"Pad": after}), check_validity=False)
        before.isValid.assert_not_called()
        after.isValid.assert_not_called()
        assert set(rep["findings"]["Pad"]) == {"validity_unchecked", "volume_changed"}
        assert rep["validity_face_limit"] == 0

    def test_a_shape_without_faces_is_still_checked(self):
        """Sketch wires have no faces and isValid is cheap on them."""
        wire = fake_shape(faces=0, solids=0, shells=0)
        run_worker_validity(make_doc([fake_obj("Sketch", wire)], after={}), check_validity=False)
        wire.isValid.assert_called()

    def test_default_keeps_the_normal_limit(self):
        rep = run_worker_validity(make_doc([fake_obj("Pad")], after={}), check_validity=True)
        assert rep["validity_face_limit"] == LIMIT and rep["findings"] == {"Pad": {}}


def run_worker_validity(doc, check_validity):
    with patch.dict(sys.modules, {"FreeCAD": FakeFreeCAD(doc), "Part": MagicMock(OCC_VERSION="8.0.1")}):
        return w.run("/tmp/x.FCStd", 0.001, 0, analysis, check_validity)


class TestMain:

    def test_validity_env_zero_disables_the_check(self, tmp_path):
        seen = {}

        def fake_run(path, tol, junk, ana, check_validity):
            seen["cv"] = check_validity
            return {"format_version": 1}
        with patch.dict(os.environ, self._env(tmp_path, FREECAD_MCP_REGRESSION_VALIDITY="0")), \
                patch.object(w, "run", side_effect=fake_run):
            w.main()
        assert seen == {"cv": False}

    @pytest.mark.parametrize("value", ["1", "", "yes", "true"])
    def test_any_other_validity_env_leaves_it_on(self, tmp_path, value):
        seen = {}
        with patch.dict(os.environ, self._env(tmp_path, FREECAD_MCP_REGRESSION_VALIDITY=value)), \
                patch.object(w, "run", side_effect=lambda *a: seen.setdefault("cv", a[4]) or {"format_version": 1}):
            w.main()
        assert seen == {"cv": True}

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

        def fake_run(path, tol, junk, ana, check_validity):
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

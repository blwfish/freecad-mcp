"""Unit tests for AICopilot/lint_worker.py with a fake FreeCAD.

The worker's full behaviour needs a real FreeCAD (tests/integration/test_model_lint.py);
these pin the logic around it: fact extraction, the face-count guard on geometry
facts, BOP only on solids, micro-step detection, per-object error counting, and
the property the whole tool relies on -- the document is NEVER recomputed.

Run with: python3 -m pytest tests/unit/test_lint_worker.py -v
"""

import json
import os
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import lint_analysis as la
import lint_worker as w


class Plane:
    def __init__(self, axis=(0, 0, 1), pos=(0, 0, 0)):
        self.Axis = SimpleNamespace(x=axis[0], y=axis[1], z=axis[2])
        self.Position = SimpleNamespace(x=pos[0], y=pos[1], z=pos[2])


class Cylinder:
    pass


def face(area=1.0, surface=None, box=(0, 0, 0, 1, 1, 0)):
    f = MagicMock()
    f.Area = area
    f.Surface = surface if surface is not None else Plane()
    f.BoundBox = SimpleNamespace(XMin=box[0], YMin=box[1], ZMin=box[2], XMax=box[3], YMax=box[4], ZMax=box[5])
    return f


def edge(length, degenerated=False):
    return SimpleNamespace(Length=length, Degenerated=degenerated)


def shell(faces=3, area=100.0):
    s = MagicMock()
    s.Faces = [object()] * faces
    s.Area = area
    return s


def fake_shape(faces=None, solid_shells=None, edges=None, tol=1e-7, check_exc=None):
    """solid_shells: one list of shells per solid (default: one solid, one shell)."""
    s = MagicMock()
    s.isNull = MagicMock(return_value=False)
    s.Faces = faces if faces is not None else [face()]
    s.Edges = edges or []
    s.Solids = [SimpleNamespace(Shells=list(sh)) for sh in (solid_shells if solid_shells is not None else [[shell()]])]
    s.Shells = [x for sh in (solid_shells if solid_shells is not None else [[shell()]]) for x in sh]
    s.getTolerance = MagicMock(return_value=tol)
    if check_exc is not None:
        s.check = MagicMock(side_effect=check_exc)
    return s


class FakePart:
    Shape = object
    volumes: dict = {}

    class Solid:
        def __init__(self, sh):
            v = FakePart.volumes.get(id(sh), 1.0)
            if isinstance(v, Exception):
                raise v
            self.Volume = v


@pytest.fixture(autouse=True)
def fake_part():
    FakePart.volumes = {}
    with patch.dict(sys.modules, {"Part": FakePart}):
        yield


def fake_obj(name, type_id="PartDesign::Pad", shape=None, part_feature=True, out=(), in_list=(), **attrs):
    o = MagicMock(spec=[])
    o.Name, o.TypeId, o.OutList, o.InList = name, type_id, list(out), list(in_list)
    o.isDerivedFrom = lambda cls: part_feature if cls == "Part::Feature" else False
    o.Shape = shape if shape is not None else MagicMock(isNull=MagicMock(return_value=True))
    for k, v in attrs.items():
        setattr(o, k, v)
    return o


class TestAttachFacts:

    def test_attachment_support_with_subnames(self):
        base = fake_obj("Pocket008")
        o = fake_obj("Sketch018", MapMode="FlatFace", AttachmentSupport=[(base, ("Face10",))])
        assert w.attach_facts(o) == {"mode": "FlatFace", "support": [["Pocket008", ["Face10"]]]}

    def test_empty_support_is_reported_as_empty(self):
        assert w.attach_facts(fake_obj("S", MapMode="FlatFace", AttachmentSupport=[])) == {"mode": "FlatFace", "support": []}

    def test_older_support_property_is_used_when_there_is_no_attachment_support(self):
        base = fake_obj("P")
        o = fake_obj("S", MapMode="FlatFace", Support=[(base, ("Face1",))])
        assert w.attach_facts(o)["support"] == [["P", ["Face1"]]]

    def test_a_single_string_subname_is_accepted(self):
        base = fake_obj("P")
        assert w.attach_facts(fake_obj("S", MapMode="FlatFace", AttachmentSupport=[(base, "Face2")]))["support"] == [["P", ["Face2"]]]

    def test_unresolved_question_mark_names_pass_through(self):
        base = fake_obj("P")
        assert w.attach_facts(fake_obj("S", MapMode="FlatFace", AttachmentSupport=[(base, ("?Face28",))]))["support"] == [["P", ["?Face28"]]]

    def test_object_without_mapmode_is_not_attachable(self):
        assert w.attach_facts(fake_obj("X")) is None


class TestLegacyAndSketchFacts:

    def test_legacy_facts(self):
        assert w.legacy_facts(fake_obj("Pad007", Midplane=True, SideType="Two sides")) == {"midplane": True, "side_type": "Two sides"}

    def test_legacy_facts_need_both_properties(self):
        assert w.legacy_facts(fake_obj("P", Midplane=True)) is None
        assert w.legacy_facts(fake_obj("P", SideType="One side")) is None

    def test_sketch_facts_exclude_containers_from_dependents(self):
        body = fake_obj("Body", "PartDesign::Body")
        pad = fake_obj("Pad", "PartDesign::Pad")
        sk = fake_obj("Sketch", "Sketcher::SketchObject", GeometryCount=0, in_list=[body, pad], ExternalGeometry=[])
        assert w.sketch_facts(sk) == {"geometry_count": 0, "dependents": ["Pad"], "external_geometry_count": 0}

    def test_sketch_facts_count_external_geometry(self):
        sk = fake_obj("Sketch", "Sketcher::SketchObject", GeometryCount=0, ExternalGeometry=[("Box", "Edge1")] * 4)
        assert w.sketch_facts(sk)["external_geometry_count"] == 4

    def test_orphan_sketch_has_no_dependents_once_the_body_is_excluded(self):
        body = fake_obj("Body004", "PartDesign::Body")
        sk = fake_obj("Sketch052", "Sketcher::SketchObject", GeometryCount=0, in_list=[body], ExternalGeometry=[])
        assert w.sketch_facts(sk)["dependents"] == []

    def test_non_sketch_is_none(self):
        assert w.sketch_facts(fake_obj("Pad")) is None

    def test_a_getter_that_raises_counts_as_absent(self):
        o = fake_obj("X")
        type(o).MapMode = property(lambda self: (_ for _ in ()).throw(RuntimeError("deleted")))
        assert w._safe_has(o, "MapMode") is False


class TestNormalKey:

    def test_opposite_normals_with_the_same_plane_agree(self):
        a = w._normal_key(SimpleNamespace(x=0, y=1, z=0), 2.5)
        b = w._normal_key(SimpleNamespace(x=0, y=-1, z=0), -2.5)
        assert a == b == ((0.0, 1.0, 0.0), 2.5)

    def test_first_component_zero_falls_through_to_the_next(self):
        assert w._normal_key(SimpleNamespace(x=0, y=0, z=-1), 3.0) == ((0.0, 0.0, 1.0), -3.0)

    def test_distinct_offsets_stay_distinct(self):
        assert w._normal_key(SimpleNamespace(x=1, y=0, z=0), 1.0) != w._normal_key(SimpleNamespace(x=1, y=0, z=0), 1.002)


class TestMicroSteps:

    def _step(self, faces):
        return w.micro_steps(faces, la)

    def test_two_planes_two_microns_apart_on_touching_faces(self):
        steps = self._step([face(surface=Plane((0, 1, 0), (0, -3.915743, 0)), box=(0, 0, 0, 1, 1, 1)),
                            face(surface=Plane((0, 1, 0), (0, -3.917852, 0)), box=(0.5, 0, 0, 2, 1, 1))])
        assert len(steps) == 1 and abs(steps[0]["gap"] - 0.002109) < 1e-9

    def test_opposite_facing_normals_still_pair_up(self):
        steps = self._step([face(surface=Plane((0, 1, 0), (0, 1.0, 0))), face(surface=Plane((0, -1, 0), (0, 1.001, 0)))])
        assert len(steps) == 1

    def test_identical_planes_are_not_a_step(self):
        assert self._step([face(surface=Plane((0, 0, 1), (0, 0, 5))), face(surface=Plane((0, 0, 1), (0, 0, 5)))]) == []

    def test_planes_wider_than_the_candidate_bound_are_ignored(self):
        assert self._step([face(surface=Plane((0, 0, 1), (0, 0, 0))), face(surface=Plane((0, 0, 1), (0, 0, 0.5)))]) == []

    def test_far_apart_faces_do_not_count_even_if_their_planes_nearly_coincide(self):
        far = face(surface=Plane((0, 0, 1), (0, 0, 0.001)), box=(100, 100, 0, 101, 101, 0))
        assert self._step([face(surface=Plane((0, 0, 1), (0, 0, 0))), far]) == []

    def test_non_planar_faces_are_skipped(self):
        assert self._step([face(surface=Cylinder()), face(surface=Plane((0, 0, 1), (0, 0, 0.001)))]) == []

    def test_a_face_that_raises_is_skipped_not_fatal(self):
        bad = MagicMock()
        type(bad).Surface = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad face")))
        assert len(self._step([bad, face(surface=Plane((0, 0, 1), (0, 0, 0))), face(surface=Plane((0, 0, 1), (0, 0, 0.002)))])) == 1

    def test_the_number_of_reported_pairs_is_capped(self):
        faces = [face(surface=Plane((0, 0, 1), (0, 0, 0.0001 * i))) for i in range(30)]
        assert len(self._step(faces)) == 50


class TestShapeFacts:

    def test_above_the_face_limit_geometry_is_skipped_entirely(self):
        s = fake_shape(faces=[face()] * (la.MAX_FACES_FOR_GEOMETRY_FACTS + 1))
        assert w.shape_facts(s, la, False) == {"skipped": True, "faces": la.MAX_FACES_FOR_GEOMETRY_FACTS + 1}

    def test_exactly_at_the_face_limit_is_still_measured(self):
        s = fake_shape(faces=[face()] * la.MAX_FACES_FOR_GEOMETRY_FACTS)
        assert "extra_shells" in w.shape_facts(s, la, False)

    def test_extra_shell_volume_comes_from_a_solid_built_from_the_shell(self):
        sh = shell(faces=3, area=1.4e-6)
        FakePart.volumes[id(sh)] = -1.49e-15
        f = w.shape_facts(fake_shape(solid_shells=[[shell(faces=400, area=900.0), sh]]), la, False)
        assert f["extra_shells"] == [{"faces": 3, "area": 1.4e-6, "volume": -1.49e-15}]

    def test_a_volume_that_cannot_be_computed_is_none(self):
        sh = shell(area=1e-6)
        FakePart.volumes[id(sh)] = RuntimeError("not closed")
        f = w.shape_facts(fake_shape(solid_shells=[[shell(area=900.0), sh]]), la, False)
        assert f["extra_shells"][0]["volume"] is None

    def test_a_single_shell_solid_has_no_extra_shells(self):
        assert w.shape_facts(fake_shape(solid_shells=[[shell(area=1e-9)]]), la, False)["extra_shells"] == []

    def test_the_largest_shell_of_each_solid_is_the_outer_one_regardless_of_order(self):
        tiny, big, mid = shell(faces=3, area=1e-6), shell(faces=400, area=900.0), shell(faces=6, area=90.0)
        f = w.shape_facts(fake_shape(solid_shells=[[tiny, big, mid]]), la, False)
        assert sorted(x["area"] for x in f["extra_shells"]) == [1e-6, 90.0]

    def test_other_solids_outer_shells_are_not_extra(self):
        """The 65-shell 'Array' false positive: many solids, one shell each."""
        f = w.shape_facts(fake_shape(solid_shells=[[shell(area=500.0)] for _ in range(65)]), la, False)
        assert f["extra_shells"] == []

    def test_extra_shells_are_found_per_solid_in_a_multi_solid_shape(self):
        j1, j2 = shell(faces=3, area=1e-6), shell(faces=3, area=2e-6)
        f = w.shape_facts(fake_shape(solid_shells=[[shell(area=500.0), j1], [shell(area=300.0)], [shell(area=200.0), j2]]), la, False)
        assert sorted(x["area"] for x in f["extra_shells"]) == [1e-6, 2e-6]

    def test_a_shape_without_solids_has_no_extra_shells(self):
        assert w.shape_facts(fake_shape(solid_shells=[]), la, False)["extra_shells"] == []

    def test_degenerated_edges_are_not_zero_length_defects(self):
        """Sphere and cone poles are legitimately ~0 mm and flagged Degenerated."""
        edges = [edge(1.9e-15, degenerated=True), edge(0.0, degenerated=True), edge(1e-17), edge(3.0)]
        assert w.shape_facts(fake_shape(edges=edges), la, False)["small_edges"] == [1e-17]

    def test_small_face_and_edge_candidates_use_the_analysis_bounds(self):
        faces = [face(area=0.9e-3), face(area=1.1e-3), face(area=-1.13e-11), face(area=5.0)]
        edges = [edge(0.9e-6), edge(1.1e-6), edge(1e-17), edge(3.0)]
        f = w.shape_facts(fake_shape(faces=faces, edges=edges), la, False)
        assert sorted(f["small_faces"]) == [-1.13e-11, 0.9e-3]
        assert sorted(f["small_edges"]) == [1e-17, 0.9e-6]

    def test_tolerance_is_the_max_tolerance(self):
        f = w.shape_facts(fake_shape(tol=0.0128), la, False)
        assert f["max_tolerance"] == 0.0128

    def test_a_tolerance_that_cannot_be_read_is_none(self):
        s = fake_shape()
        s.getTolerance = MagicMock(side_effect=RuntimeError("x"))
        assert w.shape_facts(s, la, False)["max_tolerance"] is None

    def test_bop_is_off_by_default(self):
        s = fake_shape(check_exc=Exception("Error in Edge: BOPAlgo SelfIntersect"))
        assert "bop" not in w.shape_facts(s, la, False)
        s.check.assert_not_called()

    def test_bop_counts_self_intersections_from_the_exception(self):
        s = fake_shape(check_exc=Exception("found:\nError in Edge: BOPAlgo SelfIntersect\nError in Edge: BOPAlgo SelfIntersect\n"))
        assert w.shape_facts(s, la, True)["bop"] == {"self_intersect": 2}

    def test_bop_clean_shape(self):
        assert w.shape_facts(fake_shape(), la, True)["bop"] == {"self_intersect": 0}

    def test_bop_is_never_run_on_a_shape_without_solids(self):
        """A sketch wire can legitimately cross itself."""
        s = fake_shape(solid_shells=[], check_exc=Exception("BOPAlgo SelfIntersect"))
        assert "bop" not in w.shape_facts(s, la, True)
        s.check.assert_not_called()


class FakeDoc:
    def __init__(self, objs):
        self.Objects = objs
        self.recompute = MagicMock(name="recompute")

    def getObject(self, n):
        return next(o for o in self.Objects if o.Name == n)


def run_worker(objs, bop=False, version=None):
    doc = FakeDoc(objs)
    fc = SimpleNamespace(Version=lambda: ["26", "3", "0", "x"], openDocument=MagicMock(return_value=doc))
    with patch.dict(sys.modules, {"FreeCAD": fc}), patch.object(w, "_saved_by_version", return_value=version):
        return w.run("/tmp/x.FCStd", bop, la), doc, fc


class TestRun:

    def test_the_document_is_never_recomputed(self):
        rep, doc, _ = run_worker([fake_obj("Pad", shape=fake_shape())])
        doc.recompute.assert_not_called()
        assert rep["objects_checked"] == 1

    def test_findings_from_every_fact_kind(self):
        base = fake_obj("Pocket008", shape=fake_shape())
        sk = fake_obj("Sketch018", "Sketcher::SketchObject", MapMode="FlatFace", AttachmentSupport=[], GeometryCount=3,
                      in_list=[fake_obj("Pocket009")], ExternalGeometry=[])
        pad = fake_obj("Pad007", Midplane=True, SideType="Two sides", shape=fake_shape())
        rep, _, _ = run_worker([base, sk, pad])
        kinds = {r["name"]: set(r["findings"]) for r in rep["roots"]}
        assert kinds == {"Sketch018": {la.KIND_LOST_SUPPORT}, "Pad007": {la.KIND_LEGACY_FLAG_CONFLICT}}

    def test_junk_shell_is_a_root_at_the_first_feature_and_inherited_downstream(self):
        junk = shell(faces=3, area=1.4e-6)
        FakePart.volumes[id(junk)] = -1e-15
        main = shell(faces=400, area=900.0)
        FakePart.volumes[id(main)] = 500.0
        a = fake_obj("Pocket009", shape=fake_shape(solid_shells=[[main, junk]]))
        b = fake_obj("Fillet004", shape=fake_shape(solid_shells=[[main, junk]]), out=[a])
        rep, _, _ = run_worker([b, a])
        assert [r["name"] for r in rep["roots"]] == ["Pocket009"]
        assert rep["inherited"] == [{"name": "Fillet004", "kinds": [la.KIND_JUNK_SHELL]}]

    def test_an_object_that_raises_is_counted_not_dropped(self):
        bad = fake_obj("Broken", shape=fake_shape())
        type(bad).Shape = property(lambda self: (_ for _ in ()).throw(RuntimeError("no shape")))
        rep, _, _ = run_worker([fake_obj("Pad", shape=fake_shape()), bad])
        assert "Broken" in rep["object_errors"] and "RuntimeError" in rep["object_errors"]["Broken"]
        assert rep["objects_checked"] == 1

    def test_geometry_facts_skipped_on_huge_shapes_are_listed(self):
        huge = fake_obj("Array", shape=fake_shape(faces=[face()] * (la.MAX_FACES_FOR_GEOMETRY_FACTS + 1)))
        rep, _, _ = run_worker([huge])
        assert rep["geometry_facts_skipped"] == ["Array"] and rep["root_findings"] == 0

    def test_null_shapes_get_no_geometry_facts(self):
        rep, _, _ = run_worker([fake_obj("Empty")])
        assert rep["root_findings"] == 0 and rep["objects_checked"] == 1

    def test_non_part_features_still_get_attachment_facts(self):
        lcs = fake_obj("Placement", "Part::Part2DObjectPython", part_feature=False, MapMode="FlatFace", AttachmentSupport=[])
        rep, _, _ = run_worker([lcs])
        assert rep["roots"][0]["findings"].keys() == {la.KIND_LOST_SUPPORT}

    def test_metadata(self):
        rep, _, _ = run_worker([fake_obj("Pad", shape=fake_shape())], bop=True, version="1.0R39109 (Git)")
        assert rep["freecad_version"] == "26.3.0 x" and rep["saved_by_program_version"] == "1.0R39109 (Git)"
        assert rep["bop_check"] is True and rep["objects_in_document"] == 1

    def test_the_file_is_opened_exactly_once(self):
        _, _, fc = run_worker([fake_obj("Pad", shape=fake_shape())])
        fc.openDocument.assert_called_once_with("/tmp/x.FCStd")


class TestSavedByVersion:

    def test_reads_the_version_from_document_xml(self, tmp_path):
        p = tmp_path / "m.FCStd"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Document.xml", '<Document SchemaVersion="4" ProgramVersion="1.0R39109 (Git)" FileVersion="1"/>')
        assert w._saved_by_version(str(p)) == "1.0R39109 (Git)"

    def test_missing_version_attribute_is_none(self, tmp_path):
        p = tmp_path / "m.FCStd"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Document.xml", "<Document/>")
        assert w._saved_by_version(str(p)) is None

    def test_not_a_zip_is_none(self, tmp_path):
        p = tmp_path / "m.FCStd"
        p.write_bytes(b"nope")
        assert w._saved_by_version(str(p)) is None

    def test_zip_without_document_xml_is_none(self, tmp_path):
        p = tmp_path / "m.FCStd"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("other.txt", "x")
        assert w._saved_by_version(str(p)) is None


class TestMain:

    def _env(self, tmp_path, **extra):
        env = {"FREECAD_MCP_LINT_IN": "/tmp/x.FCStd", "FREECAD_MCP_LINT_OUT": str(tmp_path / "out.json"),
               "FREECAD_MCP_LINT_MODDIR": os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot')}
        env.update(extra)
        return env

    @pytest.mark.parametrize("missing", ["FREECAD_MCP_LINT_IN", "FREECAD_MCP_LINT_OUT", "FREECAD_MCP_LINT_MODDIR"])
    def test_missing_env_is_exit_code_2_and_writes_nothing(self, tmp_path, missing):
        env = self._env(tmp_path)
        del env[missing]
        with patch.dict(os.environ, env):
            os.environ.pop(missing, None)
            assert w.main() == 2
        assert not (tmp_path / "out.json").exists()

    def test_exception_in_run_becomes_a_typed_error_report(self, tmp_path):
        with patch.dict(os.environ, self._env(tmp_path)), patch.object(w, "run", side_effect=RuntimeError("open failed")):
            assert w.main() == 0
        assert json.loads((tmp_path / "out.json").read_text())["error"] == "RuntimeError: open failed"

    @pytest.mark.parametrize("value,expected", [("1", True), ("0", False), ("", False), ("yes", False)])
    def test_bop_env_is_only_on_for_exactly_one(self, tmp_path, value, expected):
        seen = {}
        with patch.dict(os.environ, self._env(tmp_path, FREECAD_MCP_LINT_BOP=value)), \
                patch.object(w, "run", side_effect=lambda p, bop, a: seen.setdefault("bop", bop) and {"format_version": 1} or {"format_version": 1}):
            w.main()
        assert seen["bop"] is expected

    def test_report_is_written_atomically(self, tmp_path):
        with patch.dict(os.environ, self._env(tmp_path)), patch.object(w, "run", return_value={"format_version": 1}):
            w.main()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]

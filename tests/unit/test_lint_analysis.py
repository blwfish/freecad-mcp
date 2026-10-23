"""Unit tests for AICopilot/lint_analysis.py (pure rules, no FreeCAD).

Every rule came from a real defect in the MillingMachine model, and every
threshold is a stated comparison direction pinned here at / just below / just
above the value (the < vs <= choice is contract). Ambiguous inputs -- an unknown
shell volume, an empty support list vs a missing one, a negative area -- each get
a test that pins what the code does with them.

Run with: python3 -m pytest tests/unit/test_lint_analysis.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import lint_analysis as la
from lint_analysis import attribute, boxes_touch, build_report, lint_object

BIG = {"faces": 400, "area": 1000.0, "volume": 500.0}


def shape(extra_shells=None, small_faces=None, small_edges=None, max_tolerance=1e-7, micro_steps=None, bop=None):
    return {"faces": 6, "extra_shells": extra_shells or [],
            "small_faces": small_faces or [], "small_edges": small_edges or [],
            "max_tolerance": max_tolerance, "micro_steps": micro_steps or [], "bop": bop}


def kinds(facts):
    return set(lint_object(facts))


class TestVocabulary:

    def test_every_kind_has_a_severity(self):
        assert set(la.SEVERITY) == set(la.ALL_KINDS)
        assert set(la.SEVERITY.values()) <= {la.SEVERITY_ERROR, la.SEVERITY_WARNING, la.SEVERITY_INFO}

    def test_local_and_geometry_kinds_partition_all_kinds(self):
        assert set(la.ALL_KINDS) == la.LOCAL_KINDS | la.GEOMETRY_KINDS
        assert not (la.LOCAL_KINDS & la.GEOMETRY_KINDS)

    def test_clean_object_has_no_findings(self):
        assert lint_object({"shape": shape()}) == {}
        assert lint_object({}) == {}

    def test_thresholds_are_the_documented_values(self):
        assert (la.JUNK_SHELL_MAX_AREA, la.JUNK_SHELL_MAX_ABS_VOLUME) == (1e-3, 1e-6)
        assert (la.SLIVER_FACE_MAX_AREA, la.ZERO_LENGTH_EDGE_MAX, la.TOLERANCE_WARN) == (1e-6, 1e-9, 1e-3)
        assert (la.MICRO_STEP_MIN_GAP, la.MICRO_STEP_MAX_GAP) == (1e-5, 5e-3)


class TestLostSupport:

    def test_flat_face_with_empty_support_is_flagged(self):
        assert lint_object({"attach": {"mode": "FlatFace", "support": []}}) == {
            la.KIND_LOST_SUPPORT: {"mode": "FlatFace"}}

    def test_flat_face_with_a_support_is_clean(self):
        assert lint_object({"attach": {"mode": "FlatFace", "support": [["Pad", ["Face3"]]]}}) == {}

    @pytest.mark.parametrize("mode", ["Deactivated", "", None])
    def test_modes_that_need_no_support_are_clean(self, mode):
        assert lint_object({"attach": {"mode": mode, "support": []}}) == {}

    @pytest.mark.parametrize("mode", ["ObjectXY", "ObjectXZ", "Translate", "TangentPlane", "FlatFace"])
    def test_any_other_mode_with_an_empty_support_is_flagged(self, mode):
        assert la.KIND_LOST_SUPPORT in lint_object({"attach": {"mode": mode, "support": []}})

    def test_missing_support_key_counts_as_empty(self):
        assert la.KIND_LOST_SUPPORT in lint_object({"attach": {"mode": "FlatFace"}})

    def test_support_with_an_empty_subname_list_is_not_lost(self):
        """An object-level link (no sub-element) is still a support."""
        assert lint_object({"attach": {"mode": "ObjectXY", "support": [["Origin_Plane", []]]}}) == {}

    def test_no_attach_facts_is_clean(self):
        assert lint_object({"attach": None}) == {}


class TestUnresolvedReference:

    def test_question_mark_subname_is_an_error(self):
        f = lint_object({"attach": {"mode": "FlatFace", "support": [["Pad007", ["?Face28"]]]}})
        assert f == {la.KIND_UNRESOLVED_REFERENCE: {"subnames": ["?Face28"], "count": 1}}
        assert la.SEVERITY[la.KIND_UNRESOLVED_REFERENCE] == la.SEVERITY_ERROR

    def test_resolved_subname_is_clean(self):
        assert lint_object({"attach": {"mode": "FlatFace", "support": [["Pad007", ["Face28"]]]}}) == {}

    def test_only_a_leading_question_mark_counts(self):
        assert lint_object({"attach": {"mode": "FlatFace", "support": [["P", ["Face?28", "Edge1"]]]}}) == {}

    def test_counts_every_unresolved_name_and_caps_the_examples(self):
        subs = [f"?Edge{i}" for i in range(9)]
        f = lint_object({"attach": {"mode": "FlatFace", "support": [["P", subs]]}})[la.KIND_UNRESOLVED_REFERENCE]
        assert f["count"] == 9 and len(f["subnames"]) == la.MAX_EXAMPLES

    def test_unresolved_names_across_several_support_entries(self):
        f = lint_object({"attach": {"mode": "FlatFace", "support": [["A", ["?F1"]], ["B", ["F2", "?F3"]]]}})
        assert f[la.KIND_UNRESOLVED_REFERENCE]["count"] == 2


class TestLegacyFlagConflict:
    """Pad007: Midplane=true together with SideType=Two sides flips to Symmetric on the next load."""

    @pytest.mark.parametrize("side", ["Two sides", "One side"])
    def test_midplane_with_a_non_symmetric_side_type_is_flagged(self, side):
        assert lint_object({"legacy": {"midplane": True, "side_type": side}}) == {
            la.KIND_LEGACY_FLAG_CONFLICT: {"midplane": True, "side_type": side}}

    def test_midplane_with_symmetric_is_consistent(self):
        assert lint_object({"legacy": {"midplane": True, "side_type": "Symmetric"}}) == {}

    @pytest.mark.parametrize("side", ["Two sides", "One side", "Symmetric"])
    def test_midplane_false_is_clean(self, side):
        assert lint_object({"legacy": {"midplane": False, "side_type": side}}) == {}

    def test_object_without_a_side_type_is_clean(self):
        assert lint_object({"legacy": {"midplane": True, "side_type": None}}) == {}


class TestOrphanEmptySketch:

    def test_empty_unused_sketch_is_flagged_as_info(self):
        assert lint_object({"sketch": {"geometry_count": 0, "dependents": []}}) == {la.KIND_ORPHAN_EMPTY_SKETCH: {}}
        assert la.SEVERITY[la.KIND_ORPHAN_EMPTY_SKETCH] == la.SEVERITY_INFO

    def test_empty_but_used_sketch_is_clean(self):
        assert lint_object({"sketch": {"geometry_count": 0, "dependents": ["Pad"]}}) == {}

    def test_unused_but_not_empty_sketch_is_clean(self):
        assert lint_object({"sketch": {"geometry_count": 1, "dependents": []}}) == {}

    def test_missing_dependents_key_counts_as_unused(self):
        assert la.KIND_ORPHAN_EMPTY_SKETCH in lint_object({"sketch": {"geometry_count": 0}})


class TestJunkShellBoundaries:
    """An extra shell is junk when area < 1e-3 AND |volume| < 1e-6 (both strict)."""

    def _junk(self, area, volume):
        return lint_object({"shape": shape(extra_shells=[{"faces": 3, "area": area, "volume": volume}])})

    def test_the_real_millingmachine_junk_is_flagged(self):
        f = self._junk(1.4255e-06, -1.49e-15)
        assert la.KIND_JUNK_SHELL in f and la.KIND_INTERNAL_VOID not in f
        assert f[la.KIND_JUNK_SHELL]["count"] == 1

    def test_area_just_below_the_limit_is_junk(self):
        assert la.KIND_JUNK_SHELL in self._junk(9.99e-4, 0.0)

    def test_area_exactly_at_the_limit_is_not_junk(self):
        f = self._junk(1e-3, 0.0)
        assert la.KIND_JUNK_SHELL not in f and la.KIND_INTERNAL_VOID in f

    def test_area_just_above_the_limit_is_not_junk(self):
        assert la.KIND_JUNK_SHELL not in self._junk(1.01e-3, 0.0)

    def test_volume_just_below_the_limit_is_junk(self):
        assert la.KIND_JUNK_SHELL in self._junk(1e-5, 9.9e-7)

    def test_volume_exactly_at_the_limit_is_not_junk(self):
        assert la.KIND_JUNK_SHELL not in self._junk(1e-5, 1e-6)

    def test_negative_volume_uses_its_magnitude(self):
        assert la.KIND_JUNK_SHELL in self._junk(1e-5, -9.9e-7)
        assert la.KIND_JUNK_SHELL not in self._junk(1e-5, -1.1e-6)

    def test_unknown_volume_is_never_junk(self):
        f = self._junk(1e-9, None)
        assert la.KIND_JUNK_SHELL not in f and f[la.KIND_INTERNAL_VOID]["total_abs_volume"] == 0

    def test_a_real_cavity_is_an_info_internal_void(self):
        f = self._junk(89.5, 55.9)
        assert f == {la.KIND_INTERNAL_VOID: {"count": 1, "total_abs_volume": 55.9}}
        assert la.SEVERITY[la.KIND_INTERNAL_VOID] == la.SEVERITY_INFO

    def test_no_extra_shells_means_no_finding(self):
        assert lint_object({"shape": shape(extra_shells=[])}) == {}
        assert lint_object({"shape": {"faces": 6}}) == {}

    def test_counts_every_junk_shell_and_caps_the_examples(self):
        extra = [{"faces": 3, "area": 1e-6, "volume": 0.0}] * 8
        f = lint_object({"shape": shape(extra_shells=extra)})[la.KIND_JUNK_SHELL]
        assert f["count"] == 8 and len(f["shells"]) == la.MAX_EXAMPLES

    def test_junk_and_a_real_void_together(self):
        f = lint_object({"shape": shape(extra_shells=[{"faces": 3, "area": 1e-6, "volume": 0.0},
                                                      {"faces": 6, "area": 90.0, "volume": 56.0}])})
        assert f[la.KIND_JUNK_SHELL]["count"] == 1 and f[la.KIND_INTERNAL_VOID]["count"] == 1


class TestSliverFaceBoundaries:
    """|area| < 1e-6, strictly; negative areas count."""

    def _f(self, areas):
        return lint_object({"shape": shape(small_faces=areas)}).get(la.KIND_SLIVER_FACE)

    def test_just_below_the_limit_is_a_sliver(self):
        assert self._f([9.99e-7])["count"] == 1

    def test_exactly_at_the_limit_is_not(self):
        assert self._f([1e-6]) is None

    def test_just_above_is_not(self):
        assert self._f([1.01e-6]) is None

    def test_negative_area_is_a_sliver_and_counted(self):
        f = self._f([-1.13e-11, 7.1e-7])
        assert f["count"] == 2 and f["negative_area_count"] == 1 and f["smallest_abs_area"] == 1.13e-11

    def test_negative_area_exactly_at_minus_the_limit_is_not(self):
        assert self._f([-1e-6]) is None

    def test_zero_area_is_a_sliver(self):
        assert self._f([0.0])["count"] == 1

    def test_ordinary_small_candidates_are_ignored(self):
        assert self._f([5e-4, 2e-5]) is None


class TestZeroLengthEdgeBoundaries:

    def _f(self, lengths):
        return lint_object({"shape": shape(small_edges=lengths)}).get(la.KIND_ZERO_LENGTH_EDGE)

    def test_just_below_the_limit(self):
        assert self._f([9.9e-10])["count"] == 1

    def test_exactly_at_the_limit_is_not(self):
        assert self._f([1e-9]) is None

    def test_exactly_zero_is_flagged_with_shortest_zero(self):
        assert self._f([0.0, 5e-10])["shortest"] == 0.0

    def test_the_real_values(self):
        assert self._f([1.12e-17, 5.77e-17])["count"] == 2


class TestInflatedToleranceBoundaries:

    def _f(self, tol):
        return lint_object({"shape": shape(max_tolerance=tol)}).get(la.KIND_INFLATED_TOLERANCE)

    def test_exactly_at_the_threshold_is_clean(self):
        assert self._f(1e-3) is None

    def test_just_above_is_flagged(self):
        assert self._f(1.0001e-3)["threshold"] == 1e-3

    def test_just_below_is_clean(self):
        assert self._f(9.99e-4) is None

    def test_the_real_fusion_tolerance(self):
        assert self._f(1.28e-2)["max_tolerance"] == 1.28e-2

    def test_unknown_tolerance_is_clean(self):
        assert self._f(None) is None


class TestMicroStepBoundaries:
    """A step is MIN < gap < MAX (1e-5 < gap < 5e-3), both strict."""

    def _f(self, gaps):
        return lint_object({"shape": shape(micro_steps=[{"gap": g, "a": 1, "b": 2} for g in gaps])}).get(la.KIND_MICRO_STEP)

    def test_the_real_2_1_micron_step(self):
        assert self._f([0.002109])["smallest_gap"] == 0.002109

    def test_exactly_the_minimum_is_noise(self):
        assert self._f([1e-5]) is None

    def test_just_above_the_minimum_is_a_step(self):
        assert self._f([1.001e-5]) is not None

    def test_sub_nanometre_gaps_are_floating_point_noise(self):
        """17 corpus findings sat at 0.1-1 nm."""
        assert self._f([1e-7, 5e-7, 9.9e-6]) is None

    def test_exactly_the_maximum_is_not(self):
        assert self._f([5e-3]) is None

    def test_just_below_the_maximum_is_a_step(self):
        assert self._f([4.99e-3]) is not None

    def test_a_deliberate_ten_micron_offset_is_not_flagged(self):
        assert self._f([0.01]) is None

    def test_zero_gap_is_not_a_step(self):
        assert self._f([0.0]) is None

    def test_a_wide_gap_is_not(self):
        assert self._f([0.05]) is None

    def test_counts_only_the_qualifying_ones_and_caps_examples(self):
        f = self._f([1e-7, 0.0005, 0.001, 0.002, 0.003, 0.004, 0.0045, 0.5])
        assert f["count"] == 6 and f["smallest_gap"] == 0.0005 and len(f["examples"]) == la.MAX_EXAMPLES


class TestBop:

    def test_self_intersections_are_an_error(self):
        f = lint_object({"shape": shape(bop={"self_intersect": 4})})
        assert f == {la.KIND_BOP_SELF_INTERSECT: {"count": 4}}
        assert la.SEVERITY[la.KIND_BOP_SELF_INTERSECT] == la.SEVERITY_ERROR

    def test_zero_is_clean_and_absent_is_clean(self):
        assert lint_object({"shape": shape(bop={"self_intersect": 0})}) == {}
        assert lint_object({"shape": shape(bop=None)}) == {}


class TestBoxesTouch:

    A = (0, 0, 0, 1, 1, 1)

    def test_overlapping(self):
        assert boxes_touch(self.A, (0.5, 0.5, 0.5, 2, 2, 2), 0.0)

    def test_touching_exactly_with_zero_margin(self):
        assert boxes_touch(self.A, (1, 0, 0, 2, 1, 1), 0.0)

    def test_gap_with_zero_margin(self):
        assert not boxes_touch(self.A, (1.001, 0, 0, 2, 1, 1), 0.0)

    def test_gap_exactly_twice_the_margin_touches(self):
        assert boxes_touch(self.A, (1.25, 0, 0, 2, 1, 1), 0.125)

    def test_gap_just_over_twice_the_margin_does_not(self):
        assert not boxes_touch(self.A, (1.26, 0, 0, 2, 1, 1), 0.125)

    def test_gap_exactly_twice_the_margin_touches_from_the_other_side_too(self):
        """The two inequalities are separate expressions; pin the boundary on each."""
        assert boxes_touch(self.A, (-1.25, 0, 0, -0.25, 1, 1), 0.125)
        assert not boxes_touch(self.A, (-1.26, 0, 0, -0.26, 1, 1), 0.125)

    def test_separated_on_one_axis_only_is_enough_to_not_touch(self):
        assert not boxes_touch(self.A, (0, 0, 5, 1, 1, 6), 0.1)

    def test_symmetric(self):
        b = (1.2, 0, 0, 2, 1, 1)
        assert boxes_touch(self.A, b, 0.1) == boxes_touch(b, self.A, 0.1)


class TestAttribution:

    def test_geometry_kind_in_an_input_is_inherited(self):
        f = {"A": {la.KIND_JUNK_SHELL: {}}, "B": {la.KIND_JUNK_SHELL: {}}}
        r = attribute(f, {"B": ["A"]}, ["A", "B"])
        assert [x["name"] for x in r["roots"]] == ["A"]
        assert r["inherited"] == [{"name": "B", "kinds": [la.KIND_JUNK_SHELL]}]

    def test_local_kinds_are_always_roots_even_when_an_input_has_them(self):
        f = {"A": {la.KIND_LOST_SUPPORT: {}}, "B": {la.KIND_LOST_SUPPORT: {}}}
        r = attribute(f, {"B": ["A"]}, ["A", "B"])
        assert [x["name"] for x in r["roots"]] == ["A", "B"] and r["inherited"] == []

    def test_a_new_geometry_kind_downstream_is_its_own_root(self):
        f = {"A": {la.KIND_JUNK_SHELL: {}}, "B": {la.KIND_JUNK_SHELL: {}, la.KIND_MICRO_STEP: {}}}
        r = attribute(f, {"B": ["A"]}, ["A", "B"])
        b = next(x for x in r["roots"] if x["name"] == "B")
        assert set(b["findings"]) == {la.KIND_MICRO_STEP} and b["also_inherited"] == [la.KIND_JUNK_SHELL]

    def test_objects_without_findings_are_skipped(self):
        assert attribute({"A": {}}, {}, ["A", "B"]) == {"roots": [], "inherited": []}

    def test_independent_branches_are_both_roots(self):
        f = {"A": {la.KIND_SLIVER_FACE: {}}, "B": {la.KIND_SLIVER_FACE: {}}}
        assert [x["name"] for x in attribute(f, {}, ["A", "B"])["roots"]] == ["A", "B"]

    def test_diamond_inherits_if_any_input_has_the_kind(self):
        f = {"S": {la.KIND_JUNK_SHELL: {}}, "L": {}, "R": {}, "T": {la.KIND_JUNK_SHELL: {}}}
        r = attribute(f, {"L": ["S"], "R": ["S"], "T": ["L", "R", "S"]}, ["S", "L", "R", "T"])
        assert [x["name"] for x in r["roots"]] == ["S"] and [x["name"] for x in r["inherited"]] == ["T"]


class TestBuildReport:

    def test_counts_roots_by_kind_and_severity_not_inherited(self):
        f = {"Pocket009": {la.KIND_JUNK_SHELL: {}, la.KIND_SLIVER_FACE: {}},
             "Fusion": {la.KIND_JUNK_SHELL: {}, la.KIND_SLIVER_FACE: {}},
             "Sketch018": {la.KIND_LOST_SUPPORT: {}},
             "Sketch052": {la.KIND_ORPHAN_EMPTY_SKETCH: {}, la.KIND_LOST_SUPPORT: {}}}
        order = ["Sketch018", "Sketch052", "Pocket009", "Fusion"]
        r = build_report(f, {"Fusion": ["Pocket009"]}, order, {"freecad_version": "26.3 test"})
        assert r["findings_by_kind"] == {la.KIND_JUNK_SHELL: 1, la.KIND_LOST_SUPPORT: 2,
                                         la.KIND_ORPHAN_EMPTY_SKETCH: 1, la.KIND_SLIVER_FACE: 1}
        assert r["findings_by_severity"] == {"error": 0, "warning": 4, "info": 1}
        assert r["root_findings"] == 5 and r["objects_checked"] == 4
        assert [x["name"] for x in r["inherited"]] == ["Fusion"]
        assert r["freecad_version"] == "26.3 test" and r["format_version"] == la.FORMAT_VERSION

    def test_each_root_carries_its_severities(self):
        r = build_report({"A": {la.KIND_UNRESOLVED_REFERENCE: {}, la.KIND_LOST_SUPPORT: {}}}, {}, ["A"], {})
        assert r["roots"][0]["severity"] == {la.KIND_UNRESOLVED_REFERENCE: "error", la.KIND_LOST_SUPPORT: "warning"}
        assert r["findings_by_severity"]["error"] == 1

    def test_empty_model(self):
        r = build_report({}, {}, [], {})
        assert r["root_findings"] == 0 and r["roots"] == [] and r["findings_by_severity"] == {"error": 0, "warning": 0, "info": 0}

    def test_meta_cannot_overwrite_computed_fields(self):
        r = build_report({"A": {la.KIND_LOST_SUPPORT: {}}}, {}, ["A"], {"root_findings": 99, "objects_checked": 99})
        assert r["root_findings"] == 1 and r["objects_checked"] == 1

"""Unit tests for AICopilot/regression_analysis.py (pure logic, no FreeCAD).

Covers the three things the regression check lives or dies on:
  * compare_snapshots -- every finding kind, and the strictly-greater-than
    boundary of the volume and bounding-box tolerances (at / just below /
    just above);
  * attribute_findings -- root cause vs inherited, same rule as find_root_cause;
  * aggregate_runs -- "present in every run" is a regression, "present in some
    runs" is `unstable`. This is the case that motivated the whole design: a
    fillet valid 12/12 in FreeCAD 1.0.0 but valid only 3-5/12 in 1.1+.

Run with: python3 -m pytest tests/unit/test_regression_analysis.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import regression_analysis as ra
from regression_analysis import (
    KIND_EXTENT_CHANGED,
    KIND_RECOMPUTE_ERROR,
    KIND_SHAPE_LOST,
    KIND_STORED_INVALID,
    KIND_TOPOLOGY_CHANGED,
    KIND_VALIDITY_GAINED,
    KIND_VALIDITY_LOST,
    KIND_VOLUME_CHANGED,
    aggregate_runs,
    attribute_findings,
    compare_snapshots,
)

TOL = 0.001


def snap(valid=True, solids=1, shells=1, faces=6, volume=1000.0, bbox=(0, 0, 0, 10, 10, 10),
         null=False, recompute_error=None):
    return {"null": null, "valid": valid, "solids": solids, "shells": shells, "faces": faces,
            "volume": volume, "bbox": list(bbox) if bbox is not None else None,
            "recompute_error": recompute_error}


class TestCompareValidity:

    def test_identical_snapshots_have_no_findings(self):
        assert compare_snapshots(snap(), snap(), TOL) == {}

    def test_valid_to_invalid_is_validity_lost(self):
        f = compare_snapshots(snap(valid=True), snap(valid=False), TOL)
        assert set(f) == {KIND_VALIDITY_LOST}

    def test_invalid_to_valid_is_gained_not_a_regression(self):
        f = compare_snapshots(snap(valid=False), snap(valid=True), TOL)
        assert set(f) == {KIND_VALIDITY_GAINED}
        assert KIND_VALIDITY_GAINED not in ra.REGRESSION_KINDS

    def test_invalid_both_sides_is_stored_invalid_and_not_a_regression(self):
        f = compare_snapshots(snap(valid=False), snap(valid=False), TOL)
        assert set(f) == {KIND_STORED_INVALID}
        assert KIND_STORED_INVALID not in ra.REGRESSION_KINDS


class TestCompareNullAndErrors:

    def test_shape_lost_returns_early(self):
        f = compare_snapshots(snap(), snap(null=True, valid=False, solids=0, faces=0, volume=0.0, bbox=None), TOL)
        assert set(f) == {KIND_SHAPE_LOST}

    def test_null_on_both_sides_is_not_comparable(self):
        n = snap(null=True, valid=False, solids=0, faces=0, volume=0.0, bbox=None)
        assert compare_snapshots(n, dict(n), TOL) == {}

    def test_null_stored_non_null_fresh_is_not_comparable(self):
        n = snap(null=True, valid=False, solids=0, faces=0, volume=0.0, bbox=None)
        assert compare_snapshots(n, snap(), TOL) == {}

    def test_recompute_error_is_reported_alongside_other_findings(self):
        f = compare_snapshots(snap(), snap(valid=False, recompute_error="Resulting shape is invalid"), TOL)
        assert f[KIND_RECOMPUTE_ERROR] == "Resulting shape is invalid"
        assert KIND_VALIDITY_LOST in f

    def test_recompute_error_with_null_shape_reports_both(self):
        f = compare_snapshots(snap(), snap(null=True, recompute_error="boom"), TOL)
        assert set(f) == {KIND_RECOMPUTE_ERROR, KIND_SHAPE_LOST}


class TestCompareTopology:

    def test_each_count_is_reported_with_both_values(self):
        f = compare_snapshots(snap(solids=1, shells=1, faces=25), snap(solids=2, shells=2, faces=28), TOL)
        assert f[KIND_TOPOLOGY_CHANGED] == {
            "solids": {"stored": 1, "recomputed": 2},
            "shells": {"stored": 1, "recomputed": 2},
            "faces": {"stored": 25, "recomputed": 28},
        }

    def test_only_the_differing_counts_are_listed(self):
        f = compare_snapshots(snap(faces=25), snap(faces=24), TOL)
        assert set(f[KIND_TOPOLOGY_CHANGED]) == {"faces"}


class TestVolumeToleranceBoundary:
    """Strictly greater-than: 1000 -> 999 is a relative delta of exactly 1/1000
    == the 0.001 tolerance, and must NOT count. The < vs <= choice is contract."""

    def test_exactly_at_tolerance_is_not_a_change(self):
        assert KIND_VOLUME_CHANGED not in compare_snapshots(snap(volume=1000.0), snap(volume=999.0), TOL)

    def test_just_below_tolerance_is_not_a_change(self):
        assert KIND_VOLUME_CHANGED not in compare_snapshots(snap(volume=1000.0), snap(volume=999.1), TOL)

    def test_just_above_tolerance_is_a_change(self):
        f = compare_snapshots(snap(volume=1000.0), snap(volume=998.9), TOL)
        assert KIND_VOLUME_CHANGED in f
        assert abs(f[KIND_VOLUME_CHANGED]["relative_delta"] - 0.0011) < 1e-9

    def test_direction_does_not_matter(self):
        assert KIND_VOLUME_CHANGED in compare_snapshots(snap(volume=998.9), snap(volume=1000.0), TOL)

    def test_both_zero_is_not_a_change(self):
        assert KIND_VOLUME_CHANGED not in compare_snapshots(snap(volume=0.0), snap(volume=0.0), TOL)

    def test_zero_to_nonzero_is_a_change(self):
        """A sketch/wire stores volume 0; anything non-zero afterwards is a
        100% relative delta, not a division by zero."""
        assert KIND_VOLUME_CHANGED in compare_snapshots(snap(volume=0.0), snap(volume=5.0), TOL)

    def test_the_real_fillet007_noise_is_under_the_default_tolerance(self):
        """Fillet007's valid and invalid builds differ by ~1.5e-4 relative
        (3.77392 vs 3.77335): below the default tolerance, which is exactly why
        stability is judged by validity flips, not by volume."""
        f = compare_snapshots(snap(volume=3.773920545), snap(valid=False, volume=3.773354), TOL)
        assert KIND_VOLUME_CHANGED not in f
        assert KIND_VALIDITY_LOST in f


class TestExtentToleranceBoundary:
    """Tolerance is relative to the stored bbox diagonal. Diagonal 8, tol 0.25
    -> limit exactly 2.0 (all exactly representable)."""

    BBOX = (0, 0, 0, 8, 0, 0)

    def _cmp(self, shift):
        fresh = (0, 0, 0, 8 + shift, 0, 0)
        return compare_snapshots(snap(bbox=self.BBOX), snap(bbox=fresh), 0.25)

    def test_exactly_at_limit_is_not_a_change(self):
        assert KIND_EXTENT_CHANGED not in self._cmp(2.0)

    def test_just_below_limit_is_not_a_change(self):
        assert KIND_EXTENT_CHANGED not in self._cmp(1.5)

    def test_just_above_limit_is_a_change(self):
        f = self._cmp(2.5)
        assert f[KIND_EXTENT_CHANGED]["max_coordinate_shift_mm"] == 2.5
        assert f[KIND_EXTENT_CHANGED]["stored_diagonal_mm"] == 8.0

    def test_degenerate_zero_diagonal_box_any_movement_counts(self):
        f = compare_snapshots(snap(bbox=(1, 1, 1, 1, 1, 1)), snap(bbox=(1, 1, 1.5, 1, 1, 1.5)), TOL)
        assert KIND_EXTENT_CHANGED in f

    def test_degenerate_zero_diagonal_box_unchanged_is_clean(self):
        assert compare_snapshots(snap(bbox=(1, 1, 1, 1, 1, 1)), snap(bbox=(1, 1, 1, 1, 1, 1)), TOL) == {}

    def test_bbox_valid_on_one_side_only_is_reported(self):
        f = compare_snapshots(snap(bbox=None), snap(bbox=(0, 0, 0, 1, 1, 1)), TOL)
        assert f[KIND_EXTENT_CHANGED] == {"detail": "bounding box valid on one side only"}

    def test_bbox_missing_on_both_sides_is_clean(self):
        assert compare_snapshots(snap(bbox=None), snap(bbox=None), TOL) == {}


class TestAttribution:

    def test_inherited_kind_is_not_a_second_root(self):
        findings = {"A": {KIND_VALIDITY_LOST: "x"}, "B": {KIND_VALIDITY_LOST: "x"}}
        r = attribute_findings(findings, {"B": ["A"]}, ["A", "B"])
        assert [x["name"] for x in r["roots"]] == ["A"]
        assert r["inherited"] == [{"name": "B", "kinds": [KIND_VALIDITY_LOST]}]

    def test_new_kind_downstream_is_its_own_root_and_lists_what_it_inherits(self):
        findings = {"A": {KIND_VALIDITY_LOST: "x"},
                    "B": {KIND_VALIDITY_LOST: "x", KIND_TOPOLOGY_CHANGED: {"faces": {}}}}
        r = attribute_findings(findings, {"B": ["A"]}, ["A", "B"])
        b = next(x for x in r["roots"] if x["name"] == "B")
        assert set(b["new"]) == {KIND_TOPOLOGY_CHANGED}
        assert b["also_inherited"] == [KIND_VALIDITY_LOST]

    def test_independent_branches_are_both_roots(self):
        findings = {"A": {KIND_VALIDITY_LOST: "x"}, "B": {KIND_VALIDITY_LOST: "x"}}
        r = attribute_findings(findings, {}, ["A", "B"])
        assert [x["name"] for x in r["roots"]] == ["A", "B"]
        assert r["inherited"] == []

    def test_informational_kinds_never_make_a_root(self):
        findings = {"A": {KIND_STORED_INVALID: "x", KIND_VALIDITY_GAINED: "y"}}
        assert attribute_findings(findings, {}, ["A"]) == {"roots": [], "inherited": []}

    def test_feature_with_no_findings_is_skipped(self):
        assert attribute_findings({}, {}, ["A", "B"]) == {"roots": [], "inherited": []}

    def test_diamond_input_inherits_when_any_input_has_the_kind(self):
        findings = {"S": {KIND_VALIDITY_LOST: "x"}, "L": {}, "R": {}, "T": {KIND_VALIDITY_LOST: "x"}}
        r = attribute_findings(findings, {"L": ["S"], "R": ["S"], "T": ["L", "R", "S"]}, ["S", "L", "R", "T"])
        assert [x["name"] for x in r["roots"]] == ["S"]
        assert [x["name"] for x in r["inherited"]] == ["T"]


def run_report(findings, order=("Pad", "Fillet"), inputs=None, **extra):
    base = {"order": list(order), "inputs": inputs if inputs is not None else {"Fillet": ["Pad"]},
            "findings": findings, "tolerance": TOL, "features_compared": len(order),
            "skipped_non_feature": 0, "snapshot_errors": {}, "freecad_version": "26.3.0 test",
            "occt_version": "8.0.1", "saved_by_program_version": "1.0R39109"}
    base.update(extra)
    return base


class TestAggregateStability:

    def test_difference_in_every_run_is_a_stable_regression(self):
        runs = [run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}) for _ in range(3)]
        rep = aggregate_runs(runs, 3)
        assert rep["regression_count"] == 1 and rep["unstable_count"] == 0
        assert rep["regressions"]["roots"][0]["name"] == "Fillet"

    def test_difference_in_only_some_runs_is_unstable_not_a_regression(self):
        """Fillet007's actual behaviour: validity lost in 2 of 3 runs."""
        runs = [run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}),
                run_report({"Fillet": {}}),
                run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}})]
        rep = aggregate_runs(runs, 3)
        assert rep["regression_count"] == 0 and rep["unstable_count"] == 1
        root = rep["unstable"]["roots"][0]
        assert root["name"] == "Fillet"
        assert root["new"] == {KIND_VALIDITY_LOST: {"runs_with": 2, "of": 3}}

    def test_present_in_exactly_one_of_n_runs_is_unstable(self):
        runs = [run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}), run_report({"Fillet": {}}),
                run_report({"Fillet": {}})]
        assert aggregate_runs(runs, 3)["unstable_count"] == 1

    def test_present_in_all_but_one_run_is_unstable(self):
        runs = [run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}), run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}),
                run_report({"Fillet": {}})]
        rep = aggregate_runs(runs, 3)
        assert rep["regression_count"] == 0 and rep["unstable_count"] == 1

    def test_single_run_can_never_call_anything_unstable(self):
        rep = aggregate_runs([run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}})], 1)
        assert rep["unstable_count"] == 0 and rep["regression_count"] == 1
        assert rep["runs_used"] == 1 and rep["runs_requested"] == 1

    def test_requested_vs_used_runs_are_both_reported(self):
        rep = aggregate_runs([run_report({}), run_report({})], 5)
        assert (rep["runs_requested"], rep["runs_used"]) == (5, 2)

    def test_downstream_features_inherit_the_instability(self):
        flaky = {"Pad": {}, "Fillet": {KIND_VALIDITY_LOST: "d"}, "Next": {KIND_VALIDITY_LOST: "d"}}
        clean = {"Pad": {}, "Fillet": {}, "Next": {}}
        order = ("Pad", "Fillet", "Next")
        inputs = {"Fillet": ["Pad"], "Next": ["Fillet"]}
        rep = aggregate_runs([run_report(flaky, order, inputs), run_report(clean, order, inputs)], 2)
        assert [x["name"] for x in rep["unstable"]["roots"]] == ["Fillet"]
        assert rep["unstable"]["inherited"] == [{"name": "Next", "kinds": [KIND_VALIDITY_LOST]}]

    def test_stable_and_unstable_kinds_on_the_same_feature_are_split(self):
        runs = [run_report({"Fillet": {KIND_TOPOLOGY_CHANGED: {"faces": 1}, KIND_VALIDITY_LOST: "d"}}),
                run_report({"Fillet": {KIND_TOPOLOGY_CHANGED: {"faces": 1}}})]
        rep = aggregate_runs(runs, 2)
        assert set(rep["regressions"]["roots"][0]["new"]) == {KIND_TOPOLOGY_CHANGED}
        assert set(rep["unstable"]["roots"][0]["new"]) == {KIND_VALIDITY_LOST}

    def test_stable_detail_comes_from_the_first_run(self):
        runs = [run_report({"Fillet": {KIND_VOLUME_CHANGED: {"relative_delta": 0.5}}}),
                run_report({"Fillet": {KIND_VOLUME_CHANGED: {"relative_delta": 0.6}}})]
        rep = aggregate_runs(runs, 2)
        assert rep["regressions"]["roots"][0]["new"][KIND_VOLUME_CHANGED] == {"relative_delta": 0.5}

    def test_informational_kinds_are_collected_and_never_counted(self):
        runs = [run_report({"Pad": {KIND_STORED_INVALID: "s"}}), run_report({"Pad": {KIND_STORED_INVALID: "s"}})]
        rep = aggregate_runs(runs, 2)
        assert rep["informational"] == [{"name": "Pad", "kinds": [KIND_STORED_INVALID]}]
        assert rep["regression_count"] == 0 and rep["unstable_count"] == 0

    def test_feature_missing_from_a_runs_findings_counts_as_clean_there(self):
        runs = [run_report({"Fillet": {KIND_VALIDITY_LOST: "d"}}), run_report({})]
        assert aggregate_runs(runs, 2)["unstable_count"] == 1

    def test_metadata_is_carried_through(self):
        rep = aggregate_runs([run_report({})], 1)
        assert rep["freecad_version"] == "26.3.0 test" and rep["occt_version"] == "8.0.1"
        assert rep["saved_by_program_version"] == "1.0R39109"
        assert rep["features_compared"] == 2 and rep["tolerance"] == TOL
        assert rep["format_version"] == ra.FORMAT_VERSION

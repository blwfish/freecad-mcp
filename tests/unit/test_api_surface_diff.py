"""Unit tests for tests/integration/_api_surface_diff.py -- the pure
snapshot-diff logic behind the API-surface drift test. No FreeCAD, no
sockets: golden/live snapshots are handcrafted dicts.

Covers the ambiguous/threshold cases per this project's testing discipline:
whole-type staleness vs. per-property staleness (a new property added to an
*already-known* type must not silently fall through uncaught -- see the
"NewProp" cases below), errored-on-one-side vs. errored-on-both-sides, and
the deliberate non-goal of reporting property *additions*.
"""

from tests.integration._api_surface_diff import diff_snapshots

EMPTY = {
    "stale_snapshot_types": {},
    "types_no_longer_resolve": {},
    "properties_removed": {},
    "properties_changed": {},
}


def _result(**overrides):
    result = {k: dict(v) for k, v in EMPTY.items()}
    result.update(overrides)
    return result


class TestDiffSnapshots:
    def test_all_match_reports_nothing(self):
        scope = {"Part::Box": {"Length"}}
        golden = {"Part::Box": {"Length": "App::PropertyLength"}}
        live = {"Part::Box": {"Length": "App::PropertyLength"}}
        assert diff_snapshots(scope, golden, live) == _result()

    def test_type_entirely_absent_from_golden_is_stale(self):
        scope = {"Part::Box": {"Length"}}
        golden = {}
        live = {"Part::Box": {"Length": "App::PropertyLength"}}
        result = diff_snapshots(scope, golden, live)
        assert result["stale_snapshot_types"] == {"Part::Box": ["Length"]}
        assert result["types_no_longer_resolve"] == {}
        assert result["properties_removed"] == {}
        assert result["properties_changed"] == {}

    def test_new_property_on_already_known_type_is_stale_not_silently_dropped(self):
        # This is the gap the first draft of the diff logic had: a type
        # already present in golden, but golden's dict for it never
        # scanned "NewProp" (added to handler code after the last regen).
        # Must surface as stale, not vanish silently.
        scope = {"Part::Box": {"Length", "NewProp"}}
        golden = {"Part::Box": {"Length": "App::PropertyLength"}}
        live = {"Part::Box": {"Length": "App::PropertyLength", "NewProp": "App::PropertyBool"}}
        result = diff_snapshots(scope, golden, live)
        assert result["stale_snapshot_types"] == {"Part::Box": ["NewProp"]}
        assert result["properties_removed"] == {}
        assert result["properties_changed"] == {}

    def test_type_stopped_resolving(self):
        scope = {"Part::Revolution": {"Axis"}}
        golden = {"Part::Revolution": {"Axis": "App::PropertyVector"}}
        live = {"Part::Revolution": {"__ERROR__": "'Part' has no attribute 'Revolution'"}}
        result = diff_snapshots(scope, golden, live)
        assert result["types_no_longer_resolve"] == {
            "Part::Revolution": "'Part' has no attribute 'Revolution'"
        }
        assert result["properties_removed"] == {}
        assert result["properties_changed"] == {}
        assert result["stale_snapshot_types"] == {}

    def test_type_missing_entirely_from_live_scan_treated_as_no_longer_resolving(self):
        # live_scan dict simply doesn't have this key at all (e.g. the
        # remote driver script crashed producing it) -- must not KeyError,
        # must be treated the same as an explicit __ERROR__.
        scope = {"Part::Box": {"Length"}}
        golden = {"Part::Box": {"Length": "App::PropertyLength"}}
        live = {}
        result = diff_snapshots(scope, golden, live)
        assert "Part::Box" in result["types_no_longer_resolve"]

    def test_errored_on_both_sides_reports_nothing(self):
        # A type that never resolved even at snapshot time and still
        # doesn't resolve live isn't a NEW break -- nothing to report.
        scope = {"FreeCADGui::Widget": {"X"}}
        golden = {"FreeCADGui::Widget": {"__ERROR__": "no such type"}}
        live = {"FreeCADGui::Widget": {"__ERROR__": "no such type"}}
        assert diff_snapshots(scope, golden, live) == _result()

    def test_errored_only_in_golden_recovering_live_reports_nothing(self):
        # A type that failed to resolve at snapshot time but now resolves
        # live isn't a regression -- informational at best, deliberately
        # not surfaced by this mechanism (see module docstring).
        scope = {"Something::New": {"X"}}
        golden = {"Something::New": {"__ERROR__": "no such type"}}
        live = {"Something::New": {"X": "App::PropertyBool"}}
        assert diff_snapshots(scope, golden, live) == _result()

    def test_property_removed(self):
        scope = {"Part::Box": {"Length"}}
        golden = {"Part::Box": {"Length": "App::PropertyLength"}}
        live = {"Part::Box": {}}
        result = diff_snapshots(scope, golden, live)
        assert result["properties_removed"] == {"Part::Box": ["Length"]}
        assert result["properties_changed"] == {}
        assert result["types_no_longer_resolve"] == {}

    def test_property_type_changed(self):
        scope = {"Part::Wedge": {"Xmax"}}
        golden = {"Part::Wedge": {"Xmax": "App::PropertyFloat"}}
        live = {"Part::Wedge": {"Xmax": "App::PropertyDistance"}}
        result = diff_snapshots(scope, golden, live)
        assert result["properties_changed"] == {
            "Part::Wedge": ["Xmax: App::PropertyFloat -> App::PropertyDistance"]
        }
        assert result["properties_removed"] == {}

    def test_property_missing_on_both_sides_reports_nothing(self):
        # golden recorded "__MISSING__" (it was scanned and genuinely
        # didn't exist at snapshot time) and it's still "__MISSING__"
        # live -- never existed either side, nothing to diff.
        scope = {"Part::Box": {"Optional"}}
        golden = {"Part::Box": {"Optional": "__MISSING__"}}
        live = {"Part::Box": {"Optional": "__MISSING__"}}
        assert diff_snapshots(scope, golden, live) == _result()

    def test_property_added_live_not_reported_as_drift(self):
        # golden recorded "__MISSING__" for a scanned-but-absent property;
        # it now resolves live. This is an *addition*, deliberately not
        # reported by this mechanism (see module docstring) -- distinct
        # from the "new property on a known type" staleness case above,
        # where golden never had the property KEY at all.
        scope = {"Part::Box": {"Optional"}}
        golden = {"Part::Box": {"Optional": "__MISSING__"}}
        live = {"Part::Box": {"Optional": "App::PropertyBool"}}
        assert diff_snapshots(scope, golden, live) == _result()

    def test_multiple_types_diffed_independently(self):
        scope = {
            "Part::Box": {"Length"},
            "Part::Sphere": {"Radius"},
        }
        golden = {
            "Part::Box": {"Length": "App::PropertyLength"},
            "Part::Sphere": {"Radius": "App::PropertyLength"},
        }
        live = {
            "Part::Box": {"Length": "App::PropertyLength"},
            "Part::Sphere": {},  # Radius removed
        }
        result = diff_snapshots(scope, golden, live)
        assert result["properties_removed"] == {"Part::Sphere": ["Radius"]}
        assert "Part::Box" not in result["properties_removed"]

    def test_empty_scope_reports_nothing(self):
        assert diff_snapshots({}, {"Part::Box": {"Length": "App::PropertyLength"}}, {}) == _result()

    def test_type_not_in_scope_is_never_diffed_even_if_golden_and_live_disagree(self):
        # A type the current handlers don't touch at all must never
        # surface a finding, even if golden/live wildly disagree on it --
        # the diff is always scoped to current handler source, never to
        # whatever's incidentally sitting in golden or live.
        scope = {}
        golden = {"Part::Box": {"Length": "App::PropertyLength"}}
        live = {"Part::Box": {"Length": "App::PropertyDistance"}}
        assert diff_snapshots(scope, golden, live) == _result()

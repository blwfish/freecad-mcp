"""Unit tests for helpers/window_door_helpers.py.

This module had zero test coverage before this file — it can't even import
without FreeCAD, and no test referenced it, so the gap was invisible in the
coverage report (absent from the table entirely rather than showing 0%).

Focus: the behavioral fixes made alongside this test file —
  * get_object no longer first-match-wins on an ambiguous Label
  * match_hole_to_master uses find_holes_in_wall's already-correct
    width/height/depth labels instead of re-deriving them by sorting
    (the sort silently assumes wall thickness is always the smallest
    dimension, which breaks on a thick wall with a small window)
  * populate_holes's auto_place is now a real gate — _prompt_user used to
    unconditionally return True, so auto_place=False created clones anyway
  * get_master_dimensions no longer swallows param-read failures with a
    bare except
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests.unit._freecad_mocks import (  # noqa: E402
    mock_FreeCAD,
    reset_mocks,
    make_mock_doc,
    make_part_object,
    make_box_object,
    _Vec,
    _Quantity,
)

import pytest
from unittest.mock import MagicMock

from helpers.window_door_helpers import WindowDoorHelpers  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    reset_mocks()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_bbox(xmin=0, ymin=0, zmin=0, xlen=900, ylen=1200, zlen=200):
    bb = MagicMock()
    bb.XMin, bb.YMin, bb.ZMin = xmin, ymin, zmin
    bb.XLength, bb.YLength, bb.ZLength = xlen, ylen, zlen
    return bb


def _make_cut(base, tool_bbox, name="Cut", label=None):
    """A Part::Cut whose Base is `base` and whose Tool has the given bbox."""
    cut = MagicMock()
    cut.TypeId = "Part::Cut"
    cut.Name = name
    cut.Label = label or name
    cut.Base = base
    tool = MagicMock()
    tool.Name = f"{name}Tool"
    tool.Label = f"{name}Tool"
    tool_shape = MagicMock()
    tool_shape.BoundBox = tool_bbox
    tool.Shape = tool_shape
    cut.Tool = tool
    return cut


def _make_spreadsheet(**params):
    """spec= limits the mock to only the given attrs, so getattr(ss, 'x',
    None) on an unset param correctly returns None instead of MagicMock's
    default attribute auto-vivification (which would make every param
    "present" as a fresh MagicMock, defeating the "params missing" tests)."""
    attrs = ["TypeId", "Name", "Label"] + list(params.keys())
    ss = MagicMock(spec=attrs)
    ss.TypeId = "Spreadsheet::Sheet"
    ss.Name = "Params"
    ss.Label = "Params"
    for k, v in params.items():
        setattr(ss, k, v)
    return ss


# ---------------------------------------------------------------------------
# get_object
# ---------------------------------------------------------------------------

class TestGetObject:
    def test_resolves_by_internal_name(self):
        box = make_box_object("B1")
        doc = make_mock_doc([box])
        wdh = WindowDoorHelpers(doc=doc)
        assert wdh.get_object("B1") is box

    def test_resolves_by_label_when_name_differs(self):
        box = make_box_object("B1")
        box.Label = "East Wall"
        doc = make_mock_doc([box])
        wdh = WindowDoorHelpers(doc=doc)
        assert wdh.get_object("East Wall") is box

    def test_not_found_raises(self):
        doc = make_mock_doc([])
        wdh = WindowDoorHelpers(doc=doc)
        with pytest.raises(ValueError, match="not found"):
            wdh.get_object("Ghost")

    def test_ambiguous_label_raises_instead_of_first_match(self):
        """The core regression: two objects sharing a Label used to
        silently resolve to whichever was first in doc.Objects — exactly
        the "wrong solid" risk BaseHandler.get_object() was hardened
        against elsewhere in this codebase."""
        a = make_box_object("Box001")
        a.Label = "Window"
        b = make_box_object("Box002")
        b.Label = "Window"
        doc = make_mock_doc([a, b])
        wdh = WindowDoorHelpers(doc=doc)

        with pytest.raises(ValueError, match="Ambiguous label"):
            wdh.get_object("Window")


# ---------------------------------------------------------------------------
# find_holes_in_wall
# ---------------------------------------------------------------------------

class TestFindHolesInWall:
    def test_finds_cut_using_wall_as_base(self):
        wall = make_box_object("Wall1")
        tool_bbox = _make_bbox(xlen=900, ylen=1200, zlen=200)
        cut = _make_cut(wall, tool_bbox, name="Cut1", label="Cut1")
        doc = make_mock_doc([wall, cut])
        wdh = WindowDoorHelpers(doc=doc)

        holes = wdh.find_holes_in_wall(wall)

        assert len(holes) == 1
        hole = holes[0]
        assert hole['width'] == 900
        assert hole['height'] == 1200
        assert hole['depth'] == 200
        assert hole['cut_label'] == "Cut1"

    def test_ignores_cuts_on_a_different_wall(self):
        wall = make_box_object("Wall1")
        other_wall = make_box_object("Wall2")
        cut = _make_cut(other_wall, _make_bbox(), name="Cut1")
        doc = make_mock_doc([wall, other_wall, cut])
        wdh = WindowDoorHelpers(doc=doc)

        holes = wdh.find_holes_in_wall(wall)
        assert holes == []

    def test_unknown_wall_name_returns_empty_not_raise(self):
        doc = make_mock_doc([])
        wdh = WindowDoorHelpers(doc=doc)
        holes = wdh.find_holes_in_wall("NoSuchWall")
        assert holes == []

    def test_multiple_holes_all_found(self):
        wall = make_box_object("Wall1")
        cut1 = _make_cut(wall, _make_bbox(xlen=900, ylen=1200, zlen=200), name="Cut1")
        cut2 = _make_cut(wall, _make_bbox(xlen=800, ylen=2000, zlen=200), name="Cut2")
        doc = make_mock_doc([wall, cut1, cut2])
        wdh = WindowDoorHelpers(doc=doc)

        holes = wdh.find_holes_in_wall(wall)
        assert len(holes) == 2


# ---------------------------------------------------------------------------
# get_master_dimensions
# ---------------------------------------------------------------------------

class TestGetMasterDimensions:
    def test_reads_quantity_valued_params(self):
        ss = _make_spreadsheet(width=_Quantity(900), height=_Quantity(1200))
        master = make_part_object("Master")
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)

        dims = wdh.get_master_dimensions(master)

        assert dims['width'] == 900
        assert dims['height'] == 1200

    def test_reads_plain_float_params(self):
        ss = _make_spreadsheet(width=900.0, height=1200.0)
        master = make_part_object("Master")
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)

        dims = wdh.get_master_dimensions(master)
        assert dims['width'] == 900.0

    def test_falls_back_to_bbox_when_params_missing(self):
        ss = _make_spreadsheet()  # no width/height params at all
        master = make_part_object("Master", bbox=(900.0, 1200.0, 60.0))
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)

        dims = wdh.get_master_dimensions(master)

        assert dims['width'] == 900.0
        assert dims['height'] == 1200.0

    def test_no_spreadsheet_raises(self):
        master = make_part_object("Master")
        doc = make_mock_doc([master])
        wdh = WindowDoorHelpers(doc=doc)

        with pytest.raises(ValueError, match="[Ss]preadsheet"):
            wdh.get_master_dimensions(master)

    def test_bad_param_value_does_not_crash_the_whole_lookup(self, capsys):
        """A malformed parameter must be skipped (with a visible warning),
        not silently swallowed by a bare except, and must not abort
        extraction of the other, valid params."""
        ss = MagicMock()
        ss.TypeId = "Spreadsheet::Sheet"
        ss.Name = "Params"
        ss.Label = "Params"
        ss.width = object()  # not a Quantity, not float()-able
        ss.height = _Quantity(1200)
        master = make_part_object("Master")
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)

        dims = wdh.get_master_dimensions(master)

        assert dims.get('height') == 1200
        assert 'width' not in dims or isinstance(dims['width'], float)
        # The failure must be visible, not silent.
        assert "width" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# match_hole_to_master
# ---------------------------------------------------------------------------

class TestMatchHoleToMaster:
    def _hole(self, width, height, depth):
        return {'width': width, 'height': height, 'depth': depth,
                'position': _Vec(0, 0, 0), 'cut_label': 'Cut1'}

    def test_exact_match_within_tolerance(self):
        wdh = WindowDoorHelpers.__new__(WindowDoorHelpers)
        hole = self._hole(900.2, 1200.1, 200)
        master_dims = {'width': 900, 'height': 1200}
        assert wdh.match_hole_to_master(hole, master_dims, tolerance_mm=0.3) is True

    def test_outside_tolerance_no_match(self):
        wdh = WindowDoorHelpers.__new__(WindowDoorHelpers)
        hole = self._hole(905, 1200, 200)
        master_dims = {'width': 900, 'height': 1200}
        assert wdh.match_hole_to_master(hole, master_dims, tolerance_mm=0.3) is False

    def test_smaller_hole_also_matches_symmetric_tolerance(self):
        """The tolerance band is symmetric — a hole slightly SMALLER than
        the master must match too, not just larger (see the corrected
        comment in match_hole_to_master)."""
        wdh = WindowDoorHelpers.__new__(WindowDoorHelpers)
        hole = self._hole(899.8, 1200, 200)
        master_dims = {'width': 900, 'height': 1200}
        assert wdh.match_hole_to_master(hole, master_dims, tolerance_mm=0.3) is True

    def test_thick_wall_small_window_matches_correctly(self):
        """The regression case: a wall thicker than the window is wide.
        The old sort-based logic assumed depth (wall thickness) was always
        the smallest of the three hole dimensions — sorted [500(width),
        600(height), 900(wall depth)] would report width=600, height=900,
        completely misreading which axis is which. Using
        find_holes_in_wall's own axis-correct labels avoids that."""
        wdh = WindowDoorHelpers.__new__(WindowDoorHelpers)
        # A 900mm-thick wall (unusually thick) with a 500x600 window.
        hole = self._hole(width=500, height=600, depth=900)
        master_dims = {'width': 500, 'height': 600}

        assert wdh.match_hole_to_master(hole, master_dims, tolerance_mm=0.3) is True

    def test_thick_wall_small_window_would_have_false_matched_wrong_master(self):
        """Same thick-wall hole, but checked against a master whose
        dimensions match what the OLD (sort-based) logic would have
        mis-extracted (width=600, height=900) — the fixed version must
        correctly reject this, proving it isn't sorting anymore."""
        wdh = WindowDoorHelpers.__new__(WindowDoorHelpers)
        hole = self._hole(width=500, height=600, depth=900)
        wrong_master_dims = {'width': 600, 'height': 900}  # old-logic's misread

        assert wdh.match_hole_to_master(hole, wrong_master_dims, tolerance_mm=0.3) is False


# ---------------------------------------------------------------------------
# clone_and_position
# ---------------------------------------------------------------------------

class TestCloneAndPosition:
    def test_dry_run_creates_nothing(self):
        master = make_part_object("Master")
        ss = _make_spreadsheet(width=_Quantity(900), height=_Quantity(1200))
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)
        hole = {'position': _Vec(100, 200, 0), 'cut_label': 'Cut1'}

        result = wdh.clone_and_position(master, hole, dry_run=True)

        assert result is None
        doc.addObject.assert_not_called()

    def test_creates_clone_positioned_at_hole(self):
        master = make_part_object("Master")
        ss = _make_spreadsheet(width=_Quantity(900), height=_Quantity(1200))
        doc = make_mock_doc([master, ss])
        wdh = WindowDoorHelpers(doc=doc)
        hole = {'position': _Vec(100, 200, 0), 'cut_label': 'Cut1'}

        clone = wdh.clone_and_position(master, hole)

        doc.addObject.assert_called_once_with("Part::Clone", f"{master.Name}_Clone")
        assert clone.Source == master
        assert clone.Placement.Base == _Vec(100, 200, 0)


# ---------------------------------------------------------------------------
# populate_holes — auto_place is a real gate, not a fake prompt
# ---------------------------------------------------------------------------

class TestPopulateHolesAutoPlaceGate:
    def _setup(self):
        wall = make_box_object("Wall1")
        master = make_part_object("Master")
        ss = _make_spreadsheet(width=_Quantity(900), height=_Quantity(1200))
        tool_bbox = _make_bbox(xlen=900, ylen=1200, zlen=200)
        cut = _make_cut(wall, tool_bbox, name="Cut1", label="Cut1")
        doc = make_mock_doc([wall, master, ss, cut])
        wdh = WindowDoorHelpers(doc=doc)
        return wdh, master, wall, doc

    def test_dry_run_true_creates_nothing_even_with_matches(self):
        wdh, master, wall, doc = self._setup()
        result = wdh.populate_holes(master.Name, wall.Name, dry_run=True)
        assert len(result['matches']) == 1
        assert result['created'] == []
        doc.addObject.assert_not_called()

    def test_auto_place_false_reports_matches_but_creates_nothing(self):
        """The core regression: _prompt_user used to unconditionally
        return True, so auto_place=False still created clones. Now
        auto_place is the only gate and must be honored."""
        wdh, master, wall, doc = self._setup()
        result = wdh.populate_holes(master.Name, wall.Name, dry_run=False, auto_place=False)
        assert len(result['matches']) == 1
        assert result['created'] == []
        doc.addObject.assert_not_called()

    def test_auto_place_true_creates_clones(self):
        wdh, master, wall, doc = self._setup()
        result = wdh.populate_holes(master.Name, wall.Name, dry_run=False, auto_place=True)
        assert len(result['matches']) == 1
        assert len(result['created']) == 1
        doc.addObject.assert_called_once()


# ---------------------------------------------------------------------------
# check_link_config
# ---------------------------------------------------------------------------

class TestCheckLinkConfig:
    def _make_link(self, name, copy_on_change):
        link = MagicMock()
        link.TypeId = "App::Link"
        link.Name = name
        link.Label = name
        link.LinkCopyOnChange = copy_on_change
        return link

    def test_flags_linked_as_an_issue(self):
        link = self._make_link("L1", "Linked")
        doc = make_mock_doc([link])
        wdh = WindowDoorHelpers(doc=doc)

        issues, fixed = wdh.check_link_config(auto_fix=False)

        assert len(issues) == 1
        assert issues[0]['object'] == "L1"
        assert fixed == []

    def test_owned_is_not_an_issue(self):
        link = self._make_link("L1", "Owned")
        doc = make_mock_doc([link])
        wdh = WindowDoorHelpers(doc=doc)

        issues, fixed = wdh.check_link_config(auto_fix=False)
        assert issues == []

    def test_auto_fix_sets_owned_and_recomputes(self):
        link = self._make_link("L1", "Linked")
        doc = make_mock_doc([link])
        wdh = WindowDoorHelpers(doc=doc)

        issues, fixed = wdh.check_link_config(auto_fix=True)

        assert link.LinkCopyOnChange == "Owned"
        assert fixed == ["L1"]
        doc.recompute.assert_called_once()

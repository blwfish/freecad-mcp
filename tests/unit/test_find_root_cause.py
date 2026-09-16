"""Unit tests for MeasurementOpsHandler.find_root_cause and its supporting
_classify_shape_findings helper.

find_root_cause walks object_name's OutList subtree and, for each
shape-bearing object or sketch found, checks it independently for four
anomaly kinds (sketch_open_wire, null_shape, invalid_topology, not_solid,
bop_errors) then reports only the object(s) where a given anomaly kind (or,
for bop_errors, a given OCCT error CLASS) first appears -- not every object
that merely inherited it from upstream.

Run with: python3 -m pytest tests/unit/test_find_root_cause.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

if 'FreeCAD' not in sys.modules:
    _fc = MagicMock()
    _fc.GuiUp = False
    _fc.Console = MagicMock()
    sys.modules['FreeCAD'] = _fc
    sys.modules['FreeCADGui'] = MagicMock()
    sys.modules['Part'] = MagicMock()
    sys.modules['Sketcher'] = MagicMock()

sys.modules['FreeCAD'].ActiveDocument = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

import handlers.base as base_module
import handlers.measurement_ops as measurement_ops_module
from handlers.measurement_ops import MeasurementOpsHandler


def make_handler():
    return MeasurementOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


def _make_object(name, type_id="Part::Feature", out_list=None,
                  is_null=False, is_valid=True, faces=0, solids=0, shells=0,
                  check_side_effect=None, label2=""):
    """A generic shape-bearing mock object. faces/solids/shells are plain
    counts (len() of the corresponding list), not real geometry -- every
    check under test reads only len(shape.Faces)/.Solids/.Shells."""
    obj = MagicMock()
    obj.Name = name
    obj.TypeId = type_id
    obj.OutList = out_list or []
    obj.Label2 = label2
    obj.Shape.isNull = MagicMock(return_value=is_null)
    obj.Shape.isValid = MagicMock(return_value=is_valid)
    obj.Shape.Faces = [MagicMock() for _ in range(faces)]
    obj.Shape.Solids = [MagicMock() for _ in range(solids)]
    obj.Shape.Shells = [MagicMock() for _ in range(shells)]
    if check_side_effect is not None:
        obj.Shape.check = MagicMock(side_effect=check_side_effect)
    else:
        obj.Shape.check = MagicMock(return_value=None)
    return obj


def _make_sketch(name="Sketch", out_list=None, open_vertices=None):
    sketch = MagicMock()
    sketch.Name = name
    sketch.TypeId = "Sketcher::SketchObject"
    sketch.OutList = out_list or []
    sketch.OpenVertices = open_vertices if open_vertices is not None else []
    sketch.detectMissingPointOnPointConstraints = MagicMock(return_value=None)
    sketch.MissingPointOnPointConstraints = []
    return sketch


def _bop_exception(*classes_and_counts):
    """Build an exception whose text matches shape.check(True)'s real
    "BOP check found the following errors:\\nError in Edge: <Class>\\n..."
    format -- classes_and_counts is (class_name, count) pairs."""
    lines = ["BOP check found the following errors:"]
    for cls, count in classes_and_counts:
        for _ in range(count):
            lines.append(f"Error in Edge: {cls}")
    return Exception("\n".join(lines) + "\n")


class TestFindRootCauseCleanAndMissing:

    def test_object_not_found(self):
        handler = make_handler()
        handler.resolve_object = MagicMock(return_value=(None, None, "Object not found: Ghost"))
        result = handler.find_root_cause({'object_name': 'Ghost'})
        assert result == "Object not found: Ghost"

    def test_clean_subtree_reports_no_anomalies(self):
        handler = make_handler()
        child = _make_object("Child", faces=6, solids=1)
        root = _make_object("Root", out_list=[child], faces=6, solids=1)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), root, None))

        result = handler.find_root_cause({'object_name': 'Root'})
        assert "no anomalies found" in result
        assert "2 shape-bearing" in result


class TestNotSolidThreshold:

    def test_faces_with_zero_solids_is_flagged(self):
        """The exact bug this operation grew out of: a Revolution/Extrusion
        with Solid=False produces a Shell -- Faces > 0 but Solids == 0."""
        handler = make_handler()
        leaf = _make_object("Revolve", type_id="Part::Revolution",
                             faces=7, solids=0, shells=1, label2="Main Body")
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Revolve'})
        assert "ROOT CAUSE: Revolve" in result
        assert "Label2='Main Body'" in result
        assert "not_solid" in result
        assert "0 Solids" in result

    def test_zero_faces_and_zero_solids_not_flagged(self):
        """A pure-wire/empty shape (0 Faces) must NOT be flagged not_solid --
        the check is specifically 'has faces, but none of them close into a
        Solid', not 'has no Solids'. This is the ambiguous zero-remainder
        case the Threshold-Boundary rule calls out."""
        handler = make_handler()
        leaf = _make_object("Wire", faces=0, solids=0)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Wire'})
        assert "no anomalies found" in result

    def test_faces_with_solids_present_not_flagged(self):
        handler = make_handler()
        leaf = _make_object("Box", faces=6, solids=1)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Box'})
        assert "no anomalies found" in result


class TestNullAndInvalidShape:

    def test_null_shape_short_circuits_other_checks(self):
        """A null shape means recompute failed -- isValid()/Faces/check(True)
        are all meaningless on a null shape and must not even be called."""
        handler = make_handler()
        leaf = _make_object("Failed", is_null=True)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Failed'})
        assert "ROOT CAUSE: Failed" in result
        assert "null_shape" in result
        leaf.Shape.isValid.assert_not_called()
        leaf.Shape.check.assert_not_called()

    def test_invalid_topology_flagged(self):
        handler = make_handler()
        leaf = _make_object("Bad", is_valid=False, faces=4, solids=1)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Bad'})
        assert "ROOT CAUSE: Bad" in result
        assert "invalid_topology" in result


class TestSketchOpenWire:

    def test_open_wire_sketch_is_root_cause(self):
        handler = make_handler()
        sketch = _make_sketch("Sketch003", open_vertices=[(1.0, 2.0, 0.0)])
        handler.resolve_object = MagicMock(return_value=(MagicMock(), sketch, None))

        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler.find_root_cause({'object_name': 'Sketch003'})

        assert "ROOT CAUSE: Sketch003" in result
        assert "sketch_open_wire" in result

    def test_clean_sketch_not_flagged(self):
        handler = make_handler()
        sketch = _make_sketch("Sketch003", open_vertices=[])
        handler.resolve_object = MagicMock(return_value=(MagicMock(), sketch, None))

        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler.find_root_cause({'object_name': 'Sketch003'})

        assert "no anomalies found" in result

    def test_sketch_skips_shape_checks_entirely(self):
        """A sketch is classified purely via _diagnose_open_wires -- it must
        never fall through to the Faces/Solids/check(True) path (a sketch's
        .Shape is wires/edges only and check(True) on it is meaningless)."""
        handler = make_handler()
        sketch = _make_sketch("Sketch003", open_vertices=[])
        handler.resolve_object = MagicMock(return_value=(MagicMock(), sketch, None))

        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            handler.find_root_cause({'object_name': 'Sketch003'})

        sketch.Shape.isNull.assert_not_called()


class TestBopErrorPropagation:
    """Mirrors the live Compound/Fusion004/Fillet investigation this
    operation was built from: shape.check(True) raises structured
    'Error in <Elem>: <Class>' lines, and an object should only be a ROOT
    CAUSE for a given error CLASS if none of its direct inputs already
    show that class."""

    def test_inherited_bop_class_is_not_a_second_root_cause(self):
        child = _make_object(
            "Fillet", type_id="Part::Fillet", faces=1, solids=1,
            check_side_effect=_bop_exception(("BOPAlgo_InvalidCurveOnSurface", 4)))
        parent = _make_object(
            "Cut", type_id="Part::Cut", out_list=[child], faces=1, solids=1,
            check_side_effect=_bop_exception(("BOPAlgo_InvalidCurveOnSurface", 4)))
        handler = make_handler()
        handler.resolve_object = MagicMock(return_value=(MagicMock(), parent, None))

        result = handler.find_root_cause({'object_name': 'Cut'})

        assert "ROOT CAUSE: Fillet" in result
        assert "BOPAlgo_InvalidCurveOnSurface x4" in result
        assert "ROOT CAUSE: Cut" not in result
        assert "Cut (Part::Cut) -- inherits: bop_errors" in result

    def test_new_bop_class_at_parent_is_an_independent_root_cause(self):
        """The exact live case: Compound inherits Fusion004's
        InvalidCurveOnSurface AND independently introduces SelfIntersect --
        both must be reported, and Compound's block must show only the NEW
        class, not re-list the inherited one."""
        child = _make_object(
            "Fusion004", type_id="Part::MultiFuse", faces=1, solids=1,
            check_side_effect=_bop_exception(("BOPAlgo_InvalidCurveOnSurface", 8)))
        parent = _make_object(
            "Compound", type_id="Part::Compound", out_list=[child], faces=1, solids=3,
            check_side_effect=_bop_exception(
                ("BOPAlgo_InvalidCurveOnSurface", 8), ("BOPAlgo SelfIntersect", 26)))
        handler = make_handler()
        handler.resolve_object = MagicMock(return_value=(MagicMock(), parent, None))

        result = handler.find_root_cause({'object_name': 'Compound'})

        assert "ROOT CAUSE: Fusion004" in result
        assert "ROOT CAUSE: Compound" in result
        compound_block = result.split("ROOT CAUSE: Compound", 1)[1]
        assert "BOPAlgo SelfIntersect x26" in compound_block
        assert "BOPAlgo_InvalidCurveOnSurface" not in compound_block

    def test_unparseable_check_exception_falls_back_to_raw_text(self):
        """Ambiguous input: shape.check(True) can raise text that doesn't
        match the 'Error in X: Y' format at all (e.g. an older FC build's
        wording). Must not crash or silently report nothing."""
        handler = make_handler()
        leaf = _make_object("Weird", faces=1, solids=1,
                             check_side_effect=Exception("something went sideways"))
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Weird'})
        assert "ROOT CAUSE: Weird" in result
        assert "unparsed" in result

    def test_check_passes_silently_not_flagged(self):
        handler = make_handler()
        leaf = _make_object("Clean", faces=1, solids=1, check_side_effect=None)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), leaf, None))

        result = handler.find_root_cause({'object_name': 'Clean'})
        assert "no anomalies found" in result


class TestTraversal:

    def test_non_shape_object_skipped_but_walked_through(self):
        """A Spreadsheet (no .Shape at all) sits between the root and a
        broken sketch -- it must be skipped for classification but still
        walked through so the sketch beneath it is reached."""
        handler = make_handler()
        sketch = _make_sketch("Sketch005", open_vertices=[(0.0, 0.0, 0.0)])
        spreadsheet = MagicMock()
        spreadsheet.Name = "Spreadsheet"
        spreadsheet.TypeId = "Spreadsheet::Sheet"
        spreadsheet.OutList = [sketch]
        del spreadsheet.Shape
        root = _make_object("Root", out_list=[spreadsheet], faces=1, solids=1)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), root, None))

        with patch.object(base_module, 'FreeCAD') as mock_fc:
            mock_fc.Vector = lambda x=0, y=0, z=0: MagicMock(x=x, y=y, z=z)
            result = handler.find_root_cause({'object_name': 'Root'})

        assert "ROOT CAUSE: Sketch005" in result
        assert "Spreadsheet" not in result

    def test_diamond_dependency_deduped(self):
        """The same broken object reachable via two OutList paths must be
        classified once, not reported twice."""
        handler = make_handler()
        shared = _make_object("Shared", faces=1, solids=0)  # not_solid
        branch_a = _make_object("BranchA", out_list=[shared], faces=1, solids=1)
        branch_b = _make_object("BranchB", out_list=[shared], faces=1, solids=1)
        root = _make_object("Root", out_list=[branch_a, branch_b], faces=1, solids=1)
        handler.resolve_object = MagicMock(return_value=(MagicMock(), root, None))

        result = handler.find_root_cause({'object_name': 'Root'})
        assert result.count("ROOT CAUSE: Shared") == 1

    def test_cycle_in_outlist_does_not_infinite_loop(self):
        """A malformed/cyclic OutList (A depends on B, B depends on A) must
        terminate via the visited-set, not stack-overflow."""
        handler = make_handler()
        a = _make_object("A", faces=1, solids=1)
        b = _make_object("B", out_list=[a], faces=1, solids=1)
        a.OutList = [b]
        handler.resolve_object = MagicMock(return_value=(MagicMock(), a, None))

        result = handler.find_root_cause({'object_name': 'A'})
        assert "no anomalies found" in result  # both clean; just must not hang/crash

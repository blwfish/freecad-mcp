"""Unit tests for BooleanOpsHandler.

Covers fuse (Part::MultiFuse), cut (Part::Cut), common (Part::MultiCommon).
Boolean ops are the highest-traffic operations in the server — every
non-trivial assembly goes through them. Tests verify TypeId routing,
shape parameter assembly, source visibility hiding, and error paths
(insufficient operands, missing objects).
"""

import unittest

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    assert_error_contains,
    assert_success_contains,
)

from handlers.boolean_ops import BooleanOpsHandler


class TestFuseObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_needs_at_least_two_objects(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.fuse_objects({'objects': ['OnlyOne']})
        assert_error_contains(self, result, "at least 2")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.fuse_objects({'objects': ['A', 'B']})
        assert_error_contains(self, result, "no active document")

    def test_missing_object_aborts(self):
        a = make_box_object("A")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.fuse_objects({'objects': ['A', 'GhostB']})
        assert_error_contains(self, result, "not found", "ghostb")

    def test_creates_multifuse_with_shapes_and_hides_sources(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fuse_objects({
            'objects': ['A', 'B'], 'name': 'Union',
        })

        assert_success_contains(self, result, "Union", "2 objects")
        doc.addObject.assert_called_with("Part::MultiFuse", "Union")
        fusion = doc.Objects[-1]
        self.assertEqual(list(fusion.Shapes), [a, b])
        # Sources hidden after fuse
        self.assertFalse(a.Visibility)
        self.assertFalse(b.Visibility)

    def test_fuse_three_objects(self):
        a = make_box_object("A")
        b = make_box_object("B")
        c = make_box_object("C")
        doc = make_mock_doc([a, b, c])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fuse_objects({
            'objects': ['A', 'B', 'C'],
        })

        assert_success_contains(self, result, "3 objects")
        fusion = doc.Objects[-1]
        self.assertEqual(len(list(fusion.Shapes)), 3)


class TestCutObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_no_base_or_tools(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({})
        assert_error_contains(self, result, "base", "tool")

    def test_missing_base(self):
        a = make_box_object("Tool1")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({
            'base': 'GhostBase', 'tools': ['Tool1'],
        })
        assert_error_contains(self, result, "base", "not found")

    def test_missing_tool_aborts(self):
        base = make_box_object("Base")
        doc = make_mock_doc([base])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({
            'base': 'Base', 'tools': ['GhostTool'],
        })
        assert_error_contains(self, result, "tool", "not found")

    def test_single_tool_assigned_directly(self):
        """One tool: cut.Tool = tool object (not list)."""
        base = make_box_object("Base")
        tool = make_box_object("Tool")
        doc = make_mock_doc([base, tool])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['Tool'], 'name': 'Hole',
        })

        cut = doc.Objects[-1]
        self.assertEqual(cut.Base, base)
        self.assertEqual(cut.Tool, tool)

    def test_multiple_tools_assigned_as_list(self):
        base = make_box_object("Base")
        t1 = make_box_object("T1")
        t2 = make_box_object("T2")
        doc = make_mock_doc([base, t1, t2])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['T1', 'T2'],
        })

        cut = doc.Objects[-1]
        self.assertEqual(cut.Base, base)
        self.assertEqual(list(cut.Tool), [t1, t2])

    def test_cut_hides_base_and_tools(self):
        base = make_box_object("Base")
        tool = make_box_object("Tool")
        doc = make_mock_doc([base, tool])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['Tool'],
        })

        self.assertFalse(base.Visibility)
        self.assertFalse(tool.Visibility)


class TestCommonObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_needs_at_least_two_objects(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.common_objects({'objects': ['Only']})
        assert_error_contains(self, result, "at least 2")

    def test_creates_multicommon(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.common_objects({
            'objects': ['A', 'B'], 'name': 'Intersection',
        })

        assert_success_contains(self, result, "Intersection", "2 objects")
        doc.addObject.assert_called_with("Part::MultiCommon", "Intersection")
        common = doc.Objects[-1]
        self.assertEqual(list(common.Shapes), [a, b])


if __name__ == '__main__':
    unittest.main()

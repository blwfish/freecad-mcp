"""Unit tests for TransformsHandler.

Covers move_object (relative + absolute, including the 4247599 fix),
rotate_object, copy_object, and array_object. Smallest of the part-
umbrella handlers (95 LOC) — used as a pilot to validate the shared-
helpers approach before expanding to primitives, boolean_ops, part_ops.
"""

import unittest

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_box_object,
    assert_error_contains,
    assert_success_contains,
    _Vec,
    _Placement,
)

from handlers.transforms import TransformsHandler


class TestMoveObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.move_object({'object_name': 'Box', 'x': 10})
        assert_error_contains(self, result, "no active document")

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.move_object({'object_name': 'Ghost', 'x': 10})
        assert_error_contains(self, result, "object not found", "ghost")

    def test_relative_move_adds_to_position(self):
        """Default behavior: relative=True adds offset to current Placement.Base."""
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(5, 5, 5))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'B', 'x': 10, 'y': -2, 'z': 3,
        })

        assert_success_contains(self, result, "Moved B by (10, -2, 3)")
        # 5+10, 5-2, 5+3
        self.assertEqual(box.Placement.Base.x, 15.0)
        self.assertEqual(box.Placement.Base.y, 3.0)
        self.assertEqual(box.Placement.Base.z, 8.0)

    def test_absolute_move_replaces_position(self):
        """relative=False sets Placement.Base directly (commit 4247599)."""
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(5, 5, 5))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'B', 'x': 100, 'y': 0, 'z': 0,
            'relative': False,
        })

        assert_success_contains(self, result, "Moved B to (100, 0, 0)")
        # Replaced, not added
        self.assertEqual(box.Placement.Base.x, 100.0)
        self.assertEqual(box.Placement.Base.y, 0.0)
        self.assertEqual(box.Placement.Base.z, 0.0)

    def test_label_fallback_used_for_lookup(self):
        """get_object label fallback reaches transform handlers (e62ebc5)."""
        box = make_box_object("Box001")  # internal name
        box.Label = "MyBox"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'MyBox', 'x': 5, 'y': 0, 'z': 0,
        })

        assert_success_contains(self, result, "Moved MyBox")
        self.assertEqual(box.Placement.Base.x, 5.0)


class TestRotateObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_rotate_around_z_default(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'B', 'angle': 45,
        })
        assert_success_contains(self, result, "Rotated B by 45", "Z")

    def test_rotate_around_x(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'B', 'angle': 90, 'axis': 'x',
        })
        assert_success_contains(self, result, "X")

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'Ghost', 'angle': 45,
        })
        assert_error_contains(self, result, "not found")


class TestCopyObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_copy_with_offset(self):
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.copy_object({
            'object_name': 'B', 'name': 'BCopy', 'x': 20, 'y': 0, 'z': 0,
        })

        assert_success_contains(self, result, "copy", "20")
        # copyObject was called once
        doc.copyObject.assert_called_once_with(box)
        # The new copy's Label is set to the requested name
        copy = doc.copyObject.return_value
        # Our make_mock_doc uses side_effect, not return_value;
        # the copy is the last addObject result
        self.assertGreaterEqual(len(doc.Objects), 2)
        new_obj = doc.Objects[-1]
        self.assertEqual(new_obj.Label, 'BCopy')


class TestArrayObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_array_count_creates_n_minus_1_copies(self):
        """count=4 means original + 3 new copies (4 total)."""
        box = make_box_object("B")
        box.Label = "B"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.array_object({
            'object_name': 'B', 'count': 4,
            'spacing_x': 15, 'spacing_y': 0, 'spacing_z': 0,
        })

        assert_success_contains(self, result, "array", "4")
        # 3 new copies (original is in array but not duplicated)
        self.assertEqual(doc.copyObject.call_count, 3)
        # Original + 3 copies = 4 objects total
        self.assertEqual(len(doc.Objects), 4)

    def test_array_spacing_x_only_moves_in_x(self):
        """spacing_y=spacing_z=0 ⇒ y and z of copies stay at 0."""
        box = make_box_object("B")
        box.Label = "B"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.array_object({
            'object_name': 'B', 'count': 3,
            'spacing_x': 10, 'spacing_y': 0, 'spacing_z': 0,
        })

        # Two new copies at (10,0,0) and (20,0,0)
        copies = [o for o in doc.Objects if o is not box]
        self.assertEqual(len(copies), 2)
        xs = sorted(c.Placement.Base.x for c in copies)
        self.assertEqual(xs, [10.0, 20.0])
        for c in copies:
            self.assertEqual(c.Placement.Base.y, 0.0)
            self.assertEqual(c.Placement.Base.z, 0.0)


if __name__ == '__main__':
    unittest.main()

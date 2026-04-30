"""Unit tests for DraftOpsHandler.

Covers clone, array (rectangular/ortho), polar_array, path_array,
point_array, text, and shape_string. KNOWN_ISSUES.md self-flagged this
handler as needing comprehensive tests; previously had 5% statement
coverage.

The array operations are the silent-regression risk — wrong axis or
off-by-one count produces a valid but incorrect layout. Tests verify
the Draft API is called with the right factor counts, axes, and
parameters.

Not covered here:
  * Draft module ImportError fallback — every method has the same
    wrapper, single test in TestImportError covers the pattern.
"""

import unittest
from unittest.mock import MagicMock, patch

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Draft,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    assert_error_contains,
    assert_success_contains,
)

from handlers.draft_ops import DraftOpsHandler


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

class TestClone(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.clone({'object_name': 'B'})
        assert_error_contains(self, result, "no active document")

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.clone({'object_name': 'Ghost'})
        assert_error_contains(self, result, "ghost", "not found")

    def test_clone_calls_draft_make_clone(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        clone_obj = MagicMock(Name="Clone001")
        clone_obj.Placement = MagicMock(Base=MagicMock())
        mock_Draft.make_clone = MagicMock(return_value=clone_obj)

        result = self.handler.clone({
            'object_name': 'B', 'x': 50, 'y': 0, 'z': 0,
        })

        mock_Draft.make_clone.assert_called_once_with(box)
        # Clone got placed at offset
        self.assertEqual(clone_obj.Placement.Base.x, 50)
        assert_success_contains(self, result, "Clone001", "B")


# ---------------------------------------------------------------------------
# Rectangular / ortho array
# ---------------------------------------------------------------------------

class TestArray(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_default_array_creates_2x1x1(self):
        """Default count_x=2, count_y=1, count_z=1 — total 2 instances."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        array_obj = MagicMock(Name="Array001")
        mock_Draft.make_ortho_array = MagicMock(return_value=array_obj)

        result = self.handler.array({'object_name': 'B'})

        mock_Draft.make_ortho_array.assert_called_once()
        kwargs = mock_Draft.make_ortho_array.call_args.kwargs
        self.assertEqual(kwargs['n_x'], 2)
        self.assertEqual(kwargs['n_y'], 1)
        self.assertEqual(kwargs['n_z'], 1)
        # Default intervals
        self.assertEqual(kwargs['v_x'].x, 100)
        self.assertEqual(kwargs['v_y'].y, 100)
        self.assertEqual(kwargs['v_z'].z, 100)
        assert_success_contains(self, result, "Array001", "2 instances",
                                "2x1x1")

    def test_3d_array_count_total(self):
        """3x2x4 array = 24 instances."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_ortho_array = MagicMock(return_value=MagicMock(Name="A"))

        result = self.handler.array({
            'object_name': 'B',
            'count_x': 3, 'count_y': 2, 'count_z': 4,
            'interval_x': 10, 'interval_y': 20, 'interval_z': 30,
        })

        kwargs = mock_Draft.make_ortho_array.call_args.kwargs
        self.assertEqual(kwargs['n_x'], 3)
        self.assertEqual(kwargs['n_y'], 2)
        self.assertEqual(kwargs['n_z'], 4)
        self.assertEqual(kwargs['v_x'].x, 10)
        self.assertEqual(kwargs['v_y'].y, 20)
        self.assertEqual(kwargs['v_z'].z, 30)
        assert_success_contains(self, result, "24 instances", "3x2x4")

    def test_array_uses_link_for_efficiency(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_ortho_array = MagicMock(return_value=MagicMock(Name="A"))

        self.handler.array({'object_name': 'B'})

        kwargs = mock_Draft.make_ortho_array.call_args.kwargs
        self.assertTrue(kwargs.get('use_link'),
                        "Array should use App::Link for efficiency")


# ---------------------------------------------------------------------------
# Polar array
# ---------------------------------------------------------------------------

class TestPolarArray(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_default_polar_360(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_polar_array = MagicMock(return_value=MagicMock(Name="P"))

        result = self.handler.polar_array({
            'object_name': 'B', 'count': 6, 'angle': 360,
        })

        kwargs = mock_Draft.make_polar_array.call_args.kwargs
        self.assertEqual(kwargs['number'], 6)
        self.assertEqual(kwargs['angle'], 360)
        # Default center is (0,0,0)
        center = kwargs['center']
        self.assertEqual(center.x, 0)
        self.assertEqual(center.y, 0)
        self.assertEqual(center.z, 0)
        assert_success_contains(self, result, "6 instances", "360")

    def test_polar_with_custom_center(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_polar_array = MagicMock(return_value=MagicMock(Name="P"))

        self.handler.polar_array({
            'object_name': 'B', 'count': 8, 'angle': 180,
            'center_x': 50, 'center_y': 50, 'center_z': 0,
        })

        kwargs = mock_Draft.make_polar_array.call_args.kwargs
        center = kwargs['center']
        self.assertEqual(center.x, 50)
        self.assertEqual(center.y, 50)
        self.assertEqual(kwargs['angle'], 180)


# ---------------------------------------------------------------------------
# Path array
# ---------------------------------------------------------------------------

class TestPathArray(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_missing_path(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.path_array({
            'object_name': 'B', 'path_name': 'NoSuchPath',
        })
        assert_error_contains(self, result, "path", "not found")

    def test_path_array_calls_draft_with_count_and_align(self):
        box = make_box_object("B")
        path = make_part_object("Curve")
        doc = make_mock_doc([box, path])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_path_array = MagicMock(return_value=MagicMock(Name="PA"))

        result = self.handler.path_array({
            'object_name': 'B', 'path_name': 'Curve',
            'count': 7, 'align': True,
        })

        args = mock_Draft.make_path_array.call_args
        # Positional: obj, path_obj
        self.assertEqual(args.args[0], box)
        self.assertEqual(args.args[1], path)
        self.assertEqual(args.kwargs['count'], 7)
        self.assertTrue(args.kwargs['align'])
        assert_success_contains(self, result, "7 instances", "Curve")


# ---------------------------------------------------------------------------
# Point array
# ---------------------------------------------------------------------------

class TestPointArray(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_missing_point_object(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.point_array({
            'object_name': 'B', 'point_object': 'NoSuchPoints',
        })
        assert_error_contains(self, result, "point", "not found")

    def test_point_count_reported_from_Points_attr(self):
        box = make_box_object("B")
        points = MagicMock()
        points.Name = "Pts"
        points.Label = "Pts"
        points.Points = [MagicMock(), MagicMock(), MagicMock()]  # 3 points
        # No Shape attribute (Points obj only)
        if hasattr(points, 'Shape'):
            del points.Shape
        doc = make_mock_doc([box, points])
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_point_array = MagicMock(return_value=MagicMock(Name="PA"))

        result = self.handler.point_array({
            'object_name': 'B', 'point_object': 'Pts',
        })

        assert_success_contains(self, result, "PA", "3 points")


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

class TestText(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_creates_text_with_single_line_input(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_text = MagicMock(return_value=MagicMock(Name="Label"))

        result = self.handler.text({
            'text': 'Hello', 'x': 10, 'y': 20, 'z': 0,
        })

        # First positional arg is a list of lines
        args = mock_Draft.make_text.call_args
        self.assertEqual(args.args[0], ['Hello'])
        assert_success_contains(self, result, "Hello", "Label", "10", "20")

    def test_creates_text_with_multi_line_input(self):
        """A list of strings preserves multi-line input."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        mock_Draft.make_text = MagicMock(return_value=MagicMock(Name="Label"))

        result = self.handler.text({
            'text': ['Line 1', 'Line 2'],
        })

        args = mock_Draft.make_text.call_args
        self.assertEqual(args.args[0], ['Line 1', 'Line 2'])
        # Result preview joins lines with /
        assert_success_contains(self, result, "Line 1", "Line 2")


# ---------------------------------------------------------------------------
# ImportError fallback (Draft module unavailable)
# ---------------------------------------------------------------------------

class TestShapeString(unittest.TestCase):
    """shape_string was previously broken — referenced os.path.basename
    without importing os. Regression test for that fix."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_no_font_returns_helpful_error(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        with patch.object(self.handler, 'find_font', return_value=''):
            result = self.handler.shape_string({'string': 'Hi'})
        assert_error_contains(self, result, "no font", ".ttf")

    def test_creates_shapestring_via_make_shapestring(self):
        """Happy path: find_font returns a path, Draft.make_shapestring
        produces an object, handler labels it and returns success.

        Regression: before the os import was added, this path raised
        NameError on os.path.basename(font) inside the success-message
        f-string."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        ss = MagicMock()
        ss.Name = "ShapeString001"
        ss.Placement = MagicMock(Base=MagicMock())
        mock_Draft.make_shapestring = MagicMock(return_value=ss)
        # Make sure the alternate names aren't picked up via the getattr
        # chain so we know which API we exercised.
        if hasattr(mock_Draft, 'make_shape_string'):
            del mock_Draft.make_shape_string
        if hasattr(mock_Draft, 'makeShapeString'):
            del mock_Draft.makeShapeString

        with patch.object(self.handler, 'find_font',
                          return_value='/fonts/Arial.ttf'):
            result = self.handler.shape_string({
                'string': 'Hello', 'size': 12, 'name': 'Logo',
                'x': 5, 'y': 10, 'z': 0,
            })

        # Success message uses os.path.basename — would have crashed
        # with NameError without the import fix.
        assert_success_contains(self, result, "Hello", "Arial.ttf",
                                "(5.0,10.0,0.0)", "size=12")
        # Custom Label was applied
        self.assertEqual(ss.Label, 'Logo')
        # Placement was set to the requested coordinates
        self.assertEqual(ss.Placement.Base.x, 5)
        self.assertEqual(ss.Placement.Base.y, 10)


class TestImportErrorFallback(unittest.TestCase):
    """Verify the ``except ImportError`` branch surfaces a helpful message.

    Each method wraps its Draft import in try/except — if Draft isn't
    available (e.g. a slim FreeCAD build), the user gets a clear message
    instead of a Python traceback. Single test covers the pattern; the
    same wrapping is used by every method.
    """

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(DraftOpsHandler)

    def test_clone_handles_missing_draft_module(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        # Simulate Draft import raising ImportError inside the method
        with patch.dict('sys.modules', {'Draft': None}):
            result = self.handler.clone({'object_name': 'B'})

        assert_error_contains(self, result, "draft module not available")


if __name__ == '__main__':
    unittest.main()

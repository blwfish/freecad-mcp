"""Unit tests for MeshOpsHandler.

Tests all 8 mesh operations with mocked FreeCAD modules.
Run with: python3 -m pytest tests/unit/test_mesh_ops.py -v
"""

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Mock FreeCAD modules before any handler imports
# ---------------------------------------------------------------------------

# Create mock FreeCAD module hierarchy
mock_FreeCAD = MagicMock()
mock_FreeCAD.GuiUp = False
mock_FreeCAD.Console = MagicMock()
mock_FreeCADGui = MagicMock()
mock_Part = MagicMock()
mock_Mesh = MagicMock()
mock_MeshPart = MagicMock()

sys.modules['FreeCAD'] = mock_FreeCAD
sys.modules['FreeCADGui'] = mock_FreeCADGui
sys.modules['Part'] = mock_Part
sys.modules['Mesh'] = mock_Mesh
sys.modules['MeshPart'] = mock_MeshPart

# Now we can import the handler
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))
from handlers.mesh_ops import MeshOpsHandler


def make_handler():
    """Create a MeshOpsHandler with mocked server and logging."""
    server = MagicMock()
    log_op = MagicMock()
    capture = MagicMock(return_value={})
    handler = MeshOpsHandler(server, log_op, capture)
    return handler


def make_mock_doc(objects=None):
    """Create a mock FreeCAD document."""
    doc = MagicMock()
    doc.Name = "TestDoc"
    doc.Objects = objects or []

    def get_object(name):
        for o in doc.Objects:
            if o.Name == name:
                return o
        return None

    doc.getObject = get_object
    return doc


def make_mesh_object(name="TestMesh", count_points=100, count_facets=200):
    """Create a mock Mesh::Feature object."""
    obj = MagicMock()
    obj.Name = name
    obj.TypeId = "Mesh::Feature"

    mesh = MagicMock()
    mesh.CountPoints = count_points
    mesh.CountFacets = count_facets
    mesh.Area = 5000.0
    mesh.Volume = 12000.0
    mesh.isSolid.return_value = True
    mesh.hasNonManifolds.return_value = False
    mesh.hasSelfIntersections.return_value = False
    mesh.hasInvalidPoints.return_value = False

    bb = MagicMock()
    bb.XLength = 50.0
    bb.YLength = 40.0
    bb.ZLength = 30.0
    bb.Center = MagicMock(x=25.0, y=20.0, z=15.0)
    mesh.BoundBox = bb
    mesh.Topology = ([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    mesh.copy.return_value = MagicMock(
        CountPoints=count_points,
        CountFacets=count_facets,
        BoundBox=bb,
    )

    obj.Mesh = mesh
    # Mesh objects don't have Shape
    del obj.Shape
    return obj


def make_part_object(name="TestPart"):
    """Create a mock Part::Feature object."""
    obj = MagicMock()
    obj.Name = name
    obj.TypeId = "Part::Feature"

    shape = MagicMock()
    bb = MagicMock()
    bb.XLength = 100.0
    bb.YLength = 80.0
    bb.ZLength = 60.0
    shape.BoundBox = bb
    shape.Volume = 480000.0
    shape.Shells = [MagicMock()]

    obj.Shape = shape
    # Part objects don't have Mesh
    del obj.Mesh
    return obj


class TestImportMesh(unittest.TestCase):
    """Tests for import_mesh operation."""

    def setUp(self):
        self.handler = make_handler()
        mock_FreeCAD.ActiveDocument = make_mock_doc()

    def test_missing_file_path(self):
        result = self.handler.import_mesh({})
        self.assertIn("file_path parameter required", result)

    def test_file_not_found(self):
        result = self.handler.import_mesh({'file_path': '/nonexistent/file.stl'})
        self.assertIn("File not found", result)

    def test_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False) as f:
            f.write(b"test")
            tmp_path = f.name
        try:
            result = self.handler.import_mesh({'file_path': tmp_path})
            self.assertIn("Unsupported mesh format", result)
        finally:
            os.unlink(tmp_path)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as f:
            f.write(b"test")
            tmp_path = f.name
        try:
            result = self.handler.import_mesh({'file_path': tmp_path})
            self.assertIn("No active document", result)
        finally:
            os.unlink(tmp_path)

    def test_successful_import(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        mesh_data = MagicMock()
        mesh_data.CountPoints = 100
        mesh_data.CountFacets = 200
        bb = MagicMock()
        bb.XLength = 50.0
        bb.YLength = 40.0
        bb.ZLength = 30.0
        mesh_data.BoundBox = bb
        mock_Mesh.Mesh.return_value = mesh_data

        added_obj = MagicMock()
        added_obj.Name = "terrain"
        doc.addObject.return_value = added_obj

        with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as f:
            f.write(b"test stl data")
            tmp_path = f.name
        try:
            result = self.handler.import_mesh({'file_path': tmp_path, 'name': 'terrain'})
            self.assertIn("Imported mesh", result)
            self.assertIn("Points: 100", result)
            self.assertIn("Facets: 200", result)
            doc.addObject.assert_called_once_with("Mesh::Feature", "terrain")
        finally:
            os.unlink(tmp_path)

    def test_name_derived_from_filename(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        mesh_data = MagicMock()
        mesh_data.CountPoints = 10
        mesh_data.CountFacets = 20
        mesh_data.BoundBox = MagicMock(XLength=1, YLength=1, ZLength=1)
        mock_Mesh.Mesh.return_value = mesh_data
        doc.addObject.return_value = MagicMock(Name="my_terrain_file")

        with tempfile.NamedTemporaryFile(suffix='.stl', prefix='my-terrain-file', delete=False) as f:
            f.write(b"data")
            tmp_path = f.name
        try:
            result = self.handler.import_mesh({'file_path': tmp_path})
            # Name should be sanitized (hyphens → underscores)
            call_args = doc.addObject.call_args
            name_arg = call_args[0][1]
            self.assertNotIn('-', name_arg)
        finally:
            os.unlink(tmp_path)

    def test_all_mesh_formats_accepted(self):
        """Verify all supported extensions pass format check."""
        for ext in ['.stl', '.obj', '.ply', '.off', '.amf', '.3mf']:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(b"data")
                tmp_path = f.name
            try:
                doc = make_mock_doc()
                mock_FreeCAD.ActiveDocument = doc
                mock_Mesh.Mesh.return_value = MagicMock(
                    CountPoints=10, CountFacets=20,
                    BoundBox=MagicMock(XLength=1, YLength=1, ZLength=1))
                doc.addObject.return_value = MagicMock(Name="test")
                result = self.handler.import_mesh({'file_path': tmp_path})
                self.assertNotIn("Unsupported", result, f"Format {ext} should be supported")
            finally:
                os.unlink(tmp_path)


class TestExportMesh(unittest.TestCase):
    """Tests for export_mesh operation."""

    def setUp(self):
        self.handler = make_handler()

    def test_missing_object_name(self):
        result = self.handler.export_mesh({'file_path': '/tmp/out.stl'})
        self.assertIn("object_name parameter required", result)

    def test_missing_file_path(self):
        result = self.handler.export_mesh({'object_name': 'Foo'})
        self.assertIn("file_path parameter required", result)

    def test_unsupported_format(self):
        doc = make_mock_doc([make_mesh_object("Foo")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.export_mesh({'object_name': 'Foo', 'file_path': '/tmp/out.step'})
        self.assertIn("Unsupported mesh format", result)

    def test_object_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.export_mesh({'object_name': 'Missing', 'file_path': '/tmp/out.stl'})
        self.assertIn("not found", result)

    def test_export_mesh_object(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.stl")
            # Mock the file creation
            mock_Mesh.export = MagicMock()
            # Create a dummy file so os.path.getsize works
            with open(out_path, 'w') as f:
                f.write("dummy")

            result = self.handler.export_mesh({
                'object_name': 'Terrain',
                'file_path': out_path
            })
            self.assertIn("Exported", result)
            mock_Mesh.export.assert_called_once()

    def test_export_part_object_tessellates(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc

        mock_MeshPart.meshFromShape.return_value = MagicMock()
        temp_obj = MagicMock(Name="_export_temp")
        doc.addObject.return_value = temp_obj
        mock_Mesh.export = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.stl")
            with open(out_path, 'w') as f:
                f.write("dummy")

            result = self.handler.export_mesh({
                'object_name': 'Box',
                'file_path': out_path,
                'linear_deflection': 0.05
            })
            self.assertIn("Exported", result)
            mock_MeshPart.meshFromShape.assert_called_once()
            # Verify temp object was cleaned up
            doc.removeObject.assert_called_once_with("_export_temp")

    def test_no_shape_or_mesh(self):
        """Object with neither Mesh nor Shape should error."""
        obj = MagicMock()
        obj.Name = "Datum"
        del obj.Mesh
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.export_mesh({
            'object_name': 'Datum',
            'file_path': '/tmp/out.stl'
        })
        self.assertIn("no Mesh or Shape", result)


class TestMeshToSolid(unittest.TestCase):
    """Tests for mesh_to_solid operation."""

    def setUp(self):
        sys.modules['Part'] = mock_Part  # conftest autouse may replace it; restore ours
        self.handler = make_handler()

    def test_missing_object_name(self):
        result = self.handler.mesh_to_solid({})
        self.assertIn("object_name parameter required", result)

    def test_object_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mesh_to_solid({'object_name': 'Missing'})
        self.assertIn("not found", result)

    def test_not_a_mesh_object(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mesh_to_solid({'object_name': 'Box'})
        self.assertIn("not a mesh object", result)

    def test_successful_conversion(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        # Mock Part.Shape() constructor and makeSolid
        mock_shape = MagicMock()
        mock_shape.removeSplitter.return_value = mock_shape
        mock_Part.Shape.return_value = mock_shape
        mock_Part.makeSolid.reset_mock()
        mock_Part.makeSolid.side_effect = None

        # Solid needs real numeric attributes for f-string :.2f formatting
        solid = types.SimpleNamespace(
            BoundBox=types.SimpleNamespace(XLength=50.0, YLength=40.0, ZLength=30.0),
            Volume=60000.0,
        )
        mock_Part.makeSolid.return_value = solid

        solid_obj = MagicMock(Name="Terrain_Solid")
        doc.addObject.return_value = solid_obj

        result = self.handler.mesh_to_solid({
            'object_name': 'Terrain',
            'tolerance': 0.05
        })
        self.assertIn("Converted mesh", result)
        self.assertIn("Terrain_Solid", result)
        mock_shape.makeShapeFromMesh.assert_called_once()

    def test_custom_name(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        mock_shape = MagicMock()
        mock_shape.removeSplitter.return_value = mock_shape
        mock_Part.Shape.return_value = mock_shape
        mock_Part.makeSolid.reset_mock()
        mock_Part.makeSolid.side_effect = None
        mock_Part.makeSolid.return_value = types.SimpleNamespace(
            BoundBox=types.SimpleNamespace(XLength=1.0, YLength=1.0, ZLength=1.0),
            Volume=1.0)
        doc.addObject.return_value = MagicMock(Name="MySolid")

        result = self.handler.mesh_to_solid({
            'object_name': 'Terrain',
            'name': 'MySolid'
        })
        doc.addObject.assert_called_with("Part::Feature", "MySolid")

    def test_fallback_to_sewing(self):
        """When makeSolid fails, should try sewing first."""
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        mock_shape = MagicMock()
        mock_shape.removeSplitter.return_value = mock_shape
        mock_shape.Shells = [MagicMock()]
        mock_Part.Shape.return_value = mock_shape

        # First makeSolid fails, second succeeds (after sewing)
        solid = types.SimpleNamespace(
            BoundBox=types.SimpleNamespace(XLength=1.0, YLength=1.0, ZLength=1.0),
            Volume=1.0)
        mock_Part.makeSolid.reset_mock()
        mock_Part.makeSolid.side_effect = [Exception("Failed"), solid]
        doc.addObject.return_value = MagicMock(Name="Terrain_Solid")

        result = self.handler.mesh_to_solid({'object_name': 'Terrain'})
        self.assertIn("Converted mesh", result)
        self.assertIn("sewing", result)


class TestGetMeshInfo(unittest.TestCase):
    """Tests for get_mesh_info operation."""

    def setUp(self):
        self.handler = make_handler()

    def test_missing_object_name(self):
        result = self.handler.get_mesh_info({})
        self.assertIn("object_name parameter required", result)

    def test_not_a_mesh(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_mesh_info({'object_name': 'Box'})
        self.assertIn("not a mesh object", result)

    def test_healthy_mesh(self):
        mesh_obj = make_mesh_object("Good")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_mesh_info({'object_name': 'Good'})
        self.assertIn("Points: 100", result)
        self.assertIn("Facets: 200", result)
        self.assertIn("Is manifold: True", result)
        self.assertIn("Is watertight: True", result)
        self.assertIn("Health: OK", result)

    def test_unhealthy_mesh(self):
        mesh_obj = make_mesh_object("Bad")
        mesh_obj.Mesh.hasNonManifolds.return_value = True
        mesh_obj.Mesh.hasSelfIntersections.return_value = True
        mesh_obj.Mesh.isSolid.return_value = False
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_mesh_info({'object_name': 'Bad'})
        self.assertIn("Is manifold: False", result)
        self.assertIn("Has self-intersections: True", result)
        self.assertIn("ISSUES DETECTED", result)


class TestImportFile(unittest.TestCase):
    """Tests for import_file (generic format auto-detect)."""

    def setUp(self):
        sys.modules['Part'] = mock_Part  # conftest autouse may replace it; restore ours
        self.handler = make_handler()

    def test_missing_file_path(self):
        result = self.handler.import_file({})
        self.assertIn("file_path parameter required", result)

    def test_file_not_found(self):
        result = self.handler.import_file({'file_path': '/no/such/file.step'})
        self.assertIn("File not found", result)

    def test_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix='.dwg', delete=False) as f:
            f.write(b"data")
            tmp_path = f.name
        try:
            result = self.handler.import_file({'file_path': tmp_path})
            self.assertIn("Unsupported format", result)
        finally:
            os.unlink(tmp_path)

    def test_delegates_stl_to_import_mesh(self):
        """STL files should delegate to import_mesh."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        mock_Mesh.Mesh.return_value = MagicMock(
            CountPoints=10, CountFacets=20,
            BoundBox=MagicMock(XLength=1, YLength=1, ZLength=1))
        doc.addObject.return_value = MagicMock(Name="test")

        with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as f:
            f.write(b"data")
            tmp_path = f.name
        try:
            result = self.handler.import_file({'file_path': tmp_path})
            # Should have called Mesh.Mesh (via import_mesh)
            self.assertIn("Imported mesh", result)
        finally:
            os.unlink(tmp_path)

    def test_step_import(self):
        """STEP files should use Part.insert."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        # Simulate Part.insert adding an object
        new_obj = make_part_object("ImportedPart")
        def add_objects_side_effect(*args):
            doc.Objects.append(new_obj)
        mock_Part.insert = MagicMock(side_effect=add_objects_side_effect)

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as f:
            f.write(b"ISO-10303 data")
            tmp_path = f.name
        try:
            result = self.handler.import_file({'file_path': tmp_path})
            mock_Part.insert.assert_called_once()
            self.assertIn("Imported", result)
        finally:
            os.unlink(tmp_path)

    def test_fcstd_opens_document(self):
        """FCStd files should open as FreeCAD documents."""
        mock_doc = MagicMock()
        mock_doc.Name = "MyProject"
        mock_doc.Objects = [MagicMock(), MagicMock(), MagicMock()]
        mock_FreeCAD.openDocument.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix='.fcstd', delete=False) as f:
            f.write(b"data")
            tmp_path = f.name
        try:
            result = self.handler.import_file({'file_path': tmp_path})
            self.assertIn("MyProject", result)
            self.assertIn("3 object(s)", result)
        finally:
            os.unlink(tmp_path)


class TestExportFile(unittest.TestCase):
    """Tests for export_file (generic format auto-detect)."""

    def setUp(self):
        sys.modules['Part'] = mock_Part  # conftest autouse may replace it; restore ours
        self.handler = make_handler()

    def test_missing_params(self):
        result = self.handler.export_file({})
        self.assertIn("object_name parameter required", result)

        result = self.handler.export_file({'object_name': 'Foo'})
        self.assertIn("file_path parameter required", result)

    def test_unsupported_format(self):
        doc = make_mock_doc([make_part_object("Box")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.export_file({
            'object_name': 'Box',
            'file_path': '/tmp/out.dwg'
        })
        self.assertIn("Unsupported export format", result)

    def test_step_export(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc
        mock_Part.export = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.step")
            with open(out_path, 'w') as f:
                f.write("dummy")

            result = self.handler.export_file({
                'object_name': 'Box',
                'file_path': out_path
            })
            self.assertIn("Exported", result)
            mock_Part.export.assert_called_once()

    def test_mesh_without_shape_cant_export_step(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.export_file({
            'object_name': 'Terrain',
            'file_path': '/tmp/out.step'
        })
        self.assertIn("no Shape", result)

    def test_stl_delegates_to_export_mesh(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        mock_Mesh.export = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.stl")
            with open(out_path, 'w') as f:
                f.write("dummy")

            result = self.handler.export_file({
                'object_name': 'Terrain',
                'file_path': out_path
            })
            self.assertIn("Exported", result)


class TestValidateMesh(unittest.TestCase):
    """Tests for validate_mesh operation."""

    def setUp(self):
        self.handler = make_handler()

    def test_missing_object_name(self):
        result = self.handler.validate_mesh({})
        self.assertIn("object_name parameter required", result)

    def test_not_a_mesh(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.validate_mesh({'object_name': 'Box'})
        self.assertIn("not a mesh object", result)

    def test_valid_mesh(self):
        mesh_obj = make_mesh_object("Good")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.validate_mesh({'object_name': 'Good'})
        self.assertIn("passed all validation", result)
        self.assertIn("VALID", result)

    def test_invalid_mesh_no_repair(self):
        mesh_obj = make_mesh_object("Bad")
        mesh_obj.Mesh.hasNonManifolds.return_value = True
        mesh_obj.Mesh.isSolid.return_value = False
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.validate_mesh({'object_name': 'Bad'})
        self.assertIn("non-manifold", result)
        self.assertIn("not watertight", result)
        self.assertIn("auto_repair=true", result)

    def test_auto_repair(self):
        mesh_obj = make_mesh_object("Bad", count_points=105, count_facets=210)
        mesh_obj.Mesh.hasNonManifolds.return_value = True
        mesh_obj.Mesh.hasSelfIntersections.return_value = True
        mesh_obj.Mesh.isSolid.return_value = False

        # After repair, the copy is clean
        repaired = mesh_obj.Mesh.copy.return_value
        repaired.CountPoints = 100
        repaired.CountFacets = 200
        repaired.hasNonManifolds.return_value = False
        repaired.hasSelfIntersections.return_value = False
        repaired.isSolid.return_value = True

        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.validate_mesh({
            'object_name': 'Bad',
            'auto_repair': True
        })
        self.assertIn("Attempting repairs", result)
        self.assertIn("harmonized normals", result)
        self.assertIn("All issues resolved", result)


class TestSimplifyMesh(unittest.TestCase):
    """Tests for simplify_mesh operation."""

    def setUp(self):
        self.handler = make_handler()

    def test_missing_object_name(self):
        result = self.handler.simplify_mesh({})
        self.assertIn("object_name parameter required", result)

    def test_not_a_mesh(self):
        part_obj = make_part_object("Box")
        doc = make_mock_doc([part_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.simplify_mesh({
            'object_name': 'Box',
            'target_count': 50
        })
        self.assertIn("not a mesh object", result)

    def test_no_target_or_reduction(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.simplify_mesh({'object_name': 'Terrain'})
        self.assertIn("Provide either target_count", result)

    def test_target_count_too_large(self):
        mesh_obj = make_mesh_object("Terrain", count_facets=200)
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.simplify_mesh({
            'object_name': 'Terrain',
            'target_count': 300
        })
        self.assertIn("must be less than", result)

    def test_reduction_out_of_range(self):
        mesh_obj = make_mesh_object("Terrain")
        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.simplify_mesh({
            'object_name': 'Terrain',
            'reduction': 1.5
        })
        self.assertIn("must be between 0 and 1", result)

    def test_simplify_with_target_count(self):
        mesh_obj = make_mesh_object("Terrain", count_facets=1000)
        copy = mesh_obj.Mesh.copy.return_value
        copy.CountFacets = 500
        copy.CountPoints = 260

        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        doc.addObject.return_value = MagicMock(Name="Terrain_Simplified")

        result = self.handler.simplify_mesh({
            'object_name': 'Terrain',
            'target_count': 500
        })
        self.assertIn("Simplified", result)
        self.assertIn("Before: 1000", result)
        self.assertIn("After:  500", result)
        self.assertIn("50.0% reduction", result)
        copy.decimate.assert_called_once_with(500)

    def test_simplify_with_reduction_ratio(self):
        mesh_obj = make_mesh_object("Terrain", count_facets=1000)
        copy = mesh_obj.Mesh.copy.return_value
        copy.CountFacets = 250
        copy.CountPoints = 130

        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        doc.addObject.return_value = MagicMock(Name="Terrain_Simplified")

        result = self.handler.simplify_mesh({
            'object_name': 'Terrain',
            'reduction': 0.25
        })
        # 0.25 * 1000 = 250 target
        copy.decimate.assert_called_once_with(250)

    def test_custom_name(self):
        mesh_obj = make_mesh_object("Terrain", count_facets=1000)
        copy = mesh_obj.Mesh.copy.return_value
        copy.CountFacets = 500
        copy.CountPoints = 260

        doc = make_mock_doc([mesh_obj])
        mock_FreeCAD.ActiveDocument = doc
        doc.addObject.return_value = MagicMock(Name="LowRes")

        self.handler.simplify_mesh({
            'object_name': 'Terrain',
            'target_count': 500,
            'name': 'LowRes'
        })
        doc.addObject.assert_called_with("Mesh::Feature", "LowRes")


class TestFormatConstants(unittest.TestCase):
    """Test that format constants are correct."""

    def test_mesh_formats(self):
        expected = {'.stl', '.obj', '.ply', '.off', '.amf', '.3mf'}
        self.assertEqual(MeshOpsHandler.MESH_FORMATS, expected)

    def test_cad_formats(self):
        expected = {'.step', '.stp', '.iges', '.igs', '.brep', '.brp'}
        self.assertEqual(MeshOpsHandler.CAD_FORMATS, expected)


if __name__ == '__main__':
    unittest.main()

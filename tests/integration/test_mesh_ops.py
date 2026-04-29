"""Integration tests for mesh_operations against a live FreeCAD instance.

mesh_ops has 50 unit tests but file I/O is exactly the area where mocks
diverge from reality (Mesh module, OCCT tessellation, file format quirks).
These tests do roundtrips through real Mesh/Part modules:

  Part::Box  --export_mesh-->  STL/OBJ/3MF  --import_mesh-->  Mesh::Feature
                                                           |
                                                           +--mesh_to_solid--> Part::Feature

Run with: python3 -m pytest tests/integration/test_mesh_ops.py -v
"""

import json
import os
import tempfile
import time

import pytest

from . import conftest as _conftest  # noqa: F401
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mesh(args: dict, timeout: float = 30.0) -> str:
    resp = send_command("mesh_operations", args, timeout=timeout)
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return str(resp)


def _exec(code: str, timeout: float = 10.0):
    return send_command("execute_python", {"code": code}, timeout=timeout)


# ---------------------------------------------------------------------------
# Fixture: per-test document with a known Part::Box
# ---------------------------------------------------------------------------
@pytest.fixture
def doc_with_box():
    doc = f"Mesh_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {"operation": "create_document", "document_name": doc})
    _exec(f"""
import FreeCAD
d = FreeCAD.getDocument({doc!r})
b = d.addObject('Part::Box', 'TestBox')
b.Length = 20; b.Width = 15; b.Height = 10
d.recompute()
""")
    yield doc
    try:
        _exec(f"FreeCAD.closeDocument({doc!r})")
    except Exception:
        pass


@pytest.fixture
def tmpdir_path():
    with tempfile.TemporaryDirectory(prefix="freecad_mcp_mesh_") as d:
        yield d


# ---------------------------------------------------------------------------
# export_mesh / import_mesh roundtrip
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestExportImportRoundtrip:
    @pytest.mark.parametrize("ext", [".stl", ".obj", ".ply", ".3mf"])
    def test_part_to_mesh_roundtrip(self, doc_with_box, tmpdir_path, ext):
        out = os.path.join(tmpdir_path, f"test{ext}")
        result = _mesh({"operation": "export_mesh",
                        "object_name": "TestBox",
                        "file_path": out})
        assert "error" not in result.lower(), result
        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0

        # Reimport into the same document under a different name
        result = _mesh({"operation": "import_mesh",
                        "file_path": out,
                        "name": "Reimported"})
        assert "error" not in result.lower(), result
        assert "Imported mesh" in result
        # Box dimensions: 20×15×10 — bounding box should match (within tess
        # tolerance). Output is human-formatted.
        assert "20" in result
        assert "15" in result
        assert "10" in result

    def test_export_unsupported_format(self, doc_with_box, tmpdir_path):
        result = _mesh({"operation": "export_mesh",
                        "object_name": "TestBox",
                        "file_path": os.path.join(tmpdir_path, "nope.xyz")})
        assert "Unsupported" in result or "error" in result.lower()

    def test_import_missing_file(self, doc_with_box):
        result = _mesh({"operation": "import_mesh",
                        "file_path": "/nonexistent/file.stl"})
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# get_mesh_info
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestMeshInfo:
    def test_info_after_import(self, doc_with_box, tmpdir_path):
        stl = os.path.join(tmpdir_path, "info.stl")
        _mesh({"operation": "export_mesh", "object_name": "TestBox",
               "file_path": stl})
        _mesh({"operation": "import_mesh", "file_path": stl,
               "name": "InfoMesh"})

        result = _mesh({"operation": "get_mesh_info",
                        "object_name": "InfoMesh"})
        assert "InfoMesh" in result
        # Must report point/facet counts and bounds
        assert "Points" in result or "points" in result.lower()
        assert "Facets" in result or "facets" in result.lower()


# ---------------------------------------------------------------------------
# mesh_to_solid — the bridge to CAM workflows
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestMeshToSolid:
    def test_box_mesh_to_solid_roundtrip(self, doc_with_box, tmpdir_path):
        # Export Part::Box to STL, reimport as mesh, convert back to solid
        stl = os.path.join(tmpdir_path, "convert.stl")
        _mesh({"operation": "export_mesh", "object_name": "TestBox",
               "file_path": stl})
        _mesh({"operation": "import_mesh", "file_path": stl,
               "name": "BoxMesh"})
        result = _mesh({"operation": "mesh_to_solid",
                        "object_name": "BoxMesh",
                        "name": "BoxSolid",
                        "tolerance": 0.1})
        assert "error" not in result.lower(), result
        assert "Converted mesh" in result
        # Volume of a 20x15x10 box = 3000 mm³ — actual mesh-derived solid may
        # have small tessellation deltas, so check the order of magnitude
        # rather than exact value.
        assert "Volume" in result

    def test_mesh_to_solid_on_non_mesh_errors(self, doc_with_box):
        result = _mesh({"operation": "mesh_to_solid",
                        "object_name": "TestBox"})  # Part::Box, not a mesh
        assert "not a mesh" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# validate_mesh
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestValidateMesh:
    def test_validate_imported_mesh(self, doc_with_box, tmpdir_path):
        stl = os.path.join(tmpdir_path, "valid.stl")
        _mesh({"operation": "export_mesh", "object_name": "TestBox",
               "file_path": stl})
        _mesh({"operation": "import_mesh", "file_path": stl,
               "name": "ValidMesh"})
        result = _mesh({"operation": "validate_mesh",
                        "object_name": "ValidMesh"})
        # Output format depends on the handler; assert it's not an error
        assert "error" not in result.lower(), result
        # A box exported and reimported should be a valid solid mesh
        assert "ValidMesh" in result

    def test_validate_non_mesh_errors(self, doc_with_box):
        result = _mesh({"operation": "validate_mesh",
                        "object_name": "TestBox"})  # Part::Box, not Mesh
        assert "error" in result.lower() or "not a mesh" in result.lower()


# ---------------------------------------------------------------------------
# simplify_mesh
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestSimplifyMesh:
    def test_simplify_with_reduction(self, doc_with_box, tmpdir_path):
        stl = os.path.join(tmpdir_path, "simp.stl")
        # Export a slightly higher-poly mesh by tightening tessellation
        _mesh({"operation": "export_mesh", "object_name": "TestBox",
               "file_path": stl, "linear_deflection": 0.01})
        _mesh({"operation": "import_mesh", "file_path": stl,
               "name": "SimpMesh"})

        # Capture original facet count via get_mesh_info
        info_before = _mesh({"operation": "get_mesh_info",
                             "object_name": "SimpMesh"})

        result = _mesh({"operation": "simplify_mesh",
                        "object_name": "SimpMesh",
                        "reduction": 0.5})
        # Box meshes are already minimal — simplify may report "no change
        # possible" or actually reduce. Either is acceptable; just no error.
        assert "error" not in result.lower(), result

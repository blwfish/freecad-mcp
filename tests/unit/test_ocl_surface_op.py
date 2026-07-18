"""Unit tests for ocl_surface_op._load_stl.

ocl_surface_op.py previously had zero test coverage (H15 finding). This
file covers the binary-STL format validation added to close C11: the
parser never checked the file was actually binary STL before
struct-unpacking it, and never cross-validated the declared triangle count
against the actual file size.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

# Triggers sys.modules mocking for FreeCAD/Path/etc before ocl_surface_op's
# module-level `import FreeCAD` / `import Path` resolve.
from tests.unit._freecad_mocks import mock_FreeCAD, reset_mocks  # noqa: E402

import pytest
from unittest.mock import MagicMock

import ocl_surface_op  # noqa: E402


# ---------------------------------------------------------------------------
# Binary STL file builder
# ---------------------------------------------------------------------------

def _write_binary_stl(path, triangles, declared_count=None, header=b"", trailing_garbage=b""):
    """Write a binary STL file.

    triangles: list of 9-float tuples (v1xyz, v2xyz, v3xyz).
    declared_count: override the header's triangle count (for
        mismatch-testing); defaults to len(triangles).
    """
    n = len(triangles) if declared_count is None else declared_count
    with open(path, "wb") as f:
        f.write(header.ljust(80, b"\x00")[:80])
        f.write(struct.pack("<I", n))
        for tri in triangles:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))  # normal (ignored)
            f.write(struct.pack("<9f", *tri))
            f.write(struct.pack("<H", 0))  # attribute byte count
        f.write(trailing_garbage)


def _write_ascii_stl(path):
    content = (
        "solid test\n"
        "  facet normal 0 0 0\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid test\n"
    )
    with open(path, "w") as f:
        f.write(content)


def _fake_ocl():
    """Minimal ocl module stand-in: STLSurf/Triangle/Point are all just
    call-recording MagicMocks — _load_stl only needs to be able to call
    them, not get real OCL geometry back."""
    ocl = MagicMock()
    ocl.STLSurf.return_value = MagicMock()
    return ocl


@pytest.fixture(autouse=True)
def _reset():
    reset_mocks()


class TestLoadStlValidBinary:
    def test_parses_triangles_and_computes_bbox(self, tmp_path):
        path = str(tmp_path / "cube_corner.stl")
        triangles = [
            (0, 0, 0, 10, 0, 0, 0, 10, 0),
            (10, 0, 0, 10, 10, 0, 0, 10, 0),
        ]
        _write_binary_stl(path, triangles)
        ocl = _fake_ocl()

        stl_surf, x_min, x_max, y_min, y_max = ocl_surface_op._load_stl(path, ocl)

        assert ocl.STLSurf.return_value.addTriangle.call_count == 2
        assert x_min == 0
        assert x_max == 10
        assert y_min == 0
        assert y_max == 10

    def test_exact_boundary_size_match_is_accepted(self, tmp_path):
        """file_size == expected_size exactly (84 + 50*n) must pass — the
        boundary this validation is built around, not just clearly-wrong
        sizes on either side."""
        path = str(tmp_path / "one_tri.stl")
        _write_binary_stl(path, [(0, 0, 0, 1, 0, 0, 0, 1, 0)])
        assert os.path.getsize(path) == 84 + 50 * 1

        ocl = _fake_ocl()
        ocl_surface_op._load_stl(path, ocl)  # must not raise


class TestLoadStlRejectsNonBinary:
    def test_ascii_stl_rejected_with_clear_message(self, tmp_path):
        path = str(tmp_path / "ascii.stl")
        _write_ascii_stl(path)

        with pytest.raises(ValueError, match="ASCII"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_size_mismatch_non_ascii_rejected(self, tmp_path):
        """Declared count doesn't match actual file size, and the header
        doesn't start with 'solid' — a corrupt/non-STL binary file, not an
        ASCII STL. Must give the size-mismatch reason, not the ASCII one."""
        path = str(tmp_path / "mismatch.stl")
        _write_binary_stl(path, [(0, 0, 0, 1, 0, 0, 0, 1, 0)], declared_count=5)

        with pytest.raises(ValueError, match="does not match declared triangle count"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_trailing_extra_bytes_rejected(self, tmp_path):
        """Extra bytes past the declared triangle count used to be silently
        never read — now caught by the same size check."""
        path = str(tmp_path / "trailing.stl")
        _write_binary_stl(
            path, [(0, 0, 0, 1, 0, 0, 0, 1, 0)], trailing_garbage=b"\x00" * 100
        )

        with pytest.raises(ValueError, match="does not match declared triangle count"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_file_too_small_for_header_rejected(self, tmp_path):
        path = str(tmp_path / "tiny.stl")
        with open(path, "wb") as f:
            f.write(b"\x00" * 10)

        with pytest.raises(ValueError, match="too small"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_zero_triangles_rejected(self, tmp_path):
        """An empty-but-correctly-sized binary STL (84 bytes, count=0) must
        still hit the pre-existing 'no triangles' check, not the new
        size-mismatch path — size validation happens after this check."""
        path = str(tmp_path / "empty.stl")
        _write_binary_stl(path, [])
        assert os.path.getsize(path) == 84

        with pytest.raises(ValueError, match="no triangles"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_random_non_stl_file_rejected(self, tmp_path):
        """A file that's neither ASCII-STL-prefixed nor size-matching binary
        STL (e.g. some other 200-byte binary format) must not be silently
        parsed as garbage triangles."""
        path = str(tmp_path / "random.bin")
        with open(path, "wb") as f:
            f.write(os.urandom(200))

        with pytest.raises(ValueError):
            ocl_surface_op._load_stl(path, _fake_ocl())

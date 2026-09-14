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

    def test_declared_count_exceeding_sanity_cap_rejected(self, tmp_path, monkeypatch):
        """A binary STL whose declared triangle count exceeds the sanity
        cap is rejected with a clear message, even when its size correctly
        matches that count (so the size cross-check alone wouldn't catch
        it). The cap is monkeypatched down so this doesn't require
        actually writing a multi-hundred-MB fixture file."""
        monkeypatch.setattr(ocl_surface_op, "_MAX_STL_TRIANGLES", 2)
        path = str(tmp_path / "too_many.stl")
        _write_binary_stl(path, [
            (0, 0, 0, 1, 0, 0, 0, 1, 0),
            (0, 0, 0, 1, 0, 0, 0, 1, 0),
            (0, 0, 0, 1, 0, 0, 0, 1, 0),
        ])

        with pytest.raises(ValueError, match="sanity limit"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_declared_count_at_sanity_cap_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ocl_surface_op, "_MAX_STL_TRIANGLES", 2)
        path = str(tmp_path / "at_cap.stl")
        _write_binary_stl(path, [
            (0, 0, 0, 1, 0, 0, 0, 1, 0),
            (0, 0, 0, 1, 0, 0, 0, 1, 0),
        ])

        ocl_surface_op._load_stl(path, _fake_ocl())  # must not raise

    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_vertex_coordinate_rejected(self, tmp_path, bad_value):
        """A NaN vertex coordinate silently no-ops every bounds comparison
        (`x < x_min` is always False when x is NaN), leaving the computed
        bounding box wrong with no error; an Inf coordinate that survives
        into y_max can make _build_zigzag_scan's scan-line loop
        (`while y <= y_max + 1e-9`) never terminate."""
        path = str(tmp_path / "nonfinite.stl")
        _write_binary_stl(path, [(0, 0, 0, 1, 0, 0, bad_value, 1, 0)])

        with pytest.raises(ValueError, match="[Nn]on-finite"):
            ocl_surface_op._load_stl(path, _fake_ocl())

    def test_all_finite_vertex_coordinates_accepted(self, tmp_path):
        path = str(tmp_path / "finite.stl")
        _write_binary_stl(path, [(0, 0, 0, 1, 0, 0, 0, 1, 0)])

        ocl_surface_op._load_stl(path, _fake_ocl())  # must not raise


# ---------------------------------------------------------------------------
# _do_execute: StlFile is a persisted document property, re-read on every
# recompute (including after a document reload from a different machine, or
# a direct property edit) -- it must be re-validated against the same
# home/tmp/Volumes allowlist enforced at creation time in
# cam_ops.surface_stl, not merely checked for existence.
# ---------------------------------------------------------------------------

class TestDoExecuteStlFilePathValidation:
    def _make_obj(self, stl_file):
        obj = MagicMock()
        obj.StlFile = stl_file
        return obj

    def _patch_opencamlib(self, monkeypatch):
        """_do_execute lazily imports `from opencamlib import ocl` before
        touching StlFile at all -- stub the module out so the import
        succeeds regardless of whether opencamlib is actually installed."""
        fake_opencamlib = MagicMock()
        fake_opencamlib.ocl = MagicMock()
        monkeypatch.setitem(sys.modules, "opencamlib", fake_opencamlib)

    def test_path_outside_allowlist_rejected(self, monkeypatch):
        self._patch_opencamlib(monkeypatch)
        proxy = ocl_surface_op.OCLSurfaceProxy.__new__(ocl_surface_op.OCLSurfaceProxy)
        obj = self._make_obj("/etc/passwd")

        with pytest.raises(ValueError, match="outside allowed directories"):
            proxy._do_execute(obj)

    def test_path_inside_allowlist_proceeds_past_validation(self, monkeypatch, tmp_path):
        """A path under an allowed dir (tmp) must clear the allowlist check
        and fail later for a legitimate reason (file doesn't exist), not be
        rejected as outside the allowlist."""
        self._patch_opencamlib(monkeypatch)
        proxy = ocl_surface_op.OCLSurfaceProxy.__new__(ocl_surface_op.OCLSurfaceProxy)
        missing = str(tmp_path / "does_not_exist.stl")
        obj = self._make_obj(missing)

        with pytest.raises(FileNotFoundError):
            proxy._do_execute(obj)

    def test_empty_stl_file_skips_validation_and_returns(self, monkeypatch):
        """An unset StlFile is a legitimate no-op (warn + return), not a
        rejected path -- must not be affected by this change."""
        self._patch_opencamlib(monkeypatch)
        proxy = ocl_surface_op.OCLSurfaceProxy.__new__(ocl_surface_op.OCLSurfaceProxy)
        obj = self._make_obj("")

        proxy._do_execute(obj)  # must not raise


class TestDoExecuteNumericParamValidation:
    """safe_z/cut_feed/plunge_feed had no bounds check at all -- unlike
    tool_dia/stepover/sampling, which are validated a few lines above them
    in the same function. Not fed into OCL itself (no C++ hang/crash risk
    like the OCL-bound params), but a non-positive value here still
    produces unsafe or degenerate G-code: zero/negative SafeHeight gives
    the rapid retract move no real clearance above the work."""

    def _make_valid_obj(self, stl_file, **overrides):
        obj = MagicMock()
        obj.StlFile = stl_file
        obj.ToolDiameter = overrides.get("ToolDiameter", 3.0)
        obj.StepOver = overrides.get("StepOver", 0.5)
        obj.SampleInterval = overrides.get("SampleInterval", 0.5)
        obj.SafeHeight = overrides.get("SafeHeight", 8.0)
        obj.CutFeed = overrides.get("CutFeed", 400.0)
        obj.PlungeFeed = overrides.get("PlungeFeed", 150.0)
        return obj

    def _patch_opencamlib(self, monkeypatch):
        fake_opencamlib = MagicMock()
        fake_opencamlib.ocl = _fake_ocl()
        monkeypatch.setitem(sys.modules, "opencamlib", fake_opencamlib)

    def _valid_stl_path(self, tmp_path):
        path = str(tmp_path / "valid.stl")
        _write_binary_stl(path, [(0, 0, 0, 1, 0, 0, 0, 1, 0)])
        return path

    @pytest.mark.parametrize("field,value", [
        ("SafeHeight", 0), ("SafeHeight", -1),
        ("CutFeed", 0), ("CutFeed", -1),
        ("PlungeFeed", 0), ("PlungeFeed", -1),
    ])
    def test_non_positive_value_rejected(self, monkeypatch, tmp_path, field, value):
        self._patch_opencamlib(monkeypatch)
        proxy = ocl_surface_op.OCLSurfaceProxy.__new__(ocl_surface_op.OCLSurfaceProxy)
        obj = self._make_valid_obj(self._valid_stl_path(tmp_path), **{field: value})

        with pytest.raises(ValueError, match=field):
            proxy._do_execute(obj)

    def test_valid_positive_values_pass_validation(self, monkeypatch, tmp_path):
        """Confirms the new checks don't reject legitimate values -- must
        proceed past validation into the (mocked) OCL pipeline, not raise."""
        self._patch_opencamlib(monkeypatch)
        proxy = ocl_surface_op.OCLSurfaceProxy.__new__(ocl_surface_op.OCLSurfaceProxy)
        obj = self._make_valid_obj(self._valid_stl_path(tmp_path))

        proxy._do_execute(obj)  # must not raise

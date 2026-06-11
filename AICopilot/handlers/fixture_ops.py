# Fixture comparison handlers for FreeCAD MCP
#
# Two tools for snapshot-style geometric regression testing on generator
# output.  The workflow: capture a known-good shape as a fixture, compare
# future runs against it, fail loudly when output diverges.
#
# Historical context: the shingle generator's left-handed rotation matrix
# (v5.2.0, 2026-03-12) survived six sessions of "build, look, change, look"
# because there was no automated "is-this-different-from-last-known-good"
# check.  With a fixture from any earlier-working dormer-roof test, the very
# next regenerate would have failed comparison and pointed at the rotation
# issue immediately.
#
# Design decisions:
#   - JSON + STL only.  No pickled FreeCAD objects — they don't survive
#     version changes.
#   - Fixtures live in the repo (fixtures/<name>/) and are version-controlled
#     so they're reviewable in PRs and carry their own git blame history.
#   - Tolerance defaults: exact counts for topology integers; 0.1% for volume;
#     0.001 mm for bbox.  All overridable per call.
#   - save_fixture is idempotent (overwrites) — re-running the generator and
#     re-saving produces a clean update with no hidden state.

import json
import math
import os
import re
import struct
import time
from typing import Any, Dict, Optional

from .base import BaseHandler

# ---------------------------------------------------------------------------
# Default tolerances
# ---------------------------------------------------------------------------

# Integer counts must match exactly — any change in face/edge/vertex count
# indicates a topological change worth investigating.
_EXACT = 0          # sentinel: exact integer match required

# Volume tolerance: 0.1 % relative.  A shingle-sheet with 100 shingles at
# 1 mm³ each has volume ≈ 100 mm³; 0.1 % = 0.1 mm³, well below any meaningful
# geometric change.
_VOLUME_REL_TOL = 0.001   # 0.1 %

# Bounding-box tolerance: 0.001 mm absolute.  FreeCAD's OCCT kernel typically
# produces bbox coords reproducible to < 1e-6 mm; 0.001 mm allows for
# floating-point variability across FreeCAD versions while catching any real
# dimensional change.
_BBOX_ABS_TOL = 0.001     # mm

# Schema version — bump only when topology.json structure changes in a
# backwards-incompatible way.
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fixtures_root() -> str:
    """Absolute path to the fixtures/ directory (repo root / fixtures)."""
    # This file lives at AICopilot/handlers/fixture_ops.py.
    # Repo root is two directories up.
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(repo_root, 'fixtures')


def _fixture_dir(fixture_name: str) -> str:
    return os.path.join(_fixtures_root(), fixture_name)


def _safe_name(name: str) -> bool:
    """Return True if fixture_name is safe to use as a directory component.

    Reject anything containing path separators, dots at the start (hidden
    dirs), or characters that would make shell quoting tricky.
    """
    if not name:
        return False
    if '..' in name or '/' in name or '\\' in name:
        return False
    # Allow alphanumerics, underscores, hyphens, and dots (not leading).
    return bool(re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$', name))


def _extract_topology(shape) -> Dict[str, Any]:
    """Pull topology summary from a FreeCAD Part.Shape object.

    Returns a dict matching the topology.json schema (schema_version 1).
    """
    bb = shape.BoundBox
    return {
        "schema_version": _SCHEMA_VERSION,
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "vertex_count": len(shape.Vertexes),
        "volume": shape.Volume,
        "bbox": {
            "x": [bb.XMin, bb.XMax],
            "y": [bb.YMin, bb.YMax],
            "z": [bb.ZMin, bb.ZMax],
        },
        "is_solid": bool(shape.isSolid()),
        "is_closed": bool(shape.isClosed()),
    }


def _write_binary_stl(shape, path: str) -> None:
    """Export shape to binary STL at path.

    Uses FreeCAD's Mesh module for the conversion.  The resulting file is
    the standard 84 + N*50 byte binary STL format.

    Raises on failure so the caller can surface the error to the MCP client.
    """
    import Mesh
    mesh = Mesh.Mesh()
    verts, tris = shape.tessellate(0.1)
    mesh.addMesh(verts, tris)
    mesh.write(path)


def _write_stl_via_export(shape, path: str) -> None:
    """Fallback STL export using MeshPart.meshFromShape + Mesh.export.

    MeshPart.meshFromShape produces better tessellations than the bare
    Mesh.Mesh approach for complex OCCT geometry (dormers, lofts, etc.).
    """
    import Mesh
    import MeshPart
    mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=0.05,
        AngularDeflection=0.5,
        Relative=False,
    )
    Mesh.export([mesh], path)


def _export_stl(shape, path: str) -> None:
    """Export shape to STL, trying MeshPart first, falling back to Mesh."""
    try:
        _write_stl_via_export(shape, path)
    except Exception:
        _write_binary_stl(shape, path)


def _try_screenshot(path: str) -> bool:
    """Attempt a GUI screenshot to path.  Returns True on success."""
    try:
        import FreeCAD
        if not FreeCAD.GuiUp:
            return False
        import FreeCADGui
        view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
        if view is None:
            view = FreeCADGui.activeView()
        if view is None:
            return False
        view.saveImage(path, 800, 600, 'White')
        return True
    except Exception:
        return False


def _compare_values(actual, saved, tolerances: Dict[str, Any]) -> Dict[str, Any]:
    """Compare actual topology dict against saved dict using tolerances.

    Returns a dict of field → {ok, actual, saved, delta} for each field
    that was checked.  The 'ok' key at top level aggregates all checks.
    """
    checks = {}
    all_ok = True

    # Integer fields — exact match by default
    int_fields = ['face_count', 'edge_count', 'vertex_count']
    for field in int_fields:
        if field not in saved:
            continue
        a = actual.get(field)
        s = saved[field]
        delta = a - s if (a is not None and s is not None) else None
        ok = (delta == 0)
        checks[field] = {'ok': ok, 'actual': a, 'saved': s, 'delta': delta}
        if not ok:
            all_ok = False

    # Volume — relative tolerance
    vol_tol = tolerances.get('volume_rel_tol', _VOLUME_REL_TOL)
    if 'volume' in saved:
        a_vol = actual.get('volume', 0.0)
        s_vol = saved['volume']
        if s_vol != 0.0:
            rel = abs(a_vol - s_vol) / abs(s_vol)
            ok = rel <= vol_tol
        else:
            ok = abs(a_vol) < 1e-9
            rel = abs(a_vol)
        checks['volume'] = {
            'ok': ok,
            'actual': a_vol,
            'saved': s_vol,
            'delta': a_vol - s_vol,
            'relative_error': rel,
            'tolerance': vol_tol,
        }
        if not ok:
            all_ok = False

    # Bounding box — absolute tolerance per coordinate
    bbox_tol = tolerances.get('bbox_abs_tol', _BBOX_ABS_TOL)
    if 'bbox' in saved:
        a_bb = actual.get('bbox', {})
        s_bb = saved['bbox']
        bbox_ok = True
        bbox_detail = {}
        for axis in ('x', 'y', 'z'):
            a_range = a_bb.get(axis, [None, None])
            s_range = s_bb.get(axis, [None, None])
            for i, label in enumerate(('min', 'max')):
                a_v = a_range[i] if i < len(a_range) else None
                s_v = s_range[i] if i < len(s_range) else None
                if a_v is None or s_v is None:
                    continue
                diff = abs(a_v - s_v)
                coord_ok = diff <= bbox_tol
                key = f'{axis}_{label}'
                bbox_detail[key] = {
                    'ok': coord_ok, 'actual': a_v, 'saved': s_v,
                    'delta': a_v - s_v, 'tolerance': bbox_tol,
                }
                if not coord_ok:
                    bbox_ok = False
        checks['bbox'] = {'ok': bbox_ok, 'coordinates': bbox_detail}
        if not bbox_ok:
            all_ok = False

    # Boolean fields — exact
    for field in ('is_solid', 'is_closed'):
        if field not in saved:
            continue
        a_v = actual.get(field)
        s_v = saved[field]
        ok = (a_v == s_v)
        checks[field] = {'ok': ok, 'actual': a_v, 'saved': s_v}
        if not ok:
            all_ok = False

    return {'ok': all_ok, 'checks': checks}


class FixtureOpsHandler(BaseHandler):
    """Snapshot-style geometric regression fixtures.

    save_fixture  — capture topology.json + STL + optional screenshot
    compare_to_fixture — compare current shape against saved fixture

    Both methods follow the handler convention: take args dict, return JSON
    string with {"ok": bool, "details": {...}, "message": str}.
    """

    _ALLOWED_OPERATIONS = frozenset({"save_fixture", "compare_to_fixture"})

    # ------------------------------------------------------------------
    # save_fixture
    # ------------------------------------------------------------------

    def save_fixture(self, args: Dict[str, Any]) -> str:
        """Save a topology summary, STL, optional screenshot, and fixture.md
        for an object under fixtures/<fixture_name>/.

        Idempotent — rewrites on every call.  The operation is intentionally
        not atomic: if the process is interrupted mid-write, the fixture
        directory may be in a partial state, which is fine — the next
        save_fixture call will overwrite.

        Args (from args dict):
          shape:         Name or label of the FreeCAD object to snapshot.
          fixture_name:  Directory name under fixtures/.  Alphanumeric,
                         underscores, hyphens only (no slashes or dots).
          description:   Optional human-readable description for fixture.md.

        Returns JSON:
          {"ok": bool, "details": {"fixture_dir": str, "files_written": list,
           "topology": dict}, "message": str}
        """
        try:
            object_name = args.get('shape', '')
            fixture_name = args.get('fixture_name', '')
            description = args.get('description') or ''

            if not object_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: shape"
                })
            if not fixture_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: fixture_name"
                })
            if not _safe_name(fixture_name):
                return json.dumps({
                    "ok": False, "details": {},
                    "message": (
                        f"Invalid fixture_name {fixture_name!r}. "
                        f"Use only alphanumerics, underscores, hyphens, and dots. "
                        f"No path separators or leading dots."
                    )
                })

            doc = self.get_document()
            if not doc:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "No active FreeCAD document"
                })

            obj = self.get_object(object_name, doc)
            if not obj:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": f"Object not found: {object_name}"
                })
            if not hasattr(obj, 'Shape'):
                return json.dumps({
                    "ok": False, "details": {},
                    "message": f"Object {object_name!r} has no Shape attribute"
                })

            shape = obj.Shape
            fdir = _fixture_dir(fixture_name)
            os.makedirs(fdir, exist_ok=True)

            files_written = []

            # --- topology.json ---
            topo = _extract_topology(shape)
            topo_path = os.path.join(fdir, 'topology.json')
            with open(topo_path, 'w', encoding='utf-8') as f:
                json.dump(topo, f, indent=2)
                f.write('\n')
            files_written.append('topology.json')

            # --- shape.stl ---
            stl_path = os.path.join(fdir, 'shape.stl')
            stl_error = None
            try:
                _export_stl(shape, stl_path)
                files_written.append('shape.stl')
            except Exception as e:
                stl_error = str(e)

            # --- screenshot.png (best-effort, GUI only) ---
            png_path = os.path.join(fdir, 'screenshot.png')
            screenshot_ok = _try_screenshot(png_path)
            if screenshot_ok:
                files_written.append('screenshot.png')

            # --- fixture.md ---
            import FreeCAD
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            fc_ver = getattr(FreeCAD, 'Version', lambda: ['?'])()
            fc_ver_str = '.'.join(str(v) for v in fc_ver[:3]) if fc_ver else 'unknown'
            doc_name = getattr(doc, 'Name', 'unknown')

            md_lines = [
                f'# Fixture: {fixture_name}',
                '',
                f'**Saved:** {timestamp}',
                f'**FreeCAD version:** {fc_ver_str}',
                f'**Document:** {doc_name}',
                f'**Object:** {object_name}',
                '',
            ]
            if description:
                md_lines += [
                    '## Description',
                    '',
                    description.strip(),
                    '',
                ]
            md_lines += [
                '## Topology summary',
                '',
                f'- Faces: {topo["face_count"]}',
                f'- Edges: {topo["edge_count"]}',
                f'- Vertices: {topo["vertex_count"]}',
                f'- Volume: {topo["volume"]:.6f} mm³',
                f'- Is solid: {topo["is_solid"]}',
                f'- Is closed: {topo["is_closed"]}',
                f'- Bounding box X: [{topo["bbox"]["x"][0]:.4f}, {topo["bbox"]["x"][1]:.4f}] mm',
                f'- Bounding box Y: [{topo["bbox"]["y"][0]:.4f}, {topo["bbox"]["y"][1]:.4f}] mm',
                f'- Bounding box Z: [{topo["bbox"]["z"][0]:.4f}, {topo["bbox"]["z"][1]:.4f}] mm',
                '',
                '## Files',
                '',
                '- `topology.json` — machine-readable topology for comparison',
                '- `shape.stl` — binary STL for visual reference',
                '- `screenshot.png` — viewport screenshot at save time (if GUI available)',
                '',
                '## Usage',
                '',
                f'```python',
                f'compare_to_fixture(shape="{object_name}", fixture_name="{fixture_name}")',
                f'```',
                '',
            ]
            md_path = os.path.join(fdir, 'fixture.md')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(md_lines))
            files_written.append('fixture.md')

            # Build message
            parts = [
                f"Fixture '{fixture_name}' saved: {', '.join(files_written)}.",
                f"Topology: {topo['face_count']} faces, {topo['edge_count']} edges, "
                f"{topo['vertex_count']} vertices, volume = {topo['volume']:.4f} mm³.",
            ]
            if stl_error:
                parts.append(f"WARNING: STL export failed: {stl_error}")
            if not screenshot_ok:
                parts.append("Screenshot not captured (GUI not available or no active view).")

            return json.dumps({
                "ok": True,
                "details": {
                    "fixture_dir": fdir,
                    "files_written": files_written,
                    "topology": topo,
                    "stl_error": stl_error,
                    "screenshot_captured": screenshot_ok,
                },
                "message": " ".join(parts),
            })

        except Exception as e:
            return json.dumps({
                "ok": False, "details": {},
                "message": f"Error in save_fixture: {e}"
            })

    # ------------------------------------------------------------------
    # compare_to_fixture
    # ------------------------------------------------------------------

    def compare_to_fixture(self, args: Dict[str, Any]) -> str:
        """Compare a FreeCAD object's topology against a saved fixture.

        Checks face/edge/vertex counts (exact), volume (within 0.1% by
        default), and bounding box (within 0.001 mm by default).  All
        tolerances are overridable per call.

        Args (from args dict):
          shape:         Name or label of the FreeCAD object to compare.
          fixture_name:  Name of the saved fixture (directory under fixtures/).
          tolerances:    Optional dict with override keys:
                           volume_rel_tol  — float, default 0.001 (0.1 %)
                           bbox_abs_tol    — float in mm, default 0.001

        Returns JSON:
          {"ok": bool,
           "details": {"checks": {...}, "actual": {...}, "saved": {...},
                       "fixture_dir": str},
           "message": str}
        """
        try:
            object_name = args.get('shape', '')
            fixture_name = args.get('fixture_name', '')
            tolerances = args.get('tolerances') or {}

            if not object_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: shape"
                })
            if not fixture_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: fixture_name"
                })
            if not _safe_name(fixture_name):
                return json.dumps({
                    "ok": False, "details": {},
                    "message": (
                        f"Invalid fixture_name {fixture_name!r}. "
                        f"Use only alphanumerics, underscores, hyphens, and dots."
                    )
                })

            fdir = _fixture_dir(fixture_name)
            topo_path = os.path.join(fdir, 'topology.json')
            if not os.path.isdir(fdir):
                return json.dumps({
                    "ok": False, "details": {"fixture_dir": fdir},
                    "message": (
                        f"Fixture '{fixture_name}' not found at {fdir}. "
                        f"Run save_fixture first to create it."
                    )
                })
            if not os.path.isfile(topo_path):
                return json.dumps({
                    "ok": False, "details": {"fixture_dir": fdir},
                    "message": (
                        f"Fixture '{fixture_name}' exists but topology.json is missing. "
                        f"Re-run save_fixture to rebuild it."
                    )
                })

            # Load saved topology
            with open(topo_path, 'r', encoding='utf-8') as f:
                saved_topo = json.load(f)

            # Check schema version compatibility
            saved_ver = saved_topo.get('schema_version', 1)
            if saved_ver != _SCHEMA_VERSION:
                return json.dumps({
                    "ok": False,
                    "details": {
                        "fixture_dir": fdir,
                        "saved_schema_version": saved_ver,
                        "current_schema_version": _SCHEMA_VERSION,
                    },
                    "message": (
                        f"Fixture schema version mismatch: saved={saved_ver}, "
                        f"current={_SCHEMA_VERSION}. "
                        f"Re-run save_fixture to update the fixture."
                    )
                })

            # Resolve object
            doc = self.get_document()
            if not doc:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "No active FreeCAD document"
                })

            obj = self.get_object(object_name, doc)
            if not obj:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": f"Object not found: {object_name}"
                })
            if not hasattr(obj, 'Shape'):
                return json.dumps({
                    "ok": False, "details": {},
                    "message": f"Object {object_name!r} has no Shape attribute"
                })

            actual_topo = _extract_topology(obj.Shape)

            # Run comparison
            cmp = _compare_values(actual_topo, saved_topo, tolerances)
            ok = cmp['ok']
            checks = cmp['checks']

            # Build summary message
            if ok:
                msg = (
                    f"Shape '{object_name}' matches fixture '{fixture_name}': "
                    f"{len(checks)} check(s) passed."
                )
            else:
                failures = [
                    k for k, v in checks.items()
                    if not v.get('ok', True)
                ]
                # Produce human-readable failure details
                detail_parts = []
                for field in failures:
                    ch = checks[field]
                    if field == 'bbox':
                        bad_coords = [
                            coord for coord, cv in ch.get('coordinates', {}).items()
                            if not cv.get('ok', True)
                        ]
                        detail_parts.append(f"bbox[{', '.join(bad_coords)}]")
                    elif field == 'volume':
                        pct = ch.get('relative_error', 0) * 100
                        detail_parts.append(
                            f"volume (saved={ch['saved']:.4f}, actual={ch['actual']:.4f}, "
                            f"err={pct:.3f}%)"
                        )
                    else:
                        detail_parts.append(
                            f"{field} (saved={ch.get('saved')}, actual={ch.get('actual')})"
                        )
                msg = (
                    f"Shape '{object_name}' DIFFERS from fixture '{fixture_name}': "
                    f"{len(failures)} check(s) failed: "
                    + "; ".join(detail_parts) + "."
                )

            return json.dumps({
                "ok": ok,
                "details": {
                    "fixture_dir": fdir,
                    "checks": checks,
                    "actual": actual_topo,
                    "saved": saved_topo,
                    "tolerances_used": {
                        "volume_rel_tol": tolerances.get('volume_rel_tol', _VOLUME_REL_TOL),
                        "bbox_abs_tol": tolerances.get('bbox_abs_tol', _BBOX_ABS_TOL),
                    },
                },
                "message": msg,
            })

        except Exception as e:
            return json.dumps({
                "ok": False, "details": {},
                "message": f"Error in compare_to_fixture: {e}"
            })

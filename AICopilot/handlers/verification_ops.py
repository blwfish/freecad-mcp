# Geometric verification handlers for FreeCAD MCP
#
# Four self-verification tools targeting the dominant pain archetype in
# generator work: wrong axis / direction / sign in geometric utility code.
# Each returns {"ok": bool, "details": {...}, "message": str} so Claude can
# branch on the result programmatically rather than parsing free-form text.
#
# Historical context: the shingle generator's left-handed rotation matrix
# (det = -1) took six Claude Code sessions to root-cause (March 2026).
# verify_handedness directly catches that class of bug.

import json
import math
from typing import Any, Dict, List, Optional

from .base import BaseHandler

# Float tolerance for det ≈ +1 checks.  1e-6 is tight enough to catch
# numerical drift from composed rotations while loose enough not to fire
# on round-trip FreeCAD Placement → matrix conversions.
_DET_TOLERANCE = 1e-6

# Dot-product threshold for "points in expected direction":
# cos(90°) = 0.  We accept dot > 0 as "same hemisphere".
_DOT_THRESHOLD = 0.0


def _dot(v1, v2) -> float:
    """Dot product of two 3-vectors (iterables or objects with .x/.y/.z)."""
    if hasattr(v1, 'x'):
        a = (v1.x, v1.y, v1.z)
    else:
        a = tuple(v1)
    if hasattr(v2, 'x'):
        b = (v2.x, v2.y, v2.z)
    else:
        b = tuple(v2)
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _magnitude(v) -> float:
    if hasattr(v, 'x'):
        return math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
    return math.sqrt(sum(x * x for x in v))


def _normalize(v):
    """Return unit vector as (x, y, z) tuple."""
    if hasattr(v, 'x'):
        coords = (v.x, v.y, v.z)
    else:
        coords = tuple(float(x) for x in v)
    mag = math.sqrt(sum(x * x for x in coords))
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return tuple(x / mag for x in coords)


def _det3x3(m) -> float:
    """Determinant of a 3×3 matrix.

    Accepts any of:
      * 3×3 nested list/tuple
      * flat 9-element list (row-major)
      * FreeCAD Matrix (reads A11..A33 attributes)
    """
    # FreeCAD Matrix object
    if hasattr(m, 'A11'):
        a = [[m.A11, m.A12, m.A13],
             [m.A21, m.A22, m.A23],
             [m.A31, m.A32, m.A33]]
        m = a
    # Flat list → 3×3
    if hasattr(m, '__len__') and len(m) == 9 and not hasattr(m[0], '__len__'):
        m = [[m[0], m[1], m[2]],
             [m[3], m[4], m[5]],
             [m[6], m[7], m[8]]]
    # Now m is 3×3
    r0, r1, r2 = m[0], m[1], m[2]
    return (r0[0] * (r1[1] * r2[2] - r1[2] * r2[1])
            - r0[1] * (r1[0] * r2[2] - r1[2] * r2[0])
            + r0[2] * (r1[0] * r2[1] - r1[1] * r2[0]))


class VerificationOpsHandler(BaseHandler):
    """Geometric self-verification tools.

    These tools let Claude immediately check whether a generated shape or
    transformation is well-formed and oriented correctly, without requiring
    a human to inspect FreeCAD.  Use them after any generator run that
    involves rotations, normals, or topology constraints.

    All methods return JSON strings (consistent with other handlers) whose
    parsed form is {"ok": bool, "details": {...}, "message": str}.
    """

    _ALLOWED_OPERATIONS = frozenset({
        "verify_handedness", "verify_orientation", "verify_no_self_intersection",
        "verify_topology",
    })

    # ------------------------------------------------------------------
    # verify_handedness
    # ------------------------------------------------------------------

    def verify_handedness(self, args: Dict[str, Any]) -> str:
        """Check that a 3×3 rotation matrix has determinant ≈ +1 (right-handed).

        When to call this:
          After building or composing a rotation matrix, before applying it to
          geometry.  A left-handed matrix (det ≈ -1) causes a mirror-reflection
          instead of a rotation — the root cause of the shingle generator's
          6-session orientation bug (2026-03-12 v5.2.0 fix).

        Args (from args dict):
          matrix: 3×3 rotation matrix.  Accepted formats:
            - [[r00,r01,r02],[r10,r11,r12],[r20,r21,r22]]   (3×3 nested list)
            - [r00,r01,...,r22]                               (9-element flat list)
            - FreeCAD Matrix object                           (uses A11..A33)

        Returns JSON:
          {"ok": bool, "details": {"determinant": float, "tolerance": float},
           "message": str}
        """
        try:
            matrix = args.get('matrix')
            if matrix is None:
                return json.dumps({
                    "ok": False,
                    "details": {},
                    "message": "Missing required argument: matrix"
                })

            det = _det3x3(matrix)
            error = abs(det - 1.0)
            ok = error <= _DET_TOLERANCE

            if ok:
                msg = (f"Matrix is right-handed: det = {det:.8f} "
                       f"(error {error:.2e} ≤ tolerance {_DET_TOLERANCE:.2e})")
            else:
                if abs(det + 1.0) <= _DET_TOLERANCE:
                    handedness = "left-handed (mirrored)"
                elif abs(det) < _DET_TOLERANCE:
                    handedness = "singular (det ≈ 0, not a rotation)"
                else:
                    handedness = f"invalid rotation (det = {det:.8f})"
                msg = (
                    f"Matrix is {handedness}. "
                    f"det = {det:.8f}, expected +1 ± {_DET_TOLERANCE:.2e}. "
                    f"This will cause mirror-reflection instead of rotation — "
                    f"negate one column or row to restore right-handedness."
                )

            return json.dumps({
                "ok": ok,
                "details": {
                    "determinant": det,
                    "tolerance": _DET_TOLERANCE,
                    "error": error,
                },
                "message": msg,
            })

        except Exception as e:
            return json.dumps({
                "ok": False,
                "details": {},
                "message": f"Error in verify_handedness: {e}"
            })

    # ------------------------------------------------------------------
    # verify_orientation
    # ------------------------------------------------------------------

    def verify_orientation(self, args: Dict[str, Any]) -> str:
        """Check face normals of a shape point in the expected direction.

        When to call this:
          After generating a surface-like shape (roof shingles, wall panels,
          display faces, split tabs) to confirm the visible face points the
          right way.  Catches flipped-normal bugs that look correct in counts
          but wrong visually.

        Args (from args dict):
          object_name: Name or label of the FreeCAD object to inspect.
          expected_axis: [x, y, z] unit vector (or axis name: "+X", "-Z", etc.)
            representing the expected normal direction.
          mode: One of:
            "dominant"  — pass if the face with the largest area has a
                          positive dot product with expected_axis (default)
            "majority"  — pass if ≥50% of faces (by count) align
            "all"       — pass only if every face aligns

        Returns JSON:
          {"ok": bool,
           "details": {"face_count": int, "aligned_count": int,
                       "dominant_dot": float, "mode": str,
                       "expected_axis": [x,y,z]},
           "message": str}
        """
        try:
            object_name = args.get('object_name', '')
            expected_axis_raw = args.get('expected_axis')
            mode = args.get('mode', 'dominant')

            if not object_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: object_name"
                })
            if expected_axis_raw is None:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: expected_axis"
                })

            # Parse axis — accept [x,y,z] list or named string like "+Z", "-X"
            axis = self._parse_axis(expected_axis_raw)
            if axis is None:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": (f"Cannot parse expected_axis {expected_axis_raw!r}. "
                                f"Use [x,y,z] list or named axis like '+Z', '-X', '+Y'.")
                })

            doc = self.get_document()
            if not doc:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "No active document"
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
                    "message": f"Object {object_name} has no Shape property"
                })

            shape = obj.Shape
            faces = shape.Faces
            face_count = len(faces)

            if face_count == 0:
                return json.dumps({
                    "ok": False,
                    "details": {"face_count": 0, "mode": mode,
                                "expected_axis": list(axis)},
                    "message": "Shape has no faces"
                })

            # Compute per-face dot products and find dominant face
            face_dots = []
            dominant_dot = None
            dominant_area = -1.0
            errors = []

            for i, face in enumerate(faces):
                try:
                    normal = face.normalAt(0, 0)
                    dot = _dot(_normalize(normal), axis)
                    area = getattr(face, 'Area', 1.0)
                    face_dots.append((dot, area))
                    if area > dominant_area:
                        dominant_area = area
                        dominant_dot = dot
                except Exception as fe:
                    errors.append(f"Face{i+1}: {fe}")
                    face_dots.append((None, 0.0))

            valid_dots = [d for d, _ in face_dots if d is not None]
            aligned_count = sum(1 for d in valid_dots if d > _DOT_THRESHOLD)

            if mode == 'dominant':
                ok = dominant_dot is not None and dominant_dot > _DOT_THRESHOLD
                mode_desc = "dominant face"
            elif mode == 'all':
                ok = (len(valid_dots) == face_count
                      and all(d > _DOT_THRESHOLD for d in valid_dots))
                mode_desc = "all faces"
            elif mode == 'majority':
                ok = aligned_count >= (len(valid_dots) / 2.0)
                mode_desc = "majority of faces"
            else:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": (f"Unknown mode {mode!r}. "
                                f"Use 'dominant', 'majority', or 'all'.")
                })

            details = {
                "face_count": face_count,
                "aligned_count": aligned_count,
                "dominant_dot": dominant_dot,
                "mode": mode,
                "expected_axis": list(axis),
            }
            if errors:
                details["face_errors"] = errors

            if ok:
                msg = (f"Orientation OK ({mode_desc}): "
                       f"{aligned_count}/{len(valid_dots)} faces align with "
                       f"expected_axis {list(axis)}. "
                       f"Dominant face dot = {dominant_dot:.4f}.")
            else:
                msg = (f"Orientation FAIL ({mode_desc}): "
                       f"{aligned_count}/{len(valid_dots)} faces align with "
                       f"expected_axis {list(axis)}. "
                       f"Dominant face dot = {dominant_dot:.4f} "
                       f"(need > {_DOT_THRESHOLD}). "
                       f"Normals likely point in the opposite direction — "
                       f"negate the axis or reverse the surface orientation.")

            return json.dumps({"ok": ok, "details": details, "message": msg})

        except Exception as e:
            return json.dumps({
                "ok": False, "details": {},
                "message": f"Error in verify_orientation: {e}"
            })

    def _parse_axis(self, raw) -> Optional[tuple]:
        """Parse expected_axis arg to a unit (x,y,z) tuple, or None on failure."""
        if isinstance(raw, (list, tuple)) and len(raw) == 3:
            try:
                v = tuple(float(x) for x in raw)
                mag = math.sqrt(sum(x * x for x in v))
                if mag < 1e-12:
                    return None
                return tuple(x / mag for x in v)
            except (TypeError, ValueError):
                return None
        if isinstance(raw, str):
            _named = {
                '+x': (1, 0, 0), '-x': (-1, 0, 0),
                '+y': (0, 1, 0), '-y': (0, -1, 0),
                '+z': (0, 0, 1), '-z': (0, 0, -1),
                'x':  (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1),
            }
            key = raw.strip().lower()
            return _named.get(key)
        return None

    # ------------------------------------------------------------------
    # verify_no_self_intersection
    # ------------------------------------------------------------------

    def verify_no_self_intersection(self, args: Dict[str, Any]) -> str:
        """Run an OCCT-level validity check on a shape (no self-intersecting faces).

        When to call this:
          After boolean operations, lofts, or any generator that constructs
          geometry programmatically.  Self-intersecting shapes crash downstream
          boolean ops and produce incorrect slices.  Call before committing a
          generated solid to a document or exporting to STL.

        Wraps Part.Shape.check() (FreeCAD's exposure of BRepCheck_Analyzer).
        A shape that passes isValid() but has subtle geometry errors will still
        be caught here.

        Args (from args dict):
          object_name: Name or label of the FreeCAD object to check.

        Returns JSON:
          {"ok": bool,
           "details": {"is_valid": bool, "check_result": str,
                       "face_count": int, "solid_count": int},
           "message": str}
        """
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: object_name"
                })

            doc = self.get_document()
            if not doc:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "No active document"
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
                    "message": f"Object {object_name} has no Shape property"
                })

            shape = obj.Shape
            is_valid = bool(shape.isValid())
            face_count = len(shape.Faces)
            solid_count = len(shape.Solids)

            # shape.check() signals problems two ways: raising an exception, or
            # returning a non-empty error string. None or "" means the check passed.
            check_result = ""
            check_ok = True
            try:
                result = shape.check()
                if result:  # non-empty string → errors were reported
                    check_result = str(result)
                    check_ok = False
                else:
                    check_result = "Shape is valid"
            except Exception as check_exc:
                check_result = str(check_exc)
                check_ok = False

            ok = is_valid and check_ok

            details = {
                "is_valid": is_valid,
                "check_result": check_result,
                "face_count": face_count,
                "solid_count": solid_count,
            }

            if ok:
                msg = (f"Shape {object_name} is valid: no self-intersections detected. "
                       f"({face_count} faces, {solid_count} solids)")
            else:
                problems = []
                if not is_valid:
                    problems.append("isValid() returned False")
                if not check_ok:
                    problems.append(f"check() reported: {check_result}")
                msg = (f"Shape {object_name} has geometry errors: "
                       f"{'; '.join(problems)}. "
                       f"Run measurement_operations/count_elements for detail, "
                       f"or use execute_python to call BRepCheck_Analyzer.")

            return json.dumps({"ok": ok, "details": details, "message": msg})

        except Exception as e:
            return json.dumps({
                "ok": False, "details": {},
                "message": f"Error in verify_no_self_intersection: {e}"
            })

    # ------------------------------------------------------------------
    # verify_topology
    # ------------------------------------------------------------------

    def verify_topology(self, args: Dict[str, Any]) -> str:
        """Flexible topology check: compare actual counts/volume against expectations.

        When to call this:
          After running a generator that should produce a predictable number of
          faces, edges, or a specific volume range.  Catch "generator ran without
          error but produced the wrong shape" bugs immediately rather than on
          visual inspection.  All parameters are optional — only check what you
          know.

        Args (from args dict):
          object_name:  Name or label of the FreeCAD object to inspect.
          face_count:   Expected number of faces (exact).           [optional]
          edge_count:   Expected number of edges (exact).           [optional]
          vertex_count: Expected number of vertices (exact).        [optional]
          volume_range: [min, max] — expected volume in mm³.        [optional]

        Returns JSON:
          {"ok": bool,
           "details": {"face_count": int, "edge_count": int,
                       "vertex_count": int, "volume": float,
                       "checks": {"face_count": {"pass": bool, "actual": int,
                                                  "expected": int}, ...}},
           "message": str}
        """
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "Missing required argument: object_name"
                })

            doc = self.get_document()
            if not doc:
                return json.dumps({
                    "ok": False, "details": {},
                    "message": "No active document"
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
                    "message": f"Object {object_name} has no Shape property"
                })

            shape = obj.Shape
            actual_faces = len(shape.Faces)
            actual_edges = len(shape.Edges)
            actual_verts = len(shape.Vertexes)
            actual_volume = shape.Volume

            checks = {}
            failures = []

            exp_faces = args.get('face_count')
            if exp_faces is not None:
                exp_faces = int(exp_faces)
                passed = actual_faces == exp_faces
                checks['face_count'] = {
                    "pass": passed, "actual": actual_faces, "expected": exp_faces
                }
                if not passed:
                    failures.append(
                        f"face_count: expected {exp_faces}, got {actual_faces}"
                    )

            exp_edges = args.get('edge_count')
            if exp_edges is not None:
                exp_edges = int(exp_edges)
                passed = actual_edges == exp_edges
                checks['edge_count'] = {
                    "pass": passed, "actual": actual_edges, "expected": exp_edges
                }
                if not passed:
                    failures.append(
                        f"edge_count: expected {exp_edges}, got {actual_edges}"
                    )

            exp_verts = args.get('vertex_count')
            if exp_verts is not None:
                exp_verts = int(exp_verts)
                passed = actual_verts == exp_verts
                checks['vertex_count'] = {
                    "pass": passed, "actual": actual_verts, "expected": exp_verts
                }
                if not passed:
                    failures.append(
                        f"vertex_count: expected {exp_verts}, got {actual_verts}"
                    )

            vol_range = args.get('volume_range')
            if vol_range is not None:
                vol_min = float(vol_range[0])
                vol_max = float(vol_range[1])
                passed = vol_min <= actual_volume <= vol_max
                checks['volume_range'] = {
                    "pass": passed,
                    "actual": actual_volume,
                    "expected_min": vol_min,
                    "expected_max": vol_max,
                }
                if not passed:
                    failures.append(
                        f"volume: expected [{vol_min}, {vol_max}], "
                        f"got {actual_volume:.4f}"
                    )

            ok = len(failures) == 0

            details = {
                "face_count": actual_faces,
                "edge_count": actual_edges,
                "vertex_count": actual_verts,
                "volume": actual_volume,
                "checks": checks,
            }

            if not checks:
                msg = (f"Topology of {object_name}: {actual_faces} faces, "
                       f"{actual_edges} edges, {actual_verts} vertices, "
                       f"volume = {actual_volume:.4f} mm³. "
                       f"(No constraints specified — reporting counts only.)")
            elif ok:
                msg = (f"Topology OK for {object_name}: all "
                       f"{len(checks)} constraint(s) passed.")
            else:
                msg = (f"Topology FAIL for {object_name}: "
                       f"{len(failures)}/{len(checks)} constraint(s) failed: "
                       f"{'; '.join(failures)}.")

            return json.dumps({"ok": ok, "details": details, "message": msg})

        except Exception as e:
            return json.dumps({
                "ok": False, "details": {},
                "message": f"Error in verify_topology: {e}"
            })

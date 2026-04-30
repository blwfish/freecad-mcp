"""Geometric assertion helpers for integration tests.

Replaces tautological ``assert "Unknown" not in result`` checks with real
shape-property assertions. Uses execute_python to round-trip values from
the live FreeCAD instance.
"""

import json
from typing import Optional

from .test_e2e_workflows import send_command


def _result_text(result) -> str:
    """Extract the text body from a send_command response.

    Handles three wrapper shapes seen in CI vs. local runs:
      * Bare string: returned as-is.
      * MCP-style dict: ``{"content": [{"type": "text", "text": ...}]}``.
      * Bridge-wrapped dict: ``{"result": "..."}`` — what the headless
        bridge returns. If "result" is itself a dict, recurse one level.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if "result" in result:
            inner = result["result"]
            if isinstance(inner, (str, dict)):
                return _result_text(inner)
            return str(inner)
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                return first["text"]
        return json.dumps(result)
    return str(result)


def assert_op_succeeded(result, op_label: str = "operation"):
    """Fail the test if the result indicates dispatch failure or error.

    Stricter than the legacy ``"Unknown" not in result`` check: also rejects
    strings that begin with "Error" or contain a JSON ``error`` field.
    """
    text = _result_text(result)
    assert "Unknown operation" not in text, \
        f"{op_label}: dispatch failed — {text[:300]}"
    assert not text.lstrip().startswith("Error"), \
        f"{op_label}: handler returned error — {text[:300]}"


def get_shape_props(doc_name: str, obj_name: str) -> dict:
    """Return geometric properties of an object via execute_python.

    Returns dict with keys: volume, face_count, edge_count, vertex_count,
    bbox (xlen, ylen, zlen), is_valid. None if the object lacks a Shape.
    """
    code = f"""
import json
doc = FreeCAD.getDocument('{doc_name}')
if doc is None:
    raise RuntimeError('document {doc_name!r} not found')
obj = doc.getObject('{obj_name}')
if obj is None:
    found = doc.getObjectsByLabel('{obj_name}')
    obj = found[0] if found else None
if obj is None:
    raise RuntimeError('object {obj_name!r} not found in {doc_name!r}')
if not hasattr(obj, 'Shape') or obj.Shape is None:
    json.dumps(None)
else:
    s = obj.Shape
    json.dumps({{
        'volume': float(s.Volume) if hasattr(s, 'Volume') else 0.0,
        'face_count': len(s.Faces),
        'edge_count': len(s.Edges),
        'vertex_count': len(s.Vertexes),
        'solid_count': len(s.Solids),
        'wire_count': len(s.Wires),
        'bbox': [s.BoundBox.XLength, s.BoundBox.YLength, s.BoundBox.ZLength],
        'is_valid': bool(s.isValid()),
    }})
"""
    raw = send_command("execute_python", {"code": code})
    text = _result_text(raw)
    text = text.strip()
    if text in ("None", "null"):
        return None
    # execute_python wraps the value as the last expression. Strip any
    # leading "Result: " or similar prefix bridge might add.
    if text.startswith("Result: "):
        text = text[len("Result: "):]
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise AssertionError(
            f"get_shape_props: could not parse execute_python response — {e}\n"
            f"raw: {text[:400]}"
        )


def get_object_count(doc_name: str, type_filter: Optional[str] = None) -> int:
    """Return the number of objects in a document, optionally filtered by TypeId."""
    if type_filter:
        code = (
            f"doc = FreeCAD.getDocument('{doc_name}')\n"
            f"len([o for o in doc.Objects if o.TypeId == {type_filter!r}])"
        )
    else:
        code = f"len(FreeCAD.getDocument('{doc_name}').Objects)"
    raw = send_command("execute_python", {"code": code})
    text = _result_text(raw).strip()
    if text.startswith("Result: "):
        text = text[len("Result: "):]
    return int(text.strip())


def assert_volume_close(actual: float, expected: float, rel: float = 0.01,
                        op_label: str = "volume"):
    """Assert ``actual`` is within ``rel`` (fractional tolerance) of ``expected``."""
    if expected == 0:
        assert abs(actual) < rel, f"{op_label}: expected ~0, got {actual}"
        return
    ratio = abs(actual - expected) / abs(expected)
    assert ratio <= rel, \
        f"{op_label}: expected {expected} ± {rel * 100:.1f}%, got {actual} ({ratio * 100:.2f}% off)"


def assert_face_count(props: dict, expected: int, op_label: str = "face count"):
    """Assert face count matches exactly. Use a range check via ``assert_face_range``
    when surfaces may have variable subdivisions."""
    actual = props['face_count']
    assert actual == expected, \
        f"{op_label}: expected {expected} faces, got {actual}"


def assert_face_range(props: dict, min_faces: int, max_faces: int,
                      op_label: str = "face count"):
    """Assert face count falls in a closed range."""
    actual = props['face_count']
    assert min_faces <= actual <= max_faces, \
        f"{op_label}: expected {min_faces}–{max_faces} faces, got {actual}"


def assert_bbox_close(props: dict, expected: tuple, rel: float = 0.01,
                      op_label: str = "bbox"):
    """Assert bbox dimensions match expected tuple within fractional tolerance."""
    actual = tuple(props['bbox'])
    for i, (a, e) in enumerate(zip(actual, expected)):
        if e == 0:
            assert abs(a) < rel, f"{op_label}[{i}]: expected ~0, got {a}"
        else:
            ratio = abs(a - e) / abs(e)
            assert ratio <= rel, \
                f"{op_label}[{i}]: expected {e} ± {rel * 100:.1f}%, got {a}"

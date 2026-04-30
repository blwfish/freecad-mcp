"""Shared mock infrastructure for FreeCAD MCP unit tests.

Provides:
  * Module-level mock installation in sys.modules for FreeCAD,
    FreeCADGui, Part, Sketcher, Draft, Spreadsheet, PartDesign.
  * Object factories: make_mock_doc, make_part_object, make_box_object,
    make_sketch, make_body.
  * Handler factory: make_handler.
  * Assertion helpers: assert_dispatched, assert_error_contains,
    assert_awaiting_selection.
  * Selection-flow harness: simulate_selection.

Import this module at the top of any unit test file BEFORE importing
the handler under test. It side-effects sys.modules so the handler's
``import FreeCAD`` etc. resolves to our mocks.

Tests should call reset_mocks() in setUp() to clear state between cases.
"""

import os
import sys
from typing import Any, Dict, Iterable, List, Optional
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Module-level mock installation. Runs once on import.
#
# IMPORTANT: must be idempotent and tolerant of other unit-test files
# that pre-mock with their own MagicMock at module level
# (test_mesh_ops.py, test_spatial_ops.py, test_open_wire_diagnosis.py
# all do this). Whichever file loads first wins sys.modules['FreeCAD'];
# handlers close over that same object via ``import FreeCAD`` and never
# re-resolve. If we install a *fresh* mock here, our handler tests
# break the others (they set state on a different object than the
# handler reads from).
#
# Strategy: adopt whatever is already in sys.modules if it's a Mock,
# otherwise install ours. End state — every test file that uses module-
# level pre-mocking ends up sharing the same mock object as long as at
# least one of them is idempotent.
# ---------------------------------------------------------------------------

def _adopt_or_create(name: str) -> MagicMock:
    existing = sys.modules.get(name)
    if isinstance(existing, MagicMock):
        return existing
    fresh = MagicMock()
    sys.modules[name] = fresh
    return fresh


mock_FreeCAD = _adopt_or_create('FreeCAD')
mock_FreeCAD.GuiUp = False
if not isinstance(getattr(mock_FreeCAD, 'Console', None), MagicMock):
    mock_FreeCAD.Console = MagicMock()
if not hasattr(mock_FreeCAD, 'ActiveDocument'):
    mock_FreeCAD.ActiveDocument = None

mock_FreeCADGui = _adopt_or_create('FreeCADGui')
mock_Part = _adopt_or_create('Part')
mock_Sketcher = _adopt_or_create('Sketcher')
mock_Draft = _adopt_or_create('Draft')
mock_Spreadsheet = _adopt_or_create('Spreadsheet')
mock_PartDesign = _adopt_or_create('PartDesign')
mock_Mesh = _adopt_or_create('Mesh')
mock_MeshPart = _adopt_or_create('MeshPart')

# Make handler imports resolvable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))


def reset_mocks():
    """Clear state on the module-level mocks. Call from setUp().

    Resets call records, return values, and side effects, then re-installs
    the small handful of attributes our tests rely on.

    Also re-asserts our mocks into sys.modules — the unit conftest has an
    autouse mock_freecad fixture that replaces sys.modules['Part'] (and
    others) with a bare types.ModuleType per test, which would break
    handler methods that do ``import Part`` and call ``Part.Face``,
    ``Part.makeCompound``, etc. at call-time. test_mesh_ops works around
    this in its own setUp; doing it once here means downstream tests
    inherit the workaround for free.
    """
    for m in (mock_FreeCAD, mock_FreeCADGui, mock_Part, mock_Sketcher,
              mock_Draft, mock_Spreadsheet, mock_PartDesign,
              mock_Mesh, mock_MeshPart):
        m.reset_mock(return_value=True, side_effect=True)

    sys.modules['FreeCAD'] = mock_FreeCAD
    sys.modules['FreeCADGui'] = mock_FreeCADGui
    sys.modules['Part'] = mock_Part
    sys.modules['Sketcher'] = mock_Sketcher
    sys.modules['Draft'] = mock_Draft
    sys.modules['Spreadsheet'] = mock_Spreadsheet
    sys.modules['PartDesign'] = mock_PartDesign
    sys.modules['Mesh'] = mock_Mesh
    sys.modules['MeshPart'] = mock_MeshPart

    mock_FreeCAD.GuiUp = False
    mock_FreeCAD.Console = MagicMock()
    mock_FreeCAD.ActiveDocument = None
    install_freecad_value_types()


# ---------------------------------------------------------------------------
# Vector / Placement / Matrix mocks
# ---------------------------------------------------------------------------

class _Vec:
    """Simple stand-in for FreeCAD.Vector. Supports arithmetic + .Length."""

    __slots__ = ('x', 'y', 'z')

    def __init__(self, x=0, y=0, z=0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, o):
        return _Vec(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o):
        return _Vec(self.x - o.x, self.y - o.y, self.z - o.z)

    def __eq__(self, o):
        return isinstance(o, _Vec) and self.x == o.x and self.y == o.y and self.z == o.z

    def __repr__(self):
        return f"_Vec({self.x}, {self.y}, {self.z})"

    @property
    def Length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    def add(self, other):
        """FreeCAD Vector.add(other) — same as __add__ but explicit method."""
        return _Vec(self.x + other.x, self.y + other.y, self.z + other.z)

    def sub(self, other):
        return _Vec(self.x - other.x, self.y - other.y, self.z - other.z)

    def multiply(self, scalar):
        return _Vec(self.x * scalar, self.y * scalar, self.z * scalar)

    def distanceToPoint(self, other):
        return (self - other).Length


class _Rotation:
    def __init__(self, axis=None, angle=0):
        self.axis = axis or _Vec(0, 0, 1)
        self.angle = angle

    def multiply(self, other):
        return _Rotation(self.axis, self.angle + other.angle)


class _Placement:
    def __init__(self, base=None, rotation=None):
        self.Base = base if base is not None else _Vec()
        self.Rotation = rotation if rotation is not None else _Rotation()


class _Matrix:
    def __init__(self):
        self._scale = (1, 1, 1)

    def scale(self, sx, sy, sz):
        self._scale = (sx, sy, sz)


def install_freecad_value_types():
    """Install Vector / Placement / Rotation / Matrix on mock_FreeCAD.

    Handlers do ``FreeCAD.Vector(x, y, z)`` and arithmetic on the result, so
    plain MagicMock won't do — they need to behave like real value types.
    """
    mock_FreeCAD.Vector = _Vec
    mock_FreeCAD.Rotation = _Rotation
    mock_FreeCAD.Placement = _Placement
    mock_FreeCAD.Matrix = _Matrix


# Install once on import; reset_mocks() doesn't blow these away because
# they're class assignments, not state.
install_freecad_value_types()


# ---------------------------------------------------------------------------
# Document and object factories
# ---------------------------------------------------------------------------

def make_mock_doc(objects: Optional[Iterable[Any]] = None, name: str = "TestDoc"):
    """Create a mock FreeCAD document.

    Supports:
      * doc.getObject(name)  — exact internal name lookup
      * doc.getObjectsByLabel(label) — label fallback (e62ebc5)
      * doc.addObject(typeId, name) — returns a fresh MagicMock
      * doc.recompute() — no-op
      * doc.Objects, doc.Name
    """
    doc = MagicMock()
    doc.Name = name
    doc.Objects = list(objects) if objects else []

    def _get_object(n):
        for o in doc.Objects:
            if getattr(o, 'Name', None) == n:
                return o
        return None

    def _get_objects_by_label(label):
        return [o for o in doc.Objects if getattr(o, 'Label', None) == label]

    def _add_object(type_id, name=None):
        obj = MagicMock()
        obj.Name = name or f"{type_id}_auto"
        obj.Label = obj.Name
        obj.TypeId = type_id
        obj.Placement = _Placement()
        obj.Visibility = True
        doc.Objects.append(obj)
        return obj

    doc.getObject = _get_object
    doc.getObjectsByLabel = _get_objects_by_label
    doc.addObject = MagicMock(side_effect=_add_object)
    doc.copyObject = MagicMock(side_effect=lambda o: _add_object(getattr(o, 'TypeId', 'Part::Feature'),
                                                                  f"{o.Name}_copy"))
    doc.removeObject = MagicMock()
    doc.recompute = MagicMock()
    doc.FileName = ''
    return doc


def _make_shape(volume=1000.0, faces=6, edges=12, vertices=8,
                solids=1, wires=1, shells=1,
                bbox=(10.0, 10.0, 10.0), is_valid=True, is_closed=True):
    """Build a mock Part shape with the given geometric properties."""
    shape = MagicMock()
    shape.Volume = volume
    shape.Faces = [MagicMock(Area=volume / max(faces, 1)) for _ in range(faces)]
    shape.Edges = [MagicMock(Length=1.0) for _ in range(edges)]
    shape.Vertexes = [MagicMock() for _ in range(vertices)]
    shape.Solids = [MagicMock() for _ in range(solids)]
    shape.Wires = [MagicMock() for _ in range(wires)]
    shape.Shells = [MagicMock() for _ in range(shells)]

    bb = MagicMock()
    bb.XLength, bb.YLength, bb.ZLength = bbox
    bb.Center = _Vec(bbox[0] / 2, bbox[1] / 2, bbox[2] / 2)
    bb.XMin, bb.YMin, bb.ZMin = 0, 0, 0
    bb.XMax, bb.YMax, bb.ZMax = bbox
    shape.BoundBox = bb

    shape.isValid = MagicMock(return_value=is_valid)
    shape.isClosed = MagicMock(return_value=is_closed)
    shape.check = MagicMock()
    shape.copy = MagicMock(return_value=shape)
    shape.mirror = MagicMock(return_value=shape)
    shape.section = MagicMock(return_value=shape)
    shape.extrude = MagicMock(return_value=shape)
    shape.revolve = MagicMock(return_value=shape)
    shape.transformGeometry = MagicMock(return_value=shape)
    return shape


def make_part_object(name="Part", **shape_kwargs):
    """Mock Part::Feature with .Shape and .Placement.

    Pass keyword args (volume, faces, bbox, ...) to customize the shape.
    """
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "Part::Feature"
    obj.Placement = _Placement()
    obj.Visibility = True
    obj.Shape = _make_shape(**shape_kwargs)
    if hasattr(obj, 'Mesh'):
        del obj.Mesh
    return obj


_PARAMETRIC_QUANTITY_NAMES = frozenset({
    'Length', 'Width', 'Height', 'Radius', 'Radius1', 'Radius2', 'Angle',
})


class _Quantity:
    """Stand-in for a FreeCAD Quantity. Holds .Value, supports float()."""

    __slots__ = ('Value',)

    def __init__(self, value):
        self.Value = float(value)

    def __float__(self):
        return self.Value

    def __repr__(self):
        return f"_Quantity({self.Value})"


def _attach_parametric_setter(obj):
    """Make assignments to Length/Radius/etc. auto-rehydrate as _Quantity.

    FreeCAD's parametric primitive properties act like Quantity descriptors:
    ``obj.Length = 20.0`` stores a Quantity, so subsequent ``obj.Length.Value``
    still works. A plain MagicMock attribute would just become 20.0 and lose
    .Value. This setter intercepts assignments to known parametric names and
    wraps numeric values in our _Quantity stub.
    """
    original_setattr = type(obj).__setattr__

    def _setattr(self, name, value):
        if name in _PARAMETRIC_QUANTITY_NAMES and isinstance(value, (int, float)):
            value = _Quantity(value)
        original_setattr(self, name, value)

    # Per-instance __setattr__ override — type-level binding so the lookup hits
    type(obj).__setattr__ = _setattr


def make_box_object(name="Box", length=10.0, width=10.0, height=10.0,
                    placement=None):
    """Mock Part::Box (parametric primitive with Length/Width/Height)."""
    obj = make_part_object(name, volume=length * width * height,
                           bbox=(length, width, height))
    obj.TypeId = "Part::Box"
    obj.Length = _Quantity(length)
    obj.Width = _Quantity(width)
    obj.Height = _Quantity(height)
    _attach_parametric_setter(obj)
    if placement is not None:
        obj.Placement = placement
    return obj


def make_cylinder_object(name="Cylinder", radius=5.0, height=10.0):
    """Mock Part::Cylinder (parametric).

    MagicMock auto-creates any attribute on access, so the part_ops
    scale_object branches (which use ``hasattr``) would all match. Delete
    Length/Width/Radius2 so the cylinder branch wins over the box branch.
    """
    import math
    obj = make_part_object(name, volume=math.pi * radius * radius * height,
                           bbox=(2 * radius, 2 * radius, height))
    obj.TypeId = "Part::Cylinder"
    if hasattr(obj, 'Length'):
        del obj.Length
    if hasattr(obj, 'Width'):
        del obj.Width
    if hasattr(obj, 'Radius2'):
        del obj.Radius2
    obj.Radius = _Quantity(radius)
    obj.Height = _Quantity(height)
    _attach_parametric_setter(obj)
    return obj


def make_sphere_object(name="Sphere", radius=5.0):
    """Mock Part::Sphere (parametric)."""
    import math
    obj = make_part_object(name, volume=(4.0 / 3.0) * math.pi * radius ** 3,
                           bbox=(2 * radius, 2 * radius, 2 * radius))
    obj.TypeId = "Part::Sphere"
    obj.Radius = _Quantity(radius)
    if hasattr(obj, 'Height'):
        del obj.Height
    if hasattr(obj, 'Length'):
        del obj.Length
    if hasattr(obj, 'Width'):
        del obj.Width
    _attach_parametric_setter(obj)
    return obj


def make_sketch(name="Sketch", has_wires=True, has_faces=False,
                geometry_count=4):
    """Mock Sketcher::SketchObject.

    Has a .Shape with optional Wires/Faces (so part_ops.extrude / revolve
    can find a profile). geometry_count controls .GeometryCount for sketch
    constraint tests.
    """
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "Sketcher::SketchObject"
    obj.Placement = _Placement()

    shape = MagicMock()
    shape.Wires = [MagicMock()] if has_wires else []
    shape.Faces = [MagicMock()] if has_faces else []
    shape.Edges = [MagicMock(Length=1.0) for _ in range(4)]
    obj.Shape = shape

    obj.GeometryCount = geometry_count
    obj.Geometry = [MagicMock() for _ in range(geometry_count)]
    obj.Constraints = []

    obj.getConstruction = MagicMock(return_value=False)
    obj.addGeometry = MagicMock(return_value=geometry_count)
    obj.addConstraint = MagicMock(return_value=0)
    obj.delConstraint = MagicMock()
    obj.getOpenVertices = MagicMock(return_value=[])
    obj.detectMissingPointOnPointConstraints = MagicMock(return_value=0)
    obj.getMissingPointOnPointConstraints = MagicMock(return_value=[])
    return obj


def make_body(name="Body", tip=None, group=None):
    """Mock PartDesign::Body.

    body.newObject returns a stable MagicMock via return_value so callers
    can inspect ``body.newObject.return_value.Length`` etc. The mock has
    sensible defaults (Name, Label, TypeId, Placement, Shape, State=[])
    so handler-side patterns like ``getattr(feat, 'State', [])`` work.
    """
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "PartDesign::Body"
    obj.Placement = _Placement()
    obj.Tip = tip
    obj.Group = list(group) if group else []
    obj.Shape = _make_shape()

    feat = MagicMock()
    feat.Name = "AutoFeature"
    feat.Label = "AutoFeature"
    feat.TypeId = "PartDesign::Feature"
    feat.Placement = _Placement()
    feat.Shape = _make_shape()
    feat.State = []
    obj.newObject = MagicMock(return_value=feat)
    return obj


# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------

def make_handler(handler_cls, server=None):
    """Instantiate a handler with mocked server and logging.

    The server mock has a .selector for selection-flow tests, and a
    _run_on_gui_thread that just runs the task inline. The selector's
    request_selection returns a canonical awaiting_selection payload by
    default — tests that need a different operation_id can patch it.
    """
    if server is None:
        server = MagicMock()
        server.selector = MagicMock()
        server.selector.request_selection = MagicMock(return_value={
            "status": "awaiting_selection",
            "operation_id": "op_test_001",
            "tool_name": "test",
            "selection_type": "edges",
            "message": "Please select edges in FreeCAD",
        })
        server.selector.complete_selection = MagicMock(return_value=None)
        server.selector.cancel_selection = MagicMock()
        server._run_on_gui_thread = MagicMock(side_effect=lambda fn, timeout=30.0: fn())
    log_op = MagicMock()
    capture = MagicMock(return_value={})
    return handler_cls(server, log_op, capture)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def assert_dispatched(test_case, result):
    """Fail the test if the handler returned an obvious dispatch failure.

    A real "Unknown operation" error means the operation name didn't route
    to a handler method at all. Our unit tests directly call the method, so
    this should never appear — but cheap to assert.
    """
    s = result if isinstance(result, str) else str(result)
    test_case.assertNotIn("Unknown operation", s,
                          f"Operation did not dispatch: {s[:200]}")


def assert_error_contains(test_case, result, *substrings):
    """Assert the result string contains every given substring (case-insensitive).

    Usage:
        assert_error_contains(self, result, "not found", "MyObj")
    """
    s = (result if isinstance(result, str) else str(result)).lower()
    for sub in substrings:
        test_case.assertIn(sub.lower(), s,
                           f"Expected '{sub}' in result, got: {s[:200]}")


def assert_success_contains(test_case, result, *substrings):
    """Assert the result indicates success and contains expected text.

    Treats any 'Error' prefix or 'Unknown' in the result as failure.
    """
    s = result if isinstance(result, str) else str(result)
    test_case.assertNotIn("Unknown operation", s,
                          f"Dispatch failed: {s[:200]}")
    test_case.assertFalse(s.startswith("Error"),
                          f"Expected success, got error: {s[:200]}")
    for sub in substrings:
        test_case.assertIn(sub, s,
                           f"Expected '{sub}' in success result, got: {s[:200]}")


# ---------------------------------------------------------------------------
# Selection-flow harness (for partdesign fillet/chamfer/hole/draft/shell/thickness)
# ---------------------------------------------------------------------------

def assert_awaiting_selection(test_case, result):
    """Assert a handler returned the 'awaiting_selection' handshake.

    Ops that need user picks (fillet, chamfer, hole, draft, shell, thickness)
    return a dict-like response with status=awaiting_selection and an
    operation_id. Accepts either a JSON string or a dict.
    """
    import json
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            test_case.fail(f"Expected awaiting_selection JSON, got plain string: {result[:200]}")
    test_case.assertIsInstance(result, dict, f"Expected dict response, got {type(result)}")
    test_case.assertEqual(result.get("status"), "awaiting_selection",
                          f"Expected status=awaiting_selection, got {result}")
    test_case.assertIn("operation_id", result, f"Missing operation_id in {result}")
    return result["operation_id"]


def make_selector_with_picks(picks: List[Dict[str, Any]]):
    """Build a selector mock that returns the given picks on complete_selection.

    picks: list of {"object": "Box", "element": "Edge1"} dicts.
    """
    selector = MagicMock()
    selector.start_selection = MagicMock(return_value="op_test_001")
    selector.complete_selection = MagicMock(return_value={
        "operation_id": "op_test_001",
        "selections": picks,
    })
    selector.cancel_selection = MagicMock()
    return selector

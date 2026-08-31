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

import math
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

# Assembly workbench — assembly_ops.py does `import UtilsAssembly` /
# `import JointObject` inside method bodies (real FreeCAD modules, not
# importable outside a running FreeCAD). Tests configure Joint/GroundedJoint/
# getJointGroup as needed per-case.
mock_UtilsAssembly = _adopt_or_create('UtilsAssembly')
mock_JointObject = _adopt_or_create('JointObject')

# CAM workbench imports — handlers do `from Path.Tool.Bit import ToolBit`,
# `from Path.Tool.Controller import Create`, `from Path.Post.Processor
# import PostProcessorFactory` etc. inside method bodies. Pre-populate
# the module hierarchy so those imports succeed.
mock_Path = _adopt_or_create('Path')
mock_Path_Main = _adopt_or_create('Path.Main')
mock_Path_Main_Job = _adopt_or_create('Path.Main.Job')
mock_Path_Main_Stock = _adopt_or_create('Path.Main.Stock')
mock_Path_Main_Gui = _adopt_or_create('Path.Main.Gui')
mock_Path_Main_Gui_Job = _adopt_or_create('Path.Main.Gui.Job')
mock_Path_Tool = _adopt_or_create('Path.Tool')
mock_Path_Tool_Bit = _adopt_or_create('Path.Tool.Bit')
mock_Path_Tool_Controller = _adopt_or_create('Path.Tool.Controller')
mock_Path_Op = _adopt_or_create('Path.Op')
mock_Path_Op_Profile = _adopt_or_create('Path.Op.Profile')
mock_Path_Op_Pocket = _adopt_or_create('Path.Op.Pocket')
mock_Path_Op_Drilling = _adopt_or_create('Path.Op.Drilling')
mock_Path_Op_Adaptive = _adopt_or_create('Path.Op.Adaptive')
mock_Path_Post = _adopt_or_create('Path.Post')
mock_Path_Post_Processor = _adopt_or_create('Path.Post.Processor')

# Make handler imports resolvable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))


# ---------------------------------------------------------------------------
# spec= hardening (H8, scoped)
#
# A bare MagicMock() auto-vivifies ANY attribute access, so a handler calling
# a typo'd or nonexistent FreeCAD API name (e.g. FreeCAD.newDoc() instead of
# newDocument()) passes every unit test yet crashes against real FreeCAD.
#
# A full spec against FreeCAD's real classes is not available in this
# environment: these tests deliberately run WITHOUT FreeCAD installed, so
# there is no real FreeCAD/Part/Draft/Sketcher object to introspect, and
# hand-authoring a complete spec from FreeCAD's C++-bound API surface would
# be guesswork with a high chance of missing a legitimately-used attribute
# and breaking real (currently-passing) tests. That full retrofit is out of
# scope here.
#
# What IS available: this repo's own source is the ground truth for which
# attributes on each module are actually called. The allowlists below were
# generated by grepping AICopilot/ for `FreeCAD.<name>`, `Part.<name>`, etc.
# and verified against every dynamic (non-literal-attribute) access site.
# `spec=` (not `spec_set=`) restricts attribute READS to this list — the
# exact bug class this finding is about — while leaving WRITES unrestricted,
# so any attribute a test file sets that this grep didn't catch keeps working
# instead of breaking the suite. Modules with no discovered direct attribute
# usage (Spreadsheet, PartDesign, Path and its submodules — all referenced
# only via addObject() type-id strings or `from X import Y`) are left
# unspecced rather than guessed at.
# ---------------------------------------------------------------------------

_FREECAD_SPEC = [
    'ActiveDocument', 'Console', 'Document', 'GuiUp', 'Matrix', 'Placement',
    'Rotation', 'Units', 'Vector', 'Version',
    'getDocument', 'getHomePath', 'getResourceDir', 'getUserAppDataDir',
    'getUserMacroDir', 'listDocuments', 'newDocument', 'openDocument',
    'ParamGet',
    # Dynamically assigned onto the FreeCAD module itself by InitGui.py /
    # headless_server.py as ad hoc global state, not read via a literal
    # `FreeCAD.__ai_...` attribute expression that grep alone would catch
    # as "usage" in the same way — listed explicitly since they're real.
    '__ai_global_service', '__ai_socket_server',
]
_PART_SPEC = [
    'ArcOfCircle', 'Circle', 'export', 'Face', 'insert', 'LineSegment',
    'makeBox', 'makeCompound', 'makeLongHelix', 'makePlane', 'makeSolid',
    'makeWireString', 'Shape',
]
_DRAFT_SPEC = [
    'make_clone', 'make_ortho_array', 'make_path_array', 'make_point_array',
    'make_polar_array', 'make_text',
]
_SKETCHER_SPEC = ['Constraint', 'SketchObject']
_MESH_SPEC = ['export', 'Mesh']
_MESHPART_SPEC = ['meshFromShape']


def _apply_spec(mock_obj: MagicMock, names: list) -> None:
    """Restrict attribute reads on an already-constructed module mock to a
    known-used allowlist, without disturbing already-configured children —
    mock_add_spec (unlike passing spec= at construction time) can be applied
    after the fact, which matters here since these modules may already be
    populated via _adopt_or_create() by whichever test file imported first.
    """
    mock_obj.mock_add_spec(names)


_apply_spec(mock_FreeCAD, _FREECAD_SPEC)
_apply_spec(mock_Part, _PART_SPEC)
_apply_spec(mock_Draft, _DRAFT_SPEC)
_apply_spec(mock_Sketcher, _SKETCHER_SPEC)
_apply_spec(mock_Mesh, _MESH_SPEC)
_apply_spec(mock_MeshPart, _MESHPART_SPEC)


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
              mock_Mesh, mock_MeshPart, mock_UtilsAssembly, mock_JointObject):
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
    sys.modules['UtilsAssembly'] = mock_UtilsAssembly
    sys.modules['JointObject'] = mock_JointObject
    sys.modules['Path'] = mock_Path
    sys.modules['Path.Main'] = mock_Path_Main
    sys.modules['Path.Main.Job'] = mock_Path_Main_Job
    sys.modules['Path.Main.Stock'] = mock_Path_Main_Stock
    sys.modules['Path.Main.Gui'] = mock_Path_Main_Gui
    sys.modules['Path.Main.Gui.Job'] = mock_Path_Main_Gui_Job
    sys.modules['Path.Tool'] = mock_Path_Tool
    sys.modules['Path.Tool.Bit'] = mock_Path_Tool_Bit
    sys.modules['Path.Tool.Controller'] = mock_Path_Tool_Controller
    sys.modules['Path.Op'] = mock_Path_Op
    sys.modules['Path.Op.Profile'] = mock_Path_Op_Profile
    sys.modules['Path.Op.Pocket'] = mock_Path_Op_Pocket
    sys.modules['Path.Op.Drilling'] = mock_Path_Op_Drilling
    sys.modules['Path.Op.Adaptive'] = mock_Path_Op_Adaptive
    sys.modules['Path.Post'] = mock_Path_Post
    sys.modules['Path.Post.Processor'] = mock_Path_Post_Processor

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

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other):
        return _Vec(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )


class _Rotation:
    """Stand-in for FreeCAD.Rotation. Accepts (), (axis, angle), or (q1,q2,q3,q4).

    FreeCAD's real Rotation has many overloads; we only need
    enough to survive being constructed and to support .multiply/.multVec.
    """

    def __init__(self, *args):
        if len(args) == 0:
            self.axis = _Vec(0, 0, 1)
            self.angle = 0
        elif len(args) == 2:
            self.axis = args[0] if args[0] is not None else _Vec(0, 0, 1)
            self.angle = args[1]
        elif len(args) == 4:
            # Quaternion form (q1, q2, q3, q4)
            self.axis = _Vec(args[0], args[1], args[2])
            self.angle = args[3]
        else:
            self.axis = _Vec(0, 0, 1)
            self.angle = 0

    def multiply(self, other):
        return _Rotation(self.axis, self.angle + other.angle)

    def inverted(self):
        """Inverse of an axis-angle rotation: same axis, negated angle."""
        return _Rotation(self.axis, -self.angle)

    def multVec(self, vec):
        """Rotate vec by this axis-angle rotation (angle in degrees) via
        Rodrigues' formula — stand-in for FreeCAD.Rotation.multVec.
        Verified against real FreeCAD: rotating local (0,0,1) by
        Rotation((1,0,0), 90) gives (0,-1,0), matching XZ_Plane's real
        Placement empirically."""
        length = self.axis.Length
        if length == 0:
            return _Vec(vec.x, vec.y, vec.z)
        k = _Vec(self.axis.x / length, self.axis.y / length, self.axis.z / length)
        theta = math.radians(self.angle)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        k_dot_v = k.dot(vec)
        k_cross_v = k.cross(vec)
        one_minus_cos = 1 - cos_t
        return _Vec(
            vec.x * cos_t + k_cross_v.x * sin_t + k.x * k_dot_v * one_minus_cos,
            vec.y * cos_t + k_cross_v.y * sin_t + k.y * k_dot_v * one_minus_cos,
            vec.z * cos_t + k_cross_v.z * sin_t + k.z * k_dot_v * one_minus_cos,
        )


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
        # A freshly created object reports a valid, non-null shape by default so
        # handlers that verify the result before hiding sources (boolean ops) see
        # success. Tests wanting a failed op override obj.Shape.isNull explicitly.
        obj.Shape.isNull = MagicMock(return_value=False)
        obj.Shape.isValid = MagicMock(return_value=True)
        doc.Objects.append(obj)
        return obj

    doc.getObject = _get_object
    doc.getObjectsByLabel = _get_objects_by_label
    doc.addObject = MagicMock(side_effect=_add_object)
    doc.copyObject = MagicMock(side_effect=lambda o, with_deps=False: _add_object(
        getattr(o, 'TypeId', 'Part::Feature'), f"{o.Name}_copy"))
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

    # Defaults to the bbox center — a plausible-but-not-necessarily-real
    # center of mass for a uniform box. Tests that specifically need a
    # different CenterOfMass (e.g. asymmetric/mirrored geometry) should
    # override shape.CenterOfMass directly.
    shape.CenterOfMass = _Vec(bbox[0] / 2, bbox[1] / 2, bbox[2] / 2)

    shape.isValid = MagicMock(return_value=is_valid)
    shape.isNull = MagicMock(return_value=not is_valid)
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


class VarSetPropError(Exception):
    """Stand-in for the FreeCAD C++ exceptions (Base::NameError,
    Base::ValueError, ...) that addProperty/enum assignment raise — real
    FreeCAD surfaces these to Python as regular exceptions carrying its own
    message text, and varset_ops.py's handler methods catch bare Exception,
    so any exception type works here as long as the message matches."""
    pass


# Mirrors varset_ops._PROP_DYNAMIC_BIT (Property::PropDynamic, src/App/Property.h) —
# duplicated here only because this is a fixture faking FreeCAD's own behavior,
# not a business rule the handler and its tests should share a single source for.
_MOCK_PROP_DYNAMIC_BIT = 21

_VARSET_DEFAULT_VALUES = {
    'App::PropertyLength': 0.0, 'App::PropertyDistance': 0.0, 'App::PropertyFloat': 0.0,
    'App::PropertyAngle': 0.0, 'App::PropertyArea': 0.0, 'App::PropertyVolume': 0.0,
    'App::PropertyInteger': 0, 'App::PropertyString': '', 'App::PropertyBool': False,
    'App::PropertyEnumeration': None, 'App::PropertyLink': None,
}

_VARSET_QUANTITY_TYPES = {
    'App::PropertyLength', 'App::PropertyDistance', 'App::PropertyFloat',
    'App::PropertyAngle', 'App::PropertyArea', 'App::PropertyVolume',
}

_VARSET_SUPPORTED_TYPES = sorted(set(_VARSET_DEFAULT_VALUES) | {
    'App::PropertyPlacement', 'App::PropertyColor', 'App::PropertyVector',
})


def _check_varset_value_type(type_, value):
    """Raise VarSetPropError for a value real FreeCAD's C++ property
    setters would reject -- e.g. None, or a str assigned to an Integer.
    Only covers the scalar types this mock's __setattr__ actually
    intercepts (quantity/int/string/bool); other types are left
    unchecked, matching the rest of this mock's scoped-fidelity approach.
    """
    if type_ in _VARSET_QUANTITY_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise VarSetPropError(
                f"value must be a Quantity, float, int, or unit string for "
                f"{type_}, got {type(value).__name__}"
            )
    elif type_ == 'App::PropertyInteger':
        if isinstance(value, bool) or not isinstance(value, int):
            raise VarSetPropError(f"value must be int for {type_}, got {type(value).__name__}")
    elif type_ == 'App::PropertyString':
        if not isinstance(value, str):
            raise VarSetPropError(f"value must be str for {type_}, got {type(value).__name__}")
    elif type_ == 'App::PropertyBool':
        if not isinstance(value, bool):
            raise VarSetPropError(f"value must be bool for {type_}, got {type(value).__name__}")


class MockQuantity:
    """Minimal stand-in for FreeCAD.Units.Quantity — just enough surface
    (.Value, .getValueAs, .UserString) for varset_ops._property_value_for_json's
    hasattr(raw, 'getValueAs') branch."""

    def __init__(self, value, unit='mm'):
        self.Value = float(value)
        self._unit = unit

    def getValueAs(self, unit):
        return self.Value

    @property
    def UserString(self):
        return f"{self.Value:.2f} {self._unit}"


class MockVarSet:
    """Mock App::VarSet that faithfully replicates the source-verified
    FreeCAD behaviors varset_ops.py's handler depends on (see
    SPEC-varset-operations.md's review notes):

      * addProperty raises on a duplicate name — it never silently
        overwrites the existing property (DynamicProperty.cpp:253-254).
      * removeProperty returns a bool; it never raises for locked or
        non-dynamic properties (that exception path is unreachable from
        Python — DocumentObjectPyImp.cpp:153-162 + DocumentObject.cpp:831-834).
      * PropertyEnumeration overloads one attribute for the allowed-values
        list vs. the current value: assigning a list sets the options,
        assigning a string sets the value (raising if it's not in the
        list), assigning an int silently no-ops (PropertyStandard.cpp).
      * getPropertyStatus reports the PropDynamic bit (21) as a bare int
        for dynamic properties (it has no name in FreeCAD's status-name
        table) plus named strings (LockDynamic, Hidden, ReadOnly) for
        whichever flags addProperty set.
      * PropertiesList on a bare VarSet is ['ExpressionEngine', 'Label',
        'Label2', 'Visibility'] -- NOT ['Placement', 'Label', 'Visibility'].
        Confirmed live (2026-08-31, full-review task_fb07efed's follow-up
        integration-test run): App::VarSet has no Placement property at
        all, unlike most DocumentObjects -- an earlier version of this mock
        assumed it did, and a live-only integration test caught it (a unit
        test against this same fictitious mock could not have).
    """

    def __init__(self, name="VarSet"):
        self.Name = name
        self.Label = name
        self.Label2 = ""
        self.TypeId = "App::VarSet"
        self._props = {}
        self._values = {}
        self._enum_options = {}
        self.ExpressionEngine = []
        self.setExpression = MagicMock(
            side_effect=lambda prop, expr: self.ExpressionEngine.append((prop, expr))
        )

    @property
    def PropertiesList(self):
        return ['ExpressionEngine', 'Label', 'Label2', 'Visibility'] + list(self._props.keys())

    def supportedProperties(self):
        return list(_VARSET_SUPPORTED_TYPES)

    def addProperty(self, type_, name, group='', doc='', attr=0,
                     read_only=False, hidden=False, locked=False, enum_vals=None):
        if name in self._props:
            raise VarSetPropError(f"Property {self.Name}.{name} already exists")
        self._props[name] = {
            'type': type_, 'group': group, 'doc': doc,
            'locked': bool(locked), 'hidden': bool(hidden), 'read_only': bool(read_only),
        }
        if type_ == 'App::PropertyEnumeration':
            self._enum_options[name] = list(enum_vals) if enum_vals else None
            self._values[name] = enum_vals[0] if enum_vals else None
        else:
            self._values[name] = _VARSET_DEFAULT_VALUES.get(type_)
        return self

    def removeProperty(self, name):
        info = self._props.get(name)
        if info is None or info['locked']:
            return False
        del self._props[name]
        self._values.pop(name, None)
        self._enum_options.pop(name, None)
        return True

    def getTypeIdOfProperty(self, name):
        builtin = {'Label': 'App::PropertyString', 'Label2': 'App::PropertyString',
                   'Visibility': 'App::PropertyBool', 'ExpressionEngine': 'App::PropertyExpressionEngine'}
        if name in builtin:
            return builtin[name]
        return self._props[name]['type']

    def getGroupOfProperty(self, name):
        return self._props.get(name, {}).get('group', '')

    def getPropertyStatus(self, name=""):
        if name not in self._props:
            return []
        info = self._props[name]
        status = [_MOCK_PROP_DYNAMIC_BIT]
        if info['locked']:
            status.append('LockDynamic')
        if info['hidden']:
            status.append('Hidden')
        if info['read_only']:
            status.append('ReadOnly')
        return status

    def getEnumerationsOfProperty(self, name):
        return self._enum_options.get(name)

    def __getattr__(self, name):
        # Only reached when normal attribute lookup fails — i.e. for
        # dynamic properties, matching FreeCAD's real attribute proxy.
        values = self.__dict__.get('_values', {})
        if name in values:
            value = values[name]
            type_ = self._props[name]['type']
            if type_ in _VARSET_QUANTITY_TYPES and isinstance(value, (int, float)):
                return MockQuantity(value)
            return value
        raise AttributeError(name)

    def __setattr__(self, name, value):
        props = self.__dict__.get('_props')
        if props is not None and name in props:
            info = props[name]
            if info['type'] == 'App::PropertyEnumeration':
                if isinstance(value, (list, tuple)):
                    self.__dict__['_enum_options'][name] = list(value)
                elif isinstance(value, bool):
                    pass  # bool is an int subclass — silent no-op, same as int
                elif isinstance(value, str):
                    options = self.__dict__['_enum_options'].get(name)
                    if not options or value not in options:
                        raise VarSetPropError(
                            f"'{value}' is not a valid enumeration for {self.Name}.{name}"
                        )
                    self.__dict__['_values'][name] = value
                elif isinstance(value, int):
                    pass  # silent no-op — matches PropertyStandard.cpp's real behavior
                return
            _check_varset_value_type(info['type'], value)
            self.__dict__['_values'][name] = value
            return
        object.__setattr__(self, name, value)


def make_varset(name="VarSet"):
    """Factory matching this file's make_X(name) convention, wrapping MockVarSet."""
    return MockVarSet(name)


def make_dep_edge(from_obj, to_prop, from_prop="ExpressionEngine"):
    """Mock App::DepEdge — FromObj/FromProp/ToObj/ToProp, as returned by
    getInListProp() on FreeCAD builds >= weekly-2026.06.24. FromProp is
    hardcoded to the literal "ExpressionEngine" for expression-derived
    edges in real FreeCAD (DocumentObject.cpp) — that's the default here too.
    """
    edge = MagicMock()
    edge.FromObj = from_obj
    edge.FromProp = from_prop
    edge.ToProp = to_prop
    return edge


def make_spreadsheet(name="Spreadsheet"):
    """Mock Spreadsheet::Sheet with set/get/setAlias/getAlias/clear methods."""
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "Spreadsheet::Sheet"
    obj.Placement = _Placement()
    # In-memory cell store
    cells_data = {}
    aliases = {}

    def _set(cell, value):
        cells_data[cell.upper()] = value

    def _get(cell):
        return cells_data.get(cell.upper(), '')

    def _set_alias(cell, alias):
        aliases[cell.upper()] = alias

    def _get_alias(cell):
        return aliases.get(cell.upper(), None)

    def _clear(cell):
        cells_data.pop(cell.upper(), None)

    obj.set = MagicMock(side_effect=_set)
    obj.get = MagicMock(side_effect=_get)
    obj.setAlias = MagicMock(side_effect=_set_alias)
    obj.getAlias = MagicMock(side_effect=_get_alias)
    obj.clear = MagicMock(side_effect=_clear)
    obj._cells_data = cells_data
    obj._aliases = aliases

    cells = MagicMock()
    cells.Content = '<cells></cells>'
    obj.cells = cells
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

    # Mock App::Origin with the 6 standard OriginFeatures every real
    # PartDesign::Body has (confirmed live) -- needed by any operation
    # that references a Body's origin planes/axes (mirror_feature's
    # MirrorPlane, linear_pattern's Direction, polar_pattern's Axis).
    origin = MagicMock()
    origin_features = []
    for feat_name in ("X_Axis", "Y_Axis", "Z_Axis", "XY_Plane", "XZ_Plane", "YZ_Plane"):
        of = MagicMock()
        of.Name = feat_name
        origin_features.append(of)
    origin.OriginFeatures = origin_features
    obj.Origin = origin

    feat = MagicMock()
    feat.Name = "AutoFeature"
    feat.Label = "AutoFeature"
    feat.TypeId = "PartDesign::Feature"
    feat.Placement = _Placement()
    feat.Shape = _make_shape()
    feat.State = []
    obj.newObject = MagicMock(return_value=feat)
    return obj


def make_assembly(name="Assembly", group=None):
    """Mock Assembly::AssemblyObject.

    assembly.newObject(type_id, name) creates a fresh MagicMock link object
    (Name/Label/TypeId/Placement/LinkedObject) and appends it to .Group, so
    add_component tests can assert on both the return value and the group's
    new contents. isDerivedFrom defaults to False (plain part) — tests
    checking the Assembly::AssemblyLink branch must override it explicitly,
    since a bare MagicMock().isDerivedFrom(...) is truthy by default and
    would silently misclassify every linked object as a sub-assembly.
    """
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "Assembly::AssemblyObject"
    obj.Type = "Assembly"
    obj.Placement = _Placement()
    obj.Group = list(group) if group else []
    # list_joints reads the assembly's Assembly::JointGroup child (found by
    # scanning OutList) rather than the .Joints property, since .Joints
    # never includes GroundedJoint objects (confirmed live 2026-08-21,
    # fixed same day) -- default to no JointGroup present (list_joints'
    # "has no joints" path); tests needing joints/groundings should mock
    # a JointGroup-typed object into OutList with the desired .Group
    # contents, matching the real Assembly::JointGroup shape.
    obj.OutList = []
    obj.isDerivedFrom = MagicMock(
        side_effect=lambda t: t == "Assembly::AssemblyObject"
    )

    def _new_object(type_id, link_name=None):
        link = MagicMock()
        link.Name = link_name or f"{type_id}_auto"
        link.Label = link.Name
        link.TypeId = type_id
        link.Placement = _Placement()
        link.LinkedObject = None
        obj.Group.append(link)
        return link

    obj.newObject = MagicMock(side_effect=_new_object)
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

"""ocl_surface_op.py — OCL PathDropCutter surface operation for FreeCAD CAM

A proper Path::FeaturePython operation that uses OpenCAMLib PathDropCutter
to generate 3D surface toolpaths directly from STL files.

Why this exists instead of using FreeCAD's built-in Surface operation:
  - FreeCAD's Surface op requires tessellating a BREP solid via BRepMesh.
    For mesh-derived BREP shells (makeShapeFromMesh), BRepMesh throws a
    C++ exception (GH #27752) causing a crash or timeout.
  - This op bypasses that entirely: reads STL directly into ocl.STLSurf,
    runs PathDropCutter, converts CL points to Path.Command objects.
  - Active=True always → normal job consolidation → normal post-processing.
  - No workarounds, no Active=False, no manual G-code generation.

Usage (via MCP):
    cam_operations(operation="surface_stl",
                   job_name="Job",
                   stl_file="/path/to/mesh.stl",
                   tool_diameter=1.0,
                   stepover=0.75,
                   sample_interval=0.5,
                   safe_height=8.0,
                   cut_feed=400.0,
                   plunge_feed=150.0)

Document persistence:
    FreeCAD serializes Path::FeaturePython proxies by module/class name.
    This module must remain importable in FreeCAD's Python environment
    at the same path for saved documents to reload correctly.
"""

import os
import struct
import math

import FreeCAD
import Path


# ---------------------------------------------------------------------------
# Module-level helpers (no OCL dependency at import time)
# ---------------------------------------------------------------------------

def _load_stl(stl_file, ocl):
    """Read a binary STL file into an ocl.STLSurf.

    Returns (stl_surf, x_min, x_max, y_min, y_max).
    Raises ValueError on truncated/invalid file.
    """
    stl_surf = ocl.STLSurf()
    x_min = y_min = math.inf
    x_max = y_max = -math.inf

    with open(stl_file, "rb") as f:
        f.read(80)  # header (ignored)
        raw = f.read(4)
        if len(raw) < 4:
            raise ValueError(f"Truncated STL file: {stl_file}")
        n_tris = struct.unpack("<I", raw)[0]

        for _ in range(n_tris):
            f.read(12)  # normal vector (ignored — OCL recomputes)
            raw = f.read(36)
            if len(raw) < 36:
                raise ValueError(f"Truncated triangle data in: {stl_file}")
            v = struct.unpack("<9f", raw)
            f.read(2)  # attribute byte count

            stl_surf.addTriangle(ocl.Triangle(
                ocl.Point(v[0], v[1], v[2]),
                ocl.Point(v[3], v[4], v[5]),
                ocl.Point(v[6], v[7], v[8]),
            ))

            for i in range(3):
                x, y = v[i * 3], v[i * 3 + 1]
                if x < x_min: x_min = x
                if x > x_max: x_max = x
                if y < y_min: y_min = y
                if y > y_max: y_max = y

    return stl_surf, x_min, x_max, y_min, y_max


def _build_zigzag_scan(ocl, x_min, x_max, y_min, y_max, stepover):
    """Build an ocl.Path of alternating-direction scan lines (zigzag).

    Returns (ocl_path, n_lines).
    """
    scan_path = ocl.Path()
    n_lines = 0
    y = y_min
    forward = True

    while y <= y_max + 1e-9:
        if forward:
            scan_path.append(ocl.Line(
                ocl.Point(x_min, y, 0.0),
                ocl.Point(x_max, y, 0.0),
            ))
        else:
            scan_path.append(ocl.Line(
                ocl.Point(x_max, y, 0.0),
                ocl.Point(x_min, y, 0.0),
            ))
        y += stepover
        forward = not forward
        n_lines += 1

    return scan_path, n_lines


def _cl_points_to_commands(pts, safe_z, cut_feed, plunge_feed):
    """Convert a list of ocl CL points to Path.Command objects.

    Scan line transitions: retract to safe_z, rapid to XY start, plunge.
    Within a scan line: G1 feed moves with cut_feed.

    Feed rates: Path.Command F values must be in FreeCAD's internal
    velocity unit (mm/s). The GRBL post-processor multiplies by 60 to
    produce mm/min in the G-code output. Callers pass mm/min; we ÷60.
    """
    cut_feed_mms    = cut_feed    / 60.0
    plunge_feed_mms = plunge_feed / 60.0

    cmds = [Path.Command("G0", {"Z": safe_z})]
    current_y = None

    for pt in pts:
        x, y, z = pt.x, pt.y, pt.z

        if current_y is None or abs(y - current_y) > 1e-6:
            # New scan line: retract → rapid position → plunge
            cmds.append(Path.Command("G0", {"Z": safe_z}))
            cmds.append(Path.Command("G0", {"X": x, "Y": y}))
            cmds.append(Path.Command("G1", {"Z": z, "F": plunge_feed_mms}))
            current_y = y
        else:
            cmds.append(Path.Command("G1", {"X": x, "Y": y, "Z": z, "F": cut_feed_mms}))

    cmds.append(Path.Command("G0", {"Z": safe_z}))
    return cmds


def _estimate_cycle_time(pts, cut_feed, plunge_feed, safe_z, n_lines):
    """Rough cycle time estimate in minutes."""
    cut_dist = 0.0
    prev = None
    for pt in pts:
        if prev is not None and abs(pt.y - prev.y) < 1e-6:
            dx = pt.x - prev.x
            dy = pt.y - prev.y
            dz = pt.z - prev.z
            cut_dist += math.sqrt(dx * dx + dy * dy + dz * dz)
        prev = pt

    cut_min = cut_dist / cut_feed if cut_feed > 0 else 0.0
    # Rough retract overhead: each scan line retracts safe_z at ~3000 mm/min
    retract_min = n_lines * (safe_z * 2) / 3000.0
    return cut_min + retract_min


# ---------------------------------------------------------------------------
# FreeCAD proxy class
# ---------------------------------------------------------------------------

class OCLSurfaceProxy:
    """Proxy for a Path::FeaturePython OCL surface machining operation.

    FreeCAD identifies this class by module + class name when saving/loading
    documents.  Do not rename without updating existing saved documents.
    """

    def __init__(self, obj):
        """Set up properties and bind proxy to obj."""
        self._add_properties(obj)
        obj.Proxy = self

    # ------------------------------------------------------------------
    # Property setup
    # ------------------------------------------------------------------

    def _add_properties(self, obj):
        """Add custom properties (idempotent — safe to call on reload)."""

        def _add(prop_type, name, group, doc, default=None):
            if not hasattr(obj, name):
                obj.addProperty(prop_type, name, group, doc)
                if default is not None:
                    setattr(obj, name, default)

        _add("App::PropertyFile",   "StlFile",        "OCL Surface",
             "Path to the STL mesh file to machine")
        _add("App::PropertyFloat",  "ToolDiameter",   "OCL Surface",
             "Ball end mill diameter (mm)",            1.0)
        _add("App::PropertyFloat",  "StepOver",       "OCL Surface",
             "Step between Y scan lines (mm)",         0.75)
        _add("App::PropertyFloat",  "SampleInterval", "OCL Surface",
             "X sampling interval along scan line (mm)", 0.5)
        _add("App::PropertyFloat",  "SafeHeight",     "OCL Surface",
             "Safe Z height for rapid moves (mm)",     8.0)
        _add("App::PropertyFloat",  "CutFeed",        "OCL Surface",
             "Horizontal feed rate (mm/min)",          400.0)
        _add("App::PropertyFloat",  "PlungeFeed",     "OCL Surface",
             "Plunge feed rate (mm/min)",              150.0)
        _add("App::PropertyBool",   "Active",         "Base",
             "Include this operation in the job path", True)
        _add("App::PropertyString", "CycleTime",      "Base",
             "Estimated cycle time (informational)",   "N/A")

    # ------------------------------------------------------------------
    # FreeCAD lifecycle
    # ------------------------------------------------------------------

    def execute(self, obj):
        """Called by FreeCAD when the object needs recomputing."""
        try:
            self._do_execute(obj)
        except Exception as e:
            import traceback
            FreeCAD.Console.PrintError(
                f"[OCLSurface] execute() failed: {e}\n"
                f"{traceback.format_exc()}\n"
            )

    def _do_execute(self, obj):
        """Inner execute — raises on error so the outer handler can log it."""
        import time

        # Lazy OCL import — only fails at execution time, not module load
        try:
            from opencamlib import ocl
        except ImportError as e:
            raise ImportError(
                "opencamlib is not installed in FreeCAD's Python environment. "
                f"Original error: {e}"
            )

        stl_file = getattr(obj, "StlFile", "")
        if not stl_file:
            FreeCAD.Console.PrintWarning("[OCLSurface] StlFile not set — skipping\n")
            return
        if not os.path.exists(stl_file):
            raise FileNotFoundError(f"STL file not found: {stl_file!r}")

        tool_dia    = float(obj.ToolDiameter)
        stepover    = float(obj.StepOver)
        sampling    = float(obj.SampleInterval)
        safe_z      = float(obj.SafeHeight)
        cut_feed    = float(obj.CutFeed)
        plunge_feed = float(obj.PlungeFeed)

        t0 = time.time()

        # 1. Load STL
        stl_surf, x_min, x_max, y_min, y_max = _load_stl(stl_file, ocl)
        FreeCAD.Console.PrintMessage(
            f"[OCLSurface] STL loaded in {time.time()-t0:.2f}s  "
            f"X[{x_min:.3f},{x_max:.3f}] Y[{y_min:.3f},{y_max:.3f}]\n"
        )

        # 2. Cutter: ball end mill (diameter, length = 30× diameter)
        cutter = ocl.BallCutter(tool_dia, tool_dia * 30.0)

        # 3. Zigzag scan path
        scan_path, n_lines = _build_zigzag_scan(
            ocl, x_min, x_max, y_min, y_max, stepover
        )

        # 4. PathDropCutter
        t1 = time.time()
        pdc = ocl.PathDropCutter()
        pdc.setSTL(stl_surf)
        pdc.setCutter(cutter)
        pdc.setPath(scan_path)
        pdc.setSampling(sampling)
        pdc.run()
        pts = pdc.getCLPoints()
        FreeCAD.Console.PrintMessage(
            f"[OCLSurface] PathDropCutter: {len(pts)} CL points in "
            f"{time.time()-t1:.3f}s\n"
        )

        # 5. Convert to Path.Commands
        cmds = _cl_points_to_commands(pts, safe_z, cut_feed, plunge_feed)

        # 6. Attach path (Active=True → job consolidation sees it)
        obj.Path = Path.Path(cmds)

        # 7. Cycle time estimate
        cycle_min = _estimate_cycle_time(pts, cut_feed, plunge_feed, safe_z, n_lines)
        obj.CycleTime = f"~{cycle_min:.1f} min"

        FreeCAD.Console.PrintMessage(
            f"[OCLSurface] Done: {len(cmds)} commands, "
            f"cycle {obj.CycleTime}, total {time.time()-t0:.2f}s\n"
        )

    def onChanged(self, obj, prop):
        """React to property changes (currently a no-op)."""
        pass

    # ------------------------------------------------------------------
    # Serialization — called by FreeCAD on save/load
    # ------------------------------------------------------------------

    def dumps(self):
        """State to persist alongside the document object (none needed)."""
        return None

    def loads(self, state):
        """Restore proxy state on document load."""
        return None


# ---------------------------------------------------------------------------
# Factory function (used by the MCP handler)
# ---------------------------------------------------------------------------

def create_ocl_surface_op(doc, job, op_name, **kwargs):
    """Create, configure, and add an OCLSurface op to a job.

    Args:
        doc:      FreeCAD document
        job:      CAM Job object
        op_name:  Name for the new operation object
        **kwargs: stl_file, tool_diameter, stepover, sample_interval,
                  safe_height, cut_feed, plunge_feed

    Returns:
        The new Path::FeaturePython document object (proxy already attached,
        not yet recomputed).
    """
    op_obj = doc.addObject("Path::FeaturePython", op_name)
    OCLSurfaceProxy(op_obj)  # sets op_obj.Proxy and adds properties

    # Apply kwargs
    _float_props = {
        "stl_file":        ("StlFile",        None),   # string, not float
        "tool_diameter":   ("ToolDiameter",   None),
        "stepover":        ("StepOver",       None),
        "sample_interval": ("SampleInterval", None),
        "safe_height":     ("SafeHeight",     None),
        "cut_feed":        ("CutFeed",        None),
        "plunge_feed":     ("PlungeFeed",     None),
    }
    for kwarg_key, (prop_name, _) in _float_props.items():
        if kwarg_key in kwargs and kwargs[kwarg_key] is not None:
            val = kwargs[kwarg_key]
            if kwarg_key == "stl_file":
                op_obj.StlFile = str(val)
            else:
                setattr(op_obj, prop_name, float(val))

    # Add to job's operations group
    # Try job.Proxy.addOperation first (cleaner, manages group internally);
    # fall back to direct Group manipulation for older FreeCAD versions.
    added = False
    if hasattr(job, "Proxy") and hasattr(job.Proxy, "addOperation"):
        try:
            job.Proxy.addOperation(op_obj)
            added = True
        except Exception:
            pass

    if not added:
        children = list(job.Operations.Group)
        children.append(op_obj)
        job.Operations.Group = children

    return op_obj

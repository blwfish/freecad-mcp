# Regression-check worker: run under FreeCADCmd by regression_runner.py, NEVER
# inside the user's GUI session.
#
# One run: open the (temp copy of the) file, snapshot every shape-bearing
# feature as stored by the saving FreeCAD version, recompute the whole
# document, snapshot again, and write per-feature findings to a JSON file.
# Combining K runs into a report (and calling a difference `unstable` when it
# doesn't repeat) is regression_analysis.aggregate_runs.
#
# Why a separate process on a temp copy: a recompute can crash FreeCAD, and an
# in-session recompute dirties the document, which AutoSaveBeforeRiskyOp would
# then write to the user's file. Inputs and the result travel via env vars and
# a JSON file, not argv/stdout -- some FreeCADCmd builds reject unknown CLI
# flags, and stdout also carries FreeCAD's own console chatter.
#
#   FREECAD_MCP_REGRESSION_IN       path to the .FCStd to check
#   FREECAD_MCP_REGRESSION_OUT      path the JSON run report is written to
#   FREECAD_MCP_REGRESSION_MODDIR   directory holding regression_analysis.py
#   FREECAD_MCP_REGRESSION_TOL      relative tolerance (optional)
#   FREECAD_MCP_REGRESSION_JUNK     number of throwaway shapes to allocate
#                                   before opening the file (optional)
#
# JUNK exists because recompute results can depend on unrelated process state:
# OCCT-based fillets were observed flipping valid/invalid with how much had
# been allocated beforehand. Each run uses a different amount so that
# state-dependent features show up as differing outcomes.

import json
import os
import sys

_JUNK_KEEPALIVE: list = []  # module-level on purpose: the allocations must outlive the recompute


def take_snapshot(obj) -> dict:
    """Snapshot obj.Shape. Raises if it can't be read; the caller counts the
    failure per object instead of skipping it silently."""
    shape = obj.Shape
    if shape.isNull():
        return {"null": True, "valid": False, "solids": 0, "shells": 0, "faces": 0,
                "volume": 0.0, "bbox": None, "recompute_error": None}
    bb = shape.BoundBox
    # An empty shape has an invalid BoundBox whose DiagonalLength raises; read
    # the six coordinates only when it's valid.
    bbox = [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax] if bb.isValid() else None
    return {
        "null": False,
        "valid": bool(shape.isValid()),
        "solids": len(shape.Solids),
        "shells": len(shape.Shells),
        "faces": len(shape.Faces),
        "volume": float(shape.Volume),
        "bbox": bbox,
        "recompute_error": None,
    }


def _is_snapshot_target(obj) -> bool:
    # Part::Feature covers sketches, PartDesign features, Bodies, booleans, and
    # always has a Shape. Decided by class alone: reading .Shape here would let
    # one object whose getter raises something other than AttributeError abort
    # the whole run before it could be counted per object.
    return bool(obj.isDerivedFrom("Part::Feature"))


def _has_shape_attribute(obj) -> bool:
    """True if obj exposes a .Shape at all. Origin axes/planes/points do but
    are unbounded construction geometry, not modeling results, so they are
    counted as skipped rather than compared."""
    try:
        return hasattr(obj, "Shape")
    except Exception:  # noqa: BLE001 -- a getter that raises is "can't tell", counted as no
        return False


def _topo_order(doc, names: set) -> list:
    """Dependency order (inputs first) restricted to `names`."""
    seen: set = set()
    order: list = []

    def visit(o, depth=0):
        if o.Name in seen or depth > 500:
            return
        seen.add(o.Name)
        for u in o.OutList:
            visit(u, depth + 1)
        if o.Name in names:
            order.append(o.Name)

    for o in doc.Objects:
        visit(o)
    return order


def run(in_path: str, tolerance: float, junk: int, analysis) -> dict:
    import FreeCAD

    if junk > 0:
        import Part
        _JUNK_KEEPALIVE.extend(Part.makeBox(1 + i % 7, 2, 3) for i in range(junk))

    report: dict = {
        "format_version": analysis.FORMAT_VERSION,
        "tolerance": tolerance,
        "junk": junk,
        "freecad_version": "{}.{}.{} {}".format(*(FreeCAD.Version()[:4])),
    }
    try:
        import Part
        report["occt_version"] = getattr(Part, "OCC_VERSION", None)
    except ImportError:
        report["occt_version"] = None

    doc = FreeCAD.openDocument(in_path)
    report["saved_by_program_version"] = getattr(doc, "ProgramVersion", None)

    targets = [o for o in doc.Objects if _is_snapshot_target(o)]
    skipped_non_feature = sum(1 for o in doc.Objects if not _is_snapshot_target(o) and _has_shape_attribute(o))
    stored: dict = {}
    snapshot_errors: dict = {}
    for o in targets:
        try:
            stored[o.Name] = take_snapshot(o)
        except Exception as e:  # noqa: BLE001 -- counted and reported per object below
            snapshot_errors[o.Name] = f"stored snapshot failed: {type(e).__name__}: {e}"

    for o in doc.Objects:
        o.touch()
    doc.recompute()

    fresh: dict = {}
    for o in targets:
        if o.Name in snapshot_errors:
            continue
        try:
            snap = take_snapshot(o)
            if "Invalid" in o.State:
                snap["recompute_error"] = str(o.getStatusString())  # FreeCAD's own per-object status
            fresh[o.Name] = snap
        except Exception as e:  # noqa: BLE001
            snapshot_errors[o.Name] = f"recomputed snapshot failed: {type(e).__name__}: {e}"

    names = set(stored) & set(fresh)
    order = _topo_order(doc, names)
    report.update({
        "features_compared": len(order),
        "skipped_non_feature": skipped_non_feature,
        "snapshot_errors": snapshot_errors,
        "order": order,
        "inputs": {n: [u.Name for u in doc.getObject(n).OutList if u.Name in names] for n in order},
        "findings": {n: analysis.compare_snapshots(stored[n], fresh[n], tolerance) for n in order},
    })
    return report


def main() -> int:
    in_path = os.environ.get("FREECAD_MCP_REGRESSION_IN", "")
    out_path = os.environ.get("FREECAD_MCP_REGRESSION_OUT", "")
    moddir = os.environ.get("FREECAD_MCP_REGRESSION_MODDIR", "")
    if not (in_path and out_path and moddir):
        sys.stderr.write("regression_worker: FREECAD_MCP_REGRESSION_IN/OUT/MODDIR not all set\n")
        return 2
    sys.path.insert(0, moddir)
    import regression_analysis as analysis

    def _num(name, default, cast):
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return cast(raw)
        except ValueError:
            return default  # runner validates before spawning; unparseable here means a harness bug, keep default

    tolerance = _num("FREECAD_MCP_REGRESSION_TOL", analysis.DEFAULT_TOLERANCE, float)
    junk = _num("FREECAD_MCP_REGRESSION_JUNK", 0, int)
    try:
        report = run(in_path, tolerance, junk, analysis)
    except Exception as e:  # noqa: BLE001 -- surfaced as a typed field, not swallowed
        report = {"format_version": analysis.FORMAT_VERSION, "error": f"{type(e).__name__}: {e}"}
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report, f)
    os.replace(tmp, out_path)
    return 0


# FreeCADCmd execs a script with __name__ set to the module name, not
# "__main__", so also trigger on the env var the runner always sets.
if __name__ == "__main__" or os.environ.get("FREECAD_MCP_REGRESSION_IN"):
    main()

# Model-lint worker: run under FreeCADCmd by lint_runner.py, NEVER inside the
# user's GUI session. It opens a temp copy of the file, WITHOUT recomputing,
# reads per-object facts from what the file stored, applies lint_analysis'
# rules, and writes the report as JSON. Everything it reports is therefore
# about the model as saved; nothing is regenerated.
#
# Inputs and the result travel via env vars and a JSON file, not argv/stdout
# (some FreeCADCmd builds reject unknown flags; stdout also carries FreeCAD's
# console chatter) -- same contract as regression_worker.py.
#
#   FREECAD_MCP_LINT_IN       path to the .FCStd to lint
#   FREECAD_MCP_LINT_OUT      path the JSON report is written to
#   FREECAD_MCP_LINT_MODDIR   directory holding lint_analysis.py
#   FREECAD_MCP_LINT_BOP      "1" also runs OCCT's BOP argument analyzer on shapes with
#                             solids (slow)

import json
import os
import sys

# Not Part::Feature descendants but they own sketches; never "dependents".
_CONTAINER_TYPES = ("PartDesign::Body", "App::Part", "App::DocumentObjectGroup")


def _safe_has(obj, attr) -> bool:
    try:
        return hasattr(obj, attr)
    except Exception:  # noqa: BLE001 -- a getter that raises is "can't tell", counted as absent
        return False


def attach_facts(obj):
    """{mode, support} for attachable objects, else None."""
    if not _safe_has(obj, "MapMode"):
        return None
    sup = getattr(obj, "AttachmentSupport", None)
    if sup is None:
        sup = getattr(obj, "Support", None)
    support = []
    for item in sup or []:
        subs = item[1] if isinstance(item[1], (list, tuple)) else [item[1]]
        support.append([item[0].Name, [str(s) for s in subs]])
    return {"mode": str(obj.MapMode), "support": support}


def legacy_facts(obj):
    if _safe_has(obj, "Midplane") and _safe_has(obj, "SideType"):
        return {"midplane": bool(obj.Midplane), "side_type": str(obj.SideType)}
    return None


def sketch_facts(obj):
    if obj.TypeId != "Sketcher::SketchObject":
        return None
    dependents = [x.Name for x in obj.InList if x.TypeId not in _CONTAINER_TYPES]
    return {"geometry_count": int(obj.GeometryCount), "dependents": dependents}


def _normal_key(axis, offset):
    """Canonical (direction, offset) so opposite-facing planes compare equal:
    flip so the first non-negligible component of the direction is positive."""
    comps = (axis.x, axis.y, axis.z)
    sign = 1.0
    for c in comps:
        if abs(c) > 1e-9:
            sign = 1.0 if c > 0 else -1.0
            break
    return tuple(round(sign * c, 6) for c in comps), sign * offset


def micro_steps(faces, analysis):
    """Pairs of parallel planar faces whose planes differ by a few microns and
    whose bounding boxes touch. Returns [{"gap", "a", "b"}] (face indices)."""
    groups: dict = {}
    for i, f in enumerate(faces):
        try:
            surf = f.Surface
            if surf.__class__.__name__ != "Plane":
                continue
            ax, pos = surf.Axis, surf.Position
            key, off = _normal_key(ax, ax.x * pos.x + ax.y * pos.y + ax.z * pos.z)
        except Exception:  # noqa: BLE001 -- one odd face must not stop the lint
            continue
        b = f.BoundBox
        groups.setdefault(key, []).append((off, i, (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)))
    out = []
    for items in groups.values():
        items.sort()
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                gap = items[b][0] - items[a][0]
                if gap > analysis.MICRO_STEP_CANDIDATE_BOUND:
                    break
                if gap > 0 and analysis.boxes_touch(items[a][2], items[b][2], analysis.MICRO_STEP_MAX_GAP):
                    out.append({"gap": gap, "a": items[a][1] + 1, "b": items[b][1] + 1})
                    if len(out) >= 50:
                        return out
    return out


def shape_facts(shape, analysis, bop: bool):
    nf = len(shape.Faces)
    if nf > analysis.MAX_FACES_FOR_GEOMETRY_FACTS:
        return {"skipped": True, "faces": nf}
    import Part
    extra_shells = []
    for solid in shape.Solids:
        # Per solid: its largest shell is the outer surface; any other shell of
        # the SAME solid is a cavity or junk. (shape.Shells would also count the
        # outer shell of every other solid in a multi-solid shape.)
        for sh in sorted(solid.Shells, key=lambda s: -s.Area)[1:]:
            try:
                vol = float(Part.Solid(sh).Volume)
            except Exception:  # noqa: BLE001 -- volume unknown, never called junk
                vol = None
            extra_shells.append({"faces": len(sh.Faces), "area": float(sh.Area), "volume": vol})
    faces = shape.Faces
    areas = [float(f.Area) for f in faces]
    facts = {
        "faces": nf,
        "extra_shells": extra_shells,
        "small_faces": [a for a in areas if abs(a) < analysis.SLIVER_CANDIDATE_BOUND],
        # Degenerated edges (sphere and cone poles) are legitimately zero-length.
        "small_edges": [float(e.Length) for e in shape.Edges
                        if e.Length < analysis.EDGE_CANDIDATE_BOUND and not e.Degenerated],
        "micro_steps": micro_steps(faces, analysis),
    }
    try:
        facts["max_tolerance"] = float(shape.getTolerance(1, Part.Shape))
    except Exception:  # noqa: BLE001
        facts["max_tolerance"] = None
    # Solids only: a sketch's wire can legitimately cross itself, and the BOP
    # analyzer reports that as a self-intersection.
    if bop and len(shape.Solids) > 0:
        try:
            shape.check(True)
            facts["bop"] = {"self_intersect": 0}
        except Exception as e:  # noqa: BLE001 -- the BOP analyzer reports by raising
            facts["bop"] = {"self_intersect": str(e).count("SelfIntersect")}
    return facts


def _saved_by_version(path: str):
    """ProgramVersion recorded in Document.xml (the doc object does not expose it)."""
    import re
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            head = z.read("Document.xml")[:4000].decode("utf-8", "replace")
        m = re.search(r'ProgramVersion="([^"]*)"', head)
        return m.group(1) if m else None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def _is_part_feature(obj) -> bool:
    return bool(obj.isDerivedFrom("Part::Feature"))


def _topo_order(doc, names: set) -> list:
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


def run(in_path: str, bop: bool, analysis) -> dict:
    import FreeCAD

    saved_by = _saved_by_version(in_path)
    doc = FreeCAD.openDocument(in_path)  # NO recompute: lint reads what the file stored
    findings: dict = {}
    errors: dict = {}
    skipped_geometry: list = []
    for o in doc.Objects:
        try:
            facts = {"attach": attach_facts(o), "legacy": legacy_facts(o), "sketch": sketch_facts(o)}
            if _is_part_feature(o) and not o.Shape.isNull():
                sf = shape_facts(o.Shape, analysis, bop)
                if sf.get("skipped"):
                    skipped_geometry.append(o.Name)
                else:
                    facts["shape"] = sf
            findings[o.Name] = analysis.lint_object({k: v for k, v in facts.items() if v is not None})
        except Exception as e:  # noqa: BLE001 -- counted per object, never silently dropped
            errors[o.Name] = f"{type(e).__name__}: {e}"
    names = set(findings)
    order = _topo_order(doc, names)
    inputs = {n: [u.Name for u in doc.getObject(n).OutList if u.Name in names] for n in order}
    meta = {
        "freecad_version": "{}.{}.{} {}".format(*(FreeCAD.Version()[:4])),
        "saved_by_program_version": saved_by,
        "objects_in_document": len(doc.Objects),
        "bop_check": bop,
        "geometry_facts_skipped": skipped_geometry,
        "object_errors": errors,
    }
    return analysis.build_report({n: findings[n] for n in order}, inputs, order, meta)


def main() -> int:
    in_path = os.environ.get("FREECAD_MCP_LINT_IN", "")
    out_path = os.environ.get("FREECAD_MCP_LINT_OUT", "")
    moddir = os.environ.get("FREECAD_MCP_LINT_MODDIR", "")
    if not (in_path and out_path and moddir):
        sys.stderr.write("lint_worker: FREECAD_MCP_LINT_IN/OUT/MODDIR not all set\n")
        return 2
    sys.path.insert(0, moddir)
    import lint_analysis as analysis

    bop = os.environ.get("FREECAD_MCP_LINT_BOP", "0") == "1"
    try:
        report = run(in_path, bop, analysis)
    except Exception as e:  # noqa: BLE001 -- surfaced as a typed field, not swallowed
        report = {"format_version": analysis.FORMAT_VERSION, "error": f"{type(e).__name__}: {e}"}
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report, f)
    os.replace(tmp, out_path)
    return 0


# FreeCADCmd execs a script with __name__ set to the module name, not
# "__main__", so also trigger on the env var the runner always sets.
if __name__ == "__main__" or os.environ.get("FREECAD_MCP_LINT_IN"):
    main()

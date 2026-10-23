# Pure rules for the model lint -- no FreeCAD import, so they run (and are
# unit-tested) anywhere. lint_worker.py gathers per-object FACTS inside
# FreeCADCmd from the SAVED file (no recompute); lint_runner.py spawns it; this
# module turns facts into typed findings and builds the report.
#
# The lint asks "does this model carry latent problems?", as opposed to
# regression_check's "does it still recompute the way it was saved?". Every rule
# here came from a real defect found in the MillingMachine model (2026-10-23):
#
#   lost_support          Sketch018: Flat-face mode but an empty support list
#   unresolved_reference  a support sub-name that no longer resolves ("?Face28")
#   legacy_flag_conflict  Pad007: Midplane=true with SideType != Symmetric
#   orphan_empty_sketch   Sketch052: no geometry, nothing uses it
#   junk_shell            Fusion: zero-size 3-face shells inside a "valid" solid
#   internal_void         a solid's extra shell that is NOT junk (a real cavity)
#   sliver_face           faces of ~1e-6 mm^2 or less, including negative areas
#   zero_length_edge      edges of ~1e-17 mm
#   inflated_tolerance    shape tolerance 12.8 um against OCCT's 0.1 um default
#   micro_step            parallel planes a few microns apart on adjacent faces
#   bop_self_intersect    OCCT's BOP argument analyzer (optional, slow)
#
# Thresholds are comparisons with a stated direction (strict < or >), and the
# tests pin each at / just below / just above.

FORMAT_VERSION = 1

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

KIND_LOST_SUPPORT = "lost_support"
KIND_UNRESOLVED_REFERENCE = "unresolved_reference"
KIND_LEGACY_FLAG_CONFLICT = "legacy_flag_conflict"
KIND_ORPHAN_EMPTY_SKETCH = "orphan_empty_sketch"
KIND_JUNK_SHELL = "junk_shell"
KIND_INTERNAL_VOID = "internal_void"
KIND_SLIVER_FACE = "sliver_face"
KIND_ZERO_LENGTH_EDGE = "zero_length_edge"
KIND_INFLATED_TOLERANCE = "inflated_tolerance"
KIND_MICRO_STEP = "micro_step"
KIND_BOP_SELF_INTERSECT = "bop_self_intersect"

SEVERITY = {
    KIND_LOST_SUPPORT: SEVERITY_WARNING,
    KIND_UNRESOLVED_REFERENCE: SEVERITY_ERROR,
    KIND_LEGACY_FLAG_CONFLICT: SEVERITY_WARNING,
    KIND_ORPHAN_EMPTY_SKETCH: SEVERITY_INFO,
    KIND_JUNK_SHELL: SEVERITY_WARNING,
    KIND_INTERNAL_VOID: SEVERITY_INFO,
    KIND_SLIVER_FACE: SEVERITY_WARNING,
    KIND_ZERO_LENGTH_EDGE: SEVERITY_WARNING,
    KIND_INFLATED_TOLERANCE: SEVERITY_WARNING,
    KIND_MICRO_STEP: SEVERITY_WARNING,
    KIND_BOP_SELF_INTERSECT: SEVERITY_ERROR,
}
ALL_KINDS = tuple(SEVERITY)

# Properties of the object itself: never "inherited" from an input.
LOCAL_KINDS = frozenset({
    KIND_LOST_SUPPORT, KIND_UNRESOLVED_REFERENCE, KIND_LEGACY_FLAG_CONFLICT, KIND_ORPHAN_EMPTY_SKETCH,
})
# Properties of a shape: a downstream feature inherits them from its inputs, so
# only the first feature to show one is reported as a root.
GEOMETRY_KINDS = frozenset(ALL_KINDS) - LOCAL_KINDS

# --- thresholds (mm, mm^2, mm^3) -------------------------------------------
JUNK_SHELL_MAX_AREA = 1e-3         # an extra shell is junk if area  <  this ...
JUNK_SHELL_MAX_ABS_VOLUME = 1e-6   # ... and |volume| <  this (strict both)
SLIVER_FACE_MAX_AREA = 1e-6        # |face area| < this (negative areas count)
ZERO_LENGTH_EDGE_MAX = 1e-9        # edge length < this
TOLERANCE_WARN = 1e-3              # shape max tolerance > this
# Micro-step window, calibrated on the 195-file Gitea corpus (2026-10-23): below
# ~10 nm the difference is floating-point noise (17 findings at 0.1-1 nm), and at
# 10 um and above it is mostly an intentional "0.01 mm" offset. The defect this
# rule exists for was 2.1 um. Tunable; the tests pin the current values.
MICRO_STEP_MIN_GAP = 1e-5          # plane gap  >  this (10 nm)
MICRO_STEP_MAX_GAP = 5e-3          # plane gap  <  this (5 um)
# What the worker may pre-select so it never ships a whole face list; analysis
# re-applies the real thresholds above.
SLIVER_CANDIDATE_BOUND = 1e-3
EDGE_CANDIDATE_BOUND = 1e-6
MICRO_STEP_CANDIDATE_BOUND = 0.1
# Geometry facts are skipped on shapes with more faces than this (array
# pathologies); see regression_analysis.MAX_FACES_FOR_VALIDITY_CHECK.
MAX_FACES_FOR_GEOMETRY_FACTS = 20000

MAX_EXAMPLES = 5


def _modes_needing_support(mode) -> bool:
    return mode not in (None, "", "Deactivated")


def boxes_touch(a, b, margin: float) -> bool:
    """True if two axis-aligned boxes (xmin,ymin,zmin,xmax,ymax,zmax), each
    grown by `margin`, intersect. Touching exactly (zero gap) counts."""
    return all(a[i] - margin <= b[i + 3] + margin and b[i] - margin <= a[i + 3] + margin for i in range(3))


def lint_object(facts: dict) -> dict:
    """Return {kind: detail} for everything the lint finds on one object.

    `facts` (all optional keys): attach {mode, support [[obj, [subs]]]},
    legacy {midplane, side_type}, sketch {geometry_count, dependents},
    shape {faces, extra_shells [{faces, area, volume}], small_faces [area...],
    small_edges [length...], max_tolerance, micro_steps [{gap,...}],
    bop {self_intersect}}.
    """
    findings: dict = {}

    attach = facts.get("attach")
    if attach:
        support = attach.get("support") or []
        if _modes_needing_support(attach.get("mode")) and len(support) == 0:
            findings[KIND_LOST_SUPPORT] = {"mode": attach.get("mode")}
        unresolved = [s for _, subs in support for s in subs if str(s).startswith("?")]
        if unresolved:
            findings[KIND_UNRESOLVED_REFERENCE] = {"subnames": unresolved[:MAX_EXAMPLES], "count": len(unresolved)}

    legacy = facts.get("legacy")
    if legacy and legacy.get("midplane") and legacy.get("side_type") not in (None, "Symmetric"):
        findings[KIND_LEGACY_FLAG_CONFLICT] = {"midplane": True, "side_type": legacy["side_type"]}

    sketch = facts.get("sketch")
    if sketch and sketch.get("geometry_count") == 0 and not sketch.get("dependents"):
        findings[KIND_ORPHAN_EMPTY_SKETCH] = {}

    shape = facts.get("shape")
    if shape:
        # `extra_shells` are the shells of each SOLID beyond its largest (outer)
        # one, computed per solid by the worker: in a shape of several solids
        # every solid's own outer shell is not "extra".
        extra = shape.get("extra_shells") or []
        # A shell whose volume could not be computed (None) is never called junk.
        junk = [s for s in extra if s["area"] < JUNK_SHELL_MAX_AREA
                and s.get("volume") is not None and abs(s["volume"]) < JUNK_SHELL_MAX_ABS_VOLUME]
        voids = [s for s in extra if s not in junk]
        if junk:
            findings[KIND_JUNK_SHELL] = {"count": len(junk), "shells": junk[:MAX_EXAMPLES]}
        if voids:
            findings[KIND_INTERNAL_VOID] = {"count": len(voids),
                                            "total_abs_volume": sum(abs(s["volume"]) for s in voids if s.get("volume") is not None)}

        slivers = [a for a in (shape.get("small_faces") or []) if abs(a) < SLIVER_FACE_MAX_AREA]
        if slivers:
            findings[KIND_SLIVER_FACE] = {"count": len(slivers), "smallest_abs_area": min(abs(a) for a in slivers),
                                          "negative_area_count": sum(1 for a in slivers if a < 0)}
        zero = [e for e in (shape.get("small_edges") or []) if e < ZERO_LENGTH_EDGE_MAX]
        if zero:
            findings[KIND_ZERO_LENGTH_EDGE] = {"count": len(zero), "shortest": min(zero)}
        tol = shape.get("max_tolerance")
        if tol is not None and tol > TOLERANCE_WARN:
            findings[KIND_INFLATED_TOLERANCE] = {"max_tolerance": tol, "threshold": TOLERANCE_WARN}
        steps = [m for m in (shape.get("micro_steps") or []) if MICRO_STEP_MIN_GAP < m["gap"] < MICRO_STEP_MAX_GAP]
        if steps:
            findings[KIND_MICRO_STEP] = {"count": len(steps), "smallest_gap": min(m["gap"] for m in steps),
                                         "examples": steps[:MAX_EXAMPLES]}
        bop = shape.get("bop")
        if bop and bop.get("self_intersect", 0) > 0:
            findings[KIND_BOP_SELF_INTERSECT] = {"count": bop["self_intersect"]}
    return findings


def attribute(findings_by_name: dict, inputs_by_name: dict, order: list) -> dict:
    """Split findings into ROOTS and INHERITED, in `order` (inputs first).

    Local kinds are always roots. A geometry kind is a root for an object when
    none of its direct inputs shows that kind; otherwise it is inherited."""
    roots: list = []
    inherited: list = []
    for name in order:
        own = findings_by_name.get(name) or {}
        if not own:
            continue
        upstream: set = set()
        for inp in inputs_by_name.get(name, ()):
            upstream.update(findings_by_name.get(inp) or {})
        new = {k: v for k, v in own.items() if k in LOCAL_KINDS or k not in upstream}
        old = sorted(k for k in own if k in GEOMETRY_KINDS and k in upstream)
        if new:
            roots.append({"name": name, "findings": new, "also_inherited": old})
        else:
            inherited.append({"name": name, "kinds": sorted(own)})
    return {"roots": roots, "inherited": inherited}


def build_report(findings_by_name: dict, inputs_by_name: dict, order: list, meta: dict) -> dict:
    """The final report. `meta` carries freecad_version etc. through unchanged."""
    attributed = attribute(findings_by_name, inputs_by_name, order)
    by_kind: dict = {}
    by_severity = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0}
    for root in attributed["roots"]:
        for kind in root["findings"]:
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_severity[SEVERITY[kind]] += 1
    report = dict(meta)
    report.update({
        "format_version": FORMAT_VERSION,
        "objects_checked": len(order),
        "findings_by_kind": dict(sorted(by_kind.items())),
        "findings_by_severity": by_severity,
        "root_findings": sum(by_kind.values()),
        "roots": attributed["roots"],
        "inherited": attributed["inherited"],
    })
    for root in report["roots"]:
        root["severity"] = {k: SEVERITY[k] for k in root["findings"]}
    return report

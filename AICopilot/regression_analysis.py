# Pure analysis for the regression check -- no FreeCAD import, so it runs (and
# is unit-tested) anywhere. regression_worker.py produces per-feature snapshots
# inside FreeCADCmd; regression_runner.py spawns the worker and calls this to
# turn K runs into a report.
#
# The question: "I saved this file with an older FreeCAD. Does recomputing it
# in THIS FreeCAD still reproduce what the file stored?" Each feature's stored
# shape is compared with its recomputed shape. Because a recompute can land on
# different results run to run (confirmed 2026-10-23: a fillet that is valid
# 12/12 in FreeCAD 1.0.0 is valid only 3-5/12 in 1.1+, depending on unrelated
# memory-layout state), K runs are aggregated: a difference present in every
# run is a regression; one present in only some runs is `unstable`.

FORMAT_VERSION = 1
DEFAULT_TOLERANCE = 1e-3

# Closed vocabulary of finding kinds. Consumers branch on these, never on text.
KIND_VALIDITY_LOST = "validity_lost"          # valid as stored, invalid now
KIND_VALIDITY_GAINED = "validity_gained"      # invalid as stored, valid now
KIND_STORED_INVALID = "stored_invalid"        # invalid in the file and still is (not a regression)
KIND_SHAPE_LOST = "shape_lost"                # had a shape, now null
KIND_RECOMPUTE_ERROR = "recompute_error"      # FreeCAD flagged the object Invalid on recompute
KIND_TOPOLOGY_CHANGED = "topology_changed"    # solid/shell/face counts differ
KIND_VOLUME_CHANGED = "volume_changed"        # relative volume delta > tolerance
KIND_EXTENT_CHANGED = "extent_changed"        # bbox moved/resized > tolerance of its stored diagonal
ALL_KINDS = (
    KIND_VALIDITY_LOST, KIND_VALIDITY_GAINED, KIND_STORED_INVALID, KIND_SHAPE_LOST,
    KIND_RECOMPUTE_ERROR, KIND_TOPOLOGY_CHANGED, KIND_VOLUME_CHANGED, KIND_EXTENT_CHANGED,
)

# "This feature got worse or different" kinds. stored_invalid is pre-existing
# and validity_gained is an improvement; neither is a regression.
REGRESSION_KINDS = frozenset({
    KIND_VALIDITY_LOST, KIND_SHAPE_LOST, KIND_RECOMPUTE_ERROR,
    KIND_TOPOLOGY_CHANGED, KIND_VOLUME_CHANGED, KIND_EXTENT_CHANGED,
})


def _rel_delta(a: float, b: float) -> float:
    """|a-b| relative to the larger magnitude. 0 when both are 0."""
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 0.0
    return abs(a - b) / denom


def _diagonal(bbox) -> float:
    dx, dy, dz = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def compare_snapshots(stored: dict, fresh: dict, tolerance: float) -> dict:
    """Return {kind: detail} for how `fresh` differs from `stored`.

    Snapshots (regression_worker.take_snapshot): null, valid, solids, shells,
    faces, volume, bbox ([xmin,ymin,zmin,xmax,ymax,zmax] or None),
    recompute_error (str or None). Comparisons against `tolerance` are
    strictly greater-than: a delta exactly at the tolerance is NOT a change.
    """
    findings: dict = {}

    if fresh.get("recompute_error"):
        findings[KIND_RECOMPUTE_ERROR] = fresh["recompute_error"]

    if fresh["null"] and not stored["null"]:
        findings[KIND_SHAPE_LOST] = "had a shape in the file, null after recompute"
        return findings
    if fresh["null"] or stored["null"]:
        return findings  # nothing comparable

    if stored["valid"] and not fresh["valid"]:
        findings[KIND_VALIDITY_LOST] = "valid as stored, Shape.isValid() == False after recompute"
    elif not stored["valid"] and fresh["valid"]:
        findings[KIND_VALIDITY_GAINED] = "invalid as stored, valid after recompute"
    elif not stored["valid"] and not fresh["valid"]:
        findings[KIND_STORED_INVALID] = "invalid in the file and still invalid"

    counts = {k: (stored[k], fresh[k]) for k in ("solids", "shells", "faces") if stored[k] != fresh[k]}
    if counts:
        findings[KIND_TOPOLOGY_CHANGED] = {k: {"stored": a, "recomputed": b} for k, (a, b) in counts.items()}

    vd = _rel_delta(stored["volume"], fresh["volume"])
    if vd > tolerance:
        findings[KIND_VOLUME_CHANGED] = {
            "stored": stored["volume"], "recomputed": fresh["volume"], "relative_delta": vd,
        }

    sb, fb = stored.get("bbox"), fresh.get("bbox")
    if sb and fb:
        # Relative to the stored diagonal; a zero-diagonal (degenerate) box
        # has no scale, so any movement at all counts.
        moved = max(abs(x - y) for x, y in zip(sb, fb, strict=True))
        if moved > tolerance * _diagonal(sb):
            findings[KIND_EXTENT_CHANGED] = {
                "max_coordinate_shift_mm": moved, "stored_diagonal_mm": _diagonal(sb),
            }
    elif bool(sb) != bool(fb):
        findings[KIND_EXTENT_CHANGED] = {"detail": "bounding box valid on one side only"}

    return findings


def attribute_findings(findings_by_name: dict, inputs_by_name: dict, order: list) -> dict:
    """Split regression findings into ROOT causes and INHERITED.

    A feature is a root for a kind when none of its direct inputs shows that
    same kind; otherwise it inherits. `order` is dependency order (inputs
    first). Only REGRESSION_KINDS take part; informational kinds are ignored.
    """
    roots: list = []
    inherited: list = []
    for name in order:
        own = {k: v for k, v in findings_by_name.get(name, {}).items() if k in REGRESSION_KINDS}
        if not own:
            continue
        upstream: set = set()
        for inp in inputs_by_name.get(name, ()):
            upstream.update(k for k in findings_by_name.get(inp, {}) if k in REGRESSION_KINDS)
        new = {k: v for k, v in own.items() if k not in upstream}
        if new:
            roots.append({"name": name, "new": new, "also_inherited": sorted(k for k in own if k in upstream)})
        else:
            inherited.append({"name": name, "kinds": sorted(own)})
    return {"roots": roots, "inherited": inherited}


def aggregate_runs(run_reports: list, requested_runs: int) -> dict:
    """Combine per-run worker reports into one report.

    Each run report carries `order`, `inputs`, `findings` ({name: {kind: detail}})
    and counters. For each feature and regression kind: present in EVERY
    successful run -> stable; present in some but not all -> unstable. With a
    single successful run nothing can be called unstable (the report says how
    many runs it had so the reader can judge).
    """
    n = len(run_reports)
    first = run_reports[0]
    order = first["order"]
    inputs = first["inputs"]

    stable: dict = {}
    unstable: dict = {}
    informational: dict = {}
    for name in order:
        per_run = [r["findings"].get(name, {}) for r in run_reports]
        reg_sets = [{k for k in f if k in REGRESSION_KINDS} for f in per_run]
        always = set.intersection(*reg_sets) if reg_sets else set()
        ever = set.union(*reg_sets) if reg_sets else set()
        if always:
            stable[name] = {k: per_run[0][k] for k in sorted(always)}
        if ever - always:
            unstable[name] = {
                k: {"runs_with": sum(1 for s in reg_sets if k in s), "of": n}
                for k in sorted(ever - always)
            }
        info = sorted({k for f in per_run for k in f if k not in REGRESSION_KINDS})
        if info:
            informational[name] = info

    stable_attr = attribute_findings(stable, inputs, order)
    unstable_attr = attribute_findings(unstable, inputs, order)
    return {
        "format_version": FORMAT_VERSION,
        "runs_requested": requested_runs,
        "runs_used": n,
        "tolerance": first.get("tolerance"),
        "freecad_version": first.get("freecad_version"),
        "occt_version": first.get("occt_version"),
        "saved_by_program_version": first.get("saved_by_program_version"),
        "features_compared": first.get("features_compared"),
        "skipped_non_feature": first.get("skipped_non_feature"),
        "snapshot_errors": first.get("snapshot_errors", {}),
        "regression_count": len(stable_attr["roots"]) + len(stable_attr["inherited"]),
        "unstable_count": len(unstable_attr["roots"]) + len(unstable_attr["inherited"]),
        "regressions": stable_attr,
        "unstable": unstable_attr,
        "informational": [{"name": k, "kinds": v} for k, v in informational.items()],
    }

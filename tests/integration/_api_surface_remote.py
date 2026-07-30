"""FreeCAD-side helpers for the API-surface drift test.

This module's SOURCE TEXT is read (never imported normally - it never runs
in the test-runner process) and prepended to ``execute_python`` code blocks
by both the golden-snapshot generator and the drift test itself, so the
translation/description logic lives in exactly one place rather than being
duplicated as separate strings in two places.
"""


def _resolve_type_to_instance(doc, type_id):
    """Type::String -> live scratch instance.

    PartDesign::* types (other than PartDesign::Body itself) only exist as
    Body-scoped features - newObject() on a Body, not addObject() on the
    document. Everything else (Part::, Mesh::, Sketcher::, Spreadsheet::) is
    a plain document object -- EXCEPT App::FeaturePython, which is a generic
    C++ shell with no properties of its own; the Distance/Distance2/Angle
    (etc.) properties handler code actually cares about only exist once a
    Python proxy is attached, exactly as assembly_ops.py's create_joint/
    ground_part do immediately after creation. Without this, every
    App::FeaturePython property read "__MISSING__" on both golden and live
    scans, and _api_surface_diff.py skips comparing anything already
    "__MISSING__" in golden -- silently zero real drift coverage for this
    type (full-review 2026-07-24 finding #12; also shared by
    Draft.make_text()'s App::FeaturePython Draft Text objects -- Label is a
    base App::DocumentObject property present regardless of proxy, so no
    conflict between the two uses).

    App::FeaturePython is used for exactly one purpose across
    AICopilot/handlers/*.py today (confirmed by grep): assembly_ops.py's
    Joint/GroundedJoint objects, created via
    ``UtilsAssembly.getJointGroup(assembly).newObject("App::FeaturePython",
    ...)`` followed immediately by ``JointObject.Joint(obj, type_index)``.
    Confirmed live 2026-07-25 (spawned FreeCADCmd instance) that
    ``JointObject.Joint()`` requires exactly this context --
    ``JointObject.Joint(doc.addObject("App::FeaturePython", ...), 0)`` on a
    bare document object raises ``'NoneType' object has no attribute
    'Type'`` (it needs an owning Assembly, whose ``Type`` attribute
    JointObject.py reads). A first attempt at this fix used a bare
    doc.addObject() with a defensive try/except around the Joint() call --
    that DID NOT WORK: the except silently swallowed this exact error every
    time, so the fix shipped a no-op that looked complete in code review
    but produced zero actual improvement, only caught by live verification
    after the fact. Mirror assembly_ops.py's real setup instead: create (or
    reuse) a scratch Assembly::AssemblyObject with Type="Assembly" set, get
    its JointGroup, and create the scratch instance there. Still wrapped in
    try/except -- if FreeCAD's Assembly/Joint API ever changes shape again,
    this degrades to the pre-fix bare-object behavior rather than failing
    the whole scan.
    """
    if type_id == "PartDesign::Body":
        return doc.addObject(type_id, "_ScratchInstance")
    if type_id.startswith("PartDesign::"):
        body = doc.getObject("_ScratchBody")
        if body is None:
            body = doc.addObject("PartDesign::Body", "_ScratchBody")
        return body.newObject(type_id, "_ScratchInstance")
    if type_id == "App::FeaturePython":
        try:
            import JointObject
            import UtilsAssembly
            assembly = doc.getObject("_ScratchAssembly")
            if assembly is None:
                assembly = doc.addObject("Assembly::AssemblyObject", "_ScratchAssembly")
                assembly.Type = "Assembly"
            joint_group = UtilsAssembly.getJointGroup(assembly)
            obj = joint_group.newObject(type_id, "_ScratchInstance")
            JointObject.Joint(obj, 0)
            return obj
        except Exception:
            return doc.addObject(type_id, "_ScratchInstance")
    return doc.addObject(type_id, "_ScratchInstance")


def _describe_properties(obj, property_names):
    """For each property name, return its FreeCAD property-type-id string
    (e.g. "App::PropertyLength"), or the sentinel "__MISSING__" if the
    property doesn't exist.

    getTypeIdOfProperty() raises AttributeError on a missing property
    rather than returning None or an empty string - that ambiguity is
    handled explicitly here (hasattr() check first), not by accident.
    """
    result = {}
    for name in property_names:
        if not hasattr(obj, name):
            result[name] = "__MISSING__"
            continue
        try:
            result[name] = obj.getTypeIdOfProperty(name)
        except AttributeError:
            # Race with the hasattr() check above (property removed between
            # the two calls) is not expected in this single-threaded,
            # single-recompute usage, but fail the same documented way
            # rather than letting a raw AttributeError escape.
            result[name] = "__MISSING__"
    return result


def build_remote_scan_code(scope):
    """Build a full execute_python code block: this module's own two
    helper functions above, prepended so they're defined inside FreeCAD's
    Python process, followed by a driver that resolves every scope entry
    into a scratch instance and prints a one-line JSON snapshot as the
    final stdout output.

    Runs locally only (called from generate_api_surface_snapshot.py and
    test_api_surface.py, never itself sent to FreeCAD) -- this keeps the
    driver code that both scripts need in exactly one place rather than
    duplicated as two near-identical f-strings.
    """
    import json

    with open(__file__) as f:
        helpers_src = f.read()
    scope_literal = json.dumps({k: sorted(v) for k, v in scope.items()})
    return helpers_src + f"""
import FreeCAD, json as _json
doc = FreeCAD.newDocument("_APISurfaceScan")
scope = {scope_literal}
snapshot = {{}}
for type_id, props in scope.items():
    try:
        obj = _resolve_type_to_instance(doc, type_id)
        snapshot[type_id] = _describe_properties(obj, props)
    except Exception as e:
        snapshot[type_id] = {{"__ERROR__": str(e)}}
FreeCAD.closeDocument(doc.Name)
# Not a type_id -- a reserved "__meta__" key, ignored by
# _api_surface_diff.diff_snapshots (it only ever looks up keys present in
# `scope`, never iterates golden/live directly), so this can't be mistaken
# for real drift data. Answers "which FreeCAD build produced this
# snapshot" for anyone debugging a drift-test failure or deciding whether a
# checked-in snapshot needs regenerating (full-review 2026-07-24 finding
# #20 -- previously nothing in the snapshot itself recorded this).
try:
    snapshot["__meta__"] = {{"freecad_version": list(FreeCAD.Version())}}
except Exception as e:
    snapshot["__meta__"] = {{"freecad_version": f"__ERROR__: {{e}}"}}
print(_json.dumps(snapshot))
"""

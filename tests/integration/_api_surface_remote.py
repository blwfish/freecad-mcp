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
    document. Everything else (Part::, Mesh::, App::, Sketcher::,
    Spreadsheet::) is a plain document object.
    """
    if type_id == "PartDesign::Body":
        return doc.addObject(type_id, "_ScratchInstance")
    if type_id.startswith("PartDesign::"):
        body = doc.getObject("_ScratchBody")
        if body is None:
            body = doc.addObject("PartDesign::Body", "_ScratchBody")
        return body.newObject(type_id, "_ScratchInstance")
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
print(_json.dumps(snapshot))
"""

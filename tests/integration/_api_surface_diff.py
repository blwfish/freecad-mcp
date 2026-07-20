"""Pure diff logic for the API-surface drift test.

Deliberately dependency-free (no FreeCAD, no sockets) so it can be unit
tested with handcrafted dicts in tests/unit/test_api_surface_diff.py as well
as exercised for real by tests/integration/test_api_surface.py against a
live FreeCAD instance.

Inputs are all {type_id: {property_name: type_id_string}} shaped, with the
sentinel "__MISSING__" standing in for a property that doesn't exist on the
resolved object (see _api_surface_remote.py:_describe_properties), and the
sentinel key "__ERROR__" standing in for a Type::String that failed to
resolve to a live instance at all (see _api_surface_remote.py:
_resolve_type_to_instance) -- distinct from "__MISSING__" because a whole
type not resolving is a different failure than one property being absent
on a type that resolved fine.

``scope`` is the set of (type, property) pairs current handler source code
actually cares about (from _api_surface_scan.scan_type_properties) -- the
diff is always scoped to it, never to whatever happens to be in ``golden``
or ``live`` alone, so a type/property the handlers no longer touch doesn't
spuriously fail (see diff_snapshots' docstring for the full three-outcome
shape this produces).
"""

from typing import Dict, List, Set


def diff_snapshots(scope: Dict[str, Set[str]], golden: Dict[str, Dict[str, str]],
                    live: Dict[str, Dict[str, str]]) -> Dict[str, object]:
    """Three-outcome diff (match / changed / removed) over ``scope``, plus a
    fourth bucket for scope entries the golden snapshot doesn't know about
    yet (a stale snapshot, not necessarily a break -- see
    ``stale_snapshot_types`` below).

    Returns a dict with four keys, each a mapping keyed by type_id (never
    omitted just because it's empty -- callers assert on truthiness, not
    key presence):

    - ``stale_snapshot_types``: type_id -> sorted list of properties in
      ``scope`` that the golden snapshot has no *key* for at all -- either
      the whole type_id is absent from ``golden``, or the type_id is
      present but this particular property was never scanned into it (a
      handler started setting a new property on an already-known type
      since the last regen). Either way this means a handler started
      depending on new surface since the last deliberate snapshot regen --
      not upstream drift, but the snapshot needs a deliberate regen before
      this diff can say anything meaningful about that (type, property).
    - ``types_no_longer_resolve``: type_id -> live error string, for a
      type_id that resolved fine in ``golden`` (no "__ERROR__" key) but
      comes back "__ERROR__" in ``live``. Distinct bucket from property
      removal because the whole type is gone/renamed, not just one field.
    - ``properties_removed``: type_id -> sorted list of property names
      present (non-"__MISSING__") in ``golden`` but "__MISSING__" in
      ``live``, for types that resolve cleanly on both sides.
    - ``properties_changed``: type_id -> sorted list of "prop: old -> new"
      strings, for properties present on both sides with a different
      property-type-id string.

    A property whose golden *value* (not key) is the sentinel
    "__MISSING__" -- i.e. it was scanned at snapshot time and genuinely
    didn't exist on the type then -- is not stale; that's a real prior
    scan result, not an unscanned gap. If it's still "__MISSING__" live,
    nothing to report (never existed on either side). If it's now present
    live, that is an *addition*, not drift, and deliberately not
    reported -- see the module-level docstring and DEFERRED_TESTS.md for
    why additions are out of scope for this mechanism.
    """
    stale_snapshot_types: Dict[str, List[str]] = {}
    types_no_longer_resolve: Dict[str, str] = {}
    properties_removed: Dict[str, List[str]] = {}
    properties_changed: Dict[str, List[str]] = {}

    for type_id, props in scope.items():
        props = sorted(props)
        if type_id not in golden:
            stale_snapshot_types[type_id] = props
            continue

        golden_entry = golden[type_id]
        live_entry = live.get(type_id, {"__ERROR__": "not present in live scan"})

        golden_errored = "__ERROR__" in golden_entry
        live_errored = "__ERROR__" in live_entry
        if live_errored and not golden_errored:
            types_no_longer_resolve[type_id] = live_entry["__ERROR__"]
            continue
        if golden_errored or live_errored:
            # Errored on both sides (or only in golden, e.g. a type that
            # never resolved even at snapshot time) -- nothing new to
            # report for this type_id's properties either way.
            continue

        stale_props = [p for p in props if p not in golden_entry]
        if stale_props:
            stale_snapshot_types[type_id] = stale_props

        removed = []
        changed = []
        for prop in props:
            if prop in stale_props:
                continue  # unscanned gap, not a removal/change -- see above
            golden_val = golden_entry[prop]
            live_val = live_entry.get(prop, "__MISSING__")
            if golden_val == "__MISSING__":
                continue  # never existed on either side -- nothing to compare
            if live_val == "__MISSING__":
                removed.append(prop)
            elif golden_val != live_val:
                changed.append(f"{prop}: {golden_val} -> {live_val}")

        if removed:
            properties_removed[type_id] = sorted(removed)
        if changed:
            properties_changed[type_id] = sorted(changed)

    return {
        "stale_snapshot_types": stale_snapshot_types,
        "types_no_longer_resolve": types_no_longer_resolve,
        "properties_removed": properties_removed,
        "properties_changed": properties_changed,
    }

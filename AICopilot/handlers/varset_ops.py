# App::VarSet operation handlers for FreeCAD MCP
#
# App::VarSet is a plain App::DocumentObject subclass (no Proxy) used as a
# lightweight container for named, typed parametric variables that other
# objects bind to via expressions. See SPEC-varset-operations.md for the
# FreeCAD source citations backing the design choices below.

import json
import re
import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


# Property::PropDynamic (src/App/Property.h) is the bit FreeCAD sets on every
# property created through addDynamicProperty (DynamicProperty.cpp) -- it is
# the authoritative "was this added dynamically, not built in" signal. It has
# no name in PropertyContainerPyImp.cpp's status-name table, so
# getPropertyStatus() returns it as a bare int rather than a string; every
# other bit we care about (LockDynamic, Hidden, ReadOnly) does have a name.
_PROP_DYNAMIC_BIT = 21

# Set True the first time _verify_prop_dynamic_bit() passes on a live
# FreeCAD instance -- memoized at module scope so the check runs once per
# process, not once per call.
_dynamic_bit_verified = False


def _verify_prop_dynamic_bit(varset) -> "str | None":
    """One-time runtime sanity check that _PROP_DYNAMIC_BIT still means
    "added dynamically" on this FreeCAD build.

    _PROP_DYNAMIC_BIT has no name in FreeCAD's status-name table (see the
    constant's docstring above) -- it's a hardcoded bit index with no
    version check. If a future FreeCAD release renumbers it, every
    dynamic/not-dynamic classification in this file (list_properties'
    filter, and remove_property's locked/built-in safety gate) would
    silently start being wrong instead of raising. 'Label' is a guaranteed
    built-in property on every App::DocumentObject, never added via
    addDynamicProperty, so it must never report the dynamic bit -- if it
    does, the bit-to-meaning mapping has changed and every caller of this
    check must refuse to proceed rather than risk classifying a built-in
    property as safe to remove.

    Returns an error string if the check fails, else None. Can't verify
    (e.g. `Label` status unavailable) -> returns None rather than blocking,
    since that's a "don't know" state, not a confirmed mismatch.
    """
    global _dynamic_bit_verified
    if _dynamic_bit_verified:
        return None
    try:
        label_status = varset.getPropertyStatus('Label')
    except Exception:
        return None
    if _PROP_DYNAMIC_BIT in label_status:
        return (
            "Internal check failed: this FreeCAD build's built-in 'Label' "
            f"property reports the dynamic-property bit ({_PROP_DYNAMIC_BIT}) "
            "that this handler assumes only added properties have. The "
            "bit-to-status mapping appears to have changed on this FreeCAD "
            "version -- refusing dynamic/locked property checks rather than "
            "risk misclassifying a built-in property as removable. Please "
            "report this along with your FreeCAD version."
        )
    _dynamic_bit_verified = True
    return None

# Common property types, used only to keep add_property's "unknown type"
# error message short and readable. NOT used for validation -- the actual
# check is against the live supportedProperties() result, which is the only
# ground truth FreeCAD exposes (it has no hardcoded type allow-list either).
_COMMON_PROPERTY_TYPES = frozenset({
    "App::PropertyLength", "App::PropertyDistance", "App::PropertyFloat",
    "App::PropertyInteger", "App::PropertyString", "App::PropertyBool",
    "App::PropertyAngle", "App::PropertyArea", "App::PropertyVolume",
    "App::PropertyEnumeration", "App::PropertyLink",
})


def _property_value_for_json(raw_value):
    """Coerce a FreeCAD property's raw Python value into something
    json.dumps can serialize, returning (value, unit_string).

    Quantity-derived values (Length, Angle, Volume, ...) expose
    getValueAs/UserString; Link-typed values expose Name; Vector-like
    values (App::PropertyVector, and elements of PropertyVectorList) expose
    x/y/z; Placement-like values expose Base/Rotation. List/tuple-typed
    values (PropertyVectorList, PropertyFloatList, PropertyLinkList, a
    Color's (r,g,b,a) tuple, ...) recurse per-element rather than falling
    through to a single opaque repr() string -- add_property accepts any
    type in FreeCAD's supportedProperties(), so these are real, reachable
    shapes, not hypothetical ones. Plain str/int/float/bool/None pass
    through unchanged with no unit string.
    """
    if isinstance(raw_value, (str, int, float, bool, type(None))):
        return raw_value, None
    if hasattr(raw_value, 'getValueAs'):
        try:
            return float(raw_value.Value), getattr(raw_value, 'UserString', str(raw_value))
        except Exception:
            return str(raw_value), None
    if hasattr(raw_value, 'x') and hasattr(raw_value, 'y') and hasattr(raw_value, 'z'):
        return {"x": raw_value.x, "y": raw_value.y, "z": raw_value.z}, None
    if hasattr(raw_value, 'Base') and hasattr(raw_value, 'Rotation'):
        position, _ = _property_value_for_json(raw_value.Base)
        rotation = raw_value.Rotation
        axis = getattr(rotation, 'Axis', None)
        return {
            "position": position,
            "axis": {"x": axis.x, "y": axis.y, "z": axis.z} if axis is not None else None,
            "angle": getattr(rotation, 'Angle', None),
        }, None
    if hasattr(raw_value, 'Name'):
        return raw_value.Name, None
    if isinstance(raw_value, (list, tuple)):
        return [_property_value_for_json(v)[0] for v in raw_value], None
    return str(raw_value), None


def _contains_reference(expr_str: str, needle: str) -> bool:
    """True if `needle` (e.g. "Params.Width") appears in `expr_str` as a
    whole reference, not as a substring of a longer identifier.

    Plain `needle in expr_str` containment is a syntactic-semantic-seam bug:
    a VarSet with sibling properties "Width" and "Width2" makes needle
    "Params.Width" a substring of "Params.Width2 * 2", so an expression that
    only references Width2 gets misattributed to Width. Word-boundary
    lookaround rejects a match with an identifier character immediately
    before or after it (so "MyParams.Width" and "Params.Width2" both
    correctly fail to match needle "Params.Width"), without requiring a full
    expression parser.
    """
    pattern = r'(?<![\w.])' + re.escape(needle) + r'(?!\w)'
    return re.search(pattern, expr_str) is not None


class VarSetOpsHandler(BaseHandler):
    """Handler for App::VarSet operations."""

    _ALLOWED_OPERATIONS = frozenset({
        "create_varset", "add_property", "set_property", "get_property",
        "set_enum_options", "list_properties", "remove_property",
        "bind_property", "list_references",
    })

    def create_varset(self, args: Dict[str, Any]) -> str:
        """Create a new App::VarSet in the active document."""
        try:
            name = args.get('varset_name', args.get('name', 'VarSet'))

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            varset = doc.addObject('App::VarSet', name)
            self.recompute(doc)

            return f"Created VarSet: {varset.Name}"

        except Exception as e:
            return f"Error creating VarSet: {e}"

    def add_property(self, args: Dict[str, Any]) -> str:
        """Add a dynamic, typed property to a VarSet."""
        try:
            varset_name = args.get('varset_name', '')
            type_ = args.get('type', '')
            name = args.get('name', '')
            group = args.get('group', '')
            docs = args.get('docs', '')
            locked = bool(args.get('locked', False))
            enum_vals = args.get('enum_vals')

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            if not name:
                return "Property name is required"
            if not type_:
                return "Property type is required (e.g. 'App::PropertyLength')"
            if enum_vals is not None and not isinstance(enum_vals, (list, tuple)):
                return "enum_vals must be a list of strings, not a bare string"

            supported = set(varset.supportedProperties())
            if type_ not in supported:
                hint = ", ".join(sorted(t for t in supported if t in _COMMON_PROPERTY_TYPES))
                return (
                    f"Unknown property type '{type_}'. Common types: {hint}. "
                    f"({len(supported)} total valid types on this FreeCAD build -- "
                    "any string from FreeCAD's supportedProperties() is accepted.)"
                )

            varset.addProperty(
                type_, name,
                group=group, doc=docs, attr=0,
                read_only=False, hidden=False, locked=locked,
                enum_vals=list(enum_vals) if enum_vals is not None else [],
            )
            self.recompute(doc)

            return f"Added property {varset_name}.{name} ({type_})"

        except Exception as e:
            return f"Error adding property: {e}"

    def set_property(self, args: Dict[str, Any]) -> str:
        """Set a VarSet property's value.

        Not used for App::PropertyEnumeration -- use set_enum_options, since
        FreeCAD overloads the same attribute for both the allowed-values
        list and the current value (see set_enum_options's docstring).
        """
        try:
            varset_name = args.get('varset_name', '')
            name = args.get('name', '')
            value = args.get('value')

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"
            if value is None:
                return "value is required"

            type_id = varset.getTypeIdOfProperty(name)
            if type_id == 'App::PropertyEnumeration':
                return (
                    f"'{name}' is an App::PropertyEnumeration -- use set_enum_options "
                    "to set its allowed values and/or current value."
                )
            if type_id == 'App::PropertyLink':
                # A Link property expects an actual DocumentObject, not a
                # JSON-serializable value -- the MCP schema's "value" field
                # (string/number/boolean) can't carry one directly, so this
                # resolves a string object name to the real object first,
                # same as bind_property/resolve_object do elsewhere in this
                # file. Previously this type was advertised as supported but
                # setattr(varset, name, value) could never succeed for it.
                if not isinstance(value, str):
                    return "value must be an object name (string) for App::PropertyLink"
                target = doc.getObject(value)
                if target is None:
                    return f"Error setting property: object not found: {value}"
                setattr(varset, name, target)
                self.recompute(doc)
                return f"Set {varset_name}.{name} = {value}"

            # Unit-bearing types (Length, Angle, Volume, ...) accept a bare
            # number or a unit string ("10 in") the same way FreeCAD's own
            # Python API does (varSet.Length = "10.0 mm") -- no separate
            # Quantity parsing needed here.
            setattr(varset, name, value)
            self.recompute(doc)

            return f"Set {varset_name}.{name} = {value}"

        except Exception as e:
            return f"Error setting property: {e}"

    def get_property(self, args: Dict[str, Any]) -> str:
        """Get a VarSet property's value, type, and (for Quantity-derived
        types) a unit-aware display string."""
        try:
            varset_name = args.get('varset_name', '')
            name = args.get('name', '')

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            type_id = varset.getTypeIdOfProperty(name)
            raw_value = getattr(varset, name)
            value, unit_string = _property_value_for_json(raw_value)

            result = {
                "varset": varset_name,
                "name": name,
                "value": value,
                "type": type_id,
            }
            if unit_string is not None:
                result["unit_string"] = unit_string
            if type_id == 'App::PropertyEnumeration':
                try:
                    result["options"] = list(varset.getEnumerationsOfProperty(name) or [])
                except Exception:
                    pass

            return json.dumps(result)

        except Exception as e:
            return f"Error getting property: {e}"

    def set_enum_options(self, args: Dict[str, Any]) -> str:
        """Set an App::PropertyEnumeration's allowed values and current value.

        FreeCAD overloads one Python attribute for both roles: assigning a
        list sets the allowed values, assigning a string sets the current
        value (and raises Base::ValueError if the list doesn't exist yet or
        the string isn't in it). Assigning an *int* index instead of a
        string before the list exists is a silent no-op, not an error --
        so this always sets the value by its string form, never by index,
        to fail loudly on an ordering bug instead of doing nothing.
        """
        try:
            varset_name = args.get('varset_name', '')
            name = args.get('name', '')
            options = args.get('options', [])
            default_index = args.get('default_index', 0)

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            type_id = varset.getTypeIdOfProperty(name)
            if type_id != 'App::PropertyEnumeration':
                return f"'{name}' is {type_id}, not App::PropertyEnumeration"
            if not isinstance(options, (list, tuple)):
                return "options must be a list of strings, not a bare string"
            if not options:
                return "options list is required and must be non-empty"
            if not isinstance(default_index, int) or isinstance(default_index, bool):
                return f"default_index must be an integer, got {type(default_index).__name__}"
            if not (0 <= default_index < len(options)):
                return f"default_index {default_index} out of range for {len(options)} options"

            setattr(varset, name, list(options))
            setattr(varset, name, options[default_index])
            self.recompute(doc)

            return (
                f"Set enum options for {varset_name}.{name}: {list(options)} "
                f"(default: {options[default_index]})"
            )

        except Exception as e:
            return f"Error setting enum options: {e}"

    def list_properties(self, args: Dict[str, Any]) -> str:
        """List a VarSet's dynamic properties (built-in DocumentObject
        properties like Placement/Label are excluded).

        limit/offset paginate like document_ops.list_objects -- "total" and
        "truncated" let the caller detect an elided page instead of a
        response that just silently stops (and could otherwise exceed the
        bridge's 50KB message cap on a VarSet with very many properties).
        """
        try:
            varset_name = args.get('varset_name', '')
            raw_limit = args.get('limit', 200)
            limit = max(0, min(int(200 if raw_limit is None else raw_limit), 1000))
            raw_offset = args.get('offset', 0)
            offset = max(0, int(0 if raw_offset is None else raw_offset))

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            bit_err = _verify_prop_dynamic_bit(varset)
            if bit_err:
                return bit_err

            dynamic = [(n, varset.getPropertyStatus(n)) for n in varset.PropertiesList]
            dynamic = [(n, s) for n, s in dynamic if _PROP_DYNAMIC_BIT in s]
            total = len(dynamic)
            page = dynamic[offset:offset + limit]

            properties = []
            for name, status in page:
                raw_value = getattr(varset, name)
                value, unit_string = _property_value_for_json(raw_value)

                entry = {
                    "name": name,
                    "type": varset.getTypeIdOfProperty(name),
                    "group": varset.getGroupOfProperty(name),
                    "value": value,
                    "locked": "LockDynamic" in status,
                    "hidden": "Hidden" in status,
                    "read_only": "ReadOnly" in status,
                }
                if unit_string is not None:
                    entry["unit_string"] = unit_string
                if varset.getTypeIdOfProperty(name) == 'App::PropertyEnumeration':
                    try:
                        entry["options"] = list(varset.getEnumerationsOfProperty(name) or [])
                    except Exception:
                        pass
                properties.append(entry)

            return json.dumps({
                "varset": varset_name,
                "properties": properties,
                "total": total,
                "truncated": offset + len(page) < total,
            })

        except Exception as e:
            return f"Error listing properties: {e}"

    def remove_property(self, args: Dict[str, Any]) -> str:
        """Remove a dynamic property from a VarSet.

        Blocks by default if list_references finds anything bound to this
        property (or can't check on this FreeCAD version) -- FreeCAD does
        not orphan-proof removeProperty(); it only clears an expression set
        on the property itself, never scans other objects for references
        to it. Pass force=true to remove anyway.
        """
        try:
            varset_name = args.get('varset_name', '')
            name = args.get('name', '')
            force = bool(args.get('force', False))

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err
            bit_err = _verify_prop_dynamic_bit(varset)
            if bit_err:
                return bit_err
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            status = varset.getPropertyStatus(name)
            if _PROP_DYNAMIC_BIT not in status:
                return f"Cannot remove '{name}': not a dynamic property (built-in property)."
            if "LockDynamic" in status:
                return f"Cannot remove '{name}': property is locked (added with locked=True)."

            # Calls the dict-returning core directly with the varset already
            # resolved above, instead of round-tripping through the public
            # list_references()'s JSON string (a second resolve_object() +
            # TypeId check plus a json.dumps/json.loads pair, purely for an
            # in-process call).
            refs = self._list_references_dict(varset, varset_name, property_name=name)

            available = refs.get("available", False)
            # Use "total" (the actual reference count), not len(references) --
            # list_references paginates, so a page smaller than the real count
            # would otherwise understate how many references actually exist.
            ref_count = refs.get("total", len(refs.get("references", [])))

            if not force and (not available or ref_count > 0):
                if available:
                    reason = (
                        f"{ref_count} reference(s) to {varset_name}.{name} found. "
                        "Removing would orphan them -- FreeCAD does not update external "
                        "expressions when a property is removed."
                    )
                else:
                    reason = (
                        "Could not check for references on this FreeCAD version "
                        f"({refs.get('message', 'reference tracking unavailable')})."
                    )
                return json.dumps({
                    "blocked": True,
                    "reason": f"{reason} Pass force=true to proceed anyway.",
                    "references": refs,
                })

            # removeProperty() returns bool -- it does NOT raise for the
            # locked/not-dynamic cases (those exceptions live in an internal
            # C++ path unreachable from this Python method), which is why
            # both are checked explicitly above rather than caught here.
            removed = varset.removeProperty(name)
            if not removed:
                return f"Failed to remove '{name}' (FreeCAD refused; reason unknown)."

            # removeProperty() already succeeded at this point -- a recompute
            # failure here must not be reported as "Error removing property",
            # which would be indistinguishable from removal itself failing
            # when the property is actually already gone.
            recompute_error = None
            try:
                self.recompute(doc)
            except Exception as e:
                recompute_error = str(e)

            result = {"removed": name, "varset": varset_name, "references": refs}
            if recompute_error is not None:
                result["recompute_error"] = recompute_error
            return json.dumps(result)

        except Exception as e:
            return f"Error removing property: {e}"

    def bind_property(self, args: Dict[str, Any]) -> str:
        """Bind an object's property to a VarSet property using expressions."""
        try:
            object_name = args.get('object_name', '')
            property_name = args.get('property_name', '')
            varset_name = args.get('varset_name', '')
            varset_property = args.get('varset_property', '')

            if not varset_property:
                return "varset_property is required"

            def _validate_varset(doc, varset):
                if varset.TypeId != 'App::VarSet':
                    return f"Object {varset_name} is not a VarSet"
                if varset_property not in varset.PropertiesList:
                    return f"Property not found on VarSet {varset_name}: {varset_property}"
                return None

            return self.bind_expression(
                object_name, property_name, varset_name, varset_property,
                target_noun='VarSet', validate_target=_validate_varset,
            )

        except Exception as e:
            return f"Error binding property: {e}"

    def list_references(self, args: Dict[str, Any]) -> str:
        """List objects/properties bound to a VarSet (or one of its
        properties) via expressions.

        Wraps DepEdge/InListProp, which is absent from FreeCAD builds older
        than weekly-2026.06.24 (including all 1.1.x stable releases).
        Feature-detects rather than crashing; an empty "references" list
        always means "checked, found nothing" -- never "couldn't check"
        (that case sets available=False instead).

        InListProp is a Python PROPERTY (plain attribute access, not a
        method call) despite the underlying C++ implementation being named
        DocumentObjectPy::getInListProp -- FreeCAD's binding generator
        strips the "get" prefix for this one. Confirmed live 2026-08-31
        (full-review follow-up): `hasattr(varset, 'getInListProp')` was
        always False on real FreeCAD regardless of build, silently forcing
        this method into its "unavailable" branch on every environment,
        including ones that fully support dependency-edge tracking. Do not
        revert this to `getInListProp()` without re-confirming the real
        binding name via `dir(obj)` against a live instance first.

        limit/offset paginate like list_properties -- "total" and
        "truncated" let the caller detect an elided page. remove_property
        uses "total" (not len(references)) for its blocking decision, so a
        truncated page never undercounts how many references actually
        exist.
        """
        try:
            varset_name = args.get('varset_name', '')
            property_name = args.get('property_name')
            raw_limit = args.get('limit', 200)
            limit = max(0, min(int(200 if raw_limit is None else raw_limit), 1000))
            raw_offset = args.get('offset', 0)
            offset = max(0, int(0 if raw_offset is None else raw_offset))

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet', type_id='App::VarSet')
            if err:
                return err

            return json.dumps(self._list_references_dict(varset, varset_name, property_name, limit, offset))

        except Exception as e:
            return f"Error listing references: {e}"

    def _list_references_dict(self, varset, varset_name: str, property_name=None,
                               limit: int = 200, offset: int = 0) -> dict:
        """Core of list_references() -- returns a plain dict instead of a
        JSON string. Shared by the public list_references() (which wraps
        this in json.dumps for the MCP response) and remove_property()
        (which consumes the dict directly, with `varset` already resolved,
        avoiding a second resolve_object() lookup and a stringify/parse
        round trip for what both callers use as an in-process query).
        """
        if not hasattr(varset, 'InListProp'):
            return {
                "varset": varset_name,
                "property_name": property_name,
                "available": False,
                "references": [],
                "total": 0,
                "truncated": False,
                "message": (
                    "Dependency-edge tracking (InListProp) is not available on "
                    "this FreeCAD build. It requires FreeCAD's DepEdge API, first "
                    "shipped in weekly-2026.06.24; it is absent from all 1.1.x "
                    "stable releases."
                ),
            }

        all_edges = [
            edge for edge in varset.InListProp
            if not property_name or getattr(edge, 'ToProp', None) == property_name
        ]
        total = len(all_edges)
        page = all_edges[offset:offset + limit]

        references = []
        for edge in page:
            to_prop = getattr(edge, 'ToProp', None)
            from_obj = getattr(edge, 'FromObj', None)
            from_prop_raw = getattr(edge, 'FromProp', None)

            # FromProp is hardcoded to the literal string "ExpressionEngine"
            # for expression-derived edges (DocumentObject.cpp) -- it never
            # names the actual bound property. Resolve it by reading the
            # referencing object's own ExpressionEngine list and matching
            # against the VarSet reference the expression text must contain.
            resolved_prop = None
            if from_obj is not None and hasattr(from_obj, 'ExpressionEngine'):
                needle = f"{varset_name}.{to_prop}" if to_prop else varset_name
                try:
                    for path_str, expr_str in from_obj.ExpressionEngine:
                        if _contains_reference(expr_str, needle):
                            resolved_prop = path_str
                            break
                except Exception:
                    pass

            references.append({
                "from_object": getattr(from_obj, 'Name', str(from_obj)),
                "from_property": resolved_prop,
                "from_property_raw": from_prop_raw,
                "to_property": to_prop,
            })

        return {
            "varset": varset_name,
            "property_name": property_name,
            "available": True,
            "references": references,
            "total": total,
            "truncated": offset + len(page) < total,
        }

# App::VarSet operation handlers for FreeCAD MCP
#
# App::VarSet is a plain App::DocumentObject subclass (no Proxy) used as a
# lightweight container for named, typed parametric variables that other
# objects bind to via expressions. See SPEC-varset-operations.md for the
# FreeCAD source citations backing the design choices below.

import json
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
    getValueAs/UserString; Link-typed values expose Name. Plain
    str/int/float/bool/None pass through unchanged with no unit string.
    """
    if isinstance(raw_value, (str, int, float, bool, type(None))):
        return raw_value, None
    if hasattr(raw_value, 'getValueAs'):
        try:
            return float(raw_value.Value), getattr(raw_value, 'UserString', str(raw_value))
        except Exception:
            return str(raw_value), None
    if hasattr(raw_value, 'Name'):
        return raw_value.Name, None
    return str(raw_value), None


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

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"
            if not name:
                return "Property name is required"
            if not type_:
                return "Property type is required (e.g. 'App::PropertyLength')"

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

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            type_id = varset.getTypeIdOfProperty(name)
            if type_id == 'App::PropertyEnumeration':
                return (
                    f"'{name}' is an App::PropertyEnumeration -- use set_enum_options "
                    "to set its allowed values and/or current value."
                )

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

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"
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

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            type_id = varset.getTypeIdOfProperty(name)
            if type_id != 'App::PropertyEnumeration':
                return f"'{name}' is {type_id}, not App::PropertyEnumeration"
            if not options:
                return "options list is required and must be non-empty"
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
        properties like Placement/Label are excluded)."""
        try:
            varset_name = args.get('varset_name', '')

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"

            properties = []
            for name in varset.PropertiesList:
                status = varset.getPropertyStatus(name)
                if _PROP_DYNAMIC_BIT not in status:
                    continue

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
                properties.append(entry)

            return json.dumps({"varset": varset_name, "properties": properties})

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

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"
            if not name:
                return "Property name is required"
            if name not in varset.PropertiesList:
                return f"Property not found: {name}"

            status = varset.getPropertyStatus(name)
            if _PROP_DYNAMIC_BIT not in status:
                return f"Cannot remove '{name}': not a dynamic property (built-in property)."
            if "LockDynamic" in status:
                return f"Cannot remove '{name}': property is locked (added with locked=True)."

            refs_raw = self.list_references({"varset_name": varset_name, "property_name": name})
            try:
                refs = json.loads(refs_raw)
            except Exception:
                refs = {"available": False, "references": [], "message": refs_raw}

            available = refs.get("available", False)
            ref_count = len(refs.get("references", []))

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

            self.recompute(doc)

            return json.dumps({"removed": name, "varset": varset_name, "references": refs})

        except Exception as e:
            return f"Error removing property: {e}"

    def bind_property(self, args: Dict[str, Any]) -> str:
        """Bind an object's property to a VarSet property using expressions."""
        try:
            object_name = args.get('object_name', '')
            property_name = args.get('property_name', '')
            varset_name = args.get('varset_name', '')
            varset_property = args.get('varset_property', '')

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            _, varset, err = self.resolve_object(varset_name, doc, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"

            expression = f"{varset_name}.{varset_property}"
            obj.setExpression(property_name, expression)
            self.recompute(doc)

            return f"Bound {object_name}.{property_name} to {expression}"

        except Exception as e:
            return f"Error binding property: {e}"

    def list_references(self, args: Dict[str, Any]) -> str:
        """List objects/properties bound to a VarSet (or one of its
        properties) via expressions.

        Wraps DepEdge/getInListProp, which is absent from FreeCAD builds
        older than weekly-2026.06.24 (including all 1.1.x stable releases).
        Feature-detects rather than crashing; an empty "references" list
        always means "checked, found nothing" -- never "couldn't check"
        (that case sets available=False instead).
        """
        try:
            varset_name = args.get('varset_name', '')
            property_name = args.get('property_name')

            doc, varset, err = self.resolve_object(varset_name, noun='VarSet')
            if err:
                return err
            if varset.TypeId != 'App::VarSet':
                return f"Object {varset_name} is not a VarSet"

            if not hasattr(varset, 'getInListProp'):
                return json.dumps({
                    "varset": varset_name,
                    "property_name": property_name,
                    "available": False,
                    "references": [],
                    "message": (
                        "Dependency-edge tracking (getInListProp) is not available on "
                        "this FreeCAD build. It requires FreeCAD's DepEdge API, first "
                        "shipped in weekly-2026.06.24; it is absent from all 1.1.x "
                        "stable releases."
                    ),
                })

            references = []
            for edge in varset.getInListProp():
                to_prop = getattr(edge, 'ToProp', None)
                if property_name and to_prop != property_name:
                    continue

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
                            if needle in expr_str:
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

            return json.dumps({
                "varset": varset_name,
                "property_name": property_name,
                "available": True,
                "references": references,
            })

        except Exception as e:
            return f"Error listing references: {e}"

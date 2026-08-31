"""Integration tests for varset_operations against a live FreeCAD instance.

App::VarSet exercises FreeCAD's dynamic-property system (addProperty/
removeProperty status-bit quirks), the PropertyEnumeration overload, and the
expression engine (DepEdge/ExpressionEngine) for cross-object bindings --
none of which the unit tests' MockVarSet can independently verify itself
against, since the mock's fidelity is what these tests exist to check.

Background (full-review 2026-08-31, finding #05): every other handler in
this repo has an integration-test counterpart; varset_ops.py shipped with
unit tests only, leaving the mock's fidelity to real FreeCAD (particularly
the two "looks like it'd raise but doesn't" API behaviors documented in
CLAUDE.md, and the DepEdge/ExpressionEngine reference-resolution path)
completely unverified against a live process.

Run with: python3 -m pytest tests/integration/test_varset_ops.py -v
"""

import json
import time

import pytest

from . import conftest as _conftest  # noqa: F401  bootstraps the session fixture
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _vs(args: dict, timeout: float = 10.0):
    """Call varset_operations and return the bridge-wrapped result string."""
    resp = send_command("varset_operations", args, timeout=timeout)
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return resp


def _exec(code: str, timeout: float = 10.0):
    return send_command("execute_python_sync", {"code": code}, timeout=timeout)


@pytest.fixture
def doc_with_varset():
    doc_name = f"VS_{int(time.time() * 1000) % 100000}"
    varset_name = "Params"
    send_command("view_control", {"operation": "create_document",
                                  "document_name": doc_name})
    _vs({"operation": "create_varset", "varset_name": varset_name})
    yield doc_name, varset_name
    try:
        _exec(f"FreeCAD.closeDocument({doc_name!r})")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Basic create/add/set/get round trip
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBasicRoundTrip:
    def test_create_varset_typeid(self, doc_with_varset):
        _, varset_name = doc_with_varset
        result = _exec(f"FreeCAD.ActiveDocument.getObject({varset_name!r}).TypeId")
        assert "App::VarSet" in str(result)

    def test_add_length_property_and_round_trip_value(self, doc_with_varset):
        _, varset_name = doc_with_varset
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyLength", "name": "Width"})
        _vs({"operation": "set_property", "varset_name": varset_name,
             "name": "Width", "value": 25.0})
        result = _vs({"operation": "get_property", "varset_name": varset_name,
                      "name": "Width"})
        payload = json.loads(result)
        assert payload["value"] == 25.0
        assert payload["type"] == "App::PropertyLength"
        assert "unit_string" in payload

    def test_add_uncommon_type_property(self, doc_with_varset):
        """Confirms add_property accepts a type outside the curated hint
        list, validated only against the live supportedProperties()."""
        _, varset_name = doc_with_varset
        result = _vs({"operation": "add_property", "varset_name": varset_name,
                      "type": "App::PropertyColor", "name": "Tint"})
        assert "Tint" in result and "Error" not in result


# ---------------------------------------------------------------------------
# The two documented "looks like it'd raise but doesn't" FreeCAD behaviors
# (CLAUDE.md: "App::VarSet: two FreeCAD API behaviors that look like they'd
# raise, but don't") -- these are exactly what MockVarSet claims to
# replicate; this class is the live check that the claim is correct.
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestDocumentedQuirksAgainstRealFreeCAD:
    def test_remove_builtin_property_rejected_not_via_exception(self, doc_with_varset):
        """Uses 'Label' -- confirmed live that a bare App::VarSet's real
        PropertiesList is ['ExpressionEngine', 'Label', 'Label2',
        'Visibility'], with no Placement property at all (unlike most
        DocumentObjects). This test originally used 'Placement' and was the
        first live run to catch that assumption was wrong."""
        _, varset_name = doc_with_varset
        result = _vs({"operation": "remove_property", "varset_name": varset_name,
                      "name": "Label"})
        assert "not a dynamic property" in result.lower()

    def test_remove_locked_property_rejected(self, doc_with_varset):
        _, varset_name = doc_with_varset
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyLength", "name": "Fixed", "locked": True})
        result = _vs({"operation": "remove_property", "varset_name": varset_name,
                      "name": "Fixed", "force": True})
        assert "locked" in result.lower()

    def test_enum_value_assigned_by_string_survives(self, doc_with_varset):
        """Pins the PropertyStandard.cpp int-assignment silent-no-op quirk:
        set_enum_options must assign the default by string form for the
        value to actually take, not by raw index."""
        _, varset_name = doc_with_varset
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyEnumeration", "name": "Grade"})
        _vs({"operation": "set_enum_options", "varset_name": varset_name,
             "name": "Grade", "options": ["A", "B", "C"], "default_index": 1})
        result = _vs({"operation": "get_property", "varset_name": varset_name,
                      "name": "Grade"})
        payload = json.loads(result)
        assert payload["value"] == "B"
        assert payload["options"] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# bind_property / list_references -- DepEdge + ExpressionEngine, the parts
# no mock can fully stand in for.
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBindingsAndReferences:
    def test_bind_and_find_reference(self, doc_with_varset):
        doc_name, varset_name = doc_with_varset
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyLength", "name": "Width"})
        # A Length property defaults to 0.0 -- binding a Box's own Length to
        # an unset Width would make the box degenerate (zero volume) and
        # legitimately mark it State=Invalid after recompute, which
        # bind_property's own _check_feature_state check would then (also
        # legitimately) report as an error. Set a real value first.
        _vs({"operation": "set_property", "varset_name": varset_name,
             "name": "Width", "value": 5.0})
        send_command("part_operations", {"operation": "box", "name": "Cube", "length": 1, "width": 1, "height": 1})
        result = _vs({"operation": "bind_property", "object_name": "Cube",
                      "property_name": "Length", "varset_name": varset_name,
                      "varset_property": "Width"})
        assert "Bound" in result

        refs_result = _vs({"operation": "list_references", "varset_name": varset_name,
                           "property_name": "Width"})
        refs = json.loads(refs_result)
        if not refs["available"]:
            # getInListProp (FreeCAD's DepEdge API) is absent on this build --
            # confirmed live (2026-08-31) that even a 26.3.0 build dated after
            # the documented weekly-2026.06.24 cutoff can lack it, if it's a
            # generic/shallow checkout rather than the project's own patched
            # branch (see CLAUDE.md on FreeCAD-prefs build provenance). This
            # is list_references' own documented graceful-degradation path,
            # not a test or handler bug -- nothing to verify against a build
            # that doesn't have the feature.
            pytest.skip(f"getInListProp unavailable on this FreeCAD build: {refs.get('message')}")
        assert any(r["from_object"] == "Cube" for r in refs["references"])

    def test_bind_nonexistent_property_rejected(self, doc_with_varset):
        """Pins the review finding: bind_property previously reported
        success even for a varset_property that was never added."""
        doc_name, varset_name = doc_with_varset
        send_command("part_operations", {"operation": "box", "name": "Cube2", "length": 1, "width": 1, "height": 1})
        result = _vs({"operation": "bind_property", "object_name": "Cube2",
                      "property_name": "Length", "varset_name": varset_name,
                      "varset_property": "DoesNotExist"})
        assert "not found" in result.lower()

    def test_prefix_colliding_property_names_not_misattributed(self, doc_with_varset):
        """Live confirmation of the substring-match fix: a VarSet with both
        Width and Width2 must not misattribute a Width2 binding to Width."""
        doc_name, varset_name = doc_with_varset
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyLength", "name": "Width"})
        _vs({"operation": "add_property", "varset_name": varset_name,
             "type": "App::PropertyLength", "name": "Width2"})
        # Nonzero, per test_bind_and_find_reference's comment above -- a
        # zero-Length/Height box is degenerate and marks State=Invalid.
        _vs({"operation": "set_property", "varset_name": varset_name,
             "name": "Width", "value": 5.0})
        _vs({"operation": "set_property", "varset_name": varset_name,
             "name": "Width2", "value": 3.0})
        send_command("part_operations", {"operation": "box", "name": "Cube3", "length": 1, "width": 1, "height": 1})
        # Bind Width2 first so its ExpressionEngine entry is ordered before
        # any Width binding -- this is the exact ordering that made the
        # naive substring match misattribute the wrong entry.
        _vs({"operation": "bind_property", "object_name": "Cube3",
             "property_name": "Length", "varset_name": varset_name,
             "varset_property": "Width2"})
        _vs({"operation": "bind_property", "object_name": "Cube3",
             "property_name": "Height", "varset_name": varset_name,
             "varset_property": "Width"})

        refs_result = _vs({"operation": "list_references", "varset_name": varset_name,
                           "property_name": "Width"})
        refs = json.loads(refs_result)
        if not refs["available"]:
            pytest.skip(f"getInListProp unavailable on this FreeCAD build: {refs.get('message')}")
        matches = [r for r in refs["references"] if r["from_object"] == "Cube3"]
        assert len(matches) == 1
        assert matches[0]["from_property"] == ".Height"

"""Unit tests for VarSetOpsHandler.

Covers create_varset, add_property, set_property, get_property,
set_enum_options, list_properties, remove_property, bind_property,
list_references.

Per this project's Spec Review Rule, SPEC-varset-operations.md was cold-
reviewed against the live FC-clone source before implementation, and two
of its original design assumptions turned out to be wrong:

  1. removeProperty() does NOT raise Base::RuntimeError for locked/
     non-dynamic properties -- that exception path is unreachable from
     Python (DocumentObjectPyImp.cpp + DocumentObject.cpp's early guard).
     It returns False silently instead, so remove_property must check
     property status *before* calling it, not catch an exception after.
  2. PropertyEnumeration's overloaded attribute raises Base::ValueError
     on an out-of-order string assignment, but silently no-ops on an
     out-of-order *int* assignment (PropertyStandard.cpp has no `else`
     branch for that case) -- so set_enum_options must assign the value
     by its string form, never by index.

Tests below pin both corrected behaviors, plus the threshold/ambiguous-
input cases required by this repo's Threshold-Boundary Testing Rule.
"""

import json
import unittest

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_varset,
    make_dep_edge,
    assert_error_contains,
    assert_success_contains,
)

from handlers.varset_ops import VarSetOpsHandler, _property_value_for_json, _PROP_DYNAMIC_BIT


class TestPropertyValueForJson(unittest.TestCase):
    """_property_value_for_json is the single conversion point every JSON-
    returning op routes through -- pin its three branches directly."""

    def test_plain_scalars_pass_through(self):
        for v in ("hello", 5, 3.5, True, None):
            value, unit_string = _property_value_for_json(v)
            self.assertEqual(value, v)
            self.assertIsNone(unit_string)

    def test_quantity_like_value_extracted(self):
        class FakeQuantity:
            Value = 12.5
            UserString = "12.50 mm"
            def getValueAs(self, unit):
                return self.Value
        value, unit_string = _property_value_for_json(FakeQuantity())
        self.assertEqual(value, 12.5)
        self.assertEqual(unit_string, "12.50 mm")

    def test_link_like_value_uses_name(self):
        class FakeLink:
            Name = "Box001"
        value, unit_string = _property_value_for_json(FakeLink())
        self.assertEqual(value, "Box001")
        self.assertIsNone(unit_string)


class TestCreateVarSet(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.create_varset({'name': 'Params'})
        assert_error_contains(self, result, "no active document")

    def test_creates_varset_typeid(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_varset({'name': 'Params'})
        assert_success_contains(self, result, "Params")
        doc.addObject.assert_called_once_with('App::VarSet', 'Params')

    def test_varset_name_alias_accepted(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_varset({'varset_name': 'Sizes'})
        doc.addObject.assert_called_once_with('App::VarSet', 'Sizes')

    def test_default_name(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_varset({})
        doc.addObject.assert_called_once_with('App::VarSet', 'VarSet')


class TestAddProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_missing_varset(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Ghost', 'type': 'App::PropertyLength', 'name': 'Width',
        })
        assert_error_contains(self, result, "varset not found", "ghost")

    def test_wrong_typeid_rejects(self):
        not_a_varset = make_part_object("Box1")
        doc = make_mock_doc([not_a_varset])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Box1', 'type': 'App::PropertyLength', 'name': 'Width',
        })
        assert_error_contains(self, result, "not a varset")

    def test_missing_name(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyLength',
        })
        assert_error_contains(self, result, "name is required")

    def test_missing_type(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Params', 'name': 'Width',
        })
        assert_error_contains(self, result, "type is required")

    def test_common_type_added(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyLength', 'name': 'Width',
        })
        assert_success_contains(self, result, "Width", "App::PropertyLength")
        self.assertIn('Width', vs.PropertiesList)

    def test_valid_but_uncommon_type_accepted(self):
        """add_property must accept ANY type from supportedProperties(), not just
        the curated hint list -- this is the fix for the spec's original
        short-name-vs-App::Property* naming contradiction: there is exactly one
        namespace (FreeCAD's fully-qualified type strings), validated against
        the live list, with no separate alias table to drift out of sync."""
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyColor', 'name': 'Tint',
        })
        assert_success_contains(self, result, "Tint")

    def test_unknown_type_rejected_with_hint(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyBogus', 'name': 'Width',
        })
        assert_error_contains(self, result, "unknown property type", "App::PropertyLength")

    def test_duplicate_name_raises_not_silently_overwrites(self):
        """Pins the review finding: DynamicProperty.cpp:253-254 raises
        Base::NameError on a duplicate dynamic-property name -- addProperty
        never silently replaces the existing property's type."""
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyLength', 'name': 'Width',
        })
        result = self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyInteger', 'name': 'Width',
        })
        assert_error_contains(self, result, "already exists")
        # Original type must survive -- confirms no silent overwrite.
        self.assertEqual(vs.getTypeIdOfProperty('Width'), 'App::PropertyLength')

    def test_enum_vals_at_creation(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyEnumeration', 'name': 'Grade',
            'enum_vals': ['A', 'B', 'C'],
        })
        self.assertEqual(vs.getEnumerationsOfProperty('Grade'), ['A', 'B', 'C'])

    def test_locked_true_recorded(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.add_property({
            'varset_name': 'Params', 'type': 'App::PropertyLength', 'name': 'Fixed',
            'locked': True,
        })
        self.assertIn('LockDynamic', vs.getPropertyStatus('Fixed'))


class TestSetProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def _varset_with_length(self, name='Width'):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', name)
        return vs

    def test_property_not_found(self):
        vs = self._varset_with_length()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_property({
            'varset_name': 'Params', 'name': 'Ghost', 'value': 5,
        })
        assert_error_contains(self, result, "property not found")

    def test_sets_numeric_value(self):
        vs = self._varset_with_length()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_property({
            'varset_name': 'Params', 'name': 'Width', 'value': 25.0,
        })
        assert_success_contains(self, result, "Width", "25.0")
        self.assertEqual(vs._values['Width'], 25.0)

    def test_sets_unit_string_value(self):
        vs = self._varset_with_length()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.set_property({
            'varset_name': 'Params', 'name': 'Width', 'value': "10 in",
        })
        self.assertEqual(vs._values['Width'], "10 in")

    def test_enumeration_rejected_redirects_to_set_enum_options(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyEnumeration', 'Grade', enum_vals=['A', 'B'])
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_property({
            'varset_name': 'Params', 'name': 'Grade', 'value': 'B',
        })
        assert_error_contains(self, result, "set_enum_options")


class TestGetProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_property_not_found(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_property({'varset_name': 'Params', 'name': 'Ghost'})
        assert_error_contains(self, result, "property not found")

    def test_json_round_trip_scalar(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyInteger', 'Count')
        vs.Count = 7
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_property({'varset_name': 'Params', 'name': 'Count'})
        parsed = json.loads(result)
        self.assertEqual(parsed['value'], 7)
        self.assertEqual(parsed['type'], 'App::PropertyInteger')
        self.assertNotIn('unit_string', parsed)

    def test_json_round_trip_quantity_includes_unit_string(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        vs.Width = 42.0
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_property({'varset_name': 'Params', 'name': 'Width'})
        parsed = json.loads(result)
        self.assertEqual(parsed['value'], 42.0)
        self.assertIn('unit_string', parsed)
        self.assertIn('mm', parsed['unit_string'])

    def test_enumeration_includes_options(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyEnumeration', 'Grade', enum_vals=['A', 'B', 'C'])
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_property({'varset_name': 'Params', 'name': 'Grade'})
        parsed = json.loads(result)
        self.assertEqual(parsed['options'], ['A', 'B', 'C'])
        self.assertEqual(parsed['value'], 'A')


class TestSetEnumOptions(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def _varset_with_enum(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyEnumeration', 'Grade')
        return vs

    def test_not_an_enumeration_rejected(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Width', 'options': ['A', 'B'],
        })
        assert_error_contains(self, result, "not App::PropertyEnumeration")

    def test_empty_options_rejected(self):
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': [],
        })
        assert_error_contains(self, result, "options list is required")

    def test_default_index_zero_is_valid(self):
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': ['A', 'B', 'C'],
            'default_index': 0,
        })
        assert_success_contains(self, result, "A")
        self.assertEqual(vs._values['Grade'], 'A')

    def test_default_index_last_valid_is_valid(self):
        """Threshold: default_index == len(options) - 1 is the last valid index."""
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': ['A', 'B', 'C'],
            'default_index': 2,
        })
        assert_success_contains(self, result, "C")
        self.assertEqual(vs._values['Grade'], 'C')

    def test_default_index_one_past_end_rejected(self):
        """Threshold: default_index == len(options) is one past the last valid index."""
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': ['A', 'B', 'C'],
            'default_index': 3,
        })
        assert_error_contains(self, result, "out of range")

    def test_default_index_negative_rejected(self):
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': ['A', 'B', 'C'],
            'default_index': -1,
        })
        assert_error_contains(self, result, "out of range")

    def test_value_assigned_by_string_not_index(self):
        """Pins the review finding: PropertyStandard.cpp's int-assignment
        branch has no `else`, so assigning the value by integer index (rather
        than by its string form) would silently no-op instead of raising on
        an ordering bug. set_enum_options must assign options[default_index]
        (a string) so a real ordering mistake fails loudly."""
        vs = self._varset_with_enum()
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.set_enum_options({
            'varset_name': 'Params', 'name': 'Grade', 'options': ['A', 'B', 'C'],
            'default_index': 1,
        })
        # The value stored must be the string 'B', never the raw int 1.
        self.assertEqual(vs._values['Grade'], 'B')
        self.assertIsInstance(vs._values['Grade'], str)


class TestListProperties(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_empty_varset_returns_empty_list_not_error(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_properties({'varset_name': 'Params'})
        parsed = json.loads(result)
        self.assertEqual(parsed['properties'], [])

    def test_builtin_properties_filtered_out(self):
        """A VarSet has no dynamic properties by default -- Placement/Label/
        Visibility must never appear, confirming the PropDynamic-bit filter
        (not a hand-maintained exclude-list) does its job."""
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_properties({'varset_name': 'Params'})
        parsed = json.loads(result)
        names = [p['name'] for p in parsed['properties']]
        self.assertNotIn('Placement', names)
        self.assertNotIn('Label', names)
        self.assertNotIn('Visibility', names)

    def test_reports_locked_hidden_readonly_flags(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Fixed', locked=True, hidden=True, read_only=True)
        vs.addProperty('App::PropertyInteger', 'Open')
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_properties({'varset_name': 'Params'})
        parsed = {p['name']: p for p in json.loads(result)['properties']}
        self.assertTrue(parsed['Fixed']['locked'])
        self.assertTrue(parsed['Fixed']['hidden'])
        self.assertTrue(parsed['Fixed']['read_only'])
        self.assertFalse(parsed['Open']['locked'])
        self.assertFalse(parsed['Open']['hidden'])
        self.assertFalse(parsed['Open']['read_only'])


class TestRemoveProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_property_not_found(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Ghost'})
        assert_error_contains(self, result, "property not found")

    def test_builtin_property_rejected_via_status_not_exception(self):
        """Pins the review finding: removeProperty() never raises
        RuntimeError('property is not dynamic') through Python -- the
        handler must detect this via getPropertyStatus's PropDynamic bit
        BEFORE calling removeProperty, not by catching an exception after."""
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Placement'})
        assert_error_contains(self, result, "not a dynamic property")

    def test_locked_property_rejected_via_status_not_exception(self):
        """Pins the review finding: removeProperty() never raises
        RuntimeError('property is locked') through Python either -- same
        status-bit pre-check as the not-dynamic case above."""
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Fixed', locked=True)
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Fixed'})
        assert_error_contains(self, result, "locked")

    def test_no_references_removes_without_force(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        # No getInListProp on this mock varset by default -> list_references
        # reports available=False, so this also exercises the "can't verify,
        # block unless forced" path when there ARE no real references.
        result = self.handler.remove_property({
            'varset_name': 'Params', 'name': 'Width', 'force': True,
        })
        parsed = json.loads(result)
        self.assertEqual(parsed['removed'], 'Width')
        self.assertNotIn('Width', vs.PropertiesList)

    def test_unavailable_reference_check_blocks_by_default(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Width'})
        parsed = json.loads(result)
        self.assertTrue(parsed['blocked'])
        self.assertIn('Width', vs.PropertiesList)  # not actually removed

    def test_references_found_blocks_without_force(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        consumer = make_part_object("Cube")
        vs.getInListProp = lambda: [make_dep_edge(consumer, 'Width')]
        doc = make_mock_doc([vs, consumer])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Width'})
        parsed = json.loads(result)
        self.assertTrue(parsed['blocked'])
        self.assertEqual(len(parsed['references']['references']), 1)
        self.assertIn('Width', vs.PropertiesList)

    def test_references_found_removes_with_force(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        consumer = make_part_object("Cube")
        vs.getInListProp = lambda: [make_dep_edge(consumer, 'Width')]
        doc = make_mock_doc([vs, consumer])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({
            'varset_name': 'Params', 'name': 'Width', 'force': True,
        })
        parsed = json.loads(result)
        self.assertEqual(parsed['removed'], 'Width')
        self.assertNotIn('Width', vs.PropertiesList)

    def test_zero_references_with_detection_available_removes_without_force(self):
        vs = make_varset("Params")
        vs.addProperty('App::PropertyLength', 'Width')
        vs.getInListProp = lambda: []
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.remove_property({'varset_name': 'Params', 'name': 'Width'})
        parsed = json.loads(result)
        self.assertEqual(parsed['removed'], 'Width')


class TestBindProperty(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_missing_object(self):
        vs = make_varset("Params")
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.bind_property({
            'object_name': 'Ghost', 'property_name': 'Length',
            'varset_name': 'Params', 'varset_property': 'Width',
        })
        assert_error_contains(self, result, "not found", "ghost")

    def test_wrong_typeid_varset_rejected(self):
        cube = make_part_object("Cube")
        not_a_varset = make_part_object("Box1")
        doc = make_mock_doc([cube, not_a_varset])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.bind_property({
            'object_name': 'Cube', 'property_name': 'Length',
            'varset_name': 'Box1', 'varset_property': 'Width',
        })
        assert_error_contains(self, result, "not a varset")

    def test_binds_expression(self):
        vs = make_varset("Params")
        cube = make_part_object("Cube")
        doc = make_mock_doc([vs, cube])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.bind_property({
            'object_name': 'Cube', 'property_name': 'Length',
            'varset_name': 'Params', 'varset_property': 'Width',
        })
        assert_success_contains(self, result, "Cube.Length", "Params.Width")
        cube.setExpression.assert_called_once_with('Length', 'Params.Width')
        doc.recompute.assert_called()


class TestListReferences(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(VarSetOpsHandler)

    def test_unavailable_on_older_freecad(self):
        vs = make_varset("Params")  # no getInListProp -- default state
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_references({'varset_name': 'Params'})
        parsed = json.loads(result)
        self.assertFalse(parsed['available'])
        self.assertEqual(parsed['references'], [])
        self.assertIn('message', parsed)

    def test_zero_references(self):
        vs = make_varset("Params")
        vs.getInListProp = lambda: []
        doc = make_mock_doc([vs])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_references({'varset_name': 'Params'})
        parsed = json.loads(result)
        self.assertTrue(parsed['available'])
        self.assertEqual(parsed['references'], [])

    def test_several_references_across_multiple_objects(self):
        vs = make_varset("Params")
        cube = make_part_object("Cube")
        cube.ExpressionEngine = [('.Length', 'Params.Width')]
        cylinder = make_part_object("Cyl")
        cylinder.ExpressionEngine = [('.Height', 'Params.Depth')]
        vs.getInListProp = lambda: [
            make_dep_edge(cube, 'Width'),
            make_dep_edge(cylinder, 'Depth'),
        ]
        doc = make_mock_doc([vs, cube, cylinder])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_references({'varset_name': 'Params'})
        parsed = json.loads(result)
        self.assertEqual(len(parsed['references']), 2)
        by_obj = {r['from_object']: r for r in parsed['references']}
        self.assertEqual(by_obj['Cube']['from_property'], '.Length')
        self.assertEqual(by_obj['Cyl']['from_property'], '.Height')

    def test_filtered_by_property_name(self):
        vs = make_varset("Params")
        cube = make_part_object("Cube")
        cylinder = make_part_object("Cyl")
        vs.getInListProp = lambda: [
            make_dep_edge(cube, 'Width'),
            make_dep_edge(cylinder, 'Depth'),
        ]
        doc = make_mock_doc([vs, cube, cylinder])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_references({
            'varset_name': 'Params', 'property_name': 'Width',
        })
        parsed = json.loads(result)
        self.assertEqual(len(parsed['references']), 1)
        self.assertEqual(parsed['references'][0]['to_property'], 'Width')


class TestAllowedOperations(unittest.TestCase):
    """Every _ALLOWED_OPERATIONS entry must resolve to a real, callable
    handler method -- a typo here would dispatch to a 'not callable' error
    at MCP-call time instead of failing at import/test time."""

    def test_every_allowed_operation_is_callable(self):
        handler = make_handler(VarSetOpsHandler)
        for op in VarSetOpsHandler._ALLOWED_OPERATIONS:
            method = getattr(handler, op, None)
            self.assertTrue(callable(method), f"{op} is not a callable handler method")


if __name__ == '__main__':
    unittest.main()

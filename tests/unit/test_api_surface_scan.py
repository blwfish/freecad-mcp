"""Unit tests for tests/integration/_api_surface_scan.py -- the static AST
scanner that builds the (type, property) scope table for the API-surface
drift test. Pure Python, no FreeCAD needed.

Covers the ambiguous/threshold cases per this project's testing discipline:
branch-aware bindings (mutually-exclusive if/else must not collapse),
dynamic (non-literal) type strings dropped rather than guessed, nested defs
not inheriting enclosing bindings, and the known static-analysis limitation
where reassigning a tracked variable to a non-addObject/newObject call
leaves its old type binding stale (pinned, not silently left undefined).
"""

import ast
import textwrap

from tests.integration._api_surface_scan import _type_id_from_call, scan_type_properties


def _scan_source(tmp_path, source, filename="handler.py"):
    (tmp_path / filename).write_text(textwrap.dedent(source))
    return scan_type_properties(handlers_dir=str(tmp_path))


class TestTypeIdFromCall:
    def test_addobject_literal_type_string(self):
        node = ast.parse('doc.addObject("Part::Box", "Box")').body[0].value
        assert _type_id_from_call(node) == "Part::Box"

    def test_newobject_literal_type_string(self):
        node = ast.parse('body.newObject("PartDesign::Pad", "Pad")').body[0].value
        assert _type_id_from_call(node) == "PartDesign::Pad"

    def test_unrelated_method_call_ignored(self):
        node = ast.parse('doc.getObject("Box")').body[0].value
        assert _type_id_from_call(node) is None

    def test_dynamic_type_string_dropped_not_guessed(self):
        node = ast.parse('doc.addObject(type_var, "Box")').body[0].value
        assert _type_id_from_call(node) is None

    def test_literal_string_without_double_colon_ignored(self):
        node = ast.parse('doc.addObject("NotATypeString", "Box")').body[0].value
        assert _type_id_from_call(node) is None

    def test_call_with_no_positional_args_ignored(self):
        node = ast.parse("doc.addObject()").body[0].value
        assert _type_id_from_call(node) is None

    def test_non_call_node_returns_none(self):
        node = ast.parse('"Part::Box"').body[0].value
        assert _type_id_from_call(node) is None


class TestScanTypeProperties:
    def test_basic_property_assignment_recorded(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_box(doc):
                box = doc.addObject("Part::Box", "Box")
                box.Length = 10
                box.Width = 5
                return box
        """)
        assert table == {"Part::Box": {"Length", "Width"}}

    def test_type_with_zero_properties_set_appears_as_empty_set(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_group(doc):
                return doc.addObject("App::DocumentObjectGroup", "Group")
        """)
        assert table == {"App::DocumentObjectGroup": set()}

    def test_branch_aware_bindings_do_not_leak_across_if_else(self, tmp_path):
        # The fillet/chamfer/revolution Body-vs-standalone pattern: same
        # variable name reused across mutually-exclusive branches must
        # record each branch's own type's properties, never collapse onto
        # whichever branch's type was assigned last.
        table = _scan_source(tmp_path, """
            def make_revolution(doc, body, sketch, axis_vector):
                if body:
                    revolution = body.newObject("PartDesign::Revolution", "Rev")
                    revolution.Profile = sketch
                else:
                    revolution = doc.addObject("Part::Revolution", "Rev")
                    revolution.Axis = axis_vector
        """)
        assert table == {
            "PartDesign::Revolution": {"Profile"},
            "Part::Revolution": {"Axis"},
        }

    def test_dynamic_type_string_call_site_dropped_with_reason(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_dynamic(doc, type_name):
                obj = doc.addObject(type_name, "Obj")
                obj.SomeProp = 1
        """)
        assert table == {}

    def test_multiple_call_sites_same_type_union_properties(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_box_a(doc):
                box = doc.addObject("Part::Box", "A")
                box.Length = 1

            def make_box_b(doc):
                box = doc.addObject("Part::Box", "B")
                box.Width = 2
        """)
        assert table == {"Part::Box": {"Length", "Width"}}

    def test_try_except_both_branches_walked(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_it(doc):
                try:
                    obj = doc.addObject("Part::Box", "Box")
                    obj.Length = 1
                except Exception:
                    obj = doc.addObject("Part::Feature", "Fallback")
                    obj.Label = "x"
        """)
        assert table == {
            "Part::Box": {"Length"},
            "Part::Feature": {"Label"},
        }

    def test_for_loop_body_walked(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_many(doc, names):
                for name in names:
                    obj = doc.addObject("Part::Box", name)
                    obj.Length = 1
        """)
        assert table == {"Part::Box": {"Length"}}

    def test_with_block_body_walked(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_it(doc, lock):
                with lock:
                    obj = doc.addObject("Part::Box", "Box")
                    obj.Length = 1
        """)
        assert table == {"Part::Box": {"Length"}}

    def test_nested_def_does_not_inherit_enclosing_bindings(self, tmp_path):
        # ast.walk still reaches the nested FunctionDef directly and scans
        # its body with a *fresh* bindings dict -- it must not see outer's
        # var->type bindings just because a parameter happens to share a
        # name with an outer-scope variable.
        table = _scan_source(tmp_path, """
            def outer(doc):
                box = doc.addObject("Part::Box", "Box")

                def inner(box):
                    box.Length = 99

                inner(box)
        """)
        assert table == {"Part::Box": set()}

    def test_reassignment_to_non_addobject_call_leaves_stale_binding(self, tmp_path):
        # Known static-analysis limitation, pinned rather than silently
        # left undefined: reassigning a tracked variable to a different,
        # non-addObject/newObject call does not clear its old type
        # binding, so a later attribute assignment is (incorrectly) still
        # attributed to the original type. Not a crash -- pinned so a
        # future refactor changes this behavior deliberately, not by
        # accident.
        table = _scan_source(tmp_path, """
            def make_it(doc):
                obj = doc.addObject("Part::Box", "Box")
                obj = some_other_function()
                obj.NotReallyABoxProp = 1
        """)
        assert table == {"Part::Box": {"NotReallyABoxProp"}}

    def test_reassignment_to_new_addobject_call_rebinds_correctly(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_it(doc):
                obj = doc.addObject("Part::Box", "Box")
                obj.Length = 1
                obj = doc.addObject("Part::Sphere", "Sphere")
                obj.Radius = 2
        """)
        assert table == {
            "Part::Box": {"Length"},
            "Part::Sphere": {"Radius"},
        }

    def test_attribute_assignment_on_untracked_variable_ignored(self, tmp_path):
        # `self.foo = 1` or any attribute-set on a variable never bound to
        # a Type::String must be silently skipped, not raise/guess.
        table = _scan_source(tmp_path, """
            def make_it(self, doc):
                self.cache = {}
                obj = doc.addObject("Part::Box", "Box")
        """)
        assert table == {"Part::Box": set()}

    def test_syntax_error_file_skipped_not_raised(self, tmp_path):
        (tmp_path / "broken.py").write_text("def f(:\n")
        (tmp_path / "ok.py").write_text(
            'def make_box(doc):\n'
            '    box = doc.addObject("Part::Box", "Box")\n'
            '    box.Length = 1\n'
        )
        table = scan_type_properties(handlers_dir=str(tmp_path))
        assert table == {"Part::Box": {"Length"}}

    def test_empty_directory_returns_empty_table(self, tmp_path):
        table = scan_type_properties(handlers_dir=str(tmp_path))
        assert table == {}

    def test_return_of_addobject_call_recorded_with_zero_properties(self, tmp_path):
        # Not currently used anywhere in AICopilot/handlers, but a handler
        # could plausibly be refactored to `return doc.addObject(...)`
        # directly -- the type_id must still register (nothing captures a
        # name, so it can only ever have zero tracked properties).
        table = _scan_source(tmp_path, """
            def make_group(doc):
                return doc.addObject("App::DocumentObjectGroup", "Group")
        """)
        assert table == {"App::DocumentObjectGroup": set()}

    def test_bare_fire_and_forget_addobject_call_recorded(self, tmp_path):
        table = _scan_source(tmp_path, """
            def make_group(doc):
                doc.addObject("App::DocumentObjectGroup", "Group")
        """)
        assert table == {"App::DocumentObjectGroup": set()}

    def test_non_python_files_ignored(self, tmp_path):
        (tmp_path / "notes.txt").write_text('doc.addObject("Part::Box", "Box")')
        table = scan_type_properties(handlers_dir=str(tmp_path))
        assert table == {}


# ---------------------------------------------------------------------------
# Draft.make_*() factory-function recognition (full-review 2026-07-24
# finding #16, closed 2026-07-25 with live-verified TypeIds -- see
# _DRAFT_FACTORY_TYPES's docstring for the confirmed mapping).
# ---------------------------------------------------------------------------

class TestDraftFactoryRecognition:
    def test_make_clone_recorded_as_part_featurepython(self, tmp_path):
        table = _scan_source(tmp_path, """
            def clone(obj, doc):
                clone = Draft.make_clone(obj)
                clone.Placement.Base = FreeCAD.Vector(1, 2, 3)
        """)
        assert table == {"Part::FeaturePython": {"Placement"}}

    def test_make_text_recorded_as_app_featurepython(self, tmp_path):
        table = _scan_source(tmp_path, """
            def text(lines, doc):
                t = Draft.make_text(lines)
                t.Label = "Hello"
        """)
        assert table == {"App::FeaturePython": {"Label"}}

    def test_make_ortho_array_recorded(self, tmp_path):
        table = _scan_source(tmp_path, """
            def array(obj, doc):
                array = Draft.make_ortho_array(obj, n_x=2)
                array.Fuse = True
        """)
        assert table == {"Part::FeaturePython": {"Fuse"}}

    def test_non_draft_module_same_method_name_not_matched(self, tmp_path):
        """Only calls on a bare `Draft.` name are recognized -- a same-named
        method on some other object (e.g. a local helper also called
        make_clone) must not be misattributed to Draft's factory types."""
        table = _scan_source(tmp_path, """
            def clone(obj, doc):
                clone = SomeOtherModule.make_clone(obj)
                clone.Whatever = 1
        """)
        assert table == {}

    def test_unrecognized_draft_function_dropped_not_guessed(self, tmp_path):
        """A real Draft.* call the scanner has no confirmed mapping for is
        dropped, same as any other unresolvable call -- not guessed at."""
        table = _scan_source(tmp_path, """
            def something(obj, doc):
                thing = Draft.make_something_unmapped(obj)
                thing.Prop = 1
        """)
        assert table == {}

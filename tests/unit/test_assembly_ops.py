"""Unit tests for AssemblyOpsHandler (Assembly workbench).

Phase 1 covers container + reference-geometry + component-linking.
Coverage focus: create_assembly (and the Type="Assembly" side effect that
Phase 2's joint/grounding code depends on), create_lcs (Part::
LocalCoordinateSystem attach mechanics, mirroring create_datum_plane but
without forcing a PartDesign Body), add_component (App::Link vs
Assembly::AssemblyLink branch, same- and cross-document linking, label
resolution), and list_components.

Phase 2 covers joints, grounding, and solve. Coverage focus: create_joint
(joint_type closed-vocabulary rejection, default vertex == element,
orphan-object cleanup on setJointConnectors failure -- same shape as
Phase 1's add_component bug), ground_part (orphan cleanup on
GroundedJoint failure), solve (int-code -> named-status mapping,
including an unrecognized code), and list_joints (GroundedJoint vs
regular-Joint disambiguation -- MagicMock auto-vivifies any attribute
access truthy, so `hasattr(mock, 'ObjectToGround')` is always True unless
explicitly deleted; tests below do that explicitly for "regular joint"
cases, mirroring the same footgun already handled for isDerivedFrom in
the Phase 1 add_component tests).
"""

import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_UtilsAssembly,
    mock_JointObject,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    make_body,
    make_assembly,
    assert_error_contains,
    assert_success_contains,
    _Placement,
    _Vec,
)

from handlers.assembly_ops import AssemblyOpsHandler, _JOINT_TYPES


def _make_joint_group(name="Joints"):
    """Mock Assembly::JointGroup. newObject appends fresh joint mocks to .Group,
    mirroring make_assembly's newObject side effect."""
    jg = MagicMock()
    jg.Name = name
    jg.TypeId = "Assembly::JointGroup"
    jg.Group = []

    def _new_object(type_id, obj_name=None):
        obj = MagicMock()
        obj.Name = obj_name or f"{type_id}_auto"
        obj.TypeId = type_id
        jg.Group.append(obj)
        return obj

    jg.newObject = MagicMock(side_effect=_new_object)
    return jg


def _set_joints(assembly, joints):
    """Wire a mock Assembly::JointGroup containing `joints` into
    assembly.OutList -- list_joints() finds the group by scanning OutList
    for TypeId=="Assembly::JointGroup" and reads its .Group, not
    assembly.Joints (which never includes GroundedJoint objects; fixed
    2026-08-21). Mirrors the shape create_joint/ground_part's real
    UtilsAssembly.getJointGroup(assembly) call produces."""
    jg = _make_joint_group()
    jg.Group = list(joints)
    assembly.OutList = [jg]
    return jg


# ---------------------------------------------------------------------------
# create_assembly
# ---------------------------------------------------------------------------

class TestCreateAssembly(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_creates_with_default_name(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_assembly({})
        assert_success_contains(self, result, "Assembly")

    def test_creates_with_custom_name(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_assembly({"name": "MyAsm"})
        assert_success_contains(self, result, "MyAsm")

    def test_sets_type_assembly(self):
        """Not automatic in FreeCAD itself (CommandCreateAssembly.py sets it
        by hand). Phase 2's joint/grounding code branches on
        assembly.Type == "Assembly" — skipping this silently breaks that,
        so it needs its own explicit assertion, not just "object exists"."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_assembly({"name": "MyAsm"})
        created = doc.getObject("MyAsm")
        self.assertEqual(created.Type, "Assembly")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.create_assembly({})
        assert_error_contains(self, result, "No active document")

    def test_addobject_exception(self):
        doc = make_mock_doc([])
        doc.addObject = MagicMock(side_effect=RuntimeError("boom"))
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_assembly({"name": "Fail"})
        assert_error_contains(self, result, "Error", "boom")


# ---------------------------------------------------------------------------
# create_lcs
# ---------------------------------------------------------------------------

class TestCreateLCS(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.create_lcs({})
        assert_error_contains(self, result, "No active document")

    def test_bare_document_creation(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({"name": "LCS1"})
        assert_success_contains(self, result, "LCS1")
        doc.addObject.assert_called_once_with("Part::LocalCoordinateSystem", "LCS1")

    def test_container_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({"name": "LCS1", "container_name": "Ghost"})
        assert_error_contains(self, result, "Container not found")

    def test_nested_in_container_not_forced_into_body(self):
        """create_lcs must NOT go through create_body_if_needed — a bare
        Part::Feature container should work directly via its own
        newObject(), with no PartDesign Body created as a side effect."""
        body = make_body("Body")
        doc = make_mock_doc([body])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({"name": "LCS1", "container_name": "Body"})
        assert_success_contains(self, result, "Created LCS")
        body.newObject.assert_called_once_with("Part::LocalCoordinateSystem", "LCS1")
        doc.addObject.assert_not_called()

    def test_map_mode_and_reference_applied(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_lcs({
            "name": "LCS1", "map_mode": "FlatFace",
            "reference": "Face3", "reference_object": "B",
        })
        lcs = doc.getObject("LCS1")
        self.assertEqual(lcs.MapMode, "FlatFace")
        self.assertEqual(lcs.AttachmentSupport, [(box, "Face3")])

    def test_reference_without_reference_object_is_rejected(self):
        """reference given but reference_object omitted entirely -- full-
        review 2026-07-24 finding #05: this used to silently apply MapMode
        with AttachmentSupport left unset and report success. Now an
        explicit, distinct error, and no LCS object is created at all."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({
            "name": "LCS1", "map_mode": "FlatFace", "reference": "Face3",
        })
        assert_error_contains(self, result, "reference_object", "required")
        doc.addObject.assert_not_called()

    def test_reference_with_unresolvable_reference_object_is_rejected(self):
        """reference_object given but doesn't resolve to a real object --
        a distinct code path from the omitted case above (finding #17: the
        two used to be conflated, with only the omitted-case pinned by a
        test). Also rejected before anything is created, with an error
        naming the unresolved object."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({
            "name": "LCS1", "map_mode": "FlatFace",
            "reference": "Face3", "reference_object": "GhostObject",
        })
        assert_error_contains(self, result, "GhostObject")
        doc.addObject.assert_not_called()

    def test_offset_applied(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.create_lcs({
            "name": "LCS1", "offset_x": 10, "offset_y": 5, "offset_z": 2,
        })
        lcs = doc.getObject("LCS1")
        self.assertEqual(lcs.AttachmentOffset.Base.x, 10)
        self.assertEqual(lcs.AttachmentOffset.Base.y, 5)
        self.assertEqual(lcs.AttachmentOffset.Base.z, 2)

    def test_property_assignment_failure_cleans_up_orphan(self):
        """finding #18: create_lcs previously had no exception cleanup
        around MapMode/AttachmentSupport/AttachmentOffset assignment, unlike
        create_joint/ground_part in the same file -- an exception there used
        to leave a half-configured LCS object in the document."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc

        def _raise_on_map_mode_set(value):
            raise ValueError("bad enum value")

        created = {}

        def _add_object(type_id, name):
            obj = MagicMock()
            obj.Name = name
            type(obj).MapMode = property(lambda self: None, lambda self, v: _raise_on_map_mode_set(v))
            created['obj'] = obj
            return obj

        doc.addObject = MagicMock(side_effect=_add_object)

        result = self.handler.create_lcs({"name": "LCS1", "map_mode": "FlatFace"})

        assert_error_contains(self, result, "bad enum value")
        doc.removeObject.assert_called_once_with("LCS1")

    def test_no_offset_no_crash(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({"name": "LCS1"})
        assert_success_contains(self, result, "LCS1")


# ---------------------------------------------------------------------------
# add_component
# ---------------------------------------------------------------------------

class TestAddComponent(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_missing_object_name(self):
        result = self.handler.add_component({})
        assert_error_contains(self, result, "object_name")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.add_component({"object_name": "Box"})
        assert_error_contains(self, result, "No active document")

    def test_object_not_found(self):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_component({"object_name": "Ghost"})
        assert_error_contains(self, result, "not found")

    def test_no_assembly_in_document(self):
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_component({"object_name": "Box"})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_assembly_name_not_found(self):
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.add_component({
            "object_name": "Box", "assembly_name": "Ghost",
        })
        assert_error_contains(self, result, "Assembly not found")

    def test_source_doc_not_open(self):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        mock_FreeCAD.listDocuments = MagicMock(return_value={"ActiveDoc": doc})
        result = self.handler.add_component({
            "object_name": "Box", "source_doc": "ClosedDoc",
        })
        assert_error_contains(self, result, "not open")

    def test_object_not_found_in_source_doc(self):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        doc.FileName = "/tmp/owner.FCStd"
        mock_FreeCAD.ActiveDocument = doc
        src_doc = make_mock_doc([], name="OtherDoc")
        src_doc.FileName = "/tmp/other.FCStd"
        mock_FreeCAD.listDocuments = MagicMock(return_value={"OtherDoc": src_doc})
        mock_FreeCAD.getDocument = MagicMock(return_value=src_doc)
        result = self.handler.add_component({
            "object_name": "Missing", "source_doc": "OtherDoc",
        })
        assert_error_contains(self, result, "not found", "OtherDoc")

    def test_source_doc_not_saved(self):
        """FreeCAD's PropertyXLink refuses to link into a document with no
        file path on disk (App/PropertyLinks.cpp: "Linked document not
        saved") -- confirmed live against real FreeCAD 2026-07-24: without
        this precondition check, assembly.newObject() already ran and left
        an orphan, unlinked App::Link in the assembly's Group by the time
        LinkedObject's assignment threw. Must be checked BEFORE creating
        anything, not caught-and-cleaned-up after."""
        box = make_box_object("Box")
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        src_doc = make_mock_doc([box], name="OtherDoc")
        src_doc.FileName = ""  # never saved
        mock_FreeCAD.listDocuments = MagicMock(return_value={"OtherDoc": src_doc})
        mock_FreeCAD.getDocument = MagicMock(return_value=src_doc)

        result = self.handler.add_component({
            "object_name": "Box", "source_doc": "OtherDoc",
        })

        assert_error_contains(self, result, "never been saved")
        assembly.newObject.assert_not_called()

    def test_plain_part_becomes_app_link(self):
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        assembly = make_assembly("Asm")
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_component({"object_name": "Box"})

        assert_success_contains(self, result, "App::Link")
        assembly.newObject.assert_called_once_with("App::Link", "Box")
        link = assembly.Group[-1]
        self.assertIs(link.LinkedObject, box)

    def test_subassembly_becomes_assembly_link(self):
        """A nested sub-assembly (itself an Assembly::AssemblyObject) must
        become an Assembly::AssemblyLink, not a plain App::Link — mirrors
        FreeCAD's own Assembly_InsertLink command."""
        sub = make_assembly("SubAsm")
        top = make_assembly("Top")
        doc = make_mock_doc([sub, top])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_component({
            "object_name": "SubAsm", "assembly_name": "Top",
        })

        assert_success_contains(self, result, "Assembly::AssemblyLink")
        top.newObject.assert_called_once_with("Assembly::AssemblyLink", "SubAsm")

    def test_cross_document_link(self):
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        src_doc = make_mock_doc([box], name="OtherDoc")
        src_doc.FileName = "/tmp/other.FCStd"
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        doc.FileName = "/tmp/owner.FCStd"
        mock_FreeCAD.ActiveDocument = doc
        mock_FreeCAD.listDocuments = MagicMock(return_value={"OtherDoc": src_doc})
        mock_FreeCAD.getDocument = MagicMock(return_value=src_doc)

        result = self.handler.add_component({
            "object_name": "Box", "source_doc": "OtherDoc",
        })

        assert_success_contains(self, result, "OtherDoc")
        link = assembly.Group[-1]
        self.assertIs(link.LinkedObject, box)

    def test_owner_doc_not_saved(self):
        """FreeCAD's PropertyXLink needs a file path on BOTH ends to compute
        a relative path (App/PropertyLinks.cpp: "Owner document not saved")
        -- confirmed live 2026-07-24 as a second, independent precondition
        from the source-doc-not-saved case. Must also be checked before
        creating anything."""
        box = make_box_object("Box")
        src_doc = make_mock_doc([box], name="OtherDoc")
        src_doc.FileName = "/tmp/other.FCStd"
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        doc.FileName = ""  # owner/active document never saved
        mock_FreeCAD.ActiveDocument = doc
        mock_FreeCAD.listDocuments = MagicMock(return_value={"OtherDoc": src_doc})
        mock_FreeCAD.getDocument = MagicMock(return_value=src_doc)

        result = self.handler.add_component({
            "object_name": "Box", "source_doc": "OtherDoc",
        })

        assert_error_contains(self, result, "never been saved")
        assembly.newObject.assert_not_called()

    def test_placement_offset_applied(self):
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        assembly = make_assembly("Asm")
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.add_component({
            "object_name": "Box", "x": 50, "y": 0, "z": 25,
        })

        link = assembly.Group[-1]
        self.assertEqual(link.Placement.Base.x, 50)
        self.assertEqual(link.Placement.Base.z, 25)

    def test_resolves_object_by_label(self):
        box = make_box_object("Cylinder001")
        box.Label = "MyCylinder"
        box.isDerivedFrom = MagicMock(return_value=False)
        assembly = make_assembly("Asm")
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.add_component({"object_name": "MyCylinder"})

        self.assertNotIn("not found", result.lower())
        link = assembly.Group[-1]
        self.assertIs(link.LinkedObject, box)

    def test_link_assignment_failure_cleans_up_orphan(self):
        """finding #06: add_component had no exception cleanup around
        `link.LinkedObject = src_obj` for any cause OTHER than the specific
        doc-not-saved precondition already checked earlier -- any other
        failure used to leave an orphan, unlinked App::Link/
        Assembly::AssemblyLink sitting in the assembly's Group."""
        box = make_box_object("Box")
        box.isDerivedFrom = MagicMock(return_value=False)
        assembly = make_assembly("Asm")
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        created_link = {}

        def _new_object(type_id, link_name=None):
            link = MagicMock()
            link.Name = link_name or f"{type_id}_auto"

            def _raise(self, v):
                raise RuntimeError("assignment failed")

            type(link).LinkedObject = property(lambda self: None, _raise)
            assembly.Group.append(link)
            created_link['link'] = link
            return link

        assembly.newObject = MagicMock(side_effect=_new_object)

        result = self.handler.add_component({"object_name": "Box"})

        assert_error_contains(self, result, "assignment failed")
        doc.removeObject.assert_called_once_with(created_link['link'].Name)


# ---------------------------------------------------------------------------
# list_components
# ---------------------------------------------------------------------------

class TestListComponents(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.list_components({})
        assert_error_contains(self, result, "No active document")

    def test_assembly_name_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_components({"assembly_name": "Ghost"})
        assert_error_contains(self, result, "Assembly not found")

    def test_no_assembly_in_document(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_components({})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_empty_assembly(self):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_components({})
        assert_success_contains(self, result, "no components")

    def test_lists_components_with_linked_object(self):
        linked = make_part_object("Box")
        link = MagicMock()
        link.Name = "Box_Link"
        link.TypeId = "App::Link"
        link.LinkedObject = linked
        assembly = make_assembly("Asm", group=[link])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({})

        assert_success_contains(self, result, "Box_Link", "App::Link", "-> Box")

    def test_lists_components_without_linked_object(self):
        """A group member with no LinkedObject (degenerate/ambiguous case)
        must not crash — LinkedObject is explicitly None here since a bare
        MagicMock() attribute would auto-vivify truthy and mask this path."""
        bare = MagicMock()
        bare.Name = "Loose"
        bare.TypeId = "Part::Feature"
        bare.LinkedObject = None
        assembly = make_assembly("Asm", group=[bare])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({})

        assert_success_contains(self, result, "Loose")
        self.assertNotIn("->", result.split("Loose")[1].split("\n")[0])

    def test_pagination_limits_returned_components(self):
        """finding #08: list_components had no pagination at all, unlike
        document_ops.list_objects -- this repo already crashed once
        (SIGPIPE/exit 141) on an unbounded object listing over the bridge's
        50 KiB message cap."""
        links = []
        for i in range(5):
            link = MagicMock()
            link.Name = f"Link{i}"
            link.TypeId = "App::Link"
            link.LinkedObject = None
            links.append(link)
        assembly = make_assembly("Asm", group=links)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({"limit": 2})

        assert_success_contains(self, result, "5 total", "Link0", "Link1")
        self.assertNotIn("Link2", result)
        assert_success_contains(self, result, "3 more")

    def test_pagination_offset_skips_components(self):
        links = []
        for i in range(3):
            link = MagicMock()
            link.Name = f"Link{i}"
            link.TypeId = "App::Link"
            link.LinkedObject = None
            links.append(link)
        assembly = make_assembly("Asm", group=links)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({"offset": 2})

        self.assertNotIn("Link0", result)
        self.assertNotIn("Link1", result)
        assert_success_contains(self, result, "Link2")

    def test_negative_limit_and_offset_clamp_to_zero(self):
        link = MagicMock()
        link.Name = "Link0"
        link.TypeId = "App::Link"
        link.LinkedObject = None
        assembly = make_assembly("Asm", group=[link])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({"limit": -5, "offset": -5})

        assert_success_contains(self, result, "1 total")
        self.assertNotIn("Link0", result)

    def test_one_malformed_component_does_not_drop_the_rest(self):
        """finding #22: a single outer except around the whole loop used to
        make one bad component drop the entire list."""
        good = MagicMock()
        good.Name = "Good"
        good.TypeId = "App::Link"
        good.LinkedObject = None

        bad = MagicMock()
        type(bad).Name = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))
        bad.TypeId = "App::Link"

        assembly = make_assembly("Asm", group=[bad, good])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_components({})

        assert_success_contains(self, result, "Good", "failed to introspect")


# ---------------------------------------------------------------------------
# create_joint
# ---------------------------------------------------------------------------

class TestCreateJoint(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def _setup(self, doc_objects=None):
        """doc_objects are added to the document AND to assembly.Group --
        i.e. they're already-added components (as if add_component had run),
        which is what create_joint now requires of both ref objects."""
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly] + (doc_objects or []))
        assembly.Group.extend(doc_objects or [])
        mock_FreeCAD.ActiveDocument = doc
        joint_group = _make_joint_group()
        mock_UtilsAssembly.getJointGroup = MagicMock(return_value=joint_group)
        mock_JointObject.Joint = MagicMock()
        return doc, assembly, joint_group

    def test_unknown_joint_type(self):
        self._setup()
        result = self.handler.create_joint({
            "joint_type": "Bogus", "ref1_object": "A", "ref2_object": "B",
        })
        assert_error_contains(self, result, "Unknown joint_type")

    def test_missing_ref_objects(self):
        self._setup()
        result = self.handler.create_joint({"joint_type": "Fixed"})
        assert_error_contains(self, result, "ref1_object", "ref2_object")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
        })
        assert_error_contains(self, result, "No active document")

    def test_no_assembly_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
        })
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_ref1_object_not_found(self):
        self._setup()
        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "Ghost", "ref2_object": "B",
        })
        assert_error_contains(self, result, "not found", "Ghost")

    def test_ref2_object_not_found(self):
        box = make_box_object("A")
        self._setup(doc_objects=[box])
        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "Ghost",
        })
        assert_error_contains(self, result, "not found", "Ghost")

    def test_ref_not_a_component_rejected(self):
        """FreeCAD's own solve-status checks and solver only recognize
        actual assembly components -- confirmed live 2026-07-24 that
        jointing a bare document-root object used to silently succeed while
        producing a state get_part_status couldn't see. Reject it up front
        instead: box_a exists in the document but was never added to
        assembly.Group (i.e. never add_component'd)."""
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        # _require_component now takes an exempt_types param (LCS refs bypass
        # the check) and checks it via isDerivedFrom -- a bare MagicMock's
        # isDerivedFrom auto-vivifies truthy for ANY argument, so a plain
        # box must explicitly say it's not an LCS, same convention already
        # used elsewhere in this file for the Assembly::AssemblyLink branch.
        box_a.isDerivedFrom = MagicMock(return_value=False)
        box_b.isDerivedFrom = MagicMock(return_value=False)
        doc = make_mock_doc([box_a, box_b])
        assembly = make_assembly("Asm")
        # Only box_b is a component -- box_a is a bare document object.
        assembly.Group.append(box_b)
        doc.Objects.append(assembly)
        mock_FreeCAD.ActiveDocument = doc
        joint_group = _make_joint_group()
        mock_UtilsAssembly.getJointGroup = MagicMock(return_value=joint_group)
        mock_JointObject.Joint = MagicMock()

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
        })

        assert_error_contains(self, result, "A", "not a component", "add_component")
        joint_group.newObject.assert_not_called()

    def test_ref_is_lcs_bypasses_component_check(self):
        """finding #09: create_lcs's own docstring documents an LCS as a
        valid bare-document joint mating reference that never needs
        add_component -- create_joint's _require_component must exempt it,
        not reject it uniformly with plain parts."""
        lcs = MagicMock()
        lcs.Name = "LCS1"
        lcs.isDerivedFrom = MagicMock(
            side_effect=lambda t: t == "App::LocalCoordinateSystem")
        box_b = make_box_object("B")
        box_b.isDerivedFrom = MagicMock(return_value=False)
        doc = make_mock_doc([lcs, box_b])
        assembly = make_assembly("Asm")
        assembly.Group.append(box_b)  # LCS1 deliberately NOT added as a component
        doc.Objects.append(assembly)
        mock_FreeCAD.ActiveDocument = doc
        joint_group = _make_joint_group()
        mock_UtilsAssembly.getJointGroup = MagicMock(return_value=joint_group)
        mock_JointObject.Joint = MagicMock()

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "LCS1", "ref2_object": "B",
        })

        assert_success_contains(self, result, "Fixed")
        self.assertNotIn("not a component", result.lower())

    def test_invalid_ref1_element_rejected_before_creating_anything(self):
        """FreeCAD's own joint machinery does NOT validate element names --
        confirmed live 2026-07-24 that a nonexistent 'Face99' on a 6-face
        box is silently accepted and reports 'Created ... joint' with no
        error. _validate_element catches this before anything gets built."""
        box_a = make_box_object("A")  # default: 6 faces
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref1_element": "Face99",
        })

        assert_error_contains(self, result, "Face99", "does not exist", "A")
        joint_group.newObject.assert_not_called()

    def test_invalid_ref2_element_rejected(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref2_element": "Edge99",
        })

        assert_error_contains(self, result, "Edge99", "does not exist", "B")
        joint_group.newObject.assert_not_called()

    def test_invalid_ref1_vertex_rejected(self):
        """finding #07: _validate_element was applied to ref1_element/
        ref2_element but never to ref1_vertex/ref2_vertex -- an explicit,
        out-of-range vertex disambiguator used to pass through unvalidated
        to setJointConnectors."""
        box_a = make_box_object("A")  # default: 8 vertices
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref1_element": "Face1", "ref1_vertex": "Vertex99",
        })

        assert_error_contains(self, result, "Vertex99", "does not exist", "A")
        joint_group.newObject.assert_not_called()

    def test_invalid_ref2_vertex_rejected(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")  # default: 8 vertices
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref2_element": "Face1", "ref2_vertex": "Vertex99",
        })

        assert_error_contains(self, result, "Vertex99", "does not exist", "B")
        joint_group.newObject.assert_not_called()

    def test_creates_joint_with_correct_type_index(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        result = self.handler.create_joint({
            "joint_type": "Revolute", "ref1_object": "A", "ref2_object": "B",
            "ref1_element": "Face1", "ref2_element": "Face2",
        })

        assert_success_contains(self, result, "Revolute", "A.Face1", "B.Face2")
        joint_group.newObject.assert_called_once_with("App::FeaturePython", "RevoluteJoint")
        joint = joint_group.Group[-1]
        mock_JointObject.Joint.assert_called_once_with(joint, _JOINT_TYPES.index("Revolute"))

    def test_default_vertex_equals_element(self):
        """Passing the same string for element and vertex is FreeCAD's own
        convention for 'use this element's own center' (confirmed against
        UtilsAssembly.findPlacement) -- the natural default when the caller
        doesn't care about a specific disambiguating vertex."""
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref1_element": "Face1", "ref2_element": "Face2",
        })

        joint = joint_group.Group[-1]
        joint.Proxy.setJointConnectors.assert_called_once_with(
            joint, [[box_a, ["Face1", "Face1"]], [box_b, ["Face2", "Face2"]]]
        )

    def test_explicit_vertex_overrides_default(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
            "ref1_element": "Face1", "ref1_vertex": "Vertex3",
            "ref2_element": "Face2", "ref2_vertex": "Vertex4",
        })

        joint = joint_group.Group[-1]
        joint.Proxy.setJointConnectors.assert_called_once_with(
            joint, [[box_a, ["Face1", "Vertex3"]], [box_b, ["Face2", "Vertex4"]]]
        )

    def test_distance_set_when_provided(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        self.handler.create_joint({
            "joint_type": "Distance", "ref1_object": "A", "ref2_object": "B",
            "distance": 25,
        })

        joint = joint_group.Group[-1]
        self.assertEqual(joint.Distance, 25)

    def test_distance_not_touched_when_omitted(self):
        """Pinning behavior: omitting distance must not set it to a default
        like 0 -- the property is simply left alone (whatever Joint's own
        constructor initialized it to)."""
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])
        # Sentinel: if the handler wrote to .Distance, this would be replaced.
        sentinel = object()

        def _joint_ctor(joint, idx):
            joint.Distance = sentinel

        mock_JointObject.Joint = MagicMock(side_effect=_joint_ctor)

        self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
        })

        joint = joint_group.Group[-1]
        self.assertIs(joint.Distance, sentinel)

    def test_cleanup_on_setjointconnectors_failure(self):
        """setJointConnectors can throw after the joint object already
        exists in the document -- same orphan-object shape as Phase 1's
        add_component bug. The joint must be removed, not left behind."""
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        doc, assembly, joint_group = self._setup(doc_objects=[box_a, box_b])

        def _joint_ctor(joint, idx):
            joint.Proxy.setJointConnectors = MagicMock(
                side_effect=RuntimeError("bad reference")
            )

        mock_JointObject.Joint = MagicMock(side_effect=_joint_ctor)

        result = self.handler.create_joint({
            "joint_type": "Fixed", "ref1_object": "A", "ref2_object": "B",
        })

        assert_error_contains(self, result, "Error creating joint", "bad reference")
        joint = joint_group.Group[-1]
        doc.removeObject.assert_called_once_with(joint.Name)


# ---------------------------------------------------------------------------
# ground_part
# ---------------------------------------------------------------------------

class TestGroundPart(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def _setup(self, doc_objects=None):
        """doc_objects are added to the document AND to assembly.Group --
        i.e. they're already-added components, which ground_part now
        requires of its target."""
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly] + (doc_objects or []))
        assembly.Group.extend(doc_objects or [])
        mock_FreeCAD.ActiveDocument = doc
        joint_group = _make_joint_group()
        mock_UtilsAssembly.getJointGroup = MagicMock(return_value=joint_group)
        mock_JointObject.GroundedJoint = MagicMock()
        return doc, assembly, joint_group

    def test_missing_object_name(self):
        self._setup()
        result = self.handler.ground_part({})
        assert_error_contains(self, result, "object_name")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.ground_part({"object_name": "Box"})
        assert_error_contains(self, result, "No active document")

    def test_object_not_found(self):
        self._setup()
        result = self.handler.ground_part({"object_name": "Ghost"})
        assert_error_contains(self, result, "not found", "Ghost")

    def test_no_assembly_found(self):
        box = make_box_object("Box")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.ground_part({"object_name": "Box"})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_object_not_a_component_rejected(self):
        """Grounding a bare document-root object (never add_component'd)
        used to silently succeed while producing a state get_part_status
        couldn't see -- confirmed live 2026-07-24. Now rejected up front."""
        box = make_box_object("Box")
        assembly = make_assembly("Asm")  # empty Group -- box was never added
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc
        joint_group = _make_joint_group()
        mock_UtilsAssembly.getJointGroup = MagicMock(return_value=joint_group)

        result = self.handler.ground_part({"object_name": "Box"})

        assert_error_contains(self, result, "Box", "not a component", "add_component")
        joint_group.newObject.assert_not_called()

    def test_grounds_successfully_with_default_name(self):
        box = make_box_object("Box")
        doc, assembly, joint_group = self._setup(doc_objects=[box])

        result = self.handler.ground_part({"object_name": "Box"})

        assert_success_contains(self, result, "Grounded", "Box")
        joint_group.newObject.assert_called_once_with("App::FeaturePython", "Box_Ground")
        ground = joint_group.Group[-1]
        mock_JointObject.GroundedJoint.assert_called_once_with(ground, box)

    def test_custom_name(self):
        box = make_box_object("Box")
        doc, assembly, joint_group = self._setup(doc_objects=[box])

        self.handler.ground_part({"object_name": "Box", "name": "MyGround"})

        joint_group.newObject.assert_called_once_with("App::FeaturePython", "MyGround")

    def test_cleanup_on_groundedjoint_failure(self):
        box = make_box_object("Box")
        doc, assembly, joint_group = self._setup(doc_objects=[box])
        mock_JointObject.GroundedJoint = MagicMock(side_effect=RuntimeError("boom"))

        result = self.handler.ground_part({"object_name": "Box"})

        assert_error_contains(self, result, "Error grounding part", "boom")
        ground = joint_group.Group[-1]
        doc.removeObject.assert_called_once_with(ground.Name)


# ---------------------------------------------------------------------------
# solve
# ---------------------------------------------------------------------------

class TestSolve(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.solve({})
        assert_error_contains(self, result, "No active document")

    def test_no_assembly_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.solve({})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_success_code(self):
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=0)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.solve({})
        assert_success_contains(self, result, "success", "code=0")

    def test_no_grounded_parts_code(self):
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=-6)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.solve({})
        assert_success_contains(self, result, "no_grounded_parts", "code=-6")

    def test_solver_error_code(self):
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=-1)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.solve({})
        assert_success_contains(self, result, "solver_error", "code=-1")

    def test_unrecognized_code_does_not_crash(self):
        """Ambiguous input: a code outside the documented -6..0 range (e.g.
        from a future FreeCAD version) must not KeyError -- it should be
        reported explicitly as unknown, not silently mapped to something
        misleading."""
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=-99)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.solve({})
        assert_success_contains(self, result, "unknown_code_-99")

    def test_enable_undo_defaults_false(self):
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=0)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.solve({})
        assembly.solve.assert_called_once_with(False)

    def test_enable_undo_passed_through(self):
        assembly = make_assembly("Asm")
        assembly.solve = MagicMock(return_value=0)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.solve({"enable_undo": True})
        assembly.solve.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# list_joints
# ---------------------------------------------------------------------------

class TestListJoints(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.list_joints({})
        assert_error_contains(self, result, "No active document")

    def test_no_assembly_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_joints({})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_empty_joints(self):
        assembly = make_assembly("Asm")
        _set_joints(assembly, [])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_joints({})
        assert_success_contains(self, result, "no joints")

    def test_lists_regular_joint(self):
        box_a = make_box_object("A")
        box_b = make_box_object("B")
        joint = MagicMock()
        joint.Name = "FixedJoint"
        joint.JointType = "Fixed"
        joint.Reference1 = [box_a, ["Face1", "Face1"]]
        joint.Reference2 = [box_b, ["Face2", "Face2"]]
        # MagicMock auto-vivifies ANY attribute access truthy -- without
        # this, `hasattr(joint, 'ObjectToGround')` in list_joints would be
        # True and misclassify a regular joint as a grounding.
        del joint.ObjectToGround

        assembly = make_assembly("Asm")
        _set_joints(assembly, [joint])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "FixedJoint", "Fixed", "A.Face1", "B.Face2")
        self.assertNotIn("Grounded", result)

    def test_lists_grounded_joint_distinctly(self):
        box = make_box_object("Box")
        ground = MagicMock()
        ground.Name = "Box_Ground"
        ground.ObjectToGround = box

        assembly = make_assembly("Asm")
        _set_joints(assembly, [ground])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "Box_Ground", "Grounded", "Box")

    def test_unset_reference_shown_as_unset(self):
        joint = MagicMock()
        joint.Name = "PartialJoint"
        joint.JointType = "Fixed"
        joint.Reference1 = None
        joint.Reference2 = None
        del joint.ObjectToGround

        assembly = make_assembly("Asm")
        _set_joints(assembly, [joint])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "(unset)")

    def test_pagination_limits_returned_joints(self):
        """finding #08: list_joints had no pagination, same class of gap
        list_components had."""
        joints = []
        for i in range(4):
            j = MagicMock()
            j.Name = f"Joint{i}"
            j.JointType = "Fixed"
            j.Reference1 = None
            j.Reference2 = None
            del j.ObjectToGround
            joints.append(j)
        assembly = make_assembly("Asm")
        _set_joints(assembly, joints)
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({"limit": 2})

        assert_success_contains(self, result, "4 total", "Joint0", "Joint1", "2 more")
        self.assertNotIn("Joint2", result)

    def test_one_malformed_joint_does_not_drop_the_rest(self):
        """finding #22: list_joints wrapped its whole per-joint loop in one
        outer except -- one bad joint used to drop every other joint too.
        FreeCAD's own joint machinery is documented elsewhere in this file
        (_validate_element) to silently accept dangling references, so a
        joint that fails to introspect is a real, not hypothetical, case."""
        good = MagicMock()
        good.Name = "GoodJoint"
        good.JointType = "Fixed"
        good.Reference1 = None
        good.Reference2 = None
        del good.ObjectToGround

        bad = MagicMock()
        del bad.ObjectToGround
        type(bad).JointType = property(lambda self: (_ for _ in ()).throw(RuntimeError("dangling reference")))
        bad.Name = "BadJoint"

        assembly = make_assembly("Asm")
        _set_joints(assembly, [bad, good])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "GoodJoint", "failed to introspect")

    def test_distance_joint_shows_distance(self):
        joint = MagicMock()
        joint.Name = "DistJoint"
        joint.JointType = "Distance"
        joint.Distance = 15.5
        joint.Reference1 = None
        joint.Reference2 = None
        del joint.ObjectToGround

        assembly = make_assembly("Asm")
        _set_joints(assembly, [joint])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "Distance=15.5")

    def test_fixed_joint_does_not_show_distance(self):
        """finding #23, precision check: Distance/Distance2/Angle are gated
        on joint type (per create_joint's own docstring) -- a Fixed joint
        must not show a meaningless default Distance value."""
        joint = MagicMock()
        joint.Name = "FixedJoint"
        joint.JointType = "Fixed"
        joint.Reference1 = None
        joint.Reference2 = None
        del joint.ObjectToGround

        assembly = make_assembly("Asm")
        _set_joints(assembly, [joint])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        self.assertNotIn("Distance", result)

    def test_shows_offset_detach_and_limits(self):
        joint = MagicMock()
        joint.Name = "SliderJoint"
        joint.JointType = "Slider"
        joint.Reference1 = None
        joint.Reference2 = None
        del joint.ObjectToGround
        joint.Detach1 = True
        joint.Detach2 = False
        joint.EnableLengthMin = True
        joint.LengthMin = 5.0
        joint.EnableLengthMax = False

        assembly = make_assembly("Asm")
        _set_joints(assembly, [joint])
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "Detach1=True", "LengthMin=5.0")
        self.assertNotIn("Detach2", result)
        self.assertNotIn("LengthMax", result)


# ---------------------------------------------------------------------------
# _validate_element -- threshold coverage (at/below/above the boundary),
# per this repo's Threshold-Boundary Testing Rule.
# ---------------------------------------------------------------------------

class TestValidateElement(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def _box(self):
        # make_box_object defaults: 6 faces, 12 edges, 8 vertices.
        return make_box_object("Box")

    def test_valid_face_mid_range(self):
        self.assertIsNone(self.handler._validate_element(self._box(), "Face3"))

    def test_face_at_upper_boundary_valid(self):
        """Face6 on a 6-face box is the last valid index -- not rejected."""
        self.assertIsNone(self.handler._validate_element(self._box(), "Face6"))

    def test_face_one_over_upper_boundary_rejected(self):
        result = self.handler._validate_element(self._box(), "Face7")
        assert_error_contains(self, result, "Face7", "does not exist")

    def test_face_at_lower_boundary_valid(self):
        """Face1 is the first valid (1-indexed) face."""
        self.assertIsNone(self.handler._validate_element(self._box(), "Face1"))

    def test_face_zero_rejected(self):
        """FreeCAD element names are 1-indexed -- Face0 doesn't exist."""
        result = self.handler._validate_element(self._box(), "Face0")
        assert_error_contains(self, result, "Face0", "does not exist")

    def test_face_far_over_rejected(self):
        result = self.handler._validate_element(self._box(), "Face99")
        assert_error_contains(self, result, "Face99", "does not exist")

    def test_edge_within_range_valid(self):
        self.assertIsNone(self.handler._validate_element(self._box(), "Edge12"))

    def test_edge_over_range_rejected(self):
        result = self.handler._validate_element(self._box(), "Edge13")
        assert_error_contains(self, result, "Edge13", "does not exist")

    def test_vertex_within_range_valid(self):
        self.assertIsNone(self.handler._validate_element(self._box(), "Vertex8"))

    def test_vertex_over_range_rejected(self):
        result = self.handler._validate_element(self._box(), "Vertex9")
        assert_error_contains(self, result, "Vertex9", "does not exist")

    def test_empty_element_not_validated(self):
        """Empty string means 'whole object' -- not a validation failure."""
        self.assertIsNone(self.handler._validate_element(self._box(), ""))

    def test_unrecognized_prefix_not_validated(self):
        """Not this helper's job to judge non-Face/Edge/Vertex names (e.g.
        an LCS sub-element) -- let FreeCAD's own machinery handle those."""
        self.assertIsNone(self.handler._validate_element(self._box(), "SomeOtherRef"))

    def test_non_numeric_suffix_not_validated(self):
        """'FaceX' isn't a recognized PrefixN pattern -- skip, don't crash
        trying to int() it."""
        self.assertIsNone(self.handler._validate_element(self._box(), "FaceX"))

    def test_object_without_shape_not_validated(self):
        obj = MagicMock(spec=["Name"])  # no Shape attribute
        self.assertIsNone(self.handler._validate_element(obj, "Face1"))


class TestRequireComponent(unittest.TestCase):
    """Direct coverage of the helper backing create_joint/ground_part/
    get_part_status's 'must be an assembly component' rejection."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_object_in_group_accepted(self):
        obj = make_box_object("Box")
        assembly = make_assembly("Asm", group=[obj])
        self.assertIsNone(self.handler._require_component(obj, assembly))

    def test_object_not_in_group_rejected(self):
        obj = make_box_object("Box")
        assembly = make_assembly("Asm")  # empty Group
        result = self.handler._require_component(obj, assembly)
        assert_error_contains(self, result, "Box", "Asm", "not a component", "add_component")

    def test_object_in_group_alongside_others_accepted(self):
        obj = make_box_object("Box")
        other = make_box_object("Other")
        assembly = make_assembly("Asm", group=[other, obj])
        self.assertIsNone(self.handler._require_component(obj, assembly))

    def test_different_object_same_name_not_matched_by_identity(self):
        """Membership is by object identity, not by Name string -- a
        same-named but distinct object must not be mistaken for the real
        component."""
        obj = make_box_object("Box")
        lookalike = make_box_object("Box")
        assembly = make_assembly("Asm", group=[lookalike])
        result = self.handler._require_component(obj, assembly)
        assert_error_contains(self, result, "not a component")


class TestDescribeReference(unittest.TestCase):
    """Direct coverage of the static helper's edge cases."""

    def test_none_ref(self):
        self.assertEqual(AssemblyOpsHandler._describe_reference(None), "(unset)")

    def test_whole_object_ref_no_trailing_dot(self):
        obj = MagicMock()
        obj.Name = "Box"
        result = AssemblyOpsHandler._describe_reference([obj, ["", ""]])
        self.assertEqual(result, "Box")

    def test_element_ref(self):
        obj = MagicMock()
        obj.Name = "Box"
        result = AssemblyOpsHandler._describe_reference([obj, ["Face3", "Face3"]])
        self.assertEqual(result, "Box.Face3")

    def test_malformed_ref_falls_back_to_str(self):
        """Defensive branch -- shouldn't happen with real FreeCAD data, but
        must not raise if it does."""
        result = AssemblyOpsHandler._describe_reference("not-a-list")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# get_part_status
# ---------------------------------------------------------------------------

class TestGetPartStatus(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_missing_object_name(self):
        result = self.handler.get_part_status({})
        assert_error_contains(self, result, "object_name")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.get_part_status({"object_name": "Box"})
        assert_error_contains(self, result, "No active document")

    def test_object_not_found(self):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_part_status({"object_name": "Ghost"})
        assert_error_contains(self, result, "not found", "Ghost")

    def test_no_assembly_found(self):
        box = make_box_object("Box")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.get_part_status({"object_name": "Box"})
        assert_error_contains(self, result, "No Assembly::AssemblyObject found")

    def test_object_not_a_component_rejected(self):
        """Querying status on a bare document-root object (never
        add_component'd) used to silently return a plausible-looking but
        meaningless grounded=False/connected=False -- confirmed live
        2026-07-24 that isPartGrounded returns False regardless of the
        object's real state when it isn't a component. Now rejected."""
        box = make_box_object("Box")
        assembly = make_assembly("Asm")  # empty Group -- box never added
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_part_status({"object_name": "Box"})

        assert_error_contains(self, result, "Box", "not a component", "add_component")

    def test_reports_grounded_and_connected(self):
        box = make_box_object("Box")
        assembly = make_assembly("Asm", group=[box])
        assembly.isPartGrounded = MagicMock(return_value=True)
        assembly.isPartConnected = MagicMock(return_value=True)
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_part_status({"object_name": "Box"})

        assert_success_contains(self, result, "grounded=True", "connected_to_ground=True")
        assembly.isPartGrounded.assert_called_once_with(box)
        assembly.isPartConnected.assert_called_once_with(box)

    def test_reports_neither_grounded_nor_connected(self):
        box = make_box_object("Box")
        assembly = make_assembly("Asm", group=[box])
        assembly.isPartGrounded = MagicMock(return_value=False)
        assembly.isPartConnected = MagicMock(return_value=False)
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_part_status({"object_name": "Box"})

        assert_success_contains(self, result, "grounded=False", "connected_to_ground=False")

    def test_connected_without_being_grounded(self):
        """A part jointed to a grounded part is connected but not itself
        grounded -- the two flags are independent, not one implying the
        other."""
        box = make_box_object("Box")
        assembly = make_assembly("Asm", group=[box])
        assembly.isPartGrounded = MagicMock(return_value=False)
        assembly.isPartConnected = MagicMock(return_value=True)
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_part_status({"object_name": "Box"})

        assert_success_contains(self, result, "grounded=False", "connected_to_ground=True")

    def test_reports_placement_and_related_joints(self):
        """finding #24: get_part_status used to report only the two
        booleans, with no way to see where the part ended up or which
        joints/grounding reference it."""
        box = make_box_object("Box", placement=_Placement(base=_Vec(3, 4, 5)))
        assembly = make_assembly("Asm", group=[box])
        assembly.isPartGrounded = MagicMock(return_value=True)
        assembly.isPartConnected = MagicMock(return_value=True)

        ground = MagicMock()
        ground.Name = "Box_Ground"
        ground.ObjectToGround = box

        joint = MagicMock()
        joint.Name = "SomeJoint"
        joint.Reference1 = [box, ["Face1", "Face1"]]
        joint.Reference2 = None
        del joint.ObjectToGround

        _set_joints(assembly, [ground, joint])
        doc = make_mock_doc([box, assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.get_part_status({"object_name": "Box"})

        assert_success_contains(self, result, "placement=(3.00,4.00,5.00)",
                                 "Box_Ground (grounding)", "SomeJoint")


# ---------------------------------------------------------------------------
# _resolve_joint / set_joint_offset
# ---------------------------------------------------------------------------

def _make_joint_mock(name="TestJoint"):
    """A mock that behaves like a real Joint FeaturePython object -- has
    JointType (so _resolve_joint accepts it), Offset1/Offset2 as real
    _Placement instances (via install_freecad_value_types, wired at
    _freecad_mocks import time) so .Base.x/.y/.z are assertable."""
    joint = MagicMock()
    joint.Name = name
    joint.JointType = "Fixed"
    return joint


class TestSetJointOffset(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.set_joint_offset({"joint_name": "J"})
        assert_error_contains(self, result, "No active document")

    def test_joint_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_offset({"joint_name": "Ghost"})
        assert_error_contains(self, result, "not found", "Ghost")

    def test_not_a_joint(self):
        """A resolvable object with no JointType (e.g. a plain part) must be
        rejected -- MagicMock auto-vivifies JointType truthy on access, so
        the mock here explicitly deletes it to exercise the real branch."""
        obj = MagicMock()
        obj.Name = "NotAJoint"
        del obj.JointType
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_offset({"joint_name": "NotAJoint"})
        assert_error_contains(self, result, "not a joint")

    def test_invalid_connector(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_offset({"joint_name": "TestJoint", "connector": 3})
        assert_error_contains(self, result, "connector must be 1 or 2")

    def test_sets_offset1_by_default(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_offset({
            "joint_name": "TestJoint", "x": 10, "y": 20, "z": 30,
        })

        assert_success_contains(self, result, "Offset1")
        self.assertEqual(joint.Offset1.Base.x, 10)
        self.assertEqual(joint.Offset1.Base.y, 20)
        self.assertEqual(joint.Offset1.Base.z, 30)

    def test_sets_offset2_when_connector_2(self):
        joint = _make_joint_mock()
        joint.Offset1 = "untouched_sentinel"
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_offset({
            "joint_name": "TestJoint", "connector": 2, "x": 5, "y": 0, "z": 0,
        })

        self.assertEqual(joint.Offset2.Base.x, 5)
        self.assertEqual(joint.Offset1, "untouched_sentinel")

    def test_detach_true_sets_flag(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_offset({"joint_name": "TestJoint", "detach": True})

        self.assertTrue(joint.Detach1)

    def test_detach_false_sets_flag(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_offset({"joint_name": "TestJoint", "detach": False})

        self.assertFalse(joint.Detach1)

    def test_detach_omitted_leaves_it_untouched(self):
        joint = _make_joint_mock()
        joint.Detach1 = "untouched_sentinel"
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_offset({"joint_name": "TestJoint", "x": 1})

        self.assertEqual(joint.Detach1, "untouched_sentinel")


# ---------------------------------------------------------------------------
# set_joint_limits
# ---------------------------------------------------------------------------

class TestSetJointLimits(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.set_joint_limits({"joint_name": "J"})
        assert_error_contains(self, result, "No active document")

    def test_joint_not_found(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_limits({"joint_name": "Ghost"})
        assert_error_contains(self, result, "not found", "Ghost")

    def test_not_a_joint(self):
        obj = MagicMock()
        obj.Name = "NotAJoint"
        del obj.JointType
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_limits({"joint_name": "NotAJoint"})
        assert_error_contains(self, result, "not a joint")

    def test_no_limits_provided(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.set_joint_limits({"joint_name": "TestJoint"})
        assert_error_contains(self, result, "No limits provided")

    def test_length_min_sets_value_and_enables(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({"joint_name": "TestJoint", "length_min": 5})

        assert_success_contains(self, result, "LengthMin=5")
        self.assertEqual(joint.LengthMin, 5)
        self.assertTrue(joint.EnableLengthMin)

    def test_length_max_sets_value_and_enables(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_limits({"joint_name": "TestJoint", "length_max": 50})

        self.assertEqual(joint.LengthMax, 50)
        self.assertTrue(joint.EnableLengthMax)

    def test_angle_min_sets_value_and_enables(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_limits({"joint_name": "TestJoint", "angle_min": -45})

        self.assertEqual(joint.AngleMin, -45)
        self.assertTrue(joint.EnableAngleMin)

    def test_angle_max_sets_value_and_enables(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_limits({"joint_name": "TestJoint", "angle_max": 90})

        self.assertEqual(joint.AngleMax, 90)
        self.assertTrue(joint.EnableAngleMax)

    def test_multiple_limits_at_once(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({
            "joint_name": "TestJoint", "length_min": 1, "angle_max": 90,
        })

        assert_success_contains(self, result, "LengthMin=1", "AngleMax=90")
        self.assertEqual(joint.LengthMin, 1)
        self.assertEqual(joint.AngleMax, 90)

    def test_omitted_limit_not_touched(self):
        joint = _make_joint_mock()
        joint.LengthMax = "untouched_sentinel"
        joint.EnableLengthMax = "untouched_flag_sentinel"
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.set_joint_limits({"joint_name": "TestJoint", "length_min": 5})

        self.assertEqual(joint.LengthMax, "untouched_sentinel")
        self.assertEqual(joint.EnableLengthMax, "untouched_flag_sentinel")

    def test_inverted_length_range_rejected(self):
        """finding #19: an inverted range (min > max) used to be accepted
        silently and reported as success."""
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({
            "joint_name": "TestJoint", "length_min": 50, "length_max": 10,
        })

        assert_error_contains(self, result, "length_min", "length_max")
        # Nothing applied -- validated before any assignment.
        self.assertNotEqual(joint.LengthMin, 50)

    def test_inverted_angle_range_rejected(self):
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({
            "joint_name": "TestJoint", "angle_min": 90, "angle_max": -90,
        })

        assert_error_contains(self, result, "angle_min", "angle_max")

    def test_equal_min_max_accepted(self):
        """min == max is a valid (if degenerate) fixed limit, not inverted."""
        joint = _make_joint_mock()
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({
            "joint_name": "TestJoint", "length_min": 10, "length_max": 10,
        })

        assert_success_contains(self, result, "LengthMin=10", "LengthMax=10")

    def test_new_min_checked_against_existing_enabled_max(self):
        """A call that only supplies length_min must still be checked
        against an already-enabled length_max from a prior call, not just
        against itself."""
        joint = _make_joint_mock()
        joint.EnableLengthMax = True
        joint.LengthMax = 10
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({"joint_name": "TestJoint", "length_min": 50})

        assert_error_contains(self, result, "length_min", "length_max")

    def test_new_min_not_checked_against_disabled_existing_max(self):
        """An existing LengthMax value that was never enabled (EnableLengthMax
        False) must not be treated as an active constraint."""
        joint = _make_joint_mock()
        joint.EnableLengthMax = False
        joint.LengthMax = 10  # stale/unused value, flag says it's not active
        doc = make_mock_doc([joint])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.set_joint_limits({"joint_name": "TestJoint", "length_min": 50})

        assert_success_contains(self, result, "LengthMin=50")


if __name__ == "__main__":
    unittest.main()

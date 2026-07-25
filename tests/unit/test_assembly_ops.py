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
)

from handlers.assembly_ops import AssemblyOpsHandler, _JOINT_TYPES


def _make_joint_group(name="Joints"):
    """Mock Assembly::JointGroup. newObject appends fresh joint mocks to .Group,
    mirroring make_assembly's newObject side effect."""
    jg = MagicMock()
    jg.Name = name
    jg.Group = []

    def _new_object(type_id, obj_name=None):
        obj = MagicMock()
        obj.Name = obj_name or f"{type_id}_auto"
        obj.TypeId = type_id
        jg.Group.append(obj)
        return obj

    jg.newObject = MagicMock(side_effect=_new_object)
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

    def test_reference_without_resolvable_object_does_not_crash(self):
        """reference given but reference_object doesn't resolve to anything
        (e.g. empty string, or a typo) — pins the current silent-skip
        behavior: AttachmentSupport is left unset, no error raised."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.create_lcs({
            "name": "LCS1", "map_mode": "FlatFace", "reference": "Face3",
        })
        assert_success_contains(self, result, "LCS1")

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


# ---------------------------------------------------------------------------
# create_joint
# ---------------------------------------------------------------------------

class TestCreateJoint(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(AssemblyOpsHandler)

    def _setup(self, doc_objects=None):
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly] + (doc_objects or []))
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
        assembly = make_assembly("Asm")
        doc = make_mock_doc([assembly] + (doc_objects or []))
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
        assembly.Joints = []
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
        assembly.Joints = [joint]
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
        assembly.Joints = [ground]
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
        assembly.Joints = [joint]
        doc = make_mock_doc([assembly])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_joints({})

        assert_success_contains(self, result, "(unset)")


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


if __name__ == "__main__":
    unittest.main()

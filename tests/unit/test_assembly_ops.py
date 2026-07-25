"""Unit tests for AssemblyOpsHandler (Assembly workbench, Phase 1).

Phase 1 covers container + reference-geometry + component-linking only —
no joints yet. Coverage focus: create_assembly (and the Type="Assembly"
side effect that Phase 2's joint/grounding code will silently depend on),
create_lcs (Part::LocalCoordinateSystem attach mechanics, mirroring
create_datum_plane but without forcing a PartDesign Body), add_component
(App::Link vs Assembly::AssemblyLink branch, same- and cross-document
linking, label resolution), and list_components.
"""

import unittest
from unittest.mock import MagicMock

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
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

from handlers.assembly_ops import AssemblyOpsHandler


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


if __name__ == "__main__":
    unittest.main()

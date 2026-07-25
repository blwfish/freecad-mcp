# Assembly workbench operation handlers for FreeCAD MCP
#
# Phase 1: container + reference-geometry + component-linking primitives.
# No joints yet (Phase 2) — this only gets parts into an Assembly::AssemblyObject
# with Local Coordinate Systems available as mating references.
#
# Every operation here is confirmed headless-safe (no Gui:: / ViewObject
# dependency in the underlying FreeCAD API) and requires no GUI click-selection.

import FreeCAD
from typing import Dict, Any
from .base import BaseHandler


class AssemblyOpsHandler(BaseHandler):
    """Handler for Assembly workbench operations (container, LCS, linking)."""

    _ALLOWED_OPERATIONS = frozenset({
        "create_assembly", "create_lcs", "add_component", "list_components",
    })

    def create_assembly(self, args: Dict[str, Any]) -> str:
        """Create an Assembly::AssemblyObject container in the active document.

        Args:
            name: Name for the assembly (default "Assembly")
        """
        try:
            name = args.get('name', 'Assembly')

            doc = self.get_document()
            if not doc:
                return "No active document"

            assembly = doc.addObject("Assembly::AssemblyObject", name)
            # Not automatic: FreeCAD's own CommandCreateAssembly.py sets this by
            # hand after addObject(). JointObject.py branches on
            # assembly.Type == "Assembly" in several places (grounding,
            # solve-status lookups) — skip this and Phase 2's joint/grounding
            # logic will silently fail to find the assembly.
            assembly.Type = "Assembly"

            self.recompute(doc)

            return f"Created assembly: {assembly.Name}"

        except Exception as e:
            return f"Error creating assembly: {e}"

    def create_lcs(self, args: Dict[str, Any]) -> str:
        """Create a Local Coordinate System (joint mating reference).

        Uses Part::LocalCoordinateSystem, not the bare App::LocalCoordinateSystem
        base class — the base class has no MapMode/AttachmentSupport (Part::
        AttachExtension is mixed into the Part:: subclass, confirmed against
        Part/App/Datums.h and by a live headless smoke test: the base class
        raises "'App.GeoFeature' object has no attribute 'MapMode'"). It still
        satisfies Assembly's own joint-reference gate, which checks
        isDerivedFrom("App::LocalCoordinateSystem") — true for the subclass.
        Doesn't require a PartDesign Body — it can be a bare document object,
        or nested inside any container that supports newObject() (a Body, an
        App::Part, an Assembly::AssemblyObject).

        Args:
            name: Name for the LCS (default "LCS")
            container_name: Object to nest the LCS inside (default: bare document).
                Must support newObject() — a Body, App::Part, or Assembly.
            map_mode: Attachment mode, e.g. "FlatFace", "ObjectXY" (default: none —
                LCS stays at the origin unless offset_x/y/z is given)
            reference: Face or edge reference, e.g. "Face1", "Edge3"
            reference_object: Object name containing the reference
            offset_x: Offset in X from attached position (mm)
            offset_y: Offset in Y from attached position (mm)
            offset_z: Offset in Z / normal direction (mm)
        """
        try:
            name = args.get('name', 'LCS')
            container_name = args.get('container_name', '')
            map_mode = args.get('map_mode', '')
            reference = args.get('reference', '')
            reference_object = args.get('reference_object', '')
            offset_x = args.get('offset_x', 0)
            offset_y = args.get('offset_y', 0)
            offset_z = args.get('offset_z', 0)

            doc = self.get_document()
            if not doc:
                return "No active document"

            container = doc
            if container_name:
                container = self.get_object(container_name, doc)
                if not container:
                    return f"Container not found: {container_name}"

            if container is doc:
                lcs = doc.addObject("Part::LocalCoordinateSystem", name)
            else:
                lcs = container.newObject("Part::LocalCoordinateSystem", name)

            if map_mode:
                lcs.MapMode = map_mode
                if reference:
                    ref_obj = None
                    if reference_object:
                        ref_obj = self.get_object(reference_object, doc)
                    if ref_obj:
                        lcs.AttachmentSupport = [(ref_obj, reference)]

            if offset_x or offset_y or offset_z:
                lcs.AttachmentOffset = FreeCAD.Placement(
                    FreeCAD.Vector(offset_x, offset_y, offset_z),
                    FreeCAD.Rotation(0, 0, 0, 1)
                )

            self.recompute(doc)

            return (f"Created LCS: {lcs.Name}"
                    + (f", container={container_name}" if container_name else "")
                    + (f", map_mode={map_mode}" if map_mode else ""))

        except Exception as e:
            return f"Error creating LCS: {e}"

    def add_component(self, args: Dict[str, Any]) -> str:
        """Add an object into an assembly as a lightweight link (not a copy).

        Mirrors FreeCAD's own Assembly_InsertLink command: a nested sub-assembly
        (an Assembly::AssemblyObject) becomes an Assembly::AssemblyLink; anything
        else becomes a plain App::Link. Unlike insert_shape (which copies the
        Shape), this is a live link — edits to the source object propagate.

        Args:
            object_name: Name (or Label) of the object to link in
            assembly_name: Target assembly (default: first Assembly::AssemblyObject
                in the active document)
            source_doc: Name of another already-open document to pull object_name
                from (default: active document). Cross-document linking requires
                the source document to already be open, AND both the source and
                the active document to already be saved to disk (FreeCAD's
                PropertyXLink needs a file path on both ends) — FreeCAD does not
                auto-reopen documents.
            x, y, z: Initial placement offset in mm (default: unplaced)
        """
        try:
            object_name = args.get('object_name', '')
            assembly_name = args.get('assembly_name', '')
            source_doc = args.get('source_doc', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            if not object_name:
                return "object_name parameter required"

            doc = self.get_document()
            if not doc:
                return "No active document"

            src_search_doc = doc
            if source_doc:
                docs = FreeCAD.listDocuments()
                if source_doc not in docs:
                    return f"Document not open: {source_doc}. Open docs: {list(docs.keys())}"
                src_search_doc = FreeCAD.getDocument(source_doc)
                # FreeCAD's PropertyXLink needs a file path on both ends to compute
                # a relative path between them (App/PropertyLinks.cpp: "Linked
                # document not saved" / "Owner document not saved") -- confirmed
                # live against both cases: without this check, assembly.newObject()
                # already ran and left an orphan, unlinked App::Link sitting in the
                # assembly's Group by the time LinkedObject's assignment threw.
                # Check before creating anything so a rejected link never partially
                # exists.
                if not getattr(src_search_doc, 'FileName', ''):
                    return (f"Document '{source_doc}' has never been saved. "
                            f"Cross-document links require a file path on disk — "
                            f"save it first with view_control(operation='save_document').")
                if not getattr(doc, 'FileName', ''):
                    return (f"Active document '{doc.Name}' has never been saved. "
                            f"Cross-document links require a file path on disk on "
                            f"both ends — save it first with "
                            f"view_control(operation='save_document').")

            src_obj = self.get_object(object_name, src_search_doc)
            if not src_obj:
                if source_doc:
                    return f"Object not found in '{source_doc}': {object_name}"
                return f"Object not found: {object_name}"

            if assembly_name:
                assembly = self.get_object(assembly_name, doc)
                if not assembly:
                    return f"Assembly not found: {assembly_name}"
            else:
                assembly = self.find_assembly(doc)
                if not assembly:
                    return ("No Assembly::AssemblyObject found in the active document. "
                            "Create one first with create_assembly.")

            is_sub_assembly = False
            try:
                is_sub_assembly = bool(src_obj.isDerivedFrom("Assembly::AssemblyObject"))
            except Exception:
                pass
            link_type = "Assembly::AssemblyLink" if is_sub_assembly else "App::Link"

            link_name = getattr(src_obj, 'Label', None) or object_name
            link = assembly.newObject(link_type, link_name)
            link.LinkedObject = src_obj

            if x != 0 or y != 0 or z != 0:
                link.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            return (f"Added component: {link.Name} ({link_type}) -> {object_name}"
                    + (f" from '{source_doc}'" if source_doc else "")
                    + f" into {assembly.Name}")

        except Exception as e:
            return f"Error adding component: {e}"

    def list_components(self, args: Dict[str, Any]) -> str:
        """List the components (links) inside an assembly.

        Args:
            assembly_name: Assembly to inspect (default: first
                Assembly::AssemblyObject in the active document)
        """
        try:
            assembly_name = args.get('assembly_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            if assembly_name:
                assembly = self.get_object(assembly_name, doc)
                if not assembly:
                    return f"Assembly not found: {assembly_name}"
            else:
                assembly = self.find_assembly(doc)
                if not assembly:
                    return "No Assembly::AssemblyObject found in the active document."

            group = getattr(assembly, 'Group', []) or []
            if not group:
                return f"Assembly '{assembly.Name}' has no components."

            lines = [f"Components of {assembly.Name} ({len(group)} total):"]
            for obj in group:
                linked = getattr(obj, 'LinkedObject', None)
                linked_str = f" -> {linked.Name}" if linked else ""
                lines.append(f"  {obj.Name} ({obj.TypeId}){linked_str}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error listing components: {e}"

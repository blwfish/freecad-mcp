# Assembly workbench operation handlers for FreeCAD MCP
#
# Phase 1: container + reference-geometry + component-linking primitives.
# Phase 2: joints, grounding, and solving.
# Phase 3: diagnostics (isPartConnected/isPartGrounded) and per-joint
# offset/detach + motion-limit controls.
#
# Every operation here is confirmed headless-safe (no Gui:: / ViewObject
# dependency in the underlying FreeCAD API) and requires no GUI click-selection.
# Joint reference tuples are addressed programmatically (e.g. "Face3" from
# measurement_operations.list_faces) — no GUI click-selection needed for
# joints either, unlike fillet/chamfer.

import FreeCAD
from typing import Dict, Any, Optional
from .base import BaseHandler

# Mirrors JointObject.JointTypes exactly (JointObject.py) -- index order is
# load-bearing, since Joint(joint, type_index) takes a positional index, not
# a name. Keep in sync if FreeCAD adds joint types.
_JOINT_TYPES = [
    "Fixed", "Revolute", "Cylindrical", "Slider", "Ball", "Distance",
    "Parallel", "Perpendicular", "Angle", "RackPinion", "Screw", "Gears",
    "Belt",
]

# assembly.solve()'s return code, per AssemblyObject.pyi's documented
# contract. Confirmed by source reading (2026-07-24) that only 0/-1/-6 are
# actually reachable in the current FreeCAD tree -- -2/-3/-4/-5 are declared
# in the docstring but the C++ implementation never returns them today. Kept
# here in full for forward-compatibility and because the docstring is the
# closed vocabulary FreeCAD itself commits to, not because all branches are
# currently live.
_SOLVE_STATUS = {
    0: "success",
    -1: "solver_error",
    -2: "redundant_constraints",
    -3: "conflicting_constraints",
    -4: "over_constrained",
    -5: "malformed_constraints",
    -6: "no_grounded_parts",
}


class AssemblyOpsHandler(BaseHandler):
    """Handler for Assembly workbench operations (container, LCS, linking, joints)."""

    _ALLOWED_OPERATIONS = frozenset({
        "create_assembly", "create_lcs", "add_component", "list_components",
        "create_joint", "ground_part", "solve", "list_joints",
        "get_part_status", "set_joint_offset", "set_joint_limits",
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

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

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

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

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

    def _resolve_assembly(self, assembly_name: str, doc):
        """Shared assembly-resolution: explicit name, or first in document.

        Returns (assembly, error_string). error_string is None on success.
        """
        if assembly_name:
            assembly = self.get_object(assembly_name, doc)
            if not assembly:
                return None, f"Assembly not found: {assembly_name}"
            return assembly, None
        assembly = self.find_assembly(doc)
        if not assembly:
            return None, ("No Assembly::AssemblyObject found in the active document. "
                           "Create one first with create_assembly.")
        return assembly, None

    def _require_component(self, obj, assembly) -> "Optional[str]":
        """Return an error string if obj is not a direct component of
        assembly (i.e. not in assembly.Group), else None.

        FreeCAD's own solve-status queries (isPartGrounded, isPartConnected)
        and the solver itself only recognize objects that are actual
        assembly components. Confirmed live 2026-07-24: grounding or
        jointing a bare document-root object previously succeeded with no
        error, but silently produced a state get_part_status couldn't see
        -- Placement genuinely read-only, a correctly-configured
        GroundedJoint, yet isPartGrounded still reported False. Rejecting
        non-components up front, before anything gets created, converts
        that silent trap into an immediate, actionable error instead of a
        misleading query result discovered later.
        """
        group = getattr(assembly, 'Group', []) or []
        if obj in group:
            return None
        return (f"'{obj.Name}' is not a component of assembly '{assembly.Name}' "
                f"(not in its Group). Add it first with add_component -- "
                f"FreeCAD's own grounding/connectivity checks and solver "
                f"only recognize actual assembly components.")

    # element name prefix -> Shape collection attribute. Single source of
    # truth for Face/Edge/Vertex dispatch (Syntactic-Semantic Seam Rule) --
    # both the "which collection to bound-check" and "is this a recognized
    # element type at all" questions read from this one dict.
    _ELEMENT_COLLECTIONS = {"Face": "Faces", "Edge": "Edges", "Vertex": "Vertexes"}

    def _validate_element(self, obj, element_name: str) -> "Optional[str]":
        """Return an error string if element_name doesn't exist on obj.Shape.

        FreeCAD's own joint machinery does NOT validate this: a nonexistent
        face like "Face99" on a 6-face box is silently accepted --
        UtilsAssembly.findPlacement's get_element() returns None for a
        missing element and the caller just falls back to an identity
        Placement. Confirmed live 2026-07-24: create_joint against a real
        headless FreeCAD instance returned "Created ... joint" with no
        error for a garbage face index. Exactly the "silent reasonable
        behavior on ambiguous input" failure mode -- catch it before
        creating anything, rather than let a broken joint report success.
        """
        if not element_name:
            return None
        for prefix, collection_attr in self._ELEMENT_COLLECTIONS.items():
            if not element_name.startswith(prefix):
                continue
            try:
                index = int(element_name[len(prefix):])
            except ValueError:
                # Not a recognized "PrefixN" pattern (e.g. an LCS sub-element
                # name) -- not this helper's job to judge, let FreeCAD handle it.
                return None
            if not hasattr(obj, 'Shape'):
                return None
            collection = getattr(obj.Shape, collection_attr, [])
            if index < 1 or index > len(collection):
                return (f"{element_name} does not exist on {obj.Name} "
                        f"({len(collection)} {collection_attr.lower()}, "
                        f"valid range 1-{len(collection)})")
            return None
        return None

    def create_joint(self, args: Dict[str, Any]) -> str:
        """Create a joint between two objects' geometry.

        Reference tuples are addressed programmatically -- e.g. "Face3" from
        measurement_operations.list_faces -- no GUI click-selection needed,
        unlike partdesign fillet/chamfer. Mirrors FreeCAD's own
        setJointConnectors() call, which also auto-solves the assembly once
        both references are set (if the SolveInJointCreation preference is
        on, which is the default).

        Args:
            joint_type: One of Fixed, Revolute, Cylindrical, Slider, Ball,
                Distance, Parallel, Perpendicular, Angle, RackPinion, Screw,
                Gears, Belt.
            ref1_object, ref2_object: Names (or Labels) of the two objects to joint.
            ref1_element, ref2_element: Sub-element on each object, e.g. "Face3",
                "Edge8" (default: whole object).
            ref1_vertex, ref2_vertex: Vertex used to disambiguate placement on
                that element (default: same as the element itself, which FreeCAD
                interprets as "use this element's own center" -- a face
                centroid, an edge midpoint/circle-center, etc).
            name: Name for the joint (default: "<joint_type>Joint")
            assembly_name: Target assembly (default: first Assembly::AssemblyObject
                in the active document)
            distance: Value for Distance-using joints (Distance, RackPinion,
                Screw's pitch, Gears/Belt's first radius)
            distance2: Second radius, Gears/Belt only
            angle: Value for the Angle joint
        """
        try:
            joint_type = args.get('joint_type', '')
            if joint_type not in _JOINT_TYPES:
                return (f"Unknown joint_type: {joint_type!r}. Must be one of: "
                        f"{', '.join(_JOINT_TYPES)}")

            ref1_object = args.get('ref1_object', '')
            ref2_object = args.get('ref2_object', '')
            if not ref1_object or not ref2_object:
                return "ref1_object and ref2_object are required"

            ref1_element = args.get('ref1_element', '')
            ref1_vertex = args.get('ref1_vertex', '') or ref1_element
            ref2_element = args.get('ref2_element', '')
            ref2_vertex = args.get('ref2_vertex', '') or ref2_element

            name = args.get('name', '') or f"{joint_type}Joint"
            assembly_name = args.get('assembly_name', '')
            distance = args.get('distance', None)
            distance2 = args.get('distance2', None)
            angle = args.get('angle', None)

            doc = self.get_document()
            if not doc:
                return "No active document"

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

            ref1_obj = self.get_object(ref1_object, doc)
            if not ref1_obj:
                return f"Object not found: {ref1_object}"
            ref2_obj = self.get_object(ref2_object, doc)
            if not ref2_obj:
                return f"Object not found: {ref2_object}"

            comp_err = (self._require_component(ref1_obj, assembly)
                        or self._require_component(ref2_obj, assembly))
            if comp_err:
                return comp_err

            elem_err = (self._validate_element(ref1_obj, ref1_element)
                        or self._validate_element(ref2_obj, ref2_element))
            if elem_err:
                return elem_err

            import UtilsAssembly
            import JointObject

            joint_group = UtilsAssembly.getJointGroup(assembly)

            joint = None
            try:
                joint = joint_group.newObject("App::FeaturePython", name)
                JointObject.Joint(joint, _JOINT_TYPES.index(joint_type))

                if distance is not None:
                    joint.Distance = distance
                if distance2 is not None:
                    joint.Distance2 = distance2
                if angle is not None:
                    joint.Angle = angle

                refs = [
                    [ref1_obj, [ref1_element, ref1_vertex]],
                    [ref2_obj, [ref2_element, ref2_vertex]],
                ]
                joint.Proxy.setJointConnectors(joint, refs)
            except Exception:
                # setJointConnectors (or an earlier step) can throw after the
                # joint object already exists in the document -- same
                # orphan-object shape as Phase 1's add_component bug. Clean
                # up rather than leave a half-built joint behind.
                if joint is not None:
                    try:
                        doc.removeObject(joint.Name)
                    except Exception:
                        pass
                raise

            self.recompute(doc)

            return (f"Created {joint_type} joint: {joint.Name} "
                    f"({ref1_object}.{ref1_element or '(whole)'} <-> "
                    f"{ref2_object}.{ref2_element or '(whole)'})")

        except Exception as e:
            return f"Error creating joint: {e}"

    def ground_part(self, args: Dict[str, Any]) -> str:
        """Fix a part in place ("ground" it) -- every assembly needs at least
        one grounded part before solve() can succeed (code -6 otherwise).

        Grounding is not a boolean flag in FreeCAD -- it's a dedicated
        GroundedJoint object in the JointGroup that makes the target's
        Placement property read-only. Grounding an already-grounded part is
        harmless (creates a second GroundedJoint pointing at the same
        object) but redundant; not guarded against here since FreeCAD's own
        GUI doesn't prevent it either.

        object_name must already be an assembly component (added via
        add_component) -- FreeCAD's own isPartGrounded/isPartConnected and
        the solver only recognize grounding on actual components. Grounding
        a bare document-root object used to silently "succeed" while
        producing a state those checks couldn't see; that's now rejected
        up front instead.

        Args:
            object_name: Name (or Label) of the object to ground
            assembly_name: Target assembly (default: first Assembly::AssemblyObject
                in the active document)
            name: Name for the grounding joint (default: "<object_name>_Ground")
        """
        try:
            object_name = args.get('object_name', '')
            assembly_name = args.get('assembly_name', '')
            name = args.get('name', '')

            if not object_name:
                return "object_name parameter required"

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

            comp_err = self._require_component(obj, assembly)
            if comp_err:
                return comp_err

            import UtilsAssembly
            import JointObject

            joint_group = UtilsAssembly.getJointGroup(assembly)
            ground_name = name or f"{object_name}_Ground"

            ground = None
            try:
                ground = joint_group.newObject("App::FeaturePython", ground_name)
                JointObject.GroundedJoint(ground, obj)
            except Exception:
                if ground is not None:
                    try:
                        doc.removeObject(ground.Name)
                    except Exception:
                        pass
                raise

            self.recompute(doc)

            return f"Grounded {object_name} via {ground.Name}"

        except Exception as e:
            return f"Error grounding part: {e}"

    def solve(self, args: Dict[str, Any]) -> str:
        """Solve the assembly, updating part placements from its joints.

        The return code is mapped to a named status rather than left as a
        bare int -- 0=success, -1=solver_error, -6=no_grounded_parts are the
        codes actually reachable in the current FreeCAD tree; -2/-3/-4/-5
        (redundant/conflicting/over_constrained/malformed) are declared in
        FreeCAD's own contract but not currently produced by the solver.

        Args:
            assembly_name: Assembly to solve (default: first
                Assembly::AssemblyObject in the active document)
            enable_undo: Whether to save the pre-solve position for undoSolve()
                (default False)
        """
        try:
            assembly_name = args.get('assembly_name', '')
            enable_undo = bool(args.get('enable_undo', False))

            doc = self.get_document()
            if not doc:
                return "No active document"

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

            code = assembly.solve(enable_undo)
            status = _SOLVE_STATUS.get(code, f"unknown_code_{code}")

            return f"Solve result for {assembly.Name}: {status} (code={code})"

        except Exception as e:
            return f"Error solving assembly: {e}"

    def list_joints(self, args: Dict[str, Any]) -> str:
        """List the joints (and grounded parts) inside an assembly.

        Args:
            assembly_name: Assembly to inspect (default: first
                Assembly::AssemblyObject in the active document)
        """
        try:
            assembly_name = args.get('assembly_name', '')

            doc = self.get_document()
            if not doc:
                return "No active document"

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

            joints = list(getattr(assembly, 'Joints', []) or [])
            if not joints:
                return f"Assembly '{assembly.Name}' has no joints."

            lines = [f"Joints of {assembly.Name} ({len(joints)} total):"]
            for j in joints:
                # GroundedJoint objects have no JointType -- describe them
                # distinctly rather than let getattr('JointType', '?') print
                # a misleading '?' for what's actually a grounding, not a
                # regular joint.
                if hasattr(j, 'ObjectToGround'):
                    target = getattr(j.ObjectToGround, 'Name', '?')
                    lines.append(f"  {j.Name} (Grounded): {target}")
                    continue
                jtype = getattr(j, 'JointType', '?')
                r1 = self._describe_reference(getattr(j, 'Reference1', None))
                r2 = self._describe_reference(getattr(j, 'Reference2', None))
                lines.append(f"  {j.Name} ({jtype}): {r1} <-> {r2}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error listing joints: {e}"

    @staticmethod
    def _describe_reference(ref) -> str:
        """Render a Reference1/Reference2 value ([obj, [elem, vtx]]) as text."""
        if not ref:
            return "(unset)"
        try:
            obj, sub = ref[0], ref[1]
            elem = sub[0] if sub else ''
            return f"{obj.Name}.{elem}" if elem else obj.Name
        except Exception:
            return str(ref)

    def get_part_status(self, args: Dict[str, Any]) -> str:
        """Report whether a part is grounded and/or connected to ground through joints.

        "Grounded" (isPartGrounded) means the part has its own GroundedJoint.
        "Connected" (isPartConnected) means the part is reachable to *some*
        grounded part through the joint graph -- a part can be connected
        without being grounded itself (e.g. jointed to a grounded part), or
        grounded without any other joints at all.

        object_name must already be an assembly component (added via
        add_component) -- FreeCAD's isPartGrounded/isPartConnected only
        recognize actual components, so querying anything else is rejected
        up front rather than returning a plausible-looking but meaningless
        grounded=False/connected_to_ground=False. (ground_part and
        create_joint enforce the same requirement on their own targets, so
        this case should mainly come up when checking status on an object
        that was never involved in the assembly at all.)

        Args:
            object_name: Name (or Label) of the part to check
            assembly_name: Assembly to check within (default: first
                Assembly::AssemblyObject in the active document)
        """
        try:
            object_name = args.get('object_name', '')
            assembly_name = args.get('assembly_name', '')

            if not object_name:
                return "object_name parameter required"

            doc = self.get_document()
            if not doc:
                return "No active document"

            obj = self.get_object(object_name, doc)
            if not obj:
                return f"Object not found: {object_name}"

            assembly, err = self._resolve_assembly(assembly_name, doc)
            if err:
                return err

            comp_err = self._require_component(obj, assembly)
            if comp_err:
                return comp_err

            grounded = bool(assembly.isPartGrounded(obj))
            connected = bool(assembly.isPartConnected(obj))

            return (f"{object_name}: grounded={grounded}, connected_to_ground={connected}")

        except Exception as e:
            return f"Error getting part status: {e}"

    def _resolve_joint(self, joint_name: str, doc):
        """Resolve a joint by name and confirm it's a real Joint (has JointType,
        not a GroundedJoint or something else).

        Returns (joint, error_string). error_string is None on success.
        """
        if not joint_name:
            return None, "joint_name parameter required"
        joint = self.get_object(joint_name, doc)
        if not joint:
            return None, f"Joint not found: {joint_name}"
        if not hasattr(joint, 'JointType'):
            return None, f"'{joint_name}' is not a joint (no JointType property)"
        return joint, None

    def set_joint_offset(self, args: Dict[str, Any]) -> str:
        """Set a joint connector's attachment offset (Offset1 or Offset2).

        Args:
            joint_name: Name of the joint to modify
            connector: Which connector to offset, 1 or 2 (default 1)
            x, y, z: Offset position in mm (default 0,0,0)
            detach: If True, sets Detach1/Detach2 so Placement1/2 stops
                auto-recomputing from the reference and can be positioned
                manually via this offset. If False, re-enables auto-recompute.
                Omit to leave Detach unchanged.
        """
        try:
            joint_name = args.get('joint_name', '')
            connector = args.get('connector', 1)
            if connector not in (1, 2):
                return f"connector must be 1 or 2, got {connector!r}"
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            detach = args.get('detach', None)

            doc = self.get_document()
            if not doc:
                return "No active document"

            joint, err = self._resolve_joint(joint_name, doc)
            if err:
                return err

            offset_attr = f"Offset{connector}"
            detach_attr = f"Detach{connector}"

            setattr(joint, offset_attr, FreeCAD.Placement(
                FreeCAD.Vector(x, y, z), FreeCAD.Rotation(0, 0, 0, 1)
            ))

            if detach is not None:
                setattr(joint, detach_attr, bool(detach))

            self.recompute(doc)

            return (f"Set {joint_name}.{offset_attr} = ({x},{y},{z})"
                    + (f", {detach_attr}={bool(detach)}" if detach is not None else ""))

        except Exception as e:
            return f"Error setting joint offset: {e}"

    def set_joint_limits(self, args: Dict[str, Any]) -> str:
        """Set a joint's motion limits (length and/or angle, min and/or max).

        Setting a limit value also enables it (EnableLengthMin/Max,
        EnableAngleMin/Max) -- FreeCAD gates whether a limit is active on
        these separate bool flags, so a value with the flag left off would
        silently do nothing. Only the limits actually provided are touched;
        omitted ones are left as-is.

        Args:
            joint_name: Name of the joint to modify
            length_min, length_max: Length limits in mm (Cylindrical/Slider)
            angle_min, angle_max: Angle limits in degrees (Revolute/Cylindrical)
        """
        try:
            joint_name = args.get('joint_name', '')
            length_min = args.get('length_min', None)
            length_max = args.get('length_max', None)
            angle_min = args.get('angle_min', None)
            angle_max = args.get('angle_max', None)

            doc = self.get_document()
            if not doc:
                return "No active document"

            joint, err = self._resolve_joint(joint_name, doc)
            if err:
                return err

            applied = []
            if length_min is not None:
                joint.LengthMin = length_min
                joint.EnableLengthMin = True
                applied.append(f"LengthMin={length_min}")
            if length_max is not None:
                joint.LengthMax = length_max
                joint.EnableLengthMax = True
                applied.append(f"LengthMax={length_max}")
            if angle_min is not None:
                joint.AngleMin = angle_min
                joint.EnableAngleMin = True
                applied.append(f"AngleMin={angle_min}")
            if angle_max is not None:
                joint.AngleMax = angle_max
                joint.EnableAngleMax = True
                applied.append(f"AngleMax={angle_max}")

            if not applied:
                return "No limits provided (length_min/length_max/angle_min/angle_max)"

            self.recompute(doc)

            return f"Set {joint_name}: {', '.join(applied)}"

        except Exception as e:
            return f"Error setting joint limits: {e}"

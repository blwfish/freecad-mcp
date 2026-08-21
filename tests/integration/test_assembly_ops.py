"""
Assembly workbench integration tests — create_assembly, create_lcs,
add_component, list_components, create_joint, ground_part, solve,
list_joints, get_part_status, set_joint_offset, set_joint_limits.

Every operation here is confirmed headless-safe (no FreeCADGui dependency
anywhere in AICopilot/handlers/assembly_ops.py) — unlike PartDesign's
fillet/chamfer, joints are addressed by element name (e.g. "Face3"), no
GUI click-selection needed. Two plain Part::Box components are sufficient
fixtures for every test in this file.
"""

import re
import time
import pytest
from ._geom_helpers import assert_op_succeeded, _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"AssemblyOps_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })
    yield doc_name
    try:
        send_command("execute_python_sync", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


def _add_component(object_name: str, assembly_name: str = "Assy") -> str:
    """add_component renames the resulting link if the source object's
    Label collides with an existing document object name (e.g. "BoxA"
    -> "BoxA001") — parse the actual link name out of the response
    rather than assuming it matches object_name."""
    result = send_command("assembly_operations", {
        "operation": "add_component",
        "object_name": object_name,
        "assembly_name": assembly_name,
    })
    text = _text(result)
    assert_op_succeeded(result, f"add_component({object_name})")
    m = re.search(r"Added component: (\S+) \(", text)
    assert m, f"could not parse component name from: {text[:300]}"
    return m.group(1)


@pytest.fixture
def assembly_with_two_boxes(clean_document):
    """An assembly named 'Assy' with two 10mm Part::Box components.

    Returns (doc_name, box_a_component_name, box_b_component_name).
    """
    send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
    send_command("part_operations", {
        "operation": "box", "length": 10, "width": 10, "height": 10, "name": "BoxA",
    })
    send_command("part_operations", {
        "operation": "box", "length": 10, "width": 10, "height": 10, "name": "BoxB",
    })
    comp_a = _add_component("BoxA")
    comp_b = _add_component("BoxB")
    return clean_document, comp_a, comp_b


class TestCreateAssembly:
    def test_create_assembly_default_name(self, clean_document):
        result = send_command("assembly_operations", {"operation": "create_assembly"})
        assert_op_succeeded(result, "create_assembly")
        assert "Created assembly: Assembly" in _text(result)

    def test_create_assembly_custom_name(self, clean_document):
        result = send_command("assembly_operations", {
            "operation": "create_assembly", "name": "MyRig",
        })
        text = _text(result)
        assert "Created assembly: MyRig" in text, text[:300]


class TestCreateLCS:
    def test_create_lcs_bare(self, clean_document):
        result = send_command("assembly_operations", {
            "operation": "create_lcs", "name": "LCS_Origin",
        })
        assert_op_succeeded(result, "create_lcs")
        assert "Created LCS: LCS_Origin" in _text(result)

    def test_create_lcs_with_reference_requires_reference_object(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "RefBox",
        })
        result = send_command("assembly_operations", {
            "operation": "create_lcs", "name": "LCS1",
            "map_mode": "FlatFace", "reference": "Face1",
        })
        text = _text(result)
        assert "reference_object is required" in text, text[:300]

    def test_create_lcs_attached_to_face(self, clean_document):
        send_command("part_operations", {
            "operation": "box", "length": 10, "width": 10, "height": 10, "name": "RefBox",
        })
        result = send_command("assembly_operations", {
            "operation": "create_lcs", "name": "LCS1", "map_mode": "FlatFace",
            "reference": "Face1", "reference_object": "RefBox",
        })
        text = _text(result)
        assert "Created LCS: LCS1" in text, text[:300]
        assert "reference=RefBox.Face1" in text, text[:300]


class TestAddComponent:
    def test_add_component_success(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "SoloBox",
        })
        result = send_command("assembly_operations", {
            "operation": "add_component", "object_name": "SoloBox", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "Added component:" in text and "SoloBox" in text, text[:300]
        assert "App::Link" in text, text[:300]

    def test_add_component_object_not_found(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        result = send_command("assembly_operations", {
            "operation": "add_component", "object_name": "Ghost", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "not found" in text.lower(), text[:300]


class TestListComponents:
    def test_list_components_empty(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        result = send_command("assembly_operations", {
            "operation": "list_components", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "no components" in text.lower(), text[:300]

    def test_list_components_single(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "OneBox",
        })
        _add_component("OneBox")
        result = send_command("assembly_operations", {
            "operation": "list_components", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "1 total" in text, text[:300]
        assert "OneBox" in text, text[:300]

    def test_list_components_paginated(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        for i in range(3):
            send_command("part_operations", {
                "operation": "box", "length": 5, "width": 5, "height": 5, "name": f"PageBox{i}",
            })
            _add_component(f"PageBox{i}")
        result = send_command("assembly_operations", {
            "operation": "list_components", "assembly_name": "Assy", "limit": 2,
        })
        text = _text(result)
        assert "3 total" in text, text[:300]
        assert "more" in text.lower(), text[:300]


class TestCreateJoint:
    def test_create_fixed_joint(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        result = send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "assembly_name": "Assy",
        })
        text = _text(result)
        assert "Created Fixed joint" in text, text[:300]
        assert f"{comp_a}.Face1" in text and f"{comp_b}.Face1" in text, text[:300]

    def test_create_joint_rejects_unknown_joint_type(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        result = send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Bogus",
            "ref1_object": comp_a, "ref2_object": comp_b,
            "assembly_name": "Assy",
        })
        text = _text(result)
        assert "Unknown joint_type" in text, text[:300]

    def test_create_joint_rejects_out_of_range_face(self, assembly_with_two_boxes):
        """A box has 6 faces — Face99 doesn't exist. This validation is
        newer/hardened (finding #07 in assembly_ops.py); FreeCAD's own
        joint machinery silently accepts a dangling reference otherwise."""
        _doc, comp_a, comp_b = assembly_with_two_boxes
        result = send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face99",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "assembly_name": "Assy",
        })
        text = _text(result)
        assert "Face99" in text and "does not exist" in text, text[:300]

    def test_create_joint_rejects_non_component_object(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "NotAComponent",
        })
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "AlsoNot",
        })
        result = send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": "NotAComponent", "ref2_object": "AlsoNot",
            "assembly_name": "Assy",
        })
        text = _text(result)
        assert "not a component" in text.lower(), text[:300]


class TestGroundPart:
    def test_ground_part_success(self, assembly_with_two_boxes):
        _doc, comp_a, _comp_b = assembly_with_two_boxes
        result = send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        text = _text(result)
        assert f"Grounded {comp_a}" in text, text[:300]

    def test_ground_part_twice_creates_second_grounding(self, assembly_with_two_boxes):
        """list_joints can't be used to observe this: Assembly::AssemblyObject's
        own `.Joints` property never includes GroundedJoint objects in this
        FreeCAD build (confirmed live — a real, non-hypothetical gap in
        list_joints' own docstring claim "List the joints (and grounded
        parts)"; flagged separately, not fixed here). Verify via
        get_part_status (still reports grounded=True) and by counting the
        real GroundedJoint objects in the document directly."""
        _doc, comp_a, _comp_b = assembly_with_two_boxes
        first = send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        second = send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        assert_op_succeeded(first, "ground_part (first)")
        assert_op_succeeded(second, "ground_part (second)")

        status = send_command("assembly_operations", {
            "operation": "get_part_status", "object_name": comp_a, "assembly_name": "Assy",
        })
        assert "grounded=True" in _text(status), _text(status)[:300]

        code = f"""
doc = FreeCAD.getDocument({_doc!r})
count = sum(1 for o in doc.Objects if hasattr(o, 'ObjectToGround')
            and o.ObjectToGround is not None
            and o.ObjectToGround.Name == {comp_a!r})
print(count)
"""
        raw = send_command("execute_python_sync", {"code": code})
        count_text = _text(raw).strip()
        if count_text.startswith("Result: "):
            count_text = count_text[len("Result: "):]
        assert int(count_text) == 2, f"expected 2 GroundedJoint objects, got: {count_text}"


class TestSolve:
    def test_solve_without_grounding_succeeds_trivially(self, assembly_with_two_boxes):
        """Pins actual observed behavior rather than the handler's own
        comment, which is wrong here: assembly_ops.py's _SOLVE_STATUS
        comment claims code=-6 (no_grounded_parts) is reachable, but a real
        two-component assembly with NO grounding — with or without a real
        joint between the components — solves successfully (code=0) on
        this FreeCAD build (confirmed live, both with and without a
        Fixed joint present). Flagged separately as a possible stale
        comment; not fixed here since reproducing -6 needs a scenario this
        exploration didn't find."""
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "solve", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "success" in text and "code=0" in text, text[:300]

    def test_solve_grounded_and_jointed_succeeds(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "solve", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "success" in text and "code=0" in text, text[:300]


class TestListJoints:
    def test_list_joints_none(self, assembly_with_two_boxes):
        _doc, _comp_a, _comp_b = assembly_with_two_boxes
        result = send_command("assembly_operations", {
            "operation": "list_joints", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "no joints" in text.lower(), text[:300]

    def test_list_joints_with_limits_shown(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Cylindrical",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "name": "CylJoint", "assembly_name": "Assy",
        })
        send_command("assembly_operations", {
            "operation": "set_joint_limits", "joint_name": "CylJoint",
            "length_min": 0, "length_max": 20,
        })
        result = send_command("assembly_operations", {
            "operation": "list_joints", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "CylJoint" in text and "limits[" in text, text[:400]
        assert "LengthMin=0" in text and "LengthMax=20" in text, text[:400]


class TestGetPartStatus:
    def test_grounded_only(self, assembly_with_two_boxes):
        _doc, comp_a, _comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "get_part_status", "object_name": comp_a, "assembly_name": "Assy",
        })
        text = _text(result)
        assert "grounded=True" in text, text[:300]

    def test_connected_not_grounded(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "ground_part", "object_name": comp_a, "assembly_name": "Assy",
        })
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "assembly_name": "Assy",
        })
        send_command("assembly_operations", {"operation": "solve", "assembly_name": "Assy"})
        result = send_command("assembly_operations", {
            "operation": "get_part_status", "object_name": comp_b, "assembly_name": "Assy",
        })
        text = _text(result)
        assert "grounded=False" in text, text[:300]
        assert "connected_to_ground=True" in text, text[:300]

    def test_neither_grounded_nor_connected(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "Lonely",
        })
        comp = _add_component("Lonely")
        result = send_command("assembly_operations", {
            "operation": "get_part_status", "object_name": comp, "assembly_name": "Assy",
        })
        text = _text(result)
        assert "grounded=False" in text, text[:300]
        assert "connected_to_ground=False" in text, text[:300]

    def test_rejects_non_component(self, clean_document):
        send_command("assembly_operations", {"operation": "create_assembly", "name": "Assy"})
        send_command("part_operations", {
            "operation": "box", "length": 5, "width": 5, "height": 5, "name": "Bare",
        })
        result = send_command("assembly_operations", {
            "operation": "get_part_status", "object_name": "Bare", "assembly_name": "Assy",
        })
        text = _text(result)
        assert "not a component" in text.lower(), text[:300]


class TestSetJointOffset:
    def test_set_joint_offset(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Fixed",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "name": "OffsetJoint", "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "set_joint_offset", "joint_name": "OffsetJoint",
            "connector": 2, "x": 1, "y": 2, "z": 3, "detach": True,
        })
        text = _text(result)
        assert "Set OffsetJoint.Offset2 = (1,2,3)" in text, text[:300]
        assert "Detach2=True" in text, text[:300]

    def test_set_joint_offset_unknown_joint(self, assembly_with_two_boxes):
        result = send_command("assembly_operations", {
            "operation": "set_joint_offset", "joint_name": "Ghost",
        })
        text = _text(result)
        assert "not found" in text.lower(), text[:300]


class TestSetJointLimits:
    def test_set_length_limits(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Cylindrical",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "name": "LimJoint", "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "set_joint_limits", "joint_name": "LimJoint",
            "length_min": 5, "length_max": 20,
        })
        text = _text(result)
        assert "LengthMin=5" in text and "LengthMax=20" in text, text[:300]

    def test_inverted_limits_rejected_across_two_calls(self, assembly_with_two_boxes):
        """Sets length_min=5 in one call, then a second call tries
        length_max=2 — checked against the already-enabled length_min=5
        from the first call, not just against itself in isolation."""
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Cylindrical",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "name": "LimJoint2", "assembly_name": "Assy",
        })
        first = send_command("assembly_operations", {
            "operation": "set_joint_limits", "joint_name": "LimJoint2", "length_min": 5,
        })
        assert_op_succeeded(first, "set_joint_limits (length_min=5)")

        second = send_command("assembly_operations", {
            "operation": "set_joint_limits", "joint_name": "LimJoint2", "length_max": 2,
        })
        text = _text(second)
        assert "Invalid limits" in text, text[:300]
        assert "5" in text and "2" in text, text[:300]

    def test_no_limits_provided(self, assembly_with_two_boxes):
        _doc, comp_a, comp_b = assembly_with_two_boxes
        send_command("assembly_operations", {
            "operation": "create_joint", "joint_type": "Cylindrical",
            "ref1_object": comp_a, "ref1_element": "Face1",
            "ref2_object": comp_b, "ref2_element": "Face1",
            "name": "LimJoint3", "assembly_name": "Assy",
        })
        result = send_command("assembly_operations", {
            "operation": "set_joint_limits", "joint_name": "LimJoint3",
        })
        text = _text(result)
        assert "No limits provided" in text, text[:300]

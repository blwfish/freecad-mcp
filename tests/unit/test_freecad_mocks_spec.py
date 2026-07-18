"""Tests for the spec= hardening applied to the shared FreeCAD module mocks
in _freecad_mocks.py (H8).

A bare MagicMock() auto-vivifies any attribute access, so a handler calling
a typo'd or nonexistent FreeCAD API name (e.g. FreeCAD.newDoc() instead of
newDocument()) would previously pass every unit test — the mock happily
returns a fresh MagicMock for the typo — yet crash against real FreeCAD.
mock_add_spec() restricts reads on FreeCAD/Part/Draft/Sketcher/Mesh/MeshPart
to the allowlists derived from grepping this repo's actual usage.

This file exists to prove the guard actually does something (a positive
test that an unknown attribute now raises) and that it doesn't collaterally
break access to every name real handler code uses.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Part,
    mock_Draft,
    mock_Sketcher,
    mock_Mesh,
    mock_MeshPart,
    reset_mocks,
)

import pytest


class TestSpecRejectsUnknownAttributes:
    """The actual bug class this closes: before spec=, none of these raised —
    they silently returned a fresh MagicMock, indistinguishable from a real
    API call succeeding."""

    def test_freecad_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_FreeCAD.newDoc  # real API is newDocument

    def test_part_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_Part.makeBoxx  # real API is makeBox

    def test_draft_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_Draft.make_polar_arary  # real API is make_polar_array

    def test_sketcher_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_Sketcher.SketchObjectt

    def test_mesh_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_Mesh.Meshh

    def test_meshpart_typo_raises(self):
        with pytest.raises(AttributeError):
            mock_MeshPart.meshFromShapee


class TestSpecAllowsRealUsage:
    """Every name actually referenced by AICopilot/ source (per the grep the
    allowlists were built from) must remain accessible — the whole point of
    spec= here is name-typo protection, not blocking legitimate calls."""

    def setup_method(self):
        reset_mocks()

    @pytest.mark.parametrize("name", [
        "ActiveDocument", "Console", "Document", "GuiUp", "Matrix",
        "Placement", "Rotation", "Units", "Vector", "Version",
        "getDocument", "getHomePath", "getResourceDir", "getUserAppDataDir",
        "getUserMacroDir", "listDocuments", "newDocument", "openDocument",
        "ParamGet",
    ])
    def test_freecad_known_attribute_accessible(self, name):
        getattr(mock_FreeCAD, name)

    @pytest.mark.parametrize("name", [
        "ArcOfCircle", "Circle", "export", "Face", "insert", "LineSegment",
        "makeBox", "makeCompound", "makePlane", "makeSolid", "makeWireString",
        "Shape",
    ])
    def test_part_known_attribute_accessible(self, name):
        getattr(mock_Part, name)

    @pytest.mark.parametrize("name", [
        "make_clone", "make_ortho_array", "make_path_array",
        "make_point_array", "make_polar_array", "make_text",
    ])
    def test_draft_known_attribute_accessible(self, name):
        getattr(mock_Draft, name)

    def test_ai_global_attrs_settable_and_deletable(self):
        """InitGui.py / headless_server.py stash ad hoc state directly on
        the FreeCAD module (FreeCAD.__ai_socket_server = ...) and later
        hasattr()-check / delattr() it — spec (not spec_set) must not raise
        on assignment or deletion of a name already in the allowlist.

        Uses setattr/delattr with an explicit string rather than literal
        dunder-attribute syntax (mock_FreeCAD.__ai_socket_server = ...)
        because the latter gets Python name-mangled to
        _TestSpecAllowsRealUsage__ai_socket_server inside a class body,
        which would silently test the wrong attribute name entirely.

        Does NOT assert hasattr() is False after delattr(): MagicMock
        auto-vivifies any spec-listed name on first access regardless of
        prior deletion — that's pre-existing MagicMock(spec=[...]) behavior,
        unrelated to and unchanged by this hardening, so asserting real
        del-then-hasattr fidelity here would test something spec was never
        going to provide.
        """
        setattr(mock_FreeCAD, "__ai_socket_server", object())
        delattr(mock_FreeCAD, "__ai_socket_server")


class TestSpecSurvivesResetMocks:
    """reset_mock() must not strip the spec — otherwise the guard only
    holds for the first test in a file and silently disappears after the
    first setUp()/reset_mocks() call."""

    def test_spec_still_active_after_reset(self):
        reset_mocks()
        with pytest.raises(AttributeError):
            mock_FreeCAD.thisAttributeDoesNotExist

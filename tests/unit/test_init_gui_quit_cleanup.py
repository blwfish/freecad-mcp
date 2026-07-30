"""Tests for AICopilot/InitGui.py's aboutToQuit cleanup wiring.

Before this existed, GlobalAIService.stop() -- which removes the discovery
JSON and unlinks the socket file -- only ran when an MCP tool explicitly
called stop_freecad_instance/restart_freecad. A normal Cmd+Q/File>Quit never
invoked it, leaving both behind on disk indefinitely (confirmed via a real
running instance: a dead PID's discovery file and six stale /tmp socket
files accumulated from a single day of normal quits).

_connect_quit_cleanup is tested directly, in isolation from
GlobalAIService.start()'s full dependency chain (which constructs a real
FreeCADSocketServer) -- that's the whole reason it was split out as its own
function rather than left inline.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
INIT_GUI_PATH = os.path.join(AICOPILOT_DIR, "InitGui.py")


def _load_init_gui(fc_module):
    """Import InitGui.py fresh with FreeCAD.GuiUp = True.

    InitGui.py isn't a normal importable package (it's loaded by FreeCAD's
    own workbench mechanism), and it executes module-level GUI-gated code on
    import -- so each test gets its own fresh exec via spec_from_file_location
    rather than relying on Python's module cache.
    """
    fc_module.GuiUp = True
    spec = importlib.util.spec_from_file_location("InitGui_test", INIT_GUI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def test_mode_env(monkeypatch):
    """Skip GlobalAIService's heavy auto-start (real FreeCADSocketServer
    construction) -- irrelevant to what this file is testing."""
    monkeypatch.setenv("FREECAD_MCP_TEST_MODE", "1")


class TestConnectQuitCleanup:
    """`from PySide import QtCore` inside the function resolves via attribute
    access on sys.modules["PySide"] (itself a MagicMock, per conftest's
    mock_freecad fixture) -- attribute access on a MagicMock auto-vivifies a
    child mock, so the thing to configure is sys.modules["PySide"].QtCore,
    not a separately pre-seeded sys.modules["PySide.QtCore"] entry (those are
    two different mock objects; the latter is never consulted here)."""

    def test_connects_stop_callable_to_about_to_quit(self, mock_freecad):
        fake_app = MagicMock()
        qtcore_mock = sys.modules["PySide"].QtCore
        qtcore_mock.QCoreApplication.instance.return_value = fake_app

        module = _load_init_gui(mock_freecad)
        stop_fn = MagicMock()
        module.GlobalAIService._connect_quit_cleanup(stop_fn)

        fake_app.aboutToQuit.connect.assert_called_once_with(stop_fn)

    def test_no_app_instance_does_not_raise(self, mock_freecad):
        """QCoreApplication.instance() returning None (no Qt app running yet)
        must not raise -- there's nothing to hook cleanup to, so skip silently
        rather than crash AICopilot startup over it."""
        qtcore_mock = sys.modules["PySide"].QtCore
        qtcore_mock.QCoreApplication.instance.return_value = None

        module = _load_init_gui(mock_freecad)
        stop_fn = MagicMock()
        module.GlobalAIService._connect_quit_cleanup(stop_fn)  # must not raise

    def test_qtcore_import_failure_does_not_raise(self, mock_freecad, monkeypatch):
        """If PySide can't be imported at all, cleanup wiring is best-effort --
        warn and move on, don't take AICopilot startup down with it. Deleting
        the mocked sys.modules entry makes `from PySide import QtCore` attempt
        a real import, which fails in this FreeCAD-less test environment."""
        monkeypatch.delitem(sys.modules, "PySide", raising=False)
        monkeypatch.delitem(sys.modules, "PySide.QtCore", raising=False)

        module = _load_init_gui(mock_freecad)
        stop_fn = MagicMock()
        module.GlobalAIService._connect_quit_cleanup(stop_fn)  # must not raise
        mock_freecad.Console.PrintWarning.assert_called()

    def test_start_wires_cleanup_via_real_service(self, mock_freecad, monkeypatch):
        """End-to-end (still without a real FreeCADSocketServer): GlobalAIService
        .start() must call _connect_quit_cleanup(self.stop), not just define it."""
        fake_app = MagicMock()
        qtcore_mock = sys.modules["PySide"].QtCore
        qtcore_mock.QCoreApplication.instance.return_value = fake_app

        # Avoid constructing a real FreeCADSocketServer -- out of scope here,
        # covered by freecad_mcp_handler.py's own tests.
        fake_server = MagicMock()
        fake_server.start_server.return_value = True
        fake_server.instance_uuid = "testuuid0001"
        fake_server.socket_path = "/tmp/does_not_matter.sock"
        handler_mock = MagicMock()
        handler_mock.FreeCADSocketServer.return_value = fake_server
        handler_mock.__version__ = "test"
        monkeypatch.setitem(sys.modules, "freecad_mcp_handler", handler_mock)

        instance_registry_mock = MagicMock()
        monkeypatch.setitem(sys.modules, "instance_registry", instance_registry_mock)

        module = _load_init_gui(mock_freecad)
        service = module.GlobalAIService()
        assert service.start() is True

        fake_app.aboutToQuit.connect.assert_called_once_with(service.stop)

    def test_survives_freecads_real_separate_globals_locals_loader(self, mock_freecad, monkeypatch):
        """Regression test for a NameError that shipped to production past
        1700 green tests. FreeCAD's actual workbench loader
        (FreeCADGuiInit.py's DirModGui.run_init_gui, source-confirmed) calls
        bare `exec(code)` inside a method body -- which implicitly uses that
        method's own globals()/locals() as TWO SEPARATE dicts, unlike
        _load_init_gui's importlib.util.exec_module() above (a single shared
        namespace). Under that split, a top-level `def` lands in locals only;
        a *method* referencing it as a free name compiles to LOAD_GLOBAL,
        which only ever consults __globals__ -- never locals -- so it raised
        NameError in real FreeCAD while every test using the importlib loader
        stayed green. _connect_quit_cleanup was moved onto GlobalAIService as
        a staticmethod specifically so it resolves via attribute lookup
        (self._connect_quit_cleanup) instead of __globals__ lookup, sidestepping
        the split entirely. Reproduce the real loader's split explicitly here
        so a future regression fails CI instead of only failing in a live
        FreeCAD process, as this one did.
        """
        fake_app = MagicMock()
        qtcore_mock = sys.modules["PySide"].QtCore
        qtcore_mock.QCoreApplication.instance.return_value = fake_app

        fake_server = MagicMock()
        fake_server.start_server.return_value = True
        fake_server.instance_uuid = "testuuid0002"
        fake_server.socket_path = "/tmp/does_not_matter2.sock"
        handler_mock = MagicMock()
        handler_mock.FreeCADSocketServer.return_value = fake_server
        handler_mock.__version__ = "test"
        monkeypatch.setitem(sys.modules, "freecad_mcp_handler", handler_mock)
        monkeypatch.setitem(sys.modules, "instance_registry", MagicMock())

        mock_freecad.GuiUp = True
        source = open(INIT_GUI_PATH, encoding="utf-8").read()
        code = compile(source, INIT_GUI_PATH, "exec")
        # g mirrors FreeCADGuiInit.py's OWN real module globals -- confirmed
        # via source read to include `import FreeCAD` and `import FreeCADGui`
        # at its own top level, which is exactly why THOSE names resolve fine
        # via LOAD_GLOBAL inside start() in real FreeCAD while a
        # workbench-specific name like _connect_quit_cleanup (never a
        # FreeCADGuiInit.py global) does not. Omitting them here would make
        # this repro fail on the wrong NameError (FreeCAD itself) instead of
        # exercising the bug this test guards against.
        g = {
            "__name__": "InitGui_real_loader_repro",
            "FreeCAD": sys.modules["FreeCAD"],
            "FreeCADGui": sys.modules["FreeCADGui"],
        }
        l = {}
        exec(code, g, l)  # mirrors FreeCADGuiInit.py's bare exec(code) inside a method

        GlobalAIService = l["GlobalAIService"]
        service = GlobalAIService()
        assert service.start() is True

        fake_app.aboutToQuit.connect.assert_called_once_with(service.stop)

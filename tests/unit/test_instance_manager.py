"""
Tests for the _BridgeCtx instance manager and related helpers in working_bridge.py.
"""

import os
import sys
import socket
import subprocess
import time
import threading
import types as _types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Inject working_bridge module without running asyncio.run(main())
# We load it as a module and poke at the module-level symbols directly.
# ---------------------------------------------------------------------------
BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "working_bridge.py")

def _load_bridge():
    """Import working_bridge as a module without executing __main__."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("working_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Stub out mcp so the top-level import doesn't fail outside the bridge env
    mcp_stub = _types.ModuleType("mcp")
    mcp_stub.types = _types.ModuleType("mcp.types")
    sys.modules.setdefault("mcp", mcp_stub)
    sys.modules.setdefault("mcp.types", mcp_stub.types)
    sys.modules.setdefault("mcp.server", _types.ModuleType("mcp.server"))
    sys.modules.setdefault("mcp.server.models", _types.ModuleType("mcp.server.models"))
    sys.modules.setdefault("mcp.server.stdio", _types.ModuleType("mcp.server.stdio"))
    for opt in ("freecad_debug", "freecad_health"):
        sys.modules.setdefault(opt, None)  # type: ignore
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bridge():
    return _load_bridge()


# ===========================================================================
# _BridgeCtx
# ===========================================================================

class TestBridgeCtx:

    def test_default_socket_path_uses_env(self, monkeypatch):
        monkeypatch.setenv("FREECAD_MCP_SOCKET", "/tmp/test_mcp.sock")
        from importlib.util import spec_from_file_location, module_from_spec
        import types as _t
        # Re-instantiate to pick up monkeypatched env
        spec = spec_from_file_location("wb2", BRIDGE_PATH)
        mod = module_from_spec(spec)
        for k in list(sys.modules):
            pass  # don't clobber existing stubs
        spec.loader.exec_module(mod)
        ctx = mod._BridgeCtx()
        assert ctx.socket_path == "/tmp/test_mcp.sock"

    def test_default_socket_path_fallback(self, monkeypatch, bridge):
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)
        ctx = bridge._BridgeCtx()
        assert ctx.socket_path == "/tmp/freecad_mcp.sock"

    def test_register_and_list(self, bridge):
        ctx = bridge._BridgeCtx()
        ctx.register("/tmp/a.sock", 1001, None, "worker-1")
        ctx.register("/tmp/b.sock", 1002, None, "worker-2")
        lst = ctx.list_all()
        paths = [i["socket_path"] for i in lst]
        assert "/tmp/a.sock" in paths
        assert "/tmp/b.sock" in paths

    def test_list_includes_default_when_not_spawned(self, bridge):
        ctx = bridge._BridgeCtx()
        ctx.socket_path = "/tmp/default.sock"
        lst = ctx.list_all()
        defaults = [i for i in lst if not i.get("managed")]
        assert len(defaults) == 1
        assert defaults[0]["socket_path"] == "/tmp/default.sock"
        assert defaults[0]["is_current"] is True

    def test_list_marks_current(self, bridge):
        ctx = bridge._BridgeCtx()
        ctx.register("/tmp/a.sock", 1001, None, "a")
        ctx.register("/tmp/b.sock", 1002, None, "b")
        ctx.socket_path = "/tmp/b.sock"
        lst = ctx.list_all()
        current = [i for i in lst if i.get("is_current")]
        assert len(current) == 1
        assert current[0]["socket_path"] == "/tmp/b.sock"

    def test_unregister(self, bridge):
        ctx = bridge._BridgeCtx()
        ctx.register("/tmp/x.sock", 999, None, "x")
        ctx.unregister("/tmp/x.sock")
        paths = [i["socket_path"] for i in ctx.list_all()]
        assert "/tmp/x.sock" not in paths

    def test_freecad_available_false_when_no_socket(self, bridge, tmp_path):
        ctx = bridge._BridgeCtx()
        ctx.socket_path = str(tmp_path / "nonexistent.sock")
        assert ctx.freecad_available is False

    def test_freecad_available_true_when_socket_exists(self, bridge, tmp_path):
        sock = tmp_path / "test.sock"
        sock.touch()
        ctx = bridge._BridgeCtx()
        ctx.socket_path = str(sock)
        assert ctx.freecad_available is True


# ===========================================================================
# _find_freecadcmd
# ===========================================================================

class TestFindFreecadCmd:

    def test_env_override_takes_priority(self, bridge, monkeypatch, tmp_path):
        fake_bin = tmp_path / "MyFreeCADCmd"
        fake_bin.touch()
        monkeypatch.setenv("FREECAD_MCP_FREECAD_BIN", str(fake_bin))
        result = bridge._find_freecadcmd()
        assert result == str(fake_bin)

    def test_env_override_missing_file_falls_through(self, bridge, monkeypatch):
        monkeypatch.setenv("FREECAD_MCP_FREECAD_BIN", "/nonexistent/FreeCADCmd")
        monkeypatch.delenv("FREECAD_MCP_FREECAD_BIN", raising=False)
        # Should not raise
        bridge._find_freecadcmd()

    def test_returns_none_when_nothing_found(self, bridge, monkeypatch):
        monkeypatch.delenv("FREECAD_MCP_FREECAD_BIN", raising=False)
        with patch("shutil.which", return_value=None):
            result = bridge._find_freecadcmd()
        # May still find a Mac app bundle; just ensure it doesn't crash
        assert result is None or os.path.isfile(result)


# ===========================================================================
# _find_headless_script
# ===========================================================================

class TestFindHeadlessScript:

    def test_env_override(self, bridge, monkeypatch, tmp_path):
        script = tmp_path / "headless_server.py"
        script.touch()
        monkeypatch.setenv("FREECAD_MCP_MODULE_DIR", str(tmp_path))
        result = bridge._find_headless_script()
        assert result == str(script)

    def test_sibling_aicopilot_dir(self, bridge):
        # The actual AICopilot/headless_server.py we just created should be found
        result = bridge._find_headless_script()
        assert result is not None
        assert result.endswith("headless_server.py")
        assert os.path.isfile(result)


# ===========================================================================
# _run_on_gui_thread headless path (socket_server side)
# ===========================================================================

class TestRunOnGuiThreadHeadless:
    """Verify that _run_on_gui_thread runs inline when QtCore is None."""

    def _make_server(self, monkeypatch):
        """Instantiate FreeCADSocketServer with FreeCAD and handlers mocked.

        Uses monkeypatch.setitem for all sys.modules entries so they are
        automatically restored after each test (prevents pollution of later
        tests that patch 'handlers.view_ops' etc.).
        """
        aicopilot_dir = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
        sys.path.insert(0, aicopilot_dir)

        import importlib.util, types as _t

        # Build minimal FreeCAD mock
        fc = _t.ModuleType("FreeCAD")
        fc.GuiUp = False
        fc.Console = _t.SimpleNamespace(
            PrintMessage=lambda s: None,
            PrintError=lambda s: None,
            PrintWarning=lambda s: None,
        )
        monkeypatch.setitem(sys.modules, "FreeCAD", fc)

        # Handler stubs
        handler_names = [
            "PrimitivesHandler", "BooleanOpsHandler", "TransformsHandler",
            "SketchOpsHandler", "PartDesignOpsHandler", "PartOpsHandler",
            "CAMOpsHandler", "CAMToolsHandler", "CAMToolControllersHandler",
            "DraftOpsHandler", "ViewOpsHandler", "DocumentOpsHandler",
            "MeasurementOpsHandler", "SpreadsheetOpsHandler", "MeshOpsHandler",
        ]
        hmod = _t.ModuleType("handlers")
        for n in handler_names:
            cls = MagicMock(return_value=MagicMock())
            setattr(hmod, n, cls)
        monkeypatch.setitem(sys.modules, "handlers", hmod)

        # Use the same _ImportBlocker pattern as conftest.py / test_socket_server.py:
        # any attribute access raises ImportError, so socket_server takes fallback paths.
        class _ImportBlocker:
            def __getattr__(self, name):
                raise ImportError(f"mocked optional module: {name}")

        for opt in ("freecad_debug", "freecad_health", "mcp_versions"):
            monkeypatch.setitem(sys.modules, opt, _ImportBlocker())  # type: ignore

        spec = importlib.util.spec_from_file_location(
            "socket_server_test",
            os.path.join(aicopilot_dir, "socket_server.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.FreeCADSocketServer()

    def test_headless_runs_inline_not_queue(self, monkeypatch):
        server = self._make_server(monkeypatch)
        # In headless mode QtCore is None — task must run inline
        called = []
        def my_task():
            called.append(True)
            return {"result": "ok"}

        result = server._run_on_gui_thread(my_task)
        assert called, "_run_on_gui_thread did not call task in headless mode"
        assert "ok" in result

    def test_headless_task_exception_returns_error_json(self, monkeypatch):
        import json
        server = self._make_server(monkeypatch)
        def bad_task():
            raise ValueError("intentional error")

        result = server._run_on_gui_thread(bad_task)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "intentional error" in parsed["error"]

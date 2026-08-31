"""
Tests for the _BridgeCtx instance manager and related helpers in freecad_mcp_server.py.
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
# Inject freecad_mcp_server module without running asyncio.run(main())
# We load it as a module and poke at the module-level symbols directly.
# ---------------------------------------------------------------------------
BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py")

def _load_bridge():
    """Import freecad_mcp_server as a module without executing __main__."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("freecad_mcp_server", BRIDGE_PATH)
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


@pytest.fixture(autouse=True)
def _isolate_discovery(bridge, tmp_path, monkeypatch):
    """Point DISCOVERY_DIR at an empty tmp path for every test so the host
    machine's real discovery state cannot leak in."""
    monkeypatch.setattr(bridge, "DISCOVERY_DIR", str(tmp_path / "instances"))


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
        # When FREECAD_MCP_SOCKET is unset, the bridge starts with no target;
        # resolve_target() picks an instance lazily from the discovery dir.
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)
        ctx = bridge._BridgeCtx()
        assert ctx.socket_path is None

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

    def test_freecad_available_true_when_socket_listens(self, bridge):
        # freecad_available now probes the socket (connect), not just existence.
        # Bind+listen a real Unix socket so the probe succeeds. Use /tmp
        # directly because AF_UNIX paths are length-limited on macOS.
        import uuid as _uuid
        sock_path = f"/tmp/freecad_mcp_test_{_uuid.uuid4().hex[:8]}.sock"
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            srv.listen(1)
            ctx = bridge._BridgeCtx()
            ctx.socket_path = sock_path
            assert ctx.freecad_available is True
        finally:
            srv.close()
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_freecad_available_windows_probes_instead_of_unconditional_true(self, bridge, monkeypatch):
        """Windows used to return True unconditionally without probing the
        socket — check_freecad_connection (the mandatory pre-flight gate
        CLAUDE.md requires) was a deterministic false positive on an entire
        supported platform whenever nothing was actually listening."""
        monkeypatch.setattr(bridge.platform, "system", lambda: "Windows")
        ctx = bridge._BridgeCtx()
        ctx.socket_path = "127.0.0.1:1"  # port 1 — nothing listens there
        assert ctx.freecad_available is False

    def test_freecad_available_windows_true_when_tcp_listens(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge.platform, "system", lambda: "Windows")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        try:
            ctx = bridge._BridgeCtx()
            ctx.socket_path = f"127.0.0.1:{port}"
            assert ctx.freecad_available is True
        finally:
            srv.close()

    def test_freecad_available_windows_no_socket_path_is_false(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge.platform, "system", lambda: "Windows")
        ctx = bridge._BridgeCtx()
        ctx.socket_path = None
        assert ctx.freecad_available is False


class TestTcpSocketAlive:
    """_tcp_socket_alive backs freecad_available on Windows, where discovery
    is TCP-based (no Unix domain sockets, so _socket_alive doesn't apply)."""

    def test_malformed_host_port_returns_false(self, bridge):
        assert bridge._tcp_socket_alive("not-a-host-port") is False

    def test_non_numeric_port_returns_false(self, bridge):
        assert bridge._tcp_socket_alive("localhost:notaport") is False

    def test_empty_string_returns_false(self, bridge):
        assert bridge._tcp_socket_alive("") is False

    def test_live_tcp_listener_returns_true(self, bridge):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        try:
            assert bridge._tcp_socket_alive(f"127.0.0.1:{port}") is True
        finally:
            srv.close()

    def test_dead_tcp_endpoint_returns_false(self, bridge):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()  # bind-then-close: port is very likely refused now
        assert bridge._tcp_socket_alive(f"127.0.0.1:{port}", timeout=0.2) is False


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

    def test_finds_official_bundle_resources_bin_layout(self, bridge, monkeypatch, tmp_path):
        """Official macOS builds put the real binary under Contents/Resources/bin/
        (lowercase 'freecadcmd'); Contents/MacOS/ there is just a launcher stub.
        Regression test for issue #58."""
        monkeypatch.delenv("FREECAD_MCP_FREECAD_BIN", raising=False)
        bundle = tmp_path / "FreeCAD.app"
        real_bin = bundle / "Contents" / "Resources" / "bin" / "freecadcmd"
        real_bin.parent.mkdir(parents=True)
        real_bin.touch()
        (bundle / "Contents" / "MacOS" / "FreeCAD").parent.mkdir(parents=True)
        (bundle / "Contents" / "MacOS" / "FreeCAD").touch()  # launcher stub, not FreeCADCmd

        with patch("shutil.which", return_value=None), \
             patch.object(bridge.glob, "glob", return_value=[str(bundle)]):
            result = bridge._find_freecadcmd()
        assert result == str(real_bin)

    def test_globs_versioned_app_bundle_names(self, bridge, monkeypatch, tmp_path):
        """Bundle names vary by version ('FreeCAD 1.1.app', 'FreeCAD 26.app', ...);
        the candidate list should come from a glob, not a fixed enumeration."""
        monkeypatch.delenv("FREECAD_MCP_FREECAD_BIN", raising=False)
        with patch("shutil.which", return_value=None), \
             patch.object(bridge.glob, "glob") as glob_mock:
            glob_mock.return_value = []
            bridge._find_freecadcmd()
        glob_mock.assert_any_call("/Applications/FreeCAD*.app")


# ===========================================================================
# _find_freecad_gui
# ===========================================================================

class TestFindFreecadGui:

    def test_env_override_takes_priority(self, bridge, monkeypatch, tmp_path):
        fake_bin = tmp_path / "MyFreeCAD"
        fake_bin.touch()
        monkeypatch.setenv("FREECAD_MCP_FREECAD_GUI_BIN", str(fake_bin))
        result = bridge._find_freecad_gui()
        assert result == str(fake_bin)

    def test_env_override_missing_file_falls_through(self, bridge, monkeypatch):
        monkeypatch.setenv("FREECAD_MCP_FREECAD_GUI_BIN", "/nonexistent/FreeCAD")
        # Should not crash; may pick something else off the system
        bridge._find_freecad_gui()

    def test_returns_none_when_nothing_found(self, bridge, monkeypatch):
        monkeypatch.delenv("FREECAD_MCP_FREECAD_GUI_BIN", raising=False)
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            assert bridge._find_freecad_gui() is None

    def test_macos_skips_shutil_which(self, bridge, monkeypatch):
        """On Darwin, GUI lookup must target the .app inner Mach-O directly —
        going through `open`/PATH would dedupe to an existing process."""
        monkeypatch.delenv("FREECAD_MCP_FREECAD_GUI_BIN", raising=False)
        with patch("platform.system", return_value="Darwin"), \
             patch("shutil.which") as which_mock, \
             patch("os.path.isfile", return_value=False):
            bridge._find_freecad_gui()
            which_mock.assert_not_called()


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

    def test_darwin_globs_versioned_user_mod_dir(self, bridge, monkeypatch, tmp_path):
        """FreeCAD's per-user Mod dir is version-stamped (v1-1, v1-2, v26-3, ...)
        and is exactly what AGENT-INSTALL.md has the caller resolve via
        FreeCAD.getUserAppDataDir(). Regression test for issue #58."""
        monkeypatch.delenv("FREECAD_MCP_MODULE_DIR", raising=False)
        mod_script = tmp_path / "v1-2" / "Mod" / "AICopilot" / "headless_server.py"
        mod_script.parent.mkdir(parents=True)
        mod_script.touch()

        monkeypatch.setattr(bridge.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(bridge.glob, "glob", lambda pattern: [str(mod_script)])
        # Force every higher-priority candidate (dev sibling, ~/.freecad-mcp) to miss
        # so we can prove the new Mod-dir glob candidate is actually reachable.
        monkeypatch.setattr("os.path.isfile", lambda p: p == str(mod_script))

        result = bridge._find_headless_script()
        assert result == str(mod_script)

    def test_darwin_globs_user_mod_dir_pattern(self, bridge, monkeypatch):
        """The glob pattern itself should target ~/Library/Application Support/
        FreeCAD/*/Mod/AICopilot, not a fixed set of hardcoded version strings."""
        monkeypatch.delenv("FREECAD_MCP_MODULE_DIR", raising=False)
        monkeypatch.setattr(bridge.platform, "system", lambda: "Darwin")
        with patch.object(bridge.glob, "glob", return_value=[]) as glob_mock:
            bridge._find_headless_script()
        expected = os.path.expanduser(
            "~/Library/Application Support/FreeCAD/*/Mod/AICopilot/headless_server.py"
        )
        glob_mock.assert_any_call(expected)


# ===========================================================================
# _run_on_gui_thread headless path (freecad_mcp_handler side)
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
            "SpatialOpsHandler", "InspectorOpsHandler",
            "MacroOpsHandler", "IntrospectionOpsHandler", "SketchBuilderOpsHandler",
            "VerificationOpsHandler", "FixtureOpsHandler", "DiagnosticsOpsHandler",
            "ExecutePythonOpsHandler", "AssemblyOpsHandler", "VarSetOpsHandler",
        ]
        hmod = _t.ModuleType("handlers")
        for n in handler_names:
            cls = MagicMock(return_value=MagicMock())
            setattr(hmod, n, cls)
        monkeypatch.setitem(sys.modules, "handlers", hmod)

        # Use the same _ImportBlocker pattern as conftest.py / test_freecad_mcp_handler.py:
        # any attribute access raises ImportError, so freecad_mcp_handler takes fallback paths.
        class _ImportBlocker:
            def __getattr__(self, name):
                raise ImportError(f"mocked optional module: {name}")

        for opt in ("freecad_debug", "freecad_health", "mcp_versions"):
            monkeypatch.setitem(sys.modules, opt, _ImportBlocker())  # type: ignore

        spec = importlib.util.spec_from_file_location(
            "freecad_mcp_handler_test",
            os.path.join(aicopilot_dir, "freecad_mcp_handler.py"),
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

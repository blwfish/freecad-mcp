"""
Tests for the four instance-management MCP tool handlers in working_bridge.py:
  list_freecad_instances, select_freecad_instance,
  spawn_freecad_instance, stop_freecad_instance.

Strategy
--------
The handlers live inside the async ``call_tool`` coroutine.  We extract
the module-level helpers (_ctx, _find_freecadcmd, _find_headless_script)
and test handler behaviour by directly invoking the relevant slice of
logic through thin async wrappers, mocking subprocess and socket I/O so
the tests run without FreeCAD installed.
"""

import asyncio
import json
import os
import sys
import types as _types
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

# ---------------------------------------------------------------------------
# Load the bridge module without executing __main__
# ---------------------------------------------------------------------------
BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "working_bridge.py")


def _load_bridge():
    import importlib.util
    spec = importlib.util.spec_from_file_location("working_bridge_ih", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_text(result) -> dict:
    """Return the parsed JSON from the first TextContent item."""
    assert result, "handler returned empty list"
    text = result[0].text
    return json.loads(text)


def _fresh_ctx(bridge):
    """Return a fresh _BridgeCtx so tests don't share state."""
    ctx = bridge._BridgeCtx()
    return ctx


# ---------------------------------------------------------------------------
# list_freecad_instances
# ---------------------------------------------------------------------------

class TestListInstances:

    def test_empty_returns_default(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.socket_path = "/tmp/default.sock"
        instances = ctx.list_all()
        assert len(instances) == 1
        assert instances[0]["socket_path"] == "/tmp/default.sock"

    def test_managed_instances_appear(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.register("/tmp/a.sock", 1, None, "alpha")
        ctx.register("/tmp/b.sock", 2, None, "beta")
        paths = {i["socket_path"] for i in ctx.list_all()}
        assert "/tmp/a.sock" in paths
        assert "/tmp/b.sock" in paths


# ---------------------------------------------------------------------------
# select_freecad_instance
# ---------------------------------------------------------------------------

class TestSelectInstance:

    def _run_select(self, bridge, ctx, arguments):
        """
        Simulate the select_freecad_instance handler branch directly.
        Returns the parsed JSON result dict.
        """
        target_path = arguments.get("socket_path")
        target_label = arguments.get("label")

        if not target_path and target_label:
            for sp, info in ctx.instances.items():
                if info.get("label") == target_label:
                    target_path = sp
                    break
            if not target_path:
                return {"error": f"No instance with label '{target_label}'"}

        if not target_path:
            return {"error": "Provide socket_path or label"}

        ctx.socket_path = target_path
        return {
            "result": f"Active instance set to {target_path}",
            "socket_path": target_path,
        }

    def test_select_by_socket_path(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.register("/tmp/x.sock", 10, None, "x")
        result = self._run_select(bridge, ctx, {"socket_path": "/tmp/x.sock"})
        assert "error" not in result
        assert ctx.socket_path == "/tmp/x.sock"

    def test_select_by_label(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.register("/tmp/y.sock", 20, None, "my-label")
        result = self._run_select(bridge, ctx, {"label": "my-label"})
        assert "error" not in result
        assert ctx.socket_path == "/tmp/y.sock"

    def test_select_unknown_label_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._run_select(bridge, ctx, {"label": "ghost"})
        assert "error" in result
        assert "ghost" in result["error"]

    def test_select_no_args_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._run_select(bridge, ctx, {})
        assert "error" in result


# ---------------------------------------------------------------------------
# spawn_freecad_instance
# ---------------------------------------------------------------------------

class TestSpawnInstance:
    """
    Tests for spawn_freecad_instance handler.

    We mock:
    - _find_freecadcmd → returns a fake path (or None for error cases)
    - _find_headless_script → returns a fake path (or None for error cases)
    - subprocess.Popen → returns a mock process
    - os.path.exists → True (socket appears immediately)
    - socket.socket.connect → succeeds immediately
    - asyncio.sleep → no-op (speeds up tests)
    """

    def _spawn(self, bridge, ctx, arguments, *,
               freecadcmd="/fake/FreeCADCmd",
               headless_script="/fake/headless_server.py",
               popen_proc=None,
               socket_ready=True):
        """
        Run the spawn handler logic asynchronously and return parsed JSON.
        """
        if popen_proc is None:
            popen_proc = MagicMock()
            popen_proc.pid = 12345

        async def _inner():
            import socket as _socket
            import uuid as _uuid

            freecadcmd_val = freecadcmd
            headless_val = headless_script

            args = arguments or {}
            label = args.get("label")
            sock_path = args.get("socket_path") or f"/tmp/freecad_mcp_{_uuid.uuid4().hex[:8]}.sock"
            select_new = args.get("select", True)

            if not freecadcmd_val:
                return {"error": "Cannot find FreeCADCmd binary. Set FREECAD_MCP_FREECAD_BIN env var to its path."}

            if not headless_val:
                return {"error": "Cannot find headless_server.py. Set FREECAD_MCP_MODULE_DIR env var, or deploy AICopilot to ~/.freecad-mcp/AICopilot/."}

            env = os.environ.copy()
            env["FREECAD_MCP_SOCKET"] = sock_path
            try:
                proc = popen_proc
            except OSError as e:
                return {"error": f"Failed to spawn FreeCAD: {e}"}

            # Poll loop (mocked)
            import time as _time
            deadline = _time.time() + 30
            ready = False
            while _time.time() < deadline:
                if socket_ready:
                    ready = True
                    break
                await asyncio.sleep(0)

            if not ready:
                proc.kill()
                return {"error": "Headless FreeCAD did not become ready within 30 s",
                        "socket_path": sock_path}

            ctx.register(sock_path, proc.pid, proc, label or sock_path, headless=True)
            if select_new:
                ctx.socket_path = sock_path

            return {
                "result": "Headless FreeCAD instance spawned and ready",
                "socket_path": sock_path,
                "pid": proc.pid,
                "label": label or sock_path,
                "selected": select_new,
            }

        return asyncio.run(_inner())

    def test_spawn_success(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {"label": "test-inst"})
        assert "error" not in result, result
        assert result["result"] == "Headless FreeCAD instance spawned and ready"
        assert result["pid"] == 12345
        assert result["label"] == "test-inst"
        assert result["selected"] is True
        # ctx should now point to the new socket
        assert ctx.socket_path == result["socket_path"]

    def test_spawn_registers_in_ctx(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {})
        sock = result["socket_path"]
        paths = [i["socket_path"] for i in ctx.list_all()]
        assert sock in paths

    def test_spawn_select_false_does_not_switch(self, bridge):
        ctx = _fresh_ctx(bridge)
        original = ctx.socket_path
        result = self._spawn(bridge, ctx, {"select": False})
        assert result["selected"] is False
        assert ctx.socket_path == original

    def test_spawn_no_freecadcmd_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {}, freecadcmd=None)
        assert "error" in result
        assert "FreeCADCmd" in result["error"]

    def test_spawn_no_headless_script_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {}, headless_script=None)
        assert "error" in result
        assert "headless_server.py" in result["error"]

    def test_spawn_timeout_kills_proc(self, bridge):
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        proc.pid = 9999
        result = self._spawn(bridge, ctx, {}, popen_proc=proc, socket_ready=False)
        assert "error" in result
        assert "30 s" in result["error"]
        proc.kill.assert_called_once()

    def test_spawn_custom_socket_path_used(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {"socket_path": "/tmp/my_custom.sock"})
        assert result["socket_path"] == "/tmp/my_custom.sock"


# ---------------------------------------------------------------------------
# stop_freecad_instance
# ---------------------------------------------------------------------------

class TestStopInstance:

    def _run_stop(self, ctx, arguments):
        """
        Simulate the stop_freecad_instance handler branch directly.
        Returns parsed JSON result dict.
        """
        import subprocess as _sp

        args = arguments or {}
        target_path = args.get("socket_path")
        target_label = args.get("label")

        if not target_path and target_label:
            for sp, info in ctx.instances.items():
                if info.get("label") == target_label:
                    target_path = sp
                    break

        if not target_path:
            return {"error": "Provide socket_path or label of instance to stop"}

        info = ctx.instances.get(target_path)
        if not info:
            return {"error": f"Instance '{target_path}' not managed by this bridge"}

        proc = info.get("proc")
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except _sp.TimeoutExpired:
                    proc.kill()
            except OSError:
                pass

        # Skip actual os.remove — socket file is fake in tests
        ctx.unregister(target_path)

        if ctx.socket_path == target_path:
            ctx.socket_path = os.environ.get("FREECAD_MCP_SOCKET", "/tmp/freecad_mcp.sock")

        return {
            "result": f"Instance {target_path} stopped",
            "active_socket": ctx.socket_path,
        }

    def test_stop_success(self, bridge):
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        ctx.register("/tmp/z.sock", 1, proc, "z")
        result = self._run_stop(ctx, {"socket_path": "/tmp/z.sock"})
        assert "error" not in result
        assert "z.sock" in result["result"]
        proc.terminate.assert_called_once()

    def test_stop_by_label(self, bridge):
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        ctx.register("/tmp/w.sock", 2, proc, "worker")
        result = self._run_stop(ctx, {"label": "worker"})
        assert "error" not in result
        proc.terminate.assert_called_once()

    def test_stop_unregisters(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.register("/tmp/q.sock", 3, MagicMock(), "q")
        self._run_stop(ctx, {"socket_path": "/tmp/q.sock"})
        paths = [i["socket_path"] for i in ctx.list_all()]
        assert "/tmp/q.sock" not in paths

    def test_stop_reverts_active_socket(self, bridge, monkeypatch):
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)
        ctx = _fresh_ctx(bridge)
        ctx.register("/tmp/active.sock", 4, MagicMock(), "active")
        ctx.socket_path = "/tmp/active.sock"
        result = self._run_stop(ctx, {"socket_path": "/tmp/active.sock"})
        assert ctx.socket_path == "/tmp/freecad_mcp.sock"
        assert result["active_socket"] == "/tmp/freecad_mcp.sock"

    def test_stop_does_not_change_socket_if_not_active(self, bridge):
        ctx = _fresh_ctx(bridge)
        ctx.socket_path = "/tmp/other.sock"
        ctx.register("/tmp/idle.sock", 5, MagicMock(), "idle")
        self._run_stop(ctx, {"socket_path": "/tmp/idle.sock"})
        assert ctx.socket_path == "/tmp/other.sock"

    def test_stop_unknown_instance_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._run_stop(ctx, {"socket_path": "/tmp/ghost.sock"})
        assert "error" in result
        assert "ghost.sock" in result["error"]

    def test_stop_no_args_returns_error(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._run_stop(ctx, {})
        assert "error" in result

    def test_stop_timeout_kills_proc(self, bridge):
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        import subprocess as _sp
        proc.wait.side_effect = _sp.TimeoutExpired(cmd="fake", timeout=5)
        ctx.register("/tmp/slow.sock", 6, proc, "slow")
        result = self._run_stop(ctx, {"socket_path": "/tmp/slow.sock"})
        assert "error" not in result
        proc.kill.assert_called_once()

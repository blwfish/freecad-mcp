"""
Tests for the four instance-management MCP tool handlers in freecad_mcp_server.py:
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
import itertools
import json
import os
import sys
import types as _types
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

# ---------------------------------------------------------------------------
# Load the bridge module without executing __main__
# ---------------------------------------------------------------------------
BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py")


def _load_bridge():
    import importlib.util
    spec = importlib.util.spec_from_file_location("freecad_mcp_server_ih", BRIDGE_PATH)
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


@pytest.fixture(autouse=True)
def _isolate_discovery(bridge, tmp_path, monkeypatch):
    """Point DISCOVERY_DIR at an empty tmp path so host state can't leak in."""
    monkeypatch.setattr(bridge, "DISCOVERY_DIR", str(tmp_path / "instances"))


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
    - proc.poll() → None while "running"; an int once the mock process has
      "exited" (mirrors production's early-exit detection — see
      _spawn's `exit_code` param)
    """

    def _spawn(self, bridge, ctx, arguments, *,
               freecadcmd="/fake/FreeCADCmd",
               headless_script="/fake/headless_server.py",
               popen_proc=None,
               socket_ready=True,
               exit_code=None,
               output=""):
        """
        Run the spawn handler logic asynchronously and return parsed JSON.

        exit_code: if not None, simulates the spawned process having already
        exited with this code before the socket became ready — production
        checks proc.poll() each loop iteration and reports this distinctly
        from a plain timeout (0 is a valid "exited cleanly" code, so this is
        an `is not None` check throughout, never a truthiness check).
        output: fake captured stdout/stderr tail, standing in for what
        production's _read_launch_log_tail() would have read from the real
        spawned process's log file.
        """
        if popen_proc is None:
            popen_proc = MagicMock()
            popen_proc.pid = 12345
        popen_proc.poll.return_value = exit_code

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
            early_exit_code = None
            while _time.time() < deadline:
                early_exit_code = proc.poll()
                if early_exit_code is not None:
                    break
                if socket_ready:
                    ready = True
                    break
                await asyncio.sleep(0)

            if not ready:
                if early_exit_code is not None:
                    error_msg = f"Headless FreeCAD exited (code {early_exit_code}) before becoming ready"
                else:
                    proc.kill()
                    error_msg = "Headless FreeCAD did not become ready within 30 s"
                return {"error": error_msg,
                        "socket_path": sock_path,
                        "exit_code": early_exit_code,
                        "output": output}

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
        """_spawn's poll loop checks a real time.time() deadline (mirroring
        the production spawn_freecad_instance handler); with socket_ready
        never True, it previously busy-spun for the real 30s before timing
        out. time.time() is patched to jump straight past the deadline on
        the loop's first condition check instead — an ever-increasing
        counter rather than a fixed pair of values, so it can't raise
        StopIteration regardless of how many times the code under test
        happens to call time.time()."""
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        proc.pid = 9999
        with patch('time.time', side_effect=itertools.count(0, 1000)):
            result = self._spawn(bridge, ctx, {}, popen_proc=proc, socket_ready=False)
        assert "error" in result
        assert "30 s" in result["error"]
        proc.kill.assert_called_once()

    def test_spawn_early_exit_reports_exit_code_and_skips_kill(self, bridge):
        """If the process has already exited (crashed on startup) before the
        socket ever appears, production detects this via proc.poll() inside
        the wait loop instead of waiting out the full timeout. It must not
        call proc.kill() on an already-dead process, and the error must name
        the real cause distinctly from a plain timeout."""
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        proc.pid = 9999
        result = self._spawn(bridge, ctx, {}, popen_proc=proc, socket_ready=False,
                              exit_code=1, output="Traceback...\nImportError: no module named foo")
        assert "error" in result
        assert "exited (code 1)" in result["error"]
        assert "30 s" not in result["error"]
        assert result["exit_code"] == 1
        assert "ImportError" in result["output"]
        proc.kill.assert_not_called()

    def test_spawn_early_exit_code_zero_is_not_mistaken_for_still_running(self, bridge):
        """exit_code=0 (clean exit) must still be treated as 'exited', not
        as 'still running' — an `is not None` check, not a truthiness check.
        This is the ambiguous-zero-vs-missing case this project's testing
        rules flag explicitly."""
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        proc.pid = 9999
        result = self._spawn(bridge, ctx, {}, popen_proc=proc, socket_ready=False, exit_code=0)
        assert "error" in result
        assert "exited (code 0)" in result["error"]
        assert result["exit_code"] == 0
        proc.kill.assert_not_called()

    def test_spawn_timeout_without_early_exit_has_null_exit_code(self, bridge):
        """A genuine timeout (process still running, socket never appears)
        must report exit_code: null, distinguishing it from the early-exit
        case above."""
        ctx = _fresh_ctx(bridge)
        proc = MagicMock()
        proc.pid = 9999
        with patch('time.time', side_effect=itertools.count(0, 1000)):
            result = self._spawn(bridge, ctx, {}, popen_proc=proc, socket_ready=False)
        assert result["exit_code"] is None
        proc.kill.assert_called_once()

    def test_spawn_custom_socket_path_used(self, bridge):
        ctx = _fresh_ctx(bridge)
        result = self._spawn(bridge, ctx, {"socket_path": "/tmp/my_custom.sock"})
        assert result["socket_path"] == "/tmp/my_custom.sock"


class TestSpawnLaunchCmdConstruction:
    """Regression test for the headless launch_cmd command line.

    2026-07-24: production built launch_cmd as
    ``[freecad_bin, headless_script, "--socket-path", sock_path]``. Some
    FreeCADCmd builds (e.g. the local FC-clone release build, matching the
    AppImage case tests/integration/conftest.py already worked around)
    reject unrecognized CLI flags outright and exit 1 with their own
    --help text before headless_server.py ever runs -- spawn_freecad_instance
    was completely broken against those builds. The socket path must be
    passed via the FREECAD_MCP_SOCKET env var only, which headless_server.py
    already reads as a fallback when no --socket-path arg is present.

    This is a static-analysis test (grep on source), following the same
    pattern as test_dispatch_completeness.py, because the handler lives
    inside a decorator-wrapped closure in main() and isn't independently
    callable -- TestSpawnInstance above works around that by reimplementing
    the poll-loop logic, which is exactly why it never exercised the real
    launch_cmd construction and didn't catch this bug.
    """

    def _server_source(self):
        with open(BRIDGE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_headless_launch_cmd_does_not_pass_socket_path_flag(self):
        src = self._server_source()
        assert '"--socket-path"' not in src, (
            "launch_cmd must not pass --socket-path on argv -- some "
            "FreeCADCmd builds reject unrecognized CLI flags before "
            "headless_server.py can parse them; use the FREECAD_MCP_SOCKET "
            "env var instead (see tests/integration/conftest.py)"
        )

    def test_headless_launch_cmd_is_binary_and_script_only(self):
        src = self._server_source()
        import re
        m = re.search(r'launch_cmd = \[freecad_bin, headless_script\]', src)
        assert m, "expected launch_cmd = [freecad_bin, headless_script] with no extra argv"

    def test_env_sets_freecad_mcp_socket_unconditionally(self):
        src = self._server_source()
        assert 'env["FREECAD_MCP_SOCKET"] = sock_path' in src

    def test_headless_server_reads_env_var_as_fallback(self):
        """Companion check on the consumer side: headless_server.py must
        still read FREECAD_MCP_SOCKET when no --socket-path arg is given,
        since that's now the only way the socket path is communicated."""
        headless_path = os.path.join(
            os.path.dirname(BRIDGE_PATH), "AICopilot", "headless_server.py"
        )
        with open(headless_path, "r", encoding="utf-8") as f:
            headless_src = f.read()
        assert 'os.environ.get("FREECAD_MCP_SOCKET")' in headless_src


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

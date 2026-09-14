"""Parity test: freecad_mcp_server._scan_discovery vs. instance_registry.scan_discovery.

These are two independent implementations of the same discovery-directory
scan, living in separate processes/installs that can't reliably import each
other (the bridge deploys to ~/.freecad-mcp/, AICopilot/ deploys into the
FreeCAD Mod directory — see the docstring on each scan_discovery for why).
Nothing in the type system or import graph enforces they agree; a divergence
here is invisible to every other test in the suite, since each side's own
tests only exercise its own copy.

Regression: the bridge's copy diverged on exactly the forward-compatibility
case the canonical instance_registry.py version was written to protect — a
record missing socket_path (e.g. a newer bridge using a renamed key) was
silently deleted instead of preserved. instance_registry.scan_discovery was
called only by this test suite; production used the bridge's copy
exclusively, so the divergence was invisible to CI.
"""

import json
import os
import socket
import subprocess
import sys
import uuid
import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import instance_registry  # noqa: E402
import freecad_mcp_server  # noqa: E402

import tests.unit._freecad_mocks  # noqa: E402,F401 -- installs FreeCAD/Part/etc. mocks into sys.modules, required before importing freecad_mcp_handler below
import freecad_mcp_handler  # noqa: E402


@pytest.fixture
def dead_pid():
    """A pid guaranteed to not refer to a running process. Spawned via
    subprocess (not raw os.fork) since pytest itself is multi-threaded and
    forking a multi-threaded process risks deadlock."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def isolated_dirs(monkeypatch, tmp_path):
    """Point both implementations' DISCOVERY_DIR at the same fresh tmp path."""
    target = str(tmp_path / "instances")
    os.makedirs(target, exist_ok=True)
    monkeypatch.setattr(instance_registry, "DISCOVERY_DIR", target)
    monkeypatch.setattr(freecad_mcp_server, "DISCOVERY_DIR", target)
    return target


@pytest.fixture
def listen_sock():
    """Yield a (sock_path, server_socket) pair. Server is listening so probes succeed."""
    sock_path = f"/tmp/freecad_mcp_test_{uuid.uuid4().hex[:8]}.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv.bind(sock_path)
    # Backlog > 1: each parity test connects twice (once per implementation)
    # without ever accept()ing, so a backlog of 1 leaves the second probe's
    # connection refused/reset once the first fills the accept queue.
    srv.listen(5)
    try:
        yield sock_path, srv
    finally:
        srv.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


def _write(directory, name, content):
    """Write content to a discovery file. A str is written verbatim (used
    for the deliberately-invalid-JSON case); anything else is JSON-encoded."""
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        if isinstance(content, str):
            f.write(content)
        else:
            json.dump(content, f)
    return path


class TestPidAliveParity:
    """instance_registry.is_pid_alive and freecad_mcp_server._pid_alive are
    independent implementations of the same check (same reason as
    scan_discovery above: separate processes/installs, can't import each
    other) -- must agree on every input."""

    @pytest.mark.parametrize("pid", [None, 0, -1, "12345", 1.5])
    def test_agree_on_invalid_pids(self, pid):
        assert instance_registry.is_pid_alive(pid) is False
        assert freecad_mcp_server._pid_alive(pid) is False

    def test_agree_current_process_is_alive(self):
        assert instance_registry.is_pid_alive(os.getpid()) is True
        assert freecad_mcp_server._pid_alive(os.getpid()) is True

    def test_agree_reaped_child_is_dead(self, dead_pid):
        assert instance_registry.is_pid_alive(dead_pid) is False
        assert freecad_mcp_server._pid_alive(dead_pid) is False

    def test_agree_both_dispatch_to_windows_specific_check_on_win32(self, monkeypatch):
        """Both independent implementations must avoid os.kill(pid, 0) on
        Windows (signal 0 maps to CTRL_C_EVENT there, not a liveness
        probe) and dispatch to their own _is_pid_alive_windows helper
        instead."""
        monkeypatch.setattr(instance_registry.sys, "platform", "win32")
        monkeypatch.setattr(freecad_mcp_server.sys, "platform", "win32")
        monkeypatch.setattr(instance_registry, "_is_pid_alive_windows", lambda pid: True)
        monkeypatch.setattr(freecad_mcp_server, "_is_pid_alive_windows", lambda pid: True)

        def fail_kill(pid, sig):
            raise AssertionError("must not call os.kill on Windows")
        monkeypatch.setattr(instance_registry.os, "kill", fail_kill)
        monkeypatch.setattr(freecad_mcp_server.os, "kill", fail_kill)

        assert instance_registry.is_pid_alive(4321) is True
        assert freecad_mcp_server._pid_alive(4321) is True


class TestWindowsAuthTokenPathParity:
    """freecad_mcp_handler.WINDOWS_AUTH_TOKEN_PATH and
    freecad_mcp_server.WINDOWS_AUTH_TOKEN_PATH are two independently
    computed constants for the same shared-secret file (same reason as
    the other parity classes in this file: separate processes/installs,
    no shared-import chokepoint) -- a security-relevant path (this pins
    where the Windows TCP auth token, added to close CWE-306, actually
    lives). Before this test, only the bridge side was pinned against a
    hardcoded literal string; a drift introduced only in the handler's
    own computation (WINDOWS_AUTH_TOKEN_DIR + "windows_auth_token") would
    have gone completely uncaught by any test, and Windows isn't in the
    CI matrix either, so no CI run would have caught it live."""

    def test_both_sides_compute_the_same_path(self):
        assert freecad_mcp_handler.WINDOWS_AUTH_TOKEN_PATH == freecad_mcp_server.WINDOWS_AUTH_TOKEN_PATH

    def test_both_sides_agree_with_the_documented_literal(self):
        expected = os.path.expanduser("~/.freecad-mcp/windows_auth_token")
        assert freecad_mcp_handler.WINDOWS_AUTH_TOKEN_PATH == expected
        assert freecad_mcp_server.WINDOWS_AUTH_TOKEN_PATH == expected


class TestScanDiscoveryParity:
    """Run both implementations against identical directories; results and
    on-disk side effects (which files got pruned) must match exactly."""

    def test_live_socket_record_kept_by_both(self, isolated_dirs, listen_sock):
        sock_path, _srv = listen_sock
        _write(isolated_dirs, "a.json", {"uuid": "a", "socket_path": sock_path})

        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == [{"uuid": "a", "socket_path": sock_path}]
        assert os.path.exists(os.path.join(isolated_dirs, "a.json"))

        # Re-seed: the canonical call above didn't mutate this record (still live).
        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == [{"uuid": "a", "socket_path": sock_path}]
        assert os.path.exists(os.path.join(isolated_dirs, "a.json"))

    def test_dead_socket_record_pruned_by_both(self, isolated_dirs):
        dead_path = "/tmp/freecad_mcp_definitely_dead_9f8e7d.sock"
        assert not os.path.exists(dead_path)
        _write(isolated_dirs, "dead.json", {"uuid": "d", "socket_path": dead_path})

        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == []
        assert not os.path.exists(os.path.join(isolated_dirs, "dead.json"))

        _write(isolated_dirs, "dead2.json", {"uuid": "d2", "socket_path": dead_path})
        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == []
        assert not os.path.exists(os.path.join(isolated_dirs, "dead2.json"))

    def test_missing_socket_path_preserved_by_both(self, isolated_dirs):
        """The core regression: a forward-compat record (no socket_path) must
        survive prune_stale=True on BOTH sides, not just the canonical one."""
        _write(isolated_dirs, "future.json", {"uuid": "f", "renamed_key": "x"})

        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == []  # not "live" (no socket to probe) ...
        assert os.path.exists(os.path.join(isolated_dirs, "future.json"))  # ... but not deleted

        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == []
        assert os.path.exists(os.path.join(isolated_dirs, "future.json"))

    def test_corrupt_json_pruned_by_both(self, isolated_dirs):
        _write(isolated_dirs, "corrupt.json", "{not valid json")

        instance_registry.scan_discovery(prune_stale=True)
        assert not os.path.exists(os.path.join(isolated_dirs, "corrupt.json"))

        _write(isolated_dirs, "corrupt2.json", "{not valid json")
        freecad_mcp_server._scan_discovery(prune_stale=True)
        assert not os.path.exists(os.path.join(isolated_dirs, "corrupt2.json"))

    def test_non_dict_json_skipped_without_crashing_either(self, isolated_dirs):
        """A JSON list/number/null must not raise AttributeError out of
        data.get(...) and abort the scan for every other record in the
        directory."""
        _write(isolated_dirs, "list.json", ["not", "a", "record"])
        _write(isolated_dirs, "number.json", 42)
        _write(isolated_dirs, "null.json", None)

        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == []  # did not raise
        for name in ("list.json", "number.json", "null.json"):
            assert os.path.exists(os.path.join(isolated_dirs, name))

        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == []  # did not raise
        for name in ("list.json", "number.json", "null.json"):
            assert os.path.exists(os.path.join(isolated_dirs, name))

    def test_busy_but_alive_pid_not_pruned_by_either(self, isolated_dirs, tmp_path):
        """The pid-liveness fallback both implementations must share: a
        dead socket whose recorded pid is confirmed alive (this test
        process's own) must NOT be pruned by either side -- a failed
        connect probe alone isn't proof of death (see both scan_discovery
        docstrings)."""
        stale_sock = str(tmp_path / "busy.sock")
        with open(stale_sock, "w"):
            pass
        _write(isolated_dirs, "busy.json", {
            "uuid": "busy", "socket_path": stale_sock, "pid": os.getpid(),
        })

        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == []  # not reported live (socket really is dead)
        assert os.path.exists(os.path.join(isolated_dirs, "busy.json"))
        assert os.path.exists(stale_sock)  # NOT unlinked

        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == []
        assert os.path.exists(os.path.join(isolated_dirs, "busy.json"))

    def test_dead_socket_and_dead_pid_pruned_by_both(self, isolated_dirs, tmp_path, dead_pid):
        """Regression guard shared by both implementations: when the pid
        is ALSO confirmed dead, pruning still happens exactly as before
        this fix -- including removal of the orphaned socket FILE itself,
        not just the discovery JSON record. A prior version of this test
        reused one socket path across both calls and only ever asserted
        the JSON record was gone; that masked a real divergence where the
        bridge-side implementation never removed the socket file at all
        (only instance_registry.py's copy did) -- the second call's file
        had already been deleted by the first call before the bridge ever
        got a chance to prove it could do the same. Using a distinct
        socket path per side closes that gap."""
        canonical_sock = str(tmp_path / "reallydead_canonical.sock")
        with open(canonical_sock, "w"):
            pass
        _write(isolated_dirs, "dead.json", {
            "uuid": "d", "socket_path": canonical_sock, "pid": dead_pid,
        })
        canonical = instance_registry.scan_discovery(prune_stale=True)
        assert canonical == []
        assert not os.path.exists(os.path.join(isolated_dirs, "dead.json"))
        assert not os.path.exists(canonical_sock), "orphaned socket file must be removed too"

        bridge_sock = str(tmp_path / "reallydead_bridge.sock")
        with open(bridge_sock, "w"):
            pass
        _write(isolated_dirs, "dead2.json", {
            "uuid": "d2", "socket_path": bridge_sock, "pid": dead_pid,
        })
        bridge = freecad_mcp_server._scan_discovery(prune_stale=True)
        assert bridge == []
        assert not os.path.exists(os.path.join(isolated_dirs, "dead2.json"))
        assert not os.path.exists(bridge_sock), "orphaned socket file must be removed too"

    def test_prune_stale_false_leaves_dead_record_on_both(self, isolated_dirs):
        dead_path = "/tmp/freecad_mcp_definitely_dead_9f8e7d.sock"
        _write(isolated_dirs, "dead.json", {"uuid": "d", "socket_path": dead_path})

        canonical = instance_registry.scan_discovery(prune_stale=False)
        assert canonical == []
        assert os.path.exists(os.path.join(isolated_dirs, "dead.json"))

        bridge = freecad_mcp_server._scan_discovery(prune_stale=False)
        assert bridge == []
        assert os.path.exists(os.path.join(isolated_dirs, "dead.json"))

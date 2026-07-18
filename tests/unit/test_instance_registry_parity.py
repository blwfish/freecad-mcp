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
import sys
import uuid
import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import instance_registry  # noqa: E402
import freecad_mcp_server  # noqa: E402


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

    def test_prune_stale_false_leaves_dead_record_on_both(self, isolated_dirs):
        dead_path = "/tmp/freecad_mcp_definitely_dead_9f8e7d.sock"
        _write(isolated_dirs, "dead.json", {"uuid": "d", "socket_path": dead_path})

        canonical = instance_registry.scan_discovery(prune_stale=False)
        assert canonical == []
        assert os.path.exists(os.path.join(isolated_dirs, "dead.json"))

        bridge = freecad_mcp_server._scan_discovery(prune_stale=False)
        assert bridge == []
        assert os.path.exists(os.path.join(isolated_dirs, "dead.json"))

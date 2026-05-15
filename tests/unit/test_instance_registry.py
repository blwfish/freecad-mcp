"""Tests for AICopilot/instance_registry.py — discovery file write/scan/prune."""

import json
import os
import socket
import sys
import uuid
import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)

import instance_registry  # noqa: E402


@pytest.fixture
def isolated_dir(monkeypatch, tmp_path):
    """Point DISCOVERY_DIR at a fresh tmp path for every test."""
    target = str(tmp_path / "instances")
    monkeypatch.setattr(instance_registry, "DISCOVERY_DIR", target)
    return target


@pytest.fixture
def listen_sock():
    """Yield a (sock_path, server_socket) pair. Server is listening so probes succeed."""
    sock_path = f"/tmp/freecad_mcp_test_{uuid.uuid4().hex[:8]}.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv.bind(sock_path)
    srv.listen(1)
    try:
        yield sock_path, srv
    finally:
        srv.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


class TestUUIDGeneration:
    def test_returns_short_hex(self):
        u = instance_registry.generate_uuid()
        assert isinstance(u, str)
        assert len(u) == 12
        int(u, 16)  # must be valid hex

    def test_unique(self):
        uuids = {instance_registry.generate_uuid() for _ in range(50)}
        assert len(uuids) == 50


class TestDefaultSocketPath:
    def test_includes_uuid(self):
        path = instance_registry.default_socket_path("abc123")
        assert path == "/tmp/freecad_mcp_abc123.sock"


class TestWriteDiscovery:
    def test_creates_file_with_expected_fields(self, isolated_dir):
        u = "test12345678"
        path = instance_registry.write_discovery(
            u, "/tmp/x.sock", gui=True, label="my-build", freecad_version="1.2.0"
        )
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["uuid"] == u
        assert data["socket_path"] == "/tmp/x.sock"
        assert data["gui"] is True
        assert data["label"] == "my-build"
        assert data["freecad_version"] == "1.2.0"
        assert data["pid"] == os.getpid()
        assert "started_at" in data

    def test_label_defaults_to_uuid(self, isolated_dir):
        u = "labeluuid001"
        instance_registry.write_discovery(u, "/tmp/x.sock", gui=False)
        with open(instance_registry.discovery_path(u)) as f:
            data = json.load(f)
        assert data["label"] == u

    def test_atomic_via_rename(self, isolated_dir):
        # Write twice; the second should completely replace the first.
        u = "atomicuuid01"
        instance_registry.write_discovery(u, "/tmp/old.sock", gui=False, label="old")
        instance_registry.write_discovery(u, "/tmp/new.sock", gui=True, label="new")
        with open(instance_registry.discovery_path(u)) as f:
            data = json.load(f)
        assert data["socket_path"] == "/tmp/new.sock"
        assert data["label"] == "new"
        assert data["gui"] is True


class TestRemoveDiscovery:
    def test_removes_existing(self, isolated_dir):
        u = "remove000001"
        instance_registry.write_discovery(u, "/tmp/x.sock", gui=False)
        assert os.path.isfile(instance_registry.discovery_path(u))
        instance_registry.remove_discovery(u)
        assert not os.path.exists(instance_registry.discovery_path(u))

    def test_silent_on_missing(self, isolated_dir):
        # Must not raise even if file doesn't exist
        instance_registry.remove_discovery("ghost0000001")


class TestIsSocketAlive:
    def test_false_when_path_missing(self, isolated_dir, tmp_path):
        assert instance_registry.is_socket_alive(str(tmp_path / "nope")) is False

    def test_true_when_listening(self, listen_sock):
        sock_path, _ = listen_sock
        assert instance_registry.is_socket_alive(sock_path) is True

    def test_false_when_stale_file(self, isolated_dir):
        # File exists but nothing is listening
        stale = f"/tmp/freecad_mcp_test_stale_{uuid.uuid4().hex[:8]}.sock"
        with open(stale, "w") as f:
            f.write("")  # not a real socket
        try:
            assert instance_registry.is_socket_alive(stale) is False
        finally:
            os.unlink(stale)


class TestScanDiscovery:
    def test_empty_when_dir_missing(self, isolated_dir):
        # isolated_dir points at a path that doesn't exist yet
        assert instance_registry.scan_discovery() == []

    def test_returns_live_instances(self, isolated_dir, listen_sock):
        sock_path, _ = listen_sock
        u = "live00000001"
        instance_registry.write_discovery(u, sock_path, gui=False, label="alive")
        result = instance_registry.scan_discovery()
        assert len(result) == 1
        assert result[0]["uuid"] == u
        assert result[0]["socket_path"] == sock_path

    def test_prunes_stale_entries(self, isolated_dir):
        # Write a discovery file pointing at a socket that doesn't exist
        u = "stale0000001"
        instance_registry.write_discovery(u, "/tmp/definitely_not_there.sock",
                                           gui=False, label="stale")
        path = instance_registry.discovery_path(u)
        assert os.path.isfile(path)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(path)  # pruned

    def test_keeps_stale_when_prune_disabled(self, isolated_dir):
        u = "keeps0000001"
        instance_registry.write_discovery(u, "/tmp/definitely_not_there.sock",
                                           gui=False, label="stale")
        path = instance_registry.discovery_path(u)
        instance_registry.scan_discovery(prune_stale=False)
        assert os.path.exists(path)  # still there

    def test_prunes_unreadable_json(self, isolated_dir):
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "garbage.json")
        with open(bad_path, "w") as f:
            f.write("not-json{")
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(bad_path)

    def test_ignores_non_json_files(self, isolated_dir, listen_sock):
        sock_path, _ = listen_sock
        instance_registry.write_discovery("realuuid0001", sock_path, gui=False)
        # Add a non-.json file that should be ignored
        with open(os.path.join(isolated_dir, "README.txt"), "w") as f:
            f.write("hello")
        result = instance_registry.scan_discovery()
        assert len(result) == 1

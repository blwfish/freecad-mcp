"""Tests for AICopilot/instance_registry.py — discovery file write/scan/prune."""

import json
import os
import socket
import sys
import uuid
from unittest.mock import patch
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

    def test_temp_file_opened_with_explicit_0600_mode(self, isolated_dir):
        """M13: the temp file used to be created with plain open(tmp, 'w'),
        which gets whatever mode the process umask allows (typically
        0o644/0o664, group/world-readable) — with a LATER chmod(0o600)
        only tightening it after the fact. That's a real window, not just
        cosmetic: os.stat() after write_discovery() returns can't
        distinguish "created loose then tightened" from "created tight the
        whole time", since both end at 0o600 — the bug is specifically in
        the file's state DURING that window, which a final-state check
        can't observe. Spying on os.open (wraps=os.open, so the real
        syscall still runs — this isn't a mocked-out no-op) directly
        verifies the file is requested at 0o600 from the moment of
        creation, which is the actual mechanism that closes the window."""
        u = "spyopenuuid1"
        with patch("os.open", wraps=os.open) as spy:
            instance_registry.write_discovery(u, "/tmp/x.sock", gui=False)

        tmp_path_arg = instance_registry.discovery_path(u) + ".tmp"
        matching_calls = [c for c in spy.call_args_list if c.args[0] == tmp_path_arg]
        assert len(matching_calls) == 1, (
            f"expected exactly one os.open() call for the temp file, got {spy.call_args_list}"
        )
        call = matching_calls[0]
        mode_arg = call.args[2] if len(call.args) > 2 else call.kwargs.get("mode")
        assert mode_arg == 0o600, f"expected os.open(..., mode=0o600), got {oct(mode_arg) if mode_arg is not None else None}"

    def test_file_created_at_0600_even_under_permissive_umask(self, isolated_dir):
        """Final-state check, kept alongside the os.open spy above: confirms
        the end result is correct even with a wide-open umask (0o000), which
        would have widened the OLD open()-then-chmod version's transient
        window to the full 0o666 default rather than narrowing it."""
        old_umask = os.umask(0o000)
        try:
            u = "umasktest001"
            path = instance_registry.write_discovery(u, "/tmp/x.sock", gui=False)
        finally:
            os.umask(old_umask)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_no_transient_tmp_file_left_behind(self, isolated_dir):
        u = "tmpcleanup01"
        instance_registry.write_discovery(u, "/tmp/x.sock", gui=False)
        tmp_path = instance_registry.discovery_path(u) + ".tmp"
        assert not os.path.exists(tmp_path)


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


# ---------------------------------------------------------------------------
# Forward-compatibility / malformed-but-parseable JSON
#
# scan_discovery distinguishes three failure modes:
#   - corrupt or unreadable JSON  → deleted (current is dead, nothing
#     usable to preserve)
#   - parseable JSON with a known dead socket_path  → deleted (stale)
#   - parseable JSON with NO socket_path at all     → KEPT, warning logged
#
# That last case is the forward-compat path: a future bridge version
# writing records with a renamed key would otherwise have every record
# silently deleted by an older AICopilot scanning the same directory.
# We preserve unknown-schema records so the newer process can still
# rely on them.
# ---------------------------------------------------------------------------

class TestScanDiscoveryMalformedRecords:
    def test_record_missing_socket_path_is_preserved(self, isolated_dir):
        """A parseable JSON record missing `socket_path` is NOT deleted —
        it's likely a future-version record with a renamed key.  The
        previous behavior (silent delete) was pinned at commit 82f2764 and
        is flipped here alongside the fix to instance_registry."""
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "future0000001.json")
        with open(bad_path, "w") as f:
            json.dump({
                "uuid": "future0000001",
                "pid": 12345,
                # 'socket_path' deliberately missing — pretend a future
                # version of write_discovery renamed this field.
                "gui": False,
                "label": "future-version-instance",
                "started_at": 1700000000.0,
            }, f)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []                       # we can't connect to it
        assert os.path.exists(bad_path)           # but we don't destroy it

    def test_record_missing_socket_path_preserved_with_prune_disabled(self, isolated_dir):
        """prune_stale=False is a no-op for missing-socket-path records —
        they're preserved either way.  Symmetric to the prune=True case."""
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "future0000002.json")
        with open(bad_path, "w") as f:
            json.dump({"uuid": "future0000002", "gui": True}, f)
        result = instance_registry.scan_discovery(prune_stale=False)
        assert result == []
        assert os.path.exists(bad_path)

    def test_missing_socket_path_logs_warning(self, isolated_dir, capsys):
        """Forward-compat record produces a visible warning so the bug
        isn't invisible to operators debugging discovery problems."""
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "future0000003.json")
        payload = {"uuid": "future0000003", "endpoint": "tcp://localhost:23457"}
        with open(bad_path, "w") as f:
            json.dump(payload, f)
        instance_registry.scan_discovery(prune_stale=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Warning identifies the file and the keys we *did* see so an
        # operator can decide whether to handle the new schema.
        assert "future0000003.json" in combined
        assert "endpoint" in combined  # the unfamiliar key

    def test_record_with_unlistened_socket_file_is_pruned(self, isolated_dir, tmp_path):
        """is_socket_alive returns False for a file that exists but isn't
        a listening Unix socket.  Existing test_prunes_stale_entries uses
        a nonexistent path; this exercises the "file exists but connect
        fails" branch — the real-world case after a crash that left a
        stale socket file behind."""
        instance_registry.ensure_dir()
        # Create a regular file at a /tmp socket path
        stale_sock = str(tmp_path / "stale.sock")
        with open(stale_sock, "w") as f:
            f.write("")
        u = "ghost0000001"
        instance_registry.write_discovery(u, stale_sock, gui=False, label="ghost")
        path = instance_registry.discovery_path(u)
        assert os.path.isfile(path)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(path)

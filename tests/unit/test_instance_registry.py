"""Tests for AICopilot/instance_registry.py — discovery file write/scan/prune."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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
def dead_pid():
    """A pid guaranteed to not refer to a running process.

    Spawns a real child process (via subprocess, not raw os.fork -- pytest
    itself is multi-threaded, and forking a multi-threaded process risks
    deadlock) that exits immediately, and waits on it, so the pid is
    definitely reaped (not just "probably exited") before the test uses
    it — avoids the flakiness of picking a large/arbitrary pid number and
    hoping nothing on the test machine happens to hold it.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


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


class TestIsPidAlive:
    def test_true_for_current_process(self):
        assert instance_registry.is_pid_alive(os.getpid()) is True

    def test_false_for_reaped_child(self, dead_pid):
        assert instance_registry.is_pid_alive(dead_pid) is False

    def test_false_for_none(self):
        # Ambiguous input: a pre-pid-field record. Must not raise, and
        # must not be treated as "confirmed alive" -- an unconfirmable pid
        # shouldn't block pruning forever.
        assert instance_registry.is_pid_alive(None) is False

    def test_false_for_missing_key_via_dict_get_default(self):
        # Mirrors how callers actually invoke this: data.get("pid") on a
        # record with no "pid" key at all yields None.
        assert instance_registry.is_pid_alive({}.get("pid")) is False

    def test_false_for_zero(self):
        # Boundary: 0 is not a valid pid (would ambiguously signal the
        # calling process's own process group to os.kill).
        assert instance_registry.is_pid_alive(0) is False

    def test_false_for_negative(self):
        # Boundary: negative pids signal process groups to os.kill, not a
        # single process -- must be rejected before ever reaching os.kill.
        assert instance_registry.is_pid_alive(-1) is False

    def test_false_for_non_int(self):
        # A garbled/mistyped pid field (e.g. hand-edited JSON) must not
        # crash is_pid_alive or be silently coerced.
        assert instance_registry.is_pid_alive("12345") is False

    def test_true_when_permission_denied(self, monkeypatch):
        """A PermissionError from os.kill means the process exists but
        isn't signalable by us (different uid) -- existence is confirmed,
        so this must count as alive, not dead. Simulated via monkeypatch
        since provoking a real cross-user PermissionError isn't portable
        in a test environment."""
        def fake_kill(pid, sig):
            raise PermissionError("not our process")
        monkeypatch.setattr(instance_registry.os, "kill", fake_kill)
        assert instance_registry.is_pid_alive(1) is True

    def test_windows_dispatches_to_windows_specific_check(self, monkeypatch):
        """On win32, is_pid_alive must NOT fall through to os.kill(pid, 0)
        -- signal 0 isn't a liveness probe on Windows (it maps to
        CTRL_C_EVENT), so a plain os.kill call there would misreport a
        genuinely-alive process as dead. Confirms the dispatch happens by
        monkeypatching sys.platform and the Windows-specific helper."""
        monkeypatch.setattr(instance_registry.sys, "platform", "win32")
        monkeypatch.setattr(instance_registry, "_is_pid_alive_windows", lambda pid: True)
        assert instance_registry.is_pid_alive(4321) is True
        monkeypatch.setattr(instance_registry, "_is_pid_alive_windows", lambda pid: False)
        assert instance_registry.is_pid_alive(4321) is False

    def test_windows_check_uses_openprocess_not_os_kill(self, monkeypatch):
        """_is_pid_alive_windows must call OpenProcess (a real existence
        check), not rely on os.kill at all."""
        calls = []

        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                calls.append(("OpenProcess", pid))
                return 12345  # non-zero handle == process exists

            def CloseHandle(self, handle):
                calls.append(("CloseHandle", handle))

        import ctypes
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": FakeKernel32()})(), raising=False)

        def fail_kill(pid, sig):
            raise AssertionError("must not call os.kill on Windows")
        monkeypatch.setattr(instance_registry.os, "kill", fail_kill)

        assert instance_registry._is_pid_alive_windows(4321) is True
        assert ("OpenProcess", 4321) in calls
        assert ("CloseHandle", 12345) in calls

    def test_windows_check_false_when_openprocess_returns_null_handle(self, monkeypatch):
        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                return 0  # NULL handle == process doesn't exist / access denied

        import ctypes
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": FakeKernel32()})(), raising=False)
        assert instance_registry._is_pid_alive_windows(99999) is False


class TestScanDiscoveryPidLivenessFallback:
    """The busy-vs-dead distinction this fix adds: a record whose socket
    fails to connect is only pruned if its pid is ALSO confirmed dead.
    Confirmed 2026-09-13: a long-running recompute() blocked the accept
    loop long enough that a liveness probe failed while FreeCAD was still
    genuinely alive and working, and the old unconditional-prune behavior
    deleted its discovery record and live socket file out from under it."""

    def test_busy_but_alive_process_is_not_pruned(self, isolated_dir, tmp_path):
        """Socket doesn't accept a connection (simulating a GUI thread
        blocked by a long operation), but the recorded pid is this test
        process's own -- unambiguously alive. Must be left alone: neither
        the discovery JSON nor the socket file may be deleted, and it
        still isn't reported as 'live' (the socket really isn't usable
        right now)."""
        instance_registry.ensure_dir()
        stale_sock = str(tmp_path / "busy.sock")
        with open(stale_sock, "w") as f:
            f.write("")  # exists, but nothing is listening -- socket "dead"
        u = "busy0000001"
        instance_registry.write_discovery(u, stale_sock, gui=True, label="busy",
                                           pid=os.getpid())
        path = instance_registry.discovery_path(u)

        result = instance_registry.scan_discovery(prune_stale=True)

        assert result == []                    # not reported live
        assert os.path.exists(path)             # discovery record preserved
        assert os.path.exists(stale_sock)       # socket file NOT unlinked

    def test_dead_socket_and_dead_pid_is_still_pruned(self, isolated_dir, tmp_path, dead_pid):
        """Regression guard: the new pid check must not accidentally make
        pruning permissive across the board -- a genuinely dead instance
        (both socket and pid confirmed dead) is pruned exactly as before."""
        instance_registry.ensure_dir()
        stale_sock = str(tmp_path / "reallydead.sock")
        with open(stale_sock, "w") as f:
            f.write("")
        u = "dead00000001"
        instance_registry.write_discovery(u, stale_sock, gui=True, label="dead",
                                           pid=dead_pid)
        path = instance_registry.discovery_path(u)

        result = instance_registry.scan_discovery(prune_stale=True)

        assert result == []
        assert not os.path.exists(path)
        assert not os.path.exists(stale_sock)

    def test_busy_record_kept_across_repeated_scans_until_it_recovers(self, isolated_dir):
        """A busy-but-alive record must survive not just one scan but
        repeated scans (e.g. the bridge polling every second during a
        long recompute), and start reporting live again the moment the
        socket recovers -- without ever having been deleted in between.

        Uses a short /tmp-rooted path (not pytest's deeply-nested tmp_path)
        for the actual bind(), same reason as TestSweepStaleSockets.short_dir
        above: AF_UNIX bind paths are capped at ~104 bytes on macOS.
        """
        instance_registry.ensure_dir()
        stale_sock = f"/tmp/freecad_mcp_test_recover_{uuid.uuid4().hex[:8]}.sock"
        with open(stale_sock, "w") as f:
            f.write("")
        u = "recover00001"
        instance_registry.write_discovery(u, stale_sock, gui=True, label="recovering",
                                           pid=os.getpid())
        path = instance_registry.discovery_path(u)

        for _ in range(3):
            result = instance_registry.scan_discovery(prune_stale=True)
            assert result == []
            assert os.path.exists(path)

        # Socket "recovers": replace the dead file with a real listener.
        os.unlink(stale_sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(stale_sock)
        srv.listen(1)
        try:
            result = instance_registry.scan_discovery(prune_stale=True)
            assert len(result) == 1
            assert result[0]["uuid"] == u
        finally:
            srv.close()
            if os.path.exists(stale_sock):
                os.unlink(stale_sock)

    def test_missing_pid_field_falls_back_to_old_prune_behavior(self, isolated_dir, tmp_path):
        """A pre-pid-field record (written by an older AICopilot version)
        has no 'pid' key at all. is_pid_alive(None) is False, so this must
        still prune exactly as it did before this fix -- an unconfirmable
        pid can't be allowed to permanently block pruning of an old-schema
        record with a dead socket."""
        instance_registry.ensure_dir()
        stale_sock = str(tmp_path / "nopid.sock")
        with open(stale_sock, "w") as f:
            f.write("")
        path = os.path.join(isolated_dir, "nopid0000001.json")
        with open(path, "w") as f:
            json.dump({"uuid": "nopid0000001", "socket_path": stale_sock,
                       "gui": True, "label": "nopid"}, f)

        result = instance_registry.scan_discovery(prune_stale=True)

        assert result == []
        assert not os.path.exists(path)
        assert not os.path.exists(stale_sock)


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

    def test_prunes_stale_entries(self, isolated_dir, dead_pid):
        # Write a discovery file pointing at a socket that doesn't exist,
        # with a pid that's also confirmed dead -- otherwise write_discovery's
        # default pid (this test process's own, very much alive) would now
        # trip the busy-vs-dead fallback and block pruning.
        u = "stale0000001"
        instance_registry.write_discovery(u, "/tmp/definitely_not_there.sock",
                                           gui=False, label="stale", pid=dead_pid)
        path = instance_registry.discovery_path(u)
        assert os.path.isfile(path)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(path)  # pruned

    def test_keeps_stale_when_prune_disabled(self, isolated_dir, dead_pid):
        u = "keeps0000001"
        instance_registry.write_discovery(u, "/tmp/definitely_not_there.sock",
                                           gui=False, label="stale", pid=dead_pid)
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


class TestSweepStaleSockets:
    """sweep_stale_sockets is the startup-time complement to scan_discovery's
    prune step: it catches orphaned socket files even when no discovery
    record ever existed for them (e.g. a crashed spawn_freecad_instance
    launch), by globbing the naming pattern directly instead of relying on
    the discovery registry."""

    def test_empty_directory_returns_zero(self, tmp_path):
        assert instance_registry.sweep_stale_sockets(str(tmp_path)) == 0

    def test_removes_dead_socket_file(self, tmp_path):
        dead = tmp_path / "freecad_mcp_deadbeef0001.sock"
        dead.write_text("")
        removed = instance_registry.sweep_stale_sockets(str(tmp_path))
        assert removed == 1
        assert not dead.exists()

    @pytest.fixture
    def short_dir(self):
        """A short /tmp-rooted scratch directory (not pytest's tmp_path,
        which nests too deep) -- AF_UNIX bind paths are capped at ~104
        bytes on macOS, so real bind() calls in this class need a short
        directory of their own rather than the real /tmp (to avoid any
        interaction with real orphaned sockets that might legitimately be
        sitting there) or pytest's deeply-nested tmp_path."""
        d = tempfile.mkdtemp(dir="/tmp")
        try:
            yield d
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_leaves_alive_socket_alone(self, short_dir):
        sock_path = os.path.join(short_dir, "freecad_mcp_alive0001.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        try:
            removed = instance_registry.sweep_stale_sockets(short_dir)
            assert removed == 0
            assert os.path.exists(sock_path)
        finally:
            srv.close()

    def test_mixed_alive_and_dead_only_removes_dead(self, short_dir):
        dead1 = os.path.join(short_dir, "freecad_mcp_dead0001.sock")
        dead2 = os.path.join(short_dir, "freecad_mcp_dead0002.sock")
        for p in (dead1, dead2):
            with open(p, "w"):
                pass
        alive_path = os.path.join(short_dir, "freecad_mcp_alive0002.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(alive_path)
        srv.listen(1)
        try:
            removed = instance_registry.sweep_stale_sockets(short_dir)
            assert removed == 2
            assert not os.path.exists(dead1)
            assert not os.path.exists(dead2)
            assert os.path.exists(alive_path)
        finally:
            srv.close()

    def test_ignores_legacy_single_instance_socket_name(self, tmp_path):
        """The legacy /tmp/freecad_mcp.sock (no uuid segment) doesn't match
        the freecad_mcp_*.sock glob and must be left untouched even though
        it would fail an is_socket_alive check too -- out of scope here."""
        legacy = tmp_path / "freecad_mcp.sock"
        legacy.write_text("")
        removed = instance_registry.sweep_stale_sockets(str(tmp_path))
        assert removed == 0
        assert legacy.exists()

    def test_ignores_unrelated_files(self, tmp_path):
        (tmp_path / "freecad_mcp_debug.log").write_text("")
        (tmp_path / "unrelated.sock").write_text("")
        removed = instance_registry.sweep_stale_sockets(str(tmp_path))
        assert removed == 0

    def test_defaults_to_tmp_directory(self):
        """No directory argument must default to /tmp, matching
        default_socket_path's hardcoded /tmp/ prefix."""
        import inspect
        sig = inspect.signature(instance_registry.sweep_stale_sockets)
        assert sig.parameters["directory"].default == "/tmp"

    def test_busy_but_alive_socket_not_removed(self, isolated_dir, short_dir):
        """Regression guard: a socket that fails its connect probe but is
        named by a discovery record whose pid is confirmed alive must be
        left in place -- the same busy-vs-dead protection scan_discovery
        applies, now also applied here. Before this fix, sweep_stale_sockets
        had no way to associate a bare socket path with a pid at all, so it
        would unconditionally remove any non-listening socket regardless of
        whether its owning process was actually still running (e.g. blocked
        mid-recompute) -- reproducing the exact incident the scan_discovery
        fix (9ae8425) addressed, via this sibling code path."""
        stale_sock = os.path.join(short_dir, "freecad_mcp_busy0001.sock")
        with open(stale_sock, "w"):
            pass  # not listening -- fails the connect probe
        os.makedirs(isolated_dir, exist_ok=True)
        with open(os.path.join(isolated_dir, "busy.json"), "w") as f:
            json.dump({"uuid": "busy", "socket_path": stale_sock, "pid": os.getpid()}, f)

        removed = instance_registry.sweep_stale_sockets(short_dir)
        assert removed == 0
        assert os.path.exists(stale_sock)

    def test_dead_socket_with_dead_pid_record_still_removed(self, isolated_dir, short_dir, dead_pid):
        """The pid-liveness fallback only protects a socket whose recorded
        owner is actually alive -- one whose discovery record names a
        confirmed-dead pid is pruned exactly as before."""
        stale_sock = os.path.join(short_dir, "freecad_mcp_reallydead0001.sock")
        with open(stale_sock, "w"):
            pass
        os.makedirs(isolated_dir, exist_ok=True)
        with open(os.path.join(isolated_dir, "dead.json"), "w") as f:
            json.dump({"uuid": "d", "socket_path": stale_sock, "pid": dead_pid}, f)

        removed = instance_registry.sweep_stale_sockets(short_dir)
        assert removed == 1
        assert not os.path.exists(stale_sock)

    def test_orphaned_socket_with_no_discovery_record_still_removed(self, isolated_dir, tmp_path):
        """A socket with no matching discovery record at all (the original
        'fully orphaned, crashed before cleanup' case this function exists
        for) has no pid to check -- falls back to the prior unconditional
        removal, unchanged by the new pid-liveness lookup."""
        dead = tmp_path / "freecad_mcp_orphan0001.sock"
        dead.write_text("")
        removed = instance_registry.sweep_stale_sockets(str(tmp_path))
        assert removed == 1
        assert not dead.exists()


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

    def test_corrupt_json_drop_logs_warning(self, isolated_dir, capsys):
        """Regression: corrupt/unparseable JSON used to be dropped via a
        bare `continue` with zero visibility -- a directory full of
        corrupted records was indistinguishable from "no live instances".
        """
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "corrupt0000001.json")
        with open(bad_path, "w") as f:
            f.write("{not valid json")
        instance_registry.scan_discovery(prune_stale=False)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "corrupt0000001.json" in combined

    def test_non_dict_json_drop_logs_warning(self, isolated_dir, capsys):
        """Regression: valid-but-non-object JSON (list/number/null) was
        also dropped silently -- same visibility gap as the corrupt-JSON
        case above."""
        instance_registry.ensure_dir()
        bad_path = os.path.join(isolated_dir, "listrecord0000001.json")
        with open(bad_path, "w") as f:
            json.dump(["not", "a", "record"], f)
        instance_registry.scan_discovery(prune_stale=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "listrecord0000001.json" in combined

    def test_record_with_unlistened_socket_file_is_pruned(self, isolated_dir, tmp_path, dead_pid):
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
        instance_registry.write_discovery(u, stale_sock, gui=False, label="ghost", pid=dead_pid)
        path = instance_registry.discovery_path(u)
        assert os.path.isfile(path)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(path)

    def test_prune_also_removes_the_orphaned_socket_file(self, isolated_dir, tmp_path, dead_pid):
        """Pruning a stale record used to remove only the discovery JSON,
        leaving the socket file itself behind forever -- every future
        instance picks a fresh random UUID path, so nothing else would
        ever revisit that exact file. The prune step must now also
        os.remove() the socket file it just proved is dead."""
        instance_registry.ensure_dir()
        stale_sock = str(tmp_path / "orphan.sock")
        with open(stale_sock, "w") as f:
            f.write("")
        u = "orphan000001"
        instance_registry.write_discovery(u, stale_sock, gui=False, label="orphan", pid=dead_pid)
        assert os.path.exists(stale_sock)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []
        assert not os.path.exists(stale_sock)  # socket file itself is gone

    def test_prune_disabled_keeps_the_socket_file_too(self, isolated_dir, tmp_path, dead_pid):
        """Symmetric to the JSON-record case: prune_stale=False must leave
        the socket file alone as well, not just the discovery JSON."""
        instance_registry.ensure_dir()
        stale_sock = str(tmp_path / "keep.sock")
        with open(stale_sock, "w") as f:
            f.write("")
        u = "keepsock0001"
        instance_registry.write_discovery(u, stale_sock, gui=False, label="keep", pid=dead_pid)
        instance_registry.scan_discovery(prune_stale=False)
        assert os.path.exists(stale_sock)

    def test_prune_tolerates_socket_file_already_gone(self, isolated_dir, dead_pid):
        """The dead socket_path may not exist as a file at all (the
        nonexistent-path case already covered by test_prunes_stale_entries)
        -- os.remove on a missing path must not raise and abort the scan."""
        u = "nofile000001"
        instance_registry.write_discovery(u, "/tmp/definitely_not_there_either.sock",
                                           gui=False, label="nofile", pid=dead_pid)
        result = instance_registry.scan_discovery(prune_stale=True)
        assert result == []

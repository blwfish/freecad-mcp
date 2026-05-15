"""Tests for the bridge-side `_fetch_instance_info` helper and the enriched
`list_freecad_instances` flow that fans out to multiple instances.

We don't have FreeCAD available in unit-test runs, so the FreeCAD side is
simulated by a small threaded socket server that speaks the same length-
prefixed framing protocol and returns a canned `get_instance_info` reply.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import types as _types
import uuid as _uuid
import pytest

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py")
ROOT = os.path.dirname(os.path.abspath(BRIDGE_PATH))


def _load_bridge():
    import importlib.util
    spec = importlib.util.spec_from_file_location("freecad_mcp_server_ie", BRIDGE_PATH)
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
def _clear_cache(bridge):
    """Reset the info cache between tests so stale entries don't leak."""
    bridge._info_cache.clear()
    yield
    bridge._info_cache.clear()


# ---------------------------------------------------------------------------
# Fake AICopilot — listens on a Unix socket, returns a canned info reply
# ---------------------------------------------------------------------------

class FakeAICopilot:
    """Threaded socket server that mimics the FreeCAD-side handler.

    Accepts one or more `get_instance_info` requests and replies with a
    pre-baked dict using the same length-prefixed framing as the real handler.
    """

    def __init__(self, info: dict, sock_path: str | None = None):
        self.info = info
        self.sock_path = sock_path or f"/tmp/freecad_mcp_fake_{_uuid.uuid4().hex[:8]}.sock"
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.request_count = 0

    def start(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        self._srv.listen(5)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self):
        # Inline the framing protocol so we don't import bridge framing here.
        import struct
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2.0)
                # Read 4-byte length prefix
                hdr = b""
                while len(hdr) < 4:
                    chunk = conn.recv(4 - len(hdr))
                    if not chunk:
                        break
                    hdr += chunk
                if len(hdr) < 4:
                    conn.close()
                    continue
                msg_len = struct.unpack(">I", hdr)[0]
                body = b""
                while len(body) < msg_len:
                    chunk = conn.recv(msg_len - len(body))
                    if not chunk:
                        break
                    body += chunk
                self.request_count += 1
                # Parse, only respond if it's get_instance_info
                try:
                    req = json.loads(body.decode())
                except Exception:
                    conn.close()
                    continue
                if req.get("tool") == "get_instance_info":
                    payload = json.dumps({"result": self.info}).encode()
                else:
                    payload = json.dumps({"error": "unsupported"}).encode()
                conn.sendall(struct.pack(">I", len(payload)) + payload)
            finally:
                conn.close()

    def stop(self):
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass
        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass


@pytest.fixture
def fake_instance():
    """Spawn one FakeAICopilot; tear down on teardown."""
    fakes = []

    def make(info: dict, sock_path: str | None = None) -> FakeAICopilot:
        fake = FakeAICopilot(info, sock_path).start()
        fakes.append(fake)
        return fake

    yield make
    for fake in fakes:
        fake.stop()


# ---------------------------------------------------------------------------
# _fetch_instance_info
# ---------------------------------------------------------------------------

class TestFetchInstanceInfo:

    def test_returns_canned_result(self, bridge, fake_instance):
        canned = {
            "uuid": "abc123",
            "active_doc_label": "MyPart",
            "window_title": "FreeCAD — MyPart",
            "gui": True,
        }
        fake = fake_instance(canned)
        info = bridge._fetch_instance_info(fake.sock_path, timeout=2.0)
        assert info is not None
        assert info["uuid"] == "abc123"
        assert info["active_doc_label"] == "MyPart"
        assert info["window_title"] == "FreeCAD — MyPart"

    def test_returns_none_when_socket_missing(self, bridge, tmp_path):
        result = bridge._fetch_instance_info(
            f"/tmp/freecad_mcp_does_not_exist_{_uuid.uuid4().hex[:8]}.sock"
        )
        assert result is None

    def test_caches_result(self, bridge, fake_instance):
        canned = {"uuid": "cache01", "active_doc_label": "Cached"}
        fake = fake_instance(canned)
        # First call → round-trip
        bridge._fetch_instance_info(fake.sock_path, timeout=2.0)
        assert fake.request_count == 1
        # Second call within TTL → from cache
        bridge._fetch_instance_info(fake.sock_path, timeout=2.0)
        assert fake.request_count == 1

    def test_cache_expires(self, bridge, fake_instance, monkeypatch):
        canned = {"uuid": "exp01"}
        fake = fake_instance(canned)
        monkeypatch.setattr(bridge, "_INFO_CACHE_TTL", 0.01)
        bridge._fetch_instance_info(fake.sock_path, timeout=2.0)
        assert fake.request_count == 1
        time.sleep(0.05)
        bridge._fetch_instance_info(fake.sock_path, timeout=2.0)
        assert fake.request_count == 2

    def test_windows_returns_none(self, bridge, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        assert bridge._fetch_instance_info("/tmp/anywhere.sock") is None


# ---------------------------------------------------------------------------
# End-to-end: two fakes + discovery dir → list_all sees both, fetch enriches
# ---------------------------------------------------------------------------

class TestTwoInstanceFlow:

    def test_two_instances_discoverable_and_enrichable(
        self, bridge, fake_instance, tmp_path, monkeypatch
    ):
        # Isolate discovery dir so host state doesn't leak in.
        disc = tmp_path / "instances"
        disc.mkdir()
        monkeypatch.setattr(bridge, "DISCOVERY_DIR", str(disc))

        # Two fakes with distinct identities.
        fake_a = fake_instance({
            "uuid": "uuidaaaa",
            "active_doc_label": "PartA",
            "gui": True,
        })
        fake_b = fake_instance({
            "uuid": "uuidbbbb",
            "active_doc_label": "PartB",
            "gui": False,
        })

        # Write matching discovery files manually (mimicking what AICopilot
        # would do at startup).
        for fake, label, gui in [(fake_a, "instance-a", True), (fake_b, "instance-b", False)]:
            data = {
                "uuid": fake.info["uuid"],
                "pid": 99000 + ord(label[-1]),
                "socket_path": fake.sock_path,
                "gui": gui,
                "label": label,
                "started_at": time.time(),
            }
            with open(disc / f"{fake.info['uuid']}.json", "w") as f:
                json.dump(data, f)

        # Discovery scan should see both
        live = bridge._scan_discovery()
        labels = sorted(r["label"] for r in live)
        assert labels == ["instance-a", "instance-b"]

        # list_all should include both, marked not-managed (no register call)
        ctx = bridge._BridgeCtx()
        listing = ctx.list_all()
        listed_labels = {e["label"] for e in listing}
        assert {"instance-a", "instance-b"}.issubset(listed_labels)

        # Fetching info on each should return the right doc label
        info_a = bridge._fetch_instance_info(fake_a.sock_path, timeout=2.0)
        info_b = bridge._fetch_instance_info(fake_b.sock_path, timeout=2.0)
        assert info_a["active_doc_label"] == "PartA"
        assert info_b["active_doc_label"] == "PartB"

    def test_resolve_target_errors_when_two_live(
        self, bridge, fake_instance, tmp_path, monkeypatch
    ):
        """With two live instances and no explicit selection, resolve_target
        must refuse to auto-pick and return an actionable error."""
        disc = tmp_path / "instances"
        disc.mkdir()
        monkeypatch.setattr(bridge, "DISCOVERY_DIR", str(disc))
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)

        fake_a = fake_instance({"uuid": "twoaa001"})
        fake_b = fake_instance({"uuid": "twobb001"})
        for fake, label in [(fake_a, "lhs"), (fake_b, "rhs")]:
            data = {
                "uuid": fake.info["uuid"],
                "pid": os.getpid(),
                "socket_path": fake.sock_path,
                "gui": False,
                "label": label,
                "started_at": time.time(),
            }
            with open(disc / f"{fake.info['uuid']}.json", "w") as f:
                json.dump(data, f)

        ctx = bridge._BridgeCtx()
        path, err = ctx.resolve_target()
        assert path is None
        assert err is not None
        assert "2 live" in err or "cannot auto-select" in err

    def test_resolve_target_auto_picks_lone_instance(
        self, bridge, fake_instance, tmp_path, monkeypatch
    ):
        disc = tmp_path / "instances"
        disc.mkdir()
        monkeypatch.setattr(bridge, "DISCOVERY_DIR", str(disc))
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)

        fake = fake_instance({"uuid": "lone0000001"})
        data = {
            "uuid": "lone0000001",
            "pid": os.getpid(),
            "socket_path": fake.sock_path,
            "gui": False,
            "label": "solo",
            "started_at": time.time(),
        }
        with open(disc / "lone0000001.json", "w") as f:
            json.dump(data, f)

        ctx = bridge._BridgeCtx()
        path, err = ctx.resolve_target()
        assert err is None
        assert path == fake.sock_path

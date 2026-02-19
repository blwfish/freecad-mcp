"""
Tests for mcp_bridge_framing.py — the bridge-side message framing protocol.

No FreeCAD dependency — these test pure socket protocol logic.
"""

import socket
import struct
import json
import threading
import pytest
import sys
import os

# Add project root to path so we can import mcp_bridge_framing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mcp_bridge_framing import send_message, receive_message, _recv_exact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_socketpair():
    """Create a connected pair of sockets for testing."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = f"/tmp/test_framing_{os.getpid()}.sock"
    if os.path.exists(path):
        os.remove(path)
    server.bind(path)
    server.listen(1)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(path)
    peer, _ = server.accept()

    server.close()
    os.remove(path)
    return client, peer


def frame_message(msg_str: str) -> bytes:
    """Manually frame a message for testing receive_message."""
    msg_bytes = msg_str.encode("utf-8")
    return struct.pack(">I", len(msg_bytes)) + msg_bytes


# ---------------------------------------------------------------------------
# send_message tests
# ---------------------------------------------------------------------------

class TestSendMessage:
    def test_basic_send(self):
        client, peer = make_socketpair()
        try:
            assert send_message(client, "hello") is True

            # Read what was sent
            raw = peer.recv(4096)
            length = struct.unpack(">I", raw[:4])[0]
            assert length == 5
            assert raw[4:] == b"hello"
        finally:
            client.close()
            peer.close()

    def test_send_json(self):
        client, peer = make_socketpair()
        try:
            msg = json.dumps({"tool": "create_box", "args": {"length": 10}})
            assert send_message(client, msg) is True

            raw = peer.recv(4096)
            length = struct.unpack(">I", raw[:4])[0]
            decoded = raw[4:4 + length].decode("utf-8")
            assert json.loads(decoded)["tool"] == "create_box"
        finally:
            client.close()
            peer.close()

    def test_send_unicode(self):
        client, peer = make_socketpair()
        try:
            msg = "résultat: réussi"
            assert send_message(client, msg) is True

            raw = peer.recv(4096)
            length = struct.unpack(">I", raw[:4])[0]
            decoded = raw[4:4 + length].decode("utf-8")
            assert decoded == msg
        finally:
            client.close()
            peer.close()

    def test_send_to_closed_socket(self):
        client, peer = make_socketpair()
        peer.close()
        client.close()
        assert send_message(client, "hello") is False

    def test_send_empty_string(self):
        client, peer = make_socketpair()
        try:
            assert send_message(client, "") is True
            raw = peer.recv(4096)
            length = struct.unpack(">I", raw[:4])[0]
            assert length == 0
        finally:
            client.close()
            peer.close()


# ---------------------------------------------------------------------------
# receive_message tests
# ---------------------------------------------------------------------------

class TestReceiveMessage:
    def test_basic_receive(self):
        client, peer = make_socketpair()
        try:
            peer.sendall(frame_message("hello"))
            result = receive_message(client, timeout=5.0)
            assert result == "hello"
        finally:
            client.close()
            peer.close()

    def test_receive_json(self):
        client, peer = make_socketpair()
        try:
            msg = json.dumps({"result": "Box created"})
            peer.sendall(frame_message(msg))
            result = receive_message(client, timeout=5.0)
            assert json.loads(result)["result"] == "Box created"
        finally:
            client.close()
            peer.close()

    def test_receive_timeout(self):
        client, peer = make_socketpair()
        try:
            # Don't send anything — should timeout
            result = receive_message(client, timeout=0.1)
            assert result is None
        finally:
            client.close()
            peer.close()

    def test_receive_connection_closed(self):
        client, peer = make_socketpair()
        peer.close()
        result = receive_message(client, timeout=1.0)
        assert result is None
        client.close()

    def test_receive_oversized_message(self):
        """Messages larger than 50KB should be rejected."""
        client, peer = make_socketpair()
        try:
            # Send a length prefix claiming a very large message
            fake_length = 100 * 1024  # 100KB
            peer.sendall(struct.pack(">I", fake_length))
            result = receive_message(client, timeout=2.0)
            assert result is None
        finally:
            client.close()
            peer.close()

    def test_receive_preserves_original_timeout(self):
        client, peer = make_socketpair()
        try:
            client.settimeout(42.0)
            peer.sendall(frame_message("test"))
            receive_message(client, timeout=5.0)
            assert client.gettimeout() == 42.0
        finally:
            client.close()
            peer.close()


# ---------------------------------------------------------------------------
# _recv_exact tests
# ---------------------------------------------------------------------------

class TestRecvExact:
    def test_exact_bytes(self):
        client, peer = make_socketpair()
        try:
            peer.sendall(b"ABCDEF")
            result = _recv_exact(client, 6)
            assert result == b"ABCDEF"
        finally:
            client.close()
            peer.close()

    def test_partial_reads(self):
        """_recv_exact should handle data arriving in chunks."""
        client, peer = make_socketpair()
        try:
            # Send data in two chunks with a small delay
            def send_in_chunks():
                import time
                peer.sendall(b"ABC")
                time.sleep(0.05)
                peer.sendall(b"DEF")

            t = threading.Thread(target=send_in_chunks)
            t.start()
            result = _recv_exact(client, 6)
            t.join()
            assert result == b"ABCDEF"
        finally:
            client.close()
            peer.close()

    def test_connection_closed_midstream(self):
        client, peer = make_socketpair()
        try:
            peer.sendall(b"AB")
            peer.close()
            result = _recv_exact(client, 6)
            assert result is None
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_send_then_receive(self):
        """Full round trip: send_message on one side, receive_message on the other."""
        client, peer = make_socketpair()
        try:
            msg = json.dumps({"tool": "create_box", "args": {"length": 10}})
            assert send_message(client, msg) is True
            result = receive_message(peer, timeout=5.0)
            assert json.loads(result)["tool"] == "create_box"
        finally:
            client.close()
            peer.close()

    def test_multiple_messages(self):
        """Multiple messages sent sequentially should each be received correctly."""
        client, peer = make_socketpair()
        try:
            msgs = ["first", "second", "third"]
            for m in msgs:
                send_message(client, m)

            for expected in msgs:
                result = receive_message(peer, timeout=5.0)
                assert result == expected
        finally:
            client.close()
            peer.close()

    def test_large_message_within_limit(self):
        """A message just under 50KB should work fine."""
        client, peer = make_socketpair()
        try:
            # 49KB of data — need threaded send/receive since buffer may be small
            msg = "x" * (49 * 1024)

            def sender():
                send_message(client, msg)

            t = threading.Thread(target=sender)
            t.start()
            result = receive_message(peer, timeout=10.0)
            t.join()
            assert result == msg
        finally:
            client.close()
            peer.close()

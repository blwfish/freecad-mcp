"""
MCP Bridge Message Framing Adapter
===================================

This module provides the client-side message framing functions for the
freecad_mcp_handler wire protocol: a 4-byte big-endian length prefix
followed by UTF-8 JSON.

Use this in your MCP bridge (freecad_mcp_server.py or similar) to communicate
with the FreeCAD socket server.

Deliberately NOT version-labeled against freecad_mcp_handler's own
__version__: this module's actual behavioral contract with that file is
MAX_MESSAGE_SIZE (both copies must stay equal -- see that constant's own
comment), which is enforced by a real cross-module parity test
(tests/unit/test_freecad_mcp_handler.py). A hardcoded version number in
this docstring drifted from freecad_mcp_handler's real version at least
once already (that mismatch was "fixed" by only making the docstring's
own two mentions agree with each other, not with reality) -- nothing
enforces a doc comment, so the actual invariant that matters is now
tracked by the real test instead of restated here as a claim to keep
manually in sync.
"""

import socket
import struct
import sys
from typing import Optional

# Maximum message size for the length-prefixed protocol (single source of truth
# on the bridge side). The handler (freecad_mcp_handler.py) embeds its own copy
# because it runs inside FreeCAD and cannot import this module — keep them equal.
MAX_MESSAGE_SIZE = 50 * 1024  # 50KB ≈ 15K tokens


def _log(msg: str) -> None:
    """Log to stderr only.

    This process speaks the MCP protocol over *stdout*; any stray write to
    stdout is parsed by the client as a protocol frame and corrupts the
    transport. All framing diagnostics must go to stderr.
    """
    print(msg, file=sys.stderr, flush=True)


def send_message(sock: socket.socket, message_str: str) -> bool:
    """Send a length-prefixed message over socket (client-side).
    
    Must match the protocol used by freecad_mcp_handler (length-prefixed framing; see MAX_MESSAGE_SIZE parity test, not a version number).
    
    Protocol:
        [4 bytes: message length as uint32 big-endian][message bytes]
    
    Args:
        sock: Connected socket to FreeCAD server
        message_str: JSON command string to send
        
    Returns:
        True if successful, False if socket error
        
    Example:
        import json
        command = json.dumps({"tool": "create_box", "args": {"length": 10}})
        send_message(sock, command)
    """
    try:
        # Encode message
        message_bytes = message_str.encode('utf-8')
        message_len = len(message_bytes)

        # Refuse to put an oversized frame on the wire: the peer will reject the
        # body after reading the length prefix, desyncing every subsequent frame.
        if message_len > MAX_MESSAGE_SIZE:
            _log(f"❌ Refusing to send oversized message: {message_len} bytes "
                 f"(limit {MAX_MESSAGE_SIZE}); sending it would desync the framing.")
            return False

        # Create length prefix (4 bytes, big-endian unsigned int)
        length_prefix = struct.pack('>I', message_len)

        # Send length + message atomically
        sock.sendall(length_prefix + message_bytes)
        return True

    except (socket.error, BrokenPipeError, OSError) as e:
        _log(f"⚠️  Socket send error: {e}")
        return False
    except Exception as e:
        _log(f"❌ Unexpected error in send_message: {e}")
        return False


def receive_message(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive a length-prefixed message from socket (client-side).
    
    Must match the protocol used by freecad_mcp_handler (length-prefixed framing; see MAX_MESSAGE_SIZE parity test, not a version number).
    
    Args:
        sock: Connected socket to FreeCAD server
        timeout: Maximum time to wait for complete message (seconds)
        
    Returns:
        Decoded message string, or None if error/timeout
        
    Example:
        response_str = receive_message(sock)
        if response_str:
            response = json.loads(response_str)
            print(response['result'])
    """
    old_timeout = sock.gettimeout()
    try:
        # Set socket timeout
        sock.settimeout(timeout)

        # Read the 4-byte length prefix (None => connection closed)
        length_bytes = _recv_exact(sock, 4)
        if length_bytes is None:
            return None

        # Unpack length
        message_len = struct.unpack('>I', length_bytes)[0]

        # Validate length (prevent memory attacks and accidental token waste)
        if message_len > MAX_MESSAGE_SIZE:
            est_tokens = int(message_len / 3.5)
            _log(f"❌ Message too large: {message_len/1024:.1f}KB ({est_tokens:,} tokens); "
                 f"limit {MAX_MESSAGE_SIZE/1024:.0f}KB. To raise it, change MAX_MESSAGE_SIZE "
                 f"in mcp_bridge_framing.py (and the matching copy in freecad_mcp_handler.py).")
            return None

        # Read the exact number of message bytes. message_len may legitimately be
        # 0 (an empty-body frame); _recv_exact returns b'' for that and None only
        # on a closed connection, so distinguish with `is None` — never falsiness,
        # which would misread a valid empty frame as a disconnect.
        message_bytes = _recv_exact(sock, message_len)
        if message_bytes is None:
            return None

        # Decode and return
        return message_bytes.decode('utf-8')

    except socket.timeout:
        _log("⚠️  Socket receive timeout")
        return None
    except UnicodeDecodeError as e:
        _log(f"❌ Message decode error: {e}")
        return None
    except Exception as e:
        _log(f"❌ Receive error: {e}")
        return None
    finally:
        # Always restore the caller's timeout, even on an exception path.
        sock.settimeout(old_timeout)


def _recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Receive exactly num_bytes from socket, handling partial reads.
    
    This is critical because recv() may return less than requested bytes,
    especially for large messages or slow networks.
    
    Args:
        sock: Socket to receive from
        num_bytes: Exact number of bytes to read
        
    Returns:
        Complete byte buffer of exactly num_bytes, or None if connection closed
    """
    buffer = bytearray()
    
    while len(buffer) < num_bytes:
        remaining = num_bytes - len(buffer)
        chunk = sock.recv(min(remaining, 65536))  # Read in 64KB chunks max
        if not chunk:
            # Connection closed before receiving all bytes
            return None
        buffer.extend(chunk)
    
    return bytes(buffer)


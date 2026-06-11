"""
MCP Bridge Message Framing Adapter
===================================

This module provides the client-side message framing functions that match
the freecad_mcp_handler v2.1.0 protocol.

Use this in your MCP bridge (freecad_mcp_server.py or similar) to communicate
with the updated FreeCAD socket server.

Version: 2.1.2 (matches freecad_mcp_handler v2.1.2)
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
    
    Must match the protocol used by freecad_mcp_handler v2.1.0.
    
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
    
    Must match the protocol used by freecad_mcp_handler v2.1.0.
    
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


# =============================================================================
# Integration Examples
# =============================================================================

def example_send_command(sock: socket.socket, tool: str, args: dict) -> Optional[dict]:
    """Example: Send a command to FreeCAD and get the response.
    
    This shows the complete pattern for using the message framing protocol.
    """
    import json
    
    # Create command
    command = json.dumps({
        "tool": tool,
        "args": args
    })
    
    # Send with framing
    if not send_message(sock, command):
        print("❌ Failed to send command")
        return None
    
    # Receive response with framing
    response_str = receive_message(sock, timeout=30.0)
    if not response_str:
        print("❌ Failed to receive response")
        return None
    
    # Parse JSON response
    try:
        response = json.loads(response_str)
        return response
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response: {e}")
        return None


def example_bridge_integration():
    """Example: How to integrate into an existing MCP bridge."""
    
    print("""
    To integrate into your existing bridge (e.g., freecad_mcp_server.py):
    
    1. Add this import at the top:
       from mcp_bridge_framing import send_message, receive_message
    
    2. Replace all socket.send() calls with send_message():
       # OLD:
       sock.send(command.encode('utf-8'))
       
       # NEW:
       send_message(sock, command)
    
    3. Replace all socket.recv() calls with receive_message():
       # OLD:
       data = sock.recv(4096).decode('utf-8')
       
       # NEW:
       data = receive_message(sock, timeout=30.0)
    
    4. Update error handling to check for None returns:
       response = receive_message(sock)
       if response is None:
           # Handle connection error
           ...
    
    That's it! The bridge will now properly handle messages of any size.
    """)


if __name__ == '__main__':
    # Show integration guide
    example_bridge_integration()
    
    print("\n" + "="*70)
    print("Testing with FreeCAD socket...")
    print("="*70)
    
    # Connect to FreeCAD
    SOCKET_PATH = "/tmp/freecad_mcp_socket"
    
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        print(f"✓ Connected to {SOCKET_PATH}")
        
        # Test with a simple command
        response = example_send_command(sock, "create_box", {"length": 10, "width": 10, "height": 10})
        
        if response and response.get('success'):
            print(f"✓ Command successful: {response.get('result')}")
        else:
            print(f"❌ Command failed: {response.get('error') if response else 'No response'}")
        
        sock.close()
        
    except FileNotFoundError:
        print(f"⚠️  FreeCAD socket not found at {SOCKET_PATH}")
        print("   Make sure FreeCAD is running with MCP socket server")
    except Exception as e:
        print(f"❌ Error: {e}")

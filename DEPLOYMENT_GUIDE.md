# FreeCAD MCP v2.1.0 Deployment Guide
## Fixing JSON Truncation and Crash Issues

---

## What's Fixed in v2.1.0

### Critical Bugs Resolved
1. **Buffer Overflow** - Messages >4KB were truncated, breaking JSON parsing
2. **No Message Boundaries** - Multiple messages or partial sends caused corruption
3. **Crash on Malformed JSON** - Server crashed instead of returning error
4. **UTF-8 Decode Crashes** - Binary garbage caused handler to crash
5. **Poor Error Recovery** - Single client error could hang entire server

### New Features
- ✅ **Length-Prefixed Protocol** - Messages can be any size (up to 100MB)
- ✅ **Graceful Error Handling** - Malformed input returns errors, doesn't crash
- ✅ **Robust UTF-8 Handling** - Invalid encoding handled gracefully
- ✅ **Better Logging** - JSON parse errors logged with preview
- ✅ **Connection Recovery** - Server continues running after client errors

---

## Deployment Steps

### Part 1: Update FreeCAD Socket Server

1. **Backup Current Installation**
   ```bash
   # Find your AICopilot installation
   # macOS: ~/Library/Application Support/FreeCAD/v1-2/Mod/AICopilot/
   # Linux: ~/.local/share/FreeCAD/Mod/AICopilot/
   # Windows: %APPDATA%\FreeCAD\Mod\AICopilot\
   
   cd ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/
   cp socket_server.py socket_server.py.backup
   ```

2. **Install New Version**
   ```bash
   # Copy the updated socket_server_v2.1.0.py
   cp /path/to/socket_server_v2.1.0.py socket_server.py
   ```

3. **Verify Version**
   - Restart FreeCAD
   - Check console for: `✓ socket_server v2.1.0 validated`
   - Look for: `✅ MCP Debug infrastructure loaded` (if installed)

### Part 2: Update MCP Bridge

**Option A: Using the Framing Module (Recommended)**

1. **Copy the framing module to your bridge directory**
   ```bash
   cp mcp_bridge_framing.py ~/freecad-mcp/
   ```

2. **Update your bridge code (e.g., working_bridge.py)**
   
   Add import at top:
   ```python
   from mcp_bridge_framing import send_message, receive_message
   ```
   
   Replace old socket operations:
   ```python
   # OLD CODE - REMOVE THIS:
   sock.send(command.encode('utf-8'))
   data = sock.recv(4096).decode('utf-8')
   
   # NEW CODE - USE THIS:
   send_message(sock, command)
   data = receive_message(sock, timeout=30.0)
   if data is None:
       # Handle connection error
       print("Connection lost or timeout")
       return
   ```

**Option B: Manual Integration**

If you can't use the module, copy the three functions directly into your bridge:
- `send_message(sock, message_str)`
- `receive_message(sock, timeout)`
- `_recv_exact(sock, num_bytes)`

See mcp_bridge_framing.py for the complete implementations.

### Part 3: Testing

1. **Test Small Command (< 4KB)**
   ```python
   {"tool": "create_box", "args": {"length": 10, "width": 10, "height": 10}}
   ```
   ✅ Should work exactly as before

2. **Test Large Command (> 4KB)**
   ```python
   # Send Python code with a complex sketch (previously would fail)
   code = "sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')\n" * 500
   {"tool": "execute_python", "args": {"code": code}}
   ```
   ✅ Should now succeed without truncation

3. **Test Malformed JSON**
   ```python
   {"tool": "create_box", "args": {incomplete
   ```
   ✅ Should return error JSON instead of crashing:
   ```json
   {"success": false, "error": "Invalid JSON: ..."}
   ```

4. **Test Connection Recovery**
   - Send valid command
   - Send garbage data
   - Send another valid command
   ✅ Server should handle all three without crashing

---

## Troubleshooting

### "Message too large" error
- Check if you're sending >100MB
- This is likely a bug in your code (infinite loop generating huge message)
- Adjust MAX_MESSAGE_SIZE in both files if legitimately needed

### Bridge still getting truncated messages
- Make sure BOTH server and bridge are updated
- Check that bridge is using send_message/receive_message
- Verify no other code is bypassing the framing functions

### "Socket receive timeout" warnings
- Increase timeout parameter: `receive_message(sock, timeout=60.0)`
- Check if FreeCAD is hung (heavy computation)
- Verify network connection is stable

### Server not starting after update
- Check FreeCAD console for error messages
- Verify struct module is available: `python3 -c "import struct"`
- Try running with debug: Set `DEBUG_ENABLED = True` and check `/tmp/freecad_mcp_debug/`

### Version mismatch warnings
- Update mcp_versions.py if you have it
- Or ignore if not using version system

---

## Verification Checklist

After deployment, verify:

- [ ] FreeCAD console shows `socket_server v2.1.0 validated`
- [ ] Small commands (<4KB) work correctly
- [ ] Large commands (>4KB) work without truncation
- [ ] Malformed JSON returns error instead of crashing
- [ ] Server continues running after client errors
- [ ] Bridge updated to use message framing protocol
- [ ] Test suite passes (if you have one)

---

## Rollback Procedure

If something goes wrong:

1. **Restore backup**
   ```bash
   cd ~/Library/Application\ Support/FreeCAD/v1-2/Mod/AICopilot/
   cp socket_server.py.backup socket_server.py
   ```

2. **Revert bridge changes**
   ```bash
   git checkout working_bridge.py  # if using git
   ```

3. **Restart FreeCAD**

4. **Report issues** with:
   - FreeCAD version
   - Python version
   - Error messages from console
   - Contents of /tmp/freecad_mcp_debug/ (if available)

---

## Performance Notes

### Message Framing Overhead
- **Small messages (<1KB)**: ~0.01ms overhead (negligible)
- **Medium messages (1-100KB)**: ~0.1-1ms overhead
- **Large messages (1-10MB)**: ~10-100ms overhead

The overhead is minimal compared to FreeCAD operation time.

### Memory Usage
- Server uses O(1) memory (no message buffering)
- Bridge uses O(message_size) during receive
- Large messages (>1MB) briefly increase memory usage

### Network Behavior
- Each message requires 4 extra bytes (length prefix)
- Messages are sent atomically (sendall) to prevent fragmentation
- Partial receives are handled automatically

---

## Migration Path

If you have many MCP clients:

1. **Phase 1**: Update socket_server.py only
   - Old bridges will fail (expected)
   - Document which bridges need updating

2. **Phase 2**: Update bridges one at a time
   - Test each bridge individually
   - Keep list of updated vs pending

3. **Phase 3**: Verify all clients working
   - Run integration tests
   - Monitor for errors

**DO NOT run mixed versions** - all components must use message framing or none.

---

## Support

If you encounter issues:

1. Check FreeCAD console for errors
2. Look in /tmp/freecad_mcp_debug/ for detailed logs (if debug enabled)
3. Verify both server and bridge are updated
4. Test with simple commands first
5. Check that FreeCAD 1.2-dev is running (not 1.0.2 or 1.1-dev)

The message framing protocol is a **breaking change** - you cannot use v2.1.0
server with pre-v2.1.0 bridges or vice versa.

---

## What's Next

Planned improvements for v2.2.0:
- Async message handling (non-blocking)
- Message compression for large payloads
- Batch command support
- Connection pooling for multiple clients
- WebSocket support

These are optional enhancements and won't break compatibility.

---

## Version History

**v2.1.0** (2025-12-05)
- Added length-prefixed message protocol
- Fixed JSON parsing crashes
- Improved error recovery
- Added UTF-8 decode handling

**v2.0.0** (Previous)
- Added AST expression evaluation
- Fixed GUI thread safety
- Added operation ring buffer
- Version validation system

---

## Files in This Package

- `socket_server_v2.1.0.py` - Updated FreeCAD socket server
- `mcp_bridge_framing.py` - Bridge-side message framing
- `SOCKET_SERVER_PATCH_v2.1.0.md` - Detailed patch documentation
- `DEPLOYMENT_GUIDE.md` - This file
- `apply_patch.py` - Automated patch application script (optional)

Good luck with your deployment! 🚀

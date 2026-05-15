#!/opt/homebrew/bin/python3.11
"""
FreeCAD MCP Bridge - Phase 1 Smart Dispatcher Architecture
Smart dispatchers aligned with FreeCAD workbench structure for optimal Claude Code integration
"""

import asyncio
import json
import os
import sys
import socket
import platform
import subprocess
import shutil
import time
import uuid
from typing import Any


# =============================================================================
# Mutable bridge state — socket target + spawned instance registry
# =============================================================================

DISCOVERY_DIR = os.path.expanduser("~/.cache/freecad-mcp/instances")


def _socket_alive(sock_path: str, timeout: float = 0.5) -> bool:
    """Return True if a Unix socket at sock_path accepts connections."""
    if not sock_path or not os.path.exists(sock_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        s.close()
        return True
    except OSError:
        return False


def _scan_discovery(prune_stale: bool = True) -> list[dict]:
    """Read ~/.cache/freecad-mcp/instances/*.json, return live records.

    On Windows this is a no-op (returns []); GUI discovery on Windows is TCP
    based and uses _ctx.socket_path directly.
    """
    if platform.system() == "Windows":
        return []
    try:
        entries = os.listdir(DISCOVERY_DIR)
    except FileNotFoundError:
        return []

    live = []
    for name in entries:
        if not name.endswith(".json"):
            continue
        path = os.path.join(DISCOVERY_DIR, name)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            if prune_stale:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            continue
        sock_path = data.get("socket_path")
        if sock_path and _socket_alive(sock_path):
            live.append(data)
        elif prune_stale:
            try:
                os.unlink(path)
            except OSError:
                pass
    return live


# =============================================================================
# Per-instance info cache — keyed by socket_path → (timestamp, info_dict)
# =============================================================================
_INFO_CACHE_TTL = 5.0
_info_cache: dict = {}


def _fetch_instance_info(sock_path: str, timeout: float = 1.0) -> dict | None:
    """Round-trip get_instance_info to a single FreeCAD instance.

    Returns the parsed result dict on success, None on any failure (so the
    caller falls back to discovery-file metadata).
    """
    if not sock_path or platform.system() == "Windows":
        return None

    now = time.time()
    cached = _info_cache.get(sock_path)
    if cached and (now - cached[0]) < _INFO_CACHE_TTL:
        return cached[1]

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        cmd = json.dumps({"tool": "get_instance_info", "args": {}})
        if not send_message(s, cmd):
            s.close()
            return None
        resp = receive_message(s, timeout=timeout + 1.0)
        s.close()
        if not resp:
            return None
        parsed = json.loads(resp)
        result = parsed.get("result") if isinstance(parsed, dict) else None
        if isinstance(result, dict):
            _info_cache[sock_path] = (now, result)
            return result
    except (OSError, json.JSONDecodeError):
        return None
    return None


class _BridgeCtx:
    """Holds the active socket path and all spawned instance metadata.

    Using a class instance (rather than closure variables) lets nested async
    functions read and write the active target without nonlocal gymnastics.

    `socket_path` starts as None on Unix; the first tool call triggers
    discovery-based auto-selection. On Windows it's a fixed TCP endpoint
    (the discovery scheme is Unix-socket-only).
    """

    def __init__(self):
        if platform.system() == "Windows":
            self.socket_path: str | None = "localhost:23456"
        else:
            # Honor an explicit env override; otherwise resolve lazily.
            self.socket_path = os.environ.get("FREECAD_MCP_SOCKET")
        # socket_path -> {pid, proc, label, headless, started_at, uuid}
        self.instances: dict = {}

    @property
    def freecad_available(self) -> bool:
        if platform.system() == "Windows":
            return True
        if not self.socket_path:
            return False
        return _socket_alive(self.socket_path)

    def register(self, sock_path: str, pid: int, proc, label: str,
                 headless: bool = True, instance_uuid: str | None = None):
        self.instances[sock_path] = {
            "socket_path": sock_path,
            "pid": pid,
            "proc": proc,
            "label": label,
            "headless": headless,
            "started_at": time.time(),
            "uuid": instance_uuid,
        }

    def unregister(self, sock_path: str):
        self.instances.pop(sock_path, None)

    def lookup_pid(self, sock_path: str | None) -> int | None:
        """Find the PID for a socket path, checking managed instances then discovery."""
        if not sock_path:
            return None
        info = self.instances.get(sock_path)
        if info and info.get("pid"):
            return info["pid"]
        for record in _scan_discovery(prune_stale=False):
            if record.get("socket_path") == sock_path:
                return record.get("pid")
        return None

    def resolve_target(self) -> tuple[str | None, str | None]:
        """Resolve the active socket path. Returns (socket_path, error_or_none).

        Resolution order:
          1. self.socket_path already set and live → use it.
          2. self.socket_path set but stale → clear, fall through.
          3. Scan discovery dir:
             - 0 live instances → error
             - 1 live instance  → auto-select, log
             - 2+ live          → error listing them
        """
        if platform.system() == "Windows":
            return self.socket_path, None

        # 1/2: previously selected target
        if self.socket_path:
            if _socket_alive(self.socket_path):
                return self.socket_path, None
            # stale — drop it and re-resolve via discovery
            self.socket_path = None

        # 3: discovery
        live = _scan_discovery()
        if not live:
            return None, (
                "No live FreeCAD instances found. Start FreeCAD with AICopilot, "
                "or call spawn_freecad_instance."
            )
        if len(live) == 1:
            self.socket_path = live[0]["socket_path"]
            return self.socket_path, None
        # multiple: require explicit selection
        listing = ", ".join(
            f"{r.get('label') or r['uuid']} (uuid={r['uuid']}, gui={r.get('gui')})"
            for r in live
        )
        return None, (
            f"{len(live)} live FreeCAD instances; cannot auto-select. "
            f"Call select_freecad_instance with one of: {listing}"
        )

    def list_all(self) -> list:
        """Merge bridge-spawned + discovered instances into a single view.

        If self.socket_path is set but not present in either source (e.g.
        FREECAD_MCP_SOCKET env override pointing at a hand-launched instance
        that doesn't write discovery files), a synthetic entry is added so
        the caller can see the active target.
        """
        result = []
        seen_paths = set()

        # Bridge-spawned (managed) instances
        for sp, info in self.instances.items():
            seen_paths.add(sp)
            result.append({
                **{k: v for k, v in info.items() if k != "proc"},
                "managed": True,
                "is_current": sp == self.socket_path,
                "available": _socket_alive(sp) if platform.system() != "Windows" else True,
            })

        # Discovered (unmanaged) instances
        if platform.system() != "Windows":
            for record in _scan_discovery():
                sp = record.get("socket_path")
                if sp in seen_paths:
                    # Already covered by managed listing — annotate with discovery extras
                    for entry in result:
                        if entry.get("socket_path") == sp:
                            entry.setdefault("uuid", record.get("uuid"))
                            entry.setdefault("gui", record.get("gui"))
                            entry.setdefault("freecad_version", record.get("freecad_version"))
                    continue
                seen_paths.add(sp)
                result.append({
                    "socket_path": sp,
                    "uuid": record.get("uuid"),
                    "pid": record.get("pid"),
                    "label": record.get("label"),
                    "gui": record.get("gui"),
                    "headless": not record.get("gui", False),
                    "started_at": record.get("started_at"),
                    "freecad_version": record.get("freecad_version"),
                    "freecad_binary": record.get("freecad_binary"),
                    "managed": False,
                    "is_current": sp == self.socket_path,
                    "available": True,  # scan_discovery already pruned dead
                })

        # Synthetic entry for an explicit env-var target that isn't tracked anywhere
        if self.socket_path and self.socket_path not in seen_paths:
            result.append({
                "socket_path": self.socket_path,
                "label": "default",
                "headless": False,
                "managed": False,
                "is_current": True,
                "available": self.freecad_available,
            })

        return result


_ctx = _BridgeCtx()


# =============================================================================
# FreeCADCmd / headless_server.py discovery helpers
# =============================================================================

def _find_freecadcmd() -> str | None:
    """Return path to FreeCADCmd binary, or None if not found.

    Search order:
      1. FREECAD_MCP_FREECAD_BIN env var (explicit override)
      2. shutil.which for common binary names
      3. macOS app bundle locations
      4. Linux/common system paths
    """
    override = os.environ.get("FREECAD_MCP_FREECAD_BIN")
    if override and os.path.isfile(override):
        return override

    for name in ("FreeCADCmd", "freecadcmd", "FreeCAD", "freecad"):
        path = shutil.which(name)
        if path:
            return path

    mac_candidates = [
        "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.0.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.1.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.2.app/Contents/MacOS/FreeCADCmd",
        # Local build (FC-clone)
        os.path.expanduser("~/Documents/FC-clone/build/release/bin/FreeCADCmd"),
        "/Volumes/Files/claude/FC-clone/build/release/bin/FreeCADCmd",
    ]
    for p in mac_candidates:
        if os.path.isfile(p):
            return p

    return None


def _find_freecad_gui() -> str | None:
    """Return path to the FreeCAD GUI binary, or None if not found.

    Used by spawn_freecad_instance(gui=True) to launch a GUI FreeCAD with a
    custom env (FREECAD_MCP_SOCKET / FREECAD_MCP_LABEL). On macOS we deliberately
    target the inner Mach-O at .app/Contents/MacOS/FreeCAD — going through
    `open` would dedupe to an existing process and not propagate env vars.

    Search order:
      1. FREECAD_MCP_FREECAD_GUI_BIN env var (explicit override)
      2. shutil.which("FreeCAD" / "freecad")  — Linux distro install
      3. macOS app bundle inner binaries
      4. Local builds
    """
    override = os.environ.get("FREECAD_MCP_FREECAD_GUI_BIN")
    if override and os.path.isfile(override):
        return override

    if platform.system() != "Darwin":
        for name in ("FreeCAD", "freecad"):
            path = shutil.which(name)
            if path:
                return path

    mac_candidates = [
        "/Applications/FreeCAD.app/Contents/MacOS/FreeCAD",
        "/Applications/FreeCAD 1.0.app/Contents/MacOS/FreeCAD",
        "/Applications/FreeCAD 1.1.app/Contents/MacOS/FreeCAD",
        "/Applications/FreeCAD 1.2.app/Contents/MacOS/FreeCAD",
        os.path.expanduser("~/Documents/FC-clone/build/release/bin/FreeCAD"),
        "/Volumes/Files/claude/FC-clone/build/release/bin/FreeCAD",
    ]
    for p in mac_candidates:
        if os.path.isfile(p):
            return p

    return None


def _find_headless_script() -> str | None:
    """Return path to headless_server.py, or None if not found.

    Search order:
      1. FREECAD_MCP_MODULE_DIR env var / headless_server.py
      2. Alongside the bridge script (for dev workflows)
      3. ~/.freecad-mcp/ (standard install)
      4. Known FreeCAD addon paths from MEMORY.md
    """
    override_dir = os.environ.get("FREECAD_MCP_MODULE_DIR")
    if override_dir:
        p = os.path.join(override_dir, "headless_server.py")
        if os.path.isfile(p):
            return p

    bridge_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # Dev: AICopilot/ sibling of bridge
        os.path.join(bridge_dir, "AICopilot", "headless_server.py"),
        # Standard install
        os.path.expanduser("~/.freecad-mcp/AICopilot/headless_server.py"),
        # Known addon paths (from MEMORY.md)
        "/Volumes/Files/claude/FreeCAD-prefs/Mod/AICopilot/headless_server.py",
        "/Volumes/Files/claude/FreeCAD-prefs/v1-2/Mod/AICopilot/headless_server.py",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p

    return None

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import message framing for v2.1.1 protocol
from mcp_bridge_framing import send_message, receive_message

# ── Crash diagnostics (always enabled — no optional flag) ──────────────────
import importlib.util as _ilu
import os as _os

def _load_crash_report():
    """Load freecad_crash_report from same dir as this script, or ~/.freecad-mcp/."""
    for candidate in [
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "freecad_crash_report.py"),
        _os.path.expanduser("~/.freecad-mcp/freecad_crash_report.py"),
    ]:
        if _os.path.isfile(candidate):
            spec = _ilu.spec_from_file_location("freecad_crash_report", candidate)
            mod  = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None

_crash_mod = _load_crash_report()
_op_log    = _crash_mod.get_op_log() if _crash_mod else None

def _record_op(tool: str, args: dict) -> None:
    if _op_log is not None:
        _op_log.record(tool, args)

def _complete_op() -> None:
    if _op_log is not None:
        _op_log.complete()

# Progressive poll backoff: fast first polls catch quick ops, then settle at 1 s.
_POLL_BACKOFF_SECS = [0.05, 0.1, 0.25, 0.5, 1.0]
_POLL_TIMEOUT_SECS = 120  # 2-minute ceiling; return job_id so caller can cancel

def _diagnose_crash(error: Exception = None) -> str:
    if _crash_mod is None:
        return f"FreeCAD connection lost: {error}"
    info = _ctx.instances.get(_ctx.socket_path, {}) if _ctx.socket_path else {}
    proc = info.get("proc")
    pid = info.get("pid") or _ctx.lookup_pid(_ctx.socket_path)
    return _crash_mod.diagnose(
        socket_path=_ctx.socket_path,
        proc=proc,
        op_log=_op_log,
        error=error,
        pid=pid,
    )

# Initialize debugging infrastructure (optional - works without it)
try:
    from freecad_debug import init_debugger, debug_deccorator
    from freecad_health import init_monitor
    import logging
    
    # Initialize with file-only logging (no console output for MCP)
    debugger = init_debugger(
        log_dir="/tmp/freecad_mcp_debug",
        level=logging.DEBUG,
        enable_console=False,  # CRITICAL: No console output for MCP!
        enable_file=True
    )
    monitor = init_monitor()
    
    # Log startup to file only
    debugger.logger.info("="*80)
    debugger.logger.info("FreeCAD MCP Bridge Starting with Debug Infrastructure")
    debugger.logger.info("="*80)
    DEBUG_ENABLED = True
except ImportError:
    debugger = None
    monitor = None
    DEBUG_ENABLED = False
    
    def debug_decorator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

async def main():
    """Run MCP server for FreeCAD integration"""
    try:
        # Import MCP components with correct API
        import mcp.types as types
        from mcp.server import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
    except ImportError as e:
        # MCP import failed - exit silently to avoid STDIO corruption
        sys.exit(1)

    # Create server with freecad naming
    server = Server("freecad")

    @debug_decorator(track_state=False, track_performance=True)
    async def send_to_freecad(tool_name: str, args: dict) -> str:
        """Send command to FreeCAD via socket (cross-platform)"""
        # Record operation before sending (bridge-side crash tracking)
        _record_op(tool_name, args)
        try:
            # Create socket connection based on platform
            if platform.system() == "Windows":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(('localhost', 23456))
            else:
                current_path, err = _ctx.resolve_target()
                if err:
                    return json.dumps({"error": err})
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(current_path)

            # Send command with length-prefixed protocol (v2.1.1)
            command = json.dumps({"tool": tool_name, "args": args})
            if not send_message(sock, command):
                sock.close()
                return json.dumps({"error": "Failed to send command to FreeCAD"})

            # Receive response with length-prefixed protocol (v2.1.1)
            # Use caller's timeout if provided (e.g., execute_python long ops)
            recv_timeout = float(args.get("timeout", 30.0)) if isinstance(args, dict) else 30.0
            # Add 5s grace period so server-side timeout fires first
            response = receive_message(sock, timeout=recv_timeout + 5.0)
            sock.close()

            if response is None:
                report = _diagnose_crash()
                return json.dumps({"error": report})

            # Check if this is a selection workflow response
            try:
                result = json.loads(response)
                if isinstance(result, dict) and result.get("status") == "awaiting_selection":
                    # Handle interactive selection workflow
                    return await handle_selection_workflow(tool_name, args, result)
            except json.JSONDecodeError:
                pass  # Not JSON, return as-is

            _complete_op()   # mark successful on the bridge side
            return response

        except Exception as e:
            # ── Crash diagnosis ──────────────────────────────────────────────
            # Log to optional debug infrastructure if present
            if DEBUG_ENABLED and debugger:
                debugger.log_operation(
                    operation="send_to_freecad",
                    parameters={"tool_name": tool_name, "args": args},
                    error=e
                )
                if monitor:
                    status = monitor.perform_health_check()
                    if not status['is_healthy']:
                        monitor.log_crash(status, {
                            "triggered_by": "socket_error",
                            "tool_name": tool_name,
                            "args": args
                        })
            # Always produce a rich crash report (replaces generic "Connection refused")
            report = _diagnose_crash(error=e)
            return json.dumps({"error": report})
    
    async def poll_job_until_done(job_id: str, context: str = "Operation") -> dict:
        """Poll a FreeCAD async job with progressive backoff.

        Returns the final poll_resp dict. On timeout returns a dict with
        status="timeout" and the job_id so the caller can surface it.
        """
        delays = iter(_POLL_BACKOFF_SECS)
        delay = next(delays)
        poll_start = time.time()
        while True:
            await asyncio.sleep(delay)
            try:
                delay = next(delays)
            except StopIteration:
                delay = 1.0
            if time.time() - poll_start > _POLL_TIMEOUT_SECS:
                return {
                    "status": "timeout",
                    "error": (
                        f"{context} timed out after {_POLL_TIMEOUT_SECS}s. "
                        f"Job {job_id} may still be running. "
                        f"Use poll_job(job_id='{job_id}') to check status, "
                        f"or cancel_job(job_id='{job_id}') to abort."
                    ),
                    "job_id": job_id,
                }
            poll_resp = json.loads(await send_to_freecad("poll_job", {"job_id": job_id}))
            status = poll_resp.get("status")
            if status in ("done", "error"):
                return poll_resp
            if "error" in poll_resp and "Crash" in poll_resp.get("error", ""):
                return poll_resp
            # status == "running" → keep polling

    async def handle_selection_workflow(tool_name: str, original_args: dict, selection_request: dict) -> str:
        """Handle the interactive selection workflow - Claude Code style"""
        try:
            # Format the interactive message for Claude Code
            message = selection_request.get("message", "Please make selection in FreeCAD")
            selection_type = selection_request.get("selection_type", "elements")
            object_name = selection_request.get("object_name", "")
            operation_id = selection_request.get("operation_id", "")
            
            # Create Claude Code compatible interactive response
            interactive_response = {
                "interactive": True,
                "message": f"🎯 Interactive Selection Required\n\n{message}",
                "operation_id": operation_id,
                "selection_type": selection_type,
                "object_name": object_name,
                "tool_name": tool_name,
                "original_args": original_args,
                "instructions": f"1. Go to FreeCAD and select {selection_type} on {object_name}\n2. Return here and choose an option:"
            }
            
            return json.dumps(interactive_response)
            
        except Exception as e:
            return json.dumps({"error": f"Selection workflow error: {e}"})
    
    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        """List available Phase 1 smart dispatcher tools"""
        base_tools = [
            types.Tool(
                name="check_freecad_connection",
                description="Check if FreeCAD is running with AICopilot installed",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                ),
            ),
            types.Tool(
                name="test_echo",
                description="Test tool that echoes back a message",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to echo back"
                        }
                    },
                    "required": ["message"]
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                ),
            ),
            types.Tool(
                name="restart_freecad",
                description="Restart FreeCAD: saves open documents, spawns new instance, exits current. Use when FreeCAD is unresponsive or needs to reload addons.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "save_documents": {
                            "type": "boolean",
                            "description": "Save open documents before restart (default true)",
                            "default": True,
                        },
                        "reopen_documents": {
                            "type": "boolean",
                            "description": "Reopen documents in new instance (default true)",
                            "default": True,
                        }
                    },
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                ),
            ),
            types.Tool(
                name="reload_modules",
                description="Hot-reload all handler modules without restarting FreeCAD. Use after deploying new code (rsync) to pick up changes immediately.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                ),
            ),
            types.Tool(
                name="manage_connection",
                description=(
                    "Diagnostic and lifecycle management for the FreeCAD/bridge connection. "
                    "Actions:\n"
                    "  status  — connection state, recovery file health, crash-loop detection\n"
                    "  clear_recovery — remove corrupt FreeCAD session/autosave files that "
                    "cause crash loops (FreeCAD crashes immediately on every restart). "
                    "Safe: only deletes files that fail ZIP validation.\n"
                    "  validate_fcstd — check whether a saved .FCStd file is an intact ZIP archive"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "One of: status, clear_recovery, validate_fcstd",
                        },
                        "path": {
                            "type": "string",
                            "description": "FCStd file path (required for validate_fcstd action)",
                        },
                    },
                    "required": ["action"],
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                ),
            ),
        ]

        # Always expose all smart dispatchers; check_freecad_connection / spawn
        # let callers inspect or establish a connection at runtime.
        if True:
            smart_dispatchers = [
                types.Tool(
                    name="partdesign_operations", 
                    description="⚠️ MODIFIES FreeCAD document: Smart dispatcher for parametric features. Operations like fillet/chamfer require edge selection and will permanently modify the 3D model.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "PartDesign operation to perform",
                                "enum": [
                                    # Additive features
                                    "pad", "revolution", "loft", "sweep", "additive_pipe",
                                    # Subtractive features
                                    "pocket", "groove", "subtractive_loft", "subtractive_sweep",
                                    # Dress-up features
                                    "fillet", "chamfer", "draft", "shell", "thickness",
                                    # Hole features
                                    "hole", "counterbore", "countersink",
                                    # Pattern features
                                    "linear_pattern", "polar_pattern", "mirror",
                                    # Additional features
                                    "helix", "rib",
                                    # Datum features
                                    "datum_plane", "datum_line", "datum_point",
                                    "datum_from_face"
                                ]
                            },
                            "face_index": {"type": "integer", "description": "1-based face index (from list_faces output)"},
                            "sketch_name": {"type": "string", "description": "Sketch name for operations"},
                            "object_name": {"type": "string", "description": "Object name for dress-up operations"},
                            "feature_name": {"type": "string", "description": "Feature name for pattern operations"},
                            # Common parameters
                            "length": {"type": "number", "description": "Length/depth for pad", "default": 10},
                            "radius": {"type": "number", "description": "Radius for fillet/holes", "default": 1},
                            "distance": {"type": "number", "description": "Distance for chamfer", "default": 1},
                            "angle": {"type": "number", "description": "Angle for revolution/draft", "default": 360},
                            "thickness": {"type": "number", "description": "Thickness value", "default": 2},
                            # Pattern parameters
                            "count": {"type": "integer", "description": "Pattern count", "default": 3},
                            "spacing": {"type": "number", "description": "Pattern spacing", "default": 10},
                            "axis": {"type": "string", "description": "Axis for patterns", "enum": ["x", "y", "z"], "default": "x"},
                            "plane": {"type": "string", "description": "Mirror plane", "enum": ["XY", "XZ", "YZ"], "default": "YZ"},
                            # Hole parameters
                            "diameter": {"type": "number", "description": "Hole diameter", "default": 6},
                            "depth": {"type": "number", "description": "Hole depth", "default": 10},
                            "x": {"type": "number", "description": "X position", "default": 0},
                            "y": {"type": "number", "description": "Y position", "default": 0},
                            # Datum parameters
                            "map_mode": {"type": "string", "description": "Attachment mode for datums (e.g. FlatFace, ObjectXY, ObjectXZ)"},
                            "reference": {"type": "string", "description": "Face/edge/vertex reference (e.g. Face1, Edge3)"},
                            "reference_object": {"type": "string", "description": "Object name containing the reference"},
                            "offset_x": {"type": "number", "description": "X offset from attached position", "default": 0},
                            "offset_y": {"type": "number", "description": "Y offset from attached position", "default": 0},
                            "offset_z": {"type": "number", "description": "Z offset / normal offset", "default": 0},
                            # Direction control
                            "reversed": {"type": "boolean", "description": "Reverse pocket/pad direction (cut/extrude opposite to sketch normal)"},
                            # datum_from_face parameters
                            "face_index": {"type": "integer", "description": "1-based face index (from list_faces output)"},
                            "offset": {"type": "number", "description": "Offset along face normal in mm", "default": 0},
                            # Advanced parameters
                            "name": {"type": "string", "description": "Name for result feature"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="sketch_operations",
                    description="Smart dispatcher for all Sketcher workbench operations: geometry creation, constraints, and sketch management. "
                                "Geometry IDs (geo_id) are assigned in order starting at 0. "
                                "Point indices: 0=edge itself, 1=start point, 2=end point, 3=center. "
                                "Special geo_ids: -1=X axis, -2=Y axis, -3 and below=external geometry.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Sketch operation to perform",
                                "enum": [
                                    # Lifecycle
                                    "create_sketch", "close_sketch", "verify_sketch",
                                    # Geometry
                                    "add_line", "add_circle", "add_rectangle", "add_arc",
                                    "add_polygon", "add_slot", "add_fillet",
                                    # Constraints
                                    "add_constraint", "delete_constraint", "list_constraints",
                                    # External geometry
                                    "add_external_geometry"
                                ]
                            },
                            # Sketch identification
                            "sketch_name": {"type": "string", "description": "Name of the sketch to operate on"},
                            "name": {"type": "string", "description": "Name for new sketch (create_sketch)"},
                            "plane": {"type": "string", "description": "Sketch plane: XY, XZ, or YZ", "enum": ["XY", "XZ", "YZ"], "default": "XY"},
                            # Line parameters
                            "x1": {"type": "number", "description": "Line start X", "default": 0},
                            "y1": {"type": "number", "description": "Line start Y", "default": 0},
                            "x2": {"type": "number", "description": "Line end X", "default": 10},
                            "y2": {"type": "number", "description": "Line end Y", "default": 10},
                            # Circle/arc/polygon parameters
                            "x": {"type": "number", "description": "Center X / origin X", "default": 0},
                            "y": {"type": "number", "description": "Center Y / origin Y", "default": 0},
                            "radius": {"type": "number", "description": "Radius for circle/arc/polygon/fillet", "default": 5},
                            "center_x": {"type": "number", "description": "Arc center X", "default": 0},
                            "center_y": {"type": "number", "description": "Arc center Y", "default": 0},
                            "start_angle": {"type": "number", "description": "Arc start angle (degrees)", "default": 0},
                            "end_angle": {"type": "number", "description": "Arc end angle (degrees)", "default": 90},
                            # Rectangle parameters
                            "width": {"type": "number", "description": "Rectangle width", "default": 10},
                            "height": {"type": "number", "description": "Rectangle height", "default": 10},
                            "constrain": {"type": "boolean", "description": "Auto-add constraints to rectangle/polygon", "default": True},
                            # Polygon parameters
                            "sides": {"type": "integer", "description": "Number of polygon sides", "default": 6},
                            # Slot parameters
                            "length": {"type": "number", "description": "Slot total length", "default": 20},
                            # Constraint parameters
                            "constraint_type": {
                                "type": "string",
                                "description": "Constraint type for add_constraint",
                                "enum": [
                                    "Coincident", "PointOnObject",
                                    "Horizontal", "Vertical",
                                    "Perpendicular", "Parallel", "Tangent", "Equal",
                                    "Symmetric", "Block", "Fix",
                                    "Distance", "DistanceX", "DistanceY",
                                    "Radius", "Diameter", "Angle"
                                ]
                            },
                            "geo_id1": {"type": "integer", "description": "First geometry index (0+ for user geometry, -1=X axis, -2=Y axis)", "default": 0},
                            "pos_id1": {"type": "integer", "description": "First point index (0=edge, 1=start, 2=end, 3=center)", "default": 0},
                            "geo_id2": {"type": "integer", "description": "Second geometry index"},
                            "pos_id2": {"type": "integer", "description": "Second point index", "default": 0},
                            "value": {"type": "number", "description": "Constraint value (mm for distance, degrees for angle)"},
                            "sym_geo": {"type": "integer", "description": "Symmetry axis geo_id (Symmetric constraint)", "default": -2},
                            "sym_pos": {"type": "integer", "description": "Symmetry axis point index", "default": 0},
                            # Delete constraint
                            "index": {"type": "integer", "description": "Constraint index for delete_constraint"},
                            # Fillet parameters
                            "geo_id": {"type": "integer", "description": "Geometry index for sketch fillet", "default": 0},
                            "pos_id": {"type": "integer", "description": "Point index for sketch fillet (1=start, 2=end)", "default": 2},
                            # External geometry
                            "object_name": {"type": "string", "description": "Object name for external geometry reference"},
                            "edge_name": {"type": "string", "description": "Edge name for external geometry (e.g. Edge1)"},
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="part_operations",
                    description="Smart dispatcher for all basic solid and boolean operations (18+ operations)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Part operation to perform", 
                                "enum": [
                                    # Primitive creation (6)
                                    "box", "cylinder", "sphere", "cone", "torus", "wedge",
                                    # Boolean operations (4)
                                    "fuse", "cut", "common", "section",
                                    # Transform operations (4)
                                    "move", "rotate", "scale", "mirror",
                                    # Advanced creation (4)
                                    "loft", "sweep", "extrude", "revolve",
                                    # Text / geometry utilities
                                    "shape_string", "compound", "check_geometry"
                                ]
                            },
                            # Primitive parameters
                            "length": {"type": "number", "description": "Box length", "default": 10},
                            "width": {"type": "number", "description": "Box width", "default": 10},
                            "height": {"type": "number", "description": "Box/cylinder height", "default": 10},
                            "radius": {"type": "number", "description": "Sphere/cylinder radius", "default": 5},
                            "radius1": {"type": "number", "description": "Major radius for torus/cone", "default": 10},
                            "radius2": {"type": "number", "description": "Minor radius for torus/cone", "default": 3},
                            # Position parameters
                            "x": {"type": "number", "description": "X position", "default": 0},
                            "y": {"type": "number", "description": "Y position", "default": 0},
                            "z": {"type": "number", "description": "Z position", "default": 0},
                            # Boolean operation parameters
                            "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names for boolean ops"},
                            "base": {"type": "string", "description": "Base object for cut operation"},
                            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool objects for cut"},
                            # Transform parameters
                            "object_name": {"type": "string", "description": "Object to transform"},
                            "axis": {"type": "string", "description": "Rotation axis", "enum": ["x", "y", "z"], "default": "z"},
                            "angle": {"type": "number", "description": "Rotation angle", "default": 90},
                            "scale_factor": {"type": "number", "description": "Scale factor", "default": 1.5},
                            # Advanced creation parameters
                            "sketches": {"type": "array", "items": {"type": "string"}, "description": "Sketches for loft"},
                            "profile_sketch": {"type": "string", "description": "Profile sketch for sweep"},
                            "path_sketch": {"type": "string", "description": "Path sketch for sweep"},
                            # ShapeString parameters
                            "string": {"type": "string", "description": "Text string for shape_string"},
                            "font_file": {"type": "string", "description": "Path to .ttf font (auto-discovered if omitted)"},
                            "size": {"type": "number", "description": "Text size in mm", "default": 10},
                            "tracking": {"type": "number", "description": "Character spacing in mm", "default": 0},
                            # Naming
                            "name": {"type": "string", "description": "Name for result object"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="view_control",
                    description="Smart dispatcher for all view, screenshot, and document operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "View control operation",
                                "enum": [
                                    # View operations
                                    "screenshot", "set_view", "fit_all", "zoom_in", "zoom_out",
                                    # Document operations  
                                    "create_document", "save_document", "list_objects",
                                    # Selection operations
                                    "select_object", "clear_selection", "get_selection",
                                    # Object visibility
                                    "hide_object", "show_object", "delete_object",
                                    # History operations
                                    "undo", "redo",
                                    # Workbench control
                                    "activate_workbench",
                                    # Diagnostics
                                    "get_report_view",
                                    # Section view (clip plane)
                                    "add_clip_plane", "remove_clip_plane",
                                    # Checkpoint / rollback
                                    "checkpoint", "rollback_to_checkpoint",
                                    # Multi-doc shape import
                                    "insert_shape"
                                ]
                            },
                            # Screenshot parameters
                            "width": {"type": "integer", "description": "Screenshot width", "default": 800},
                            "height": {"type": "integer", "description": "Screenshot height", "default": 600},
                            # View parameters
                            "view_type": {"type": "string", "description": "View orientation",
                                         "enum": ["top", "front", "left", "right", "isometric", "axonometric"],
                                         "default": "isometric"},
                            # Document parameters
                            "document_name": {"type": "string", "description": "Document name", "default": "Unnamed"},
                            "filename": {"type": "string", "description": "File path to save"},
                            # Object parameters
                            "object_name": {"type": "string", "description": "Object name for operations"},
                            # Workbench parameters
                            "workbench_name": {"type": "string", "description": "Workbench name to activate"},
                            # get_report_view parameters
                            "tail": {"type": "integer", "description": "Number of lines to return from the end (0 = all)", "default": 50},
                            "filter": {"type": "string", "description": "Substring to filter lines by (case-insensitive)"},
                            "clear": {"type": "boolean", "description": "Clear the Report View after reading", "default": False},
                            # Clip plane (add_clip_plane) parameters
                            "axis": {"type": "string", "description": "Clip plane normal axis", "enum": ["x", "y", "z"], "default": "z"},
                            "depth": {"type": "number", "description": "Distance along axis where clip plane cuts (mm)", "default": 0},
                            # Checkpoint parameters
                            "name": {"type": "string", "description": "Checkpoint label (default 'default')"},
                            # insert_shape parameters
                            "source_doc": {"type": "string", "description": "Source document name"},
                            "source_object": {"type": "string", "description": "Object name in source document"},
                            "x": {"type": "number", "description": "X placement offset (mm)", "default": 0},
                            "y": {"type": "number", "description": "Y placement offset (mm)", "default": 0},
                            "z": {"type": "number", "description": "Z placement offset (mm)", "default": 0}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="cam_operations",
                    description="Smart dispatcher for CAM (Path) workbench - CNC toolpath generation and machining operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "CAM operation to perform",
                                "enum": [
                                    # Job management (5)
                                    "create_job", "setup_stock", "configure_job", "inspect_job", "job_status", "delete_job",
                                    # Primary milling operations (12)
                                    "profile", "pocket", "adaptive", "face", "helix", "slot",
                                    "engrave", "vcarve", "deburr", "surface", "surface_stl", "waterline", "pocket_3d",
                                    # Drilling operations (2)
                                    "drilling", "thread_milling",
                                    # Dressup operations (7)
                                    "dogbone", "lead_in_out", "ramp_entry", "tag", "axis_map",
                                    "drag_knife", "z_correct",
                                    # Operation management (4)
                                    "list_operations", "get_operation", "configure_operation", "delete_operation",
                                    # Tool management (2) - deprecated, use cam_tools and cam_tool_controllers instead
                                    "create_tool", "tool_controller",
                                    # Utility operations (4)
                                    "simulate", "simulate_job", "post_process", "export_gcode", "inspect"
                                ]
                            },
                            # Job parameters
                            "job_name": {"type": "string", "description": "CAM job name"},
                            "base_object": {"type": "string", "description": "Base 3D object for CAM operations"},
                            # Stock parameters
                            "stock_type": {"type": "string", "description": "Stock type", "enum": ["CreateBox", "CreateCylinder", "FromBase"], "default": "CreateBox"},
                            "length": {"type": "number", "description": "Stock length", "default": 100},
                            "width": {"type": "number", "description": "Stock width", "default": 100},
                            "height": {"type": "number", "description": "Stock height", "default": 50},
                            "extent_x": {"type": "number", "description": "Stock extent in X", "default": 10},
                            "extent_y": {"type": "number", "description": "Stock extent in Y", "default": 10},
                            "extent_z": {"type": "number", "description": "Stock extent in Z", "default": 10},
                            # Operation parameters
                            "faces": {"type": "array", "items": {"type": "string"}, "description": "Face names for profile/pocket base geometry e.g. ['Face1','Face3']. Omit for whole-model exterior contour."},
                            "edges": {"type": "array", "items": {"type": "string"}, "description": "Edge names for profile base geometry e.g. ['Edge1','Edge4']."},
                            "side": {"type": "string", "description": "Profile cut side: Outside (default) cuts outside the contour, Inside cuts inside", "enum": ["Outside", "Inside"], "default": "Outside"},
                            "cut_side": {"type": "string", "description": "Deprecated alias for side", "enum": ["Outside", "Inside"]},
                            "process_perimeter": {"type": "boolean", "description": "Profile: trace outer boundary of selected faces (default true)"},
                            "process_holes": {"type": "boolean", "description": "Profile: trace inner holes of selected faces (default false)"},
                            "process_circles": {"type": "boolean", "description": "Profile: treat circular holes as drillable (default false)"},
                            "direction": {"type": "string", "description": "Cut direction", "enum": ["CW", "CCW"]},
                            "stepdown": {"type": "number", "description": "Stepdown depth"},
                            "stepover": {"type": "number", "description": "Stepover percentage"},
                            "cut_mode": {"type": "string", "description": "Cutting mode", "enum": ["Climb", "Conventional"]},
                            # Drilling parameters
                            "depth": {"type": "number", "description": "Drilling depth"},
                            "retract_height": {"type": "number", "description": "Retract height"},
                            "peck_depth": {"type": "number", "description": "Peck drilling depth"},
                            "dwell_time": {"type": "number", "description": "Dwell time in seconds"},
                            # Tool parameters
                            "tool_type": {"type": "string", "description": "Tool type", "enum": ["endmill", "ballend", "bullnose", "chamfer", "drill"], "default": "endmill"},
                            "tool_name": {"type": "string", "description": "Tool name"},
                            "diameter": {"type": "number", "description": "Tool diameter", "default": 6.0},
                            "spindle_speed": {"type": "number", "description": "Spindle speed in RPM", "default": 10000},
                            "feed_rate": {"type": "number", "description": "Feed rate in mm/min", "default": 1000},
                            # Post-processing parameters
                            "output_file": {"type": "string", "description": "Output G-code file path"},
                            "post_processor": {"type": "string", "description": "Post processor name", "default": "grbl"},
                            "post_processor_args": {"type": "string", "description": "Post processor arguments (e.g. '--no-show-editor')"},
                            # Adaptive parameters
                            "tolerance": {"type": "number", "description": "Adaptive tolerance"},
                            # General
                            "name": {"type": "string", "description": "Name for the operation"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="cam_tools",
                    description="CAM Tool Library Management - CRUD operations for cutting tools",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Tool library operation",
                                "enum": ["create_tool", "list_tools", "get_tool", "update_tool", "delete_tool"]
                            },
                            "tool_name": {"type": "string", "description": "Name of the tool"},
                            "tool_type": {
                                "type": "string",
                                "description": "Type of tool",
                                "enum": ["endmill", "ballend", "bullnose", "chamfer", "drill", "v-bit"],
                                "default": "endmill"
                            },
                            "diameter": {"type": "number", "description": "Tool diameter in mm", "default": 6.0},
                            "flute_length": {"type": "number", "description": "Cutting edge length in mm"},
                            "shank_diameter": {"type": "number", "description": "Shank diameter in mm"},
                            "material": {"type": "string", "description": "Tool material (HSS, Carbide, etc.)"},
                            "number_of_flutes": {"type": "integer", "description": "Number of flutes"},
                            "name": {"type": "string", "description": "Tool name (for create operation)"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="cam_tool_controllers",
                    description="CAM Tool Controller Management - CRUD operations for tool controllers (link tools to jobs with speeds/feeds)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Tool controller operation",
                                "enum": ["add_tool_controller", "list_tool_controllers", "get_tool_controller", "update_tool_controller", "remove_tool_controller"]
                            },
                            "job_name": {"type": "string", "description": "CAM job name"},
                            "tool_name": {"type": "string", "description": "Name of the tool bit to use"},
                            "controller_name": {"type": "string", "description": "Name for the tool controller"},
                            "spindle_speed": {"type": "number", "description": "Spindle speed in RPM", "default": 10000},
                            "feed_rate": {"type": "number", "description": "Horizontal feed rate in mm/min", "default": 1000},
                            "vertical_feed_rate": {"type": "number", "description": "Vertical (plunge) feed rate in mm/min"},
                            "tool_number": {"type": "integer", "description": "Tool number for G-code", "default": 1}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="spreadsheet_operations",
                    description="Spreadsheet operations for data management and calculations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Spreadsheet operation to perform",
                                "enum": [
                                    "create_spreadsheet", "set_cell", "get_cell",
                                    "set_alias", "get_alias", "clear_cell",
                                    "set_cell_range", "get_cell_range"
                                ]
                            },
                            "name": {"type": "string", "description": "Spreadsheet name"},
                            "cell": {"type": "string", "description": "Cell address (e.g., 'A1')"},
                            "value": {"type": ["string", "number"], "description": "Cell value"},
                            "alias": {"type": "string", "description": "Cell alias name"},
                            "start_cell": {"type": "string", "description": "Range start cell"},
                            "end_cell": {"type": "string", "description": "Range end cell"},
                            "values": {"type": "array", "description": "Array of values for range"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="draft_operations",
                    description="Draft workbench operations: arrays, clones, text annotations, and ShapeString (extrudable 3D text)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Draft operation to perform",
                                "enum": [
                                    "clone", "array", "polar_array", "path_array", "point_array",
                                    "shape_string", "text"
                                ]
                            },
                            "object_name": {"type": "string", "description": "Object to operate on"},
                            "count": {"type": "integer", "description": "Array count"},
                            "spacing": {"type": "number", "description": "Array spacing"},
                            "angle": {"type": "number", "description": "Polar array angle"},
                            "string": {"type": "string", "description": "Text string for shape_string"},
                            "text": {"description": "Text content for text annotation (string or list of strings)", "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                            "font_file": {"type": "string", "description": "Path to .ttf font file (optional, auto-discovered if omitted)"},
                            "size": {"type": "number", "description": "Text size in mm", "default": 10},
                            "tracking": {"type": "number", "description": "Character spacing in mm", "default": 0},
                            "x": {"type": "number", "description": "X position", "default": 0},
                            "y": {"type": "number", "description": "Y position", "default": 0},
                            "z": {"type": "number", "description": "Z position", "default": 0},
                            "name": {"type": "string", "description": "Label for created object"}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="mesh_operations",
                    description="Mesh import/export, mesh-to-solid conversion, validation, simplification, and CAD file I/O (STL, OBJ, STEP, IGES, BREP)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Mesh/file operation to perform",
                                "enum": [
                                    "import_mesh", "export_mesh", "mesh_to_solid",
                                    "get_mesh_info", "import_file", "export_file",
                                    "validate_mesh", "simplify_mesh"
                                ]
                            },
                            "file_path": {"type": "string", "description": "File path for import/export"},
                            "object_name": {"type": "string", "description": "Object name to operate on"},
                            "name": {"type": "string", "description": "Name for created object"},
                            "tolerance": {"type": "number", "description": "Mesh-to-solid sewing tolerance", "default": 0.1},
                            "linear_deflection": {"type": "number", "description": "Tessellation linear deflection for Part-to-mesh export", "default": 0.1},
                            "angular_deflection": {"type": "number", "description": "Tessellation angular deflection for Part-to-mesh export"},
                            "target_count": {"type": "integer", "description": "Target face count for mesh simplification"},
                            "reduction": {"type": "number", "description": "Reduction ratio 0-1 for mesh simplification (e.g., 0.5 = 50% fewer faces)"},
                            "auto_repair": {"type": "boolean", "description": "Auto-repair mesh issues during validation", "default": False}
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="measurement_operations",
                    description="Inspect object geometry: face normals/centroids, bounding boxes, volume, surface area, center of mass, element counts",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Measurement operation to perform",
                                "enum": [
                                    "list_faces", "get_bounding_box", "get_volume",
                                    "get_surface_area", "get_center_of_mass",
                                    "get_mass_properties", "count_elements",
                                    "check_solid", "measure_distance"
                                ]
                            },
                            "object_name": {"type": "string", "description": "Object to inspect"},
                            "object1": {"type": "string", "description": "First object (measure_distance)"},
                            "object2": {"type": "string", "description": "Second object (measure_distance)"},
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="spatial_query",
                    description="Analyze spatial relationships between objects: interference/collision detection, clearance measurement, containment checks, face-to-face analysis, batch interference, alignment verification",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Spatial query to perform",
                                "enum": [
                                    "interference_check", "clearance", "containment",
                                    "face_relationship", "batch_interference",
                                    "alignment_check"
                                ]
                            },
                            "object1": {"type": "string", "description": "First object name"},
                            "object2": {"type": "string", "description": "Second object name"},
                            "objects": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of object names (batch_interference)"
                            },
                            "face1": {"type": "string", "description": "Face on object1 (e.g. 'Face6') for face_relationship"},
                            "face2": {"type": "string", "description": "Face on object2 (e.g. 'Face3') for face_relationship"},
                            "axis": {"type": "string", "description": "Axis for alignment_check: X, Y, or Z (default Z)", "enum": ["X", "Y", "Z"]},
                        },
                        "required": ["operation"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="run_inspector",
                    description="Run FreeCAD Inspector DRC checks on the active document. "
                                "Checks model validity (open shells, zero-volume solids, invalid geometry, "
                                "degenerate faces, disconnected shells, coincident/interfering objects) and "
                                "TNP robustness (direct face attachment, expression sub-shape references, "
                                "no datum strategy). With profile_process='resin', also checks minimum "
                                "feature size, wall thickness, overhang angles, build volume, and trapped volumes.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "profile_process": {
                                "type": "string",
                                "description": "Manufacturing process for process-specific rules. "
                                               "Omit for model-only checks.",
                                "enum": ["resin", "laser", "cnc_3axis"]
                            },
                            "machine": {
                                "type": "string",
                                "description": "Machine name for profile (e.g. 'AnyCubic M7 Pro'). Informational."
                            },
                            "profile_params": {
                                "type": "object",
                                "description": "Override default process rule parameters. "
                                               "E.g. {\"min_wall_mm\": 0.6, \"max_overhang_deg\": 30}"
                            },
                            "objects": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Object names to check. Default: all objects in active document."
                            },
                            "doc_name": {
                                "type": "string",
                                "description": "Document name. Default: active document."
                            }
                        }
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="macro_operations",
                    description="Discover, read, and run FreeCAD macros from the user's macro directory "
                                "(App.getUserMacroDir(), typically ~/.FreeCAD/Macro/). Use this to leverage "
                                "the user's existing library of automation macros instead of regenerating "
                                "common operations from scratch via execute_python. Always 'list' first to "
                                "see what's available; 'read' a macro before 'run' if its purpose isn't obvious.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Macro action: 'list' enumerates the macro directory, "
                                               "'read' returns a macro's source, 'run' executes it.",
                                "enum": ["list", "read", "run"],
                            },
                            "name": {
                                "type": "string",
                                "description": "Macro filename (e.g. 'foo.FCMacro' or bare 'foo'). "
                                               "Required for 'read' and 'run'.",
                            },
                            "include_hidden": {
                                "type": "boolean",
                                "description": "List action: include dotfiles (default false).",
                                "default": False,
                            },
                        },
                        "required": ["operation"],
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="api_introspection",
                    description="Live introspection of FreeCAD's running Python API. Use this BEFORE writing "
                                "execute_python code that calls unfamiliar methods — it eliminates the "
                                "wrong-signature / AttributeError class of failures. "
                                "'inspect' returns the signature + docstring for a dotted path "
                                "(e.g. 'Part.makeBox', 'Sketcher.SketchObject'). "
                                "'search' fuzzy-matches a query across FreeCAD's modules and workbenches. "
                                "Search ranking improves over time: call 'record_useful' after a successful "
                                "search → inspect → execute_python sequence to bias future searches toward "
                                "the path that actually worked.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "description": "Introspection action.",
                                "enum": ["inspect", "search", "record_useful"],
                            },
                            "path": {
                                "type": "string",
                                "description": "Dotted path for 'inspect' or 'record_useful' "
                                               "(e.g. 'Part.makeBox', 'FreeCAD.Vector').",
                            },
                            "query": {
                                "type": "string",
                                "description": "Search string for 'search' or 'record_useful' "
                                               "(e.g. 'make box', 'fillet edge').",
                            },
                            "modules": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Search action: optional list of module names to scan "
                                               "(defaults to FreeCAD core + common workbenches). Use this "
                                               "to extend coverage to a specific addon workbench.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Search action: max results to return (default 30, cap 100).",
                                "default": 30,
                            },
                        },
                        "required": ["operation"],
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=False,
                    ),
                ),
                types.Tool(
                    name="get_debug_logs",
                    description="Retrieve recent debug logs for troubleshooting and analysis",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "count": {
                                "type": "integer",
                                "description": "Number of recent log entries to retrieve",
                                "default": 20
                            },
                            "operation": {
                                "type": "string",
                                "description": "Optional filter by operation name (e.g., 'execute_python', 'cam_operations')"
                            }
                        }
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="execute_python",
                    description="Execute arbitrary Python code in FreeCAD context for power users and advanced operations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute in FreeCAD context"
                            }
                        },
                        "required": ["code"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="execute_python_async",
                    description="Submit Python code for async execution in FreeCAD. Returns a job_id immediately without waiting. Use poll_job(job_id) to check status. Use this for long-running operations (CAM recompute, mesh operations, surface generation) that would otherwise timeout.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute in FreeCAD context (same semantics as execute_python)"
                            }
                        },
                        "required": ["code"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                types.Tool(
                    name="poll_job",
                    description="Poll the status of an async job submitted via execute_python_async. Returns 'running' with elapsed seconds, 'done' with result, or 'error'. Completed jobs are cleaned up after retrieval.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "job_id": {
                                "type": "string",
                                "description": "Job ID returned by execute_python_async"
                            }
                        },
                        "required": ["job_id"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                    ),
                ),
                types.Tool(
                    name="list_jobs",
                    description="List all currently tracked async jobs and their status (running/done/error) and elapsed time.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="cancel_operation",
                    description="Cancel the current long-running FreeCAD operation (Thickness, boolean, Check Geometry, etc.). "
                                "Sets the global cancel flag; the operation stops within ≤200 ms. "
                                "Safe to call while the GUI thread is blocked.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="cancel_job",
                    description="Mark a running async job as cancelled so poll_job stops returning 'running'. "
                                "Also fires the FreeCAD cancel flag. "
                                "WARNING: raw OCCT booleans (Shape.common/fuse/cut) do NOT respond to the cancel flag — "
                                "the GUI thread stays blocked until the C++ call finishes or crashes. "
                                "After cancel_job, use restart_freecad to fully recover a stuck GUI thread.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "job_id": {
                                "type": "string",
                                "description": "Job ID to cancel (from execute_python_async)"
                            }
                        },
                        "required": ["job_id"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=False,
                    ),
                ),
                types.Tool(
                    name="continue_selection",
                    description="Continue an interactive selection operation after selecting elements in FreeCAD",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "operation_id": {
                                "type": "string",
                                "description": "The operation ID from the awaiting_selection response"
                            }
                        },
                        "required": ["operation_id"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                # ------------------------------------------------------------------
                # SketchBuilder — pre-validated parametric sketch emission
                # ------------------------------------------------------------------
                types.Tool(
                    name="build_sketch",
                    description=(
                        "Validate and emit a parametric FreeCAD sketch from a JSON layout "
                        "descriptor. Uses python-solvespace to pre-validate constraints before "
                        "touching the document — no trial-and-error in FreeCAD. Returns DOF, "
                        "geometry count, and constraint count on success, or conflict details "
                        "on failure.\n\n"
                        "Supported element types:\n"
                        "  envelope   — outer bounding rectangle (width, height)\n"
                        "  hline      — horizontal reference line at y (name)\n"
                        "  arch       — single arched window/opening (cx, sill, spring, radius, name)\n"
                        "  arch_array — N evenly-spaced arches; use {i} in cx expression (count, cx, sill, spring, radius, name)\n"
                        "  door       — door opening tied to a floor hline (left_x, spring, width, floor_ref, name)\n"
                        "  monitor    — clerestory monitor (width, height, cx, base_y, name)\n\n"
                        "All dimension values are spreadsheet alias names (strings), not numbers."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "layout": {
                                "type": "object",
                                "description": "Sketch layout descriptor with an 'elements' array",
                                "properties": {
                                    "elements": {
                                        "type": "array",
                                        "description": "Ordered list of sketch elements to add",
                                        "items": {"type": "object"}
                                    }
                                },
                                "required": ["elements"]
                            },
                            "sketch_name": {
                                "type": "string",
                                "description": "Name for the FreeCAD sketch object (default 'Master XZ')",
                                "default": "Master XZ"
                            },
                            "placement": {
                                "type": "string",
                                "enum": ["XY", "XZ", "YZ"],
                                "description": "Sketch plane (default 'XZ')",
                                "default": "XZ"
                            },
                            "spreadsheet": {
                                "type": "string",
                                "description": "FreeCAD object name of the parameter spreadsheet (default 'Spreadsheet')",
                                "default": "Spreadsheet"
                            }
                        },
                        "required": ["layout"]
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
                # ------------------------------------------------------------------
                # Instance management tools
                # ------------------------------------------------------------------
                types.Tool(
                    name="spawn_freecad_instance",
                    description=(
                        "Spawn a new FreeCAD instance managed by this bridge. "
                        "Defaults to headless (FreeCADCmd). Set gui=true to launch a "
                        "full GUI window — useful for side-by-side comparisons between "
                        "different FreeCAD builds via the freecad_binary arg. "
                        "Returns the socket path, PID, uuid. Selects the new instance "
                        "as the active target by default."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "Human-readable label for this instance (optional)"
                            },
                            "socket_path": {
                                "type": "string",
                                "description": "Explicit socket path (auto-generated UUID path if omitted)"
                            },
                            "gui": {
                                "type": "boolean",
                                "description": "Launch a GUI window instead of headless (default false)",
                                "default": False
                            },
                            "freecad_binary": {
                                "type": "string",
                                "description": (
                                    "Explicit FreeCAD binary path. Overrides auto-detection. "
                                    "Use to pick between, e.g., /Applications/FreeCAD.app and a "
                                    "local build."
                                )
                            },
                            "select": {
                                "type": "boolean",
                                "description": "Make this instance the active target (default true)",
                                "default": True
                            }
                        }
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=False,
                    ),
                ),
                types.Tool(
                    name="list_freecad_instances",
                    description=(
                        "List all known FreeCAD instances: the current default socket "
                        "and any instances spawned by this bridge."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="select_freecad_instance",
                    description=(
                        "Switch the active FreeCAD instance. All subsequent tool calls "
                        "will be routed to this instance. Use list_freecad_instances to "
                        "see available uuids / labels / socket paths."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "uuid": {
                                "type": "string",
                                "description": "Instance UUID (preferred selector)"
                            },
                            "label": {
                                "type": "string",
                                "description": "Instance label (alternative to uuid)"
                            },
                            "socket_path": {
                                "type": "string",
                                "description": "Socket path of the instance (alternative to uuid/label)"
                            }
                        }
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=False,
                        idempotentHint=True,
                    ),
                ),
                types.Tool(
                    name="stop_freecad_instance",
                    description=(
                        "Stop a headless FreeCAD instance that was spawned by this bridge. "
                        "Has no effect on externally-launched instances."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "uuid": {
                                "type": "string",
                                "description": "Instance UUID"
                            },
                            "label": {
                                "type": "string",
                                "description": "Instance label (alternative to uuid)"
                            },
                            "socket_path": {
                                "type": "string",
                                "description": "Socket path (alternative to uuid/label)"
                            }
                        }
                    },
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                    ),
                ),
            ]
            return base_tools + smart_dispatchers

        return base_tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        """Handle tool calls with smart dispatcher routing"""
        
        if name == "check_freecad_connection":
            # Trigger lazy resolution so available/socket_path reflect discovery.
            resolved, resolve_err = _ctx.resolve_target()
            available = _ctx.freecad_available
            status = {
                "freecad_socket_exists": available,
                "socket_path": _ctx.socket_path,
                "status": "FreeCAD running with AICopilot" if available
                         else (resolve_err or "FreeCAD not running. Start FreeCAD or call spawn_freecad_instance."),
                "instances": _ctx.list_all(),
            }
            return [types.TextContent(
                type="text",
                text=json.dumps(status, indent=2)
            )]
            
        elif name == "test_echo":
            message = arguments.get("message", "No message provided") if arguments else "No arguments"
            return [types.TextContent(
                type="text",
                text=f"Bridge received: {message}"
            )]

        elif name == "restart_freecad":
            # Send restart command, then wait for new instance
            result = await send_to_freecad("restart_freecad", arguments or {})
            # Wait for old instance to die and new one to start
            await asyncio.sleep(3)
            # Poll for new instance (up to 30s)
            for i in range(30):
                if _ctx.socket_path and os.path.exists(_ctx.socket_path):
                    try:
                        test = await send_to_freecad("test_echo", {"message": "ping"})
                        parsed = json.loads(test)
                        if "error" not in parsed:
                            return [types.TextContent(
                                type="text",
                                text=json.dumps({
                                    "status": "FreeCAD restarted successfully",
                                    "restart_response": json.loads(result) if isinstance(result, str) else result,
                                })
                            )]
                    except Exception:
                        pass
                await asyncio.sleep(1)
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "status": "Restart command sent but new instance not yet available",
                    "restart_response": json.loads(result) if isinstance(result, str) else result,
                })
            )]
            
        elif name == "reload_modules":
            result = await send_to_freecad("reload_modules", {})
            return [types.TextContent(
                type="text",
                text=result if isinstance(result, str) else json.dumps(result)
            )]

        elif name == "manage_connection":
            action = (arguments or {}).get("action", "status")

            if action == "clear_recovery":
                if _crash_mod is None:
                    return [types.TextContent(type="text",
                        text=json.dumps({"error": "crash report module not loaded"}))]
                removed = _crash_mod.clear_recovery_files(dry_run=False)
                return [types.TextContent(type="text", text=json.dumps({
                    "action": "clear_recovery",
                    "removed": removed,
                    "count": len(removed),
                    "note": "Removed corrupt FreeCAD recovery files. Restart FreeCAD for a clean session.",
                }, indent=2))]

            elif action == "validate_fcstd":
                path = (arguments or {}).get("path", "")
                if not path:
                    return [types.TextContent(type="text",
                        text=json.dumps({"error": "path parameter required"}))]
                if _crash_mod is None:
                    import zipfile, os as _os
                    try:
                        sz = _os.path.getsize(path)
                        with zipfile.ZipFile(path, "r") as zf:
                            bad = zf.testzip()
                        result = {"valid": bad is None, "size_bytes": sz,
                                  "error": f"Corrupt member: {bad}" if bad else None}
                    except Exception as exc:
                        result = {"valid": False, "size_bytes": 0, "error": str(exc)}
                else:
                    result = _crash_mod.validate_fcstd(path)
                return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

            else:  # action == "status"
                out: dict = {
                    "connected": _ctx.freecad_available,
                    "socket_path": _ctx.socket_path,
                    "instances": _ctx.list_all(),
                }
                if _crash_mod is not None:
                    rec = _crash_mod.find_recovery_files()
                    out["recovery_files"] = rec
                    out["crash_loop_risk"] = any(not f["valid"] for f in rec)
                return [types.TextContent(type="text", text=json.dumps(out, indent=2))]

        # Handle continue_selection tool
        elif name == "continue_selection":
            operation_id = arguments.get("operation_id") if arguments else None
            if not operation_id:
                return [types.TextContent(
                    type="text",
                    text="Error: operation_id is required to continue selection"
                )]
            
            # Send continuation command to FreeCAD
            response = await send_to_freecad("continue_selection", {
                "operation_id": operation_id
            })
            
            return [types.TextContent(
                type="text",
                text=response
            )]
            
        # build_sketch: route directly to FreeCAD handler
        elif name == "build_sketch":
            result = await send_to_freecad("build_sketch", arguments or {})
            return [types.TextContent(type="text", text=result)]

        # execute_python: submit as async job, poll with timeout
        elif name == "execute_python":
            args = arguments or {}
            submit_resp = json.loads(await send_to_freecad("execute_python_async", {"code": args.get("code", "")}))
            if "error" in submit_resp:
                return [types.TextContent(type="text", text=json.dumps(submit_resp))]
            job_id = submit_resp.get("job_id")
            if not job_id:
                return [types.TextContent(type="text", text=json.dumps({"error": "no job_id returned", "response": submit_resp}))]
            poll_resp = await poll_job_until_done(job_id, context="execute_python")
            status = poll_resp.get("status")
            if status == "done":
                _complete_op()
                return [types.TextContent(type="text", text=json.dumps({"result": poll_resp.get("result"), "elapsed": poll_resp.get("elapsed")}))]
            elif status == "timeout":
                return [types.TextContent(type="text", text=json.dumps({"error": poll_resp["error"], "job_id": job_id}))]
            else:
                return [types.TextContent(type="text", text=json.dumps({"error": poll_resp.get("error"), "elapsed": poll_resp.get("elapsed")}))]

        # macOS screenshot: run screencapture in the bridge process (which inherits
        # Screen Recording permission from the terminal), never touching FreeCAD's
        # GUI thread or requiring FreeCAD to have its own TCC permission.
        elif (name == "view_control"
              and (arguments or {}).get("operation") == "screenshot"
              and platform.system() == "Darwin"):
            import tempfile, base64 as _b64
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp_path = f.name
                proc = subprocess.run(
                    ["screencapture", "-x", tmp_path],
                    timeout=10, capture_output=True,
                )
                if proc.returncode == 0 and os.path.getsize(tmp_path) > 0:
                    with open(tmp_path, "rb") as f:
                        image_data = _b64.b64encode(f.read()).decode("utf-8")
                    return [types.ImageContent(
                        type="image", data=image_data, mimeType="image/png"
                    )]
                err = proc.stderr.decode(errors="replace")[:200]
                return [types.TextContent(type="text", text=json.dumps({
                    "error": f"screencapture failed (rc={proc.returncode}): {err}"
                }))]
            except Exception as e:
                return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        # Route smart dispatcher tools to socket with enhanced routing
        elif name in ["partdesign_operations", "sketch_operations", "part_operations",
                      "view_control", "cam_operations", "cam_tools", "cam_tool_controllers",
                      "cam_machines", "mesh_operations", "measurement_operations",
                      "spatial_query",
                      "spreadsheet_operations", "draft_operations", "get_debug_logs",
                      "macro_operations", "api_introspection",
                      "execute_python_async", "poll_job", "list_jobs",
                      "cancel_operation", "cancel_job"]:
            args = arguments or {}

            # Check if this is a continuation from interactive selection
            if args.get("_continue_from_interactive"):
                # Extract the original operation details
                operation_id = args.get("operation_id")
                tool_name = args.get("tool_name")
                original_args = args.get("original_args", {})

                # Add continuation flag
                continue_args = {
                    **original_args,
                    "_continue_selection": True,
                    "_operation_id": operation_id
                }

                response = await send_to_freecad(tool_name, continue_args)
            else:
                response = await send_to_freecad(name, args)

            # If FreeCAD returned a job_id, auto-poll until done (transparent to the agent).
            # All dedicated handlers now use the async GUI-thread path so this fires
            # for every op; progressive backoff keeps fast ops snappy.
            try:
                result = json.loads(response)
                if isinstance(result, dict) and result.get("job_id") and result.get("status") == "submitted":
                    job_id = result["job_id"]
                    poll_resp = await poll_job_until_done(job_id, context=name)
                    status = poll_resp.get("status")
                    if status == "done":
                        _complete_op()
                        return [types.TextContent(type="text", text=json.dumps({
                            "result": poll_resp.get("result"),
                            "elapsed": poll_resp.get("elapsed"),
                        }))]
                    elif status == "timeout":
                        return [types.TextContent(type="text", text=json.dumps({
                            "error": poll_resp["error"],
                            "job_id": job_id,
                        }))]
                    else:
                        return [types.TextContent(type="text", text=json.dumps({
                            "error": poll_resp.get("error"),
                            "elapsed": poll_resp.get("elapsed"),
                        }))]
                        # status == "running" → keep polling
            except (json.JSONDecodeError, Exception):
                pass

            # Return image content when the response contains base64 image data
            try:
                result = json.loads(response)
                if isinstance(result, dict) and result.get("image_data"):
                    return [types.ImageContent(
                        type="image",
                        data=result["image_data"],
                        mimeType=result.get("mime_type", "image/png"),
                    )]
            except (json.JSONDecodeError, Exception):
                pass

            return [types.TextContent(
                type="text",
                text=response
            )]
            
        # ------------------------------------------------------------------
        # Instance management handlers
        # ------------------------------------------------------------------

        elif name == "list_freecad_instances":
            instances = _ctx.list_all()
            # Enrich each entry with active-doc / window-title info via a
            # short round-trip. Run probes in parallel so 3 instances take
            # ~1 round-trip's worth of time, not N's worth.
            fetch_tasks = []
            for entry in instances:
                sp = entry.get("socket_path")
                if sp and entry.get("available", True):
                    fetch_tasks.append((entry, asyncio.to_thread(_fetch_instance_info, sp)))
            for entry, task in fetch_tasks:
                try:
                    info = await task
                except Exception:
                    info = None
                if info:
                    entry["active_doc_label"] = info.get("active_doc_label")
                    entry["active_doc_file"] = info.get("active_doc_file")
                    entry["window_title"] = info.get("window_title")
                    # Backfill uuid/version/gui if discovery didn't have them
                    for k in ("uuid", "freecad_version", "gui"):
                        if not entry.get(k) and info.get(k) is not None:
                            entry[k] = info[k]
            return [types.TextContent(
                type="text",
                text=json.dumps({"instances": instances}, indent=2)
            )]

        elif name == "select_freecad_instance":
            args = arguments or {}
            target_path = args.get("socket_path")
            target_label = args.get("label")
            target_uuid = args.get("uuid")

            # Build a combined search space: managed + discovered.
            candidates = []
            for sp, info in _ctx.instances.items():
                candidates.append({
                    "socket_path": sp,
                    "label": info.get("label"),
                    "uuid": info.get("uuid"),
                })
            for record in _scan_discovery():
                candidates.append({
                    "socket_path": record.get("socket_path"),
                    "label": record.get("label"),
                    "uuid": record.get("uuid"),
                })

            # Resolve by uuid → label → socket_path
            if not target_path and target_uuid:
                for c in candidates:
                    if c.get("uuid") == target_uuid:
                        target_path = c["socket_path"]
                        break
                if not target_path:
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({"error": f"No instance with uuid '{target_uuid}'"})
                    )]
            if not target_path and target_label:
                for c in candidates:
                    if c.get("label") == target_label:
                        target_path = c["socket_path"]
                        break
                if not target_path:
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({"error": f"No instance with label '{target_label}'"})
                    )]

            if not target_path:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Provide socket_path, label, or uuid"})
                )]

            _ctx.socket_path = target_path
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "result": f"Active instance set to {target_path}",
                    "socket_path": target_path,
                })
            )]

        elif name == "spawn_freecad_instance":
            args = arguments or {}
            label = args.get("label")
            sock_path = args.get("socket_path") or f"/tmp/freecad_mcp_{uuid.uuid4().hex[:8]}.sock"
            select_new = args.get("select", True)
            gui_mode = bool(args.get("gui", False))
            freecad_binary_override = args.get("freecad_binary")

            # Validate socket path: must resolve to within /tmp/ to prevent path traversal
            # On macOS, /tmp is a symlink to /private/tmp, so accept both
            real_sock_path = os.path.realpath(sock_path)
            if ".." in sock_path or not (real_sock_path.startswith("/tmp/") or real_sock_path.startswith("/private/tmp/")):
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": f"Invalid socket_path: must be within /tmp/ (resolved to {real_sock_path})"
                    })
                )]

            # Resolve which FreeCAD binary to launch, and which arg vector to use.
            if freecad_binary_override:
                if not os.path.isfile(freecad_binary_override):
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({
                            "error": f"freecad_binary not found: {freecad_binary_override}"
                        })
                    )]
                freecad_bin = freecad_binary_override
            elif gui_mode:
                freecad_bin = _find_freecad_gui()
                if not freecad_bin:
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({
                            "error": (
                                "Cannot find FreeCAD GUI binary. "
                                "Set FREECAD_MCP_FREECAD_GUI_BIN env var or pass "
                                "freecad_binary=... to point at it."
                            )
                        })
                    )]
            else:
                freecad_bin = _find_freecadcmd()
                if not freecad_bin:
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({
                            "error": (
                                "Cannot find FreeCADCmd binary. "
                                "Set FREECAD_MCP_FREECAD_BIN env var to its path."
                            )
                        })
                    )]

            # Build the launch command. Headless wraps headless_server.py; GUI
            # auto-loads InitGui.py from the AICopilot addon at startup.
            if gui_mode:
                launch_cmd = [freecad_bin]
            else:
                headless_script = _find_headless_script()
                if not headless_script:
                    return [types.TextContent(
                        type="text",
                        text=json.dumps({
                            "error": (
                                "Cannot find headless_server.py. "
                                "Set FREECAD_MCP_MODULE_DIR env var, or deploy AICopilot "
                                "to ~/.freecad-mcp/AICopilot/."
                            )
                        })
                    )]
                launch_cmd = [freecad_bin, headless_script, "--socket-path", sock_path]

            env = os.environ.copy()
            env["FREECAD_MCP_SOCKET"] = sock_path
            if label:
                env["FREECAD_MCP_LABEL"] = label
            try:
                proc = subprocess.Popen(
                    launch_cmd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as e:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"Failed to spawn FreeCAD: {e}"})
                )]

            # GUI startup (workbench load, Qt init) is noticeably slower than
            # headless — give it more time.
            ready_timeout = 60 if gui_mode else 30
            deadline = time.time() + ready_timeout
            ready = False
            while time.time() < deadline:
                if os.path.exists(sock_path):
                    try:
                        test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        test_sock.settimeout(2)
                        test_sock.connect(sock_path)
                        test_sock.close()
                        ready = True
                        break
                    except OSError:
                        pass
                await asyncio.sleep(0.5)

            if not ready:
                proc.kill()
                kind = "GUI" if gui_mode else "Headless"
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": f"{kind} FreeCAD did not become ready within {ready_timeout} s",
                        "socket_path": sock_path,
                    })
                )]

            # The spawned process generates its own UUID inside AICopilot.
            # Look it up from the discovery file so we can store it.
            instance_uuid = None
            for record in _scan_discovery(prune_stale=False):
                if record.get("socket_path") == sock_path:
                    instance_uuid = record.get("uuid")
                    break

            _ctx.register(
                sock_path, proc.pid, proc, label or sock_path,
                headless=not gui_mode, instance_uuid=instance_uuid,
            )
            if select_new:
                _ctx.socket_path = sock_path

            kind = "GUI" if gui_mode else "Headless"
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "result": f"{kind} FreeCAD instance spawned and ready",
                    "socket_path": sock_path,
                    "pid": proc.pid,
                    "uuid": instance_uuid,
                    "label": label or sock_path,
                    "gui": gui_mode,
                    "freecad_binary": freecad_bin,
                    "selected": select_new,
                })
            )]

        elif name == "stop_freecad_instance":
            args = arguments or {}
            target_path = args.get("socket_path")
            target_label = args.get("label")
            target_uuid = args.get("uuid")

            if not target_path and target_uuid:
                for sp, info in _ctx.instances.items():
                    if info.get("uuid") == target_uuid:
                        target_path = sp
                        break
            if not target_path and target_label:
                for sp, info in _ctx.instances.items():
                    if info.get("label") == target_label:
                        target_path = sp
                        break

            if not target_path:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Provide socket_path, label, or uuid of instance to stop"})
                )]

            info = _ctx.instances.get(target_path)
            if not info:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"Instance '{target_path}' not managed by this bridge"})
                )]

            proc = info.get("proc")
            if proc:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except OSError:
                    pass

            # Clean up socket file if it still exists
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except OSError:
                    pass

            _ctx.unregister(target_path)

            # If we just stopped the active instance, clear it so the next
            # call re-resolves via discovery (or env var if set).
            if _ctx.socket_path == target_path:
                _ctx.socket_path = os.environ.get("FREECAD_MCP_SOCKET")

            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "result": f"Instance {target_path} stopped",
                    "active_socket": _ctx.socket_path,
                })
            )]

        else:
            return [types.TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    # Optional: Start health monitoring if debugging enabled
    async def health_check_loop():
        """Periodic health check for FreeCAD"""
        if not DEBUG_ENABLED or not monitor:
            return
            
        while True:
            try:
                status = monitor.perform_health_check()
                if not status['is_healthy']:
                    debugger.logger.error("FreeCAD health check FAILED!")
                    monitor.log_crash(status)
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                if debugger:
                    debugger.logger.error(f"Health check error: {e}")
                await asyncio.sleep(30)
    
    # Start health monitoring in background if enabled
    if DEBUG_ENABLED and monitor:
        health_task = asyncio.create_task(health_check_loop())
    
    # Run the server
    import mcp.server.stdio
    
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="freecad",
                    server_version="2.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        # Export debug info on shutdown if debugging enabled
        if DEBUG_ENABLED and debugger:
            debugger.logger.info("="*80)
            debugger.logger.info("MCP Bridge shutting down - exporting debug info")
            debugger.logger.info("="*80)
            
            try:
                # Performance report
                perf_report = debugger.get_performance_report()
                debugger.logger.info(f"\n{perf_report}")
                
                # Export debug package
                debug_pkg = debugger.export_debug_package()
                debugger.logger.info(f"Debug package: {debug_pkg}")
                
                # Export crash report if there were crashes
                if monitor and monitor.crash_history:
                    crash_report = monitor.export_crash_report()
                    debugger.logger.info(f"Crash report: {crash_report}")
                    stats = monitor.get_crash_statistics()
                    debugger.logger.info(f"Crash statistics: {stats}")
            except Exception as e:
                if debugger:
                    debugger.logger.error(f"Error during shutdown export: {e}")

if __name__ == "__main__":
    asyncio.run(main())

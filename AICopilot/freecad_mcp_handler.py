# FreeCAD Socket Server for MCP Communication
# Runs inside FreeCAD to receive commands from external MCP bridge
#
# Version: 5.8.0 - Multi-instance discovery via per-instance UUID +
#                   ~/.cache registry; GUI spawn + explicit freecad_binary;
#                   list_freecad_instances enriched with active doc + window
#                   title; startup banner shows handler version.
# Version: 6.2.0 - GUI-thread heartbeat (fast unresponsive-vs-busy diagnosis
#                   instead of a blind 30-120s timeout); active_connections +
#                   queue_depth surfaced on busy rejections and async job
#                   submission, so contention from another connected client
#                   is visible instead of presenting as an unexplained delay.

__version__ = "6.2.0"

# Minimum FreeCAD version required for CAM tools (the new Path Toolbit API).
# Below this, cam_operations / cam_tools / cam_tool_controllers return a clean
# "not supported" error rather than crashing on the missing Path/CAM API.
#
# NOTE on the value: FreeCAD's version scheme was renumbered from 1.2 to 26.3
# (the dev series jumped 1.1 -> 26.3, skipping over 1.2 entirely). This tuple
# threshold still gates correctly because of that gap: legacy builds without
# the Toolbit API report (1, 0/1, x) which sort below (1, 2, 0), while every
# build that HAS the API now reports (26, 3, 0)+ which sorts above it. Nothing
# real lives between 1.1 and 26.3, so the boundary remains unambiguous.
CAM_MIN_FC_VERSION = (1, 2, 0)

REQUIRED_VERSIONS = {
    "freecad_debug": ">=1.1.0",
    "freecad_health": ">=1.0.1",
}

import FreeCAD
import socket
import threading
import json
import os
import time
import queue
import uuid
import platform
import struct
import sys
import traceback as tb_module
from typing import Dict, Any, Optional

from universal_selector import UniversalSelector
from gui_heartbeat import GuiHeartbeat

# Conditional GUI imports (not available in console mode)
if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtCore
else:
    FreeCADGui = None
    QtCore = None

IS_WINDOWS = platform.system() == "Windows"

# Limit on concurrent/retained async jobs to prevent unbounded memory growth
MAX_ASYNC_JOBS = 50
# Completed jobs older than this (seconds) are automatically cleaned up
ASYNC_JOB_TTL = 600

# Configurable socket path/port via environment variables.
# SOCKET_PATH is None when the env var is unset; start_server() then
# generates /tmp/freecad_mcp_<uuid>.sock from instance_registry.
SOCKET_PATH = os.environ.get("FREECAD_MCP_SOCKET") or None
WINDOWS_HOST = "localhost"
WINDOWS_PORT = int(os.environ.get("FREECAD_MCP_PORT", "23456"))

# =============================================================================
# MCP Debug Infrastructure (Optional)
# =============================================================================
DEBUG_ENABLED = False
_debugger = None
_monitor = None

# capture_state() walks every object's Shape and calls shape.isValid() -- a
# full OCCT BRep topology check -- before every single command, regardless
# of whether anything goes wrong. Measured cost: 0ms on a 1-object sketch,
# 13s on a 315-object structural model, 38-75s (and GIL-contention-driven
# process death) on a document with ~33K faces concentrated in a handful of
# FeaturePython objects (BrickedWall_*/SlateTiles, 2026-07-29). It's
# diagnostic-only -- not part of the actual tool response -- and the
# separate health-check/crash-watcher loop already captures state
# independently when a command actually fails. Opt in per-session with
# FREECAD_MCP_CAPTURE_STATE_PER_COMMAND=1 for real debugging; default off so
# large documents aren't taxed on every call for a snapshot most calls never
# need.
CAPTURE_STATE_PER_COMMAND = os.environ.get("FREECAD_MCP_CAPTURE_STATE_PER_COMMAND") == "1"


def _log_operation(operation, parameters=None, result=None, error=None, duration=None):
    """No-op fallback if debug not enabled"""
    pass


def _capture_state():
    """No-op fallback if debug not enabled"""
    return {}


try:
    from freecad_debug import (
        init_debugger,
        log_operation as _log_op_impl,
        capture_state as _capture_state_impl,
        get_debugger
    )
    from freecad_health import init_monitor, get_monitor

    _log_dir = os.path.expanduser("~/.freecad-mcp/logs")
    _crash_dir = os.path.expanduser("~/.freecad-mcp/crashes")
    os.makedirs(_log_dir, mode=0o700, exist_ok=True)
    os.makedirs(_crash_dir, mode=0o700, exist_ok=True)

    _debugger = init_debugger(
        log_dir=_log_dir,
        enable_console=False,
        enable_file=True,
        lean_logging=False,
    )
    _monitor = init_monitor()

    _log_operation = _log_op_impl
    _capture_state = _capture_state_impl

    DEBUG_ENABLED = True
    FreeCAD.Console.PrintMessage("MCP Debug infrastructure loaded\n")
    FreeCAD.Console.PrintMessage(f"  Logs: {_log_dir}/\n")
    FreeCAD.Console.PrintMessage(f"  Crashes: {_crash_dir}/\n")

except ImportError as e:
    FreeCAD.Console.PrintMessage(f"MCP Debug not available (optional): {e}\n")

except Exception as e:
    FreeCAD.Console.PrintError(f"MCP Debug modules broken: {e}\n")
    FreeCAD.Console.PrintError("  Fix or remove freecad_debug.py/freecad_health.py\n")
    sys.exit(1)

# =============================================================================
# Crash Watcher (optional — writes last-op to /tmp before each operation)
# =============================================================================
_set_current_op = None
_clear_current_op = None
try:
    from crash_watcher import set_current_op as _set_current_op, clear_current_op as _clear_current_op
    FreeCAD.Console.PrintMessage("Crash watcher loaded — op tracking active\n")
except ImportError:
    pass  # graceful degradation: no op tracking

# =============================================================================
# Version Validation
# =============================================================================
try:
    from mcp_versions import (
        register_component,
        declare_requirements,
        validate_all,
        get_status,
    )
    register_component("freecad_mcp_handler", __version__)
    declare_requirements("freecad_mcp_handler", REQUIRED_VERSIONS)
    valid, error = validate_all()
    if not valid:
        FreeCAD.Console.PrintError(f"Version validation failed: {error}\n")
        FreeCAD.Console.PrintError("Component status:\n")
        FreeCAD.Console.PrintError(json.dumps(get_status(), indent=2) + "\n")
        sys.exit(1)
    FreeCAD.Console.PrintMessage(f"freecad_mcp_handler v{__version__} validated\n")
except ImportError as e:
    FreeCAD.Console.PrintWarning(f"Version system not available (optional): {e}\n")

# =============================================================================
# Modular Handlers
# =============================================================================
# Single source of truth for {attr_name: HandlerClass} -- __init__ and
# _reload_handlers each used to hand-maintain their own copy of this same
# 25-entry mapping (plus their own copy of the flat `from handlers import
# (...)` name list), so a handler added to one but not the other would
# silently only work until the next hot-reload. Both now derive their
# instantiation dict from this one dict via _build_handler_class_map,
# which resolves against whatever `handlers` module object is current
# (the module-level import at startup, or the freshly-reloaded package
# object _reload_handlers builds).
_HANDLER_CLASS_NAMES = {
    'primitives': 'PrimitivesHandler',
    'boolean_ops': 'BooleanOpsHandler',
    'transforms': 'TransformsHandler',
    'sketch_ops': 'SketchOpsHandler',
    'partdesign_ops': 'PartDesignOpsHandler',
    'part_ops': 'PartOpsHandler',
    'cam_ops': 'CAMOpsHandler',
    'cam_tools': 'CAMToolsHandler',
    'cam_tool_controllers': 'CAMToolControllersHandler',
    'draft_ops': 'DraftOpsHandler',
    'measurement_ops': 'MeasurementOpsHandler',
    'spreadsheet_ops': 'SpreadsheetOpsHandler',
    'mesh_ops': 'MeshOpsHandler',
    'spatial_ops': 'SpatialOpsHandler',
    'inspector_ops': 'InspectorOpsHandler',
    'macro_ops': 'MacroOpsHandler',
    'introspection_ops': 'IntrospectionOpsHandler',
    'sketch_builder_ops': 'SketchBuilderOpsHandler',
    'verification_ops': 'VerificationOpsHandler',
    'fixture_ops': 'FixtureOpsHandler',
    'diagnostics_ops': 'DiagnosticsOpsHandler',
    'execute_python_ops': 'ExecutePythonOpsHandler',
    'assembly_ops': 'AssemblyOpsHandler',
    # GUI-sensitive handlers get the task queues for thread safety
    # (see _instantiate_handlers) -- listed last only to mirror the
    # historical dict order; position carries no behavioral meaning.
    'view_ops': 'ViewOpsHandler',
    'document_ops': 'DocumentOpsHandler',
}


def _build_handler_class_map(handlers_module) -> Dict[str, type]:
    """Resolve _HANDLER_CLASS_NAMES against a `handlers` module/package
    object into {attr_name: class}. Called with the module-level import
    at startup, or with the freshly-reloaded package object during
    _reload_handlers -- either way, the {attr_name: class_name} mapping
    itself is defined in exactly one place."""
    return {attr: getattr(handlers_module, cls_name)
            for attr, cls_name in _HANDLER_CLASS_NAMES.items()}


try:
    import handlers as _handlers_module
    FreeCAD.Console.PrintMessage("Modular handlers loaded successfully\n")
except ImportError as e:
    FreeCAD.Console.PrintError(f"Modular handlers required but not available: {e}\n")
    sys.exit(1)


# =============================================================================
# Message Framing Protocol (v2.1.1)
# =============================================================================
# Length-prefixed protocol: [4-byte big-endian length][JSON message]
# Keep in sync with mcp_bridge_framing.py (freecad_mcp_server.py) on the bridge side.

MAX_MESSAGE_SIZE = 50 * 1024  # 50KB — matches bridge-side limit

# The bridge (freecad_mcp_server.py) only ever sends {"tool": ..., "args": ...}.
# A request with any other top-level key is either a bug in the bridge or a
# manually-crafted malformed request — either way it must fail loudly here
# rather than have the typo silently swallowed. Without this, a key typo'd
# as "arg" instead of "args" previously fell through `command.get("args", {})`
# to an empty dict indistinguishable from a legitimate no-args call, then
# failed several layers deeper with a confusing "not found" instead of a
# clear "malformed request" error.
_KNOWN_REQUEST_KEYS = frozenset({"tool", "args"})


def send_message(sock: socket.socket, message_str: str) -> bool:
    """Send a length-prefixed message over the socket."""
    try:
        message_bytes = message_str.encode('utf-8')
        # Refuse to put an oversized frame on the wire: the peer rejects the body
        # after the length prefix, desyncing every subsequent frame.
        if len(message_bytes) > MAX_MESSAGE_SIZE:
            FreeCAD.Console.PrintError(
                f"Refusing to send oversized message: {len(message_bytes)} bytes "
                f"(limit: {MAX_MESSAGE_SIZE}); would desync framing.\n"
            )
            return False
        length_prefix = struct.pack('>I', len(message_bytes))
        sock.sendall(length_prefix + message_bytes)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        FreeCAD.Console.PrintWarning(f"Socket send error: {e}\n")
        return False


def receive_message(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive a length-prefixed message from the socket."""
    old_timeout = sock.gettimeout()
    try:
        sock.settimeout(timeout)

        length_bytes = _recv_exact(sock, 4)
        if length_bytes is None:
            return None

        message_len = struct.unpack('>I', length_bytes)[0]

        if message_len > MAX_MESSAGE_SIZE:
            FreeCAD.Console.PrintError(
                f"Message too large: {message_len} bytes (limit: {MAX_MESSAGE_SIZE})\n"
            )
            return None

        # message_len may legitimately be 0 (empty-body frame); _recv_exact returns
        # b'' for that and None only on a closed connection. Distinguish with
        # `is None` so a valid empty frame is not misread as a disconnect.
        message_bytes = _recv_exact(sock, message_len)
        if message_bytes is None:
            return None

        return message_bytes.decode('utf-8')

    except socket.timeout:
        FreeCAD.Console.PrintWarning("Socket receive timeout\n")
        return None
    except UnicodeDecodeError as e:
        FreeCAD.Console.PrintError(f"Message decode error: {e}\n")
        return None
    except Exception as e:
        FreeCAD.Console.PrintError(f"Receive error: {e}\n")
        return None
    finally:
        # Always restore the caller's timeout, even on an exception path.
        sock.settimeout(old_timeout)


def _recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Receive exactly num_bytes, handling partial reads."""
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(min(num_bytes - len(buf), 65536))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)



# =============================================================================
# FreeCAD Socket Server
# =============================================================================
class FreeCADSocketServer:
    """Socket server for FreeCAD MCP communication with modular handler architecture."""

    def __init__(self):
        self.running = False
        self.server_socket = None
        self.server_thread = None

        # GUI thread task queues (used by handlers that need Qt main thread)
        # Tasks are (request_id, callable) tuples; responses are (request_id, result).
        self._gui_task_queue = queue.Queue()
        self._gui_response_queue = queue.Queue()
        self._request_counter = 0  # monotonic request ID

        # Timeout / busy-guard state
        # _gui_thread_busy: True while a sync task is executing on the GUI thread.
        # _stale_req_ids: req_ids whose _run_on_gui_thread call already timed out;
        #   _process_gui_tasks must discard their results (no waiter left).
        self._gui_thread_busy = False
        self._stale_req_ids: set = set()

        # Stamped every _process_gui_tasks tick (every ~100ms) as long as the
        # Qt event loop is alive and pumping. Lets a request be rejected
        # immediately with a clear "GUI thread unresponsive" error instead of
        # silently queuing and only discovering the same thing after a full
        # 30-120s timeout -- callers previously had no way to distinguish
        # "genuinely busy" from "not responding at all" until the timeout hit.
        self._heartbeat = GuiHeartbeat()

        # {obj.Name: bool} snapshot of ActiveDocument's per-object
        # Visibility, rebuilt on the Qt main thread every _process_gui_tasks
        # tick (see _refresh_visibility_cache). obj.ViewObject is a
        # Gui::ViewProviderDocumentObject wrapper -- FreeCAD's own
        # requireMainThread() guard (Gui/Application.cpp) raises if it's
        # touched off the main thread, which is exactly what document_ops
        # handlers do (list_objects is dispatched from the socket thread as
        # a "safe_op"). Reading this plain dict instead means list_objects
        # never calls into FreeCADGui at all, at the cost of up to one tick
        # (~100ms) of staleness.
        self._visibility_cache: Dict[str, bool] = {}

        # Active _handle_client invocations right now. Every tool call opens
        # its own short-lived socket connection (there's no persistent
        # per-client session at this layer), so this counts concurrent
        # in-flight requests across however many bridge processes happen to
        # be connected to this instance -- surfaced in busy/queued responses
        # so contention from another connection is visible instead of just
        # presenting as an unexplained delay.
        self._active_connections = 0
        self._active_connections_lock = threading.Lock()

        # Async job tracking: job_id -> {status, started, result, error, elapsed}
        self._async_jobs: Dict[str, Dict] = {}

        # Interactive selection manager (fillet/chamfer/draft/shell/thickness
        # request_selection/complete_selection workflow, plus select/clear/get).
        self.selector = UniversalSelector()

        # Initialize handlers
        self._instantiate_handlers(_build_handler_class_map(_handlers_module))

        FreeCAD.Console.PrintMessage("Socket server initialized with modular handlers\n")

        # Cache FreeCAD version; used by CAM dispatch gate.
        try:
            ver = FreeCAD.Version()
            major = int(ver[0])
            minor = int(ver[1])
            patch = int(ver[2]) if len(ver) > 2 and str(ver[2]).isdigit() else 0
            self._fc_version = (major, minor, patch)
            if self._fc_version < CAM_MIN_FC_VERSION:
                FreeCAD.Console.PrintWarning(
                    f"[MCP] FreeCAD {major}.{minor} detected. "
                    f"CAM tools require a build with the Path Toolbit API "
                    f"(weekly / 26.x); all other tools work normally.\n"
                )
            else:
                FreeCAD.Console.PrintMessage(f"[MCP] FreeCAD {major}.{minor} — OK\n")
        except Exception:
            self._fc_version = (0, 0, 0)

    def _instantiate_handlers(self, handler_classes: Dict[str, type]) -> None:
        """(Re-)create handler instances from a {attr_name: class} mapping.

        Shared by __init__ (initial creation, module-level imports) and
        _reload_handlers (hot reload, freshly re-imported classes) so each
        handler's constructor-argument shape is defined in exactly one
        place instead of being copy-pasted at both call sites. Callers
        pass the classes explicitly rather than this method looking them
        up from module globals, so reload's freshly-reloaded classes are
        used correctly regardless of import-timing/globals subtleties.
        """
        _gui_sensitive = {"view_ops", "document_ops"}
        for attr_name, cls in handler_classes.items():
            if attr_name in _gui_sensitive:
                setattr(self, attr_name, cls(
                    self, self._gui_task_queue, self._gui_response_queue,
                    _log_operation, _capture_state
                ))
            else:
                setattr(self, attr_name, cls(self, _log_operation, _capture_state))

    # -----------------------------------------------------------------
    # Server lifecycle
    # -----------------------------------------------------------------

    def start_server(self):
        """Start the socket server."""
        try:
            # Always generate a UUID for this instance — used for discovery file
            # whether the socket path was env-supplied or auto-generated.
            try:
                from instance_registry import generate_uuid, is_socket_alive, default_socket_path
            except ImportError:
                # Fallback shim: registry module missing (shouldn't happen in shipped
                # builds, but keep the server functional in dev scratchpads).
                import uuid as _uuid
                def generate_uuid():
                    return _uuid.uuid4().hex[:12]
                def default_socket_path(u):
                    return f"/tmp/freecad_mcp_{u}.sock"
                def is_socket_alive(p, timeout=0.5):
                    if not os.path.exists(p):
                        return False
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        s.settimeout(timeout)
                        s.connect(p)
                        s.close()
                        return True
                    except OSError:
                        return False

            self.instance_uuid = generate_uuid()

            if IS_WINDOWS:
                self.socket_path = None
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind((WINDOWS_HOST, WINDOWS_PORT))
                FreeCAD.Console.PrintMessage(
                    f"Socket server started on {WINDOWS_HOST}:{WINDOWS_PORT} (Windows TCP)\n"
                )
            else:
                # Resolve the socket path:
                #   FREECAD_MCP_SOCKET wins if set; otherwise UUID-suffixed default.
                if SOCKET_PATH:
                    self.socket_path = SOCKET_PATH
                else:
                    self.socket_path = default_socket_path(self.instance_uuid)

                # Probe-before-unlink: never stomp a live peer's socket.
                if os.path.exists(self.socket_path):
                    if is_socket_alive(self.socket_path):
                        FreeCAD.Console.PrintError(
                            f"Socket {self.socket_path} is already in use by another live "
                            "FreeCAD instance. Refusing to start. Set FREECAD_MCP_SOCKET "
                            "to a unique path, or stop the other instance first.\n"
                        )
                        return False
                    # Stale socket from a prior crash — safe to remove.
                    os.remove(self.socket_path)

                self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.server_socket.bind(self.socket_path)
                os.chmod(self.socket_path, 0o600)
                FreeCAD.Console.PrintMessage(
                    f"Socket server started on {self.socket_path} (uuid={self.instance_uuid})\n"
                )

                # init_monitor() (module import time, before this instance's
                # real socket path exists) always constructed the health
                # monitor with its hardcoded default
                # ("/tmp/freecad_mcp.sock" — the legacy single-instance
                # path). Under the multi-instance architecture that default
                # is almost never this instance's actual socket, so every
                # health check and crash snapshot was silently probing the
                # wrong socket. Point it at the real one now that it's known.
                if _monitor is not None:
                    from pathlib import Path as _Path
                    _monitor.socket_path = _Path(self.socket_path)

            self.server_socket.listen(5)
            self.running = True

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()

            # Start GUI task processing on the Qt main thread
            if QtCore:
                QtCore.QTimer.singleShot(100, self._process_gui_tasks)

            return True

        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to start socket server: {e}\n")
            return False

    def stop_server(self):
        """Stop the socket server."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        if not IS_WINDOWS and hasattr(self, 'socket_path') and self.socket_path:
            try:
                if os.path.exists(self.socket_path):
                    os.remove(self.socket_path)
            except Exception:
                pass
        FreeCAD.Console.PrintMessage("Socket server stopped\n")

    # -----------------------------------------------------------------
    # GUI thread task processing
    # -----------------------------------------------------------------

    def _refresh_visibility_cache(self):
        """Snapshot ActiveDocument's per-object Visibility into a plain dict.

        Runs on the Qt main thread (called from _process_gui_tasks' timer
        tick) -- the only thread .ViewObject may be touched from. Replaces
        the whole dict rather than mutating in place, so a concurrent
        socket-thread read (document_ops.list_objects doing
        self.server._visibility_cache.get(name)) never observes a
        partially-rebuilt cache; plain dict reference reassignment is
        atomic under the GIL.
        """
        doc = FreeCAD.ActiveDocument
        if doc is None:
            self._visibility_cache = {}
            return
        cache = {}
        for obj in doc.Objects:
            try:
                cache[obj.Name] = bool(obj.ViewObject.Visibility)
            except Exception:
                pass
        self._visibility_cache = cache

    def _process_gui_tasks(self):
        """Process queued tasks on the Qt main thread (called by QTimer).

        Handles both synchronous tasks (req_id is an int, result goes to
        _gui_response_queue) and async jobs (req_id is "async:<job_id>",
        result stored in _async_jobs dict).
        """
        # Confirms the Qt event loop is alive and this tick actually ran --
        # the core heartbeat signal. Also re-stamped after grabbing each
        # individual queued task below, so a slow multi-item batch doesn't
        # go stale between items. A single task slower than the heartbeat's
        # stale_after (default 2s) can still make the heartbeat look stale to
        # a *different*, concurrently-checking caller for that task's
        # duration -- an accepted narrow edge case, not a full fix, since the
        # alternative (a background keep-alive ping during task execution)
        # is real added complexity for a rare case that's still strictly
        # better than today's only option, a 30-120s blind timeout.
        self._heartbeat.stamp()
        self._refresh_visibility_cache()

        while not self._gui_task_queue.empty():
            req_id = None
            try:
                req_id, task = self._gui_task_queue.get_nowait()
                self._heartbeat.stamp()

                # Async job path
                if isinstance(req_id, str) and req_id.startswith("async:"):
                    job_id = req_id[6:]
                    job = self._async_jobs.get(job_id)
                    if job is None or job.get("status") != "running":
                        # Job was cancelled before it even started — skip
                        FreeCAD.Console.PrintMessage(
                            f"[MCP] Skipping cancelled/missing async job {job_id}\n"
                        )
                        continue
                    result = task()
                    job.update({
                        "status": "done",
                        "result": result,
                        "elapsed": time.time() - job["started"],
                        "finished": time.time(),
                    })
                    FreeCAD.Console.PrintMessage(
                        f"[MCP] async job {job_id} done\n"
                    )

                # Synchronous path
                else:
                    self._gui_thread_busy = True
                    try:
                        result = task()
                    finally:
                        self._gui_thread_busy = False
                    if req_id in self._stale_req_ids:
                        # Waiter already timed out — discard result, don't pollute the queue
                        self._stale_req_ids.discard(req_id)
                        FreeCAD.Console.PrintMessage(
                            f"[MCP] Discarding result for stale request {req_id}\n"
                        )
                    else:
                        self._gui_response_queue.put((req_id, result))

            except queue.Empty:
                break
            except Exception as e:
                tb = tb_module.format_exc()
                if isinstance(req_id, str) and req_id.startswith("async:"):
                    job_id = req_id[6:]
                    job = self._async_jobs.get(job_id)
                    if job is not None:
                        job.update({
                            "status": "error",
                            "error": str(e),
                            "error_id": self.diagnostics_ops.store_traceback(tb),
                            "elapsed": time.time() - job["started"],
                            "finished": time.time(),
                        })
                        FreeCAD.Console.PrintMessage(
                            f"[MCP] async job {job_id} error: {e}\n"
                        )
                elif req_id is not None:
                    self._gui_response_queue.put((req_id, {"error": f"GUI task error: {e}"}))

        if QtCore:
            QtCore.QTimer.singleShot(100, self._process_gui_tasks)

    def _gui_unresponsive_error(self) -> Optional[str]:
        """Return a JSON error string if the GUI thread's heartbeat is stale,
        else None.

        Used by the async submission paths (_call_on_gui_thread_async /
        _execute_python_async) before they queue a job. Unlike the sync path,
        _run_on_gui_thread_async itself has no return value to signal a
        problem through -- if the event loop truly isn't ticking, a silently
        queued async job just sits at status "running" forever with no
        useful signal, so the check has to happen at submission time, before
        the job entry is even created, not inside the fire-and-forget call.
        Only meaningful in GUI mode; headless/console mode has no event loop
        to have a heartbeat for, so this is skipped there.
        """
        if QtCore is None:
            return None
        if not self._heartbeat.is_stale():
            return None
        age = self._heartbeat.age()
        age_str = "never" if age is None else f"{age:.1f}s ago"
        return json.dumps({
            "error": f"GUI thread appears unresponsive (last confirmed alive: {age_str}). "
                     "FreeCAD may have crashed, be blocked in a modal dialog, or be "
                     "backgrounded/asleep. Not submitting this job -- check FreeCAD "
                     "directly rather than polling a job that will never complete."
        })

    def _submission_status(self, job_id: str, queue_depth: int) -> str:
        """Build the immediate {"job_id", "status": "submitted", ...} response
        for an async job, including queue_depth and active_connections so
        contention from other queued work or other connected clients is
        visible up front rather than presenting as an unexplained delay.
        Shared by _call_on_gui_thread_async and _execute_python_async so the
        two submission paths can't drift on what they report.

        queue_depth is passed in rather than read from self._gui_task_queue
        here, because by the time this runs the caller has already queued
        this job's own task -- reading qsize() at this point would count the
        job against itself. Callers capture qsize() immediately before
        submitting, so queue_depth means "how many other things are ahead of
        you", which is what a caller deciding whether to wait actually wants.
        """
        with self._active_connections_lock:
            active = self._active_connections
        return json.dumps({
            "job_id": job_id,
            "status": "submitted",
            "queue_depth": queue_depth,
            "active_connections": active,
        })

    def _run_on_gui_thread(self, task_fn, timeout=30.0) -> str:
        """Run a callable on the Qt GUI thread and wait for the result.

        This is the single entry point for all GUI-safe execution.
        Uses request IDs to prevent stale responses from previous
        timed-out calls from being confused with the current response.

        In headless mode (QtCore is None) there is no Qt event loop, so the
        GUI task queue is never drained.  We run the callable inline on the
        socket-handler thread instead — safe because there is no competing
        Qt main thread in that case.
        """
        if QtCore is None:
            # Headless / console mode: run inline, no queue needed.
            try:
                result = task_fn()
                if isinstance(result, dict):
                    if "error" in result:
                        return json.dumps({"error": result["error"]})
                    if "result" in result:
                        return json.dumps({"result": result["result"]})
                return json.dumps({"result": str(result)})
            except Exception as e:
                return json.dumps({"error": f"Headless task error: {e}"})

        # Heartbeat check: if the Qt event loop hasn't ticked recently, the
        # GUI thread isn't just busy -- it's not responding at all (crashed,
        # blocked in a modal dialog, backgrounded and throttled, etc). This
        # is checked BEFORE the busy guard below on purpose: if a previous
        # sync task never returned (e.g. FreeCAD died mid-task), _gui_thread_busy
        # stays stuck True forever, and the busy-guard's message ("wait for it
        # to complete") would be actively misleading -- it implies the task is
        # still progressing when the heartbeat says the event loop stopped
        # entirely. Fails fast instead of making the caller wait through the
        # full timeout to learn the same thing.
        if self._heartbeat.is_stale():
            age = self._heartbeat.age()
            age_str = "never" if age is None else f"{age:.1f}s ago"
            return json.dumps({
                "error": f"GUI thread appears unresponsive (last confirmed alive: {age_str}). "
                         "FreeCAD may have crashed, be blocked in a modal dialog, or be "
                         "backgrounded/asleep. Not queuing this request -- check FreeCAD "
                         "directly rather than waiting for a timeout."
            })

        # Busy guard: if the GUI thread is already blocked on a synchronous task,
        # refuse to queue more work.  The stuck task is still running; piling up
        # additional tasks causes cascade failures when it eventually returns.
        if self._gui_thread_busy:
            with self._active_connections_lock:
                active = self._active_connections
            FreeCAD.Console.PrintWarning(
                "[MCP] Rejected request — GUI thread is still busy with a previous operation\n"
            )
            return json.dumps({
                "error": "GUI thread is busy with a previous operation. "
                         "Wait for it to complete or use execute_python_async.",
                "active_connections": active,
            })

        self._request_counter += 1
        req_id = self._request_counter
        self._gui_task_queue.put((req_id, task_fn))

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                # Mark as stale so _process_gui_tasks discards the result when it arrives
                self._stale_req_ids.add(req_id)
                FreeCAD.Console.PrintWarning(
                    f"[MCP] Request {req_id} timed out after {timeout:.0f}s "
                    f"- GUI thread may still be busy\n"
                )
                return json.dumps({
                    "error": f"Operation timeout ({timeout:.0f}s) - "
                             f"GUI thread may be busy. Use execute_python_async "
                             f"for long operations."
                })
            try:
                resp_id, result = self._gui_response_queue.get(timeout=remaining)
            except queue.Empty:
                continue  # recalculate remaining and check deadline

            if resp_id != req_id:
                # Stale response from a previously timed-out request -- discard
                FreeCAD.Console.PrintWarning(
                    f"[MCP] Discarded stale response for request {resp_id} "
                    f"(waiting for {req_id})\n"
                )
                continue

            # Matched -- process result. Structurally identical to the
            # headless branch above: a task dict with no "result" key (e.g.
            # {"success": True, "view": ...}, the shape the now-removed
            # view_ops.set_view_gui_safe used to return) previously matched
            # a GUI-only `if "success" in result:` branch that indexed
            # result["result"] unconditionally -> KeyError. Falling through
            # to str(result) instead (as headless mode always did) is the
            # general contract any GUI task returning a bare dict depends on.
            if isinstance(result, dict):
                if "error" in result:
                    return json.dumps({"error": result["error"]})
                if "result" in result:
                    return json.dumps({"result": result["result"]})
            return json.dumps({"result": str(result)})

    def _run_on_gui_thread_async(self, job_id: str, task_fn) -> None:
        """Schedule task_fn on the Qt GUI thread without blocking the caller.

        The result is stored in self._async_jobs[job_id] when complete.

        Uses the same _gui_task_queue / _process_gui_tasks mechanism as
        synchronous execution.  QTimer.singleShot called from a non-Qt thread
        (the socket handler thread) does NOT post to the main-thread event loop
        — the callback silently never fires — so we must go through the queue
        that was set up on the main thread at startup.
        """
        if QtCore:
            # Tag the request so _process_gui_tasks routes it to the job dict
            self._gui_task_queue.put((f"async:{job_id}", task_fn))
        else:
            # Console mode: no event loop, run inline
            try:
                result = task_fn()
                self._async_jobs[job_id].update({
                    "status": "done",
                    "result": result,
                    "elapsed": time.time() - self._async_jobs[job_id]["started"],
                    "finished": time.time(),
                })
            except Exception as e:
                self._async_jobs[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "error_id": self.diagnostics_ops.store_traceback(tb_module.format_exc()),
                    "elapsed": time.time() - self._async_jobs[job_id]["started"],
                    "finished": time.time(),
                })

    def _cleanup_stale_async_jobs(self):
        """Remove completed async jobs older than ASYNC_JOB_TTL seconds."""
        now = time.time()
        stale = [
            jid for jid, job in self._async_jobs.items()
            if job["status"] in ("done", "error")
            and (now - job.get("finished", job["started"])) > ASYNC_JOB_TTL
        ]
        for jid in stale:
            del self._async_jobs[jid]

    def _execute_python_async(self, args: Dict[str, Any]) -> str:
        """Submit Python code for async GUI-safe execution; returns job_id immediately.

        Use poll_job(job_id) to check status and retrieve the result.
        Identical execution semantics to execute_python.
        """
        code = args.get("code", "")
        if not code:
            return json.dumps({"error": "No code provided"})

        unresponsive = self._gui_unresponsive_error()
        if unresponsive:
            return unresponsive

        # Clean up old completed jobs before checking the limit
        self._cleanup_stale_async_jobs()

        if len(self._async_jobs) >= MAX_ASYNC_JOBS:
            return json.dumps({
                "error": f"Too many async jobs ({len(self._async_jobs)}). "
                         f"Max is {MAX_ASYNC_JOBS}. Wait for jobs to complete or cancel some."
            })

        job_id = uuid.uuid4().hex[:8]
        self._async_jobs[job_id] = {
            "status": "running",
            "started": time.time(),
            "tool": "execute_python_async",
        }

        queue_depth = self._gui_task_queue.qsize()
        self._run_on_gui_thread_async(job_id, lambda: self.execute_python_ops.run_code(code))
        return self._submission_status(job_id, queue_depth)

    def _poll_job(self, args: Dict[str, Any]) -> str:
        """Poll status and result of an async job.

        Returns:
          {"status": "running", "elapsed_s": N}          — still computing
          {"status": "done",    "elapsed_s": N, "result": ...}  — finished
          {"status": "error",   "elapsed_s": N, "error": ...}   — failed
        Completed jobs are removed from tracking after retrieval.
        """
        job_id = args.get("job_id", "")
        if not job_id:
            return json.dumps({"error": "job_id required"})
        if job_id not in self._async_jobs:
            return json.dumps({"error": f"Unknown job_id: {job_id!r}. Already retrieved or never submitted."})

        job = self._async_jobs[job_id]
        elapsed = round(time.time() - job["started"], 1)

        if job["status"] == "running":
            response = {"status": "running", "elapsed_s": elapsed}
            if elapsed > 120:
                response["warning"] = (
                    f"Job has been running for {elapsed:.0f}s. "
                    f"If FreeCAD CPU is near zero, the Qt main thread may be blocked "
                    f"by a long OCCT operation (booleans on complex geometry can take "
                    f"many minutes or stall). "
                    f"Call cancel_job(job_id='{job_id}') to mark it failed, "
                    f"then restart_freecad to unblock the GUI thread."
                )
            return json.dumps(response)

        # Done or error — retrieve and clean up
        del self._async_jobs[job_id]

        if job["status"] == "done":
            task_result = job.get("result", {})
            if isinstance(task_result, dict) and "error" in task_result:
                return json.dumps({
                    "status": "error",
                    "error": task_result["error"],
                    "error_id": task_result.get("error_id"),
                    "elapsed_s": round(job.get("elapsed", elapsed), 1),
                })
            result_val = task_result.get("result", "done") if isinstance(task_result, dict) else str(task_result)
            return json.dumps({
                "status": "done",
                "result": result_val,
                "elapsed_s": round(job.get("elapsed", elapsed), 1),
            })
        else:
            return json.dumps({
                "status": "error",
                "error": job.get("error", "unknown error"),
                "error_id": job.get("error_id"),
                "elapsed_s": round(job.get("elapsed", elapsed), 1),
            })

    def _cancel_job(self, args: Dict[str, Any]) -> str:
        """Mark a running async job as cancelled and attempt to interrupt the operation.

        Marks the job registry entry as error immediately so future poll_job calls
        return an error instead of running forever.

        NOTE: If the job is executing an OCCT boolean (common, fuse, cut) on the
        Qt GUI thread, that C++ call will NOT be interrupted — it runs to completion
        or crashes regardless.  The GUI thread remains blocked until the operation
        finishes.  Use restart_freecad to fully recover a blocked GUI thread.
        """
        job_id = args.get("job_id", "")
        if not job_id:
            return json.dumps({"error": "job_id required"})
        if job_id not in self._async_jobs:
            return json.dumps({"error": f"Unknown job_id: {job_id!r}. Already retrieved or never submitted."})

        job = self._async_jobs[job_id]
        if job["status"] != "running":
            return json.dumps({"error": f"Job {job_id} is not running (status: {job['status']})"})

        elapsed = round(time.time() - job["started"], 1)
        job.update({
            "status": "error",
            "error": f"Cancelled by user after {elapsed}s",
            "elapsed": elapsed,
            # TTL cleanup measures retention from "finished" (see ASYNC_JOB_TTL);
            # without it a cancelled job is retained relative to its start time.
            "finished": time.time(),
        })

        # Attempt to set the FreeCAD cancellation flag (works for PartDesign/Thickness ops;
        # does NOT interrupt raw OCCT API calls like Shape.common/fuse/cut).
        cancel_note = ""
        try:
            if FreeCADGui:
                FreeCADGui.cancelOperation()
                cancel_note = " FreeCAD cancel flag set."
        except Exception:
            pass

        FreeCAD.Console.PrintWarning(
            f"[MCP] Job {job_id} cancelled after {elapsed}s.{cancel_note} "
            f"GUI thread may still be blocked if OCCT boolean is running.\n"
        )

        return json.dumps({
            "result": (
                f"Job {job_id} marked as cancelled after {elapsed}s.{cancel_note} "
                f"If the underlying OCCT operation is still executing on the GUI thread, "
                f"FreeCAD will remain unresponsive until it completes or crashes. "
                f"Use restart_freecad to fully recover."
            )
        })

    def _list_jobs(self, args: Dict[str, Any]) -> str:
        """List all tracked async jobs and their current status."""
        now = time.time()
        jobs = {
            jid: {
                "status": j["status"],
                "elapsed_s": round(now - j["started"], 1),
                # async jobs carry 'label', sync ones 'tool' — fall back so neither shows '?'
                "tool": j.get("tool") or j.get("label") or "?",
            }
            for jid, j in self._async_jobs.items()
        }
        return json.dumps({"jobs": jobs, "count": len(jobs)})

    # -----------------------------------------------------------------
    # cancel_operation
    # -----------------------------------------------------------------

    def _cancel_operation(self, args: Dict[str, Any]) -> str:
        """Request cancellation of the current long-running FreeCAD operation.

        Calls FreeCADGui.cancelOperation() which sets Base::OperationCancel::requested.
        The flag is checked within ≤200 ms by any cancellable operation
        (Thickness, boolean, Check Geometry, …).

        Safe to call from the socket thread while the GUI thread is blocked —
        the atomic store is lock-free and immediately visible to the GUI thread.
        """
        try:
            import FreeCADGui as Gui
            Gui.cancelOperation()
            return json.dumps({"result": "Cancel requested — operation will stop within 200 ms"})
        except Exception as e:
            return json.dumps({"error": f"cancelOperation failed: {e}"})

    # -----------------------------------------------------------------
    # Connection handling
    # -----------------------------------------------------------------

    def _server_loop(self):
        """Accept connections in a loop."""
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                try:
                    client_socket, _ = self.server_socket.accept()
                    threading.Thread(
                        target=self._handle_client,
                        args=(client_socket,),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
            except Exception as e:
                if self.running:
                    FreeCAD.Console.PrintError(f"Server loop error: {e}\n")

    def _handle_client(self, client_socket):
        """Handle a single client connection.

        There's no persistent per-client session at this layer -- every tool
        call is its own short-lived connection (receive one message, respond,
        close) -- so active_connections counts concurrent in-flight requests
        across however many bridge processes happen to be connected right
        now, not identified individual clients. Surfaced in busy/queued
        responses so contention from another connection is visible instead
        of presenting as an unexplained delay.
        """
        with self._active_connections_lock:
            self._active_connections += 1
        try:
            message_str = receive_message(client_socket)
            if message_str:
                response = self._process_command(message_str)
                if not send_message(client_socket, response):
                    # send_message already logged the specific cause
                    # (oversized frame or socket error) — but silently
                    # falling through here leaves the client with nothing
                    # but a closed connection and no explanation. Best-effort
                    # one retry with a small guaranteed-to-fit payload,
                    # mirroring the fallback-send already used in the
                    # except branch below.
                    try:
                        send_message(client_socket, json.dumps({
                            "error": "Failed to send response (oversized or socket error); result discarded."
                        }))
                    except Exception:
                        pass
        except Exception as e:
            FreeCAD.Console.PrintError(f"Client handler error: {e}\n")
            if DEBUG_ENABLED:
                _log_operation(
                    operation="CLIENT_ERROR",
                    error=e,
                    parameters={"traceback": tb_module.format_exc()},
                )
            try:
                send_message(client_socket, json.dumps({"error": f"Server error: {e}"}))
            except Exception:
                pass
        finally:
            with self._active_connections_lock:
                self._active_connections -= 1
            try:
                client_socket.close()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Command processing
    # -----------------------------------------------------------------

    def _process_command(self, command_str: str) -> str:
        """Parse and dispatch an incoming command."""
        start_time = time.time()
        tool_name = "unknown"

        try:
            command = json.loads(command_str)
            if not isinstance(command, dict):
                return json.dumps({
                    "error": f"Malformed request: expected a JSON object, got {type(command).__name__}"
                })

            unknown_keys = set(command.keys()) - _KNOWN_REQUEST_KEYS
            if unknown_keys:
                return json.dumps({
                    "error": f"Unrecognized request key(s): {sorted(unknown_keys)}. "
                             f"Expected only {sorted(_KNOWN_REQUEST_KEYS)}."
                })

            tool_name = command.get("tool", "")
            args = command.get("args", {})

            if not tool_name:
                return json.dumps({"error": "No tool specified"})

            if DEBUG_ENABLED:
                if CAPTURE_STATE_PER_COMMAND:
                    _capture_state()
                _log_operation(
                    operation="COMMAND_START",
                    parameters={"tool": tool_name, "args": args},
                )

            result = self._execute_tool(tool_name, args)

            if DEBUG_ENABLED:
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_SUCCESS",
                    parameters={"tool": tool_name},
                    result=result[:200] if len(result) > 200 else result,
                    duration=duration,
                )

            return result

        except json.JSONDecodeError as e:
            if DEBUG_ENABLED:
                _log_operation(
                    operation="JSON_PARSE_ERROR",
                    error=e,
                    parameters={"command_preview": command_str[:10000]},
                )
            return json.dumps({"error": f"Invalid JSON: {e}"})
        except Exception as e:
            if DEBUG_ENABLED:
                duration = time.time() - start_time
                _log_operation(
                    operation="COMMAND_ERROR",
                    error=e,
                    parameters={
                        "tool": tool_name,
                        "traceback": tb_module.format_exc(),
                    },
                    duration=duration,
                )
                if _monitor:
                    try:
                        _monitor.log_crash(
                            health_status={"tool": tool_name, "error": str(e)},
                            error_context=tb_module.format_exc(),
                        )
                    except Exception:
                        pass
            return json.dumps({"error": f"Command processing error: {e}"})

    # -----------------------------------------------------------------
    # Tool routing and dispatch
    # -----------------------------------------------------------------

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Route a tool call to the appropriate handler."""
        # ── Crash watcher: record op on disk before executing ──────────────
        # If FreeCAD crashes, this file persists so the bridge can report
        # *what* was running.  clear_current_op() is called on success.
        if _set_current_op is not None:
            _set_current_op(tool_name, args)
        try:
            return self._execute_tool_inner(tool_name, args)
        finally:
            if _clear_current_op is not None:
                _clear_current_op()

    def _execute_tool_inner(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Internal dispatch (called by _execute_tool after op tracking setup)."""

        # Direct handler method map (GUI-safe — runs on Qt thread)
        direct_map = {
            "create_box": self.primitives.create_box,
            "create_cylinder": self.primitives.create_cylinder,
            "create_sphere": self.primitives.create_sphere,
            "create_cone": self.primitives.create_cone,
            "create_torus": self.primitives.create_torus,
            "create_wedge": self.primitives.create_wedge,
            "move_object": self.transforms.move_object,
            "rotate_object": self.transforms.rotate_object,
            "copy_object": self.transforms.copy_object,
            "array_object": self.transforms.array_object,
            "create_sketch": self.sketch_ops.create_sketch,
            "sketch_verify": self.sketch_ops.verify_sketch,
        }

        # Boolean ops use the async path — they can run arbitrarily long on complex
        # geometry and must not be subject to the sync GUI-thread timeout.
        async_boolean_map = {
            "fuse_objects": self.boolean_ops.fuse_objects,
            "cut_objects": self.boolean_ops.cut_objects,
            "common_objects": self.boolean_ops.common_objects,
        }
        if tool_name in async_boolean_map:
            return self._call_on_gui_thread_async(async_boolean_map[tool_name], args, tool_name)

        if tool_name in direct_map:
            method = direct_map[tool_name]
            return self._call_on_gui_thread_async(method, args, tool_name)

        # Smart dispatchers — route by operation name within a handler
        # PartDesign has explicit method mapping (operation names differ from method names)
        if tool_name == "partdesign_operations":
            return self._dispatch_partdesign(args)
        # Sketch operations — explicit method mapping
        if tool_name == "sketch_operations":
            return self._dispatch_sketch(args)
        # Part operations have mixed routing across multiple handlers
        if tool_name == "part_operations":
            return self._dispatch_part_operations(args)
        # View control mixes view_ops and document_ops
        if tool_name == "view_control":
            return self._dispatch_view_control(args)

        # CAM version gate — return clean error on builds without the Path Toolbit API
        _CAM_TOOLS = {"cam_operations", "cam_tools", "cam_tool_controllers"}
        if tool_name in _CAM_TOOLS and self._fc_version < CAM_MIN_FC_VERSION:
            running = ".".join(str(x) for x in self._fc_version)
            return json.dumps({
                "error": (
                    f"CAM tools require a FreeCAD build with the Path Toolbit API "
                    f"(weekly builds / 26.x; running {running}). "
                    f"Use a recent FreeCAD weekly build for CAM support."
                )
            })

        # Generic dispatchers — operation name matches handler method name
        generic_dispatch_map = {
            "cam_operations": self.cam_ops,
            "cam_tools": self.cam_tools,
            "cam_tool_controllers": self.cam_tool_controllers,
            "draft_operations": self.draft_ops,
            "mesh_operations": self.mesh_ops,
            "spreadsheet_operations": self.spreadsheet_ops,
            "measurement_operations": self.measurement_ops,
            "spatial_query": self.spatial_ops,
            "macro_operations": self.macro_ops,
            "api_introspection": self.introspection_ops,
            "geometric_verification": self.verification_ops,
            "fixture_operations": self.fixture_ops,
            "assembly_operations": self.assembly_ops,
        }

        # run_inspector is a direct-dispatch tool (no 'operation' sub-field)
        if tool_name == "run_inspector":
            return self._call_on_gui_thread_async(self.inspector_ops.run, args, "run_inspector")

        # build_sketch: validate + emit a parametric sketch via SketchBuilder
        if tool_name == "build_sketch":
            return self._call_on_gui_thread_async(self.sketch_builder_ops.build_sketch, args, "build_sketch")

        if tool_name in generic_dispatch_map:
            return self._dispatch_to_handler(generic_dispatch_map[tool_name], args, tool_name)

        # Special tools
        # execute_python_sync: the raw-socket synchronous entry, used only by
        # direct-socket callers (the integration test suite's send_command
        # helper) that want to block until the code finishes rather than
        # poll a job. This is NOT what the MCP-facing "execute_python" tool
        # calls -- that tool (freecad_mcp_server.py) always submits via
        # execute_python_async and polls, specifically to avoid holding the
        # GUI thread until the socket timeout on long-running code (see
        # commit ecbe827, "Make execute_python use async+poll with unlimited
        # timeout"). Two different names now, on purpose, to end the
        # confusing state where the same string meant two different things
        # depending which layer called it.
        if tool_name == "execute_python_sync":
            return self.execute_python_ops.execute(args)
        if tool_name == "execute_python_async":
            return self._execute_python_async(args)
        if tool_name == "poll_job":
            return self._poll_job(args)
        if tool_name == "list_jobs":
            return self._list_jobs(args)
        if tool_name == "cancel_operation":
            return self._cancel_operation(args)
        if tool_name == "cancel_job":
            return self._cancel_job(args)
        if tool_name == "get_debug_logs":
            return self.diagnostics_ops.get_debug_logs(args)
        if tool_name == "get_last_traceback":
            return self.diagnostics_ops.get_last_traceback(args)
        if tool_name == "restart_freecad":
            return self.diagnostics_ops.restart_freecad(args)
        if tool_name == "reload_modules":
            return self._call_on_gui_thread_reload()
        if tool_name == "get_instance_info":
            return self._get_instance_info()
        if tool_name == "continue_selection":
            return self._continue_selection(args)
        if tool_name == "test_echo":
            # Internal liveness probe used by restart_freecad's readiness poll
            # (freecad_mcp_server.py sends this raw socket command directly,
            # separate from the client-facing test_echo MCP tool the bridge
            # answers itself). Any reply with no "error" key proves this
            # instance is up and dispatching.
            return json.dumps({"echo": args.get("message", "")})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _continue_selection(self, args: Dict[str, Any]) -> str:
        """Resume an interactive selection started via selector.request_selection().

        The dedicated continue_selection MCP tool sends only operation_id.
        Look up which handler method was waiting (stashed in
        selector.pending_operations by request_selection) and re-invoke that
        same method with _continue_selection=True + _operation_id — each of
        fillet_edges/chamfer_edges/draft_faces/shell_solid/thickness_faces
        already knows how to complete its own pending selection when called
        this way.
        """
        operation_id = args.get("operation_id")
        if not operation_id:
            return json.dumps({"error": "operation_id is required"})

        pending = self.selector.pending_operations.get(operation_id)
        if pending is None:
            return json.dumps({"error": "Selection operation not found or expired"})

        resume_tool = pending.get("tool")
        resume_methods = {
            "fillet_edges": self.partdesign_ops.fillet_edges,
            "chamfer_edges": self.partdesign_ops.chamfer_edges,
            "draft_faces": self.partdesign_ops.draft_faces,
            "shell_solid": self.partdesign_ops.shell_solid,
            "thickness_faces": self.partdesign_ops.thickness_faces,
        }
        method = resume_methods.get(resume_tool)
        if method is None:
            return json.dumps({"error": f"No resume handler registered for tool: {resume_tool}"})

        resumed_args = {k: v for k, v in pending.items()
                         if k not in ("tool", "type", "object", "timestamp")}
        resumed_args["object_name"] = pending.get("object", "")
        resumed_args["_continue_selection"] = True
        resumed_args["_operation_id"] = operation_id
        return self._call_on_gui_thread_async(method, resumed_args, resume_tool)

    def _get_instance_info(self) -> str:
        """Return identifying info about this FreeCAD process.

        Cheap, read-only query used by the bridge's list_freecad_instances
        to enrich the listing with what each instance currently has open.
        """
        def task():
            try:
                doc = FreeCAD.ActiveDocument
                doc_label = doc.Label if doc else None
                doc_file = doc.FileName if doc else None
            except Exception:
                doc_label = None
                doc_file = None

            window_title = None
            if FreeCAD.GuiUp:
                try:
                    mw = FreeCADGui.getMainWindow()
                    if mw is not None:
                        window_title = mw.windowTitle()
                except Exception:
                    pass

            try:
                version = ".".join(str(p) for p in FreeCAD.Version()[:3])
            except Exception:
                version = None

            return {
                "success": True,
                "result": {
                    "uuid": getattr(self, "instance_uuid", None),
                    "socket_path": getattr(self, "socket_path", None),
                    "gui": bool(FreeCAD.GuiUp),
                    "active_doc_label": doc_label,
                    "active_doc_file": doc_file,
                    "window_title": window_title,
                    "freecad_version": version,
                    "pid": os.getpid(),
                },
            }
        return self._run_on_gui_thread(task, timeout=2.0)

    def _call_on_gui_thread_async(self, method, args: Dict[str, Any], label: str) -> str:
        """Submit a handler method call for async GUI execution; returns job_id immediately.

        Use poll_job(job_id) to retrieve the result. Intended for long-running
        operations (boolean ops on complex geometry) that would otherwise hit
        the sync timeout and leave the GUI thread stuck.
        """
        unresponsive = self._gui_unresponsive_error()
        if unresponsive:
            return unresponsive

        self._cleanup_stale_async_jobs()
        if len(self._async_jobs) >= MAX_ASYNC_JOBS:
            return json.dumps({"error": f"Too many async jobs ({len(self._async_jobs)}). "
                                        "Use poll_job / cancel_job to clear existing jobs."})
        job_id = uuid.uuid4().hex[:8]
        self._async_jobs[job_id] = {
            "status": "running",
            "started": time.time(),
            "label": label,
            "result": None,
            "error": None,
            "elapsed": None,
        }
        def task():
            try:
                result = method(args)
                return {"success": True, "result": result}
            except Exception as e:
                return {"error": f"{label} error: {e}", "error_id": self.diagnostics_ops.store_traceback(tb_module.format_exc())}
        queue_depth = self._gui_task_queue.qsize()
        self._run_on_gui_thread_async(job_id, task)
        return self._submission_status(job_id, queue_depth)

    def _call_on_gui_thread_reload(self, timeout: float = 60.0) -> str:
        """Run _reload_handlers() on the Qt GUI thread instead of the socket
        thread that dispatches this call.

        _reload_handlers() re-executes freecad_mcp_handler.py's own module
        code (including its PySide/QtCore imports) and rebuilds handler
        instances that hold live Qt-bound state (e.g. ViewOpsHandler's
        _clip_planes Coin3D scene-graph nodes). Every other GUI-touching tool
        in this dispatcher already runs through _run_on_gui_thread for
        exactly this reason -- reload_modules used to call _reload_handlers()
        directly from the socket thread instead, racing the live Qt event
        loop. That combination crashed FreeCAD outright (SIGSEGV inside
        Shiboken's binding manager during a QPushButton teardown) when
        reproduced live on 2026-07-27, so this is a correctness fix, not a
        style one.

        _reload_handlers() already returns a complete JSON string (not a
        plain result string like other handler methods), so unlike
        _call_on_gui_thread_async this parses that JSON
        back into a dict before handing it to _run_on_gui_thread -- otherwise
        _run_on_gui_thread's own dict-to-JSON wrapping would double-encode it
        as an escaped string.
        """
        def task():
            try:
                parsed = json.loads(self._reload_handlers())
            except Exception as e:
                return {"error": f"reload_modules error: {e}"}
            if "error" in parsed:
                return {"error": parsed["error"]}
            return {"result": parsed}
        return self._run_on_gui_thread(task, timeout=timeout)

    def _dispatch_to_handler(self, handler, args: Dict[str, Any], tool_name: str) -> str:
        """Generic dispatch: look up args['operation'] against handler._ALLOWED_OPERATIONS."""
        operation = args.get("operation", "")

        if not operation:
            return json.dumps({"error": f"Missing operation for {tool_name}"})

        allowed = getattr(handler, "_ALLOWED_OPERATIONS", None)
        if allowed is None:
            return json.dumps({"error": f"Handler for {tool_name} has no _ALLOWED_OPERATIONS registry"})

        if operation not in allowed:
            return json.dumps({"error": f"Unknown {tool_name} operation: {operation}"})

        method = getattr(handler, operation, None)
        if not method or not callable(method):
            return json.dumps({"error": f"Operation {operation} not callable on {tool_name} handler"})

        return self._call_on_gui_thread_async(method, args, f"{tool_name} {operation}")

    def _dispatch_partdesign(self, args: Dict[str, Any]) -> str:
        """Route PartDesign operations (operation names differ from method names)."""
        operation = args.get("operation", "")

        operation_map = {
            # Additive features
            "pad": self.partdesign_ops.pad_sketch,
            "revolution": self.partdesign_ops.revolution,
            "loft": self.partdesign_ops.loft_profiles,
            "sweep": self.partdesign_ops.sweep_path,
            "additive_pipe": self.partdesign_ops.additive_pipe,
            # Subtractive features
            "pocket": self.partdesign_ops.pocket,
            "groove": self.partdesign_ops.groove,
            "subtractive_loft": self.partdesign_ops.subtractive_loft,
            "subtractive_sweep": self.partdesign_ops.subtractive_sweep,
            # Dress-up features
            "fillet": self.partdesign_ops.fillet_edges,
            "chamfer": self.partdesign_ops.chamfer_edges,
            "draft": self.partdesign_ops.draft_faces,
            "shell": self.partdesign_ops.shell_solid,
            "thickness": self.partdesign_ops.add_thickness,
            # Hole features
            "hole": self.partdesign_ops.hole_wizard,
            "counterbore": self.partdesign_ops.hole_wizard,
            "countersink": self.partdesign_ops.hole_wizard,
            # Pattern features
            "linear_pattern": self.partdesign_ops.linear_pattern,
            "polar_pattern": self.partdesign_ops.polar_pattern,
            "mirror": self.partdesign_ops.mirror_feature,
            # Additional features
            "helix": self.partdesign_ops.create_helix,
            "rib": self.partdesign_ops.create_rib,
            # Datum features
            "datum_plane": self.partdesign_ops.create_datum_plane,
            "datum_line": self.partdesign_ops.create_datum_line,
            "datum_point": self.partdesign_ops.create_datum_point,
            "datum_from_face": self.partdesign_ops.datum_from_face,
        }

        if operation not in operation_map:
            return json.dumps({"error": f"Unknown PartDesign operation: {operation}"})

        return self._call_on_gui_thread_async(operation_map[operation], args, f"PartDesign {operation}")

    def _dispatch_sketch(self, args: Dict[str, Any]) -> str:
        """Route Sketch operations (explicit mapping)."""
        operation = args.get("operation", "")

        operation_map = {
            # Lifecycle
            "create_sketch": self.sketch_ops.create_sketch,
            "close_sketch": self.sketch_ops.close_sketch,
            "verify_sketch": self.sketch_ops.verify_sketch,
            # Geometry
            "add_line": self.sketch_ops.add_line,
            "add_circle": self.sketch_ops.add_circle,
            "add_rectangle": self.sketch_ops.add_rectangle,
            "add_arc": self.sketch_ops.add_arc,
            "add_polygon": self.sketch_ops.add_polygon,
            "add_slot": self.sketch_ops.add_slot,
            "add_fillet": self.sketch_ops.add_fillet,
            # Constraints
            "add_constraint": self.sketch_ops.add_constraint,
            "delete_constraint": self.sketch_ops.delete_constraint,
            "list_constraints": self.sketch_ops.list_constraints,
            # External geometry
            "add_external_geometry": self.sketch_ops.add_external_geometry,
        }

        if operation not in operation_map:
            return json.dumps({"error": f"Unknown Sketch operation: {operation}"})

        return self._call_on_gui_thread_async(operation_map[operation], args, f"Sketch {operation}")

    def _dispatch_part_operations(self, args: Dict[str, Any]) -> str:
        """Route Part operations across multiple handlers."""
        operation = args.get("operation", "")

        method = None
        if operation in ("box", "cylinder", "sphere", "cone", "torus", "wedge"):
            method = getattr(self.primitives, f"create_{operation}", None)
        elif operation in ("fuse", "cut", "common"):
            method = getattr(self.boolean_ops, f"{operation}_objects", None)
        elif operation in ("move", "rotate", "copy", "array"):
            method = getattr(self.transforms, f"{operation}_object", None)
        elif operation in ("extrude", "revolve", "loft", "sweep"):
            method = getattr(self.part_ops, operation, None)
        elif operation == "mirror":
            method = self.part_ops.mirror_object
        elif operation == "scale":
            method = self.part_ops.scale_object
        elif operation == "section":
            method = self.part_ops.section
        elif operation == "compound":
            method = self.part_ops.compound
        elif operation == "check_geometry":
            method = self.part_ops.check_geometry
        elif operation == "shape_string":
            method = self.part_ops.shape_string

        if not method:
            return json.dumps({"error": f"Unknown Part operation: {operation}"})

        return self._call_on_gui_thread_async(method, args, f"Part {operation}")

    def _dispatch_view_control(self, args: Dict[str, Any]) -> str:
        """Route view control operations (mixes view_ops and document_ops).

        Operations that touch the GUI (screenshots, view changes, selection,
        hide/show, undo/redo) are routed through _call_on_gui_thread_async to
        prevent crashes from calling Qt/Coin3D from the socket thread.

        Screenshot gets a longer timeout (60s) because saveImage() on
        complex scenes can be slow.
        """
        operation = args.get("operation", "")

        # macOS screenshot: screencapture runs as a subprocess and must NOT run
        # on the GUI thread — subprocess.run() blocks Qt event processing, which
        # causes the bridge to time out even when screencapture works fine.
        # take_screenshot() uses FreeCAD.ActiveDocument (thread-safe) on macOS.
        if operation == "screenshot" and platform.system() == "Darwin":
            try:
                result = self.view_ops.take_screenshot(args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"Screenshot error: {e}"})

        # --- Operations that MUST run on the GUI thread ---
        gui_ops = {
            "screenshot":         self.view_ops.take_screenshot,
            "set_view":           self.view_ops.set_view,
            "fit_all":            self.view_ops.fit_all,
            "zoom_in":            self.view_ops.zoom_in,
            "zoom_out":           self.view_ops.zoom_out,
            "select_object":      self.view_ops.select_object,
            "clear_selection":    self.view_ops.clear_selection,
            "get_selection":      self.view_ops.get_selection,
            "hide_object":        self.view_ops.hide_object,
            "show_object":        self.view_ops.show_object,
            "delete_object":      self.view_ops.delete_object,
            "undo":               self.view_ops.undo,
            "redo":               self.view_ops.redo,
            "activate_workbench": self.view_ops.activate_workbench,
            "get_report_view":    self.view_ops.get_report_view,
            "add_clip_plane":     self.view_ops.add_clip_plane,
            "remove_clip_plane":  self.view_ops.remove_clip_plane,
            # Document mutations — must run on GUI thread (recompute touches Qt)
            "rollback_to_checkpoint": self.document_ops.rollback_to_checkpoint,
            "insert_shape":           self.document_ops.insert_shape,
            # save()/saveAs() emit modified/title signals the GUI observes; on
            # macOS a save from the socket thread trips the main-thread assert.
            "save_document":          self.document_ops.save_document,
            # openDocument() creates ViewProviders and touches Qt-observed
            # tree-view state just like create_document/save_document above,
            # but (unlike create_document) has no internal GUI-thread
            # self-dispatch of its own -- must go through this wrapper.
            "open_document":          self.document_ops.open_document,
        }

        # --- Operations safe to call from any thread ---
        safe_ops = {
            "create_document":      self.document_ops.create_document,
            "list_objects":         self.document_ops.list_objects,
            "get_object_properties": self.document_ops.get_object_properties,
            # checkpoint only reads names — thread-safe
            "checkpoint":           self.document_ops.checkpoint,
        }

        if operation in gui_ops:
            return self._call_on_gui_thread_async(gui_ops[operation], args, f"view_control {operation}")

        if operation in safe_ops:
            try:
                result = safe_ops[operation](args)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"View control {operation} error: {e}"})

        return json.dumps({"error": f"Unknown view control operation: {operation}"})

    def _reload_handlers(self) -> str:
        """Hot-reload all handler modules and re-create handler instances.

        After deploying new code (rsync), call this instead of restarting
        FreeCAD.  Reloads every handler module in dependency order (base
        first), then re-imports the classes and re-creates instances on
        this server.

        Uses spec_from_file_location (not importlib.reload) to bypass stale
        pyc caches — rsync preserves mtimes, so pyc often appears newer than
        the freshly deployed .py source.

        Also reloads freecad_mcp_handler.py itself and rebinds
        _execute_tool_inner so dispatch-map changes (new tools, new routing)
        take effect without a FreeCAD restart.
        """
        import importlib.util, os, types

        def _reload(module_name: str, module) -> object:
            """Force-load from .py source, update sys.modules, return new module."""
            src = os.path.realpath(module.__file__)
            spec = importlib.util.spec_from_file_location(module_name, src)
            new_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(new_mod)
            sys.modules[module_name] = new_mod
            return new_mod

        try:
            # Reload base first (other handlers inherit from it)
            import handlers.base as _base
            _reload('handlers.base', _base)

            # Reload each handler module. Derived from the same
            # _HANDLER_CLASS_NAMES dict __init__ uses -- attr_name maps
            # 1:1 onto its module's dotted path (handlers.<attr_name>).
            handler_modules = [f'handlers.{attr}' for attr in _HANDLER_CLASS_NAMES]
            for mod_name in handler_modules:
                mod = sys.modules.get(mod_name)
                if mod:
                    _reload(mod_name, mod)

            # Reload the package __init__ so lookups against it resolve to
            # the freshly-reloaded classes, then rebuild the {attr_name:
            # class} map against that fresh package object -- same
            # _build_handler_class_map used at startup, just fed a
            # different (freshly-reloaded) `handlers` module object.
            import handlers as _handlers_pkg
            _handlers_pkg = _reload('handlers', _handlers_pkg)
            fresh_handler_classes = _build_handler_class_map(_handlers_pkg)

            # _checkpoints (DocumentOpsHandler), _clip_planes (ViewOpsHandler),
            # the traceback ring buffer (DiagnosticsOpsHandler), and the
            # execute_python namespace (ExecutePythonOpsHandler) are
            # lazily-created plain instance attributes with no persistence
            # anywhere else. Replacing the handler instances below would
            # otherwise silently discard them: rollback_to_checkpoint would
            # report a misleading "no checkpoint named X" for a checkpoint
            # that genuinely existed before this reload, any pending
            # clip-plane Coin3D scene-graph node would become unreachable
            # (its handle only lived in the old instance's list) and leak in
            # the 3D view forever since nothing could find it to remove it,
            # get_last_traceback would silently lose crash history from
            # right before the reload — the exact moment it's most useful —
            # and execute_python's persistent namespace (variables surviving
            # across calls, its whole documented point) would silently reset
            # to empty. That last one used to be a non-issue when the
            # namespace lived directly on the server (untouched by handler
            # re-instantiation); moving it into a handler makes it subject
            # to the same hazard as the others, so it needs the same fix.
            old_checkpoints = getattr(self, 'document_ops', None) and getattr(self.document_ops, '_checkpoints', None)
            old_clip_planes = getattr(self, 'view_ops', None) and getattr(self.view_ops, '_clip_planes', None)
            old_diag = getattr(self, 'diagnostics_ops', None)
            old_tracebacks = old_diag and old_diag._last_tracebacks
            old_traceback_counter = old_diag._traceback_counter if old_diag else 0
            old_python_ns = getattr(self, 'execute_python_ops', None) and getattr(self.execute_python_ops, '_python_namespace', None)

            # Re-create handler instances
            self._instantiate_handlers(fresh_handler_classes)
            if old_checkpoints:
                self.document_ops._checkpoints = old_checkpoints
            if old_clip_planes:
                self.view_ops._clip_planes = old_clip_planes
            if old_tracebacks:
                self.diagnostics_ops._last_tracebacks = old_tracebacks
            self.diagnostics_ops._traceback_counter = old_traceback_counter
            if old_python_ns:
                self.execute_python_ops._python_namespace = old_python_ns

            # Reload freecad_mcp_handler.py itself and rebind _execute_tool_inner
            # so dispatch-map changes (new tools added to generic_dispatch_map)
            # take effect without a FreeCAD restart.
            import AICopilot.freecad_mcp_handler as _self_mod
            new_self = _reload('AICopilot.freecad_mcp_handler', _self_mod)
            # Rebind all dispatch methods so routing changes take effect immediately.
            # _execute_tool_inner calls self._dispatch_view_control etc., so those
            # must be rebound too or the old routing (without new operations) runs.
            _dispatch_methods = [
                '_execute_tool_inner',
                '_dispatch_view_control',
                '_dispatch_partdesign',
                '_dispatch_sketch',
                '_dispatch_part_operations',
                '_dispatch_to_handler',
                '_call_on_gui_thread_reload',
                '_reload_handlers',   # rebind self so future reloads use latest code
                '_instantiate_handlers',
            ]
            for method_name in _dispatch_methods:
                new_fn = getattr(new_self.FreeCADSocketServer, method_name, None)
                if new_fn:
                    setattr(self, method_name, types.MethodType(new_fn, self))

            n = len(handler_modules) + 1  # +1 for base
            FreeCAD.Console.PrintMessage(f"[MCP] Reloaded {n} handler modules\n")
            return json.dumps({
                "result": f"Reloaded {n} handler modules successfully",
                "modules_reloaded": n,
            })

        except Exception as e:
            FreeCAD.Console.PrintError(f"[MCP] Handler reload failed: {e}\n")
            return json.dumps({"error": f"Handler reload failed: {e}"})

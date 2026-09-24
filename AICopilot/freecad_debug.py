#!/usr/bin/env python3
"""
FreeCAD MCP Debugging Infrastructure - OPTIMIZED
=================================================

Comprehensive debugging with production-friendly lean logging mode.

Key changes from original:
- LEAN_LOGGING mode: Only logs operation start/end, skips intermediate stages
- Compact log format: Reduces JSON overhead
- Configurable per-stage logging: Disable verbose logging in production
- Token-efficient: ~60% reduction in log volume

Features:
- Detailed operation logging with timestamps (when enabled)
- Full exception tracking with stack traces
- Performance monitoring and timing
- FreeCAD state snapshots (optional)
- Automatic crash detection and recovery
- Rolling log files with rotation
- Configurable verbosity levels

Author: Brian (with Claude)
Version: 1.1.0 (Optimized)
"""

# Version declaration
__version__ = "1.1.1"

# Try to register with version system if available
try:
    from mcp_versions import register_component
    from datetime import datetime as _dt
    register_component("freecad_debug", __version__, _dt.now().isoformat())
except ImportError:
    # Version system not available, continue without it
    pass

import functools
import inspect
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import tmp_safety
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    # crash_watcher's redaction is deliberately reused (not a general-
    # purpose scanner -- see its own module docstring for scope/limits).
    # This module ships with lean_logging=False hardcoded at its call site
    # (freecad_mcp_handler.py), so _log_operation persists full tool
    # arguments -- including execute_python's raw code -- to disk on every
    # call; without this, that path had zero redaction while crash_watcher's
    # equivalent last-op file did.
    from crash_watcher import _redact_secrets
except ImportError:
    def _redact_secrets(text: str) -> str:
        return text


def _redact_value(value: Any) -> Any:
    """Recursively apply _redact_secrets to string values inside a
    JSON-like structure, leaving non-string types untouched."""
    if isinstance(value, str):
        return _redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


# LEAN LOGGING CONFIGURATION
# Set LEAN_LOGGING = False to get verbose per-stage logging for development
LEAN_LOGGING = True


class FreeCADDebugger:
    """Comprehensive debugging for FreeCAD MCP operations with production optimization."""
    
    # Logging levels
    CRITICAL = logging.CRITICAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    INFO = logging.INFO
    DEBUG = logging.DEBUG
    
    def __init__(
        self,
        log_dir: str = "/tmp/freecad_mcp_debug",
        level: int = logging.DEBUG,
        max_log_size: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        enable_console: bool = True,
        enable_file: bool = True,
        lean_logging: bool = True,  # NEW: Enable production logging mode
    ):
        """
        Initialize the debugger.
        
        Args:
            log_dir: Directory for log files
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            max_log_size: Maximum size of each log file before rotation
            backup_count: Number of backup log files to keep
            enable_console: Enable console output
            enable_file: Enable file logging
            lean_logging: If True, only log start/end; skip intermediate stages
        """
        self.log_dir = Path(log_dir)
        # log_dir defaults to a fixed, predictable /tmp path — refuse to
        # follow a symlink an attacker with local access could have
        # planted there before this process started.
        tmp_safety.safe_mkdir(str(self.log_dir))
        
        self.level = level
        self.max_log_size = max_log_size
        self.backup_count = backup_count
        self.lean_logging = lean_logging
        
        # Setup main logger
        self.logger = logging.getLogger("FreeCAD_MCP")
        self.logger.setLevel(level)
        self.logger.handlers.clear()
        self.logger.propagate = False  # Don't let messages bubble to root → stderr → Report View
        
        # Console handler
        if enable_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            console_formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
        
        # File handler with rotation
        if enable_file:
            from logging.handlers import RotatingFileHandler
            log_file = self.log_dir / "freecad_mcp.log"
            # log_file is a fixed, predictable path under log_dir -- refuse
            # to follow a symlink an attacker with local access could have
            # planted there before this process started. The commit that
            # added this check elsewhere (5b53802) only wired it into the
            # two mkdir() call sites (via tmp_safety.safe_mkdir) and
            # crash_watcher's own open()-for-write site, never this one or
            # the json_log_file sites below.
            tmp_safety.refuse_if_symlink(str(log_file))
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_log_size,
                backupCount=backup_count
            )
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s - %(funcName)s:%(lineno)d: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
        
        # Performance tracking
        self.operation_times: Dict[str, List[float]] = {}
        
        # State tracking
        self.last_freecad_state: Optional[Dict] = None
        
        mode = "LEAN" if lean_logging else "VERBOSE"
        self.logger.info(f"FreeCAD MCP Debugger initialized (MODE: {mode})")
        self.logger.info(f"Log directory: {self.log_dir}")
        self.logger.info(f"Log level: {logging.getLevelName(level)}")
    
    def log_operation(
        self,
        operation: str,
        parameters: Optional[Dict] = None,
        result: Optional[Any] = None,
        error: Optional[Exception] = None,
        duration: Optional[float] = None,
    ):
        """
        Log a FreeCAD operation with optional full details.
        
        In LEAN mode: logs only essential info (operation name, success/failure)
        In VERBOSE mode: logs full details including timestamps and parameters
        
        Args:
            operation: Name of the operation
            parameters: Operation parameters
            result: Operation result
            error: Exception if operation failed
            duration: Operation duration in seconds
        """
        if error:
            # Always log errors, even in LEAN mode. parameters/duration were
            # previously dropped here even though the success branch below
            # includes them — the exact operation args and how long it ran
            # before failing are diagnostically useful for a crash/timeout,
            # not just for a success.
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "operation": operation,
                "parameters": self._serialize_params(parameters),
                "duration_seconds": duration,
                "success": False,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            }
            self.logger.error(f"Operation FAILED: {operation}")
            self.logger.error(f"Error: {error}")
            self.logger.debug(f"Traceback: {traceback.format_exc()}")

            # Write to JSON log file
            json_log_file = self.log_dir / f"operations_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                # Same fixed-path symlink risk as log_file above -- see
                # that call site's comment.
                tmp_safety.refuse_if_symlink(str(json_log_file))
                with open(json_log_file, 'a') as f:
                    f.write(json.dumps(log_entry) + '\n')
            except Exception as e:
                # The text logger calls just above only recorded operation
                # name/error/traceback -- not parameters or
                # duration_seconds, which live only in log_entry (unlike
                # the verbose-success branch below, which already dumps
                # log_entry via logger.debug before its own write attempt).
                # If the JSON write then fails too, those two fields were
                # previously lost with no other record at all.
                self.logger.warning(f"Failed to write JSON log: {e}")
                self.logger.warning(f"Lost JSON log entry, dumping here instead: {json.dumps(log_entry, default=str)}")
        
        elif self.lean_logging and "START" not in operation and "QUEUE" not in operation:
            # In LEAN mode, only log DONE/RESULT/TIMEOUT operations, skip START/QUEUE
            # Skip the verbose JSON dump entirely
            self.logger.info(f"Op: {operation}")
            
        elif not self.lean_logging:
            # VERBOSE mode: full logging
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "operation": operation,
                "parameters": self._serialize_params(parameters),
                "duration_seconds": duration,
                "success": True,
            }
            
            if result is not None:
                log_entry["result"] = self._serialize_result(result)
            
            self.logger.info(f"Operation SUCCESS: {operation}")
            if duration:
                self.logger.debug(f"Duration: {duration:.3f}s")
            self.logger.debug(f"Full details: {json.dumps(log_entry, indent=2)}")
            
            # Save to JSON log file (only in verbose mode)
            json_log_file = self.log_dir / f"operations_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                # Same fixed-path symlink risk as log_file above -- see
                # that call site's comment.
                tmp_safety.refuse_if_symlink(str(json_log_file))
                with open(json_log_file, 'a') as f:
                    f.write(json.dumps(log_entry) + '\n')
            except Exception as e:
                self.logger.warning(f"Failed to write JSON log: {e}")
    
    # Fallback str() of a non-JSON-serializable value (a FreeCAD object
    # reference, a large nested structure) previously had no length cap --
    # a single oversized value could balloon a log entry unboundedly, with
    # no signal to a reader that the string was verbatim rather than
    # truncated. Matches crash_watcher.py's truncation-with-suffix pattern.
    _MAX_SERIALIZED_STR_LEN = 2000
    _SERIALIZED_TRUNCATION_SUFFIX = " …[truncated]"

    @classmethod
    def _str_with_cap(cls, value: Any) -> str:
        s = _redact_secrets(str(value))
        if len(s) > cls._MAX_SERIALIZED_STR_LEN:
            keep = cls._MAX_SERIALIZED_STR_LEN - len(cls._SERIALIZED_TRUNCATION_SUFFIX)
            s = s[:keep] + cls._SERIALIZED_TRUNCATION_SUFFIX
        return s

    def _serialize_params(self, params: Optional[Dict]) -> Optional[Dict]:
        """Serialize parameters for logging."""
        if params is None:
            return None

        serialized = {}
        for key, value in params.items():
            try:
                json.dumps(value)
                serialized[key] = _redact_value(value)
            except (TypeError, ValueError):
                serialized[key] = self._str_with_cap(value)

        return serialized

    def _serialize_result(self, result: Any) -> Any:
        """Serialize result for logging."""
        if result is None:
            return None

        try:
            json.dumps(result)
            return _redact_value(result)
        except (TypeError, ValueError):
            return self._str_with_cap(result)
    
    # Above this many objects, capture_freecad_state stops per-object detail
    # and only reports names — matches document_ops.list_objects's cap so a
    # DXF-import-sized document doesn't make a crash snapshot itself hang or
    # balloon in size.
    _MAX_DETAILED_OBJECTS = 500

    def capture_freecad_state(self) -> Dict:
        """
        Capture current FreeCAD document state.

        Returns:
            Dictionary containing document state information
        """
        try:
            import FreeCAD as App

            state = {
                "timestamp": datetime.now().isoformat(),
                "has_active_document": App.ActiveDocument is not None,
            }

            if App.ActiveDocument:
                doc = App.ActiveDocument
                objects = doc.Objects
                truncated = len(objects) > self._MAX_DETAILED_OBJECTS
                detailed = objects[:self._MAX_DETAILED_OBJECTS] if truncated else objects
                state.update({
                    "document_name": doc.Name,
                    "document_label": doc.Label,
                    "document_filename": getattr(doc, "FileName", "") or None,
                    "object_count": len(objects),
                    "objects_truncated": truncated,
                    "objects": [self._capture_object_state(obj) for obj in detailed],
                })

            self.last_freecad_state = state
            return state

        except Exception as e:
            self.logger.warning(f"Failed to capture FreeCAD state: {e}")
            return {"error": str(e)}

    @staticmethod
    def _capture_object_state(obj) -> Dict:
        """Capture enough about one object to diagnose a geometry-related
        crash: name/type/label (as before), plus Placement, Shape
        validity/bbox, State flags, and simple scalar PropertiesList values
        (the parametric dimensions — Box.Length, Fillet.Radius, etc — that
        would actually let someone reproduce the crash). Previously only
        name/type/label were captured; every crash log in this system
        recorded object *names* with none of the geometric state needed to
        diagnose a geometry-related crash.

        Iterates PropertiesList generically (no hardcoded per-TypeId field
        list) so this stays correct as new object types are added. Each
        section is independently try/except'd — this runs from inside a
        crash-diagnosis path, so one malformed object/property must not
        abort the whole capture or mask the original failure.
        """
        info = {
            "name": getattr(obj, "Name", "?"),
            "type": getattr(obj, "TypeId", "?"),
            "label": getattr(obj, "Label", "?"),
        }

        # Each section logs on failure instead of a bare `pass` -- this is
        # exactly the crash-diagnosis path where a silently-vanished
        # Shape.isValid()/BoundBox (the single most load-bearing fact for a
        # geometry crash) does the most damage: it disappears precisely when
        # OCCT internals are most likely to be corrupted, with no signal
        # that anything was even attempted. Still caught, not re-raised --
        # one malformed object/property must not abort the whole capture.
        _log = logging.getLogger("FreeCAD_MCP")

        try:
            state_flags = getattr(obj, "State", None)
            if state_flags:
                info["state"] = list(state_flags)
        except Exception as e:
            _log.warning(f"_capture_object_state({info['name']}): failed to capture State: {e}")

        try:
            placement = getattr(obj, "Placement", None)
            if placement is not None:
                info["placement"] = {
                    "position": [placement.Base.x, placement.Base.y, placement.Base.z],
                    "rotation_axis": [
                        placement.Rotation.Axis.x,
                        placement.Rotation.Axis.y,
                        placement.Rotation.Axis.z,
                    ],
                    "rotation_angle": placement.Rotation.Angle,
                }
        except Exception as e:
            _log.warning(f"_capture_object_state({info['name']}): failed to capture Placement: {e}")

        try:
            shape = getattr(obj, "Shape", None)
            if shape is not None:
                is_null = shape.isNull()
                shape_info = {"is_null": is_null}
                if not is_null:
                    shape_info["is_valid"] = shape.isValid()
                    bb = shape.BoundBox
                    shape_info["bbox"] = [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]
                info["shape"] = shape_info
        except Exception as e:
            _log.warning(f"_capture_object_state({info['name']}): failed to capture Shape: {e}")

        try:
            props = {}
            skipped = []
            for prop_name in obj.PropertiesList:
                if prop_name in ("Shape", "Placement", "State"):
                    continue  # captured separately above
                try:
                    val = getattr(obj, prop_name)
                except Exception:
                    skipped.append(prop_name)
                    continue
                if isinstance(val, (int, float, str, bool)) or val is None:
                    props[prop_name] = val
                elif hasattr(val, "Value"):  # FreeCAD Quantity (Length, Angle, ...)
                    # hasattr(val, "Value") alone doesn't prove val is a real
                    # Quantity -- anything with a .Value attribute matches,
                    # including a MagicMock (whose .Value is another
                    # MagicMock). Validate the resolved value is actually a
                    # plain scalar before trusting it; only capture that.
                    try:
                        resolved = val.Value
                    except Exception:
                        skipped.append(prop_name)
                        continue
                    if isinstance(resolved, (int, float)):
                        props[prop_name] = resolved
                    else:
                        skipped.append(prop_name)
                else:
                    # A real, non-scalar property (list/link/matrix/...)
                    # that this generic scalar-only capture doesn't
                    # attempt to represent -- previously silently dropped
                    # with no signal at all that it existed.
                    skipped.append(prop_name)
            if props:
                info["properties"] = props
            if skipped:
                info["properties_skipped"] = skipped
        except Exception:
            pass

        return info
    
    def log_state_change(self, operation: str):
        """Log state before operation (returns state for comparison)."""
        if not self.lean_logging:
            before_state = self.capture_freecad_state()
            self.logger.debug(f"State BEFORE {operation}:")
            self.logger.debug(json.dumps(before_state, indent=2))
            return before_state
        return None
    
    def compare_states(self, before_state: Optional[Dict], operation: str):
        """Compare state before/after operation."""
        if before_state is None or self.lean_logging:
            return
        
        after_state = self.capture_freecad_state()
        self.logger.debug(f"State AFTER {operation}:")
        self.logger.debug(json.dumps(after_state, indent=2))
        
        # Detect changes
        changes = []
        
        if before_state.get("object_count") != after_state.get("object_count"):
            changes.append(
                f"Object count: {before_state.get('object_count')} -> {after_state.get('object_count')}"
            )
        
        if changes:
            self.logger.info(f"State changes detected after {operation}:")
            for change in changes:
                self.logger.info(f"  - {change}")
    
    def track_performance(self, operation: str, duration: float):
        """Track operation performance over time."""
        if operation not in self.operation_times:
            self.operation_times[operation] = []
        
        self.operation_times[operation].append(duration)
        
        # Keep only last 100 measurements
        if len(self.operation_times[operation]) > 100:
            self.operation_times[operation] = self.operation_times[operation][-100:]
        
        # Only log stats in verbose mode
        if not self.lean_logging:
            times = self.operation_times[operation]
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
            
            self.logger.debug(
                f"Performance stats for {operation}: "
                f"avg={avg_time:.3f}s, min={min_time:.3f}s, max={max_time:.3f}s, "
                f"samples={len(times)}"
            )
    
    def debug_decorator(self, track_state: bool = False, track_performance: bool = False):
        """
        Decorator for automatic debug logging of functions.
        
        Args:
            track_state: Whether to capture FreeCAD state before/after (disabled by default in lean mode)
            track_performance: Whether to track operation timing (disabled by default in lean mode)
        
        Usage:
            @debugger.debug_decorator()
            def my_freecad_operation(param1, param2):
                # ... operation code ...
                return result
        """
        # In LEAN mode, disable detailed tracking by default
        if self.lean_logging:
            track_state = False
            track_performance = False
        
        def decorator(func: Callable) -> Callable:
            # Computed once at decoration time, not per-call -- a
            # function's signature is static, and the wrapped function
            # here (send_to_freecad) is called on every single MCP tool
            # dispatch on the bridge side.
            sig = inspect.signature(func)

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                operation = func.__name__

                # sig.bind()/apply_defaults() previously ran unconditionally
                # even though `parameters` is immediately discarded below
                # whenever lean_logging is True (the default) -- skip the
                # work entirely in that case instead of computing and
                # throwing it away on every call.
                if self.lean_logging:
                    parameters = None
                else:
                    bound_args = sig.bind(*args, **kwargs)
                    bound_args.apply_defaults()
                    parameters = dict(bound_args.arguments)
                
                if not self.lean_logging:
                    self.logger.info(f"Starting operation: {operation}")
                    self.logger.debug(f"Parameters: {parameters}")
                
                # Capture state before
                before_state = None
                if track_state:
                    before_state = self.log_state_change(operation)
                
                # Execute operation with timing
                start_time = time.time()
                error = None
                result = None
                
                try:
                    result = func(*args, **kwargs)
                    return result
                    
                except Exception as e:
                    error = e
                    self.logger.error(f"Exception in {operation}:", exc_info=True)
                    raise
                    
                finally:
                    duration = time.time() - start_time
                    
                    # Log operation
                    self.log_operation(
                        operation=operation,
                        parameters=parameters,
                        result=result,
                        error=error,
                        duration=duration,
                    )
                    
                    # Track performance
                    if track_performance and error is None:
                        self.track_performance(operation, duration)
                    
                    # Compare state after
                    if track_state and before_state:
                        self.compare_states(before_state, operation)
            
            return wrapper
        return decorator
    
    def get_performance_report(self) -> str:
        """Generate a performance report for all tracked operations."""
        if not self.operation_times:
            return "No performance data available"
        
        report = ["Performance Report", "=" * 80]
        
        for operation, times in sorted(self.operation_times.items()):
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
            
            report.append(f"\n{operation}:")
            report.append(f"  Samples: {len(times)}")
            report.append(f"  Average: {avg_time:.3f}s")
            report.append(f"  Min: {min_time:.3f}s")
            report.append(f"  Max: {max_time:.3f}s")
        
        return "\n".join(report)

    def export_debug_package(self) -> str:
        """Write the performance report to a timestamped file under
        log_dir, returning its path.

        Previously called from freecad_mcp_server.py's shutdown path but
        never implemented (AttributeError, silently caught by the
        enclosing try/except) -- confirmed independently by 3 review
        passes. Minimal real implementation: the "package" is the
        performance report, the one piece of debug_decorator-tracked data
        this class accumulates that isn't already written somewhere else
        (log_operation's JSON log covers individual operations;
        get_performance_report's aggregate summary had no file of its own).
        """
        pkg_file = self.log_dir / f"debug_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        tmp_safety.refuse_if_symlink(str(pkg_file))
        with open(pkg_file, 'w') as f:
            f.write(self.get_performance_report())
        return str(pkg_file)


# Global debugger instance
_debugger: Optional[FreeCADDebugger] = None


def get_debugger() -> FreeCADDebugger:
    """Get or create the global debugger instance."""
    global _debugger
    if _debugger is None:
        _debugger = FreeCADDebugger(lean_logging=LEAN_LOGGING)
    return _debugger


def init_debugger(**kwargs) -> FreeCADDebugger:
    """Initialize the global debugger with custom settings."""
    global _debugger
    if 'lean_logging' not in kwargs:
        kwargs['lean_logging'] = LEAN_LOGGING
    _debugger = FreeCADDebugger(**kwargs)
    return _debugger


# Convenience functions
def log_operation(*args, **kwargs):
    """Log an operation using the global debugger."""
    get_debugger().log_operation(*args, **kwargs)


def debug_decorator(*args, **kwargs):
    """Debug decorator using the global debugger."""
    return get_debugger().debug_decorator(*args, **kwargs)


def capture_state():
    """Capture FreeCAD state using the global debugger."""
    return get_debugger().capture_freecad_state()


def performance_report():
    """Get performance report from the global debugger."""
    return get_debugger().get_performance_report()


if __name__ == "__main__":
    # Demo usage
    print("\n=== Testing LEAN mode ===")
    debugger_lean = FreeCADDebugger(level=logging.DEBUG, lean_logging=True)
    debugger_lean.log_operation("test_op_start")
    debugger_lean.log_operation("test_op_done", result="Success")
    
    print("\n=== Testing VERBOSE mode ===")
    debugger_verbose = FreeCADDebugger(level=logging.DEBUG, lean_logging=False)
    debugger_verbose.log_operation("test_op_start")
    debugger_verbose.log_operation("test_op_done", result="Success", duration=0.125)

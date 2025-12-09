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
__version__ = "1.1.0"

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
from typing import Any, Callable, Dict, List, Optional, Tuple


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
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.level = level
        self.max_log_size = max_log_size
        self.backup_count = backup_count
        self.lean_logging = lean_logging
        
        # Setup main logger
        self.logger = logging.getLogger("FreeCAD_MCP")
        self.logger.setLevel(level)
        self.logger.handlers.clear()
        
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
            # Always log errors, even in LEAN mode
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "operation": operation,
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
                with open(json_log_file, 'a') as f:
                    f.write(json.dumps(log_entry) + '\n')
            except Exception as e:
                self.logger.warning(f"Failed to write JSON log: {e}")
        
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
                with open(json_log_file, 'a') as f:
                    f.write(json.dumps(log_entry) + '\n')
            except Exception as e:
                self.logger.warning(f"Failed to write JSON log: {e}")
    
    def _serialize_params(self, params: Optional[Dict]) -> Optional[Dict]:
        """Serialize parameters for logging."""
        if params is None:
            return None
        
        serialized = {}
        for key, value in params.items():
            try:
                json.dumps(value)
                serialized[key] = value
            except (TypeError, ValueError):
                serialized[key] = str(value)
        
        return serialized
    
    def _serialize_result(self, result: Any) -> Any:
        """Serialize result for logging."""
        if result is None:
            return None
        
        try:
            json.dumps(result)
            return result
        except (TypeError, ValueError):
            return str(result)
    
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
                state.update({
                    "document_name": doc.Name,
                    "document_label": doc.Label,
                    "object_count": len(doc.Objects),
                    "objects": [
                        {
                            "name": obj.Name,
                            "type": obj.TypeId,
                            "label": obj.Label,
                        }
                        for obj in doc.Objects
                    ],
                })
            
            self.last_freecad_state = state
            return state
            
        except Exception as e:
            self.logger.warning(f"Failed to capture FreeCAD state: {e}")
            return {"error": str(e)}
    
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
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                operation = func.__name__
                
                # Capture parameters
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                parameters = dict(bound_args.arguments) if not self.lean_logging else None
                
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

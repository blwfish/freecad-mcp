#!/usr/bin/env python3
"""
FreeCAD MCP Debugging Infrastructure
====================================

Comprehensive debugging, logging, and crash recovery for FreeCAD MCP operations.

Features:
- Detailed operation logging with timestamps
- Full exception tracking with stack traces
- Performance monitoring and timing
- FreeCAD state snapshots before/after operations
- Automatic crash detection and recovery
- Rolling log files with rotation
- Configurable verbosity levels

Author: Brian (with Claude)
Version: 1.0.0
"""

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


class FreeCADDebugger:
    """Comprehensive debugging for FreeCAD MCP operations."""
    
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
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.level = level
        self.max_log_size = max_log_size
        self.backup_count = backup_count
        
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
                datefmt='%Y-%m-%d %H:%M:%S.%f'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
        
        # Performance tracking
        self.operation_times: Dict[str, List[float]] = {}
        
        # State tracking
        self.last_freecad_state: Optional[Dict] = None
        
        self.logger.info("="*80)
        self.logger.info("FreeCAD MCP Debugger initialized")
        self.logger.info(f"Log directory: {self.log_dir}")
        self.logger.info(f"Log level: {logging.getLevelName(level)}")
        self.logger.info("="*80)
    
    def log_operation(
        self,
        operation: str,
        parameters: Optional[Dict] = None,
        result: Optional[Any] = None,
        error: Optional[Exception] = None,
        duration: Optional[float] = None,
    ):
        """
        Log a FreeCAD operation with full details.
        
        Args:
            operation: Name of the operation
            parameters: Operation parameters
            result: Operation result
            error: Exception if operation failed
            duration: Operation duration in seconds
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "parameters": self._serialize_params(parameters),
            "duration_seconds": duration,
            "success": error is None,
        }
        
        if error:
            log_entry["error"] = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
            self.logger.error(f"Operation FAILED: {operation}")
            self.logger.error(f"Error: {error}")
            self.logger.debug(f"Full details: {json.dumps(log_entry, indent=2)}")
        else:
            log_entry["result"] = self._serialize_result(result)
            self.logger.info(f"Operation SUCCESS: {operation}")
            if duration:
                self.logger.debug(f"Duration: {duration:.3f}s")
            self.logger.debug(f"Full details: {json.dumps(log_entry, indent=2)}")
        
        # Save to JSON log file
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
                # Try to convert to JSON-serializable format
                json.dumps(value)
                serialized[key] = value
            except (TypeError, ValueError):
                # If not serializable, convert to string
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
        """Log FreeCAD state before and after an operation."""
        before_state = self.capture_freecad_state()
        self.logger.debug(f"State BEFORE {operation}:")
        self.logger.debug(json.dumps(before_state, indent=2))
        
        return before_state
    
    def compare_states(self, before_state: Dict, operation: str):
        """Compare and log state changes."""
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
        
        # Calculate statistics
        times = self.operation_times[operation]
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        
        self.logger.debug(
            f"Performance stats for {operation}: "
            f"avg={avg_time:.3f}s, min={min_time:.3f}s, max={max_time:.3f}s, "
            f"samples={len(times)}"
        )
    
    def debug_decorator(self, track_state: bool = True, track_performance: bool = True):
        """
        Decorator for automatic debug logging of functions.
        
        Args:
            track_state: Whether to capture FreeCAD state before/after
            track_performance: Whether to track operation timing
        
        Usage:
            @debugger.debug_decorator()
            def my_freecad_operation(param1, param2):
                # ... operation code ...
                return result
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                operation = func.__name__
                
                # Capture parameters
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                parameters = dict(bound_args.arguments)
                
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
    
    def export_debug_package(self, output_file: Optional[str] = None) -> str:
        """
        Export all debug information to a zip file for analysis.
        
        Args:
            output_file: Output zip file path (optional)
        
        Returns:
            Path to the exported zip file
        """
        import zipfile
        
        if output_file is None:
            output_file = self.log_dir / f"debug_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        output_file = Path(output_file)
        
        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add all log files
            for log_file in self.log_dir.glob("*.log*"):
                zipf.write(log_file, log_file.name)
            
            # Add all JSON log files
            for json_file in self.log_dir.glob("*.json"):
                zipf.write(json_file, json_file.name)
            
            # Add performance report
            report_file = self.log_dir / "performance_report.txt"
            with open(report_file, 'w') as f:
                f.write(self.get_performance_report())
            zipf.write(report_file, report_file.name)
            report_file.unlink()
        
        self.logger.info(f"Debug package exported to: {output_file}")
        return str(output_file)


# Global debugger instance
_debugger: Optional[FreeCADDebugger] = None


def get_debugger() -> FreeCADDebugger:
    """Get or create the global debugger instance."""
    global _debugger
    if _debugger is None:
        _debugger = FreeCADDebugger()
    return _debugger


def init_debugger(**kwargs) -> FreeCADDebugger:
    """Initialize the global debugger with custom settings."""
    global _debugger
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


def export_debug_package(output_file: Optional[str] = None):
    """Export debug package from the global debugger."""
    return get_debugger().export_debug_package(output_file)


if __name__ == "__main__":
    # Demo usage
    debugger = FreeCADDebugger(level=logging.DEBUG)
    
    @debugger.debug_decorator()
    def example_operation(param1: int, param2: str) -> str:
        """Example operation for testing."""
        time.sleep(0.1)  # Simulate work
        return f"Result: {param1} - {param2}"
    
    # Test the decorator
    result = example_operation(42, "test")
    print(f"\nOperation result: {result}")
    
    # Show performance report
    print("\n" + debugger.get_performance_report())
    
    # Export debug package
    package_path = debugger.export_debug_package()
    print(f"\nDebug package: {package_path}")

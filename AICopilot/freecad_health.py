#!/usr/bin/env python3
"""
FreeCAD Crash Recovery and Health Monitoring - OPTIMIZED
========================================================

Automatic crash detection, recovery, and health monitoring for FreeCAD MCP.

Optimized version reduces logging overhead with lean mode.

Features:
- Automatic crash detection via heartbeat monitoring
- Graceful restart with state preservation
- Health checks and status monitoring
- Crash history tracking and analysis
- Automatic recovery strategies
- Socket cleanup and management

Author: Brian (with Claude)
Version: 1.0.1 (Optimized)
"""

# Version declaration
__version__ = "1.0.1"

# Try to register with version system if available
try:
    from mcp_versions import register_component
    from datetime import datetime as _dt
    register_component("freecad_health", __version__, _dt.now().isoformat())
except ImportError:
    # Version system not available, continue without it
    pass

import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from freecad_debug import get_debugger


# Lean logging mode - set to False for verbose health monitoring output
LEAN_LOGGING = True


class FreeCADHealthMonitor:
    """Monitor FreeCAD health and handle crash recovery."""
    
    def __init__(
        self,
        socket_path: str = "/tmp/freecad_mcp.sock",
        heartbeat_interval: float = 5.0,
        crash_log_dir: str = "/tmp/freecad_mcp_crashes",
        max_restart_attempts: int = 3,
        restart_cooldown: float = 10.0,
        lean_logging: bool = True,
    ):
        """
        Initialize the health monitor.
        
        Args:
            socket_path: Path to the MCP socket
            heartbeat_interval: Interval between health checks (seconds)
            crash_log_dir: Directory for crash logs
            max_restart_attempts: Maximum consecutive restart attempts
            restart_cooldown: Time to wait between restart attempts (seconds)
            lean_logging: If True, use compact logging; if False, verbose JSON dumps
        """
        self.socket_path = Path(socket_path)
        self.heartbeat_interval = heartbeat_interval
        self.crash_log_dir = Path(crash_log_dir)
        self.crash_log_dir.mkdir(parents=True, exist_ok=True)
        self.max_restart_attempts = max_restart_attempts
        self.restart_cooldown = restart_cooldown
        self.lean_logging = lean_logging
        
        self.debugger = get_debugger()
        self.logger = self.debugger.logger
        
        # State tracking
        self.is_healthy = False
        self.last_heartbeat = None
        self.consecutive_failures = 0
        self.restart_attempts = 0
        self.crash_history: List[Dict] = []
        self.freecad_pid: Optional[int] = None
        
        mode = "LEAN" if lean_logging else "VERBOSE"
        self.logger.info(f"FreeCAD Health Monitor initialized (MODE: {mode})")
    
    def check_socket_exists(self) -> bool:
        """Check if the MCP socket file exists."""
        exists = self.socket_path.exists()
        if not self.lean_logging:
            self.logger.debug(f"Socket exists: {exists} ({self.socket_path})")
        return exists
    
    def check_socket_responsive(self, timeout: float = 2.0) -> Tuple[bool, Optional[str]]:
        """
        Check if the MCP socket is responsive.
        
        Args:
            timeout: Socket connection timeout in seconds
        
        Returns:
            Tuple of (is_responsive, error_message)
        """
        if not self.check_socket_exists():
            return False, "Socket file does not exist"
        
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            
            try:
                sock.connect(str(self.socket_path))
                # Try a simple ping/echo test
                test_msg = json.dumps({"method": "test_echo", "params": {"message": "ping"}})
                sock.sendall(test_msg.encode() + b'\n')
                
                # Wait for response
                response = sock.recv(4096)
                if response:
                    if not self.lean_logging:
                        self.logger.debug("Socket is responsive")
                    return True, None
                else:
                    return False, "Socket connected but no response"
                    
            except socket.timeout:
                return False, "Socket connection timeout"
            except ConnectionRefusedError:
                return False, "Connection refused"
            finally:
                sock.close()
                
        except Exception as e:
            return False, f"Socket check failed: {e}"
    
    def check_freecad_process(self) -> Tuple[bool, Optional[int]]:
        """
        Check if FreeCAD process is running.
        
        Returns:
            Tuple of (is_running, pid)
        """
        try:
            result = subprocess.run(
                ["pgrep", "-f", "freecad"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                pids = [int(pid) for pid in result.stdout.strip().split('\n')]
                if pids:
                    self.freecad_pid = pids[0]
                    if not self.lean_logging:
                        self.logger.debug(f"FreeCAD process found: PID {self.freecad_pid}")
                    return True, self.freecad_pid
            
            if not self.lean_logging:
                self.logger.debug("No FreeCAD process found")
            return False, None
            
        except Exception as e:
            self.logger.warning(f"Failed to check FreeCAD process: {e}")
            return False, None
    
    def perform_health_check(self) -> Dict:
        """
        Perform comprehensive health check.
        
        Returns:
            Dictionary containing health status
        """
        health_status = {
            "timestamp": datetime.now().isoformat(),
            "socket_exists": False,
            "socket_responsive": False,
            "process_running": False,
            "freecad_pid": None,
            "is_healthy": False,
            "error": None,
        }
        
        # Check socket exists
        health_status["socket_exists"] = self.check_socket_exists()
        
        # Check socket responsive
        if health_status["socket_exists"]:
            responsive, error = self.check_socket_responsive()
            health_status["socket_responsive"] = responsive
            if error:
                health_status["error"] = error
        
        # Check process running
        process_running, pid = self.check_freecad_process()
        health_status["process_running"] = process_running
        health_status["freecad_pid"] = pid
        
        # Overall health
        health_status["is_healthy"] = (
            health_status["socket_responsive"] and
            health_status["process_running"]
        )
        
        self.is_healthy = health_status["is_healthy"]
        
        if self.is_healthy:
            self.last_heartbeat = datetime.now()
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        
        # Lean logging: compact format
        if self.lean_logging:
            status_char = "✓" if health_status["is_healthy"] else "✗"
            self.logger.debug(
                f"Health: {status_char} socket={health_status['socket_responsive']} "
                f"proc={health_status['process_running']} "
                f"pid={health_status['freecad_pid']}"
            )
        else:
            # Verbose logging: full JSON
            self.logger.debug(f"Health check: {json.dumps(health_status, indent=2)}")
        
        return health_status
    
    def log_crash(self, health_status: Dict, additional_info: Optional[Dict] = None):
        """
        Log a crash event with full details.
        
        Args:
            health_status: Current health status
            additional_info: Additional crash information
        """
        crash_info = {
            "timestamp": datetime.now().isoformat(),
            "health_status": health_status,
            "consecutive_failures": self.consecutive_failures,
            "restart_attempts": self.restart_attempts,
            "additional_info": additional_info or {},
        }
        
        # Capture FreeCAD state if possible (always capture on crash, regardless of lean mode)
        try:
            crash_info["freecad_state"] = self.debugger.capture_freecad_state()
        except Exception as e:
            crash_info["freecad_state"] = {"error": str(e)}
        
        # Save crash log
        crash_file = self.crash_log_dir / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(crash_file, 'w') as f:
            json.dump(crash_info, f, indent=2)
        
        # Add to crash history
        self.crash_history.append(crash_info)
        
        self.logger.error(f"CRASH DETECTED - Log saved to: {crash_file}")
        # Always log crash status, but compactly
        self.logger.error(
            f"Socket: {health_status.get('socket_responsive', False)}, "
            f"Process: {health_status.get('process_running', False)}, "
            f"Failures: {self.consecutive_failures}"
        )
    
    def cleanup_socket(self):
        """Clean up stale socket file."""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
                self.logger.info(f"Cleaned up socket: {self.socket_path}")
            except Exception as e:
                self.logger.warning(f"Failed to clean up socket: {e}")
    
    def attempt_restart(self) -> bool:
        """
        Attempt to restart FreeCAD.
        
        Returns:
            True if restart was successful
        """
        if self.restart_attempts >= self.max_restart_attempts:
            self.logger.error(
                f"Maximum restart attempts ({self.max_restart_attempts}) reached. "
                "Manual intervention required."
            )
            return False
        
        self.restart_attempts += 1
        self.logger.info(f"Attempting restart #{self.restart_attempts}...")
        
        # Kill existing process if found
        is_running, pid = self.check_freecad_process()
        if is_running and pid:
            self.logger.info(f"Killing existing FreeCAD process: PID {pid}")
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)
                # Force kill if still running
                if self.check_freecad_process()[0]:
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(1)
            except Exception as e:
                self.logger.warning(f"Failed to kill process: {e}")
        
        # Clean up socket
        self.cleanup_socket()
        
        # Wait for cooldown
        self.logger.info(f"Waiting {self.restart_cooldown}s before restart...")
        time.sleep(self.restart_cooldown)
        
        # TODO: Add actual FreeCAD restart logic here
        # This would depend on how FreeCAD is launched with MCP
        self.logger.info("Restart logic would execute here")
        self.logger.info("You may need to manually restart FreeCAD")
        
        return False  # Return True when actual restart is implemented
    
    def get_crash_statistics(self) -> Dict:
        """Get statistics about crash history."""
        if not self.crash_history:
            return {"total_crashes": 0, "message": "No crashes recorded"}
        
        stats = {
            "total_crashes": len(self.crash_history),
            "first_crash": self.crash_history[0]["timestamp"],
            "last_crash": self.crash_history[-1]["timestamp"],
            "restart_attempts": self.restart_attempts,
            "max_consecutive_failures": max(
                c["consecutive_failures"] for c in self.crash_history
            ),
        }
        
        return stats
    
    def export_crash_report(self, output_file: Optional[str] = None) -> str:
        """
        Export comprehensive crash report.
        
        Args:
            output_file: Output file path (optional)
        
        Returns:
            Path to the crash report
        """
        if output_file is None:
            output_file = self.crash_log_dir / f"crash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_file = Path(output_file)
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "statistics": self.get_crash_statistics(),
            "crash_history": self.crash_history,
            "current_health": self.perform_health_check(),
        }
        
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        self.logger.info(f"Crash report exported to: {output_file}")
        return str(output_file)


# Global monitor instance
_monitor: Optional[FreeCADHealthMonitor] = None


def get_monitor() -> FreeCADHealthMonitor:
    """Get or create the global health monitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = FreeCADHealthMonitor(lean_logging=LEAN_LOGGING)
    return _monitor


def init_monitor(**kwargs) -> FreeCADHealthMonitor:
    """Initialize the global monitor with custom settings."""
    global _monitor
    if 'lean_logging' not in kwargs:
        kwargs['lean_logging'] = LEAN_LOGGING
    _monitor = FreeCADHealthMonitor(**kwargs)
    return _monitor


# Convenience functions
def health_check():
    """Perform health check using the global monitor."""
    return get_monitor().perform_health_check()


def crash_statistics():
    """Get crash statistics from the global monitor."""
    return get_monitor().get_crash_statistics()


def export_crash_report(output_file: Optional[str] = None):
    """Export crash report from the global monitor."""
    return get_monitor().export_crash_report(output_file)


if __name__ == "__main__":
    # Demo usage
    monitor = FreeCADHealthMonitor()
    
    print("Performing health check...")
    status = monitor.perform_health_check()
    print(json.dumps(status, indent=2))
    
    if not status["is_healthy"]:
        print("\nFreeCAD appears to be unhealthy")
        print("Attempting recovery...")
        # monitor.attempt_restart()

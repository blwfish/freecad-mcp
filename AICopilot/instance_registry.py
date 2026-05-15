"""Instance discovery registry for FreeCAD MCP.

Each AICopilot instance (GUI or headless) writes a JSON discovery file at
startup and removes it at shutdown. The bridge scans this directory to find
all live FreeCAD instances, regardless of who launched them.

Both AICopilot side (this module) and bridge side (freecad_mcp_server.py)
must agree on:
  - DISCOVERY_DIR location
  - JSON schema (see write_discovery)
"""

__version__ = "1.0.0"

import json
import os
import socket
import time
import uuid

DISCOVERY_DIR = os.path.expanduser("~/.cache/freecad-mcp/instances")


def ensure_dir() -> str:
    os.makedirs(DISCOVERY_DIR, mode=0o700, exist_ok=True)
    return DISCOVERY_DIR


def generate_uuid() -> str:
    """Short hex UUID used as the instance identifier."""
    return uuid.uuid4().hex[:12]


def discovery_path(instance_uuid: str) -> str:
    return os.path.join(DISCOVERY_DIR, f"{instance_uuid}.json")


def default_socket_path(instance_uuid: str) -> str:
    return f"/tmp/freecad_mcp_{instance_uuid}.sock"


def write_discovery(
    instance_uuid: str,
    socket_path: str,
    *,
    gui: bool,
    label: str | None = None,
    freecad_version: str | None = None,
    freecad_binary: str | None = None,
) -> str:
    """Atomically write the discovery file for this instance.

    Returns the absolute path of the written file.
    """
    ensure_dir()
    data = {
        "uuid": instance_uuid,
        "pid": os.getpid(),
        "socket_path": socket_path,
        "gui": gui,
        "label": label or instance_uuid,
        "started_at": time.time(),
        "freecad_version": freecad_version,
        "freecad_binary": freecad_binary,
    }
    path = discovery_path(instance_uuid)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def remove_discovery(instance_uuid: str) -> None:
    """Remove this instance's discovery file. Safe to call multiple times."""
    try:
        os.unlink(discovery_path(instance_uuid))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_socket_alive(socket_path: str, timeout: float = 0.5) -> bool:
    """Return True if a Unix socket at socket_path accepts connections."""
    if not os.path.exists(socket_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.close()
        return True
    except OSError:
        return False


def scan_discovery(prune_stale: bool = True) -> list[dict]:
    """Return list of live instance discovery records.

    A record is considered live if its socket_path is connectable. If
    prune_stale is True, records whose sockets cannot be reached are deleted
    from the discovery directory.
    """
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
            # Corrupt or unreadable — drop it
            if prune_stale:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            continue
        sock_path = data.get("socket_path")
        if sock_path and is_socket_alive(sock_path):
            live.append(data)
        elif prune_stale:
            try:
                os.unlink(path)
            except OSError:
                pass
    return live

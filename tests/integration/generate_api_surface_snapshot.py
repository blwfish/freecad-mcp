"""Golden-snapshot generator for the API-surface drift test.

Run manually (never automatically -- the golden snapshot is a checked-in
fixture reviewed like any other golden file, not silently regenerated) after
Goal 2's scope table changes or when deliberately re-baselining against a
newer FreeCAD build:

    python3 -m tests.integration.generate_api_surface_snapshot

Connects to whichever FreeCAD instance conftest.py would use for the
integration suite (an already-running GUI instance at FREECAD_MCP_SOCKET, or
a freshly spawned headless FreeCADCmd), scans AICopilot/handlers/*.py for
every addObject/newObject Type::String call site and the properties handler
code sets on each, resolves each type to a live scratch instance, and writes
the resulting {type_id: {property: property_type_id}} table to
api_surface_snapshot.json.
"""

import json
import os
import sys

# Support running as `python3 tests/integration/generate_api_surface_snapshot.py`
# in addition to `python3 -m tests.integration.generate_api_surface_snapshot` --
# the former has no package context for the relative imports below.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    __package__ = "tests.integration"

from . import conftest
from ._api_surface_remote import build_remote_scan_code
from ._api_surface_scan import scan_type_properties
from .test_e2e_workflows import send_command

SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_surface_snapshot.json")


def _ensure_connection():
    """Connect to an existing GUI instance if one's up, else spawn headless.

    Returns the spawned subprocess.Popen to tear down afterward, or None if
    we attached to an already-running instance we don't own.
    """
    if conftest._socket_responds(conftest._DEFAULT_SOCKET):
        conftest._active_socket_path = conftest._DEFAULT_SOCKET
        print(f"Using existing FreeCAD instance at {conftest._DEFAULT_SOCKET}")
        return None
    proc, sock_path = conftest._spawn_headless()
    conftest._active_socket_path = sock_path
    print(f"Spawned headless FreeCAD instance at {sock_path} (pid {proc.pid})")
    return proc


def generate_snapshot() -> dict:
    scope = scan_type_properties()
    code = build_remote_scan_code(scope)
    resp = send_command("execute_python", {"code": code}, timeout=60.0)
    if "error" in resp:
        raise RuntimeError(f"execute_python failed while scanning API surface: {resp}")
    result_str = resp.get("result", "")
    try:
        return json.loads(result_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Expected the remote scan to print exactly one JSON line; "
            f"got unparseable output: {result_str[:2000]!r}"
        ) from e


def main():
    proc = _ensure_connection()
    try:
        snapshot = generate_snapshot()
    finally:
        if proc is not None:
            conftest._stop_headless(proc, conftest._active_socket_path)
        conftest._active_socket_path = None

    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")

    errored = [t for t, props in snapshot.items() if "__ERROR__" in props]
    print(f"Wrote {len(snapshot)} type entries to {SNAPSHOT_PATH}")
    if errored:
        print(f"WARNING: {len(errored)} type(s) failed to resolve on this build: {errored}")


if __name__ == "__main__":
    main()

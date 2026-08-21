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

import datetime
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
from ._api_surface_remote import build_remote_scan_code, parse_remote_scan_result
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
    resp = send_command("execute_python_sync", {"code": code}, timeout=60.0)
    if "error" in resp:
        raise RuntimeError(f"execute_python failed while scanning API surface: {resp}")
    result_str = resp.get("result", "")
    try:
        return parse_remote_scan_result(result_str)
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

    # "__meta__" (freecad_version, from build_remote_scan_code) plus a
    # generation timestamp -- previously nothing recorded in the snapshot
    # itself said which FreeCAD build or when it was produced, so a stale
    # or partial baseline was only discoverable via git blame, not the
    # artifact itself (full-review 2026-07-24 finding #20).
    meta = snapshot.setdefault("__meta__", {})
    meta["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")

    # "__meta__" itself isn't a Type::String entry -- exclude it from both
    # the total and the error scan, and separate real captures from
    # __ERROR__/__MISSING__-only entries so "Wrote N type entries" can't be
    # read as "N types captured real data" when some are actually
    # placeholders (full-review 2026-07-24 finding #21 -- previously this
    # message counted every entry uniformly, and the __ERROR__ warning
    # fired only after the file was already written, with no signal in the
    # message itself that the count included non-data entries).
    type_entries = {t: props for t, props in snapshot.items() if t != "__meta__"}
    errored = [t for t, props in type_entries.items() if "__ERROR__" in props]
    real = len(type_entries) - len(errored)
    print(f"Wrote {len(type_entries)} type entries to {SNAPSHOT_PATH} "
          f"({real} resolved, {len(errored)} errored)")
    if errored:
        print(f"WARNING: {len(errored)} type(s) failed to resolve on this build "
              f"and were written as __ERROR__ placeholders -- these will NOT be "
              f"drift-checked until fixed and regenerated: {errored}")


if __name__ == "__main__":
    main()

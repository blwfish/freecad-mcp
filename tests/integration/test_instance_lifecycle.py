"""spawn_freecad_instance / stop_freecad_instance against a real FreeCAD.

Drives the bridge's actual MCP tools (not the handler socket directly, and not
the integration conftest's own Popen) so the real spawn -> stop path runs:
process-group signalling, the wait for BOTH the wrapper and the real FreeCAD
process, and the liveness verdict.

Regression coverage for:
  #90  stop only signalled the tracked wrapper pid, leaving the real freecadcmd
       (and its AppImage FUSE helper) running as orphans.
  #99  the liveness verdict ran the instant the wrapper exited -- before the real
       process finished unwinding -- so a SUCCESSFUL stop was reported as a failure.

Both need a wrapper -> real-process layout (a Linux AppImage's AppRun forks the
real binary). Whether the CI AppImage's wrapper forks or execs isn't something we
control, so test_forking_wrapper builds that layout explicitly and asserts it
took, instead of trusting the environment to reproduce it.

Needs the `mcp` package (the bridge imports it). The plain integration job only
installs pytest, so this module skips there; the `instance-lifecycle` job in
integration-tests.yml installs the project and runs it.
"""

import contextlib
import json
import os
import signal
import stat
import subprocess
import time

import pytest

pytest.importorskip("mcp", reason="bridge needs the mcp package; run via the instance-lifecycle job / `uv run`")

import freecad_mcp_server as bridge  # noqa: E402
from tests.integration.conftest import _find_freecadcmd, _find_headless_script  # noqa: E402
from tests.unit.test_mcp_protocol import _call_tool  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (_find_freecadcmd() and _find_headless_script()),
    reason="needs a FreeCADCmd binary and headless_server.py",
)

# stop_freecad_instance waits up to term_timeout(5s) + kill_timeout(3s); a healthy
# stop is well under a second, so anything near the ceiling is itself a failure.
STOP_BUDGET_S = 8.0


def _call(name: str, **arguments) -> dict:
    return json.loads(_call_tool(name, arguments)[0].text)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_group_members(pgid: int) -> list[str]:
    """`ps` lines for processes still alive in process group `pgid`.

    killpg(pgid, 0) isn't usable for "is the group gone?": a zombie -- exited,
    just not yet reaped by its parent -- still counts as a member, and the real
    FreeCAD process is reparented to init when its wrapper dies, so there is a
    brief window where it is dead but unreaped. Zombies are therefore excluded
    (state Z); anything left is a process that is genuinely still running.
    Portable `ps -ax` form (works on Linux and macOS), filtered here by pgid.
    """
    out = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,command="],
        capture_output=True, text=True, check=True,
    ).stdout
    members = []
    for line in out.splitlines():
        fields = line.split(None, 4)
        if len(fields) == 5 and int(fields[2]) == pgid and not fields[3].startswith("Z"):
            members.append(line.strip())
    return members


def _wait_group_gone(pgid: int, grace: float = 3.0) -> list[str]:
    """Live members of `pgid` after allowing `grace` seconds for init to reap."""
    deadline = time.monotonic() + grace
    while True:
        members = _live_group_members(pgid)
        if not members or time.monotonic() >= deadline:
            return members
        time.sleep(0.1)


def _discovery_record(instance_uuid: str) -> dict:
    with open(bridge._discovery_file_path(instance_uuid)) as f:
        return json.load(f)


def _spawn_stop_and_check(label: str, **spawn_args) -> dict:
    """Spawn, stop, and assert nothing survives. Returns facts for the caller."""
    spawned = _call("spawn_freecad_instance", label=label, select=False, **spawn_args)
    assert "error" not in spawned, spawned
    sock, pgid, instance_uuid = spawned["socket_path"], spawned["pid"], spawned["uuid"]
    assert instance_uuid, f"no discovery record found for {sock}: {spawned}"
    real_pid = _discovery_record(instance_uuid)["pid"]

    stopped = False
    try:
        assert _pid_exists(real_pid), "real FreeCAD process should be running before stop"
        assert _live_group_members(pgid), "spawned process group should have live members before stop"

        started = time.monotonic()
        result = _call("stop_freecad_instance", socket_path=sock)
        elapsed = time.monotonic() - started

        # #99: a clean stop must be reported as one, not "did not fully stop".
        assert "error" not in result, result
        assert "stopped" in result["result"], result
        assert elapsed < STOP_BUDGET_S, f"stop took {elapsed:.1f}s -- near the grace-period ceiling"
        stopped = True

        # #90: nothing left behind. The group check is the strongest one -- every
        # process spawn_freecad_instance started (wrapper, AppRun, real binary, FUSE
        # helper) lives in the group it created with start_new_session=True. The
        # real-pid check follows it but must not be skipped: it's the discovery
        # record's pid, i.e. the process the bridge itself judges "alive".
        leftovers = _wait_group_gone(pgid)
        assert not leftovers, (
            f"process group {pgid} still has live members after a successful stop:\n"
            + "\n".join(leftovers)
        )
        assert not _pid_exists(real_pid), f"real FreeCAD pid {real_pid} survived stop"
        assert not os.path.exists(sock), "socket file left behind"
        assert not os.path.exists(bridge._discovery_file_path(instance_uuid)), "discovery record left behind"
        assert sock not in bridge._ctx.instances, "instance still registered after a successful stop"
    finally:
        if not stopped:  # don't leak FreeCAD processes into later tests / the CI runner
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
            bridge._ctx.unregister(sock)
            for path in (sock, bridge._discovery_file_path(instance_uuid)):
                with contextlib.suppress(OSError):
                    os.remove(path)

    return {"spawn_pid": pgid, "real_pid": real_pid, "stop_seconds": elapsed}


def test_spawn_then_stop_reports_success_and_leaves_nothing():
    """Uses whatever FREECAD_MCP_FREECAD_BIN / PATH resolve to -- in CI, the real
    AppImage via its AppRun wrapper."""
    _spawn_stop_and_check("lifecycle-default")


def test_forking_wrapper_layout(tmp_path):
    """Force the wrapper -> real-process layout from #90/#99: a launcher that
    FORKS the real binary (no exec) and so exits before it does."""
    real_bin = _find_freecadcmd()
    wrapper = tmp_path / "forking-launcher.sh"
    # `; rc=$?; exit $rc` makes the real command a non-final statement, so bash
    # can't exec-optimise it away into the wrapper's own pid.
    wrapper.write_text(f'#!/bin/bash\n"{real_bin}" "$@"\nrc=$?\nexit $rc\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    facts = _spawn_stop_and_check("lifecycle-forking", freecad_binary=str(wrapper))

    # Guard against a vacuous pass: if the launcher exec'd, spawn pid == real pid
    # and this exercised nothing the single-process case doesn't.
    assert facts["spawn_pid"] != facts["real_pid"], (
        "wrapper and real process share a pid -- the forking layout was not "
        "reproduced, so this test proves nothing about #90/#99"
    )

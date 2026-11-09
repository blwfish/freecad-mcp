"""Windows headless smoke suite (Tier 2).

Spec: design-docs/freecad/windows-headless-testing-spec.md. T2-0 (isolation) is the session-autouse gate in
conftest.py. Tests marked ``real_windows`` need real Windows and are skipped in fake mode; everything else
exercises the handler's Windows branch (TCP listener, token, framing) and runs in either mode.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid

import pytest

from ._harness import (
    MAX_MESSAGE_SIZE,
    OMIT,
    WIN,
    FcServer,
    can_connect,
    frame,
    padded_request_body,
)

real_windows = pytest.mark.skipif(not WIN, reason="needs real Windows (Windows-only OS behavior)")
HEX64 = re.compile(r"[0-9a-f]{64}")


# --------------------------------------------------------------------------------------------------
# T2-1: token file
# --------------------------------------------------------------------------------------------------
def test_token_file_created_fresh_and_listening(fc):
    assert fc.token_existed_before_start is False, "a stale token file would let this pass vacuously"
    assert HEX64.fullmatch(fc.read_token() or ""), "token must be 64 hex chars (secrets.token_hex(32))"
    assert can_connect(fc.port)


# --------------------------------------------------------------------------------------------------
# T2-2: ACL on the token file (real Windows only)
# --------------------------------------------------------------------------------------------------
def _icacls_aces(path) -> list[tuple[str, str]]:
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    lines = [ln for ln in out.splitlines() if ln.strip()]
    first = lines[0]
    marker = "windows_auth_token"
    lines[0] = first[first.find(marker) + len(marker):] if marker in first else first
    aces = []
    for ln in lines:
        m = re.match(r"\s*(?P<acct>[^\r\n:]+?):(?P<flags>(?:\([A-Za-z0-9,]+\))+)\s*$", ln)
        if m:
            aces.append((m["acct"].strip(), m["flags"]))
    return aces


@real_windows
def test_token_file_acl_has_user_ace_and_no_broad_grants(fc):
    aces = _icacls_aces(fc.token_path)
    assert aces, "could not parse any ACE from icacls output"
    user = (os.environ.get("USERNAME") or "").lower()
    mine = [flags for acct, flags in aces if acct.lower().endswith("\\" + user)]
    assert mine, f"no ACE for {user!r} in {aces}"
    assert all("(F)" in f and "(I)" not in f for f in mine), f"user ACE must be explicit full control: {mine}"
    assert not any("(I)" in f for _a, f in aces), f"/inheritance:r left inherited ACEs: {aces}"
    broad = {"everyone", "builtin\\users", "nt authority\\authenticated users"}
    assert not [a for a, _f in aces if a.lower() in broad], f"broad grant present: {aces}"
    # NOT asserted: "exactly one ACE". SYSTEM / Administrators / OWNER RIGHTS legitimately remain
    # (explicit ACEs from the process default DACL on windows-latest). See spec section 3b.


# --------------------------------------------------------------------------------------------------
# T2-3: auth matrix
# --------------------------------------------------------------------------------------------------
BAD_TOKENS = [
    pytest.param(OMIT, id="omitted"),
    pytest.param("0" * 64, id="wrong"),
    pytest.param("", id="empty"),
    pytest.param(None, id="null"),
    pytest.param(1, id="int"),
    pytest.param(["x"], id="list"),
    pytest.param("abc", id="short"),
    pytest.param("é" * 64, id="non-ascii-same-length"),
    pytest.param("\ud800", id="lone-surrogate"),
]


@pytest.mark.parametrize("bad", BAD_TOKENS)
def test_bad_token_is_unauthorized_and_server_survives(fc, bad):
    reply = fc.request("execute_python_sync", {"code": "1+1"}, token=bad)
    assert isinstance(reply, dict) and "Unauthorized" in reply.get("error", ""), reply
    assert fc.request("execute_python_sync", {"code": "1+1"}) == {"result": "2"}


def test_unknown_top_level_key_is_rejected_before_auth(fc):
    reply = fc.request("execute_python_sync", {"code": "1"}, token=OMIT, extra={"bogus": 1})
    assert "Unrecognized request key" in reply.get("error", ""), reply  # pinned: key check precedes auth


def test_valid_token_but_no_tool_gets_no_tool_error(fc):
    reply = fc.request(None, {})
    assert "No tool specified" in reply.get("error", ""), reply


# --------------------------------------------------------------------------------------------------
# T2-4: round trip
# --------------------------------------------------------------------------------------------------
def test_round_trip_sync_async_and_poll(fc):
    name = "T" + uuid.uuid4().hex[:8]
    try:
        vol = fc.py(
            f"d=FreeCAD.newDocument({name!r}); b=d.addObject('Part::Box','B'); d.recompute(); b.Shape.Volume"
        )
        assert float(vol) == pytest.approx(1000.0)
    finally:
        fc.py(f"FreeCAD.closeDocument({name!r}) if {name!r} in FreeCAD.listDocuments() else None")
    job = fc.request("execute_python_async", {"code": "2+3"})
    assert job["status"] == "submitted"
    deadline = time.monotonic() + 30
    reply = None
    while time.monotonic() < deadline:
        reply = fc.request("poll_job", {"job_id": job["job_id"]})
        if reply.get("status") == "done":
            break
        time.sleep(0.2)
    assert reply["status"] == "done" and reply["result"] == "5", reply


# --------------------------------------------------------------------------------------------------
# T2-5: SO_EXCLUSIVEADDRUSE (real Windows only)
# --------------------------------------------------------------------------------------------------
@real_windows
def test_server_port_is_held_exclusively(fc):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError) as exc:
            s.bind(("127.0.0.1", fc.port))
        # REUSEADDR cannot override an exclusive holder: WSAEACCES 10013 (observed; default options give 10048).
        assert getattr(exc.value, "winerror", None) == 10013, exc.value
    finally:
        s.close()
    # Negative control, so the test cannot pass vacuously: the same bind on an unused port works.
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        c.bind(("127.0.0.1", 0))
    finally:
        c.close()


# --------------------------------------------------------------------------------------------------
# T2-6: token rotation across a restart (own server => own token file)
# --------------------------------------------------------------------------------------------------
def test_restart_rotates_the_token(make_server):
    s = make_server("rotate")
    s.start()
    old = s.read_token()
    assert s.token_existed_before_start is False
    s.restart()  # fresh port, same profile tree (the old process is dead by now)
    new = s.read_token()
    assert HEX64.fullmatch(new) and new != old
    assert "Unauthorized" in s.request("execute_python_sync", {"code": "1"}, token=old)["error"]
    assert s.request("execute_python_sync", {"code": "1"}, token=new) == {"result": "1"}


# --------------------------------------------------------------------------------------------------
# T2-7: pid liveness (real Windows only)
# --------------------------------------------------------------------------------------------------
def _alive():
    import instance_registry  # AICopilot dir is on sys.path via the harness
    return instance_registry._is_pid_alive_windows


@real_windows
def test_pid_alive_true_for_running_server(fc):
    assert _alive()(fc.pid) is True


@real_windows
@pytest.mark.parametrize("pid", [
    0, -1, 1, 2**31 - 1, 2**31, 2**32 - 1, True,
    pytest.param(2**32, marks=pytest.mark.xfail(
        strict=True, raises=Exception, reason="W-9b: OpenProcess overflow raises ctypes.ArgumentError")),
])
def test_pid_alive_boundaries_are_false(pid):
    assert _alive()(pid) is False


@real_windows
def test_pid_alive_false_after_exit_once_our_handle_is_closed():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=30)
    handle = getattr(p, "_handle", None)
    if handle is None:
        pytest.skip("Popen._handle unavailable on this Python")
    handle.Close()
    time.sleep(0.5)
    assert _alive()(p.pid) is False


@real_windows
@pytest.mark.xfail(strict=True, reason="W-7: reports an exited process alive while any handle to it is open")
def test_pid_alive_false_after_exit_even_with_handle_open():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=30)
    assert _alive()(p.pid) is False


# --------------------------------------------------------------------------------------------------
# T2-8: framing thresholds on the real server
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("size", [MAX_MESSAGE_SIZE - 1, MAX_MESSAGE_SIZE])
def test_request_at_and_below_max_is_served(fc, size):
    reply = fc.exchange_raw(frame(padded_request_body(size, fc.read_token())))
    assert json.loads(reply) == {"result": "1"}


def test_request_one_byte_over_max_is_dropped_and_server_survives(fc):
    body = padded_request_body(MAX_MESSAGE_SIZE + 1, fc.read_token())
    assert fc.exchange_raw(frame(body)) is None
    assert fc.request("execute_python_sync", {"code": "1"}) == {"result": "1"}


def test_zero_length_frame_gets_no_reply_W11(fc):
    # W-11 (pinned, deliberately): a zero-length frame is documented as "valid" by receive_message, but
    # _handle_client does `if message_str:` so it is treated as a disconnect. Change both together.
    assert fc.exchange_raw(frame(b"")) is None
    assert fc.request("execute_python_sync", {"code": "1"}) == {"result": "1"}


@pytest.mark.parametrize("data", [
    pytest.param(b"\x00\x00", id="truncated-header"),
    pytest.param(frame(b"x" * 100)[:14], id="short-body"),
])
def test_incomplete_frames_do_not_hurt_the_server(fc, data):
    assert fc.exchange_raw(data, close_after_send=True, wait=3.0) is None
    assert fc.request("execute_python_sync", {"code": "1"}) == {"result": "1"}


# --------------------------------------------------------------------------------------------------
# T2-9: receive deadline through the real server (dedicated server, FREECAD_MCP_RECV_TIMEOUT=1.0)
# --------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def short_timeout_server(make_server):
    s = make_server("short-timeout", {"FREECAD_MCP_RECV_TIMEOUT": "1.0"})
    s.start()
    return s


def _trickle(server: FcServer, data: bytes, gap: float, give_up: float):
    """Send one byte per `gap` seconds. Return seconds until the server closed on us, or None."""
    s = server.connect()
    s.settimeout(0.02)
    t0 = time.monotonic()
    sent = 0
    try:
        while time.monotonic() - t0 < give_up:
            if sent < len(data):
                try:
                    s.sendall(data[sent:sent + 1])
                    sent += 1
                except OSError:
                    return time.monotonic() - t0
            try:
                if s.recv(65536) == b"":
                    return time.monotonic() - t0
                return time.monotonic() - t0  # a reply: the request completed
            except TimeoutError:
                pass
            except OSError:
                return time.monotonic() - t0
            time.sleep(gap)
    finally:
        s.close()
    return None


@pytest.mark.xfail(strict=True, reason="needs PR #121 (single receive deadline per frame); remove this marker once it is on dev")
def test_slow_trickle_is_dropped_at_the_single_deadline(short_timeout_server):
    s = short_timeout_server
    req = frame(json.dumps({"tool": "execute_python_sync", "args": {"code": "1"}, "token": s.read_token()}).encode())
    elapsed = _trickle(s, req, gap=0.05, give_up=15.0)
    assert elapsed is not None and 0.8 <= elapsed <= 3.0, f"closed after {elapsed}s; want ~1.0s (a ~5-10s trickle)"
    assert s.request("execute_python_sync", {"code": "1"}) == {"result": "1"}


def test_stalled_partial_request_is_dropped_by_the_timeout(short_timeout_server):
    s = short_timeout_server
    sock = s.connect()
    sock.settimeout(8)
    t0 = time.monotonic()
    try:
        sock.sendall(frame(b"x" * 50)[:6])  # header + 2 body bytes, then silence
        assert sock.recv(10) == b""        # server closes when no byte arrives within the timeout
    finally:
        sock.close()
    assert 0.8 <= time.monotonic() - t0 <= 4.0
    assert s.request("execute_python_sync", {"code": "1"}) == {"result": "1"}


def test_fragmented_request_within_budget_completes(short_timeout_server):
    s = short_timeout_server
    req = frame(json.dumps({"tool": "execute_python_sync", "args": {"code": "40+2"}, "token": s.read_token()}).encode())
    sock = s.connect()
    try:
        for i in range(0, len(req), 8):
            sock.sendall(req[i:i + 8])
            time.sleep(0.01)
        import mcp_bridge_framing as fr
        assert json.loads(fr.receive_message(sock, timeout=10)) == {"result": "42"}
    finally:
        sock.close()


# --------------------------------------------------------------------------------------------------
# T2-10: stop leaves nothing behind (own spawn -> stop cycle)
# --------------------------------------------------------------------------------------------------
def _freecadcmd_pids() -> set[int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -ieq 'freecadcmd.exe' } | "
         "Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True).stdout.strip()
    if not out:
        return set()
    val = json.loads(out)
    return {val} if isinstance(val, int) else set(val)


def test_stop_closes_the_port_and_leaves_no_orphan(make_server):
    before = _freecadcmd_pids() if WIN else set()
    s = make_server("lifecycle")
    s.start()
    port = s.port
    s.stop()
    assert s.proc.poll() is not None
    time.sleep(0.5)
    assert not can_connect(port)
    if WIN:
        leftover = _freecadcmd_pids() - before
        assert not leftover, f"orphaned FreeCADCmd process(es) after stop: {sorted(leftover)}"

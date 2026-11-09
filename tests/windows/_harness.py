"""Harness for the Windows headless smoke suite.

Design: design-docs/freecad/windows-headless-testing-spec.md (section 6 and the cold-review
dispositions in section 0). Key rules, each from an observed failure mode:

* Every server gets its OWN temp tree and therefore its own token file. All servers started with
  the same profile share one ``~/.freecad-mcp/windows_auth_token``; a second server rewrites it
  and the first keeps its old token in memory (cold review F1).
* The spawn environment redirects USERPROFILE (our token + discovery dirs), HOME (POSIX fake mode)
  and FREECAD_USER_HOME/DATA/TEMP (FreeCAD's own dirs). The FREECAD_USER_* directories MUST already
  exist or FreeCAD silently ignores them and falls back to a cwd-relative ``FreeCAD\\vX-Y``
  (observed on Windows), so the process also runs in a throwaway cwd.
* stdout/stderr go to FILES, not pipes: a Windows pipe holds 4096 bytes before the writer blocks,
  and a drain thread that blocks on ``read(4096)`` shows nothing for a live-but-silent process.
* A FreeCAD script that raises still exits 0, so readiness is "the port accepts", never the exit code.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AICOPILOT = REPO / "AICopilot"
for _p in (str(REPO), str(AICOPILOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mcp_bridge_framing as fr  # noqa: E402

WIN = sys.platform == "win32"
FAKE = os.environ.get("FREECAD_MCP_WINDOWS_TESTS_FAKE") == "1"
MAX_MESSAGE_SIZE = fr.MAX_MESSAGE_SIZE


class HarnessError(RuntimeError):
    pass


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self.name}>"


REAL = _Sentinel("real-token")   # send this server's current token
OMIT = _Sentinel("omit-token")   # send no "token" key at all


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def can_connect(port: int, timeout: float = 0.5) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


def norm(path: str | os.PathLike) -> str:
    """Comparison form: real path, case-folded, no trailing separator (Windows short names,
    case and the trailing backslash FreeCAD appends to its dirs all differ otherwise)."""
    return os.path.normcase(os.path.realpath(str(path))).rstrip("\\/")


def is_under(child: str, parent: str) -> bool:
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p + os.sep)


def padded_request_body(total_len: int, token: str) -> bytes:
    """A valid execute_python_sync request whose JSON body is exactly ``total_len`` bytes."""
    def build(pad: int) -> bytes:
        return json.dumps(
            {"tool": "execute_python_sync", "args": {"code": "1  # " + "x" * pad}, "token": token}
        ).encode()

    base = len(build(0))
    if total_len < base:
        raise HarnessError(f"cannot build a request shorter than {base} bytes")
    body = build(total_len - base)
    assert len(body) == total_len
    return body


class FcServer:
    """One headless FreeCAD running the real headless_server.py, fully isolated."""

    def __init__(self, root: Path, binary: str, label: str, extra_env: dict[str, str] | None = None):
        self.root = Path(root)
        self.binary = binary
        self.label = label
        self.extra_env = dict(extra_env or {})
        self.home = self.root / "home"
        self.data = self.root / "data"
        self.temp = self.root / "temp"
        self.appdata = self.root / "appdata"
        self.local = self.root / "local"
        self.cwd = self.root / "cwd"
        self.logs = self.root / "logs"
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None
        self.starts = 0
        self.token_existed_before_start: bool | None = None
        self._files: list = []

    # -- paths ----------------------------------------------------------------------------
    @property
    def token_path(self) -> Path:
        return self.home / ".freecad-mcp" / "windows_auth_token"

    def read_token(self) -> str | None:
        try:
            return self.token_path.read_text(encoding="ascii")
        except OSError:
            return None

    # -- lifecycle ------------------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        for k in ("FREECAD_MCP_SOCKET", "FREECAD_MCP_RECV_TIMEOUT", "FREECAD_MCP_PORT"):
            env.pop(k, None)
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            APPDATA=str(self.appdata),
            LOCALAPPDATA=str(self.local),
            FREECAD_USER_HOME=str(self.home),
            FREECAD_USER_DATA=str(self.data),
            FREECAD_USER_TEMP=str(self.temp),
            FREECAD_MCP_PORT=str(self.port),
            FAKEWIN_AICOPILOT_DIR=str(AICOPILOT),
        )
        env.update(self.extra_env)
        return env

    def start(self, port: int | None = None, ready_timeout: float = 90.0) -> None:
        if self.proc is not None and self.proc.poll() is None:
            raise HarnessError(f"{self.label}: already running")
        for d in (self.home, self.data, self.temp, self.appdata, self.local, self.cwd, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        self.port = port or free_port()
        self.token_existed_before_start = self.token_path.exists()
        self.starts += 1
        script = (
            Path(__file__).with_name("fakewin_headless.py") if FAKE else AICOPILOT / "headless_server.py"
        )
        out = open(self.logs / f"stdout-{self.starts}.log", "wb")  # noqa: SIM115 - lives with the process
        err = open(self.logs / f"stderr-{self.starts}.log", "wb")  # noqa: SIM115
        self._files += [out, err]
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {}
        self.proc = subprocess.Popen(
            [self.binary, "--disable-addon", "AICopilot", "-M", str(AICOPILOT), str(script)],
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=self._env(), cwd=str(self.cwd), **kwargs,
        )
        t0 = time.monotonic()
        while time.monotonic() - t0 < ready_timeout:
            if self.proc.poll() is not None:
                raise HarnessError(f"{self.label}: exited early rc={self.proc.returncode}\n{self.log_tail()}")
            if can_connect(self.port):
                return
            time.sleep(0.2)
        self.stop()
        raise HarnessError(f"{self.label}: port {self.port} never accepted within {ready_timeout}s\n{self.log_tail()}")

    def stop(self) -> None:
        """Hard stop, like stop_freecad_instance on Windows (TerminateProcess; no cleanup runs)."""
        p = self.proc
        if p is not None and p.poll() is None:
            with contextlib.suppress(OSError):
                p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    p.kill()
                p.wait(timeout=15)
        if p is not None and WIN:  # belt and braces: a launcher shim leaves its child behind
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
        for f in self._files:
            with contextlib.suppress(OSError):
                f.close()
        self._files = []

    def restart(self, port: int | None = None) -> None:
        self.stop()
        self.start(port=port)

    @property
    def pid(self) -> int:
        assert self.proc is not None
        return self.proc.pid

    def log_tail(self, n: int = 1500) -> str:
        parts = []
        for f in sorted(self.logs.glob("*.log")):
            with contextlib.suppress(OSError):
                parts.append(f"--- {f.name}\n" + f.read_text(errors="replace")[-n:])
        return "\n".join(parts)

    # -- requests -------------------------------------------------------------------------
    def connect(self, timeout: float = 5.0) -> socket.socket:
        assert self.port is not None
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)

    def request(self, tool: str | None, args: dict | None = None, token=REAL, extra: dict | None = None,
                timeout: float = 30.0):
        """One request on a fresh connection; the reply parsed as JSON when possible (else the raw str / None)."""
        payload: dict = {}
        if tool is not None:
            payload["tool"] = tool
        payload["args"] = args if args is not None else {}
        if token is REAL:
            payload["token"] = self.read_token()
        elif token is not OMIT:
            payload["token"] = token
        if extra:
            payload.update(extra)
        s = self.connect()
        try:
            if not fr.send_message(s, json.dumps(payload)):
                return None
            reply = fr.receive_message(s, timeout=timeout)
        finally:
            s.close()
        try:
            return json.loads(reply)
        except (TypeError, ValueError):
            return reply

    def exchange_raw(self, data: bytes, *, close_after_send: bool = False, wait: float = 5.0) -> str | None:
        """Send raw bytes; return the framed reply as str, or None if the server closed without replying."""
        s = self.connect()
        try:
            s.settimeout(wait)
            with contextlib.suppress(OSError):
                s.sendall(data)
            if close_after_send:
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_WR)
            try:
                return fr.receive_message(s, timeout=wait)
            except OSError:
                return None
        finally:
            s.close()

    def py(self, code: str):
        """Run code on the server (handler tool execute_python_sync) and return its result string."""
        reply = self.request("execute_python_sync", {"code": code})
        if not isinstance(reply, dict) or "result" not in reply:
            raise HarnessError(f"{self.label}: unexpected reply {reply!r}")
        return reply["result"]


def frame(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body

"""FREECAD_MCP_PORT parsing: handler vs. bridge parity, boundaries, and import safety.

Two independent copies of ``_parse_port`` (AICopilot/freecad_mcp_handler.py and
freecad_mcp_server.py) live in separate processes that can't import each other,
so nothing but this file keeps them agreeing.

Regression: the handler did ``int(os.environ.get("FREECAD_MCP_PORT", "23456"))``
at *module import* on every platform. An empty or non-numeric value raised
ValueError and took the whole addon down -- on Linux/macOS, where the port is
never used. Out-of-range values (0, -1, 65536) and odd-but-int()-valid forms
("+80", "2_3456", fullwidth digits) were accepted silently and failed later,
far from the cause.
"""

import importlib.util
import os
import sys

import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import tests.unit._freecad_mocks  # isort: skip  # noqa: E402,F401 -- must precede the handler import; installs FreeCAD/Part/etc. mocks

import freecad_mcp_handler  # noqa: E402

import freecad_mcp_server  # noqa: E402

pytestmark = pytest.mark.windows_safe

HANDLER_PATH = os.path.join(AICOPILOT_DIR, "freecad_mcp_handler.py")
DEFAULT = 23456

# (raw env value, expected port or None meaning "must raise ValueError").
# Thresholds: 0/1 and 65535/65536 are each tested on both sides of the boundary.
CASES = [
    (None, DEFAULT),            # unset
    ("", DEFAULT),              # set-but-empty == not configured (pinned)
    ("   ", DEFAULT),           # whitespace-only == not configured (pinned)
    ("23456", 23456),
    ("1", 1),                   # lowest valid
    ("0", None),                # just below
    ("65535", 65535),           # highest valid
    ("65536", None),            # just above
    ("99999", None),
    ("123456", None),           # six digits
    ("-1", None),
    ("+80", None),              # int() would accept; we don't
    ("2_3456", None),           # int() accepts underscores; we don't
    ("２３４５６", None),  # fullwidth digits: int() accepts, we don't
    ("abc", None),
    ("0x50", None),
    ("1e3", None),
    ("23456.0", None),
    (" 8080 ", 8080),           # surrounding whitespace is stripped (pinned)
    ("00080", 80),              # leading zeros accepted (pinned)
]


@pytest.mark.parametrize("raw,expected", CASES)
@pytest.mark.parametrize("module", [freecad_mcp_handler, freecad_mcp_server], ids=["handler", "bridge"])
def test_parse_port(module, raw, expected):
    if expected is None:
        with pytest.raises(ValueError) as exc:
            module._parse_port(raw)
        # Rule 3: the consumer is a human reading the error; it must name the variable and value.
        assert "FREECAD_MCP_PORT" in str(exc.value)
        assert repr(raw) in str(exc.value)
    else:
        assert module._parse_port(raw) == expected


def test_handler_and_bridge_agree_on_every_case():
    for raw, _expected in CASES:
        results = []
        for module in (freecad_mcp_handler, freecad_mcp_server):
            try:
                results.append(("ok", module._parse_port(raw)))
            except ValueError as e:
                results.append(("err", str(e)))
        assert results[0] == results[1], f"handler/bridge diverge on {raw!r}: {results}"


def test_defaults_agree():
    assert freecad_mcp_handler._DEFAULT_WINDOWS_PORT == freecad_mcp_server._DEFAULT_WINDOWS_PORT == DEFAULT


def _load_handler_fresh(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("FREECAD_MCP_PORT", raising=False)
    else:
        monkeypatch.setenv("FREECAD_MCP_PORT", env_value)
    spec = importlib.util.spec_from_file_location("freecad_mcp_handler_porttest", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("bad", ["abc", "0", "65536", "-1", "+80"])
def test_bad_port_does_not_break_handler_import(monkeypatch, bad):
    """The core regression: a bad value must not raise at import (it only matters on Windows)."""
    mod = _load_handler_fresh(monkeypatch, bad)
    assert mod.WINDOWS_PORT == DEFAULT
    assert "FREECAD_MCP_PORT" in mod._WINDOWS_PORT_ERROR


def test_empty_port_is_not_an_error_at_import(monkeypatch):
    mod = _load_handler_fresh(monkeypatch, "")
    assert mod.WINDOWS_PORT == DEFAULT
    assert mod._WINDOWS_PORT_ERROR is None


def test_valid_port_is_used_at_import(monkeypatch):
    mod = _load_handler_fresh(monkeypatch, "24000")
    assert mod.WINDOWS_PORT == 24000
    assert mod._WINDOWS_PORT_ERROR is None


class TestBridgeCtxPort:
    @staticmethod
    def _as_windows(monkeypatch):
        monkeypatch.setattr(freecad_mcp_server.platform, "system", lambda: "Windows")

    def test_valid_port_builds_endpoint(self, monkeypatch):
        self._as_windows(monkeypatch)
        monkeypatch.setenv("FREECAD_MCP_PORT", "24000")
        assert freecad_mcp_server._BridgeCtx().socket_path == "localhost:24000"

    def test_unset_uses_default(self, monkeypatch):
        self._as_windows(monkeypatch)
        monkeypatch.delenv("FREECAD_MCP_PORT", raising=False)
        assert freecad_mcp_server._BridgeCtx().socket_path == f"localhost:{DEFAULT}"

    @pytest.mark.parametrize("bad", ["abc", "0", "65536"])
    def test_bad_port_fails_loudly_on_windows(self, monkeypatch, bad):
        self._as_windows(monkeypatch)
        monkeypatch.setenv("FREECAD_MCP_PORT", bad)
        with pytest.raises(ValueError, match="FREECAD_MCP_PORT"):
            freecad_mcp_server._BridgeCtx()

    def test_bad_port_is_ignored_off_windows(self, monkeypatch):
        monkeypatch.setattr(freecad_mcp_server.platform, "system", lambda: "Linux")
        monkeypatch.setenv("FREECAD_MCP_PORT", "abc")
        monkeypatch.delenv("FREECAD_MCP_SOCKET", raising=False)
        assert freecad_mcp_server._BridgeCtx().socket_path is None

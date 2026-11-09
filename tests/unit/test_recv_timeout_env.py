"""FREECAD_MCP_RECV_TIMEOUT: strict parsing and import safety.

The handler's per-connection request receive used a fixed 30 s timeout, so the
single-deadline behavior (PR #121) could not be exercised end to end in a
reasonable time. FREECAD_MCP_RECV_TIMEOUT makes it configurable. Because the
value comes from the environment, the same hazards as FREECAD_MCP_PORT apply:
silent substitution on a bad value, int()/float()-valid oddities, and an
import-time crash. See test_port_env_parity.py for the sibling cases.
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

pytestmark = pytest.mark.windows_safe

HANDLER_PATH = os.path.join(AICOPILOT_DIR, "freecad_mcp_handler.py")
DEFAULT = 30.0

# (raw env value, expected seconds or None meaning "must raise ValueError").
# Thresholds: >0 and <=3600, tested on both sides of each end.
CASES = [
    (None, DEFAULT),             # unset
    ("", DEFAULT),               # set-but-empty == not configured (pinned)
    ("   ", DEFAULT),            # whitespace-only == not configured (pinned)
    ("30", 30.0),
    ("0.5", 0.5),
    (".25", 0.25),               # leading-dot form (pinned)
    ("5.", 5.0),                 # trailing-dot form (pinned)
    ("0.001", 0.001),            # just above zero
    ("0", None),                 # at zero: socket code treats 0 as non-blocking, not "no timeout"
    ("0.0", None),
    ("00", None),
    ("-1", None),
    ("-0.5", None),
    ("3599.999", 3599.999),      # just below the cap
    ("3600", 3600.0),            # at the cap
    ("3600.0", 3600.0),
    ("3600.001", None),          # just above the cap
    ("3601", None),
    ("99999999999999999999", None),
    ("1e3", None),               # float() accepts exponents; we don't
    ("1_0", None),               # float() accepts underscores; we don't
    ("+5", None),
    ("inf", None),
    ("nan", None),
    ("abc", None),
    ("1.2.3", None),
    (".", None),
    ("0x10", None),
    ("２", None),            # fullwidth digit: float() accepts, we don't
    ("٣", None),            # Arabic-Indic digit
    (" 2 ", 2.0),                # surrounding whitespace is stripped (pinned)
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_parse_recv_timeout(raw, expected):
    if expected is None:
        with pytest.raises(ValueError) as exc:
            freecad_mcp_handler._parse_recv_timeout(raw)
        # The consumer is a human reading the error: it must name the variable and the value.
        assert "FREECAD_MCP_RECV_TIMEOUT" in str(exc.value)
        assert repr(raw) in str(exc.value)
    else:
        assert freecad_mcp_handler._parse_recv_timeout(raw) == pytest.approx(expected)


def test_default_matches_receive_message_default():
    """The env default and receive_message()'s own default must not drift apart."""
    import inspect
    sig = inspect.signature(freecad_mcp_handler.receive_message)
    assert sig.parameters["timeout"].default == freecad_mcp_handler._DEFAULT_RECV_TIMEOUT == DEFAULT


def _load_handler_fresh(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("FREECAD_MCP_RECV_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("FREECAD_MCP_RECV_TIMEOUT", env_value)
    spec = importlib.util.spec_from_file_location("freecad_mcp_handler_timeouttest", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "3601", "nan", "1e3"])
def test_bad_timeout_does_not_break_handler_import(monkeypatch, bad):
    """A bad value must not raise at import (that would take the whole addon down)."""
    mod = _load_handler_fresh(monkeypatch, bad)
    assert mod.RECV_TIMEOUT == DEFAULT
    assert "FREECAD_MCP_RECV_TIMEOUT" in mod._RECV_TIMEOUT_ERROR


def test_unset_and_empty_are_not_errors(monkeypatch):
    for value in (None, ""):
        mod = _load_handler_fresh(monkeypatch, value)
        assert mod.RECV_TIMEOUT == DEFAULT
        assert mod._RECV_TIMEOUT_ERROR is None


def test_valid_timeout_is_used_at_import(monkeypatch):
    mod = _load_handler_fresh(monkeypatch, "0.5")
    assert mod.RECV_TIMEOUT == 0.5
    assert mod._RECV_TIMEOUT_ERROR is None

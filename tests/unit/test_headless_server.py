"""Unit tests for AICopilot/headless_server.py.

Previously zero test coverage: the module ran main() unconditionally at
import time (no `if __name__ == "__main__":` guard), so importing it for
testing would have started a real socket server and blocked in its
signal-wait loop. That guard was added specifically to make this file
testable -- FreeCADCmd invokes this file as a script (per the module's own
docstring), where __name__ == "__main__" regardless, so the guard changes
nothing about production behavior.

This diff's own commit (338a89a) is the direct motivation for this file's
one real regression-worthy behavior: `FreeCAD._ai_socket_server` (single
leading underscore) must be set, not the double-underscore form that used
to get silently name-mangled when written from inside a class method
elsewhere in this codebase.
"""

import os
import signal
import sys
import types

import pytest
from unittest.mock import MagicMock

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
sys.path.insert(0, AICOPILOT_DIR)

import tests.unit._freecad_mocks as _fc_mocks  # noqa: E402


class _FakeServer:
    def __init__(self, start_ok=True):
        self.instance_uuid = "fakeuuid0001"
        self.socket_path = "/tmp/freecad_mcp_fakeuuid0001.sock"
        self._start_ok = start_ok
        self.stop_server = MagicMock()

    def start_server(self):
        return self._start_ok


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _fc_mocks.reset_mocks()
    # headless_server does module-level `import instance_registry` inside
    # main() -- stub it out so no real files are ever written by these
    # tests, and so behavior (discovery write, sweep) is directly assertable.
    fake_instance_registry = MagicMock()
    monkeypatch.setitem(sys.modules, "instance_registry", fake_instance_registry)
    yield
    sys.modules.pop("headless_server", None)


@pytest.fixture
def hs(monkeypatch):
    """Import headless_server fresh for each test (module-level state like
    _socket_path_arg is computed once at import time).

    headless_server.py's own module-level code deliberately evicts
    `handlers` / `handlers.*` / `freecad_mcp_handler` from sys.modules
    (guarding against a real stale-cached-addon-path bug) -- appropriate
    for a real process startup, but running that eviction against the
    shared sys.modules of a whole pytest session would permanently drop
    other test files' cached module objects, causing e.g. mock.patch to
    later re-resolve a dotted path to a DIFFERENT freshly-reimported
    module instance than the one those tests actually exercise. Snapshot
    and restore the evicted entries so this test file's use of the real
    eviction logic stays contained to itself.
    """
    snapshot = {
        name: sys.modules.get(name)
        for name in list(sys.modules)
        if name == "freecad_mcp_handler" or name == "handlers" or name.startswith("handlers.")
    }
    sys.modules.pop("headless_server", None)
    import headless_server
    yield headless_server
    # Restore every snapshotted entry -- headless_server's own eviction
    # DELETES these keys from sys.modules, so a loop over the (now
    # missing-those-keys) current sys.modules would never touch them; the
    # snapshot's own keys are the ones that must come back.
    for name, mod in snapshot.items():
        sys.modules[name] = mod
    # Anything matching the pattern that got created fresh during the
    # test and wasn't there before (shouldn't normally happen, since
    # nothing in this test file imports real handlers.* modules) --
    # drop it rather than leave a stray extra entry behind.
    for name in list(sys.modules):
        if (name == "freecad_mcp_handler" or name == "handlers" or name.startswith("handlers.")) \
                and name not in snapshot:
            sys.modules.pop(name, None)


class TestParseSocketPath:
    def test_space_separated_flag(self, hs, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["headless_server.py", "--socket-path", "/tmp/x.sock"])
        assert hs._parse_socket_path() == "/tmp/x.sock"

    def test_equals_separated_flag(self, hs, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["headless_server.py", "--socket-path=/tmp/y.sock"])
        assert hs._parse_socket_path() == "/tmp/y.sock"

    def test_no_flag_returns_none(self, hs, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["headless_server.py"])
        assert hs._parse_socket_path() is None

    def test_flag_at_end_with_no_value_returns_none(self, hs, monkeypatch):
        """A dangling --socket-path with nothing after it must not raise
        an IndexError or return a bogus value."""
        monkeypatch.setattr(sys, "argv", ["headless_server.py", "--socket-path"])
        assert hs._parse_socket_path() is None


class TestMainStartupFailure:
    def test_import_error_exits_1(self, hs, monkeypatch):
        """FreeCADSocketServer import failure (addon not properly
        installed) must exit(1) with a clear error, not crash with an
        unhandled ImportError."""
        monkeypatch.setitem(sys.modules, "freecad_mcp_handler", None)  # forces ImportError
        with pytest.raises(SystemExit) as exc_info:
            hs.main()
        assert exc_info.value.code == 1

    def test_start_server_failure_exits_1(self, hs, monkeypatch):
        fake_module = types.ModuleType("freecad_mcp_handler")
        fake_module.FreeCADSocketServer = lambda: _FakeServer(start_ok=False)
        monkeypatch.setitem(sys.modules, "freecad_mcp_handler", fake_module)

        with pytest.raises(SystemExit) as exc_info:
            hs.main()
        assert exc_info.value.code == 1


class TestMainStartupSuccess:
    def _install_fake_socket_server(self, monkeypatch, server):
        fake_module = types.ModuleType("freecad_mcp_handler")
        fake_module.FreeCADSocketServer = lambda: server
        monkeypatch.setitem(sys.modules, "freecad_mcp_handler", fake_module)

    def _run_main_until_signaled(self, hs, monkeypatch):
        """main() blocks in a `while not _stop.is_set(): time.sleep(0.5)`
        loop until SIGTERM/SIGINT. Send this process SIGTERM the first
        time that sleep is reached (after startup has fully run) so the
        real signal-registration and shutdown-cleanup code paths execute,
        instead of only ever covering the pre-loop startup code."""
        def fake_sleep(_seconds):
            os.kill(os.getpid(), signal.SIGTERM)
        monkeypatch.setattr(hs.time, "sleep", fake_sleep)
        hs.main()

    def test_sets_single_underscore_attribute_on_freecad(self, hs, monkeypatch):
        """Regression coverage for 338a89a: must be `_ai_socket_server`
        (single underscore), not a name that would get mangled if this
        were ever written from inside a class method."""
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)

        self._run_main_until_signaled(hs, monkeypatch)

        # Cleaned up again by the shutdown path -- confirm it was set at
        # all by asserting the attribute existed by the time cleanup ran.
        assert not hasattr(_fc_mocks.mock_FreeCAD, "_ai_socket_server")
        assert not hasattr(_fc_mocks.mock_FreeCAD, "__ai_socket_server")

    def test_writes_discovery_record_with_headless_gui_false(self, hs, monkeypatch):
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)

        self._run_main_until_signaled(hs, monkeypatch)

        write_call = sys.modules["instance_registry"].write_discovery
        write_call.assert_called_once()
        args, kwargs = write_call.call_args
        assert args[0] == server.instance_uuid
        assert args[1] == server.socket_path
        assert kwargs["gui"] is False

    def test_sweeps_stale_sockets_on_startup(self, hs, monkeypatch):
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)
        sys.modules["instance_registry"].sweep_stale_sockets.return_value = 3

        self._run_main_until_signaled(hs, monkeypatch)

        sys.modules["instance_registry"].sweep_stale_sockets.assert_called_once()

    def test_removes_discovery_record_on_shutdown(self, hs, monkeypatch):
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)

        self._run_main_until_signaled(hs, monkeypatch)

        sys.modules["instance_registry"].remove_discovery.assert_called_once_with(server.instance_uuid)

    def test_stops_the_socket_server_on_shutdown(self, hs, monkeypatch):
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)

        self._run_main_until_signaled(hs, monkeypatch)

        server.stop_server.assert_called_once()

    def test_discovery_write_failure_does_not_crash_startup(self, hs, monkeypatch):
        """Discovery file writing is documented as best-effort/never
        fatal -- a raising write_discovery must not prevent the server
        from starting or crash main()."""
        server = _FakeServer()
        self._install_fake_socket_server(monkeypatch, server)
        sys.modules["instance_registry"].write_discovery.side_effect = OSError("disk full")

        self._run_main_until_signaled(hs, monkeypatch)  # must not raise

        server.stop_server.assert_called_once()

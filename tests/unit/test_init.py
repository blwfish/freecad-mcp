"""Tests for AICopilot/Init.py's SIGPIPE-ignore fix.

Background: a long doc.recompute()/boolean op left the GUI thread blocked
for tens of seconds to minutes. Something writing to a broken pipe during
that window delivered SIGPIPE with its default (terminating) disposition,
silently killing the whole FreeCAD process -- no crash report, no Python-level
shutdown hook fired, indistinguishable from an external SIGKILL until the
exact signal number was captured via a real exit code (2026-08-07,
FC-tools/crash-capture watchdog: exit code 141 = 128 + SIGPIPE(13), not
137 = 128 + SIGKILL(9)). Confirmed live: signal.getsignal(SIGPIPE) was
SIG_DFL on a running FreeCAD instance -- FreeCAD's embedded Python does not
inherit CPython's usual "ignore SIGPIPE at startup" behavior. Setting it to
SIG_IGN in Init.py (not InitGui.py) covers both GUI and headless
(FreeCADCmd) launches, since Init.py runs for both -- matching the spec's
finding that headless mode died the same way.

Init.py runs unconditionally at FreeCAD startup (not gated behind
FreeCAD.GuiUp, unlike InitGui.py), so it's loaded the same way regardless
of GUI/headless mode -- one test suffices for both.
"""

import importlib.util
import os
import signal

import pytest

AICOPILOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot")
INIT_PATH = os.path.join(AICOPILOT_DIR, "Init.py")


def _load_init(fc_module):
    """Import Init.py fresh via spec_from_file_location.

    Init.py isn't a normal importable package (it's exec'd by FreeCAD's own
    module loader) and it runs top-level side-effecting code on import, so
    each test gets its own fresh exec rather than relying on Python's module
    cache -- same pattern as test_init_gui_quit_cleanup.py uses for
    InitGui.py.
    """
    spec = importlib.util.spec_from_file_location("Init_test", INIT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def restore_sigpipe_disposition():
    """Init.py mutates real process-wide signal state as its whole point --
    save/restore around each test so this file doesn't leak SIGPIPE=SIG_IGN
    into the rest of the pytest run (or SIG_DFL, if some future change flips
    the default) regardless of platform or prior state."""
    previous = signal.getsignal(signal.SIGPIPE) if hasattr(signal, "SIGPIPE") else None
    yield
    # Re-check hasattr at teardown, not just before yield: the
    # missing-attribute test's own monkeypatch.delattr can un-teardown
    # (restore SIGPIPE) either before or after this fixture's teardown runs,
    # since both are function-scoped -- trusting the pre-yield hasattr
    # result caused exactly that ordering-dependent AttributeError here.
    if hasattr(signal, "SIGPIPE") and previous is not None:
        signal.signal(signal.SIGPIPE, previous)


class TestSigpipeIgnored:
    @pytest.mark.skipif(not hasattr(signal, "SIGPIPE"), reason="SIGPIPE is POSIX-only")
    def test_import_sets_sigpipe_to_ignore(self, mock_freecad):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # start from the failure-mode disposition

        _load_init(mock_freecad)

        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN

    @pytest.mark.skipif(not hasattr(signal, "SIGPIPE"), reason="SIGPIPE is POSIX-only")
    def test_runs_before_gui_up_check(self, mock_freecad):
        """Init.py must not gate this behind FreeCAD.GuiUp -- headless
        FreeCADCmd died to the identical failure mode (spec finding #3), so
        the fix has to apply to headless launches too, not just GUI ones."""
        mock_freecad.GuiUp = False
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

        _load_init(mock_freecad)

        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN

    def test_missing_sigpipe_attribute_does_not_raise(self, mock_freecad, monkeypatch):
        """Platforms without SIGPIPE (Windows) must not crash AICopilot
        startup over a guard for a signal that doesn't exist there."""
        monkeypatch.delattr(signal, "SIGPIPE", raising=False)

        _load_init(mock_freecad)  # must not raise

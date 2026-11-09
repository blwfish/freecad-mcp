"""Fixtures for the Windows headless smoke suite (see _harness.py for the design rules).

Real mode  : win32 + FREECAD_MCP_FREECAD_BIN pointing at <FreeCAD>\\bin\\FreeCADCmd.exe.
Fake mode  : FREECAD_MCP_WINDOWS_TESTS_FAKE=1 on Linux/macOS (the handler's Windows branch is forced
             on by fakewin_headless.py). Proves logic only; Windows-only tests are skipped.
Otherwise  : every test skips, and the CI job treats an all-skipped run as a FAILURE.
"""

from __future__ import annotations

import os

import pytest

from ._harness import FAKE, WIN, FcServer, HarnessError, is_under


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "windows_headless: needs a real headless FreeCAD (real Windows, or fake mode on POSIX)"
    )


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/windows" in item.nodeid.replace("\\", "/") or "windows" in item.nodeid.split("::")[0]:
            item.add_marker(pytest.mark.windows_headless)


@pytest.fixture(scope="session")
def fc_binary() -> str:
    if not (WIN or FAKE):
        pytest.skip("Windows headless suite: needs real Windows or FREECAD_MCP_WINDOWS_TESTS_FAKE=1")
    binary = os.environ.get("FREECAD_MCP_FREECAD_BIN")
    if not binary or not os.path.isfile(binary):
        pytest.skip("FREECAD_MCP_FREECAD_BIN is not set to an existing FreeCADCmd binary")
    return binary


@pytest.fixture(scope="session")
def make_server(tmp_path_factory, fc_binary):
    """Factory: each call returns a NOT-yet-started server with its own temp tree (so its own token file)."""
    created: list[FcServer] = []

    def make(label: str, extra_env: dict[str, str] | None = None) -> FcServer:
        server = FcServer(tmp_path_factory.mktemp(label), fc_binary, label, extra_env)
        created.append(server)
        return server

    yield make
    for s in created:
        s.stop()


def assert_isolated(server: FcServer) -> None:
    """T2-0 gate. If FreeCAD ignored the redirect it would use (and write into) the REAL profile, or a
    cwd-relative dir inside the checkout, so abort the whole session before any other test runs."""
    seen = server.py(
        "import os; '|'.join([FreeCAD.ConfigGet('UserAppData'), FreeCAD.ConfigGet('UserConfigPath'), "
        "os.path.expanduser('~')])"
    ).strip("'\"")
    app_data, config_path, home = seen.split("|")
    problems = []
    if not is_under(app_data, str(server.data)):
        problems.append(f"UserAppData={app_data!r} is not under {server.data}")
    if not is_under(config_path, str(server.home)):
        problems.append(f"UserConfigPath={config_path!r} is not under {server.home}")
    if not is_under(home, str(server.home)):
        problems.append(f"expanduser('~')={home!r} is not under {server.home}")
    if problems:
        server.stop()
        pytest.exit("ISOLATION GATE FAILED (refusing to run the suite): " + "; ".join(problems), returncode=3)


@pytest.fixture(scope="session")
def fc(make_server) -> FcServer:
    """The long-lived main server, started once and isolation-checked before anything else uses it."""
    server = make_server("main")
    try:
        server.start()
    except HarnessError as e:
        pytest.fail(f"main server failed to start: {e}", pytrace=False)
    assert_isolated(server)
    return server


@pytest.fixture(scope="session", autouse=True)
def _isolation_gate(fc):
    """Autouse so the gate runs (and can abort the session) before the first test, whatever it requests."""
    return fc

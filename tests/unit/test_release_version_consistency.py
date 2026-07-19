"""Regression guard for M16: the project's release version string and the
mcp dependency floor were each duplicated across multiple files with no
automated cross-check, so nothing caught a bump to one without the others.

This is distinct from AICopilot/mcp_versions.py, which tracks internal
COMPONENT-compatibility (handler <-> debug <-> health __version__
requirements at runtime) — a separate concern from the project's overall
RELEASE version string checked here.
"""

import json
import os
import re

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read_pyproject_version():
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(path) as f:
        content = f.read()
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert m, "could not find `version = \"...\"` in pyproject.toml"
    return m.group(1)


def _read_server_json_version():
    path = os.path.join(REPO_ROOT, "server.json")
    with open(path) as f:
        data = json.load(f)
    assert "version" in data, "server.json has no top-level 'version' key"
    return data["version"]


def _read_handler_version():
    path = os.path.join(REPO_ROOT, "AICopilot", "freecad_mcp_handler.py")
    with open(path) as f:
        content = f.read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert m, "could not find `__version__ = \"...\"` in freecad_mcp_handler.py"
    return m.group(1)


def _read_pyproject_mcp_floor():
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(path) as f:
        content = f.read()
    m = re.search(r'"mcp>=([\d.]+)"', content)
    assert m, "could not find an mcp>=X.Y.Z dependency in pyproject.toml"
    return m.group(1)


def _read_requirements_mcp_pin():
    path = os.path.join(REPO_ROOT, "requirements.txt")
    with open(path) as f:
        content = f.read()
    # `mcp==` specifically — must not match the `mcp-events` line.
    m = re.search(r'^mcp==([\d.]+)$', content, re.MULTILINE)
    assert m, "could not find an mcp==X.Y.Z pin in requirements.txt"
    return m.group(1)


def _read_dockerfile_mcp_floor():
    path = os.path.join(REPO_ROOT, "Dockerfile")
    with open(path) as f:
        content = f.read()
    m = re.search(r'"mcp>=([\d.]+)"', content)
    assert m, "could not find an mcp>=X.Y.Z floor in Dockerfile"
    return m.group(1)


class TestReleaseVersionConsistency:
    """pyproject.toml, server.json, and freecad_mcp_handler.py's
    __version__ are three independent, hand-edited copies of the same
    release version string."""

    def test_pyproject_server_json_and_handler_versions_match(self):
        pyproject_v = _read_pyproject_version()
        server_json_v = _read_server_json_version()
        handler_v = _read_handler_version()
        assert pyproject_v == server_json_v == handler_v, (
            f"Version mismatch: pyproject.toml={pyproject_v!r}, "
            f"server.json={server_json_v!r}, "
            f"freecad_mcp_handler.py __version__={handler_v!r}"
        )


class TestMcpDependencyPinConsistency:
    """requirements.txt, pyproject.toml, and Dockerfile each declare a
    floor/pin for the mcp package independently. The Dockerfile's floor
    (1.27.1) previously diverged below the other two (1.28.1) — a Docker
    build could resolve an older mcp version than the rest of the project
    declares as supported."""

    def test_dockerfile_floor_matches_pyproject_floor(self):
        dockerfile_floor = _read_dockerfile_mcp_floor()
        pyproject_floor = _read_pyproject_mcp_floor()
        assert dockerfile_floor == pyproject_floor, (
            f"Dockerfile's mcp floor ({dockerfile_floor}) does not match "
            f"pyproject.toml's ({pyproject_floor})"
        )

    def test_requirements_pin_is_at_least_the_pyproject_floor(self):
        """requirements.txt pins an exact version (==); it must not be
        lower than pyproject.toml's declared floor (>=)."""
        pinned = tuple(int(p) for p in _read_requirements_mcp_pin().split("."))
        floor = tuple(int(p) for p in _read_pyproject_mcp_floor().split("."))
        assert pinned >= floor, (
            f"requirements.txt pins mcp=={'.'.join(map(str, pinned))}, "
            f"below pyproject.toml's floor mcp>={'.'.join(map(str, floor))}"
        )

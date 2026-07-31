"""
Tests for the update-check helpers in freecad_mcp_server.py: version
parsing/comparison, the cached+throttled GitHub releases check, and the
local audit log. See CLAUDE.md's testing rules -- these cover exact
threshold values (TTL boundary, version-equal), ambiguous inputs
(malformed versions, missing files, network failures), and what the
consumer (check_freecad_connection's JSON response) actually sees, not
just that handlers return an expected-looking dict.
"""

import asyncio
import json
import os
import sys
import types as _types
import urllib.error
import pytest
from unittest.mock import patch

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "freecad_mcp_server.py")


def _load_bridge():
    """Import freecad_mcp_server as a module without executing __main__."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("freecad_mcp_server_vc", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    mcp_stub = _types.ModuleType("mcp")
    mcp_stub.types = _types.ModuleType("mcp.types")
    sys.modules.setdefault("mcp", mcp_stub)
    sys.modules.setdefault("mcp.types", mcp_stub.types)
    sys.modules.setdefault("mcp.server", _types.ModuleType("mcp.server"))
    sys.modules.setdefault("mcp.server.models", _types.ModuleType("mcp.server.models"))
    sys.modules.setdefault("mcp.server.stdio", _types.ModuleType("mcp.server.stdio"))
    for opt in ("freecad_debug", "freecad_health"):
        sys.modules.setdefault(opt, None)  # type: ignore
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bridge():
    return _load_bridge()


@pytest.fixture(autouse=True)
def _isolate_version_paths(bridge, tmp_path, monkeypatch):
    """Every test gets its own cache/log paths -- never touch the real
    ~/.cache/freecad-mcp/ state on the machine running tests."""
    monkeypatch.setattr(bridge, "VERSION_CACHE_PATH", str(tmp_path / "version_check.json"))
    monkeypatch.setattr(bridge, "VERSION_LOG_PATH", str(tmp_path / "version_check.log"))


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# _parse_version
# ===========================================================================

class TestParseVersion:
    def test_basic(self, bridge):
        assert bridge._parse_version("7.0.0") == (7, 0, 0)

    def test_v_prefix(self, bridge):
        assert bridge._parse_version("v7.0.0") == (7, 0, 0)

    def test_capital_v_prefix(self, bridge):
        assert bridge._parse_version("V7.0.0") == (7, 0, 0)

    def test_two_component(self, bridge):
        assert bridge._parse_version("7.0") == (7, 0)

    def test_whitespace_stripped(self, bridge):
        assert bridge._parse_version("  7.0.0  ") == (7, 0, 0)

    def test_empty_string(self, bridge):
        assert bridge._parse_version("") is None

    def test_non_numeric_component(self, bridge):
        assert bridge._parse_version("7.x.0") is None

    def test_prerelease_suffix(self, bridge):
        # "7.0.0-rc1" -> can't compare; must not be silently truncated or
        # misparsed as equal/behind.
        assert bridge._parse_version("7.0.0-rc1") is None


# ===========================================================================
# _current_version
# ===========================================================================

class TestCurrentVersion:
    def test_reads_real_pyproject(self, bridge):
        # Sanity check against the actual repo file -- this is the real
        # source of truth the release automation already maintains.
        pyproject = os.path.join(os.path.dirname(BRIDGE_PATH), "pyproject.toml")
        with open(pyproject) as f:
            content = f.read()
        import re
        m = re.search(r'^\s*version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert bridge._current_version() == m.group(1)

    def test_missing_file(self, bridge, monkeypatch, tmp_path):
        monkeypatch.setattr(bridge, "__file__", str(tmp_path / "nonexistent" / "freecad_mcp_server.py"))
        assert bridge._current_version() is None

    def test_no_version_line(self, bridge, monkeypatch, tmp_path):
        fake_dir = tmp_path / "fakebridge"
        fake_dir.mkdir()
        (fake_dir / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
        monkeypatch.setattr(bridge, "__file__", str(fake_dir / "freecad_mcp_server.py"))
        assert bridge._current_version() is None


# ===========================================================================
# _fetch_latest_release (network mocked)
# ===========================================================================

class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestFetchLatestRelease:
    def test_success(self, bridge):
        body = json.dumps({"tag_name": "v7.1.0", "html_url": "https://x/releases/tag/v7.1.0"}).encode()
        with patch.object(bridge.urllib.request, "urlopen", return_value=_FakeResponse(body)):
            result = bridge._fetch_latest_release()
        assert result == {"tag": "v7.1.0", "url": "https://x/releases/tag/v7.1.0"}

    def test_success_logs_audit_line(self, bridge):
        body = json.dumps({"tag_name": "v7.1.0", "html_url": "https://x"}).encode()
        with patch.object(bridge.urllib.request, "urlopen", return_value=_FakeResponse(body)):
            bridge._fetch_latest_release()
        with open(bridge.VERSION_LOG_PATH) as f:
            log = f.read()
        assert "v7.1.0" in log
        assert bridge.RELEASES_API_URL in log

    def test_missing_tag_name(self, bridge):
        body = json.dumps({"html_url": "https://x"}).encode()
        with patch.object(bridge.urllib.request, "urlopen", return_value=_FakeResponse(body)):
            result = bridge._fetch_latest_release()
        assert "error" in result

    def test_malformed_json(self, bridge):
        with patch.object(bridge.urllib.request, "urlopen", return_value=_FakeResponse(b"not json")):
            result = bridge._fetch_latest_release()
        assert "error" in result

    def test_network_error(self, bridge):
        with patch.object(bridge.urllib.request, "urlopen", side_effect=urllib.error.URLError("no route")):
            result = bridge._fetch_latest_release()
        assert "error" in result

    def test_timeout(self, bridge):
        import socket as _socket
        with patch.object(bridge.urllib.request, "urlopen", side_effect=_socket.timeout()):
            result = bridge._fetch_latest_release()
        assert "error" in result

    def test_failure_also_logs_audit_line(self, bridge):
        with patch.object(bridge.urllib.request, "urlopen", side_effect=urllib.error.URLError("no route")):
            bridge._fetch_latest_release()
        with open(bridge.VERSION_LOG_PATH) as f:
            log = f.read()
        assert "error" in log
        assert bridge.RELEASES_API_URL in log


# ===========================================================================
# check_for_update -- the real decision logic
# ===========================================================================

class TestCheckForUpdate:
    def _set_current(self, bridge, monkeypatch, version: str):
        monkeypatch.setattr(bridge, "_current_version", lambda: version)

    def _mock_fetch(self, bridge, tag: str | None, error: bool = False):
        if error:
            result = {"error": "boom"}
        else:
            result = {"tag": tag, "url": f"https://x/releases/tag/{tag}"}
        return patch.object(bridge, "_fetch_latest_release", return_value=result)

    def test_equal_version_no_update(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, "v7.0.0"):
            assert _run(bridge.check_for_update()) is None

    def test_patch_behind(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, "v7.0.1"):
            result = _run(bridge.check_for_update())
        assert result == {"current": "7.0.0", "latest": "7.0.1", "url": "https://x/releases/tag/v7.0.1"}

    def test_minor_behind(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, "v7.1.0"):
            result = _run(bridge.check_for_update())
        assert result["latest"] == "7.1.0"

    def test_major_behind(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, "v8.0.0"):
            result = _run(bridge.check_for_update())
        assert result["latest"] == "8.0.0"

    def test_current_ahead_of_latest_release(self, bridge, monkeypatch):
        # Dev branch scenario: local version is ahead of the last tagged
        # release. Must never report "update available" here.
        self._set_current(bridge, monkeypatch, "7.1.0")
        with self._mock_fetch(bridge, "v7.0.0"):
            assert _run(bridge.check_for_update()) is None

    def test_different_length_versions_treated_equal(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0")
        with self._mock_fetch(bridge, "v7.0.0"):
            assert _run(bridge.check_for_update()) is None

    def test_unparseable_current_version(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "not-a-version")
        with self._mock_fetch(bridge, "v7.1.0"):
            assert _run(bridge.check_for_update()) is None

    def test_missing_current_version(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge, "_current_version", lambda: None)
        with self._mock_fetch(bridge, "v7.1.0"):
            assert _run(bridge.check_for_update()) is None

    def test_unparseable_latest_tag(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, "not-a-version"):
            assert _run(bridge.check_for_update()) is None

    def test_fetch_error_result(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, None, error=True):
            assert _run(bridge.check_for_update()) is None

    def test_cache_hit_skips_network(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        bridge._write_version_cache({
            "checked_at": bridge.time.time(),
            "result": {"tag": "v7.1.0", "url": "https://x/cached"},
        })
        with patch.object(bridge, "_fetch_latest_release") as mock_fetch:
            result = _run(bridge.check_for_update())
        mock_fetch.assert_not_called()
        assert result == {"current": "7.0.0", "latest": "7.1.0", "url": "https://x/cached"}

    def test_cache_just_under_ttl_is_fresh(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        bridge._write_version_cache({
            "checked_at": bridge.time.time() - (bridge.VERSION_CHECK_TTL_SECONDS - 1),
            "result": {"tag": "v7.0.0", "url": "https://x"},
        })
        with patch.object(bridge, "_fetch_latest_release") as mock_fetch:
            _run(bridge.check_for_update())
        mock_fetch.assert_not_called()

    def test_cache_exactly_at_ttl_triggers_fetch(self, bridge, monkeypatch):
        # (now - checked_at) < TTL is the freshness test; exactly at TTL
        # is NOT < TTL, so this must trigger a live check.
        self._set_current(bridge, monkeypatch, "7.0.0")
        bridge._write_version_cache({
            "checked_at": bridge.time.time() - bridge.VERSION_CHECK_TTL_SECONDS,
            "result": {"tag": "v7.0.0", "url": "https://x"},
        })
        with patch.object(bridge, "_fetch_latest_release", return_value={"tag": "v7.0.0", "url": "https://x"}) as mock_fetch:
            _run(bridge.check_for_update())
        mock_fetch.assert_called_once()

    def test_cache_just_over_ttl_triggers_fetch(self, bridge, monkeypatch):
        self._set_current(bridge, monkeypatch, "7.0.0")
        bridge._write_version_cache({
            "checked_at": bridge.time.time() - (bridge.VERSION_CHECK_TTL_SECONDS + 1),
            "result": {"tag": "v7.0.0", "url": "https://x"},
        })
        with patch.object(bridge, "_fetch_latest_release", return_value={"tag": "v7.0.0", "url": "https://x"}) as mock_fetch:
            _run(bridge.check_for_update())
        mock_fetch.assert_called_once()

    def test_failed_check_is_cached_too(self, bridge, monkeypatch):
        # A failed live check must still populate the cache so a flaky or
        # unreachable network doesn't cause a fetch on every single call.
        self._set_current(bridge, monkeypatch, "7.0.0")
        with self._mock_fetch(bridge, None, error=True):
            _run(bridge.check_for_update())
        with patch.object(bridge, "_fetch_latest_release") as mock_fetch:
            _run(bridge.check_for_update())
        mock_fetch.assert_not_called()


# ===========================================================================
# Cache/log file resilience -- must never raise even if disk I/O fails
# ===========================================================================

class TestResilience:
    def test_write_cache_survives_oserror(self, bridge, monkeypatch):
        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(bridge.os, "makedirs", _boom)
        bridge._write_version_cache({"checked_at": 0, "result": {}})  # must not raise

    def test_read_cache_survives_missing_file(self, bridge, monkeypatch, tmp_path):
        monkeypatch.setattr(bridge, "VERSION_CACHE_PATH", str(tmp_path / "does" / "not" / "exist.json"))
        assert bridge._read_version_cache() is None

    def test_read_cache_survives_corrupt_json(self, bridge, tmp_path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json")
        with patch.object(bridge, "VERSION_CACHE_PATH", str(p)):
            assert bridge._read_version_cache() is None

    def test_log_survives_oserror(self, bridge, monkeypatch):
        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(bridge.os, "makedirs", _boom)
        bridge._log_version_check("test line")  # must not raise

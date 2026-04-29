"""Integration tests for macro_operations against a live FreeCAD instance.

Uses execute_python to monkeypatch FreeCAD.getUserMacroDir() to a per-test
temp directory so the user's real macro dir is never touched. The real
production path (App.getUserMacroDir lookup, the configurable preference,
etc.) is not exercised here — the unit tests cover the algorithm and the
risk in production isn't really in getUserMacroDir.

Requires a running FreeCAD with AICopilot or auto-spawn via conftest.

Run with: python3 -m pytest tests/integration/test_macro_ops.py -v
"""

import json
import os
import tempfile

import pytest

from . import conftest as _conftest  # noqa: F401  (used to bootstrap fixtures)
from .test_e2e_workflows import send_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _patch_macro_dir(path: str) -> dict:
    """Monkeypatch FreeCAD.getUserMacroDir inside the live instance.

    Stashes the original on FreeCAD._orig_getUserMacroDir so the fixture can
    restore it. Idempotent — re-patching from a previous test is fine.
    """
    code = f"""
import FreeCAD
if not hasattr(FreeCAD, '_orig_getUserMacroDir'):
    FreeCAD._orig_getUserMacroDir = FreeCAD.getUserMacroDir
def _patched(*a, **kw):
    return {path!r}
FreeCAD.getUserMacroDir = _patched
result = FreeCAD.getUserMacroDir()
"""
    return send_command("execute_python", {"code": code}, timeout=15.0)


def _restore_macro_dir() -> dict:
    code = """
import FreeCAD
if hasattr(FreeCAD, '_orig_getUserMacroDir'):
    FreeCAD.getUserMacroDir = FreeCAD._orig_getUserMacroDir
    del FreeCAD._orig_getUserMacroDir
result = 'restored'
"""
    return send_command("execute_python", {"code": code}, timeout=10.0)


def _macro(tool_args: dict) -> dict:
    """Call macro_operations and return the parsed JSON payload (or raw on error)."""
    resp = send_command("macro_operations", tool_args, timeout=15.0)
    # Bridge wraps successful results in {"result": "<json string>"}; pass through.
    if isinstance(resp, dict) and "result" in resp and isinstance(resp["result"], str):
        try:
            return json.loads(resp["result"])
        except json.JSONDecodeError:
            return resp
    return resp


# ---------------------------------------------------------------------------
# Fixture: per-test temp macro dir
# ---------------------------------------------------------------------------
@pytest.fixture
def macro_dir():
    with tempfile.TemporaryDirectory(prefix="freecad_mcp_macro_test_") as tmp:
        _patch_macro_dir(tmp)
        try:
            yield tmp
        finally:
            _restore_macro_dir()


def _write(macro_dir: str, name: str, body: str) -> str:
    path = os.path.join(macro_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestMacroOperationsLive:
    def test_list_empty(self, macro_dir):
        result = _macro({"operation": "list"})
        assert result.get("count") == 0
        assert result.get("macro_dir") == macro_dir

    def test_list_finds_macros(self, macro_dir):
        _write(macro_dir, "alpha.FCMacro", "x = 1\n")
        _write(macro_dir, "beta.FCMacro", "y = 2\n")
        result = _macro({"operation": "list"})
        names = {m["name"] for m in result["macros"]}
        assert names == {"alpha.FCMacro", "beta.FCMacro"}
        assert result["count"] == 2

    def test_read_returns_content(self, macro_dir):
        _write(macro_dir, "hello.FCMacro", "print('integration test')\n")
        result = _macro({"operation": "read", "name": "hello.FCMacro"})
        assert "print('integration test')" in result["content"]

    def test_read_resolves_bare_name(self, macro_dir):
        _write(macro_dir, "named.FCMacro", "z = 99\n")
        result = _macro({"operation": "read", "name": "named"})
        assert result["name"] == "named.FCMacro"
        assert "z = 99" in result["content"]

    def test_read_rejects_traversal(self, macro_dir):
        result = _macro({"operation": "read", "name": "../etc/passwd"})
        assert "error" in result

    def test_run_creates_freecad_object(self, macro_dir):
        """A macro running in the live instance can manipulate a FreeCAD doc."""
        doc_name = "MacroIntegrationDoc"
        _write(macro_dir, "make_doc.FCMacro", f"""
import FreeCAD
doc = FreeCAD.newDocument({doc_name!r})
print('created', doc.Name)
""")
        try:
            result = _macro({"operation": "run", "name": "make_doc.FCMacro"})
            assert "error" not in result, result
            assert "created" in result["stdout"]

            # Verify the document actually exists in the live instance.
            # execute_python wraps its return value as {"result": "<repr>"}
            # — no nested JSON to decode.
            check = send_command("execute_python", {
                "code": f"result = {doc_name!r} in [d.Name for d in FreeCAD.listDocuments().values()]"
            })
            assert str(check.get("result", "")) == "True", check
        finally:
            send_command("execute_python", {
                "code": f"""
import FreeCAD
if {doc_name!r} in [d.Name for d in FreeCAD.listDocuments().values()]:
    FreeCAD.closeDocument({doc_name!r})
"""
            })

    def test_run_namespace_has_freecad_modules(self, macro_dir):
        _write(macro_dir, "uses_part.FCMacro", """
import FreeCAD
import Part
print(Part is not None and FreeCAD is not None)
""")
        result = _macro({"operation": "run", "name": "uses_part.FCMacro"})
        assert "error" not in result, result
        assert result["stdout"] == "True"

    def test_run_macro_not_found(self, macro_dir):
        result = _macro({"operation": "run", "name": "does_not_exist"})
        assert "error" in result

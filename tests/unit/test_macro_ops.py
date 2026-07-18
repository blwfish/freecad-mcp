"""Unit tests for MacroOpsHandler.

Tests list/read/run actions against a real temporary directory acting as the
user macro dir. FreeCAD modules are mocked via the conftest.py autouse fixture.

All handler imports are deferred into fixtures so this test module does NOT
trigger `handlers/__init__.py` at collection time — that would cache every
handler module bound to whichever FreeCAD mock happened to be in sys.modules
first, breaking other test files (notably test_mesh_ops which sets up its
own mocks at module top).

Run with: python3 -m pytest tests/unit/test_macro_ops.py -v
"""

import json
import os
import sys
import tempfile
import textwrap
from unittest.mock import MagicMock

import pytest

# Make AICopilot importable, but DO NOT import any handler module here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def handler():
    """Construct a MacroOpsHandler. Defers the import to runtime so the
    handlers package init only runs when explicitly invoked."""
    from handlers.macro_ops import MacroOpsHandler
    return MacroOpsHandler(MagicMock(), MagicMock(), MagicMock(return_value={}))


@pytest.fixture
def macro_dir(monkeypatch):
    """Create a temp dir and patch the handler module's bound FreeCAD reference
    so its getUserMacroDir returns this dir.

    We patch the *actual* FreeCAD object the handler module imported, regardless
    of whether that's our mock, conftest's mock, or another test file's mock.
    """
    with tempfile.TemporaryDirectory() as tmp:
        import handlers.macro_ops as macro_ops_module
        monkeypatch.setattr(
            macro_ops_module.FreeCAD,
            "getUserMacroDir",
            MagicMock(return_value=tmp),
            raising=False,
        )
        yield tmp


def _write_macro(macro_dir: str, name: str, body: str) -> str:
    path = os.path.join(macro_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body).lstrip("\n"))
    return path


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
class TestList:
    def test_empty_dir(self, handler, macro_dir):
        result = json.loads(handler.list({}))
        assert result["count"] == 0
        assert result["macros"] == []
        assert result["macro_dir"] == macro_dir

    def test_single_macro(self, handler, macro_dir):
        _write_macro(macro_dir, "hello.FCMacro", "print('hi')\n")
        result = json.loads(handler.list({}))
        assert result["count"] == 1
        assert result["macros"][0]["name"] == "hello.FCMacro"
        assert result["macros"][0]["preview"] == "print('hi')"
        assert result["macros"][0]["size"] > 0

    def test_filters_non_macro_files(self, handler, macro_dir):
        _write_macro(macro_dir, "real.FCMacro", "x=1")
        _write_macro(macro_dir, "notes.txt", "hello")
        _write_macro(macro_dir, "data.json", "{}")
        result = json.loads(handler.list({}))
        names = [m["name"] for m in result["macros"]]
        assert names == ["real.FCMacro"]

    def test_includes_py_files(self, handler, macro_dir):
        _write_macro(macro_dir, "util.py", "def foo(): pass")
        result = json.loads(handler.list({}))
        names = [m["name"] for m in result["macros"]]
        assert "util.py" in names

    def test_skips_dotfiles_by_default(self, handler, macro_dir):
        _write_macro(macro_dir, ".hidden.FCMacro", "x=1")
        _write_macro(macro_dir, "visible.FCMacro", "x=2")
        result = json.loads(handler.list({}))
        names = [m["name"] for m in result["macros"]]
        assert ".hidden.FCMacro" not in names
        assert "visible.FCMacro" in names

    def test_includes_dotfiles_when_requested(self, handler, macro_dir):
        _write_macro(macro_dir, ".hidden.FCMacro", "x=1")
        result = json.loads(handler.list({"include_hidden": True}))
        names = [m["name"] for m in result["macros"]]
        assert ".hidden.FCMacro" in names

    def test_skips_subdirectories(self, handler, macro_dir):
        os.mkdir(os.path.join(macro_dir, "subdir"))
        _write_macro(macro_dir, "top.FCMacro", "x=1")
        result = json.loads(handler.list({}))
        names = [m["name"] for m in result["macros"]]
        assert names == ["top.FCMacro"]

    def test_preview_skips_shebang(self, handler, macro_dir):
        _write_macro(
            macro_dir,
            "shebang.FCMacro",
            "#!/usr/bin/env python\n# Real comment\nprint('hi')\n",
        )
        result = json.loads(handler.list({}))
        assert result["macros"][0]["preview"] == "# Real comment"

    def test_preview_truncates_long_lines(self, handler, macro_dir):
        long_line = "x = " + "1" * 200
        _write_macro(macro_dir, "long.FCMacro", long_line)
        result = json.loads(handler.list({}))
        preview = result["macros"][0]["preview"]
        assert len(preview) <= 121
        assert preview.endswith("…")

    def test_modified_is_iso_format(self, handler, macro_dir):
        _write_macro(macro_dir, "x.FCMacro", "x=1")
        result = json.loads(handler.list({}))
        from datetime import datetime
        datetime.fromisoformat(result["macros"][0]["modified"])

    def test_handles_unreadable_dir(self, handler, monkeypatch):
        import handlers.macro_ops as macro_ops_module
        monkeypatch.setattr(
            macro_ops_module.FreeCAD,
            "getUserMacroDir",
            MagicMock(return_value="/nonexistent/path/does/not/exist"),
            raising=False,
        )
        result = json.loads(handler.list({}))
        assert result["count"] == 0
        assert "note" in result

    def test_no_macro_dir_available(self, handler, monkeypatch):
        import handlers.macro_ops as macro_ops_module
        monkeypatch.setattr(
            macro_ops_module.FreeCAD,
            "getUserMacroDir",
            MagicMock(side_effect=Exception("no dir")),
            raising=False,
        )
        result = json.loads(handler.list({}))
        assert "error" in result


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
class TestRead:
    def test_read_with_extension(self, handler, macro_dir):
        _write_macro(macro_dir, "foo.FCMacro", "print('hello')\n")
        result = json.loads(handler.read({"name": "foo.FCMacro"}))
        assert result["name"] == "foo.FCMacro"
        assert "print('hello')" in result["content"]

    def test_read_bare_name_resolves_extension(self, handler, macro_dir):
        _write_macro(macro_dir, "bar.FCMacro", "x = 42\n")
        result = json.loads(handler.read({"name": "bar"}))
        assert result["name"] == "bar.FCMacro"
        assert "x = 42" in result["content"]

    def test_missing_name(self, handler, macro_dir):
        result = json.loads(handler.read({}))
        assert "error" in result

    def test_macro_not_found(self, handler, macro_dir):
        result = json.loads(handler.read({"name": "nope"}))
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_rejects_path_traversal(self, handler, macro_dir):
        result = json.loads(handler.read({"name": "../etc/passwd"}))
        assert "error" in result

    def test_rejects_absolute_path(self, handler, macro_dir):
        result = json.loads(handler.read({"name": "/etc/passwd"}))
        assert "error" in result

    def test_rejects_subdir_traversal(self, handler, macro_dir):
        os.mkdir(os.path.join(macro_dir, "sub"))
        _write_macro(macro_dir, os.path.join("sub", "nested.FCMacro"), "x=1")
        result = json.loads(handler.read({"name": "sub/nested.FCMacro"}))
        assert "error" in result


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
class TestRun:
    def test_run_captures_stdout(self, handler, macro_dir):
        _write_macro(macro_dir, "hi.FCMacro", "print('hello from macro')\n")
        result = json.loads(handler.run({"name": "hi.FCMacro", "confirmed": True}))
        assert "error" not in result
        assert result["stdout"] == "hello from macro"

    def test_run_captures_result_variable(self, handler, macro_dir):
        _write_macro(macro_dir, "calc.FCMacro", "result = 42\n")
        result = json.loads(handler.run({"name": "calc.FCMacro", "confirmed": True}))
        assert "error" not in result
        assert "42" in result["result"]

    def test_run_namespace_has_freecad(self, handler, macro_dir):
        _write_macro(
            macro_dir,
            "uses_freecad.FCMacro",
            "print(FreeCAD is not None)\n",
        )
        result = json.loads(handler.run({"name": "uses_freecad.FCMacro", "confirmed": True}))
        assert "error" not in result
        assert result["stdout"] == "True"

    def test_run_namespace_does_not_persist(self, handler, macro_dir):
        """Variables from one macro must not leak to the next."""
        _write_macro(macro_dir, "set.FCMacro", "x = 99\n")
        _write_macro(
            macro_dir,
            "check.FCMacro",
            "print('x' in dir())\n",
        )
        json.loads(handler.run({"name": "set.FCMacro", "confirmed": True}))
        result = json.loads(handler.run({"name": "check.FCMacro", "confirmed": True}))
        assert result["stdout"] == "False"

    def test_run_reports_syntax_error(self, handler, macro_dir):
        _write_macro(macro_dir, "broken.FCMacro", "def foo(:\n    pass\n")
        result = json.loads(handler.run({"name": "broken.FCMacro", "confirmed": True}))
        assert "error" in result
        assert "SyntaxError" in result["error"]

    def test_run_reports_runtime_error(self, handler, macro_dir):
        _write_macro(macro_dir, "boom.FCMacro", "1/0\n")
        result = json.loads(handler.run({"name": "boom.FCMacro", "confirmed": True}))
        assert "error" in result
        assert "ZeroDivisionError" in result["traceback"]

    def test_run_crash_includes_stdout_printed_before_the_error(self, handler, macro_dir):
        """Diagnostic prints before a crash explain *why* it crashed — these
        were silently discarded on the error path (only the success path
        included stdout) (H16)."""
        _write_macro(macro_dir, "diag_then_boom.FCMacro",
                     "print('about to divide')\n1/0\n")
        result = json.loads(handler.run({"name": "diag_then_boom.FCMacro", "confirmed": True}))
        assert "error" in result
        assert result["stdout"] == "about to divide"

    def test_run_crash_includes_stderr_printed_before_the_error(self, handler, macro_dir):
        _write_macro(macro_dir, "warn_then_boom.FCMacro",
                     "import sys\nsys.stderr.write('warning: deprecated\\n')\n1/0\n")
        result = json.loads(handler.run({"name": "warn_then_boom.FCMacro", "confirmed": True}))
        assert "error" in result
        assert result["stderr"] == "warning: deprecated"

    def test_run_missing_name(self, handler, macro_dir):
        result = json.loads(handler.run({}))
        assert "error" in result

    def test_run_macro_not_found(self, handler, macro_dir):
        result = json.loads(handler.run({"name": "ghost", "confirmed": True}))
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_run_rejects_path_traversal(self, handler, macro_dir):
        result = json.loads(handler.run({"name": "../etc/passwd", "confirmed": True}))
        assert "error" in result

    def test_run_without_confirmed_does_not_execute(self, handler, macro_dir):
        """The confirmed gate — macros run as Python with full OS access —
        had zero coverage of the omitted/False path: all 9 existing tests
        in this class pass confirmed=True. A side effect (writing a marker
        file) proves the macro body genuinely never ran, not just that the
        response shape looks right."""
        marker = os.path.join(macro_dir, "ran.txt")
        _write_macro(macro_dir, "sideeffect.FCMacro",
                     f"open({marker!r}, 'w').write('ran')\n")

        result = json.loads(handler.run({"name": "sideeffect.FCMacro"}))

        assert result.get("status") == "confirmation_required"
        assert "error" not in result
        assert not os.path.exists(marker), "macro body executed without confirmation"

    def test_run_confirmed_false_does_not_execute(self, handler, macro_dir):
        """confirmed=False (explicit, not just omitted) must be treated the
        same as omitted — args.get("confirmed") is falsy either way."""
        marker = os.path.join(macro_dir, "ran2.txt")
        _write_macro(macro_dir, "sideeffect2.FCMacro",
                     f"open({marker!r}, 'w').write('ran')\n")

        result = json.loads(handler.run({"name": "sideeffect2.FCMacro", "confirmed": False}))

        assert result.get("status") == "confirmation_required"
        assert not os.path.exists(marker)

    def test_confirmation_required_response_includes_macro_path(self, handler, macro_dir):
        _write_macro(macro_dir, "info.FCMacro", "pass\n")

        result = json.loads(handler.run({"name": "info.FCMacro"}))

        assert "path" in result
        assert "info.FCMacro" in result["path"]

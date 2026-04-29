# Macro operation handler — exposes the user's FreeCAD macro library to the MCP.
#
# Macros live in App.getUserMacroDir() (typically ~/.FreeCAD/Macro/ on macOS/Linux,
# %APPDATA%/FreeCAD/Macro/ on Windows). The path is configurable in FreeCAD's
# preferences and may differ per user.
#
# The AI sees a library of named .FCMacro / .py files and can:
#   - list   → enumerate available macros with size + first-line preview
#   - read   → fetch contents of a macro by name (so AI can inspect before running)
#   - run    → execute a macro by name in a FreeCAD-aware namespace
#
# Macros are executed via exec() in a fresh namespace seeded with FreeCAD,
# FreeCADGui, App, Gui, and Part. Variables created in a macro do NOT persist
# across calls (unlike execute_python's namespace) — macros are intended to be
# self-contained scripts.

import io
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

import FreeCAD

from .base import BaseHandler


_MACRO_EXTENSIONS = (".FCMacro", ".fcmacro", ".py")
_MAX_READ_BYTES = 256 * 1024  # 256 KB cap on read; macros are typically small


def _safe_macro_dir() -> Optional[str]:
    """Return the user's FreeCAD macro directory, or None if unavailable."""
    try:
        d = FreeCAD.getUserMacroDir(True)  # True = create if missing
    except Exception:
        try:
            d = FreeCAD.getUserMacroDir()
        except Exception:
            return None
    if not d or not isinstance(d, str):
        return None
    return d


def _resolve_macro_path(macro_dir: str, name: str) -> Optional[str]:
    """Resolve a macro name to an absolute path inside macro_dir.

    Accepts either a bare name ("foo") or a name with extension ("foo.FCMacro").
    Rejects any name containing path separators or '..' to prevent escape.
    """
    if not name or os.sep in name or "/" in name or "\\" in name or ".." in name:
        return None

    candidate = os.path.join(macro_dir, name)
    if os.path.isfile(candidate):
        return candidate

    # Try common extensions if the bare name was given
    for ext in _MACRO_EXTENSIONS:
        candidate = os.path.join(macro_dir, name + ext)
        if os.path.isfile(candidate):
            return candidate

    return None


def _first_nonblank_line(content: str, max_len: int = 120) -> str:
    """Return the first non-blank, non-shebang line as a preview, truncated."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        if len(stripped) > max_len:
            return stripped[:max_len] + "…"
        return stripped
    return ""


class MacroOpsHandler(BaseHandler):
    """Handler for FreeCAD macro discovery and execution."""

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------
    def list(self, args: Dict[str, Any]) -> str:
        """List macros in the user's FreeCAD macro directory.

        Args (optional):
            include_hidden — bool, default False. Include dotfiles.

        Returns JSON:
            {
              "macro_dir": "/Users/.../Macro",
              "count": N,
              "macros": [
                {"name": "foo.FCMacro", "size": 1234, "modified": "2026-04-28T12:00:00",
                 "preview": "first non-blank line"},
                ...
              ]
            }
        """
        macro_dir = _safe_macro_dir()
        if not macro_dir:
            return json.dumps({"error": "Could not determine FreeCAD macro directory"})
        if not os.path.isdir(macro_dir):
            return json.dumps({
                "macro_dir": macro_dir,
                "count": 0,
                "macros": [],
                "note": "Macro directory does not exist yet.",
            })

        include_hidden = bool(args.get("include_hidden", False))
        macros: List[Dict[str, Any]] = []

        try:
            entries = sorted(os.listdir(macro_dir))
        except OSError as e:
            return json.dumps({"error": f"Could not list macro directory: {e}"})

        for entry in entries:
            if not include_hidden and entry.startswith("."):
                continue
            full = os.path.join(macro_dir, entry)
            if not os.path.isfile(full):
                continue
            if not entry.endswith(_MACRO_EXTENSIONS):
                continue

            try:
                stat = os.stat(full)
            except OSError:
                continue

            preview = ""
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(2048)
                preview = _first_nonblank_line(head)
            except OSError:
                pass

            from datetime import datetime, timezone
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

            macros.append({
                "name": entry,
                "size": stat.st_size,
                "modified": modified,
                "preview": preview,
            })

        return json.dumps({
            "macro_dir": macro_dir,
            "count": len(macros),
            "macros": macros,
        }, indent=2)

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------
    def read(self, args: Dict[str, Any]) -> str:
        """Read the contents of a macro by name.

        Args:
            name — macro filename (e.g. "foo.FCMacro" or bare "foo")

        Returns JSON:
            {"name": "...", "path": "...", "size": N, "content": "..."}
        or  {"error": "..."}
        """
        name = (args.get("name") or "").strip()
        if not name:
            return json.dumps({"error": "Missing required arg: name"})

        macro_dir = _safe_macro_dir()
        if not macro_dir:
            return json.dumps({"error": "Could not determine FreeCAD macro directory"})

        path = _resolve_macro_path(macro_dir, name)
        if not path:
            return json.dumps({"error": f"Macro not found: {name}"})

        try:
            size = os.path.getsize(path)
            if size > _MAX_READ_BYTES:
                return json.dumps({
                    "error": f"Macro too large to read ({size} bytes; limit {_MAX_READ_BYTES})",
                    "name": os.path.basename(path),
                    "path": path,
                    "size": size,
                })
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return json.dumps({"error": f"Could not read macro: {e}"})

        return json.dumps({
            "name": os.path.basename(path),
            "path": path,
            "size": size,
            "content": content,
        })

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------
    def run(self, args: Dict[str, Any]) -> str:
        """Execute a macro by name in a FreeCAD-aware namespace.

        Args:
            name — macro filename (e.g. "foo.FCMacro" or bare "foo")

        Returns JSON:
            {"name": "...", "path": "...", "stdout": "...", "result": "..."}
        or  {"error": "...", "traceback": "..."}

        The macro runs with a fresh namespace pre-loaded with FreeCAD, FreeCADGui,
        App, Gui, Part, and Vector. Variables do NOT persist across calls.
        """
        name = (args.get("name") or "").strip()
        if not name:
            return json.dumps({"error": "Missing required arg: name"})

        macro_dir = _safe_macro_dir()
        if not macro_dir:
            return json.dumps({"error": "Could not determine FreeCAD macro directory"})

        path = _resolve_macro_path(macro_dir, name)
        if not path:
            return json.dumps({"error": f"Macro not found: {name}"})

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError as e:
            return json.dumps({"error": f"Could not read macro: {e}"})

        namespace: Dict[str, Any] = {
            "__name__": "__main__",
            "__file__": path,
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
        }
        try:
            import FreeCADGui  # noqa: F401
            namespace["FreeCADGui"] = FreeCADGui
            namespace["Gui"] = FreeCADGui
        except ImportError:
            pass
        try:
            import Part  # noqa: F401
            namespace["Part"] = Part
        except ImportError:
            pass
        try:
            from FreeCAD import Vector
            namespace["Vector"] = Vector
        except ImportError:
            pass

        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            try:
                code_obj = compile(source, path, "exec")
                exec(code_obj, namespace)
            except SyntaxError as e:
                return json.dumps({
                    "error": f"SyntaxError in macro: {e}",
                    "name": os.path.basename(path),
                    "traceback": traceback.format_exc(),
                })
            except Exception as e:
                return json.dumps({
                    "error": f"Macro execution error: {e}",
                    "name": os.path.basename(path),
                    "traceback": traceback.format_exc(),
                })
        finally:
            sys.stdout = old_stdout

        stdout_text = captured.getvalue().rstrip("\n")
        result_value = namespace.get("result")
        result_repr = repr(result_value) if result_value is not None else ""

        return json.dumps({
            "name": os.path.basename(path),
            "path": path,
            "stdout": stdout_text,
            "result": result_repr,
        })

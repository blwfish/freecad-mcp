# API introspection handler — live lookup against FreeCAD's running module tree.
#
# The AI can:
#   - inspect(path)              → signature + docstring for a callable/class/module
#   - search(query, modules?)    → fuzzy search for names matching a query
#   - record_useful(query, path) → record that a search→path resolution was useful;
#                                   ranks future searches accordingly
#
# Why: FreeCAD's Python API is large and unevenly documented. Training data drifts
# from current API behavior. Live introspection means the AI can verify a method
# signature before calling it via execute_python, eliminating a common class of
# AttributeError / wrong-signature failures.
#
# Persistent feedback file: ~/.freecad-mcp/introspection_feedback.json
#   schema: {"queries": {"<query>": {"<path>": {"count": N, "last_used": "iso8601"}}}}

import inspect
import json
import math
import os
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseHandler


DEFAULT_MODULES = (
    "FreeCAD",
    "FreeCADGui",
    "Part",
    "PartDesign",
    "Sketcher",
    "Mesh",
    "MeshPart",
    "Draft",
    "Path",
    "Spreadsheet",
    "TechDraw",
)

_MAX_DOC_CHARS = 2000      # docstring truncation in inspect output
_MAX_DOC_PREVIEW = 200     # docstring truncation in search results
_MAX_MEMBERS = 200         # cap on members listed for a class/module
_MAX_SEARCH_RESULTS = 30   # cap on search hits returned
_WALK_MAX_DEPTH = 3        # how deep to recurse when collecting names

_FEEDBACK_DIR = os.path.expanduser("~/.freecad-mcp")
_FEEDBACK_FILE = os.path.join(_FEEDBACK_DIR, "introspection_feedback.json")


# ------------------------------------------------------------------
# Feedback I/O
# ------------------------------------------------------------------
def _feedback_path() -> str:
    """Override hook for tests."""
    return os.environ.get("FREECAD_MCP_FEEDBACK_FILE", _FEEDBACK_FILE)


def _load_feedback() -> Dict[str, Any]:
    path = _feedback_path()
    if not os.path.isfile(path):
        return {"queries": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"queries": {}}
        if "queries" not in data or not isinstance(data["queries"], dict):
            data["queries"] = {}
        return data
    except (OSError, json.JSONDecodeError):
        return {"queries": {}}


def _save_feedback(data: Dict[str, Any]) -> None:
    path = _feedback_path()
    parent = os.path.dirname(path)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # feedback persistence is best-effort


# ------------------------------------------------------------------
# Path resolution
# ------------------------------------------------------------------
def _resolve_path(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve a dotted path like 'Part.makeBox' to a live object.

    Returns (obj, error). On success error is None; on failure obj is None.
    """
    if not path:
        return None, "empty path"
    parts = path.split(".")
    head = parts[0]

    obj = sys.modules.get(head)
    if obj is None:
        try:
            obj = __import__(head)
        except ImportError as e:
            return None, f"could not import '{head}': {e}"

    for i, attr in enumerate(parts[1:], start=1):
        if not hasattr(obj, attr):
            return None, f"'{'.'.join(parts[:i])}' has no attribute '{attr}'"
        obj = getattr(obj, attr)
    return obj, None


def _kind_of(obj: Any) -> str:
    if inspect.ismodule(obj):
        return "module"
    if inspect.isclass(obj):
        return "class"
    if inspect.ismethod(obj) or inspect.isfunction(obj):
        return "function"
    if inspect.isbuiltin(obj) or inspect.isroutine(obj):
        return "builtin"
    return type(obj).__name__


def _short_doc(obj: Any, max_len: int = _MAX_DOC_PREVIEW) -> str:
    try:
        d = inspect.getdoc(obj) or ""
    except Exception:
        return ""
    first = d.strip().split("\n", 1)[0]
    if len(first) > max_len:
        return first[:max_len] + "…"
    return first


def _signature_str(obj: Any) -> str:
    try:
        sig = inspect.signature(obj)
        return f"{getattr(obj, '__name__', '')}{sig}"
    except (TypeError, ValueError):
        return getattr(obj, "__name__", "") + "(...)"


# ------------------------------------------------------------------
# Module walking for search
# ------------------------------------------------------------------
def _is_public(name: str) -> bool:
    return bool(name) and not name.startswith("_")


def _collect_names(
    root: Any,
    root_path: str,
    max_depth: int = _WALK_MAX_DEPTH,
) -> List[Tuple[str, str, str]]:
    """Walk a module/class tree collecting (full_path, kind, short_doc).

    Avoids infinite recursion via a visited id() set. Skips private names.
    """
    out: List[Tuple[str, str, str]] = []
    visited: set = set()

    def walk(obj: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        oid = id(obj)
        if oid in visited:
            return
        visited.add(oid)

        try:
            members = dir(obj)
        except Exception:
            return

        for name in members:
            if not _is_public(name):
                continue
            try:
                child = getattr(obj, name)
            except Exception:
                continue
            if child is obj:
                continue

            child_path = f"{path}.{name}"
            kind = _kind_of(child)

            # Only record callables, classes, and modules
            if kind in ("function", "builtin", "class", "module"):
                doc = _short_doc(child)
                out.append((child_path, kind, doc))

            # Recurse into modules and classes; do NOT recurse into instances
            if inspect.ismodule(child) or inspect.isclass(child):
                # Only recurse into modules whose __name__ starts with the root
                # to avoid wandering into arbitrary stdlib modules
                if inspect.ismodule(child):
                    cname = getattr(child, "__name__", "")
                    rname = getattr(root, "__name__", "")
                    if rname and not cname.startswith(rname):
                        continue
                walk(child, child_path, depth + 1)

    walk(root, root_path, 0)
    return out


# ------------------------------------------------------------------
# Fuzzy scoring
# ------------------------------------------------------------------
def _fuzzy_score(query: str, target_path: str) -> float:
    """Compute a 0-1 similarity score between a query and a dotted path.

    The leaf name (last component) gets the most weight; the full path is
    considered as a fallback. Exact substring matches score higher than pure
    SequenceMatcher ratios.
    """
    if not query:
        return 0.0
    q = query.lower()
    full = target_path.lower()
    leaf = full.rsplit(".", 1)[-1]

    # Exact leaf match — top score
    if leaf == q:
        return 1.0

    # Substring matches
    leaf_sub = q in leaf
    full_sub = q in full

    # SequenceMatcher ratios
    leaf_ratio = SequenceMatcher(None, q, leaf).ratio()
    full_ratio = SequenceMatcher(None, q, full).ratio()

    score = max(leaf_ratio, full_ratio * 0.85)
    if leaf_sub:
        score = max(score, 0.75)
    elif full_sub:
        score = max(score, 0.6)
    return min(1.0, score)


def _recency_decay(last_used_iso: str) -> float:
    """Return a 0.1–1.0 decay factor based on how recent last_used was.

    Half-life of 30 days; clamped to a 0.1 floor so old feedback still carries
    some signal. Returns 1.0 if the timestamp can't be parsed.
    """
    if not last_used_iso:
        return 1.0
    try:
        ts = datetime.fromisoformat(last_used_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = max(0.0, (now - ts).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return 1.0
    decay = 0.5 ** (days / 30.0)
    return max(0.1, decay)


def _feedback_boost(feedback: Dict[str, Any], query: str, path: str) -> float:
    """Multiplicative boost ≥ 1.0 for a (query, path) pair based on feedback."""
    q = (feedback.get("queries") or {}).get(query) or {}
    entry = q.get(path)
    if not entry:
        return 1.0
    count = int(entry.get("count", 0))
    if count <= 0:
        return 1.0
    decay = _recency_decay(entry.get("last_used", ""))
    return 1.0 + math.log(1.0 + count) * decay


# ------------------------------------------------------------------
# Handler
# ------------------------------------------------------------------
class IntrospectionOpsHandler(BaseHandler):
    """Live API introspection for FreeCAD's module tree, with feedback-ranked search."""

    # ------------------------------------------------------------------
    # inspect
    # ------------------------------------------------------------------
    def inspect(self, args: Dict[str, Any]) -> str:
        """Look up signature + docstring for a dotted path.

        Args:
            path — dotted path, e.g. "Part.makeBox", "Sketcher.SketchObject",
                   "FreeCAD.Vector"

        Returns JSON:
            {
              "path": "Part.makeBox",
              "kind": "function",
              "signature": "makeBox(length, width, height, ...)",
              "doc": "...",
              "members": [...]   (only for class/module)
            }
        """
        path = (args.get("path") or "").strip()
        if not path:
            return json.dumps({"error": "Missing required arg: path"})

        obj, err = _resolve_path(path)
        if err:
            return json.dumps({"error": err, "path": path})

        kind = _kind_of(obj)
        result: Dict[str, Any] = {"path": path, "kind": kind}

        # Docstring
        try:
            doc = inspect.getdoc(obj) or ""
        except Exception:
            doc = ""
        if doc and len(doc) > _MAX_DOC_CHARS:
            doc = doc[:_MAX_DOC_CHARS] + "…[truncated]"
        result["doc"] = doc

        # Signature for callables
        if kind in ("function", "builtin", "class"):
            try:
                sig = inspect.signature(obj)
                result["signature"] = f"{path.rsplit('.', 1)[-1]}{sig}"
            except (TypeError, ValueError):
                result["signature"] = None

        # Members for classes / modules
        if kind in ("class", "module"):
            members: List[Dict[str, str]] = []
            try:
                names = sorted(n for n in dir(obj) if _is_public(n))
            except Exception:
                names = []
            for name in names[:_MAX_MEMBERS]:
                try:
                    child = getattr(obj, name)
                except Exception:
                    continue
                members.append({
                    "name": name,
                    "kind": _kind_of(child),
                    "doc": _short_doc(child),
                })
            result["members"] = members
            if len(names) > _MAX_MEMBERS:
                result["members_truncated"] = True
                result["total_members"] = len(names)

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def search(self, args: Dict[str, Any]) -> str:
        """Fuzzy-search the FreeCAD API for a query string.

        Args:
            query    — search string (e.g. "make box", "fillet edge")
            modules  — optional list of module names to search (defaults to
                       FreeCAD core + workbenches)
            limit    — max results (default 30, hard cap 100)

        Returns JSON:
            {
              "query": "...",
              "scanned_modules": [...],
              "missing_modules": [...],
              "count": N,
              "results": [
                {"path": "Part.makeBox", "kind": "function",
                 "doc": "...", "score": 0.87, "feedback_boost": 1.5}
              ]
            }

        Ranking: fuzzy_score × feedback_boost, where feedback_boost grows with
        how often this query→path has been recorded as useful (decayed by
        recency).
        """
        query = (args.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "Missing required arg: query"})

        modules_arg = args.get("modules")
        if modules_arg and isinstance(modules_arg, list):
            module_names = list(modules_arg)
        else:
            module_names = list(DEFAULT_MODULES)

        try:
            limit = int(args.get("limit", _MAX_SEARCH_RESULTS))
        except (TypeError, ValueError):
            limit = _MAX_SEARCH_RESULTS
        limit = max(1, min(100, limit))

        feedback = _load_feedback()
        scanned: List[str] = []
        missing: List[Dict[str, str]] = []
        candidates: List[Tuple[str, str, str]] = []

        for mod_name in module_names:
            mod = sys.modules.get(mod_name)
            if mod is None:
                try:
                    mod = __import__(mod_name)
                except Exception as e:
                    missing.append({"module": mod_name, "reason": str(e)})
                    continue
            scanned.append(mod_name)
            # Include the module itself as a candidate
            candidates.append((mod_name, "module", _short_doc(mod)))
            try:
                candidates.extend(_collect_names(mod, mod_name))
            except Exception as e:
                missing.append({"module": mod_name, "reason": f"walk failed: {e}"})

        # Score and rank
        scored: List[Dict[str, Any]] = []
        for path, kind, doc in candidates:
            base = _fuzzy_score(query, path)
            if base <= 0.0:
                continue
            boost = _feedback_boost(feedback, query, path)
            scored.append({
                "path": path,
                "kind": kind,
                "doc": doc,
                "score": round(base * boost, 4),
                "fuzzy_score": round(base, 4),
                "feedback_boost": round(boost, 4),
            })

        scored.sort(key=lambda r: r["score"], reverse=True)
        # Filter to a reasonable threshold to avoid noise
        scored = [r for r in scored if r["score"] >= 0.35]

        return json.dumps({
            "query": query,
            "scanned_modules": scanned,
            "missing_modules": missing,
            "count": min(len(scored), limit),
            "total_matches": len(scored),
            "results": scored[:limit],
        }, indent=2)

    # ------------------------------------------------------------------
    # record_useful
    # ------------------------------------------------------------------
    def record_useful(self, args: Dict[str, Any]) -> str:
        """Record that a search query → path resolution was useful.

        Future searches with the same query rank this path higher. Call this
        after a search → inspect → successful execute_python sequence.

        Args:
            query — the original search query string
            path  — the dotted path that turned out to be useful

        Returns JSON: {"recorded": true, "query": "...", "path": "...",
                        "count": N, "last_used": "..."}
        """
        query = (args.get("query") or "").strip()
        path = (args.get("path") or "").strip()
        if not query or not path:
            return json.dumps({"error": "Both query and path are required"})

        feedback = _load_feedback()
        queries = feedback.setdefault("queries", {})
        per_query = queries.setdefault(query, {})
        entry = per_query.setdefault(path, {"count": 0, "last_used": ""})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_used"] = datetime.now(timezone.utc).isoformat()
        _save_feedback(feedback)

        return json.dumps({
            "recorded": True,
            "query": query,
            "path": path,
            "count": entry["count"],
            "last_used": entry["last_used"],
            "feedback_file": _feedback_path(),
        })

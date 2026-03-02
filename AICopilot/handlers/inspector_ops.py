# Inspector DRC handler — runs FC-tools Inspector checks against a live document.
#
# The Inspector lives in a sibling repo (FC-tools/) rather than inside the
# AICopilot module. This handler adds FC-tools to sys.path on first use,
# then imports the inspector package and runs run_drc() against the active
# (or named) document.
#
# Path resolution order:
#   1. FreeCAD preference "InspectorPath" (User Parameter → BaseApp/Preferences/Mod/AICopilot)
#   2. The env var FREECAD_INSPECTOR_PATH
#   3. Well-known sibling path: the directory two levels above this file
#      (…/AICopilot/handlers/ → …/AICopilot/ → …/freecad-mcp/ → …/FC-tools/)
#   4. /Volumes/Files/claude/FC-tools  (hardcoded fallback for dev machine)
#
# All findings are serialised to JSON-friendly dicts — no FC-tools classes
# cross the wire.

import json
import os
import sys
import FreeCAD
from typing import Dict, Any, List, Optional

from .base import BaseHandler


_FALLBACK_PATHS = [
    os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'FC-tools')),
    '/Volumes/Files/claude/FC-tools',
]


def _ensure_inspector_importable() -> str:
    """Add FC-tools to sys.path if needed. Returns the path used, or raises."""
    # 1. FreeCAD preference
    try:
        pref = FreeCAD.ParamGet('User parameter:BaseApp/Preferences/Mod/AICopilot')
        p = pref.GetString('InspectorPath', '')
        if p and os.path.isdir(p):
            if p not in sys.path:
                sys.path.insert(0, p)
            return p
    except Exception:
        pass

    # 2. Environment variable
    env = os.environ.get('FREECAD_INSPECTOR_PATH', '')
    if env and os.path.isdir(env):
        if env not in sys.path:
            sys.path.insert(0, env)
        return env

    # 3 & 4. Try well-known paths
    for path in _FALLBACK_PATHS:
        if os.path.isdir(path):
            if path not in sys.path:
                sys.path.insert(0, path)
            return path

    raise ImportError(
        "Could not locate FC-tools/inspector. "
        "Set FreeCAD preference Mod/AICopilot → InspectorPath or "
        "env var FREECAD_INSPECTOR_PATH to the FC-tools directory."
    )


def _finding_to_dict(f) -> dict:
    """Serialise a Finding to a plain dict safe for JSON."""
    return {
        "rule_id":    f.rule_id,
        "severity":   f.severity.name,   # "ERROR" / "WARNING" / "INFO"
        "objects":    list(f.objects),
        "message":    f.message,
        "value":      f.value,
        "limit":      f.limit,
        "suggestion": f.suggestion,
        "context":    dict(f.context) if f.context else {},
    }


class InspectorOpsHandler(BaseHandler):
    """Runs Inspector DRC checks against the active FreeCAD document."""

    def run(self, args: Dict[str, Any]) -> str:
        """Run Inspector DRC and return findings as JSON.

        Args (all optional):
            profile_process  — "resin" | "laser" | "cnc_3axis" (default: model-only)
            machine          — profile machine string (informational)
            profile_params   — dict of parameter overrides for process rules
            objects          — list of object names to check; default = all in doc
            doc_name         — document name; default = active document
            include_model    — bool, default True: always run model validity+robustness

        Returns JSON:
            {
              "summary": {"error": N, "warning": N, "info": N},
              "findings": [ {rule_id, severity, objects, message, ...}, ... ],
              "object_count": N,
              "checked_objects": ["Obj1", "Obj2", ...],
              "profile": "resin / AnyCubic M7 Pro" | "model-only",
              "inspector_path": "..."
            }
        """
        try:
            inspector_path = _ensure_inspector_importable()
        except ImportError as e:
            return json.dumps({"error": str(e)})

        try:
            from inspector.findings import Profile
            from inspector.runner import run_drc, _default_rules
        except ImportError as e:
            return json.dumps({"error": f"Failed to import inspector: {e}"})

        # Resolve document
        doc_name = args.get('doc_name') or ''
        if doc_name:
            doc = FreeCAD.getDocument(doc_name)
            if not doc:
                return json.dumps({"error": f"Document '{doc_name}' not found"})
        else:
            doc = FreeCAD.ActiveDocument
            if not doc:
                return json.dumps({"error": "No active document"})

        # Resolve objects
        requested_names: list = args.get('objects') or []
        if requested_names:
            objects = []
            missing = []
            for name in requested_names:
                obj = doc.getObject(name)
                if obj is None:
                    # Try label lookup
                    hits = doc.getObjectsByLabel(name)
                    obj = hits[0] if hits else None
                if obj:
                    objects.append(obj)
                else:
                    missing.append(name)
            if missing:
                return json.dumps({"error": f"Objects not found: {missing}"})
        else:
            objects = list(doc.Objects)

        # Build profile
        process = args.get('profile_process') or ''
        machine  = args.get('machine') or ''
        params   = args.get('profile_params') or {}
        if process:
            profile = Profile(process=process, machine=machine, params=params)
            profile_str = f"{process}" + (f" / {machine}" if machine else "")
        else:
            profile = None
            profile_str = "model-only"

        # Build rule list — always include model rules; add process rules if profile given
        rules = _default_rules(profile)

        # Run
        result = run_drc(objects=objects, doc=doc, profile=profile, rules=rules)

        findings_out = [_finding_to_dict(f) for f in result.findings]

        return json.dumps({
            "summary":         result.summary,
            "findings":        findings_out,
            "object_count":    len(objects),
            "checked_objects": [getattr(o, 'Name', str(o)) for o in objects],
            "profile":         profile_str,
            "inspector_path":  inspector_path,
        }, indent=2)

# Base handler class for FreeCAD MCP operations

import os
import FreeCAD
import time
from typing import Dict, Any, Optional, Callable

# Conditional GUI import (not available in console mode)
if FreeCAD.GuiUp:
    import FreeCADGui
else:
    FreeCADGui = None


def mm_min_to_mm_s(value):
    """Convert a user-supplied feed rate in mm/min to the mm/s value FreeCAD's
    App::PropertySpeed feed/rapid properties expect internally.

    Assigning a bare float to a PropertySpeed sets it directly in the
    property's base unit (mm/s), independent of the user's display unit
    schema, so this is plain arithmetic rather than a Quantity conversion.
    Single source of truth for the mm/min -> mm/s divide-by-60 previously
    hand-written at each call site in cam_tool_controllers.py and
    ocl_surface_op.py.
    """
    return float(value) / 60.0


# ---------------------------------------------------------------------------
# THE AUTOSAVE CHOKEPOINT — the one home for every UNREQUESTED document save.
#
# Two hand-written copies of "save the active document before crash-prone
# work" lived in the addon: ExecutePythonOpsHandler.run_code and
# save_before_risky_op.  Neither could be switched off, and on 2026-09-14
# that rewrote a version-controlled CAD master three times in two minutes
# while the operator was only reading it.  Members of the family now CALL
# this function; they never save on their own.  tests/unit/
# test_autosave_surface_gate.py enumerates every `.save(` on the surface and
# fails on any site the register there does not claim.
# ---------------------------------------------------------------------------
AICOPILOT_PREF_PATH = 'User parameter:BaseApp/Preferences/Mod/AICopilot'
AUTOSAVE_PREF_KEY = 'AutoSaveBeforeRiskyOp'
AUTOSAVE_DEFAULT = True   # upstream behaviour: save unless the operator switches it off

AUTOSAVE_OUTCOMES = frozenset({
    'saved',             # the document was written to its FileName
    'disabled',          # preference is off — nothing written
    'no_document',       # nothing active to save
    'unsaved_document',  # active document has no FileName — never invent a path
    'pref_unreadable',   # the preference store could not be read — nothing written
    'failed',            # save raised — reported, not swallowed
})


def _report(level: str, make_message) -> None:
    """Write to the Report View and NEVER raise — including while BUILDING the
    message.  `make_message` is a callable, so the formatting of hostile
    values (an exception whose __str__ raises, a path object whose __format__
    raises) happens inside this boundary and not in the caller's argument
    list, where it would escape before any try could see it.  A logging
    failure must not change an outcome that was already decided, and must not
    stop the caller's own work — the Report View is a witness, not a
    participant."""
    try:
        message = make_message()
    except Exception:
        message = f"[MCP] autosave: a report could not be formatted ({level})\n"
    try:
        getattr(FreeCAD.Console, level)(message)
    except Exception:
        pass


def _autosave(doc, reason: str) -> str:
    if doc is None:
        return 'no_document'
    path = getattr(doc, 'FileName', '') or ''
    if not path:
        return 'unsaved_document'
    try:
        enabled = FreeCAD.ParamGet(AICOPILOT_PREF_PATH).GetBool(AUTOSAVE_PREF_KEY, AUTOSAVE_DEFAULT)
    except Exception as e:
        _report('PrintWarning', lambda: (
            f"[MCP] autosave[{reason}]: preference {AUTOSAVE_PREF_KEY} unreadable ({e}); "
            f"NOT saving {path}\n"))
        return 'pref_unreadable'
    if not enabled:
        _report('PrintLog', lambda: f"[MCP] autosave[{reason}]: disabled; not saving {path}\n")
        return 'disabled'
    try:
        doc.save()
    except Exception as e:
        _report('PrintError', lambda: f"[MCP] autosave[{reason}]: save FAILED for {path}: {e}\n")
        return 'failed'
    _report('PrintMessage', lambda: f"[MCP] autosave[{reason}]: saved {path}\n")
    return 'saved'


def autosave_before(doc, reason: str) -> str:
    """Save `doc` before crash-prone work IF the operator allows it.

    Returns one of AUTOSAVE_OUTCOMES.  Never raises — the contract is held by
    the boundary below, not by the hope that every attribute read and every
    Report View write succeeds (a stale document wrapper raises on
    `FileName`; a detached Report View can raise on print).  Never invents a
    path for an unsaved document.  If the preference cannot be read, the
    choice is unknown, and the only irreversible act here is the write — so it
    refuses the write and says so.

    `reason` names the caller for the Report View line ("execute_python",
    "risky_op"), so an operator can tell which tool wrote a file.
    """
    try:
        return _autosave(doc, reason)
    except Exception as e:
        # Reached only by an exception OUTSIDE the save call itself (the save
        # has its own boundary above), so nothing is known to have been
        # written: `failed` is the honest outcome and the cause is named —
        # by type first, so a cause whose __str__ raises still gets a name.
        _report('PrintError', lambda: f"[MCP] autosave[{reason}]: aborted before saving — {type(e).__name__}: {e}\n")
        return 'failed'


class BaseHandler:
    """Base class for all FreeCAD operation handlers.

    Provides common utilities and document access patterns.
    """

    def __init__(self, server=None, log_operation: Optional[Callable] = None, capture_state: Optional[Callable] = None):
        """Initialize handler with optional reference to server.

        Args:
            server: Reference to FreeCADSocketServer for accessing shared resources
                   like selector, gui_task_queue, etc.
            log_operation: Debug logging function (optional)
            capture_state: State capture function (optional)
        """
        self.server = server
        self._log_operation = log_operation or self._noop_log
        self._capture_state = capture_state or self._noop_capture

    def _noop_log(self, *args, **kwargs):
        """No-op fallback if debug not available"""
        pass

    def _noop_capture(self):
        """No-op fallback if debug not available"""
        return {}

    @property
    def selector(self):
        """Access the selection manager from the server."""
        return self.server.selector if self.server else None

    def run_on_gui_thread(self, task_fn, timeout=30.0) -> str:
        """Run a callable on the Qt GUI thread via the server's tagged queue.

        Delegates to server._run_on_gui_thread which handles request ID
        tagging and stale response draining.

        Returns JSON string with result or error.
        """
        if self.server and hasattr(self.server, '_run_on_gui_thread'):
            return self.server._run_on_gui_thread(task_fn, timeout)
        # Fallback: run directly (no server or console mode)
        try:
            result = task_fn()
            return result
        except Exception as e:
            return f"Error: {e}"

    def log_and_return(self, operation: str, parameters: Dict, result: str = None, error: Exception = None, duration: float = None):
        """Helper to log operation and return result/error.

        Args:
            operation: Operation name
            parameters: Operation parameters
            result: Success result string
            error: Error exception if failed
            duration: Operation duration in seconds

        Returns:
            result string if success, error string if failed
        """
        self._log_operation(
            operation=operation,
            parameters=parameters,
            result=result,
            error=error,
            duration=duration
        )

        if error:
            # Also capture state on errors for debugging
            state = self._capture_state()
            self._log_operation(
                operation=f"{operation}_error_state",
                parameters=parameters,
                result=state
            )
            return f"Error in {operation}: {error}"
        return result

    def get_document(self) -> FreeCAD.Document:
        """Return the active FreeCAD document, or None if none is open.

        Callers that need a document must check the return value and return
        an error — never auto-create here.  Auto-creation calls
        FreeCAD.newDocument() which triggers NSWindow init on macOS and must
        only be done via view_control(operation='create_document').
        """
        return FreeCAD.ActiveDocument

    def get_object(self, object_name: str, doc: FreeCAD.Document = None):
        """Get an object by internal name or label from the document.

        Tries internal name first (fast, exact), then falls back to label
        search so callers can pass user-visible labels like "LeftTab".

        FreeCAD does NOT enforce uniqueness on Label — multiple objects can
        share the same Label, only Name is guaranteed unique.  When a label
        lookup hits multiple objects we REFUSE to guess which one was meant,
        because the previous "first match wins" behavior could silently
        perform destructive operations (move/rotate/cut) on the wrong solid.
        Callers should either pass the unique internal Name to disambiguate,
        or rename one of the objects so labels are unique.

        Args:
            object_name: Internal name or Label of the object to find
            doc: Document to search in (uses active document if not specified)

        Returns:
            FreeCAD object, or None if not found.

        Raises:
            ValueError: if `object_name` matches multiple objects by Label.
                The error message lists every candidate's internal Name so
                the caller can retry with an unambiguous identifier.  The
                surrounding handler try/except converts this into a clear
                error response for the MCP client.
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        obj = doc.getObject(object_name)
        if obj is not None:
            return obj
        # Fall back to label search
        results = doc.getObjectsByLabel(object_name)
        if not results:
            return None
        if len(results) > 1:
            names = [getattr(o, "Name", "?") for o in results]
            raise ValueError(
                f"Ambiguous label {object_name!r}: {len(results)} objects "
                f"share this label ({', '.join(names)}). "
                f"Use the internal Name to disambiguate."
            )
        return results[0]

    def resolve_object(self, object_name: str, doc: FreeCAD.Document = None,
                        attr=None, noun: str = "Object", type_id=None):
        """Resolve doc + object in one call, replacing the ~10-line
        get_document/get_object/None-check/hasattr-check preamble that was
        hand-copied at 200+ call sites across every handler file.

        Args:
            object_name: Internal name or Label of the object to find.
            doc: Document to search in (fetches the active document if
                not given — pass one in if the caller already has it and
                needs it again afterward for addObject/recompute).
            attr: Optional attribute name (or tuple of names, checked with
                OR semantics) the object must have, e.g. 'Shape' or
                ('Shape', 'Mesh'). None skips this check entirely.
            noun: The word used in the not-found/missing-attr/wrong-type
                message (e.g. "Sketch", "Spreadsheet") — callers across
                this codebase already use different nouns for the same
                shape of error, and that distinction is preserved rather
                than flattened to a single generic wording.
            type_id: Optional TypeId string (or tuple of strings, checked
                with OR semantics) the object must match, e.g.
                'App::VarSet'. None skips this check entirely. Checked
                before `attr`. Folds the `resolve_object(...); if
                obj.TypeId != '...': return f"Object {name} is not a
                {noun}"` pattern that was hand-copied at every call site
                in varset_ops.py (and, unfixed, still is in
                spreadsheet_ops.py) into the same one-call shape as the
                rest of this helper.

        Returns:
            (doc, obj, error) — error is None on success. On any failure,
            obj (and/or doc) may be None; callers should return/wrap
            `error` and not use obj further. get_object's ValueError (an
            ambiguous Label) is NOT caught here — it propagates to the
            caller's own enclosing try/except, exactly as it did before
            this helper existed.
        """
        if doc is None:
            doc = self.get_document()
        if not doc:
            return None, None, "No active document"

        obj = self.get_object(object_name, doc)
        if not obj:
            return doc, None, f"{noun} not found: {object_name}"

        if type_id is not None:
            allowed_types = (type_id,) if isinstance(type_id, str) else type_id
            if obj.TypeId not in allowed_types:
                return doc, obj, f"Object {object_name} is not a {noun}"

        if attr is not None:
            attrs = (attr,) if isinstance(attr, str) else attr
            if not any(hasattr(obj, a) for a in attrs):
                attr_desc = attrs[0] if len(attrs) == 1 else " or ".join(attrs)
                return doc, obj, f"{noun} {object_name} has no {attr_desc} property"

        return doc, obj, None

    def recompute(self, doc: FreeCAD.Document = None):
        """Recompute the document.

        Args:
            doc: Document to recompute (uses active document if not specified)
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc:
            doc.recompute()

    def find_font(self, font_file: str = '') -> str:
        """Find a usable .ttf font file, trying the given path then common system locations.

        Returns the resolved path, or '' if nothing is found.

        font_file is a caller-controlled MCP argument whose bytes get handed
        straight to a native font parser (OCCT/FreeType, via
        Part.makeWireString / Draft.make_shapestring). Unlike a plain
        os.path.exists() check, a caller-supplied path is only honored if it
        also resolves inside the shared file-path allowlist
        (_validate_file_path's safe locations) or a well-known system font
        directory (_is_allowed_font_path) -- otherwise it is treated the
        same as a nonexistent path and this function falls through to the
        bundled/candidate fonts below, rather than handing an arbitrary
        file on disk to the parser.
        """
        if font_file and self._is_allowed_font_path(font_file) and os.path.exists(font_file):
            return font_file
        # FreeCAD bundles fonts in its resource directory
        try:
            fc_fonts = os.path.join(FreeCAD.getResourceDir(), 'fonts')
            for name in ('LiberationSans-Regular.ttf', 'DejaVuSans.ttf'):
                path = os.path.join(fc_fonts, name)
                if os.path.exists(path):
                    return path
        except Exception:
            pass
        candidates = [
            '/System/Library/Fonts/Supplemental/Arial.ttf',  # macOS
            '/Library/Fonts/Arial.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',  # Linux
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/TTF/DejaVuSans.ttf',
            'C:/Windows/Fonts/arial.ttf',  # Windows
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return ''

    def save_before_risky_op(self, doc: FreeCAD.Document = None) -> str:
        """Auto-save document before a potentially crashy operation.

        Boolean operations on large compounds can crash FreeCAD.  A member of
        the autosave family: routes through `autosave_before`, which owns the
        preference and the refusals.  Returns the chokepoint's outcome.
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        return autosave_before(doc, 'risky_op')

    def check_complexity(self, objs, max_solids=500, max_faces=10000):
        """Check if objects are too complex for boolean operations.

        Returns a warning string if complexity is high, or None if OK.
        """
        total_solids = 0
        total_faces = 0
        for obj in objs:
            s = getattr(obj, 'Shape', None)
            if s is None:
                continue
            total_solids += len(s.Solids)
            total_faces += len(s.Faces)
        if total_solids > max_solids or total_faces > max_faces:
            return (f"WARNING: High complexity ({total_solids} solids, "
                    f"{total_faces} faces). Boolean operations on geometry "
                    f"this large may crash FreeCAD. Consider simplifying first.")
        return None

    def feed_to_mm_min(self, value):
        """Convert a FreeCAD feed property to a numeric value in mm/min.

        CAM feed/rapid properties (HorizFeed, VertFeed, ...) are App::PropertySpeed
        velocity Quantities whose base unit is mm/s. Reading the raw property and
        string-splitting it is fragile — the formatted string's unit depends on the
        user's unit schema (mm/s, m/s, ...), so a fixed ``* 60`` is wrong under any
        non-default schema. ``Quantity.getValueAs('mm/min')`` is exact regardless.

        Returns the mm/min value as a float, or None if it can't be interpreted.
        """
        if value is None:
            return None
        try:
            q = value if hasattr(value, 'getValueAs') else FreeCAD.Units.Quantity(value)
            return float(q.getValueAs('mm/min'))
        except Exception:
            # Last-resort fallback: assume the raw magnitude is already in mm/s.
            try:
                return float(str(value).split()[0]) * 60.0
            except Exception:
                return None

    def find_body(self, doc: FreeCAD.Document = None):
        """Find a PartDesign Body in the document.

        Args:
            doc: Document to search (uses active document if not specified)

        Returns:
            First PartDesign::Body found, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                return obj
        return None

    def find_body_for_object(self, obj, doc: FreeCAD.Document = None):
        """Find the PartDesign Body containing an object.

        Args:
            obj: Object to find the body for
            doc: Document to search (uses active document if not specified)

        Returns:
            PartDesign::Body containing the object, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for body in doc.Objects:
            if body.TypeId == "PartDesign::Body" and obj in body.Group:
                return body
        return None

    def find_assembly(self, doc: FreeCAD.Document = None):
        """Find an Assembly::AssemblyObject in the document.

        Args:
            doc: Document to search (uses active document if not specified)

        Returns:
            First Assembly::AssemblyObject found, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for obj in doc.Objects:
            if obj.TypeId == "Assembly::AssemblyObject":
                return obj
        return None

    # -----------------------------------------------------------------
    # Sketch wire diagnosis helpers
    # -----------------------------------------------------------------

    def _find_geo_for_point(self, sketch, vertex, tolerance: float = 0.5):
        """Find the geometry endpoint nearest to an open vertex.

        Iterates non-construction sketch geometry and compares each
        start/end point to *vertex* (a FreeCAD.Vector).

        Returns:
            (geo_id, pos_id, dist) tuple, or None if nothing within
            *tolerance* mm.  pos_id: 1=start, 2=end.
        """
        best = None
        best_dist = tolerance
        for i in range(sketch.GeometryCount):
            try:
                if sketch.getConstruction(i):
                    continue
                geo = sketch.Geometry[i]
                if not hasattr(geo, 'StartPoint') or not hasattr(geo, 'EndPoint'):
                    continue
                for pt, pos_id in ((geo.StartPoint, 1), (geo.EndPoint, 2)):
                    d = FreeCAD.Vector(vertex.x - pt.x,
                                      vertex.y - pt.y, 0).Length
                    if d < best_dist:
                        best_dist = d
                        best = (i, pos_id, d)
            except Exception:
                continue
        return best

    def _diagnose_open_wires(self, sketch) -> str:
        """Return an actionable diagnosis for open wire / unclosed profile.

        Combines three FreeCAD APIs:
        1. ``getOpenVertices()``  — exact XY of every dangling endpoint
        2. ``_find_geo_for_point()`` — maps each dangling point back to
           its geo_id + pos_id so the user knows which geometry to fix
        3. ``detectMissingPointOnPointConstraints()`` +
           ``getMissingPointOnPointConstraints()`` — generates the exact
           Coincident constraints needed to close the gaps

        Returns an empty string when no issues are detected.
        """
        issues = []
        open_verts = []

        # --- Step 1: find dangling endpoints ---
        # OpenVertices is a Python *property* on Sketcher::SketchObject
        # (returning a list of plain (x, y, z) tuples), not a
        # getOpenVertices() method -- confirmed live against FreeCAD 26.3:
        # dir(sketch) has no getOpenVertices attribute at all, even though
        # the C++ side (TaskSketcherValidation.cpp's onHighlightButtonClicked)
        # calls it as a real member function. Same "get-prefix stripped to a
        # property" binding pattern as App::VarSet's InListProp (see
        # feedback_python_binding_name_verification.md). Wrap each tuple in
        # a Vector since the rest of this method (and _find_geo_for_point)
        # does attribute access (.x/.y), which plain tuples don't support.
        try:
            open_verts = [FreeCAD.Vector(*v) for v in sketch.OpenVertices]
        except Exception as exc:
            issues.append(f"  (OpenVertices unavailable: {exc})")

        if open_verts:
            pos_names = {1: "start", 2: "end", 3: "center"}
            issues.append(f"{len(open_verts)} open endpoint(s) found:")
            for v in open_verts:
                match = self._find_geo_for_point(sketch, v)
                if match:
                    gid, pid, dist = match
                    gap = f" (gap {dist:.5f} mm)" if dist > 1e-6 else ""
                    pname = pos_names.get(pid, str(pid))
                    issues.append(
                        f"  • geo_id={gid} {pname}-point at "
                        f"({v.x:.4f}, {v.y:.4f}){gap}"
                    )
                else:
                    issues.append(
                        f"  • Dangling point at ({v.x:.4f}, {v.y:.4f})"
                        " — no matching geometry found within 0.5 mm"
                    )

        # --- Step 2: suggest Coincident constraints to close the gaps ---
        # detectMissingPointOnPointConstraints() takes no parameters in this
        # binding (precision is hardcoded to Precision::Confusion() inside
        # the C++ implementation) -- confirmed live; the previous precision=/
        # includeconstruction= kwargs raised TypeError on every call, silently
        # swallowed by the except below, so this block never actually ran.
        # It also returns None, not a count -- read the count from the
        # MissingPointOnPointConstraints property afterward instead, the same
        # order the native ValidateSketch dialog uses (detect, then read).
        #
        # Each item is a plain 5-tuple (First, FirstPos, Second, SecondPos,
        # Type) -- confirmed live against a constructed near-miss pair, not a
        # ConstraintIds object with .First/.FirstPos attributes as previously
        # assumed (that would raise AttributeError: 'tuple' object has no
        # attribute 'First'). PointPos: 1=start, 2=end.
        try:
            sketch.detectMissingPointOnPointConstraints()
            pairs = sketch.MissingPointOnPointConstraints
            if pairs:
                issues.append(f"\n{len(pairs)} suggested fix(es):")
                for c in pairs:
                    first, first_pos, second, second_pos = c[0], c[1], c[2], c[3]
                    issues.append(
                        f"  sketch_operations(operation=\"add_constraint\","
                        f" constraint_type=\"Coincident\","
                        f" sketch_name=\"{sketch.Name}\","
                        f" geo_id1={first}, pos_id1={first_pos},"
                        f" geo_id2={second}, pos_id2={second_pos})"
                    )
                issues.append(
                    "  (or fix all at once: "
                    f"execute_python(code=\"FreeCAD.ActiveDocument.{sketch.Name}"
                    ".makeMissingPointOnPointCoincident(); "
                    f"FreeCAD.ActiveDocument.recompute()\"))"
                )
        except Exception:
            # Graceful degradation for older FC builds
            pass

        return "\n".join(issues)

    def _find_upstream_sketches(self, obj, _visited=None, _sketches=None, _depth=0):
        """Walk obj.OutList recursively, collecting every Sketcher::SketchObject
        ancestor reachable in the dependency graph.

        A single sketch can be reached through many independent OutList paths
        (e.g. several features each importing external geometry from the same
        master sketch) -- _visited dedupes by object Name so it's returned
        once regardless of how many paths lead to it. _depth caps recursion
        (50) as a defensive bound against a document with a pathological
        dependency graph; real FreeCAD documents never approach that.

        Returns a list of Sketcher::SketchObject, in first-encountered order.
        """
        if _visited is None:
            _visited = set()
        if _sketches is None:
            _sketches = []
        if _depth > 50 or obj is None or obj.Name in _visited:
            return _sketches
        _visited.add(obj.Name)

        if obj.TypeId == 'Sketcher::SketchObject':
            _sketches.append(obj)
            # A sketch's own OutList (e.g. external-geometry references to
            # another master sketch) can still lead to further sketches
            # upstream of it -- keep walking rather than stopping here.

        for upstream in getattr(obj, 'OutList', []):
            self._find_upstream_sketches(upstream, _visited, _sketches, _depth + 1)

        return _sketches

    def _sketch_health_check(self, sketch) -> str:
        """Run every FreeCAD-provided sketch validity check against *sketch*
        and return one structured, actionable report.

        This is the model-layer API the native "Validate Sketch" dialog
        (TaskSketcherValidation.cpp) itself calls one button at a time --
        detectMissingPointOnPointConstraints, OpenVertices,
        evaluateConstraints, detectDegeneratedGeometries -- plus two checks
        that exist at the model layer but the native dialog never wires up at
        all: detectMissingVerticalHorizontalConstraints and
        detectMissingEqualityConstraints. Every method name here was verified
        live against a running FreeCAD instance before use (several of the
        "obvious" C++-derived names turned out to be Python properties
        instead, or to take different arguments than their C++ callers use --
        see _diagnose_open_wires above for the specifics).

        Reversed external-geometry arcs are NOT checked here: FreeCAD's
        port_reversedExternalArcs has no Python binding at all (confirmed
        live), so neither detection nor fix is reachable outside the native
        GUI's Validate Sketch dialog.
        """
        import Part

        lines = [f"Health check for {sketch.Name}" +
                 (f" ({sketch.Label})" if sketch.Label != sketch.Name else "") + ":"]

        geo_count = sketch.GeometryCount
        con_count = sketch.ConstraintCount
        lines.append(f"  Geometry: {geo_count} elements, Constraints: {con_count}")

        dof = sketch.solve()
        if dof == 0:
            lines.append("  Fully constrained: Yes")
        elif dof > 0:
            lines.append(f"  Under-constrained: {dof} degree(s) of freedom remaining")
        else:
            lines.append("  Over-constrained or conflicting constraints")

        problems_found = False

        # --- Open/unclosed wire (the check this repo has the most history
        # with -- see reference_revolve_shell_validity_open_wire.md) ---
        open_diag = self._diagnose_open_wires(sketch)
        if open_diag.strip():
            problems_found = True
            lines.append("\nOpen wire / unclosed profile:")
            lines.append(open_diag)
        else:
            lines.append("\nOpen wire / unclosed profile: none found")

        # --- Invalid constraints ---
        try:
            if sketch.evaluateConstraints():
                lines.append("\nInvalid constraints: none found")
            else:
                problems_found = True
                lines.append("\nInvalid constraints: found")
                lines.append(
                    f"  Fix: execute_python(code=\"FreeCAD.ActiveDocument.{sketch.Name}"
                    ".validateConstraints(); "
                    f"FreeCAD.ActiveDocument.recompute()\")"
                )
        except Exception as exc:
            lines.append(f"\nInvalid constraints: check unavailable ({exc})")

        # --- Degenerate geometry ---
        try:
            tol = Part.Precision.confusion()
            count = sketch.detectDegeneratedGeometries(tol)
            if count == 0:
                lines.append("\nDegenerate geometry: none found")
            else:
                problems_found = True
                lines.append(f"\nDegenerate geometry: {count} found")
                lines.append(
                    f"  Fix: execute_python(code=\"FreeCAD.ActiveDocument.{sketch.Name}"
                    f".removeDegeneratedGeometries({tol}); "
                    f"FreeCAD.ActiveDocument.recompute()\")"
                )
        except Exception as exc:
            lines.append(f"\nDegenerate geometry: check unavailable ({exc})")

        # --- Missing Vertical/Horizontal constraints (not in the native
        # Validate Sketch dialog at all -- model-layer-only check) ---
        try:
            sketch.detectMissingVerticalHorizontalConstraints()
            vh = sketch.MissingVerticalHorizontalConstraints
            if not vh:
                lines.append("\nMissing Vertical/Horizontal constraints: none found")
            else:
                problems_found = True
                lines.append(f"\nMissing Vertical/Horizontal constraints: {len(vh)} found")
                lines.append(
                    f"  Fix: execute_python(code=\"FreeCAD.ActiveDocument.{sketch.Name}"
                    ".makeMissingVerticalHorizontal(); "
                    f"FreeCAD.ActiveDocument.recompute()\")"
                )
        except Exception as exc:
            lines.append(f"\nMissing Vertical/Horizontal constraints: check unavailable ({exc})")

        # --- Missing equality constraints (line-length and radius; also not
        # in the native dialog) ---
        try:
            sketch.detectMissingEqualityConstraints()
            eq_lines = sketch.MissingLineEqualityConstraints
            eq_radii = sketch.MissingRadiusConstraints
            total = len(eq_lines) + len(eq_radii)
            if total == 0:
                lines.append("\nMissing equality constraints: none found")
            else:
                problems_found = True
                lines.append(
                    f"\nMissing equality constraints: {len(eq_lines)} line-length, "
                    f"{len(eq_radii)} radius"
                )
                lines.append(
                    f"  Fix: execute_python(code=\"FreeCAD.ActiveDocument.{sketch.Name}"
                    ".makeMissingEquality(); "
                    f"FreeCAD.ActiveDocument.recompute()\")"
                )
        except Exception as exc:
            lines.append(f"\nMissing equality constraints: check unavailable ({exc})")

        lines.append(f"\nVerdict: {'PROBLEMS FOUND' if problems_found else 'CLEAN'}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_path(path: str) -> str:
        """Canonicalize a caller-supplied path the same way for every
        allowlist check in this class: expand ~, make absolute, resolve
        symlinks -- so every check compares against the same normal form.
        """
        return os.path.realpath(os.path.abspath(os.path.expanduser(path)))

    @staticmethod
    def _is_under_any_prefix(resolved: str, prefixes: list) -> bool:
        """True if `resolved` equals or is nested under one of `prefixes`.

        Single source of truth for the "is this path inside an allowed
        directory" check -- both _validate_file_path (general file I/O)
        and _is_allowed_font_path (read-only font lookup) consume this
        rather than each re-implementing the same resolve+match logic,
        which had drifted into two near-identical copies.
        """
        return any(
            resolved == p or resolved.startswith(p + os.sep) or resolved.startswith(p + "/")
            for p in prefixes
        )

    # Prefixes considered outside any user-writable area on common platforms.
    # Allowlist approach: only home dir, /tmp, and platform-specific temp dirs
    # are permitted for file I/O operations.
    @staticmethod
    def _validate_file_path(path: str) -> "Optional[str]":
        """Return an error string if path is outside safe user-writable locations, else None.

        Safe locations: user home directory, /tmp/, /var/folders/ (macOS),
        /var/tmp/, and /Volumes/ (macOS external/network drives).
        On Windows: home dir and the system temp directory.
        """
        import sys as _sys
        if not path:
            return "file path is required"
        resolved = BaseHandler._resolve_path(path)
        home = os.path.realpath(os.path.expanduser("~"))

        safe: list = [home]
        if _sys.platform == "win32":
            import tempfile as _tmp
            safe.append(os.path.realpath(_tmp.gettempdir()))
        else:
            # Resolve each prefix so symlinks (e.g. /tmp -> /private/tmp on macOS) match.
            safe += [os.path.realpath(p) for p in ("/tmp", "/var/folders", "/var/tmp", "/Volumes")]

        if BaseHandler._is_under_any_prefix(resolved, safe):
            return None
        return (
            f"Path is outside allowed directories (home dir, /tmp, /Volumes). "
            f"Resolved path: {resolved}"
        )

    # System font directories that are legitimate places to load a *font*
    # from but fall outside _validate_file_path's general file-I/O allowlist
    # (that allowlist intentionally excludes system-wide, non-user-writable
    # areas like /System or /usr for things like save/export paths). A font
    # is read-only input to a native parser, not a write target, so these
    # well-known, non-user-writable system font locations are safe to add
    # here without loosening _validate_file_path itself.
    _FONT_SYSTEM_DIRS = (
        "/System/Library/Fonts",       # macOS
        "/Library/Fonts",              # macOS
        "/usr/share/fonts",            # Linux
        "/usr/local/share/fonts",      # Linux
        "C:/Windows/Fonts",            # Windows
    )

    @staticmethod
    def _is_allowed_font_path(path: str) -> bool:
        """Return True if a caller-supplied font_file resolves somewhere
        legitimate to load a font from: the general file-path allowlist
        (home dir, /tmp, /var/folders, /Volumes -- see _validate_file_path,
        which already covers a user's own per-user font directories such as
        ~/Library/Fonts or ~/.fonts since they live under the home dir) or a
        well-known, non-user-writable system font directory
        (_FONT_SYSTEM_DIRS).

        This is the gate that keeps find_font() from handing an arbitrary
        file anywhere on disk to the native font parser (OCCT/FreeType) --
        without it, any file the FreeCAD process can read could be probed
        or fed to that parser via the font_file argument.
        """
        if not path:
            return False
        resolved = BaseHandler._resolve_path(path)
        safe = [os.path.realpath(p) for p in BaseHandler._FONT_SYSTEM_DIRS]
        if BaseHandler._is_under_any_prefix(resolved, safe):
            return True
        return BaseHandler._validate_file_path(path) is None

    def _check_feature_state(self, feature, feature_label: str, sketch=None) -> Optional[str]:
        """Return a diagnostic error string if feature.State contains
        'Invalid' after recompute(), else None.

        recompute() never raises on geometry failure — it marks the
        feature Invalid instead — so every feature-creating method must
        check this explicitly or risk reporting success for a broken
        feature. Originally added only to PartDesignOpsHandler; moved here
        (H13) so Part::Loft/Part::Sweep in PartOpsHandler get the same
        check instead of duplicating this method verbatim.
        """
        state = getattr(feature, 'State', [])
        if 'Invalid' not in state:
            return None
        err = f"{feature_label} created but failed to compute (State=Invalid)."
        if sketch is not None:
            diagnosis = self._diagnose_open_wires(sketch)
            if diagnosis:
                err += f"\n\nSketch wire diagnosis:\n{diagnosis}"
        return err

    def bind_expression(self, object_name: str, property_name: str,
                         target_name: str, target_ref: str,
                         target_noun: str = "Object", validate_target=None) -> str:
        """Bind an object's property to a named target's property/cell via
        a FreeCAD expression (obj.setExpression + recompute).

        Shared by every handler whose "bind to a named container" operation
        reduces to this same shape — currently VarSetOpsHandler.bind_property
        and SpreadsheetOpsHandler.bind_property, which used to each hand-copy
        the resolve/setExpression/recompute/message sequence verbatim.

        Args:
            object_name: Object whose property will be bound.
            property_name: Property on object_name to bind.
            target_name: The VarSet/Spreadsheet/etc. being bound to.
            target_ref: The property name / cell / alias on the target,
                used to build the expression string as
                f"{target_name}.{target_ref}".
            target_noun: Passed through to resolve_object's `noun` for the
                target lookup's not-found message (e.g. "VarSet",
                "Spreadsheet").
            validate_target: Optional callable (doc, target) -> Optional[str]
                error, run after the target is resolved but before the
                expression is set — lets each caller apply its own
                type-specific validation (e.g. a VarSet TypeId + property-
                existence check) without a second resolve_object() round
                trip.

        Returns:
            Success or error message string, in the same shape every other
            handler method here returns.
        """
        doc, obj, err = self.resolve_object(object_name)
        if err:
            return err

        _, target, err = self.resolve_object(target_name, doc, noun=target_noun)
        if err:
            return err

        if not property_name:
            return "property_name is required"

        if validate_target is not None:
            val_err = validate_target(doc, target)
            if val_err:
                return val_err

        expression = f"{target_name}.{target_ref}"
        obj.setExpression(property_name, expression)
        self.recompute(doc)

        # setExpression() doesn't validate the reference until recompute,
        # and a failed recompute doesn't raise -- it marks the feature's
        # State Invalid instead. Without this check, a bad target_ref would
        # report unconditional success.
        state_err = self._check_feature_state(obj, f"{object_name}.{property_name}")
        if state_err:
            return f"Error: expression bound but {state_err}"

        return f"Bound {object_name}.{property_name} to {expression}"

    def create_body_if_needed(self, doc: FreeCAD.Document = None):
        """Create a PartDesign Body if one doesn't exist.

        If no document exists, creates one via GUI thread to avoid GIL deadlock.

        Args:
            doc: Document to create body in (uses active document if not specified)

        Returns:
            Existing or newly created PartDesign::Body
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None

        body = self.find_body(doc)
        if not body:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
        return body

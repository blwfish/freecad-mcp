"""`get_usage_guidance` MCP tool.

Thin wiring of `mcp-agent-notes` into freecad-mcp's raw-SDK dispatch idiom,
mirroring kicad-mcp's and jmri-mcp's usage_guidance.py (this repo uses the
low-level `mcp.server.Server` API, not FastMCP, so there's no
`register_usage_guidance_tools(mcp)` decorator step -- freecad_mcp_server.py
wires the tool schema and the `elif name == "get_usage_guidance"` dispatch
branch directly, calling `query()` below).

`NOTES` is the durable, authored knowledge this project wants any connecting
assistant to know without rediscovering it live -- `SERVER_INSTRUCTIONS` in
freecad_mcp_server.py renders a bounded slice of it into the `initialize`
handshake, and `get_usage_guidance` is the query-tool fallback for clients
that drop that field (confirmed for LM Studio, see AGENT-INSTALL.md), plus
the full-detail/topic/search surface `instructions` deliberately can't hold
(see render_instructions()'s own docstring: it must stay small regardless of
corpus size).
"""

from __future__ import annotations

from datetime import date

from mcp_agent_notes import Note, NoteKind, Priority, find, strategy, tactics

CAPABILITY_STATEMENT = (
    "freecad-mcp: direct control of a live, running FreeCAD instance for "
    "parametric CAD modeling -- sketches, solids, booleans, assemblies, CAM "
    "toolpaths -- not a headless geometry library. Most work goes through a "
    "dedicated tool method (sketch_operations, partdesign_operations, "
    "part_operations, assembly_operations, measurement_operations, etc.); "
    "execute_python is the escape hatch for genuine one-offs and debugging."
)

QUERY_TOOL_NAME = "get_usage_guidance"

# Dated to this migration (installing mcp-agent-notes) rather than backdated
# to when each rule first appeared in CLAUDE.md/AGENT-INSTALL.md/
# AGENT-DEBUGGING.md/the old inline get_usage_guidance payload -- `added`
# drives render_instructions()'s "recently added" surfacing, and these are
# newly-structured entries even though the underlying lessons aren't new.
_MIGRATED = date(2026, 9, 22)

NOTES: tuple[Note, ...] = (
    Note(
        id="always-check-connection-first",
        added=_MIGRATED,
        priority=Priority.CRITICAL,
        kind=NoteKind.TACTIC,
        summary=(
            "Call check_freecad_connection() before any other operation -- "
            "confirms FreeCAD is running with AICopilot loaded before "
            "anything else can fail confusingly."
        ),
        tags=(),
        addresses=(
            "cannot connect to FreeCAD",
            "no response from FreeCAD",
            "socket error",
            "first call of a new session",
        ),
    ),
    Note(
        id="prefer-primary-tool-over-execute-python",
        added=_MIGRATED,
        priority=Priority.CRITICAL,
        kind=NoteKind.TACTIC,
        summary=(
            "Don't use execute_python as a substitute for a dedicated tool "
            "method that already does the job -- e.g. don't call "
            "Part.extrude() via raw execute_python when "
            "partdesign_operations already has a pad/extrude operation."
        ),
        detail=(
            "Primary methods carry validation, error handling, and "
            "GUI-thread dispatch safety that ad-hoc execute_python calls "
            "bypass. execute_python is for genuine one-offs and debugging "
            "-- inspecting live state, printing to the Python console, "
            "anything inherently ad hoc that shouldn't get a dedicated "
            "method. If a primary method exists, use it. If one doesn't "
            "exist but the operation is a real, recurring need (not a "
            "one-off), that's a signal to consider adding a primary method "
            "to this repo rather than making execute_python the permanent "
            "path for it."
        ),
        tags=(),
        addresses=(
            "should I use execute_python",
            "which tool do I call",
            "no dedicated tool for this operation",
        ),
    ),
    Note(
        id="find-root-cause-not-symptom",
        added=_MIGRATED,
        priority=Priority.CRITICAL,
        kind=NoteKind.TACTIC,
        summary=(
            "When a boolean, CAM, or export operation fails on an object "
            "built from several upstream features, don't diagnose the "
            "object where the symptom appeared -- call "
            'measurement_operations(operation="find_root_cause", '
            "object_name=<that object>) instead."
        ),
        detail=(
            "It walks that object's own dependency subtree (not the whole "
            "document), checks every sketch (open/unclosed wires) and every "
            "shape-bearing feature (null shape, topological validity, "
            "shell-vs-solid, and the same boolean-operation check FreeCAD's "
            "native Check Geometry dialog uses) independently, and reports "
            "which object actually introduced each defect versus which "
            "ones are just inheriting it downstream.\n\n"
            "check_solid and Shape.isValid() on a Part::Compound (or any "
            "multi-child container) can report clean even when one child "
            "is a Shell instead of a Solid, or when siblings only touch at "
            "a seam without being fused -- both checks ask whether a Solid "
            "exists somewhere in the shape, not whether every child "
            "actually is one. find_root_cause checks each child "
            "independently instead.\n\n"
            "Confirmed empirically, not just by reading the code: tested "
            "without this guidance present, a model defaults to "
            "check_solid/isValid on the symptom object every time, which "
            "is the specific case known to report false-clean results. "
            "Also use sketch_operations(operation=\"verify_sketch\") to "
            "catch an open-wire sketch before it ever reaches a "
            "Pad/Revolution."
        ),
        tags=("measurement", "debugging"),
        addresses=(
            "boolean operation failed",
            "CAM operation failed",
            "export failed",
            "check_solid reports clean but shape is bad",
            "Part::Compound false clean",
            "isValid returns true but geometry is broken",
        ),
        location="AGENT-DEBUGGING.md",
    ),
    Note(
        id="create-document-before-objects",
        added=_MIGRATED,
        priority=Priority.HIGH,
        kind=NoteKind.TACTIC,
        summary=(
            'Create a document with view_control(operation="create_document") '
            "in its own call, before creating objects in it -- keeps "
            "operation sequencing clear even though the historical "
            "GIL-deadlock risk this protected against is now fixed at the "
            "code level."
        ),
        detail=(
            "FreeCAD.newDocument() called off the GUI thread used to "
            "deadlock the Qt GUI; that's fixed as of get_document() in "
            "AICopilot/handlers/base.py (confirmed 2026-09-18: a live "
            "headless test firing both calls back-to-back with no wait "
            "didn't hang, and every .recompute() call site routes through "
            "the same GUI-thread dispatch chokepoint, execute_python "
            "included). Skipping this rule today gets a clean \"No active "
            "document\" error, not a crash -- but there's no reason to "
            "rely on that instead of just sequencing the calls."
        ),
        tags=("document",),
        addresses=(
            "no active document",
            "document creation order",
            "GIL deadlock",
        ),
    ),
    Note(
        id="interactive-selection-workflow",
        added=_MIGRATED,
        priority=Priority.HIGH,
        kind=NoteKind.TACTIC,
        summary=(
            "Fillet, chamfer, and hole operations can't programmatically "
            'select edges/faces -- when partdesign_operations returns '
            '{"status": "awaiting_selection"}, the user must click in '
            "FreeCAD's 3D view, then you call "
            "continue_selection(operation_id=...). Don't proceed without "
            "that confirmation."
        ),
        tags=("partdesign", "selection"),
        addresses=(
            "awaiting_selection",
            "fillet needs selection",
            "how do I select edges",
            "continue_selection",
        ),
    ),
    Note(
        id="undo-redo-inert-use-checkpoint",
        added=_MIGRATED,
        priority=Priority.HIGH,
        kind=NoteKind.TACTIC,
        summary=(
            'view_control(operation="undo"/"redo") reports success but is '
            "effectively inert for MCP-driven operations -- use checkpoint "
            "/ rollback_to_checkpoint instead; that mechanism actually "
            "works."
        ),
        detail=(
            "undo/redo just call doc.undo()/doc.redo(), which only replay "
            "real FreeCAD GUI transactions. Nothing in the dispatch path "
            "(freecad_mcp_handler.py) ever opens a transaction around a "
            "handler call -- that only happens automatically for real "
            "GUI-driven edits -- so both report success whether or not "
            "anything was actually undone/redone. Use "
            'view_control(operation="checkpoint", name=...) before a risky '
            'feature and view_control(operation="rollback_to_checkpoint", '
            "name=...) to revert instead."
        ),
        tags=("undo",),
        addresses=(
            "undo did nothing",
            "redo did nothing",
            "how do I revert a change",
            "rollback",
        ),
    ),
    Note(
        id="large-documents-pagination",
        added=_MIGRATED,
        priority=Priority.MEDIUM,
        kind=NoteKind.TACTIC,
        summary=(
            "list_objects paginates (default 100, max 500). For documents "
            "with 1000+ objects (e.g. DXF imports), use the offset, limit, "
            "and type_filter parameters rather than assuming one call "
            "returns everything."
        ),
        tags=("document",),
        addresses=(
            "list_objects missing objects",
            "large document slow",
            "DXF import too many objects",
        ),
    ),
    Note(
        id="varset-api-quirks",
        added=_MIGRATED,
        priority=Priority.MEDIUM,
        kind=NoteKind.TACTIC,
        summary=(
            "Two App::VarSet API behaviors look like they'd raise but "
            "don't: obj.removeProperty(name) silently returns False for "
            "locked/built-in properties instead of raising, and assigning "
            "a bad int index to a PropertyEnumeration silently no-ops "
            "instead of raising (only a bad string assignment raises)."
        ),
        detail=(
            "Confirmed by reading the live FC-clone source, not recalled "
            "from memory. removeProperty's two Base::RuntimeError throws "
            "live behind an early guard that returns False before either "
            "is ever reached from Python -- varset_operations therefore "
            "checks getPropertyStatus(name) for the PropDynamic/"
            "LockDynamic bits before calling removeProperty, rather than "
            "wrapping the call in a try/except. PropertyEnumeration's "
            "int-assignment branch has no else for the invalid-index case "
            "-- varset_operations always assigns the default value by its "
            "string form, never by raw index, so an ordering bug fails "
            "loudly instead of doing nothing."
        ),
        tags=("varset",),
        addresses=(
            "removeProperty didn't work",
            "VarSet property still there",
            "enum assignment silently ignored",
        ),
    ),
    Note(
        id="workflow-overview",
        added=_MIGRATED,
        priority=Priority.HIGH,
        kind=NoteKind.STRATEGY,
        summary=(
            "Typical flow: check_freecad_connection -> "
            "view_control(create_document) -> sketch_operations(create -> "
            "add geometry -> add_constraint -> close_sketch -> "
            "verify_sketch) -> partdesign_operations(pad) or "
            "part_operations booleans -> measurement/spatial_query to "
            "verify -> assembly_operations for multi-part joints."
        ),
        detail=(
            "check_freecad_connection() -> "
            'view_control(operation="create_document") -> '
            'sketch_operations(operation="create_sketch", plane="XY") -> '
            'sketch_operations(operation="add_rectangle"/"add_line"/'
            '"add_circle"/...) -> sketch_operations(operation='
            '"add_constraint", constraint_type=...) -> sketch_operations('
            'operation="close_sketch") -> sketch_operations(operation='
            '"verify_sketch") -> partdesign_operations(operation="pad") '
            '-> partdesign_operations(operation="fillet"/"chamfer"/'
            '"shell"/"hole") for features (see interactive-selection-'
            'workflow) -> view_control(operation="fit_all") -> '
            'view_control(operation="screenshot"). For multi-part designs: '
            "assembly_operations(create_assembly/create_lcs/add_component/"
            "create_joint/ground_part/solve)."
        ),
        tags=(),
        addresses=(
            "where do I start",
            "how do I model a part",
            "workflow order",
        ),
    ),
)


def query(
    operation: str = "strategy",
    *,
    topic: str | None = None,
    problem: str | None = None,
) -> str:
    """Query onboarding knowledge: known gotchas, best practices, workflow.

    Backs the `get_usage_guidance` MCP tool (wired in freecad_mcp_server.py).
    Call get_usage_guidance() (operation="strategy", no topic) once at the
    start of a session -- costs nothing, no side effects. Exists as a
    schema-visible fallback because some MCP clients (confirmed for LM
    Studio) silently drop this server's `instructions` field on the
    initialize handshake.

    Operations:
      strategy(topic=None) -> tiered overview of high-level guidance:
          full detail for universal/always-relevant notes, one-liners for
          topic-scoped ones. strategy(topic="varset") -> full detail for
          that one topic, regardless of relevance state.
      tactics(topic=None) -> list of tactical topics available.
          tactics(topic="document") -> full detail for that topic.
      find(problem="...") -> notes ranked by relevance to a free-text
          problem description (both strategy and tactic notes).
    """
    if operation == "strategy":
        return strategy(NOTES, topic=topic, active_topics=frozenset())
    if operation == "tactics":
        return tactics(NOTES, topic=topic)
    if operation == "find":
        if not problem:
            return "error: operation='find' requires 'problem'"
        return find(NOTES, problem)
    return f"error: unknown operation {operation!r}; valid: strategy|tactics|find"

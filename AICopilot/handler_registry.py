# Single source of truth for {attr_name: HandlerClass name}.
#
# freecad_mcp_handler.py uses this to instantiate handlers at startup and to
# reload them on hot-reload (see _build_handler_class_map). Test fixtures
# that stub out `handlers` as a module of MagicMocks (test_freecad_mcp_
# handler.py, test_instance_manager.py) import it too, so the class-name
# list can't drift out of sync with the real registry the way it used to --
# a handler added to one but not the other would silently only work until
# the next hot-reload (freecad_mcp_handler.py) or fail every dispatch test
# with a confusing AttributeError (the test fixtures).
#
# Deliberately zero imports beyond nothing at all: freecad_mcp_handler.py
# needs this available before its own `import FreeCAD` / `import handlers`
# (which the real handlers package requires), and the test fixtures need it
# available before they install their own FreeCAD/handlers mocks into
# sys.modules -- either of those failing to import first is exactly the
# chicken-and-egg problem this module exists to route around.

_HANDLER_CLASS_NAMES = {
    'primitives': 'PrimitivesHandler',
    'boolean_ops': 'BooleanOpsHandler',
    'transforms': 'TransformsHandler',
    'sketch_ops': 'SketchOpsHandler',
    'partdesign_ops': 'PartDesignOpsHandler',
    'part_ops': 'PartOpsHandler',
    'cam_ops': 'CAMOpsHandler',
    'cam_tools': 'CAMToolsHandler',
    'cam_tool_controllers': 'CAMToolControllersHandler',
    'draft_ops': 'DraftOpsHandler',
    'measurement_ops': 'MeasurementOpsHandler',
    'spreadsheet_ops': 'SpreadsheetOpsHandler',
    'mesh_ops': 'MeshOpsHandler',
    'spatial_ops': 'SpatialOpsHandler',
    'inspector_ops': 'InspectorOpsHandler',
    'macro_ops': 'MacroOpsHandler',
    'introspection_ops': 'IntrospectionOpsHandler',
    'sketch_builder_ops': 'SketchBuilderOpsHandler',
    'verification_ops': 'VerificationOpsHandler',
    'fixture_ops': 'FixtureOpsHandler',
    'diagnostics_ops': 'DiagnosticsOpsHandler',
    'execute_python_ops': 'ExecutePythonOpsHandler',
    'assembly_ops': 'AssemblyOpsHandler',
    'varset_ops': 'VarSetOpsHandler',
    # GUI-sensitive handlers get the task queues for thread safety
    # (see freecad_mcp_handler.py's _instantiate_handlers) -- listed last
    # only to mirror the historical dict order; position carries no
    # behavioral meaning.
    'view_ops': 'ViewOpsHandler',
    'document_ops': 'DocumentOpsHandler',
}

# The handlers whose constructors take the server's GUI task queues
# (cls(server, gui_task_queue, gui_response_queue, log, capture)); every
# other handler is cls(server, log, capture). The registry states WHICH,
# next to WHAT exists, so the server never carries its own copy of the
# family; tests/unit/test_handler_registry_gate.py derives the expected
# kind from each class's own __init__ signature and fails on a mismatch.
_GUI_SENSITIVE = frozenset({'view_ops', 'document_ops'})


def module_names():
    """Fully qualified module names in registry order: attr_name maps 1:1
    onto handlers.<attr_name>. The reload iterates this; the derivation
    lives here so the server never spells a `handlers.<x>` path itself."""
    return tuple(f'handlers.{attr}' for attr in _HANDLER_CLASS_NAMES)

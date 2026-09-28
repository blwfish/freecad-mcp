# FreeCAD MCP Handlers
# Modular operation handlers for the socket server

from .assembly_ops import AssemblyOpsHandler
from .base import BaseHandler
from .boolean_ops import BooleanOpsHandler
from .cam_ops import CAMOpsHandler
from .cam_tool_controllers import CAMToolControllersHandler
from .cam_tools import CAMToolsHandler
from .diagnostics_ops import DiagnosticsOpsHandler
from .document_ops import DocumentOpsHandler
from .draft_ops import DraftOpsHandler
from .execute_python_ops import ExecutePythonOpsHandler
from .fixture_ops import FixtureOpsHandler
from .inspector_ops import InspectorOpsHandler
from .introspection_ops import IntrospectionOpsHandler
from .macro_ops import MacroOpsHandler
from .measurement_ops import MeasurementOpsHandler
from .mesh_ops import MeshOpsHandler
from .part_ops import PartOpsHandler
from .partdesign_ops import PartDesignOpsHandler
from .primitives import PrimitivesHandler
from .sketch_builder_ops import SketchBuilderOpsHandler
from .sketch_ops import SketchOpsHandler
from .spatial_ops import SpatialOpsHandler
from .spreadsheet_ops import SpreadsheetOpsHandler
from .transforms import TransformsHandler
from .varset_ops import VarSetOpsHandler
from .verification_ops import VerificationOpsHandler
from .view_ops import ViewOpsHandler

__all__ = [
    'BaseHandler',
    'PrimitivesHandler',
    'BooleanOpsHandler',
    'TransformsHandler',
    'SketchOpsHandler',
    'PartDesignOpsHandler',
    'PartOpsHandler',
    'CAMOpsHandler',
    'CAMToolsHandler',
    'CAMToolControllersHandler',
    'DraftOpsHandler',
    'ViewOpsHandler',
    'DocumentOpsHandler',
    'MeasurementOpsHandler',
    'SpreadsheetOpsHandler',
    'MeshOpsHandler',
    'SpatialOpsHandler',
    'InspectorOpsHandler',
    'MacroOpsHandler',
    'IntrospectionOpsHandler',
    'SketchBuilderOpsHandler',
    'VerificationOpsHandler',
    'FixtureOpsHandler',
    'DiagnosticsOpsHandler',
    'ExecutePythonOpsHandler',
    'AssemblyOpsHandler',
    'VarSetOpsHandler',
]

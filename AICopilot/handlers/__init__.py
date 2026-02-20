# FreeCAD MCP Handlers
# Modular operation handlers for the socket server

from .base import BaseHandler
from .primitives import PrimitivesHandler
from .boolean_ops import BooleanOpsHandler
from .transforms import TransformsHandler
from .sketch_ops import SketchOpsHandler
from .partdesign_ops import PartDesignOpsHandler
from .part_ops import PartOpsHandler
from .cam_ops import CAMOpsHandler
from .cam_tools import CAMToolsHandler
from .cam_tool_controllers import CAMToolControllersHandler
from .draft_ops import DraftOpsHandler
from .view_ops import ViewOpsHandler
from .document_ops import DocumentOpsHandler
from .measurement_ops import MeasurementOpsHandler
from .spreadsheet_ops import SpreadsheetOpsHandler
from .mesh_ops import MeshOpsHandler

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
]

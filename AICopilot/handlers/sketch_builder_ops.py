"""
SketchBuilder MCP handler — validates + emits parametric FreeCAD sketches
via the FC-tools SketchBuilder library (python-solvespace pre-validation).

Environment requirements (already met in the FreeCAD pixi env):
- FC-tools on sys.path before importing sketch_builder
- python-solvespace installed in the FreeCAD Python env
- `import Part` must happen before sketch_builder emitter is imported
"""

import json
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict

FC_TOOLS_PATH = '/Volumes/Files/claude/FC-tools'


def _ensure_sketch_builder_importable() -> None:
    """Add FC-tools to sys.path and ensure Part is imported first."""
    if FC_TOOLS_PATH not in sys.path:
        sys.path.insert(0, FC_TOOLS_PATH)
    # Part must be imported before sketch_builder's emitter module
    import Part  # noqa: F401


def _read_spreadsheet_params(doc, spreadsheet_name: str) -> Dict[str, float]:
    """Read all aliased cells from a spreadsheet, return {alias: float_value}."""
    ss = doc.getObject(spreadsheet_name)
    if ss is None:
        raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found in document")

    params: Dict[str, float] = {}
    try:
        tree = ET.fromstring(ss.Content)
    except ET.ParseError as e:
        raise ValueError(f"Could not parse spreadsheet XML: {e}") from e

    for cell in tree.findall('.//Cell'):
        alias = cell.get('alias', '')
        if not alias:
            continue
        try:
            params[alias] = float(ss.get(alias))
        except Exception:
            pass  # skip non-numeric cells
    return params


def _apply_layout(sb, layout: Dict[str, Any]) -> None:
    """Walk layout['elements'] and call the corresponding sb.add_* methods."""
    for el in layout.get('elements', []):
        t = el.get('type')
        if t == 'envelope':
            sb.add_envelope(
                width=el.get('width', 'width'),
                height=el.get('height', 'eaveHeight'),
            )
        elif t == 'hline':
            sb.add_hline(y=el['y'], name=el.get('name', 'hline'))
        elif t == 'arch':
            sb.add_arch(
                cx_expr=el['cx'],
                sill_expr=el['sill'],
                spring_expr=el['spring'],
                radius_expr=el['radius'],
                name=el.get('name', 'arch'),
            )
        elif t == 'arch_array':
            cx_template = el['cx']
            base_name = el.get('name', 'arch')
            for i in range(el['count']):
                sb.add_arch(
                    cx_expr=cx_template.replace('{i}', str(i)),
                    sill_expr=el['sill'],
                    spring_expr=el['spring'],
                    radius_expr=el['radius'],
                    name=f"{base_name}_{i}",
                )
        elif t == 'door':
            sb.add_door(
                left_x_expr=el['left_x'],
                spring_expr=el['spring'],
                width_expr=el['width'],
                name=el.get('name', 'door'),
                floor_ref=el.get('floor_ref', 'floor'),
            )
        elif t == 'monitor':
            sb.add_monitor(
                width=el['width'],
                height=el['height'],
                cx=el['cx'],
                base_y=el['base_y'],
                name=el.get('name', 'monitor'),
            )
        else:
            raise ValueError(f"Unknown element type: {t!r}")


class SketchBuilderOpsHandler:
    """Handles the build_sketch MCP tool."""

    def __init__(self, server=None, log_operation=None, capture_state=None):
        self.server = server
        self._log_operation = log_operation or (lambda *a, **kw: None)
        self._capture_state = capture_state or (lambda: {})

    def build_sketch(self, args: Dict[str, Any]) -> str:
        """
        Validate and emit a parametric sketch from a JSON layout descriptor.

        Args (from MCP):
            layout        dict  — elements array describing the sketch geometry
            sketch_name   str   — name for the sketch object (default "Master XZ")
            placement     str   — sketch plane: "XY", "XZ", or "YZ" (default "XZ")
            spreadsheet   str   — FreeCAD object name of param spreadsheet (default "Spreadsheet")
        """
        import FreeCAD

        layout = args.get('layout')
        if not isinstance(layout, dict):
            return json.dumps({'ok': False, 'error': 'layout must be a dict'})

        sketch_name = args.get('sketch_name', 'Master XZ')
        placement = args.get('placement', 'XZ')
        spreadsheet_name = args.get('spreadsheet', 'Spreadsheet')

        doc = FreeCAD.ActiveDocument
        if doc is None:
            return json.dumps({'ok': False, 'error': 'No active FreeCAD document'})

        try:
            _ensure_sketch_builder_importable()
            from sketch_builder import SketchBuilder  # noqa: E402

            params = _read_spreadsheet_params(doc, spreadsheet_name)
            sb = SketchBuilder(params)
            _apply_layout(sb, layout)

            report = sb.validate()
            if not report.ok:
                return json.dumps({
                    'ok': False,
                    'conflicts': report.conflicts,
                    'under_constrained': report.under_constrained,
                })

            sb.emit(doc, sketch_name=sketch_name, placement=placement)

            sk = doc.getObject(sketch_name)
            geo_count = sk.GeometryCount if sk else 0
            constraint_count = len(sk.Constraints) if sk else 0

            return json.dumps({
                'ok': True,
                'dof': report.dof,
                'geo': geo_count,
                'constraints': constraint_count,
            })

        except Exception as e:
            import traceback
            return json.dumps({'ok': False, 'error': str(e), 'traceback': traceback.format_exc()})

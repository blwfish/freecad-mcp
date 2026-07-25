# CAM Tool Management Handler for FreeCAD MCP

import FreeCAD
import time
from typing import Dict, Any, List
from .base import BaseHandler


class CAMToolsHandler(BaseHandler):
    """Handler for CAM tool library operations (CRUD)."""

    _ALLOWED_OPERATIONS = frozenset({
        "create_tool", "list_tools", "get_tool", "update_tool", "delete_tool",
    })

    def create_tool(self, args: Dict[str, Any]) -> str:
        """Create a new tool in the tool library.

        Args:
            name: Tool name
            tool_type: Type of tool (endmill, ballend, bullnose, chamfer, drill, etc.)
                Required -- an omitted/empty value is rejected rather than
                silently defaulting, so a caller can't end up with a tool of
                a different physical shape than intended with no signal.
            diameter: Tool diameter in mm
            flute_length: Cutting length in mm (optional)
            shank_diameter: Shank diameter in mm (optional)
            material: Tool material (HSS, Carbide, etc.) (optional)
            number_of_flutes: Number of flutes (optional)
            length: Total tool length in mm (optional)
            tip_angle: Tip angle in degrees -- drill/vbit/chamfer-type shapes
                (optional)
            cutting_edge_angle: Cutting edge angle in degrees -- chamfer-type
                shapes (optional)
            flat_radius: Flat radius in mm -- bullnose-type shapes (optional)
            corner_radius: Corner radius in mm -- bullnose/radius-type shapes
                (optional)
            neck_diameter: Neck diameter in mm -- necked tool shapes (optional)
            neck_length: Neck length in mm -- necked tool shapes (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            # FreeCAD 1.2+ uses new toolbit structure
            try:
                from Path.Tool.Bit import ToolBit
            except ImportError:
                return "Error: Path.Tool module not available. Requires FreeCAD 1.2+"

            name = args.get('name', '')
            tool_type = args.get('tool_type')
            if not tool_type:
                return "Error: tool_type parameter required (e.g. 'endmill', 'ballend', 'drill')"
            diameter = args.get('diameter', 6.0)
            flute_length = args.get('flute_length', None)
            shank_diameter = args.get('shank_diameter', None)
            material = args.get('material', 'Carbide')
            number_of_flutes = args.get('number_of_flutes', None)
            length = args.get('length', None)
            tip_angle = args.get('tip_angle', None)
            cutting_edge_angle = args.get('cutting_edge_angle', None)
            flat_radius = args.get('flat_radius', None)
            corner_radius = args.get('corner_radius', None)
            neck_diameter = args.get('neck_diameter', None)
            neck_length = args.get('neck_length', None)

            if not name:
                name = f"{tool_type}_{diameter}mm"

            # Valid shape IDs in FC 1.2 (no .fcstd extension in ShapeID attribute)
            shape_map = {
                'endmill': 'endmill',
                'ballend': 'ballend',
                'bullnose': 'bullnose',
                'chamfer': 'chamfer',
                'drill': 'drill',
                'vbit': 'vbit',
                'v-bit': 'vbit',
                'dovetail': 'dovetail',
                'probe': 'probe',
                'slittingsaw': 'slittingsaw',
                'reamer': 'reamer',
                'tap': 'tap',
                'threadmill': 'threadmill',
                # Shipped FC 1.2 shapes the map previously rejected; stem == the
                # shipped <name>.fcstd file, matching the working 'endmill' entry.
                'radius': 'radius',
                'taperedballnose': 'taperedballnose',
            }

            shape_id = shape_map.get(tool_type.lower())
            if not shape_id:
                valid_types = ', '.join(shape_map.keys())
                return f"Error: Unknown tool type '{tool_type}'. Valid types: {valid_types}"

            doc = self.get_document()
            if not doc:
                return "Error: No active document to attach tool"

            # ToolBit.from_dict() hands "parameter" values straight to
            # PathUtil.setProperty() (Path/Tool/toolbit/models/base.py's
            # from_shape()) -- plain scalars, the same shape to_dict()'s
            # round-trip produces via to_json() (FreeCAD.Units.Quantity ->
            # .UserString) and the same shape update_tool() below already
            # uses. An earlier {"type": ..., "value": ...} wrapper here was
            # never unwrapped by this code path -- it reached
            # Base::Quantity's setter as a raw dict, which
            # QuantityPyImp.cpp rejects ("Either quantity, float with
            # units or string expected").
            parameters = {
                "Diameter": f"{diameter} mm",
            }
            # `is not None`, not truthiness: a legitimately-supplied 0 must not be
            # silently dropped from the tool definition.
            if flute_length is not None:
                parameters["CuttingEdgeHeight"] = f"{flute_length} mm"
            if shank_diameter is not None:
                parameters["ShankDiameter"] = f"{shank_diameter} mm"
            if number_of_flutes is not None:
                parameters["Flutes"] = number_of_flutes
            if material:
                parameters["Material"] = material
            # Shape-specific geometry -- previously had no caller-facing path
            # at all, permanently unreachable through both create_tool and
            # update_tool even though get_tool reads all seven back. Most of
            # shape_map's 15 shapes (bullnose, chamfer, vbit, dovetail,
            # drill, reamer, tap, threadmill, taperedballnose) are only
            # geometrically distinguishable from a generic endmill/ballend by
            # one or more of these (full-review 2026-07-24 finding #04).
            if length is not None:
                parameters["Length"] = f"{length} mm"
            if tip_angle is not None:
                parameters["TipAngle"] = f"{tip_angle} deg"
            if cutting_edge_angle is not None:
                parameters["CuttingEdgeAngle"] = f"{cutting_edge_angle} deg"
            if flat_radius is not None:
                parameters["FlatRadius"] = f"{flat_radius} mm"
            if corner_radius is not None:
                parameters["CornerRadius"] = f"{corner_radius} mm"
            if neck_diameter is not None:
                parameters["NeckDiameter"] = f"{neck_diameter} mm"
            if neck_length is not None:
                parameters["NeckLength"] = f"{neck_length} mm"

            tool_dict = {
                "version": 2,
                "name": name,
                "shape": shape_id,
                "attribute": {},
                "parameter": parameters,
            }

            tool_bit = ToolBit.from_dict(tool_dict)
            if not tool_bit:
                return f"Error: Could not create tool from dict for shape '{shape_id}'"

            tool_obj = tool_bit.attach_to_doc(doc=doc)

            # ToolBit.from_dict()'s return is only checked for truthiness --
            # it can accept a dict but silently drop or default an
            # individual requested parameter (e.g. a shape that doesn't
            # support NeckDiameter) with no error. Surface that instead of
            # reporting unconditional success (full-review 2026-07-24
            # finding #27).
            dropped = [prop for prop in parameters if not hasattr(tool_obj, prop)]

            result = f"Created tool '{name}' ({tool_type}, diameter: {diameter}mm) as {tool_obj.Label}"
            if dropped:
                result += (f" -- WARNING: shape '{shape_id}' does not support: "
                            f"{', '.join(dropped)} (silently ignored)")
            return self.log_and_return("create_tool", args, result=result, duration=time.time() - start_time)

        except ImportError as e:
            return self.log_and_return("create_tool", args, error=e, duration=time.time() - start_time)
        except Exception as e:
            return self.log_and_return("create_tool", args, error=e, duration=time.time() - start_time)

    def list_tools(self, args: Dict[str, Any]) -> str:
        """List all tools in the tool library.

        Returns:
            Formatted list of tools with details
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            # Find all tool bits in the document
            # In FreeCAD 1.2+, tool bits are Part::FeaturePython with a ToolBit proxy
            tools = []
            for obj in doc.Objects:
                if obj.TypeId == "Part::FeaturePython" and hasattr(obj, 'ShapeID'):
                    tools.append(obj)

            if not tools:
                result = "No tools found in document. Use create_tool to add tools."
                return self.log_and_return("list_tools", args, result=result, duration=time.time() - start_time)

            result = f"Found {len(tools)} tool(s):\n"
            for i, tool in enumerate(tools, 1):
                diameter = tool.Diameter if hasattr(tool, 'Diameter') else 'N/A'
                tool_type = tool.BitShape if hasattr(tool, 'BitShape') else 'unknown'
                result += f"  {i}. {tool.Label} ({tool_type}, D={diameter})\n"

            return self.log_and_return("list_tools", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("list_tools", args, error=e, duration=time.time() - start_time)

    def get_tool(self, args: Dict[str, Any]) -> str:
        """Get detailed information about a specific tool.

        Args:
            tool_name: Name of the tool to inspect

        Returns:
            Detailed tool information
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("get_tool", args, error=error, duration=time.time() - start_time)

            tool_name = args.get('tool_name', '')
            if not tool_name:
                error = Exception("tool_name parameter required")
                return self.log_and_return("get_tool", args, error=error, duration=time.time() - start_time)

            tool = self.get_object(tool_name, doc)
            if not tool:
                error = Exception(f"Tool '{tool_name}' not found")
                return self.log_and_return("get_tool", args, error=error, duration=time.time() - start_time)

            # In FreeCAD 1.2+, tool bits are Part::FeaturePython with ShapeID attribute
            if not hasattr(tool, 'ShapeID'):
                error = Exception(f"Object '{tool_name}' is not a tool bit (no ShapeID attribute)")
                return self.log_and_return("get_tool", args, error=error, duration=time.time() - start_time)

            # Collect tool details
            result = f"Tool: {tool.Label}\n"
            result += f"  Type: {tool.BitShape if hasattr(tool, 'BitShape') else 'unknown'}\n"
            result += f"  Diameter: {tool.Diameter if hasattr(tool, 'Diameter') else 'N/A'}\n"

            if hasattr(tool, 'CuttingEdgeHeight'):
                result += f"  Flute Length: {tool.CuttingEdgeHeight}\n"
            if hasattr(tool, 'ShankDiameter'):
                result += f"  Shank Diameter: {tool.ShankDiameter}\n"
            if hasattr(tool, 'Flutes'):
                result += f"  Number of Flutes: {tool.Flutes}\n"
            if hasattr(tool, 'Length'):
                result += f"  Total Length: {tool.Length}\n"
            # Material + shape-specific geometry (present only on certain bit
            # shapes); emit whichever exist instead of dropping them silently.
            for prop, label in (("Material", "Material"), ("TipAngle", "Tip Angle"),
                                ("CuttingEdgeAngle", "Cutting Edge Angle"),
                                ("FlatRadius", "Flat Radius"), ("CornerRadius", "Corner Radius"),
                                ("NeckDiameter", "Neck Diameter"), ("NeckLength", "Neck Length")):
                if hasattr(tool, prop):
                    result += f"  {label}: {getattr(tool, prop)}\n"

            return self.log_and_return("get_tool", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("get_tool", args, error=e, duration=time.time() - start_time)

    def update_tool(self, args: Dict[str, Any]) -> str:
        """Update parameters of an existing tool.

        Args:
            tool_name: Name of the tool to update
            diameter: New diameter (optional)
            flute_length: New flute length (optional)
            shank_diameter: New shank diameter (optional)
            number_of_flutes: New number of flutes (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            tool_name = args.get('tool_name', '')
            if not tool_name:
                return "Error: tool_name parameter required"

            tool = self.get_object(tool_name, doc)
            if not tool:
                return f"Error: Tool '{tool_name}' not found"

            if not hasattr(tool, 'ShapeID'):
                return f"Error: Object '{tool_name}' is not a tool bit (no ShapeID attribute)"

            # Update parameters if provided
            updates = []

            if 'diameter' in args:
                tool.Diameter = f"{args['diameter']} mm"
                updates.append(f"diameter: {args['diameter']}mm")

            if 'flute_length' in args:
                tool.CuttingEdgeHeight = f"{args['flute_length']} mm"
                updates.append(f"flute_length: {args['flute_length']}mm")

            if 'shank_diameter' in args:
                tool.ShankDiameter = f"{args['shank_diameter']} mm"
                updates.append(f"shank_diameter: {args['shank_diameter']}mm")

            if 'number_of_flutes' in args:
                tool.Flutes = args['number_of_flutes']
                updates.append(f"flutes: {args['number_of_flutes']}")

            if 'material' in args:
                tool.Material = args['material']
                updates.append(f"material: {args['material']}")

            if not updates:
                error = Exception("No parameters to update. Provide diameter, flute_length, shank_diameter, number_of_flutes, or material.")
                return self.log_and_return("update_tool", args, error=error, duration=time.time() - start_time)

            self.recompute(doc)
            result = f"Updated tool '{tool_name}': {', '.join(updates)}"
            return self.log_and_return("update_tool", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("update_tool", args, error=e, duration=time.time() - start_time)

    def delete_tool(self, args: Dict[str, Any]) -> str:
        """Delete a tool from the library.

        Args:
            tool_name: Name of the tool to delete

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            tool_name = args.get('tool_name', '')
            if not tool_name:
                return "Error: tool_name parameter required"

            tool = self.get_object(tool_name, doc)
            if not tool:
                return f"Error: Tool '{tool_name}' not found"

            if not hasattr(tool, 'ShapeID'):
                return f"Error: Object '{tool_name}' is not a tool bit (no ShapeID attribute)"

            # Check if tool is in use by any tool controllers
            in_use = []
            for obj in doc.Objects:
                if hasattr(obj, 'SpindleSpeed'):  # FC 1.2: tool controllers are Path::FeaturePython
                    if hasattr(obj, 'Tool') and obj.Tool == tool:
                        in_use.append(obj.Label)

            if in_use:
                error = Exception(f"Cannot delete tool '{tool_name}' - it is used by tool controller(s): {', '.join(in_use)}")
                return self.log_and_return("delete_tool", args, error=error, duration=time.time() - start_time)

            doc.removeObject(tool.Name)
            result = f"Deleted tool '{tool_name}'"
            return self.log_and_return("delete_tool", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("delete_tool", args, error=e, duration=time.time() - start_time)

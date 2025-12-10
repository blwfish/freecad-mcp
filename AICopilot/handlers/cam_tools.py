# CAM Tool Management Handler for FreeCAD MCP

import FreeCAD
import time
from typing import Dict, Any, List
from .base import BaseHandler


class CAMToolsHandler(BaseHandler):
    """Handler for CAM tool library operations (CRUD)."""

    def create_tool(self, args: Dict[str, Any]) -> str:
        """Create a new tool in the tool library.

        Args:
            name: Tool name
            tool_type: Type of tool (endmill, ballend, bullnose, chamfer, drill, etc.)
            diameter: Tool diameter in mm
            flute_length: Cutting length in mm (optional)
            shank_diameter: Shank diameter in mm (optional)
            material: Tool material (HSS, Carbide, etc.) (optional)
            number_of_flutes: Number of flutes (optional)

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
            tool_type = args.get('tool_type', 'endmill')
            diameter = args.get('diameter', 6.0)
            flute_length = args.get('flute_length', None)
            shank_diameter = args.get('shank_diameter', None)
            material = args.get('material', 'Carbide')
            number_of_flutes = args.get('number_of_flutes', None)

            if not name:
                name = f"{tool_type}_{diameter}mm"

            # Map tool_type to shape_id (.fcstd extension required in FC 1.2)
            shape_map = {
                'endmill': 'endmill.fcstd',
                'ballend': 'ballend.fcstd',
                'bullnose': 'bullnose.fcstd',
                'chamfer': 'chamfer.fcstd',
                'drill': 'drill.fcstd',
                'vbit': 'vbit.fcstd',
                'v-bit': 'vbit.fcstd',
                'dovetail': 'dovetail.fcstd',
                'probe': 'probe.fcstd',
                'slittingsaw': 'slittingsaw.fcstd',
                'reamer': 'reamer.fcstd',
                'tap': 'tap.fcstd',
                'threadmill': 'threadmill.fcstd'
            }

            shape_id = shape_map.get(tool_type.lower())
            if not shape_id:
                valid_types = ', '.join(shape_map.keys())
                return f"Error: Unknown tool type '{tool_type}'. Valid types: {valid_types}"

            # Create tool bit using from_shape_id (FreeCAD 1.2+ API)
            tool_bit = ToolBit.from_shape_id(shape_id, label=name)

            if not tool_bit:
                return f"Error: Could not create tool with shape_id '{shape_id}'"

            # Set basic parameters
            tool_bit.label = name
            tool_bit.set_property("Diameter", f"{diameter} mm")

            # Set optional parameters if provided
            if flute_length:
                tool_bit.set_property("CuttingEdgeHeight", f"{flute_length} mm")
            if shank_diameter:
                tool_bit.set_property("ShankDiameter", f"{shank_diameter} mm")
            if number_of_flutes and hasattr(tool_bit, 'Flutes'):
                tool_bit.set_property("Flutes", number_of_flutes)

            # Attach to active document
            doc = self.get_document()
            if not doc:
                return "Error: No active document to attach tool"

            tool_obj = tool_bit.attach_to_doc(doc=doc)

            # Note: Material is typically set in tool controller, not the bit itself

            result = f"Created tool '{name}' ({tool_type}, diameter: {diameter}mm) as {tool_obj.Label}"
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
            # FreeCAD 1.0+ uses new module structure
            try:
                from Path.Tool.Library import ToolLibrary
            except ImportError:
                return "Error: Path.Tool module not available. Requires FreeCAD 1.0+"

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            # Find all tool bits in the document
            tools = []
            for obj in doc.Objects:
                if obj.TypeId == "Path::ToolBit":
                    tools.append(obj)

            if not tools:
                result = "No tools found in document. Use create_tool to add tools."
                return self.log_and_return("list_tools", args, result=result, duration=time.time() - start_time)

            result = f"Found {len(tools)} tool(s):\n"
            for i, tool in enumerate(tools, 1):
                diameter = tool.Diameter if hasattr(tool, 'Diameter') else 'N/A'
                tool_type = tool.BitShape if hasattr(tool, 'BitShape') else 'unknown'
                result += f"  {i}. {tool.Label} ({tool_type}, ⌀{diameter})\n"

            return self.log_and_return("list_tools", args, result=result, duration=time.time() - start_time)

        except ImportError as e:
            return self.log_and_return("list_tools", args, error=e, duration=time.time() - start_time)
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

            if tool.TypeId != "Path::ToolBit":
                error = Exception(f"Object '{tool_name}' is not a tool bit (type: {tool.TypeId})")
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

            if tool.TypeId != "Path::ToolBit":
                return f"Error: Object '{tool_name}' is not a tool bit"

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

            if not updates:
                error = Exception("No parameters to update. Provide diameter, flute_length, shank_diameter, or number_of_flutes.")
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

            if tool.TypeId != "Path::ToolBit":
                return f"Error: Object '{tool_name}' is not a tool bit"

            # Check if tool is in use by any tool controllers
            in_use = []
            for obj in doc.Objects:
                if obj.TypeId == "Path::ToolController":
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

# CAM Tool Controller Management Handler for FreeCAD MCP

import FreeCAD
import time
from typing import Dict, Any
from .base import BaseHandler


class CAMToolControllersHandler(BaseHandler):
    """Handler for CAM tool controller operations (CRUD).

    Tool controllers link tools to specific jobs with operating parameters
    like spindle speed, feed rate, etc.
    """

    def add_tool_controller(self, args: Dict[str, Any]) -> str:
        """Add a tool controller to a CAM job.

        Args:
            job_name: Name of the CAM job
            tool_name: Name of the tool bit to use
            controller_name: Name for the tool controller (optional)
            spindle_speed: Spindle speed in RPM (optional, default: 10000)
            feed_rate: Horizontal feed rate in mm/min (optional, default: 1000)
            vertical_feed_rate: Vertical (plunge) feed rate in mm/min (optional)
            tool_number: Tool number for G-code (optional, default: 1)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            # FreeCAD 1.0+ uses new module structure
            try:
                from Path.Tool.Controller import Create as CreateController
            except ImportError:
                error = Exception("Path.Tool module not available. Requires FreeCAD 1.0+")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            tool_name = args.get('tool_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)
            if not tool_name:
                error = Exception("tool_name parameter required")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            # Get the job
            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            # Get the tool bit
            tool = self.get_object(tool_name, doc)
            if not tool:
                error = Exception(f"Tool '{tool_name}' not found")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            # In FreeCAD 1.2+, tool bits are Part::FeaturePython
            if tool.TypeId != "Part::FeaturePython" or not (hasattr(tool, 'ShapeID') or hasattr(tool, 'ToolBitID')):
                error = Exception(f"Object '{tool_name}' is not a tool bit")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            # Create tool controller
            controller_name = args.get('controller_name', f"TC_{tool_name}")
            controller = CreateController(controller_name)

            # Link to tool bit
            controller.Tool = tool

            # Set parameters
            spindle_speed = args.get('spindle_speed', 10000)
            feed_rate = args.get('feed_rate', 1000)
            vertical_feed_rate = args.get('vertical_feed_rate', feed_rate // 2)
            tool_number = args.get('tool_number', 1)

            controller.SpindleSpeed = spindle_speed
            controller.HorizFeed = feed_rate
            controller.VertFeed = vertical_feed_rate
            controller.ToolNumber = tool_number

            # Add to job's tool controllers
            if hasattr(job, 'Tools'):
                job.Tools.Group += [controller]
            else:
                error = Exception(f"Job '{job_name}' does not support tool controllers")
                return self.log_and_return("add_tool_controller", args, error=error, duration=time.time() - start_time)

            self.recompute(doc)
            result = f"Added tool controller '{controller.Label}' to job '{job_name}' (Tool: {tool_name}, Speed: {spindle_speed} RPM, Feed: {feed_rate} mm/min)"
            return self.log_and_return("add_tool_controller", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("add_tool_controller", args, error=e, duration=time.time() - start_time)

    def list_tool_controllers(self, args: Dict[str, Any]) -> str:
        """List all tool controllers in a CAM job.

        Args:
            job_name: Name of the CAM job

        Returns:
            Formatted list of tool controllers with details
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("list_tool_controllers", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("list_tool_controllers", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("list_tool_controllers", args, error=error, duration=time.time() - start_time)

            # Get tool controllers
            if not hasattr(job, 'Tools'):
                error = Exception(f"Job '{job_name}' does not have tool controllers")
                return self.log_and_return("list_tool_controllers", args, error=error, duration=time.time() - start_time)

            controllers = job.Tools.Group if hasattr(job.Tools, 'Group') else []

            if not controllers:
                result = f"No tool controllers found in job '{job_name}'. Use add_tool_controller to add one."
                return self.log_and_return("list_tool_controllers", args, result=result, duration=time.time() - start_time)

            result = f"Tool controllers in job '{job_name}' ({len(controllers)}):\n"
            for i, tc in enumerate(controllers, 1):
                tool_name = tc.Tool.Label if hasattr(tc, 'Tool') and tc.Tool else 'None'
                speed = tc.SpindleSpeed if hasattr(tc, 'SpindleSpeed') else 'N/A'
                feed = tc.HorizFeed if hasattr(tc, 'HorizFeed') else 'N/A'
                tool_num = tc.ToolNumber if hasattr(tc, 'ToolNumber') else 'N/A'

                result += f"  {i}. {tc.Label} (T{tool_num})\n"
                result += f"     Tool: {tool_name}\n"
                result += f"     Speed: {speed} RPM, Feed: {feed} mm/min\n"

            return self.log_and_return("list_tool_controllers", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("list_tool_controllers", args, error=e, duration=time.time() - start_time)

    def get_tool_controller(self, args: Dict[str, Any]) -> str:
        """Get detailed information about a specific tool controller.

        Args:
            job_name: Name of the CAM job
            controller_name: Name of the tool controller

        Returns:
            Detailed tool controller information
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            controller_name = args.get('controller_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)
            if not controller_name:
                error = Exception("controller_name parameter required")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)

            controller = self.get_object(controller_name, doc)
            if not controller:
                error = Exception(f"Tool controller '{controller_name}' not found")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)

            if controller.TypeId != "Path::ToolController":
                error = Exception(f"Object '{controller_name}' is not a tool controller")
                return self.log_and_return("get_tool_controller", args, error=error, duration=time.time() - start_time)

            # Collect details
            result = f"Tool Controller: {controller.Label}\n"

            if hasattr(controller, 'Tool') and controller.Tool:
                result += f"  Tool: {controller.Tool.Label}\n"
                if hasattr(controller.Tool, 'Diameter'):
                    result += f"  Tool Diameter: {controller.Tool.Diameter}\n"
            else:
                result += f"  Tool: None\n"

            if hasattr(controller, 'ToolNumber'):
                result += f"  Tool Number (T): {controller.ToolNumber}\n"
            if hasattr(controller, 'SpindleSpeed'):
                result += f"  Spindle Speed: {controller.SpindleSpeed} RPM\n"
            if hasattr(controller, 'SpindleDir'):
                result += f"  Spindle Direction: {controller.SpindleDir}\n"
            if hasattr(controller, 'HorizFeed'):
                result += f"  Horizontal Feed: {controller.HorizFeed} mm/min\n"
            if hasattr(controller, 'VertFeed'):
                result += f"  Vertical Feed: {controller.VertFeed} mm/min\n"
            if hasattr(controller, 'HorizRapid'):
                result += f"  Horizontal Rapid: {controller.HorizRapid} mm/min\n"
            if hasattr(controller, 'VertRapid'):
                result += f"  Vertical Rapid: {controller.VertRapid} mm/min\n"

            return self.log_and_return("get_tool_controller", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("get_tool_controller", args, error=e, duration=time.time() - start_time)

    def update_tool_controller(self, args: Dict[str, Any]) -> str:
        """Update parameters of an existing tool controller.

        Args:
            job_name: Name of the CAM job
            controller_name: Name of the tool controller
            spindle_speed: New spindle speed in RPM (optional)
            feed_rate: New horizontal feed rate in mm/min (optional)
            vertical_feed_rate: New vertical feed rate in mm/min (optional)
            tool_number: New tool number (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            controller_name = args.get('controller_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)
            if not controller_name:
                error = Exception("controller_name parameter required")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)

            controller = self.get_object(controller_name, doc)
            if not controller:
                error = Exception(f"Tool controller '{controller_name}' not found")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)

            if controller.TypeId != "Path::ToolController":
                error = Exception(f"Object '{controller_name}' is not a tool controller")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)

            # Update parameters if provided
            updates = []

            if 'spindle_speed' in args:
                controller.SpindleSpeed = args['spindle_speed']
                updates.append(f"spindle_speed: {args['spindle_speed']} RPM")

            if 'feed_rate' in args:
                controller.HorizFeed = args['feed_rate']
                updates.append(f"feed_rate: {args['feed_rate']} mm/min")

            if 'vertical_feed_rate' in args:
                controller.VertFeed = args['vertical_feed_rate']
                updates.append(f"vertical_feed_rate: {args['vertical_feed_rate']} mm/min")

            if 'tool_number' in args:
                controller.ToolNumber = args['tool_number']
                updates.append(f"tool_number: T{args['tool_number']}")

            if not updates:
                error = Exception("No parameters to update. Provide spindle_speed, feed_rate, vertical_feed_rate, or tool_number.")
                return self.log_and_return("update_tool_controller", args, error=error, duration=time.time() - start_time)

            self.recompute(doc)
            result = f"Updated tool controller '{controller_name}': {', '.join(updates)}"
            return self.log_and_return("update_tool_controller", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("update_tool_controller", args, error=e, duration=time.time() - start_time)

    def remove_tool_controller(self, args: Dict[str, Any]) -> str:
        """Remove a tool controller from a CAM job.

        Args:
            job_name: Name of the CAM job
            controller_name: Name of the tool controller to remove

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            controller_name = args.get('controller_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)
            if not controller_name:
                error = Exception("controller_name parameter required")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            controller = self.get_object(controller_name, doc)
            if not controller:
                error = Exception(f"Tool controller '{controller_name}' not found")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            if controller.TypeId != "Path::ToolController":
                error = Exception(f"Object '{controller_name}' is not a tool controller")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            # Check if tool controller is in use by any operations
            in_use = []
            if hasattr(job, 'Operations'):
                for op in job.Operations.Group:
                    if hasattr(op, 'ToolController') and op.ToolController == controller:
                        in_use.append(op.Label)

            if in_use:
                error = Exception(f"Cannot remove tool controller '{controller_name}' - it is used by operation(s): {', '.join(in_use)}")
                return self.log_and_return("remove_tool_controller", args, error=error, duration=time.time() - start_time)

            # Remove from job's tool controllers
            if hasattr(job, 'Tools'):
                controllers = list(job.Tools.Group)
                if controller in controllers:
                    controllers.remove(controller)
                    job.Tools.Group = controllers

            # Delete the controller object
            doc.removeObject(controller.Name)
            result = f"Removed tool controller '{controller_name}' from job '{job_name}'"
            return self.log_and_return("remove_tool_controller", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("remove_tool_controller", args, error=e, duration=time.time() - start_time)

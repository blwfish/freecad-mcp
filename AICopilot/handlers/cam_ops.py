# CAM workbench operation handlers for FreeCAD MCP

import FreeCAD
import time
from typing import Dict, Any
from .base import BaseHandler


class CAMOpsHandler(BaseHandler):
    """Handler for CAM (Path) workbench operations."""

    def create_job(self, args: Dict[str, Any]) -> str:
        """Create a new CAM Job."""
        try:
            # FreeCAD 1.0+ uses new module structure
            from Path.Main.Job import Create as CreateJob
            try:
                from Path.Main.Gui.Job import ViewProvider
                has_gui = True
            except ImportError:
                has_gui = False

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('name', 'Job')
            base_object = args.get('base_object', '')

            job = CreateJob(job_name, [], None)

            # Set up ViewProvider for GUI mode
            if has_gui and hasattr(job, 'ViewObject'):
                job.ViewObject.Proxy = ViewProvider(job.ViewObject)

            if base_object:
                obj = self.get_object(base_object, doc)
                if obj:
                    job.Model.Group = [obj]
                    job.recompute()
                    return f"Created CAM Job '{job.Name}' with base object '{base_object}'"
                else:
                    return f"Created CAM Job '{job.Name}' but base object '{base_object}' not found"

            self.recompute(doc)
            return f"Created CAM Job '{job.Name}'"

        except ImportError:
            return "Error: Path (CAM) module not available. Please install FreeCAD with CAM workbench support."
        except Exception as e:
            return f"Error creating CAM job: {e}"

    def setup_stock(self, args: Dict[str, Any]) -> str:
        """Setup stock for CAM job."""
        try:
            # FreeCAD 1.0+ uses new module structure
            from Path.Main.Stock import CreateBox, CreateFromBase

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            stock_type = args.get('stock_type', 'CreateBox')

            length = args.get('length', 100)
            width = args.get('width', 100)
            height = args.get('height', 50)

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found"

            if stock_type == 'CreateBox':
                job.Stock = CreateBox(job)
                job.Stock.Length = length
                job.Stock.Width = width
                job.Stock.Height = height
            elif stock_type == 'FromBase':
                job.Stock = CreateFromBase(job)
                extent_x = args.get('extent_x', 10)
                extent_y = args.get('extent_y', 10)
                extent_z = args.get('extent_z', 10)
                job.Stock.ExtXneg = extent_x
                job.Stock.ExtXpos = extent_x
                job.Stock.ExtYneg = extent_y
                job.Stock.ExtYpos = extent_y
                job.Stock.ExtZneg = 0
                job.Stock.ExtZpos = extent_z

            job.recompute()
            return f"Setup stock for job '{job_name}' using {stock_type}"

        except Exception as e:
            return f"Error setting up stock: {e}"

    def profile(self, args: Dict[str, Any]) -> str:
        """Create a profile (contour) operation."""
        try:
            # Try new FreeCAD 1.0+ structure first, fall back to old PathScripts
            try:
                from Path.Op.Profile import Create as CreateProfile
            except ImportError:
                import PathScripts.PathProfile as PathProfileModule
                CreateProfile = PathProfileModule.Create

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Profile')
            base_object = args.get('base_object', '')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            obj = CreateProfile(name)
            job.Operations.Group += [obj]

            if base_object:
                base = self.get_object(base_object, doc)
                if base:
                    obj.Base = [(base, [])]

            if 'cut_side' in args:
                obj.Side = args['cut_side']
            if 'direction' in args:
                obj.Direction = args['direction']
            if 'stepdown' in args:
                obj.StepDown = args['stepdown']

            job.recompute()
            return f"Created Profile operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathProfile module not available"
        except Exception as e:
            return f"Error creating profile operation: {e}"

    def pocket(self, args: Dict[str, Any]) -> str:
        """Create a pocket operation."""
        try:
            # Try new FreeCAD 1.0+ structure first, fall back to old PathScripts
            try:
                from Path.Op.Pocket import Create as CreatePocket
            except ImportError:
                import PathScripts.PathPocket as PathPocketModule
                CreatePocket = PathPocketModule.Create

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Pocket')
            base_object = args.get('base_object', '')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            obj = CreatePocket(name)
            job.Operations.Group += [obj]

            if base_object:
                base = self.get_object(base_object, doc)
                if base:
                    obj.Base = [(base, [])]

            if 'stepover' in args:
                obj.StepOver = args['stepover']
            if 'stepdown' in args:
                obj.StepDown = args['stepdown']
            if 'cut_mode' in args:
                obj.CutMode = args['cut_mode']

            job.recompute()
            return f"Created Pocket operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathPocket module not available"
        except Exception as e:
            return f"Error creating pocket operation: {e}"

    def drilling(self, args: Dict[str, Any]) -> str:
        """Create a drilling operation."""
        try:
            # Try new FreeCAD 1.0+ structure first, fall back to old PathScripts
            try:
                from Path.Op.Drilling import Create as CreateDrilling
            except ImportError:
                import PathScripts.PathDrilling as PathDrillingModule
                CreateDrilling = PathDrillingModule.Create

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Drilling')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            obj = CreateDrilling(name)
            job.Operations.Group += [obj]

            if 'depth' in args:
                obj.FinalDepth = args['depth']
            if 'retract_height' in args:
                obj.RetractHeight = args['retract_height']
            if 'peck_depth' in args:
                obj.PeckDepth = args['peck_depth']
            if 'dwell_time' in args:
                obj.DwellTime = args['dwell_time']

            job.recompute()
            return f"Created Drilling operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathDrilling module not available"
        except Exception as e:
            return f"Error creating drilling operation: {e}"

    def adaptive(self, args: Dict[str, Any]) -> str:
        """Create an adaptive clearing operation."""
        try:
            # Try new FreeCAD 1.0+ structure first, fall back to old PathScripts
            try:
                from Path.Op.Adaptive import Create as CreateAdaptive
            except ImportError:
                import PathScripts.PathAdaptive as PathAdaptiveModule
                CreateAdaptive = PathAdaptiveModule.Create

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            name = args.get('name', 'Adaptive')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found. Create a CAM job first."

            obj = CreateAdaptive(name)
            job.Operations.Group += [obj]

            if 'stepover' in args:
                obj.StepOver = args['stepover']
            if 'tolerance' in args:
                obj.Tolerance = args['tolerance']

            job.recompute()
            return f"Created Adaptive operation '{obj.Name}' in job '{job_name}'"

        except ImportError:
            return "Error: PathAdaptive module not available"
        except Exception as e:
            return f"Error creating adaptive operation: {e}"

    def face(self, args: Dict[str, Any]) -> str:
        """Create a face milling operation."""
        return self._placeholder_operation("Face Milling", args)

    def helix(self, args: Dict[str, Any]) -> str:
        """Create a helix operation."""
        return self._placeholder_operation("Helix", args)

    def slot(self, args: Dict[str, Any]) -> str:
        """Create a slot milling operation."""
        return self._placeholder_operation("Slot Milling", args)

    def engrave(self, args: Dict[str, Any]) -> str:
        """Create an engrave operation."""
        return self._placeholder_operation("Engrave", args)

    def vcarve(self, args: Dict[str, Any]) -> str:
        """Create a V-carve operation."""
        return self._placeholder_operation("V-Carve", args)

    def deburr(self, args: Dict[str, Any]) -> str:
        """Create a deburr operation."""
        return self._placeholder_operation("Deburr", args)

    def surface(self, args: Dict[str, Any]) -> str:
        """Create a surface milling operation."""
        return self._placeholder_operation("Surface Milling", args)

    def waterline(self, args: Dict[str, Any]) -> str:
        """Create a waterline operation."""
        return self._placeholder_operation("Waterline", args)

    def pocket_3d(self, args: Dict[str, Any]) -> str:
        """Create a 3D pocket operation."""
        return self._placeholder_operation("3D Pocket", args)

    def thread_milling(self, args: Dict[str, Any]) -> str:
        """Create a thread milling operation."""
        return self._placeholder_operation("Thread Milling", args)

    def dogbone(self, args: Dict[str, Any]) -> str:
        """Add dogbone dressup to a path."""
        return self._placeholder_dressup("Dogbone", args)

    def lead_in_out(self, args: Dict[str, Any]) -> str:
        """Add lead-in/lead-out to a path."""
        return self._placeholder_dressup("Lead In/Out", args)

    def ramp_entry(self, args: Dict[str, Any]) -> str:
        """Add ramp entry to a path."""
        return self._placeholder_dressup("Ramp Entry", args)

    def tag(self, args: Dict[str, Any]) -> str:
        """Add holding tags to a path."""
        return self._placeholder_dressup("Tag", args)

    def axis_map(self, args: Dict[str, Any]) -> str:
        """Add axis mapping to a path."""
        return self._placeholder_dressup("Axis Map", args)

    def drag_knife(self, args: Dict[str, Any]) -> str:
        """Add drag knife compensation to a path."""
        return self._placeholder_dressup("Drag Knife", args)

    def z_correct(self, args: Dict[str, Any]) -> str:
        """Add Z-axis correction to a path."""
        return self._placeholder_dressup("Z-Correction", args)

    def create_tool(self, args: Dict[str, Any]) -> str:
        """Create a tool bit."""
        try:
            tool_type = args.get('tool_type', 'endmill')
            diameter = args.get('diameter', 6.0)
            name = args.get('name', f'{tool_type}_{diameter}mm')

            return f"Tool creation: Please use FreeCAD's Tool Library manager (CAM -> Tool Library Editor) to create tool '{name}' ({tool_type}, {diameter}mm diameter)"

        except Exception as e:
            return f"Error: {e}"

    def tool_controller(self, args: Dict[str, Any]) -> str:
        """Create a tool controller."""
        try:
            job_name = args.get('job_name', '')
            tool_name = args.get('tool_name', '')
            spindle_speed = args.get('spindle_speed', 10000)
            feed_rate = args.get('feed_rate', 1000)

            return f"Tool controller setup: Please add tool controller in job '{job_name}' with spindle speed {spindle_speed} RPM and feed rate {feed_rate} mm/min"

        except Exception as e:
            return f"Error: {e}"

    def simulate(self, args: Dict[str, Any]) -> str:
        """Simulate CAM operations."""
        try:
            job_name = args.get('job_name', '')

            return f"Simulation: Please use CAM -> Simulate (or click Simulate button) to run simulation for job '{job_name}'"

        except Exception as e:
            return f"Error: {e}"

    def post_process(self, args: Dict[str, Any]) -> str:
        """Post-process CAM job to generate G-code."""
        try:
            # Try new FreeCAD 1.0+ structure first, fall back to old PathScripts
            try:
                from Path.Post import Processor
                # For FreeCAD 1.0+, the API might be different
                PathPost = Processor
            except ImportError:
                import PathScripts.PathPost as PathPost

            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')
            output_file = args.get('output_file', '')
            post_processor = args.get('post_processor', 'grbl')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return f"Error: Job '{job_name}' not found"

            if not output_file:
                output_file = f"/tmp/{job_name}.gcode"

            postlist = PathPost.buildPostList(job)
            if not postlist:
                return "Error: No operations to post-process"

            gcode = PathPost.exportGCode(postlist, job, output_file)

            return f"Generated G-code for job '{job_name}' -> {output_file}"

        except ImportError:
            return "Error: PathPost module not available"
        except Exception as e:
            return f"Error post-processing: {e}"

    def inspect(self, args: Dict[str, Any]) -> str:
        """Inspect CAM job and operations."""
        try:
            doc = self.get_document()
            if not doc:
                return "Error: No active document"

            job_name = args.get('job_name', '')

            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                # List all jobs - look for Path::FeaturePython objects with Operations
                jobs = [obj for obj in doc.Objects if hasattr(obj, 'Operations')]
                if not jobs:
                    return "No CAM jobs found in document"

                result = f"Found {len(jobs)} CAM job(s):\n"
                for j in jobs:
                    ops = j.Operations.Group if hasattr(j, 'Operations') else []
                    result += f"  - {j.Name}: {len(ops)} operation(s)\n"
                return result

            # Inspect specific job
            ops = job.Operations.Group if hasattr(job, 'Operations') else []
            result = f"Job '{job_name}':\n"
            result += f"  Operations: {len(ops)}\n"
            for i, op in enumerate(ops, 1):
                result += f"    {i}. {op.Name} ({op.TypeId})\n"

            return result

        except Exception as e:
            return f"Error inspecting job: {e}"

    def list_operations(self, args: Dict[str, Any]) -> str:
        """List all operations in a CAM job with detailed information.

        Args:
            job_name: Name of the CAM job

        Returns:
            Formatted list of operations with parameters
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("list_operations", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("list_operations", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("list_operations", args, error=error, duration=time.time() - start_time)

            if not hasattr(job, 'Operations'):
                error = Exception(f"Job '{job_name}' does not have operations")
                return self.log_and_return("list_operations", args, error=error, duration=time.time() - start_time)

            ops = job.Operations.Group
            if not ops:
                result = f"No operations found in job '{job_name}'"
                return self.log_and_return("list_operations", args, result=result, duration=time.time() - start_time)

            result = f"Operations in job '{job_name}' ({len(ops)}):\n"
            for i, op in enumerate(ops, 1):
                result += f"\n  {i}. {op.Label} ({op.TypeId})\n"

                # Show common parameters
                if hasattr(op, 'ToolController') and op.ToolController:
                    result += f"     Tool Controller: {op.ToolController.Label}\n"
                if hasattr(op, 'StepDown'):
                    result += f"     Step Down: {op.StepDown}\n"
                if hasattr(op, 'StepOver'):
                    result += f"     Step Over: {op.StepOver}%\n"
                if hasattr(op, 'CutMode'):
                    result += f"     Cut Mode: {op.CutMode}\n"
                if hasattr(op, 'Side'):
                    result += f"     Side: {op.Side}\n"
                if hasattr(op, 'Direction'):
                    result += f"     Direction: {op.Direction}\n"

            return self.log_and_return("list_operations", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("list_operations", args, error=e, duration=time.time() - start_time)

    def get_operation(self, args: Dict[str, Any]) -> str:
        """Get detailed information about a specific CAM operation.

        Args:
            job_name: Name of the CAM job
            operation_name: Name of the operation

        Returns:
            Detailed operation information
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("get_operation", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            operation_name = args.get('operation_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("get_operation", args, error=error, duration=time.time() - start_time)
            if not operation_name:
                error = Exception("operation_name parameter required")
                return self.log_and_return("get_operation", args, error=error, duration=time.time() - start_time)

            operation = self.get_object(operation_name, doc)
            if not operation:
                error = Exception(f"Operation '{operation_name}' not found")
                return self.log_and_return("get_operation", args, error=error, duration=time.time() - start_time)

            result = f"Operation: {operation.Label}\n"
            result += f"  Type: {operation.TypeId}\n"

            # Show all relevant properties
            if hasattr(operation, 'ToolController') and operation.ToolController:
                tc = operation.ToolController
                result += f"  Tool Controller: {tc.Label}\n"
                if hasattr(tc, 'Tool') and tc.Tool:
                    result += f"    Tool: {tc.Tool.Label}\n"
                if hasattr(tc, 'SpindleSpeed'):
                    result += f"    Spindle Speed: {tc.SpindleSpeed} RPM\n"
                if hasattr(tc, 'HorizFeed'):
                    result += f"    Feed Rate: {tc.HorizFeed} mm/min\n"

            if hasattr(operation, 'Base'):
                result += f"  Base Object: {operation.Base}\n"
            if hasattr(operation, 'StepDown'):
                result += f"  Step Down: {operation.StepDown} mm\n"
            if hasattr(operation, 'StepOver'):
                result += f"  Step Over: {operation.StepOver}%\n"
            if hasattr(operation, 'CutMode'):
                result += f"  Cut Mode: {operation.CutMode}\n"
            if hasattr(operation, 'Side'):
                result += f"  Cut Side: {operation.Side}\n"
            if hasattr(operation, 'Direction'):
                result += f"  Direction: {operation.Direction}\n"
            if hasattr(operation, 'StartDepth'):
                result += f"  Start Depth: {operation.StartDepth}\n"
            if hasattr(operation, 'FinalDepth'):
                result += f"  Final Depth: {operation.FinalDepth}\n"
            if hasattr(operation, 'SafeHeight'):
                result += f"  Safe Height: {operation.SafeHeight}\n"
            if hasattr(operation, 'ClearanceHeight'):
                result += f"  Clearance Height: {operation.ClearanceHeight}\n"

            return self.log_and_return("get_operation", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("get_operation", args, error=e, duration=time.time() - start_time)

    def configure_operation(self, args: Dict[str, Any]) -> str:
        """Configure/update parameters of an existing CAM operation.

        Args:
            job_name: Name of the CAM job
            operation_name: Name of the operation
            stepdown: Step down value (optional)
            stepover: Step over percentage (optional)
            cut_mode: Cut mode - "Climb" or "Conventional" (optional)
            cut_side: Cut side - "Inside" or "Outside" (optional)
            direction: Direction - "CW" or "CCW" (optional)
            tool_controller: Name of tool controller to use (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            operation_name = args.get('operation_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)
            if not operation_name:
                error = Exception("operation_name parameter required")
                return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)

            operation = self.get_object(operation_name, doc)
            if not operation:
                error = Exception(f"Operation '{operation_name}' not found")
                return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)

            # Update parameters if provided
            updates = []

            if 'stepdown' in args and hasattr(operation, 'StepDown'):
                operation.StepDown = args['stepdown']
                updates.append(f"stepdown: {args['stepdown']}mm")

            if 'stepover' in args and hasattr(operation, 'StepOver'):
                operation.StepOver = args['stepover']
                updates.append(f"stepover: {args['stepover']}%")

            if 'cut_mode' in args and hasattr(operation, 'CutMode'):
                operation.CutMode = args['cut_mode']
                updates.append(f"cut_mode: {args['cut_mode']}")

            if 'cut_side' in args and hasattr(operation, 'Side'):
                operation.Side = args['cut_side']
                updates.append(f"cut_side: {args['cut_side']}")

            if 'direction' in args and hasattr(operation, 'Direction'):
                operation.Direction = args['direction']
                updates.append(f"direction: {args['direction']}")

            if 'tool_controller' in args and hasattr(operation, 'ToolController'):
                tc = self.get_object(args['tool_controller'], doc)
                if tc:
                    operation.ToolController = tc
                    updates.append(f"tool_controller: {args['tool_controller']}")
                else:
                    error = Exception(f"Tool controller '{args['tool_controller']}' not found")
                    return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)

            if not updates:
                error = Exception("No parameters to update. Provide stepdown, stepover, cut_mode, cut_side, direction, or tool_controller.")
                return self.log_and_return("configure_operation", args, error=error, duration=time.time() - start_time)

            self.recompute(doc)
            result = f"Updated operation '{operation_name}': {', '.join(updates)}"
            return self.log_and_return("configure_operation", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("configure_operation", args, error=e, duration=time.time() - start_time)

    def delete_operation(self, args: Dict[str, Any]) -> str:
        """Delete an operation from a CAM job.

        Args:
            job_name: Name of the CAM job
            operation_name: Name of the operation to delete

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("delete_operation", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            operation_name = args.get('operation_name', '')

            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("delete_operation", args, error=error, duration=time.time() - start_time)
            if not operation_name:
                error = Exception("operation_name parameter required")
                return self.log_and_return("delete_operation", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("delete_operation", args, error=error, duration=time.time() - start_time)

            operation = self.get_object(operation_name, doc)
            if not operation:
                error = Exception(f"Operation '{operation_name}' not found")
                return self.log_and_return("delete_operation", args, error=error, duration=time.time() - start_time)

            # Remove from job's operations
            if hasattr(job, 'Operations'):
                ops = list(job.Operations.Group)
                if operation in ops:
                    ops.remove(operation)
                    job.Operations.Group = ops

            # Delete the operation object
            doc.removeObject(operation.Name)
            result = f"Deleted operation '{operation_name}' from job '{job_name}'"
            return self.log_and_return("delete_operation", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("delete_operation", args, error=e, duration=time.time() - start_time)

    def configure_job(self, args: Dict[str, Any]) -> str:
        """Configure job parameters.

        Args:
            job_name: Name of the CAM job
            stock_type: Stock type (optional)
            output_file: Output G-code file path (optional)
            post_processor: Post processor name (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("configure_job", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("configure_job", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("configure_job", args, error=error, duration=time.time() - start_time)

            updates = []

            if 'output_file' in args:
                job.OutputFile = args['output_file']
                updates.append(f"output_file: {args['output_file']}")

            if 'post_processor' in args:
                job.PostProcessor = args['post_processor']
                updates.append(f"post_processor: {args['post_processor']}")

            if 'stock_type' in args:
                # Stock type changes require setup_stock operation
                result = f"To change stock type, use the setup_stock operation instead"
                return self.log_and_return("configure_job", args, result=result, duration=time.time() - start_time)

            if not updates:
                error = Exception("No parameters to update. Provide output_file or post_processor.")
                return self.log_and_return("configure_job", args, error=error, duration=time.time() - start_time)

            self.recompute(doc)
            result = f"Updated job '{job_name}': {', '.join(updates)}"
            return self.log_and_return("configure_job", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("configure_job", args, error=e, duration=time.time() - start_time)

    def inspect_job(self, args: Dict[str, Any]) -> str:
        """Get complete job structure and status.

        Args:
            job_name: Name of the CAM job

        Returns:
            Detailed job information including operations, tools, and status
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("inspect_job", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("inspect_job", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("inspect_job", args, error=error, duration=time.time() - start_time)

            result = f"CAM Job: {job.Label}\n"
            result += f"{'=' * 50}\n\n"

            # Base model
            if hasattr(job, 'Model') and job.Model.Group:
                result += f"Base Model:\n"
                for obj in job.Model.Group:
                    result += f"  - {obj.Label}\n"
                result += "\n"

            # Stock
            if hasattr(job, 'Stock') and job.Stock:
                result += f"Stock: {job.Stock.TypeId}\n"
                if hasattr(job.Stock, 'Length'):
                    result += f"  Dimensions: {job.Stock.Length} x {job.Stock.Width} x {job.Stock.Height}\n"
                result += "\n"

            # Tool controllers
            if hasattr(job, 'Tools') and job.Tools.Group:
                result += f"Tool Controllers ({len(job.Tools.Group)}):\n"
                for tc in job.Tools.Group:
                    tool_name = tc.Tool.Label if hasattr(tc, 'Tool') and tc.Tool else 'None'
                    speed = tc.SpindleSpeed if hasattr(tc, 'SpindleSpeed') else 'N/A'
                    result += f"  - {tc.Label}: {tool_name} @ {speed} RPM\n"
                result += "\n"
            else:
                result += "Tool Controllers: None\n\n"

            # Operations
            if hasattr(job, 'Operations') and job.Operations.Group:
                result += f"Operations ({len(job.Operations.Group)}):\n"
                for i, op in enumerate(job.Operations.Group, 1):
                    tc_name = op.ToolController.Label if hasattr(op, 'ToolController') and op.ToolController else 'None'
                    result += f"  {i}. {op.Label} ({op.TypeId})\n"
                    result += f"     Tool Controller: {tc_name}\n"
                result += "\n"
            else:
                result += "Operations: None\n\n"

            # Output configuration
            if hasattr(job, 'OutputFile'):
                result += f"Output File: {job.OutputFile}\n"
            if hasattr(job, 'PostProcessor'):
                result += f"Post Processor: {job.PostProcessor}\n"

            # Status
            result += f"\nStatus:\n"
            ready = True
            issues = []

            if not hasattr(job, 'Tools') or not job.Tools.Group:
                ready = False
                issues.append("No tool controllers defined")

            if not hasattr(job, 'Operations') or not job.Operations.Group:
                ready = False
                issues.append("No operations defined")

            if ready:
                result += "  ✓ Ready for post-processing\n"
            else:
                result += "  ✗ Not ready:\n"
                for issue in issues:
                    result += f"    - {issue}\n"

            return self.log_and_return("inspect_job", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("inspect_job", args, error=e, duration=time.time() - start_time)

    def job_status(self, args: Dict[str, Any]) -> str:
        """Quick status check of a CAM job.

        Args:
            job_name: Name of the CAM job

        Returns:
            Quick status summary
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("job_status", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("job_status", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("job_status", args, error=error, duration=time.time() - start_time)

            num_tools = len(job.Tools.Group) if hasattr(job, 'Tools') and job.Tools.Group else 0
            num_ops = len(job.Operations.Group) if hasattr(job, 'Operations') and job.Operations.Group else 0

            ready = num_tools > 0 and num_ops > 0

            result = f"Job '{job_name}': {num_ops} operation(s), {num_tools} tool(s)"
            if ready:
                result += " - Ready for export"
            else:
                result += " - Not ready"

            return self.log_and_return("job_status", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("job_status", args, error=e, duration=time.time() - start_time)

    def simulate_job(self, args: Dict[str, Any]) -> str:
        """Run CAM simulation and return status.

        Args:
            job_name: Name of the CAM job

        Returns:
            Simulation instructions (manual UI required)
        """
        start_time = time.time()
        try:
            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("simulate_job", args, error=error, duration=time.time() - start_time)

            result = f"Simulation: Please use CAM -> Simulate (or click Simulate button) to run simulation for job '{job_name}'"
            return self.log_and_return("simulate_job", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("simulate_job", args, error=e, duration=time.time() - start_time)

    def export_gcode(self, args: Dict[str, Any]) -> str:
        """Generate G-code (alias for post_process).

        Args:
            job_name: Name of the CAM job
            output_file: Output file path
            post_processor: Post processor name (optional)

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            result = self.post_process(args)
            # Log this as export_gcode even though it delegates to post_process
            return self.log_and_return("export_gcode", args, result=result, duration=time.time() - start_time)
        except Exception as e:
            return self.log_and_return("export_gcode", args, error=e, duration=time.time() - start_time)

    def delete_job(self, args: Dict[str, Any]) -> str:
        """Delete a CAM job.

        Args:
            job_name: Name of the CAM job to delete

        Returns:
            Success/error message
        """
        start_time = time.time()
        try:
            doc = self.get_document()
            if not doc:
                error = Exception("No active document")
                return self.log_and_return("delete_job", args, error=error, duration=time.time() - start_time)

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("delete_job", args, error=error, duration=time.time() - start_time)

            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("delete_job", args, error=error, duration=time.time() - start_time)

            # Remove all operations first
            if hasattr(job, 'Operations') and job.Operations.Group:
                for op in list(job.Operations.Group):
                    doc.removeObject(op.Name)

            # Remove all tool controllers
            if hasattr(job, 'Tools') and job.Tools.Group:
                for tc in list(job.Tools.Group):
                    doc.removeObject(tc.Name)

            # Remove the job itself
            doc.removeObject(job.Name)
            result = f"Deleted job '{job_name}' and all associated operations and tool controllers"
            return self.log_and_return("delete_job", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("delete_job", args, error=e, duration=time.time() - start_time)

    def _placeholder_operation(self, operation_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM operations not yet implemented."""
        job_name = args.get('job_name', '')
        name = args.get('name', operation_name)

        return f"{operation_name} operation: This operation is available in FreeCAD but not yet automated via MCP. Please create '{name}' operation manually in job '{job_name}' using the CAM workbench UI."

    def _placeholder_dressup(self, dressup_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM dressup operations not yet implemented."""
        operation = args.get('operation', '')

        return f"{dressup_name} dressup: This dressup is available in FreeCAD but not yet automated via MCP. Please apply '{dressup_name}' dressup to operation '{operation}' manually using the CAM workbench UI."

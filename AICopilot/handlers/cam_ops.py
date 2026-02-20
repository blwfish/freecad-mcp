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

            # Prepare model list - MUST be a list, not individual objects
            model_list = []
            if base_object:
                # Try direct lookup first
                obj = doc.getObject(base_object)

                # If not found, try stripping whitespace and try again
                if not obj and base_object.strip() != base_object:
                    obj = doc.getObject(base_object.strip())

                # If still not found, search by Label
                if not obj:
                    for o in doc.Objects:
                        if o.Label == base_object or o.Label == base_object.strip():
                            obj = o
                            break

                if obj:
                    model_list = [obj]
                else:
                    # Provide helpful error with available objects
                    available = [f"{o.Name} ({o.Label})" for o in doc.Objects[:10]]
                    return f"Error: Base object '{base_object}' not found. Available objects: {', '.join(available)}"

            # Create job programmatically WITHOUT GUI dialog
            # The Create function signature is: Create(name, base, templateFile=None)
            # where 'base' is a list of base objects
            job = CreateJob(job_name, model_list, None)

            # Set up ViewProvider for GUI mode
            if has_gui and hasattr(job, 'ViewObject') and job.ViewObject:
                try:
                    job.ViewObject.Proxy = ViewProvider(job.ViewObject)
                    job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
                except Exception as e:
                    # ViewProvider setup is optional, just log if it fails
                    pass

            self.recompute(doc)

            if model_list:
                return f"Created CAM Job '{job.Name}' with base object '{base_object}'"
            else:
                return f"Created CAM Job '{job.Name}' (no base object specified)"

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
        """Create a profile (contour) operation.

        With no faces/edges → exterior contour of the whole model (FC Profile
        with empty Base calls _processEachModel → envelope of job model).
        With faces → perimeter of those faces (processPerimeter=True by default).
        With edges → trace specific edges.
        """
        try:
            try:
                from Path.Op.Profile import Create as CreateProfile
            except ImportError:
                import PathScripts.PathProfile as m
                CreateProfile = m.Create

            doc, op = self._create_path_op(CreateProfile, args, 'Profile')

            if hasattr(op, 'Side'):
                op.Side = args.get('side', 'Outside')
            if 'process_perimeter' in args and hasattr(op, 'processPerimeter'):
                op.processPerimeter = bool(args['process_perimeter'])
            if 'process_holes' in args and hasattr(op, 'processHoles'):
                op.processHoles = bool(args['process_holes'])
            if 'process_circles' in args and hasattr(op, 'processCircles'):
                op.processCircles = bool(args['process_circles'])

            self.recompute(doc)
            faces, edges = args.get('faces', []), args.get('edges', [])
            mode = f"faces={faces}" if faces else (f"edges={edges}" if edges else "whole model exterior")
            return f"Created Profile operation '{op.Name}' in job '{args.get('job_name')}' ({mode})"

        except Exception as e:
            return f"Error creating profile operation: {e}"

    def pocket(self, args: Dict[str, Any]) -> str:
        """Create a pocket operation. Pass faces=['FaceN',...] to generate toolpath."""
        try:
            try:
                from Path.Op.Pocket import Create as CreatePocket
            except ImportError:
                import PathScripts.PathPocket as m
                CreatePocket = m.Create

            doc, op = self._create_path_op(CreatePocket, args, 'Pocket')

            if 'stepover' in args and hasattr(op, 'StepOver'):
                op.StepOver = args['stepover']

            self.recompute(doc)
            faces = args.get('faces', [])
            face_info = f"faces={faces}" if faces else "no faces (provide faces= to generate toolpath)"
            return f"Created Pocket operation '{op.Name}' in job '{args.get('job_name')}' ({face_info})"

        except Exception as e:
            return f"Error creating pocket operation: {e}"

    def drilling(self, args: Dict[str, Any]) -> str:
        """Create a drilling operation.

        Pass faces=['FaceN'] where FaceN is a cylindrical hole wall — FC extracts
        drill center and diameter automatically from the cylindrical face geometry.
        """
        try:
            try:
                from Path.Op.Drilling import Create as CreateDrilling
            except ImportError:
                import PathScripts.PathDrilling as m
                CreateDrilling = m.Create

            doc, op = self._create_path_op(CreateDrilling, args, 'Drilling')

            if 'depth' in args and hasattr(op, 'FinalDepth'):
                op.FinalDepth = args['depth']
            if 'retract_height' in args and hasattr(op, 'RetractHeight'):
                op.RetractHeight = args['retract_height']
            if 'peck_depth' in args and hasattr(op, 'PeckDepth'):
                op.PeckDepth = args['peck_depth']
            if 'dwell_time' in args and hasattr(op, 'DwellTime'):
                op.DwellTime = args['dwell_time']

            self.recompute(doc)
            faces = args.get('faces', [])
            face_info = f"faces={faces}" if faces else "no faces (provide cylindrical faces= to generate drill cycles)"
            return f"Created Drilling operation '{op.Name}' in job '{args.get('job_name')}' ({face_info})"

        except Exception as e:
            return f"Error creating drilling operation: {e}"

    def adaptive(self, args: Dict[str, Any]) -> str:
        """Create an adaptive clearing operation.

        Trochoidal algorithm for constant tool engagement.
        Pass faces=['FaceN',...] to define the area to clear.
        """
        try:
            try:
                from Path.Op.Adaptive import Create as CreateAdaptive
            except ImportError:
                import PathScripts.PathAdaptive as m
                CreateAdaptive = m.Create

            doc, op = self._create_path_op(CreateAdaptive, args, 'Adaptive')

            if 'stepover' in args and hasattr(op, 'StepOver'):
                op.StepOver = args['stepover']
            if 'tolerance' in args and hasattr(op, 'Tolerance'):
                op.Tolerance = args['tolerance']

            self.recompute(doc)
            faces = args.get('faces', [])
            face_info = f"faces={faces}" if faces else "no faces (provide faces= to generate toolpath)"
            return f"Created Adaptive operation '{op.Name}' in job '{args.get('job_name')}' ({face_info})"

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

    def surface_stl(self, args: Dict[str, Any]) -> str:
        """Create an OCL PathDropCutter surface operation from an STL file.

        This is the production replacement for the 'surface' placeholder.
        Reads the STL directly into ocl.STLSurf, runs PathDropCutter, and
        attaches the resulting Path to a proper Path::FeaturePython object
        with Active=True — so job consolidation and post-processing work
        through the normal FreeCAD CAM pipeline with no workarounds.

        Args:
            job_name:        CAM Job object name
            stl_file:        Absolute path to the STL mesh file
            name:            Operation name (default: "OCLSurface")
            tool_diameter:   Ball end mill diameter in mm (default: 1.0)
            stepover:        Step between Y scan lines in mm (default: 0.75)
            sample_interval: X sampling interval in mm (default: 0.5)
            safe_height:     Safe Z for rapid moves in mm (default: 8.0)
            cut_feed:        Horizontal feed rate in mm/min (default: 400.0)
            plunge_feed:     Plunge feed rate in mm/min (default: 150.0)

        Note: For very large STLs (>100K triangles or fine stepover) use
        execute_python_async to avoid MCP timeout.
        """
        import json
        import os
        import time

        start = time.time()
        try:
            doc = self.get_document()
            if not doc:
                return json.dumps({"error": "No active document"})

            job_name = args.get("job_name", "")
            job = self.get_object(job_name, doc) if job_name else None
            if not job:
                return json.dumps({"error": f"Job '{job_name}' not found. Create a CAM job first."})

            stl_file = args.get("stl_file", "")
            if not stl_file:
                return json.dumps({"error": "stl_file is required"})
            if not os.path.exists(stl_file):
                return json.dumps({"error": f"STL file not found: {stl_file!r}"})

            op_name = args.get("name", "OCLSurface")

            from ocl_surface_op import create_ocl_surface_op

            op_obj = create_ocl_surface_op(
                doc, job, op_name,
                stl_file        = stl_file,
                tool_diameter   = args.get("tool_diameter",   1.0),
                stepover        = args.get("stepover",        0.75),
                sample_interval = args.get("sample_interval", 0.5),
                safe_height     = args.get("safe_height",     8.0),
                cut_feed        = args.get("cut_feed",        400.0),
                plunge_feed     = args.get("plunge_feed",     150.0),
            )

            # Recompute just this op (runs execute() → OCL → sets op_obj.Path)
            op_obj.recompute()

            cmd_count  = len(op_obj.Path.Commands) if op_obj.Path else 0
            cycle_time = getattr(op_obj, "CycleTime", "N/A")

            return self.log_and_return(
                "surface_stl", args,
                result=json.dumps({
                    "success":       True,
                    "operation":     op_obj.Name,
                    "stl_file":      stl_file,
                    "command_count": cmd_count,
                    "cycle_time":    cycle_time,
                }),
                duration=time.time() - start,
            )

        except Exception as e:
            return self.log_and_return("surface_stl", args, error=e,
                                       duration=time.time() - start)

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
            from Path.Post.Processor import PostProcessorFactory

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

            # Set post-processor on job
            job.PostProcessor = post_processor
            if not hasattr(job, 'PostProcessorArgs') or not job.PostProcessorArgs:
                job.PostProcessorArgs = '--no-show-editor'

            processor = PostProcessorFactory.get_post_processor(job, post_processor)
            if processor is None:
                return f"Error: Post processor '{post_processor}' not found"

            gcode_sections = processor.export()
            if not gcode_sections:
                return "Error: No G-code generated (no operations or empty paths)"

            total_lines = 0
            with open(output_file, 'w') as f:
                for _partname, gcode in gcode_sections:
                    if gcode:
                        f.write(gcode)
                        total_lines += gcode.count('\n')

            return f"Generated G-code for job '{job_name}' -> {output_file} ({total_lines} lines)"

        except ImportError as e:
            return f"Error: Path.Post module not available: {e}"
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
                    feed_mmpm = float(str(tc.HorizFeed).split()[0]) * 60
                    result += f"    Feed Rate: {feed_mmpm:.0f} mm/min\n"

            if hasattr(operation, 'Base'):
                result += f"  Base Object: {operation.Base}\n"
            if hasattr(operation, 'StepDown'):
                result += f"  Step Down: {operation.StepDown}\n"
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
                # Clear any expression binding before setting — recompute would
                # restore the SetupSheet-driven default otherwise.
                try:
                    operation.setExpression('StepDown', None)
                except Exception:
                    pass
                operation.StepDown = FreeCAD.Units.Quantity(f"{args['stepdown']} mm")
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
                job.PostProcessorOutputFile = args['output_file']
                updates.append(f"output_file: {args['output_file']}")

            if 'post_processor' in args:
                job.PostProcessor = args['post_processor']
                updates.append(f"post_processor: {args['post_processor']}")

            if 'post_processor_args' in args:
                job.PostProcessorArgs = args['post_processor_args']
                updates.append(f"post_processor_args: {args['post_processor_args']}")

            if 'stock_type' in args:
                # Stock type changes require setup_stock operation
                result = f"To change stock type, use the setup_stock operation instead"
                return self.log_and_return("configure_job", args, result=result, duration=time.time() - start_time)

            if not updates:
                error = Exception("No parameters to update. Provide output_file, post_processor, or post_processor_args.")
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
            if hasattr(job, 'PostProcessorOutputFile') and job.PostProcessorOutputFile:
                result += f"Output File: {job.PostProcessorOutputFile}\n"
            if hasattr(job, 'PostProcessor'):
                result += f"Post Processor: {job.PostProcessor}\n"
            if hasattr(job, 'PostProcessorArgs') and job.PostProcessorArgs:
                result += f"Post Processor Args: {job.PostProcessorArgs}\n"

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
        """Launch the CAM simulator for a job.

        Args:
            job_name: Name of the CAM job
            use_gl: If True (default), use CAM_SimulatorGL (GPU); else CAM_Simulator

        Returns:
            Success or error message
        """
        start_time = time.time()
        try:
            import FreeCAD
            if not FreeCAD.GuiUp:
                error = Exception("GUI not available — cannot open simulator")
                return self.log_and_return("simulate_job", args, error=error, duration=time.time() - start_time)

            import FreeCADGui

            job_name = args.get('job_name', '')
            if not job_name:
                error = Exception("job_name parameter required")
                return self.log_and_return("simulate_job", args, error=error, duration=time.time() - start_time)

            doc = self.get_document()
            job = self.get_object(job_name, doc)
            if not job:
                error = Exception(f"Job '{job_name}' not found")
                return self.log_and_return("simulate_job", args, error=error, duration=time.time() - start_time)

            # Switch to CAM workbench and select the job
            FreeCADGui.activateWorkbench('CAMWorkbench')
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(doc.Name, job.Name)

            use_gl = args.get('use_gl', True)
            cmd = 'CAM_SimulatorGL' if use_gl else 'CAM_Simulator'
            FreeCADGui.runCommand(cmd, 0)

            result = f"Launched {cmd} for job '{job_name}'"
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

    def _create_path_op(self, create_fn, args: Dict[str, Any], default_name: str):
        """Shared scaffold for CAM path operations.

        Owns everything that is identical across profile, pocket, drilling,
        adaptive (and future ops): document/job resolution, op creation with the
        FC 1.2 parentJob= pattern, Base wiring from faces/edges args, and the
        common parameters stepdown/direction/cut_mode.

        Returns (doc, op). Raises RuntimeError on missing document or job so the
        caller's existing `except Exception as e` handler catches it uniformly.

        The critical Base-wiring rule (hard-won from FC 1.2 debugging):
          - Only set op.Base when sub-geometry is explicitly named.
          - An empty sub-list makes FC consider geometry "already processed"
            and skip toolpath generation → 2-3 header commands only.
          - ops that work with empty Base (Profile → whole-model exterior)
            simply don't pass faces or edges.
        """
        doc = self.get_document()
        if not doc:
            raise RuntimeError("No active document")

        job_name = args.get('job_name', '')
        job = self.get_object(job_name, doc) if job_name else None
        if not job:
            raise RuntimeError(f"Job '{job_name}' not found. Create a CAM job first.")

        # FC 1.2: parentJob= only; passing obj= causes "Object can only be in a
        # single Group" if the object is already in job.Model.Group
        op = create_fn(args.get('name', default_name), parentJob=job)

        subs = list(args.get('faces', [])) + list(args.get('edges', []))
        if subs:
            base_obj_name = args.get('base_object') or 'Clone'
            base = self.get_object(base_obj_name, doc)
            if base:
                op.Base = [(base, subs)]

        # Common parameters shared by most ops; hasattr guard makes them safe on
        # ops that don't support them
        if 'stepdown' in args and hasattr(op, 'StepDown'):
            op.StepDown = args['stepdown']
        if 'direction' in args and hasattr(op, 'Direction'):
            op.Direction = args['direction']
        if 'cut_mode' in args and hasattr(op, 'CutMode'):
            op.CutMode = args['cut_mode']

        return doc, op

    def _placeholder_operation(self, operation_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM operations not yet implemented."""
        job_name = args.get('job_name', '')
        name = args.get('name', operation_name)

        return f"{operation_name} operation: This operation is available in FreeCAD but not yet automated via MCP. Please create '{name}' operation manually in job '{job_name}' using the CAM workbench UI."

    def _placeholder_dressup(self, dressup_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM dressup operations not yet implemented."""
        operation = args.get('operation', '')

        return f"{dressup_name} dressup: This dressup is available in FreeCAD but not yet automated via MCP. Please apply '{dressup_name}' dressup to operation '{operation}' manually using the CAM workbench UI."

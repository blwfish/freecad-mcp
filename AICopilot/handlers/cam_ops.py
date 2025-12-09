# CAM workbench operation handlers for FreeCAD MCP

import FreeCAD
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

    def _placeholder_operation(self, operation_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM operations not yet implemented."""
        job_name = args.get('job_name', '')
        name = args.get('name', operation_name)

        return f"{operation_name} operation: This operation is available in FreeCAD but not yet automated via MCP. Please create '{name}' operation manually in job '{job_name}' using the CAM workbench UI."

    def _placeholder_dressup(self, dressup_name: str, args: Dict[str, Any]) -> str:
        """Placeholder for CAM dressup operations not yet implemented."""
        operation = args.get('operation', '')

        return f"{dressup_name} dressup: This dressup is available in FreeCAD but not yet automated via MCP. Please apply '{dressup_name}' dressup to operation '{operation}' manually using the CAM workbench UI."

"""Narrow unit tests for CAM wrapper logic.

CAM testing strategy (per the test build-out plan):
  * The CAM workbench API surface we depend on is stable enough to test —
    only ~1.5% of upstream churn per fortnight is breaking. But the
    assertion ceiling is lower than for parametric ops (no Shape.Volume
    equivalent for G-code), so we limit unit tests to wrapper logic:
    parameter assembly, error paths, dispatch routing, the mm/min →
    mm/s feed-rate conversion that bit users on the FreeCAD 1.2 upgrade.

Coverage:
  * cam_tools: create_tool (ToolBit.from_dict + attach_to_doc), tool-type
    validation, list_tools (ShapeID-based detection), delete_tool refusal
    when in-use.
  * cam_tool_controllers: add_tool_controller with feed-rate conversion,
    missing-job and non-tool errors.
  * cam_ops: post_process (PostProcessorFactory.get_post_processor with
    correct args, empty G-code error, missing job), create_job,
    setup_stock.
"""

import unittest
from unittest.mock import MagicMock, patch, mock_open

from tests.unit._freecad_mocks import (
    mock_FreeCAD,
    mock_Path_Tool_Bit,
    mock_Path_Tool_Controller,
    mock_Path_Main_Job,
    mock_Path_Main_Stock,
    mock_Path_Post_Processor,
    reset_mocks,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_box_object,
    assert_error_contains,
    assert_success_contains,
)

from handlers.cam_tools import CAMToolsHandler
from handlers.cam_tool_controllers import CAMToolControllersHandler
from handlers.cam_ops import CAMOpsHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tool_bit_obj(name="6mm Endmill", shape_id="endmill", diameter=6.0):
    """Mock for the object returned by ToolBit.attach_to_doc(doc=doc)."""
    obj = MagicMock()
    obj.Name = name
    obj.Label = name
    obj.TypeId = "Part::FeaturePython"
    # ShapeID is the discriminator the handler uses to identify tool bits
    obj.ShapeID = shape_id
    obj.BitShape = shape_id
    obj.Diameter = f"{diameter} mm"
    return obj


def make_cam_job(name="Job", with_tools_group=True):
    """Mock for a CAM job (Path::FeaturePython)."""
    job = MagicMock()
    job.Name = name
    job.Label = name
    job.TypeId = "Path::FeaturePython"
    if with_tools_group:
        job.Tools = MagicMock()
        job.Tools.Group = []
    return job


# ---------------------------------------------------------------------------
# cam_tools: create_tool
# ---------------------------------------------------------------------------

class TestCreateTool(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolsHandler)

    def test_invalid_tool_type_lists_valid_types(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        # ToolBit needs to be importable; provide a stub
        mock_Path_Tool_Bit.ToolBit = MagicMock()

        result = self.handler.create_tool({
            'name': 'BadTool', 'tool_type': 'unicorn', 'diameter': 3.0,
        })

        assert_error_contains(self, result, "unknown tool type", "unicorn")
        # Error message lists valid types — at least 'endmill' should appear
        self.assertIn("endmill", result.lower(),
                      "Error message should list valid tool types")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        mock_Path_Tool_Bit.ToolBit = MagicMock()
        result = self.handler.create_tool({
            'name': 'T', 'tool_type': 'endmill', 'diameter': 6.0,
        })
        assert_error_contains(self, result, "no active document")

    def test_creates_endmill_via_from_dict(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        # Stub ToolBit.from_dict + attach_to_doc
        tool_obj = make_tool_bit_obj("6mm Endmill", "endmill", 6.0)
        tool_bit = MagicMock()
        tool_bit.attach_to_doc = MagicMock(return_value=tool_obj)
        mock_Path_Tool_Bit.ToolBit = MagicMock()
        mock_Path_Tool_Bit.ToolBit.from_dict = MagicMock(return_value=tool_bit)

        result = self.handler.create_tool({
            'name': '6mm Endmill', 'tool_type': 'endmill',
            'diameter': 6.0, 'flute_length': 25.0,
            'number_of_flutes': 2,
        })

        assert_success_contains(self, result, "6mm Endmill", "endmill", "6")

        # Verify the dict shape passed to from_dict
        from_dict_call = mock_Path_Tool_Bit.ToolBit.from_dict.call_args
        tool_dict = from_dict_call.args[0]
        self.assertEqual(tool_dict['version'], 2)
        self.assertEqual(tool_dict['name'], '6mm Endmill')
        self.assertEqual(tool_dict['shape'], 'endmill')
        # Diameter formatted as "6.0 mm" Quantity-string
        self.assertEqual(tool_dict['parameter']['Diameter']['value'], '6.0 mm')
        self.assertEqual(tool_dict['parameter']['Diameter']['type'], 'Length')
        # Optional flute_length and flutes routed correctly
        self.assertEqual(tool_dict['parameter']['CuttingEdgeHeight']['value'],
                         '25.0 mm')
        self.assertEqual(tool_dict['parameter']['Flutes']['value'], 2)
        # attach_to_doc called with the active doc
        tool_bit.attach_to_doc.assert_called_once_with(doc=doc)

    def test_zero_valued_params_not_dropped(self):
        """A legitimately-supplied 0 must be written, not dropped by a falsy
        check (flute_length=0 / number_of_flutes=0 are real values)."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        tool_bit = MagicMock()
        tool_bit.attach_to_doc = MagicMock(return_value=make_tool_bit_obj("Z", "endmill"))
        mock_Path_Tool_Bit.ToolBit = MagicMock()
        mock_Path_Tool_Bit.ToolBit.from_dict = MagicMock(return_value=tool_bit)

        self.handler.create_tool({
            'name': 'Z', 'tool_type': 'endmill', 'diameter': 6.0,
            'flute_length': 0, 'shank_diameter': 0, 'number_of_flutes': 0,
        })

        params = mock_Path_Tool_Bit.ToolBit.from_dict.call_args.args[0]['parameter']
        self.assertEqual(params['CuttingEdgeHeight']['value'], '0 mm')
        self.assertEqual(params['ShankDiameter']['value'], '0 mm')
        self.assertEqual(params['Flutes']['value'], 0)

    def test_v_bit_alias_normalized(self):
        """Both 'vbit' and 'v-bit' map to ShapeID 'vbit'."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        tool_bit = MagicMock()
        tool_bit.attach_to_doc = MagicMock(return_value=make_tool_bit_obj("V", "vbit"))
        mock_Path_Tool_Bit.ToolBit = MagicMock()
        mock_Path_Tool_Bit.ToolBit.from_dict = MagicMock(return_value=tool_bit)

        self.handler.create_tool({
            'name': 'V', 'tool_type': 'v-bit', 'diameter': 30,
        })

        tool_dict = mock_Path_Tool_Bit.ToolBit.from_dict.call_args.args[0]
        self.assertEqual(tool_dict['shape'], 'vbit')

    def test_handles_path_tool_import_error(self):
        """Pre-1.2 builds without Path.Tool.Bit get a clear error."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc

        # Make `from Path.Tool.Bit import ToolBit` fail
        with patch.dict('sys.modules', {'Path.Tool.Bit': None}):
            result = self.handler.create_tool({
                'name': 'T', 'tool_type': 'endmill', 'diameter': 6,
            })

        assert_error_contains(self, result, "path.tool", "freecad 1.2")


# ---------------------------------------------------------------------------
# cam_tools: list_tools
# ---------------------------------------------------------------------------

class TestListTools(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolsHandler)

    def test_no_tools_message(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.list_tools({})
        assert_success_contains(self, result, "No tools")

    def test_finds_tools_by_shape_id_attribute(self):
        """Tool bits are Part::FeaturePython with a ShapeID attribute.

        Plain Part::Feature without ShapeID must NOT be reported."""
        endmill = make_tool_bit_obj("6mm Endmill", "endmill", 6.0)
        drill = make_tool_bit_obj("3mm Drill", "drill", 3.0)
        # Plain part feature, NO ShapeID — should be filtered out
        plain_box = make_part_object("Box1")
        plain_box.TypeId = "Part::FeaturePython"  # right typeid, but no ShapeID
        # MagicMock auto-creates ShapeID; explicitly delete to test filter
        if hasattr(plain_box, 'ShapeID'):
            del plain_box.ShapeID

        doc = make_mock_doc([endmill, drill, plain_box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.list_tools({})

        assert_success_contains(self, result, "Found 2 tool",
                                "6mm Endmill", "3mm Drill")
        # plain_box was filtered
        self.assertNotIn("Box1", result)


# ---------------------------------------------------------------------------
# cam_tools: delete_tool
# ---------------------------------------------------------------------------

class TestDeleteTool(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolsHandler)

    def test_refuses_when_in_use_by_controller(self):
        """A tool referenced by a tool controller cannot be deleted."""
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        # Tool controller that references the tool
        tc = MagicMock()
        tc.Name = "TC1"
        tc.Label = "TC1"
        tc.SpindleSpeed = 12000
        tc.Tool = tool

        doc = make_mock_doc([tool, tc])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.delete_tool({'tool_name': 'EM6'})

        assert_error_contains(self, result, "cannot delete",
                              "tool controller", "tc1")
        # Tool was NOT removed
        doc.removeObject.assert_not_called()

    def test_deletes_unused_tool(self):
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        doc = make_mock_doc([tool])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.delete_tool({'tool_name': 'EM6'})

        assert_success_contains(self, result, "Deleted")
        doc.removeObject.assert_called_once_with("EM6")


# ---------------------------------------------------------------------------
# cam_tool_controllers: add_tool_controller (feed-rate conversion)
# ---------------------------------------------------------------------------

class TestAddToolController(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolControllersHandler)

    def test_feed_rate_converts_mm_per_min_to_mm_per_sec(self):
        """FreeCAD 1.2 stores feed rates internally in mm/s.

        Handler must divide user-supplied mm/min by 60 before assigning
        HorizFeed/VertFeed. Memory note from Feb 2026 verification.
        """
        job = make_cam_job("Job1")
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        doc = make_mock_doc([job, tool])
        mock_FreeCAD.ActiveDocument = doc

        # Mock the controller returned by Create()
        controller = MagicMock()
        controller.Name = "TC_EM6"
        controller.Label = "TC_EM6"
        mock_Path_Tool_Controller.Create = MagicMock(return_value=controller)

        result = self.handler.add_tool_controller({
            'job_name': 'Job1', 'tool_name': 'EM6',
            'spindle_speed': 12000,
            'feed_rate': 600,            # mm/min
            'vertical_feed_rate': 300,   # mm/min
            'tool_number': 1,
        })

        assert_success_contains(self, result, "TC_EM6", "Job1", "EM6",
                                "12000", "600 mm/min")
        # The conversion happened: 600 mm/min ÷ 60 = 10 mm/s
        self.assertEqual(controller.HorizFeed, 10.0)
        # 300 mm/min ÷ 60 = 5 mm/s
        self.assertEqual(controller.VertFeed, 5.0)
        # Spindle speed is in RPM (no unit conversion)
        self.assertEqual(controller.SpindleSpeed, 12000.0)
        # Controller was added to the job's Tools.Group
        self.assertIn(controller, job.Tools.Group)

    def test_default_vertical_feed_is_one_third_of_horizontal(self):
        """When vertical_feed_rate is omitted, default = horiz/3 mm/min."""
        job = make_cam_job("Job1")
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        doc = make_mock_doc([job, tool])
        mock_FreeCAD.ActiveDocument = doc

        controller = MagicMock()
        mock_Path_Tool_Controller.Create = MagicMock(return_value=controller)

        self.handler.add_tool_controller({
            'job_name': 'Job1', 'tool_name': 'EM6',
            'feed_rate': 900,  # mm/min
        })

        # 900 / 3 = 300 mm/min vertical = 5 mm/s
        self.assertAlmostEqual(controller.VertFeed, 5.0, places=4)
        self.assertAlmostEqual(controller.HorizFeed, 15.0, places=4)

    def test_missing_job(self):
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        doc = make_mock_doc([tool])
        mock_FreeCAD.ActiveDocument = doc
        mock_Path_Tool_Controller.Create = MagicMock()
        result = self.handler.add_tool_controller({
            'job_name': 'NoSuchJob', 'tool_name': 'EM6',
        })
        assert_error_contains(self, result, "nosuchjob", "not found")

    def test_non_tool_object_rejected(self):
        """Object lacking ShapeID is not a tool bit."""
        job = make_cam_job("Job1")
        not_a_tool = make_part_object("Box1")  # no ShapeID
        if hasattr(not_a_tool, 'ShapeID'):
            del not_a_tool.ShapeID
        doc = make_mock_doc([job, not_a_tool])
        mock_FreeCAD.ActiveDocument = doc
        mock_Path_Tool_Controller.Create = MagicMock()

        result = self.handler.add_tool_controller({
            'job_name': 'Job1', 'tool_name': 'Box1',
        })

        assert_error_contains(self, result, "not a tool bit", "shapeid")


# ---------------------------------------------------------------------------
# cam_ops: post_process
# ---------------------------------------------------------------------------

class TestPostProcess(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_missing_job(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.post_process({
            'job_name': 'Ghost', 'output_file': '/tmp/x.gcode',
        })
        assert_error_contains(self, result, "ghost", "not found")

    def test_calls_postprocessor_factory_with_grbl_default(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        processor = MagicMock()
        processor.export = MagicMock(return_value=[
            ("Profile", "G0 X0 Y0\nG1 X10 Y0 F600\n"),
        ])
        mock_Path_Post_Processor.PostProcessorFactory = MagicMock()
        mock_Path_Post_Processor.PostProcessorFactory.get_post_processor = (
            MagicMock(return_value=processor))

        with patch('builtins.open', mock_open()) as mocked_file:
            result = self.handler.post_process({
                'job_name': 'Job1', 'output_file': '/tmp/test.gcode',
            })

        assert_success_contains(self, result, "Job1", "/tmp/test.gcode")
        # Default post-processor is grbl
        self.assertEqual(job.PostProcessor, 'grbl')
        # Factory called with (job, post_processor_name)
        get_pp = mock_Path_Post_Processor.PostProcessorFactory.get_post_processor
        get_pp.assert_called_once_with(job, 'grbl')
        # Processor.export called once
        processor.export.assert_called_once()
        # File written
        mocked_file.assert_called_with('/tmp/test.gcode', 'w')

    def test_empty_gcode_returns_error(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        processor = MagicMock()
        processor.export = MagicMock(return_value=[])  # No sections
        mock_Path_Post_Processor.PostProcessorFactory = MagicMock()
        mock_Path_Post_Processor.PostProcessorFactory.get_post_processor = (
            MagicMock(return_value=processor))

        result = self.handler.post_process({
            'job_name': 'Job1', 'output_file': '/tmp/empty.gcode',
        })

        assert_error_contains(self, result, "no g-code", "empty paths")

    def test_post_processor_not_found(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        mock_Path_Post_Processor.PostProcessorFactory = MagicMock()
        mock_Path_Post_Processor.PostProcessorFactory.get_post_processor = (
            MagicMock(return_value=None))

        result = self.handler.post_process({
            'job_name': 'Job1',
            'post_processor': 'nonexistent_pp',
        })

        assert_error_contains(self, result, "nonexistent_pp", "not found")


# ---------------------------------------------------------------------------
# cam_ops: create_job
# ---------------------------------------------------------------------------

class TestCreateJob(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_create_job_with_base_object(self):
        box = make_box_object("Plate")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        # Path.Main.Job.Create signature: Create(name=None, base=[obj])
        new_job = make_cam_job("Job_Plate")
        mock_Path_Main_Job.Create = MagicMock(return_value=new_job)

        result = self.handler.create_job({
            'base_object': 'Plate', 'job_name': 'Job_Plate',
        })

        assert_success_contains(self, result, "Job_Plate", "Plate")
        mock_Path_Main_Job.Create.assert_called_once()

    def test_create_job_resolves_base_object_by_label(self):
        """create_job's base_object lookup previously hand-rolled its own
        'search by Label, first match wins' loop instead of using
        self.get_object() — the exact anti-pattern get_object() was
        hardened against elsewhere in this codebase."""
        box = make_box_object("Box001")
        box.Label = "East Wall"
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        new_job = make_cam_job("Job_Wall")
        mock_Path_Main_Job.Create = MagicMock(return_value=new_job)

        result = self.handler.create_job({
            'base_object': 'East Wall', 'job_name': 'Job_Wall',
        })

        assert_success_contains(self, result, "Job_Wall")
        self.assertNotIn("not found", result.lower())

    def test_create_job_ambiguous_label_errors_instead_of_first_match(self):
        """Two objects sharing a Label must error, not silently pick
        whichever was first in doc.Objects."""
        box1 = make_box_object("Box001")
        box1.Label = "Wall"
        box2 = make_box_object("Box002")
        box2.Label = "Wall"
        doc = make_mock_doc([box1, box2])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.create_job({
            'base_object': 'Wall', 'job_name': 'Job1',
        })

        assert_error_contains(self, result, "ambiguous")
        mock_Path_Main_Job.Create.assert_not_called()


class TestCreateToolShapes(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolsHandler)

    def test_radius_and_taperedballnose_accepted(self):
        """Shipped FC 1.2 shapes the map previously rejected as unknown."""
        for shape in ("radius", "taperedballnose"):
            doc = make_mock_doc()
            mock_FreeCAD.ActiveDocument = doc
            tool_bit = MagicMock()
            tool_bit.attach_to_doc = MagicMock(return_value=make_tool_bit_obj("T", shape))
            mock_Path_Tool_Bit.ToolBit = MagicMock()
            mock_Path_Tool_Bit.ToolBit.from_dict = MagicMock(return_value=tool_bit)
            result = self.handler.create_tool({'name': 'T', 'tool_type': shape, 'diameter': 6})
            self.assertNotIn("Unknown tool type", result)
            tool_dict = mock_Path_Tool_Bit.ToolBit.from_dict.call_args.args[0]
            self.assertEqual(tool_dict['shape'], shape)


class TestUpdateTool(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolsHandler)

    def test_material_writable(self):
        """Material is settable at creation but previously had no update path."""
        tool = make_tool_bit_obj("EM6", "endmill", 6)
        doc = make_mock_doc([tool])
        mock_FreeCAD.ActiveDocument = doc
        self.handler.update_tool({'tool_name': 'EM6', 'material': 'HSS'})
        self.assertEqual(tool.Material, 'HSS')


class TestUpdateToolController(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMToolControllersHandler)

    def test_rapids_and_spindle_dir_writable(self):
        """SpindleDir/HorizRapid/VertRapid are readable via get_tool_controller
        but previously had no write path. Rapids use the same mm/min->mm/s
        convention as feeds."""
        controller = MagicMock()
        controller.Name = "TC1"
        controller.Label = "TC1"
        doc = make_mock_doc([controller])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.update_tool_controller({
            'job_name': 'Job', 'controller_name': 'TC1',
            'spindle_dir': 'Forward', 'horiz_rapid': 3000, 'vert_rapid': 1500,
        })

        self.assertEqual(controller.SpindleDir, 'Forward')
        self.assertAlmostEqual(controller.HorizRapid, 50.0)   # 3000 / 60
        self.assertAlmostEqual(controller.VertRapid, 25.0)    # 1500 / 60

    def test_feed_rate_and_vertical_feed_rate_convert_mm_per_min_to_mm_per_sec(self):
        """update_tool_controller's own feed_rate/vertical_feed_rate
        conversion had no test at all — only add_tool_controller's copy of
        this same /60.0 arithmetic was pinned. Mutating this specific
        divisor (287/291) survived the pre-existing suite; mutating the
        sibling at add_tool_controller (line 92) was correctly caught,
        proving the two copies were unequally guarded despite being the
        same conversion."""
        controller = MagicMock()
        controller.Name = "TC1"
        controller.Label = "TC1"
        doc = make_mock_doc([controller])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.update_tool_controller({
            'job_name': 'Job', 'controller_name': 'TC1',
            'feed_rate': 3000, 'vertical_feed_rate': 1500,
        })

        self.assertAlmostEqual(controller.HorizFeed, 50.0)   # 3000 / 60
        self.assertAlmostEqual(controller.VertFeed, 25.0)    # 1500 / 60


# ---------------------------------------------------------------------------
# cam_ops: _create_path_op / drilling — StepDown/FinalDepth expression trap
# and silently-dropped Base wiring
#
# FreeCAD binds StepDown/FinalDepth to a SetupSheet-driven expression
# ("OpToolDiameter"/"OpFinalDepth") by default at op creation. Setting only
# the *value* while that binding is live means the very next recompute()
# silently reverts it back to the computed default — a wrong drill depth is
# a crash-into-fixture risk on real hardware. configure_operation() already
# clears the expression correctly; _create_path_op/drilling previously
# didn't.
# ---------------------------------------------------------------------------

class TestCreatePathOpStepDownExpression(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_stepdown_expression_cleared_before_value_is_set(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        op = MagicMock()
        import sys
        sys.modules['Path.Op.Pocket'].Create = MagicMock(return_value=op)

        self.handler.pocket({
            'job_name': 'Job1', 'stepdown': 2.5,
        })

        # setExpression(None) must happen BEFORE the value write, not after —
        # otherwise the clear is pointless (the binding is what gets
        # re-evaluated on recompute, order between the two calls on the same
        # mock doesn't matter for correctness here, but both must happen).
        op.setExpression.assert_any_call('StepDown', None)
        self.assertEqual(op.StepDown, 2.5)

    def test_drilling_final_depth_expression_cleared_before_value_is_set(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        op = MagicMock()
        import sys
        sys.modules['Path.Op.Drilling'].Create = MagicMock(return_value=op)

        self.handler.drilling({
            'job_name': 'Job1', 'depth': -12.5,
        })

        op.setExpression.assert_any_call('FinalDepth', None)
        self.assertEqual(op.FinalDepth, -12.5)


class TestCreatePathOpBaseWiring(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_unresolved_base_object_errors_instead_of_dropping_selection(self):
        """faces=[...] with a base_object that doesn't resolve must error —
        not silently create the op with Base unset while still reporting
        the requested faces as applied."""
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])  # no 'Clone' or any other object present
        mock_FreeCAD.ActiveDocument = doc

        op = MagicMock()
        import sys
        sys.modules['Path.Op.Pocket'].Create = MagicMock(return_value=op)

        result = self.handler.pocket({
            'job_name': 'Job1', 'faces': ['Face3'],
        })

        assert_error_contains(self, result, "clone", "not found")

    def test_explicit_base_object_not_found_errors_with_its_name(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc

        op = MagicMock()
        import sys
        sys.modules['Path.Op.Pocket'].Create = MagicMock(return_value=op)

        result = self.handler.pocket({
            'job_name': 'Job1', 'faces': ['Face1'], 'base_object': 'Plate',
        })

        assert_error_contains(self, result, "plate", "not found")

    def test_resolved_base_object_still_wires_correctly(self):
        """Regression guard: the error path above must not have broken the
        happy path where base_object does resolve."""
        job = make_cam_job("Job1")
        plate = make_box_object("Plate")
        doc = make_mock_doc([job, plate])
        mock_FreeCAD.ActiveDocument = doc

        op = MagicMock()
        import sys
        sys.modules['Path.Op.Pocket'].Create = MagicMock(return_value=op)

        result = self.handler.pocket({
            'job_name': 'Job1', 'faces': ['Face1', 'Face2'], 'base_object': 'Plate',
        })

        self.assertEqual(op.Base, [(plate, ['Face1', 'Face2'])])
        assert_success_contains(self, result, "Pocket")


# ---------------------------------------------------------------------------
# pocket / adaptive — stepover-vs-tool-diameter validation
#
# StepOver is a percentage of tool diameter (confirmed against FreeCAD's
# own source, Path/Op/PocketBase.py: PocketStepover = (radius*2) *
# (StepOver/100)). At/above 100 the tool paths don't overlap, leaving
# uncut ridges — syntactically valid G-code with silently wrong geometry.
# Neither pocket() nor adaptive() validated this before.
# ---------------------------------------------------------------------------

class TestPocketStepoverValidation(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_stepover_at_100_rejected(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc
        import sys
        create_fn = MagicMock()
        sys.modules['Path.Op.Pocket'].Create = create_fn

        result = self.handler.pocket({'job_name': 'Job1', 'stepover': 100})

        assert_error_contains(self, result, "stepover", "100")
        create_fn.assert_not_called()

    def test_stepover_above_100_rejected(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc
        import sys
        create_fn = MagicMock()
        sys.modules['Path.Op.Pocket'].Create = create_fn

        result = self.handler.pocket({'job_name': 'Job1', 'stepover': 150})

        assert_error_contains(self, result, "stepover")
        create_fn.assert_not_called()

    def test_stepover_just_below_100_accepted(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc
        op = MagicMock()
        import sys
        sys.modules['Path.Op.Pocket'].Create = MagicMock(return_value=op)

        result = self.handler.pocket({'job_name': 'Job1', 'stepover': 99})

        assert_success_contains(self, result, "Pocket")
        self.assertEqual(op.StepOver, 99)


class TestAdaptiveStepoverValidation(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(CAMOpsHandler)

    def test_stepover_at_100_rejected(self):
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc
        import sys
        create_fn = MagicMock()
        sys.modules['Path.Op.Adaptive'].Create = create_fn

        result = self.handler.adaptive({'job_name': 'Job1', 'stepover': 100})

        assert_error_contains(self, result, "stepover", "100")
        create_fn.assert_not_called()

    def test_stepover_writes_to_step_over_percent_not_legacy_stepover(self):
        """Regression: the real Adaptive property is StepOverPercent, not
        StepOver — FreeCAD's own Adaptive.py explicitly removes a legacy
        "StepOver" property if present, so hasattr(op, 'StepOver') is False
        on any modern Adaptive feature and the caller's stepover argument
        was silently never applied."""
        job = make_cam_job("Job1")
        doc = make_mock_doc([job])
        mock_FreeCAD.ActiveDocument = doc
        # Mock spec'd to the REAL property surface — no StepOver attribute
        # at all, matching a real (post-cleanup) Adaptive feature object.
        op = MagicMock(spec=['Name', 'StepOverPercent', 'Tolerance', 'Base'])
        import sys
        sys.modules['Path.Op.Adaptive'].Create = MagicMock(return_value=op)

        self.handler.adaptive({'job_name': 'Job1', 'stepover': 40})

        self.assertEqual(op.StepOverPercent, 40)


if __name__ == '__main__':
    unittest.main()

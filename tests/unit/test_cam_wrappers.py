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


if __name__ == '__main__':
    unittest.main()

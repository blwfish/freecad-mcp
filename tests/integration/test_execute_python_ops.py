"""
Integration tests for execute_python_sync's Console-stderr capture
(issue #49): FreeCAD's own C++ Console output (Console.PrintWarning,
PrintError, PrintLog, PrintCritical, and warnings FreeCAD emits internally
as a side effect of property sets/recomputes — e.g. deprecation notices)
writes via raw fprintf(stderr, ...) at the C level, below Python's
sys.stdout/sys.stderr, so run_code()'s io.StringIO() stdout capture never
saw it. AICopilot/handlers/execute_python_ops.py now also redirects the
real OS-level stderr fd for the duration of code execution and folds
anything captured into the response under a "[FreeCAD Console]" heading.

tests/unit/test_execute_python_ops.py's TestConsoleStderrCapture exercises
the capture mechanism itself (raw os.write(2, ...), no real FreeCAD
needed). This file instead confirms the actual real-world trigger this
issue was filed over: FreeCAD's own C++ layer emitting a warning as a side
effect of a property set, not a manual PrintWarning() call — the
PartDesign::Pad Midplane deprecation notice from the original bug report.
"""

import json
import time
import pytest
from ._geom_helpers import _result_text as _text
from .test_e2e_workflows import send_command


@pytest.fixture
def clean_document():
    doc_name = f"ExecPy_{int(time.time() * 1000) % 100000}"
    send_command("view_control", {
        "operation": "create_document",
        "document_name": doc_name,
    })
    yield doc_name
    try:
        send_command("execute_python_sync", {
            "code": f"FreeCAD.closeDocument('{doc_name}')"
        })
    except Exception:
        pass


class TestConsoleStderrCapture:
    def test_manual_print_warning_surfaced(self, clean_document):
        result = send_command("execute_python_sync", {
            "code": "FreeCAD.Console.PrintWarning('manual test warning\\n')\nresult = 1 + 1",
        })
        text = _text(result)
        assert "[FreeCAD Console]" in text, text
        assert "manual test warning" in text, text
        assert "2" in text, text

    def test_no_warning_omits_console_section(self, clean_document):
        result = send_command("execute_python_sync", {"code": "1 + 1"})
        text = _text(result)
        assert "[FreeCAD Console]" not in text, text

    def test_pad_midplane_deprecation_warning_surfaced(self, clean_document):
        """Exact reproduction of issue #49: FreeCAD's own C++ layer emits
        a PrintWarning-level deprecation notice as a side effect of
        setting PartDesign::Pad.Midplane, not from any explicit
        PrintWarning() call in the executed code. Before this fix, this
        was only visible via a separate, manual get_report_view call —
        several tool calls after the fact in the original report."""
        code = """
import Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sk')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(10,0,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,0,0), FreeCAD.Vector(10,10,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(10,10,0), FreeCAD.Vector(0,10,0)))
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0,10,0), FreeCAD.Vector(0,0,0)))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
doc.recompute()
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
pad.Midplane = True
doc.recompute()
result = pad.Midplane
"""
        result = send_command("execute_python_sync", {"code": code}, timeout=20.0)
        text = _text(result)
        assert "[FreeCAD Console]" in text, text
        assert "Midplane" in text and "deprecated" in text, text
        assert "True" in text, text

    def test_console_capture_does_not_break_subsequent_calls(self, clean_document):
        """The OS-level fd redirect must be fully restored after each
        call -- a leak here would corrupt every later call's stderr in
        the same long-lived headless process, not just this handler's,
        which is a much worse failure mode than a single wrong response."""
        first = send_command("execute_python_sync", {
            "code": "FreeCAD.Console.PrintWarning('first\\n')",
        })
        assert "first" in _text(first)
        second = send_command("execute_python_sync", {"code": "2 + 2"})
        text = _text(second)
        assert "4" in text
        assert "first" not in text, text
        assert "[FreeCAD Console]" not in text, text

"""Unit tests for freecad_crash_report crash-report parsing.

macOS 12+ writes JSON .ips crash reports; the parser must handle them — the
previous plaintext-only parser produced empty output on every modern .ips.
It must also capture the ACTUAL crashed thread, not a hardcoded Thread 0.

The .ips fixtures below mirror the structure of a real captured FreeCADCmd
SIGSEGV report (exception / termination / faultingThread / threads / usedImages).
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import freecad_crash_report as cr


def _ips(header, body):
    """Build .ips content: a JSON header line followed by the JSON body."""
    return json.dumps(header) + "\n" + json.dumps(body)


class TestIpsParsing:
    def _sample(self):
        header = {"app_name": "FreeCADCmd", "app_version": "1.2.0",
                  "os_version": "macOS 15.7"}
        body = {
            "exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV",
                          "subtype": "KERN_INVALID_ADDRESS at 0x0"},
            "termination": {"namespace": "SIGNAL", "code": 11,
                            "indicator": "Segmentation fault: 11"},
            "faultingThread": 1,
            "threads": [
                {"frames": [{"imageOffset": 10, "imageIndex": 0}]},
                {"triggered": True, "frames": [
                    {"imageOffset": 2628, "symbol": "_platform_strlen",
                     "symbolLocation": 4, "imageIndex": 0},
                    {"imageOffset": 96, "symbol": "string_at", "imageIndex": 1},
                ]},
            ],
            "usedImages": [
                {"name": "libsystem_platform.dylib"},
                {"name": "_ctypes.so", "path": "/x/_ctypes.so"},
            ],
        }
        return _ips(header, body)

    def test_extracts_exception_and_crashed_thread(self):
        out = cr._parse_crash_report(self._sample(), "/x/FreeCADCmd-1.ips")
        assert "EXC_BAD_ACCESS" in out
        assert "SIGSEGV" in out
        assert "KERN_INVALID_ADDRESS" in out
        # The crashed thread is #1 (faultingThread), not 0.
        assert "Crashed thread 1" in out
        # Symbolicated frames present, named via usedImages.
        assert "_platform_strlen" in out
        assert "libsystem_platform.dylib" in out

    def test_ips_does_not_fall_back_to_empty_plaintext_output(self):
        """A JSON .ips must NOT degrade to the old '[name]' header-only output."""
        out = cr._parse_crash_report(self._sample(), "/x/FreeCADCmd-1.ips")
        assert out.strip() != "[FreeCADCmd-1.ips]"
        assert len(out.splitlines()) > 3

    def test_faulting_thread_falls_back_to_triggered_flag(self):
        """When faultingThread is absent, use the thread's triggered flag."""
        header = {"app_name": "FreeCAD"}
        body = {"exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV"},
                "threads": [{"frames": []},
                            {"triggered": True,
                             "frames": [{"symbol": "boom", "imageIndex": 0}]}],
                "usedImages": [{"name": "FreeCADApp.so"}]}
        out = cr._parse_crash_report(_ips(header, body), "/x/FreeCAD-2.ips")
        assert "Crashed thread 1" in out
        assert "boom" in out

    def test_asi_field_extracted(self):
        """Application Specific Information (body['asi']) is often the
        single highest-signal field for non-EXC_BAD_ACCESS crashes (e.g.
        an assertion failure or an embedded Python traceback) — previously
        never read at all (H16)."""
        header = {"app_name": "FreeCADCmd"}
        body = {
            "exception": {"type": "EXC_CRASH", "signal": "SIGABRT"},
            "asi": {"libsystem_c.dylib": [
                "abort() called",
                "Assertion failed: (x != NULL), function foo, file bar.c, line 42.",
            ]},
            "threads": [{"triggered": True, "frames": []}],
            "usedImages": [],
        }
        out = cr._parse_crash_report(_ips(header, body), "/x/FreeCADCmd-3.ips")
        assert "Application Specific Information" in out
        assert "abort() called" in out
        assert "Assertion failed" in out

    def test_asi_absent_produces_no_asi_section(self):
        out = cr._parse_crash_report(self._sample(), "/x/FreeCADCmd-1.ips")
        assert "Application Specific Information" not in out

    def test_valid_json_with_bad_field_shape_does_not_fall_back_to_legacy_parser(self):
        """If .ips JSON parses but a field has an unexpected shape (e.g.
        'threads' is a string instead of a list), summary generation raises
        inside _parse_ips_report. Falling back to the legacy plaintext
        parser on that exception previously ran a text-prefix scanner
        against raw JSON content, silently producing near-empty output
        instead of surfacing the real failure (H16)."""
        header = {"app_name": "FreeCAD"}
        body = {"exception": {"type": "EXC_BAD_ACCESS"}, "threads": "not-a-list"}
        out = cr._parse_crash_report(_ips(header, body), "/x/FreeCAD-4.ips")
        # Must not silently degrade to the legacy parser's near-empty
        # header-only output for JSON content it can't understand.
        assert "summary generation failed" in out
        assert "FreeCAD-4.ips" in out


class TestLegacyCrashParsing:
    def test_picks_actual_crashed_thread_not_thread_0(self):
        """Legacy plaintext .crash: capture the 'Crashed Thread: N' block, not
        the hardcoded Thread 0 (whose stack is NOT the crash backtrace)."""
        content = "\n".join([
            "Exception Type:  EXC_BAD_ACCESS (SIGSEGV)",
            "Crashed Thread:  3",
            "",
            "Thread 0:",
            "0  libsystem  start + 1",
            "",
            "Thread 3 Crashed:",
            "0  FreeCADApp  real_crash_frame + 42",
            "1  FreeCADApp  caller + 8",
            "",
        ])
        out = cr._parse_crash_report(content, "/x/FreeCAD.crash")
        assert "Crashed thread 3" in out
        assert "real_crash_frame" in out
        # Must NOT have grabbed Thread 0's (non-crash) frames instead.
        assert "start + 1" not in out

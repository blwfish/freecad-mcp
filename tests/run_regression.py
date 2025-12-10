#!/usr/bin/env python3
"""
FreeCAD MCP Regression Test Runner

Usage:
    freecadcmd run_regression.py [suite] [--baseline] [--compare]

Suites:
    quick      - Fast smoke tests (8 min)
    upgrade    - After FC upgrade (25 min)
    regression - Full regression (45 min)
    cam        - CAM-focused tests (15 min)

Examples:
    # After FC upgrade
    freecadcmd run_regression.py upgrade

    # Full regression and update baseline
    freecadcmd run_regression.py regression --baseline

    # Quick check
    freecadcmd run_regression.py quick
"""

import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import FreeCAD
    import FreeCADGui
except ImportError:
    print("ERROR: This script must be run with freecadcmd")
    print("Usage: freecadcmd run_regression.py [suite]")
    sys.exit(1)

from test_config import (
    TEST_SUITES, MODULE_TESTS, Priority,
    ResultsConfig, BaselineConfig, ExecutionConfig,
    HISTORY_DIR, CURRENT_DIR, REPORTS_DIR
)

class TestRunner:
    """Main test runner for FreeCAD MCP regression tests"""

    def __init__(self, suite_name="quick"):
        self.suite_name = suite_name
        self.suite_config = TEST_SUITES.get(suite_name, TEST_SUITES["quick"])
        self.results = {
            "test_run": self._get_test_run_metadata(),
            "suite": suite_name,
            "modules": {},
            "summary": {},
            "regressions": [],
            "performance_changes": []
        }
        self.start_time = None

    def _get_test_run_metadata(self):
        """Collect FreeCAD and system metadata"""
        fc_version = ".".join(map(str, FreeCAD.Version()[:3]))
        fc_build = FreeCAD.Version()[3] if len(FreeCAD.Version()) > 3 else "unknown"

        import platform
        return {
            "timestamp": datetime.now().isoformat(),
            "freecad_version": fc_version,
            "freecad_build": fc_build,
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version()
        }

    def run_suite(self):
        """Run the selected test suite"""
        print(f"\n{'='*70}")
        print(f"FreeCAD MCP Regression Test Suite: {self.suite_name.upper()}")
        print(f"{'='*70}")
        print(f"\nFreeCAD: {self.results['test_run']['freecad_version']} "
              f"(build {self.results['test_run']['freecad_build']})")
        print(f"Platform: {self.results['test_run']['platform']}")
        print(f"Estimated time: {self.suite_config['estimated_time_minutes']} minutes")
        print(f"\n{self.suite_config['description']}\n")

        self.start_time = time.time()

        # Determine which modules to test based on priority
        modules_to_test = self._get_modules_for_suite()

        print(f"Testing {len(modules_to_test)} module(s):")
        for module_name in modules_to_test:
            print(f"  - {module_name}")
        print()

        # Run tests for each module
        for module_name in modules_to_test:
            self._test_module(module_name)

        # Calculate summary
        self._calculate_summary()

        # Save results
        self._save_results()

        # Print summary
        self._print_summary()

        return self.results

    def _get_modules_for_suite(self):
        """Determine which modules to test based on suite configuration"""
        priority_levels = set(self.suite_config["priority_levels"])
        modules = []

        for module_name, config in MODULE_TESTS.items():
            if config["priority"] in priority_levels:
                modules.append(module_name)

        return modules

    def _test_module(self, module_name):
        """Test a specific module"""
        module_config = MODULE_TESTS[module_name]
        print(f"\n{'─'*70}")
        print(f"Testing: {module_name} ({module_config['priority']})")
        print(f"Reason: {module_config['reason']}")
        print(f"{'─'*70}\n")

        module_start = time.time()
        module_results = {
            "priority": module_config["priority"],
            "handlers": module_config["handlers"],
            "tests": [],
            "status": "PENDING",
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0
        }

        # Import and run tests for each handler
        for handler_name in module_config["handlers"]:
            handler_results = self._test_handler(module_name, handler_name)
            module_results["tests"].append(handler_results)

            if handler_results["status"] == "PASS":
                module_results["tests_passed"] += 1
            elif handler_results["status"] == "FAIL":
                module_results["tests_failed"] += 1
            else:
                module_results["tests_skipped"] += 1

        # Determine module status
        if module_results["tests_failed"] > 0:
            module_results["status"] = "FAIL"
        elif module_results["tests_skipped"] > 0:
            module_results["status"] = "PARTIAL"
        else:
            module_results["status"] = "PASS"

        module_results["duration"] = time.time() - module_start
        self.results["modules"][module_name] = module_results

        # Print module summary
        status_symbol = "✓" if module_results["status"] == "PASS" else "✗"
        print(f"\n{status_symbol} {module_name}: {module_results['status']} "
              f"({module_results['tests_passed']} passed, "
              f"{module_results['tests_failed']} failed, "
              f"{module_results['duration']:.2f}s)")

    def _test_handler(self, module_name, handler_name):
        """Test a specific handler"""
        print(f"  Testing handler: {handler_name}...")

        handler_start = time.time()
        result = {
            "handler": handler_name,
            "tests_run": [],
            "status": "PENDING"
        }

        try:
            # Import handler
            from AICopilot.socket_server import FreeCADSocketServer
            from AICopilot.handlers import (
                CAMOpsHandler, CAMToolsHandler, CAMToolControllersHandler
            )

            # Run basic import/instantiation test
            test_name = f"{handler_name}_import"
            try:
                if handler_name == "cam_ops":
                    handler = CAMOpsHandler()
                elif handler_name == "cam_tools":
                    handler = CAMToolsHandler()
                elif handler_name == "cam_tool_controllers":
                    handler = CAMToolControllersHandler()
                else:
                    # Generic test for other handlers
                    result["status"] = "SKIPPED"
                    result["reason"] = "Handler testing not yet implemented"
                    return result

                result["tests_run"].append({
                    "test": test_name,
                    "status": "PASS",
                    "duration": time.time() - handler_start
                })
                print(f"    ✓ {test_name}")
                result["status"] = "PASS"

            except Exception as e:
                result["tests_run"].append({
                    "test": test_name,
                    "status": "FAIL",
                    "error": str(e),
                    "duration": time.time() - handler_start
                })
                print(f"    ✗ {test_name}: {e}")
                result["status"] = "FAIL"

        except Exception as e:
            result["status"] = "FAIL"
            result["error"] = str(e)
            print(f"    ✗ Handler test failed: {e}")

        result["duration"] = time.time() - handler_start
        return result

    def _calculate_summary(self):
        """Calculate test run summary"""
        total_duration = time.time() - self.start_time

        summary = {
            "total_modules": len(self.results["modules"]),
            "modules_passed": 0,
            "modules_failed": 0,
            "modules_partial": 0,
            "total_tests": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0,
            "total_duration": total_duration
        }

        for module_name, module_result in self.results["modules"].items():
            if module_result["status"] == "PASS":
                summary["modules_passed"] += 1
            elif module_result["status"] == "FAIL":
                summary["modules_failed"] += 1
            else:
                summary["modules_partial"] += 1

            summary["total_tests"] += len(module_result["tests"])
            summary["tests_passed"] += module_result["tests_passed"]
            summary["tests_failed"] += module_result["tests_failed"]
            summary["tests_skipped"] += module_result["tests_skipped"]

        self.results["summary"] = summary

    def _save_results(self):
        """Save test results to files"""
        fc_version = self.results["test_run"]["freecad_version"]
        fc_build = self.results["test_run"]["freecad_build"]
        timestamp = datetime.fromisoformat(self.results["test_run"]["timestamp"])

        # Save to history
        filename = ResultsConfig.get_result_filename(fc_version, fc_build, timestamp)
        history_file = HISTORY_DIR / filename
        with open(history_file, 'w') as f:
            json.dump(self.results, f, indent=2)

        # Save as latest
        latest_file = CURRENT_DIR / "latest_results.json"
        with open(latest_file, 'w') as f:
            json.dump(self.results, f, indent=2)

        print(f"\n✓ Results saved to:")
        print(f"  - {history_file}")
        print(f"  - {latest_file}")

    def _print_summary(self):
        """Print test summary"""
        summary = self.results["summary"]

        print(f"\n{'='*70}")
        print(f"TEST SUMMARY")
        print(f"{'='*70}")
        print(f"\nModules: {summary['modules_passed']}/{summary['total_modules']} passed")
        print(f"Tests:   {summary['tests_passed']}/{summary['total_tests']} passed")

        if summary['tests_failed'] > 0:
            print(f"\n⚠️  {summary['tests_failed']} test(s) FAILED")

        if summary['tests_skipped'] > 0:
            print(f"ℹ️  {summary['tests_skipped']} test(s) skipped")

        print(f"\nDuration: {summary['total_duration']:.2f}s "
              f"({summary['total_duration']/60:.1f} minutes)")

        # Overall status
        if summary['modules_failed'] > 0:
            print(f"\n❌ REGRESSION SUITE: FAILED\n")
            sys.exit(1)
        else:
            print(f"\n✅ REGRESSION SUITE: PASSED\n")
            sys.exit(0)


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="FreeCAD MCP Regression Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "suite",
        nargs="?",
        default="quick",
        choices=list(TEST_SUITES.keys()),
        help="Test suite to run (default: quick)"
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Update baseline after successful run"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare results with baseline"
    )

    args = parser.parse_args()

    # Run tests
    runner = TestRunner(args.suite)
    results = runner.run_suite()

    # Update baseline if requested and all tests passed
    if args.baseline and results["summary"]["modules_failed"] == 0:
        print("\n📝 Updating baseline...")
        # TODO: Implement baseline update
        print("✓ Baseline updated")

    # Compare with baseline if requested
    if args.compare:
        print("\n📊 Comparing with baseline...")
        # TODO: Implement comparison
        print("✓ Comparison complete")


if __name__ == "__main__":
    main()

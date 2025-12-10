# FreeCAD MCP Test Configuration
# Defines test suites, priorities, and result storage

import os
from datetime import datetime
from pathlib import Path

# Test directory structure
TEST_ROOT = Path(__file__).parent
RESULTS_DIR = TEST_ROOT / "results"
HISTORY_DIR = RESULTS_DIR / "history"
CURRENT_DIR = RESULTS_DIR / "current"
REPORTS_DIR = RESULTS_DIR / "reports"
FIXTURES_DIR = TEST_ROOT / "fixtures"

# Ensure directories exist
for dir_path in [RESULTS_DIR, HISTORY_DIR, CURRENT_DIR, REPORTS_DIR, FIXTURES_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Test Priority Levels
class Priority:
    P0_CRITICAL = "P0_CRITICAL"      # CAM, core - always test
    P1_HIGH = "P1_HIGH"              # PartDesign, Part - major upgrades
    P2_MEDIUM = "P2_MEDIUM"          # Draft, Measurement - spot check
    P3_LOW = "P3_LOW"                # Spreadsheet, utilities - quarterly

# Test Suites
TEST_SUITES = {
    "quick": {
        "description": "5-10 minute smoke test after any FC change",
        "priority_levels": [Priority.P0_CRITICAL],
        "test_types": ["smoke", "import"],
        "estimated_time_minutes": 8,
        "use_cases": ["After FC upgrade", "After MCP code change", "Before commit"]
    },

    "upgrade": {
        "description": "Run after FC version upgrade",
        "priority_levels": [Priority.P0_CRITICAL, Priority.P1_HIGH],
        "test_types": ["smoke", "full", "compatibility"],
        "estimated_time_minutes": 25,
        "use_cases": ["After FC version upgrade", "After FC build change"]
    },

    "regression": {
        "description": "Comprehensive regression (weekly/before release)",
        "priority_levels": [Priority.P0_CRITICAL, Priority.P1_HIGH, Priority.P2_MEDIUM, Priority.P3_LOW],
        "test_types": ["smoke", "full", "edge_cases"],
        "estimated_time_minutes": 45,
        "use_cases": ["Weekly check", "Before MCP release", "After major changes"]
    },

    "cam_focused": {
        "description": "Deep CAM testing after CAM workbench changes",
        "priority_levels": [Priority.P0_CRITICAL],
        "test_types": ["smoke", "full", "edge_cases", "performance"],
        "workbenches": ["CAM"],
        "estimated_time_minutes": 15,
        "use_cases": ["After FC CAM updates", "CAM regression detected", "CAM development"]
    }
}

# Module Test Configuration
MODULE_TESTS = {
    "cam_workbench": {
        "priority": Priority.P0_CRITICAL,
        "reason": "Active development, version-sensitive, module restructuring",
        "handlers": ["cam_ops", "cam_tools", "cam_tool_controllers"],
        "test_files": [
            "test_cam_version_compatibility.py",
            "test_cam_tools_crud.py",
            "test_cam_tool_controllers_crud.py",
            "test_cam_operations_crud.py",
            "test_cam_workflow.py"
        ]
    },

    "core_handlers": {
        "priority": Priority.P0_CRITICAL,
        "reason": "High usage, large API surface",
        "handlers": ["document_ops", "primitives", "boolean_ops"],
        "test_files": [
            "test_document_operations.py",
            "test_primitives.py",
            "test_boolean_operations.py"
        ]
    },

    "partdesign_workbench": {
        "priority": Priority.P1_HIGH,
        "reason": "Stable API, high usage",
        "handlers": ["partdesign_ops"],
        "test_files": [
            "test_partdesign_operations.py"
        ]
    },

    "part_workbench": {
        "priority": Priority.P1_HIGH,
        "reason": "Stable API, moderate usage",
        "handlers": ["part_ops"],
        "test_files": [
            "test_part_operations.py"
        ]
    },

    "draft_workbench": {
        "priority": Priority.P2_MEDIUM,
        "reason": "Stable, less critical",
        "handlers": ["draft_ops"],
        "test_files": [
            "test_draft_operations.py"
        ]
    },

    "measurement": {
        "priority": Priority.P2_MEDIUM,
        "reason": "Simple, stable API",
        "handlers": ["measurement_ops"],
        "test_files": [
            "test_measurement_operations.py"
        ]
    },

    "spreadsheet": {
        "priority": Priority.P3_LOW,
        "reason": "Very stable, simple operations",
        "handlers": ["spreadsheet_ops"],
        "test_files": [
            "test_spreadsheet_operations.py"
        ]
    }
}

# Result Storage Configuration
class ResultsConfig:
    """Configuration for test results storage and retention"""

    # Result file naming
    @staticmethod
    def get_result_filename(fc_version, fc_build=None, timestamp=None):
        """Generate result filename with metadata"""
        if timestamp is None:
            timestamp = datetime.now()

        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H%M%S")

        if fc_build:
            return f"{date_str}_{time_str}_fc-{fc_version}-{fc_build}_results.json"
        else:
            return f"{date_str}_{time_str}_fc-{fc_version}_results.json"

    # Retention policy
    KEEP_RESULTS_DAYS = 90  # Keep detailed results for 90 days
    KEEP_BASELINE_VERSIONS = 5  # Keep last 5 baseline versions

    # Comparison settings
    BASELINE_FILE = CURRENT_DIR / "baseline.json"
    LATEST_FILE = CURRENT_DIR / "latest_results.json"

    # Report settings
    GENERATE_HTML = True
    GENERATE_MARKDOWN = True
    GENERATE_JSON = True

    # Performance tracking
    TRACK_PERFORMANCE = True
    PERFORMANCE_THRESHOLD_PERCENT = 20  # Warn if >20% slower

# Baseline Management
class BaselineConfig:
    """Configuration for baseline test results"""

    # When to update baseline
    UPDATE_BASELINE_ON = [
        "manual_request",  # Explicit user request
        "all_tests_pass",  # All tests pass after FC upgrade
        "new_fc_version"   # New FreeCAD version detected
    ]

    # Baseline metadata
    @staticmethod
    def create_baseline_metadata(fc_version, fc_build, test_results):
        """Create metadata for baseline"""
        return {
            "created": datetime.now().isoformat(),
            "freecad_version": fc_version,
            "freecad_build": fc_build,
            "total_tests": test_results.get("total_tests", 0),
            "test_suite": test_results.get("test_suite", "unknown"),
            "platform": test_results.get("platform", "unknown")
        }

# Test Execution Config
class ExecutionConfig:
    """Configuration for test execution"""

    # Timeouts
    TEST_TIMEOUT_SECONDS = 30
    SUITE_TIMEOUT_MINUTES = 60

    # Retry policy
    RETRY_FAILED_TESTS = True
    MAX_RETRIES = 2

    # Parallel execution
    ENABLE_PARALLEL = False  # FreeCAD doesn't support parallel well

    # Output
    VERBOSE = True
    SHOW_PROGRESS = True
    COLORIZE_OUTPUT = True

# Notification Config
class NotificationConfig:
    """Configuration for test notifications"""

    # When to notify
    NOTIFY_ON = [
        "regression_detected",
        "new_failures",
        "performance_degradation",
        "fc_version_incompatibility"
    ]

    # Notification methods
    METHODS = {
        "console": True,
        "file": True,
        "slack": False,  # Future enhancement
        "email": False   # Future enhancement
    }

# Export configuration
__all__ = [
    'TEST_ROOT',
    'RESULTS_DIR',
    'HISTORY_DIR',
    'CURRENT_DIR',
    'REPORTS_DIR',
    'FIXTURES_DIR',
    'Priority',
    'TEST_SUITES',
    'MODULE_TESTS',
    'ResultsConfig',
    'BaselineConfig',
    'ExecutionConfig',
    'NotificationConfig'
]

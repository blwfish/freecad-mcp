# FreeCAD MCP Testing Framework

**Comprehensive regression testing for FreeCAD MCP after upgrades and changes**

## Quick Start

### After FreeCAD Upgrade

```bash
# 1. Run upgrade test suite (25 minutes)
freecadcmd tests/run_regression.py upgrade

# 2. If all tests pass, update baseline
freecadcmd tests/run_regression.py upgrade --baseline
```

### Quick Smoke Test

```bash
# Fast smoke test (8 minutes)
freecadcmd tests/run_regression.py quick
```

### Full Regression

```bash
# Comprehensive test before release (45 minutes)
freecadcmd tests/run_regression.py regression --baseline
```

## Test Suites

### `quick` - Fast Smoke Test (8 min)
**When to run:**
- After any FreeCAD upgrade
- After MCP code changes
- Before committing code
- Daily development check

**What it tests:**
- P0 (Critical) modules only
- Import compatibility
- Basic smoke tests
- CAM version detection

**Coverage:** CAM workbench, core handlers

---

### `upgrade` - Post-Upgrade Suite (25 min)
**When to run:**
- After FreeCAD version upgrade
- After FreeCAD build change
- When CAM workbench updates

**What it tests:**
- P0 (Critical) modules - full suite
- P1 (High priority) modules - full suite
- P2 (Medium priority) modules - smoke tests

**Coverage:** CAM, PartDesign, Part, core handlers

---

### `regression` - Full Regression (45 min)
**When to run:**
- Weekly regression check
- Before MCP releases
- After major code changes
- Quarterly full validation

**What it tests:**
- All priority levels (P0-P3)
- Smoke + full + edge case tests
- Performance benchmarks
- Compatibility matrix

**Coverage:** All workbenches, all handlers

---

### `cam` - CAM-Focused Testing (15 min)
**When to run:**
- After CAM workbench changes in FreeCAD
- When CAM regressions detected
- During CAM feature development

**What it tests:**
- Deep CAM testing
- All CRUD operations
- Edge cases
- Performance benchmarks

**Coverage:** CAM workbench only (comprehensive)

## Priority Levels

### P0: Critical - Always Test
**Modules:** CAM workbench, core handlers

**Why P0:**
- Active development in FreeCAD
- Version-sensitive (module restructuring)
- Large API surface
- High usage

**Includes:**
- `cam_ops` - CAM operations
- `cam_tools` - Tool CRUD
- `cam_tool_controllers` - Tool controller CRUD
- `document_ops` - Document management
- `primitives` - Basic shapes
- `boolean_ops` - Boolean operations

---

### P1: High - Test on Major Upgrades
**Modules:** PartDesign, Part workbenches

**Why P1:**
- Stable APIs
- High usage
- Less frequent changes

**Includes:**
- `partdesign_ops` - Pad, pocket, fillet, etc.
- `part_ops` - Boolean, loft, sweep, etc.

---

### P2: Medium - Spot Check
**Modules:** Draft, Measurement, View

**Why P2:**
- Very stable APIs
- Moderate usage
- Infrequent changes

**Includes:**
- `draft_ops` - Draft workbench
- `measurement_ops` - Measurements
- `view_ops` - View controls

---

### P3: Low - Quarterly Check
**Modules:** Spreadsheet, utilities

**Why P3:**
- Extremely stable
- Simple operations
- Rare changes

**Includes:**
- `spreadsheet_ops` - Spreadsheet operations

## Result Storage

### Directory Structure

```
tests/
├── results/
│   ├── history/           # Historical results (90 day retention)
│   │   ├── 2025-12-10_103045_fc-1.0.2-38156_results.json
│   │   ├── 2025-12-09_151230_fc-1.0.0-38100_results.json
│   │   └── ...
│   ├── current/           # Current baseline and latest results
│   │   ├── baseline.json
│   │   ├── latest_results.json
│   │   └── comparison.html
│   └── reports/           # Human-readable reports
│       ├── upgrade_report_2025-12-10.md
│       └── regression_summary.md
```

### Result Format

```json
{
  "test_run": {
    "timestamp": "2025-12-10T10:30:45",
    "freecad_version": "1.0.2",
    "freecad_build": "38156",
    "platform": "macOS",
    "python_version": "3.11.6"
  },
  "suite": "upgrade",
  "modules": {
    "cam_workbench": {
      "priority": "P0_CRITICAL",
      "status": "PASS",
      "duration": 12.5,
      "tests_passed": 45,
      "tests_failed": 0,
      "tests": [...]
    }
  },
  "summary": {
    "total_modules": 5,
    "modules_passed": 5,
    "modules_failed": 0,
    "total_tests": 120,
    "tests_passed": 120,
    "tests_failed": 0,
    "total_duration": 1502.3
  },
  "regressions": [],
  "performance_changes": []
}
```

## Baseline Management

### What is a Baseline?

A baseline is a "known-good" test result that serves as the comparison point for future test runs. Baselines are version-specific.

### When to Update Baseline

Update the baseline when:
1. ✅ All tests pass after FreeCAD upgrade
2. ✅ New FreeCAD version detected
3. ✅ Manual request (you verify tests are correct)

**DO NOT update baseline when:**
- ❌ Tests are failing
- ❌ You haven't reviewed the results
- ❌ Unsure about test validity

### How to Update Baseline

```bash
# After upgrade, if all tests pass:
freecadcmd tests/run_regression.py upgrade --baseline

# Or manually copy latest to baseline:
cp tests/results/current/latest_results.json tests/results/current/baseline.json
```

### Comparing Against Baseline

```bash
# Run tests and compare with baseline
freecadcmd tests/run_regression.py upgrade --compare
```

This will show:
- **New failures** - Tests that passed in baseline but fail now
- **Regressions** - Functionality that worked before but doesn't now
- **Performance changes** - Tests that are >20% slower
- **Fixed issues** - Tests that failed in baseline but pass now

## Retention Policy

### Test Results
- **Detailed results:** Kept for **90 days**
- **Baseline versions:** Last **5 baselines** kept permanently
- **Summary reports:** Kept indefinitely

### Cleanup

```bash
# Automatic cleanup (runs weekly)
python tests/cleanup_old_results.py

# Manual cleanup
python tests/cleanup_old_results.py --days 60  # Keep 60 days instead
```

## Common Workflows

### After FreeCAD Upgrade (2 builds)

```bash
# 1. Run quick smoke test first
freecadcmd tests/run_regression.py quick

# 2. If quick passes, run full upgrade suite
freecadcmd tests/run_regression.py upgrade

# 3. If all tests pass, update baseline
freecadcmd tests/run_regression.py upgrade --baseline

# 4. Save upgrade report
cp tests/results/current/latest_results.json \
   tests/results/history/$(date +%Y-%m-%d)_upgrade_report.json
```

### CAM Workbench Update Detected

```bash
# Run CAM-focused tests
freecadcmd tests/run_regression.py cam

# If failures, investigate:
cat tests/results/current/latest_results.json | jq '.modules.cam_workbench'
```

### Before MCP Release

```bash
# Full regression with baseline comparison
freecadcmd tests/run_regression.py regression --baseline --compare

# Generate release report
python tests/generate_release_report.py
```

### Weekly Regression Check

```bash
# Automated weekly check (add to cron)
0 2 * * 1 freecadcmd /path/to/tests/run_regression.py regression --compare
```

## Interpreting Results

### Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

### Result Status

- **PASS** ✓ - All tests passed
- **FAIL** ✗ - One or more tests failed
- **PARTIAL** ⚠️ - Some tests skipped but others passed
- **SKIPPED** ℹ️ - Test not implemented yet

### Console Output

```
==================================================================
FreeCAD MCP Regression Test Suite: UPGRADE
==================================================================

FreeCAD: 1.0.2 (build 38156)
Platform: macOS
Estimated time: 25 minutes

Testing 5 module(s):
  - cam_workbench
  - core_handlers
  - partdesign_workbench
  - part_workbench
  - draft_workbench

──────────────────────────────────────────────────────────────
Testing: cam_workbench (P0_CRITICAL)
Reason: Active development, version-sensitive, module restructuring
──────────────────────────────────────────────────────────────

  Testing handler: cam_ops...
    ✓ cam_ops_import
  Testing handler: cam_tools...
    ✓ cam_tools_import
  Testing handler: cam_tool_controllers...
    ✓ cam_tool_controllers_import

✓ cam_workbench: PASS (3 passed, 0 failed, 1.23s)

...

==================================================================
TEST SUMMARY
==================================================================

Modules: 5/5 passed
Tests:   45/45 passed

Duration: 1502.34s (25.0 minutes)

✅ REGRESSION SUITE: PASSED
```

## Troubleshooting

### Tests Fail After FC Upgrade

1. **Check FreeCAD version compatibility**
   ```bash
   freecadcmd --version
   ```

2. **Review failure details**
   ```bash
   cat tests/results/current/latest_results.json | jq '.modules.cam_workbench'
   ```

3. **Check for module restructuring**
   - Look for ImportError messages
   - Check if Path.Main vs Path imports changed

4. **Compare with baseline**
   ```bash
   freecadcmd tests/run_regression.py upgrade --compare
   ```

### Performance Degradation Detected

1. **Review performance changes**
   ```json
   "performance_changes": [
     {
       "test": "cam_tools.create_tool",
       "baseline_duration": 0.15,
       "current_duration": 0.35,
       "change_percent": 133.3,
       "status": "SLOWER"
     }
   ]
   ```

2. **Investigate cause:**
   - Check FreeCAD changelog
   - Review debug logs (`/tmp/freecad_mcp_debug/`)
   - Profile the operation

### CAM Module Import Errors

This usually indicates FreeCAD version incompatibility:

```python
ImportError: cannot import name 'Job' from 'Path'
```

**Solution:** Check `cam_ops.py` for version compatibility code:
```python
try:
    from Path.Main.Job import Create  # FreeCAD 1.0+
except ImportError:
    from Path.Job import Create  # FreeCAD < 1.0
```

## Future Enhancements

- [ ] HTML report generation
- [ ] Performance trending graphs
- [ ] Slack/email notifications
- [ ] CI/CD integration
- [ ] Automated baseline updates
- [ ] Visual diff of results
- [ ] Test coverage metrics
- [ ] Parallel test execution (when FC supports it)

## See Also

- [test_config.py](test_config.py) - Test configuration
- [CAM_CRUD_OPERATIONS.md](../docs/CAM_CRUD_OPERATIONS.md) - CAM operations reference
- [TESTING.md](TESTING.md) - Manual testing guide

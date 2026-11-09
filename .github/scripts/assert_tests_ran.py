"""Fail unless a junit file shows at least one test that actually PASSED.

A run where every test skips exits 0, which would let a broken setup (missing FreeCAD
binary, bad env) report green while testing nothing. integration-tests.yml has the same
guard for the Linux suite; this is the Windows equivalent.
"""

import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]
try:
    root = ET.parse(path).getroot()
except (OSError, ET.ParseError) as e:
    sys.exit(f"::error::cannot read {path}: {e} (the pytest step produced no junit file)")

passed = skipped = bad = 0
for case in root.iter("testcase"):
    kinds = {child.tag for child in case}
    if kinds & {"failure", "error"}:
        bad += 1
    elif "skipped" in kinds:
        skipped += 1
    else:
        passed += 1
print(f"{path}: passed={passed} skipped={skipped} failed_or_error={bad}")
if passed == 0:
    sys.exit(f"::error::{path}: no test passed (skipped={skipped}, failed={bad}); treating as failure so a broken setup cannot look green")

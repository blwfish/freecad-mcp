"""API-surface drift test (Goal 2, SPEC-fc-api-drift-detection.md).

Detects upstream FreeCAD API drift on the Type::String feature-creation
surface -- every doc.addObject("Type::String", ...)/body.newObject(...)
call site in AICopilot/handlers/*.py and the properties handler code sets
on the result. This is the confirmed-blocker mechanism the spec describes:
introspection_ops.inspect() cannot see any of this surface at all (it's a
C++ type-registry string lookup, not a Python attribute path), so this test
covers it via a second resolver -- instantiate a scratch object of each
Type::String and read its live PropertiesList instead of a function
signature.

Three real failure outcomes, each asserted separately so a failure message
names exactly which one fired (see _api_surface_diff.diff_snapshots'
docstring for the full shape):

  - a property the handlers set no longer exists on the live type
    (renamed/removed upstream)
  - a property still exists but its FreeCAD property-type changed
    (e.g. App::PropertyLength -> App::PropertyDistance)
  - the whole Type::String no longer resolves (renamed/removed C++ type)

Plus one infrastructure-freshness check, not a drift finding: if handler
code starts creating a Type::String or setting a property the checked-in
snapshot doesn't know about, that means the snapshot is stale and must be
regenerated deliberately (see generate_api_surface_snapshot.py's module
docstring) -- silently skipping the new entry would mean this test starts
covering less than it says it does without anyone noticing.

Run with: python3 -m pytest tests/integration/test_api_surface.py -v
Regenerate the snapshot with:
    python3 -m tests.integration.generate_api_surface_snapshot
"""

import json
import os

import pytest

from . import conftest as _conftest  # noqa: F401
from ._api_surface_diff import diff_snapshots
from ._api_surface_remote import build_remote_scan_code
from ._api_surface_scan import scan_type_properties
from .test_e2e_workflows import send_command

SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_surface_snapshot.json")


def _load_snapshot() -> dict:
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


def _live_scan(scope: dict) -> dict:
    code = build_remote_scan_code(scope)
    resp = send_command("execute_python", {"code": code}, timeout=60.0)
    assert "error" not in resp, f"execute_python failed while live-scanning API surface: {resp}"
    result_str = resp.get("result", "")
    try:
        return json.loads(result_str)
    except json.JSONDecodeError:
        pytest.fail(
            f"Expected the remote scan to print exactly one JSON line; "
            f"got unparseable output: {result_str[:2000]!r}"
        )


@pytest.fixture(scope="module")
def surface_diff():
    scope = scan_type_properties()
    golden = _load_snapshot()
    live = _live_scan(scope)
    return diff_snapshots(scope, golden, live)


@pytest.mark.integration
class TestApiSurfaceDrift:
    def test_snapshot_file_exists(self):
        assert os.path.isfile(SNAPSHOT_PATH), (
            "api_surface_snapshot.json is missing -- generate it with "
            "`python3 -m tests.integration.generate_api_surface_snapshot` "
            "against a known-good FreeCAD build and commit the result."
        )

    def test_scope_is_not_trivially_empty(self):
        """full-review 2026-07-24 finding #11/#H15: nothing previously
        asserted that scan_type_properties() against the real handlers
        directory returns anything at all. If HANDLERS_DIR ever silently
        resolves to a missing/renamed/empty directory, scope becomes {} and
        every other assertion in this class passes vacuously -- this is the
        backstop for the entire suite's ability to catch that, not a drift
        finding itself. The exact count isn't pinned (that would make this
        test itself brittle against legitimate handler growth/shrinkage) --
        only that scanning the real, current handler tree finds a
        realistic amount of Type::String surface."""
        scope = scan_type_properties()
        assert len(scope) > 10, (
            f"scan_type_properties() against the real handlers directory found only "
            f"{len(scope)} Type::String identifiers -- expected dozens. This usually "
            f"means HANDLERS_DIR resolved wrong (see _api_surface_scan.py), silently "
            f"disabling every other test in this file."
        )

    def test_snapshot_covers_current_scope(self, surface_diff):
        """Every Type::String (and property on it) current handler code
        actually uses must already be represented in the golden snapshot --
        otherwise a handler started depending on new FreeCAD surface since
        the last deliberate regen, and this test is silently covering less
        than it claims to."""
        stale = surface_diff["stale_snapshot_types"]
        assert not stale, (
            f"Type(s) used by handler code but absent from "
            f"api_surface_snapshot.json: {stale}. Regenerate deliberately: "
            f"`python3 -m tests.integration.generate_api_surface_snapshot`"
        )

    def test_no_type_stopped_resolving(self, surface_diff):
        """A Type::String that instantiated cleanly at snapshot time must
        still instantiate cleanly -- an error here means the upstream C++
        type was renamed or removed."""
        broken = surface_diff["types_no_longer_resolve"]
        assert not broken, f"Type(s) no longer resolve on this FreeCAD build: {broken}"

    def test_no_property_removed(self, surface_diff):
        """A property handler code sets must still exist on the live
        object -- missing here means it was renamed or removed upstream."""
        removed = surface_diff["properties_removed"]
        assert not removed, f"Propert(y/ies) removed upstream: {removed}"

    def test_no_property_type_changed(self, surface_diff):
        """A property's FreeCAD property-type-id must be stable -- e.g. a
        property silently promoted from App::PropertyLength to
        App::PropertyDistance would change semantics (default units,
        validation) without renaming anything, invisible to a plain
        hasattr() check."""
        changed = surface_diff["properties_changed"]
        assert not changed, f"Propert(y/ies) changed type upstream: {changed}"

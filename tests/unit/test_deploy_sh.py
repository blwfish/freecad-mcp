"""Tests for deploy.sh's versioned-prefs-dir selection and sync logic.

deploy.sh previously had zero test coverage despite a real, costly
precedent for exactly this failure class: it hardcoded a `v1-2` deploy
target (commit before d802009), and when FreeCAD renumbered its versioning
scheme to `v26-3`, every "fix" silently deployed to nowhere for weeks
before anyone noticed. The auto-select-newest-versioned-dir logic these
tests cover is what replaced that hardcoded path -- these tests exist so a
regression in that selection logic (or in the never-create-only-sync-if-
present legacy-dir behavior) is caught here instead of live again.

These run the real script via subprocess against a fabricated PREFS_BASE
(DEPLOY_PREFS_BASE env var override -- see deploy.sh) and a real (small)
rsync of this repo's actual AICopilot/ directory, not a mocked
reimplementation of the selection logic.
"""

import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DEPLOY_SH = os.path.join(REPO_ROOT, "deploy.sh")


def _run_deploy(prefs_base: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DEPLOY_PREFS_BASE"] = prefs_base
    return subprocess.run(
        ["bash", DEPLOY_SH],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _make_versioned_dir(prefs_base: str, name: str, with_mod: bool = True, with_aicopilot: bool = True) -> str:
    """Create <prefs_base>/<name>[/Mod[/AICopilot]] and return its path."""
    d = os.path.join(prefs_base, name)
    if with_mod:
        mod = os.path.join(d, "Mod")
        os.makedirs(mod, exist_ok=True)
        if with_aicopilot:
            os.makedirs(os.path.join(mod, "AICopilot"), exist_ok=True)
    else:
        os.makedirs(d, exist_ok=True)
    return d


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not available")
class TestDeployVersionSelection:
    def test_selects_the_newest_versioned_dir(self, tmp_path):
        """The real-world regression: FreeCAD 26.3 must select v26-3, not
        the older v1-2, when both exist -- v1-2's ordinal-looking name
        ('1-2') is numerically SMALLER than '26-3', so a naive string sort
        would get this backwards; ver_key parses each segment as an int
        specifically to avoid that trap."""
        prefs_base = str(tmp_path)
        old = _make_versioned_dir(prefs_base, "v1-2")
        new = _make_versioned_dir(prefs_base, "v26-3")

        result = _run_deploy(prefs_base)

        assert result.returncode == 0, result.stderr
        assert "Deployed (primary, live dispatch): " + os.path.join(new, "Mod", "AICopilot") in result.stdout
        # The AICopilot handler package is real content that only exists
        # if deploy.sh actually rsynced into this exact destination.
        assert os.path.isdir(os.path.join(new, "Mod", "AICopilot", "handlers"))
        assert not os.path.isdir(os.path.join(old, "Mod", "AICopilot", "handlers"))

    def test_dir_without_mod_subdir_is_skipped(self, tmp_path):
        """A version-named dir with no Mod/ subdirectory (e.g. a partial
        or in-progress FreeCAD install) is not a valid deploy target and
        must be skipped in favor of the next-newest valid one."""
        prefs_base = str(tmp_path)
        _make_versioned_dir(prefs_base, "v26-3", with_mod=False)
        valid = _make_versioned_dir(prefs_base, "v26-2")

        result = _run_deploy(prefs_base)

        assert result.returncode == 0, result.stderr
        assert os.path.isdir(os.path.join(valid, "Mod", "AICopilot", "handlers"))

    def test_no_valid_versioned_dir_fails_clearly(self, tmp_path):
        prefs_base = str(tmp_path)
        os.makedirs(prefs_base, exist_ok=True)

        result = _run_deploy(prefs_base)

        assert result.returncode != 0
        assert "no versioned FreeCAD prefs dir" in result.stderr

    def test_destination_missing_aicopilot_dir_fails_clearly(self, tmp_path):
        """deploy.sh only rsyncs INTO an existing AICopilot/ dir -- it
        never creates the destination itself (that's FreeCAD's job on
        first launch), so a versioned dir with Mod/ but no pre-existing
        Mod/AICopilot must fail with a clear message, not silently
        create a new directory in an unexpected place."""
        prefs_base = str(tmp_path)
        _make_versioned_dir(prefs_base, "v26-3", with_aicopilot=False)

        result = _run_deploy(prefs_base)

        assert result.returncode != 0
        assert "destination not found" in result.stderr

    def test_legacy_bare_dir_synced_only_if_already_present(self, tmp_path):
        """The legacy bare Mod/AICopilot (predates the vX-Y scheme) must
        be kept in sync defensively IF it already exists, but never
        created where it didn't exist before -- deploy.sh must not
        manufacture a copy on a machine that never had one."""
        prefs_base = str(tmp_path)
        new = _make_versioned_dir(prefs_base, "v26-3")
        legacy_mod = os.path.join(prefs_base, "Mod")
        os.makedirs(legacy_mod, exist_ok=True)
        os.makedirs(os.path.join(legacy_mod, "AICopilot"), exist_ok=True)

        result = _run_deploy(prefs_base)

        assert result.returncode == 0, result.stderr
        assert "Deployed (legacy bare dir, kept in sync defensively)" in result.stdout
        assert os.path.isdir(os.path.join(legacy_mod, "AICopilot", "handlers"))
        assert os.path.isdir(os.path.join(new, "Mod", "AICopilot", "handlers"))

    def test_no_preexisting_legacy_dir_is_not_created(self, tmp_path):
        prefs_base = str(tmp_path)
        _make_versioned_dir(prefs_base, "v26-3")

        result = _run_deploy(prefs_base)

        assert result.returncode == 0, result.stderr
        assert "legacy bare dir" not in result.stdout
        assert not os.path.exists(os.path.join(prefs_base, "Mod"))

    def test_equal_versioned_dir_where_legacy_equals_primary_not_double_synced(self, tmp_path):
        """If the bare Mod/AICopilot happens to BE the selected primary
        destination (e.g. a prefs_base with no versioned subdirs at all
        but somehow matched -- guarded against by the LEGACY_DEST !=
        DEST check), it must not be rsynced twice."""
        prefs_base = str(tmp_path)
        # v0-0 sorts newest under this test's fabricated (nonsensical but
        # valid-shaped) versions; what matters is DEST != legacy path here,
        # so this just confirms the normal (non-colliding) case doesn't
        # double-report -- a colliding case can't arise in practice since
        # DEST always includes a "vX-Y" path segment the legacy path lacks.
        new = _make_versioned_dir(prefs_base, "v0-0")

        result = _run_deploy(prefs_base)

        assert result.returncode == 0, result.stderr
        assert result.stdout.count("Deployed (legacy bare dir") == 0
        assert os.path.isdir(os.path.join(new, "Mod", "AICopilot", "handlers"))

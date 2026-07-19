"""Unit tests for AICopilot/tmp_safety.py (M14).

/tmp is world-writable and the debug/crash/last-op paths in this codebase
(freecad_debug.py, freecad_health.py, crash_watcher.py) use fixed,
predictable names. A symlink planted at one of those paths before this
process starts would otherwise be silently followed by mkdir(exist_ok=True)
or open(path, "w") — both resolve symlinks by design — redirecting
debug/crash writes to an attacker-chosen location with no warning.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

import tmp_safety


class TestRefuseIfSymlink:
    def test_no_op_when_path_does_not_exist(self, tmp_path):
        target = tmp_path / "does_not_exist"
        tmp_safety.refuse_if_symlink(str(target))  # must not raise

    def test_no_op_for_a_real_file(self, tmp_path):
        target = tmp_path / "real_file"
        target.write_text("x")
        tmp_safety.refuse_if_symlink(str(target))  # must not raise

    def test_no_op_for_a_real_directory(self, tmp_path):
        target = tmp_path / "real_dir"
        target.mkdir()
        tmp_safety.refuse_if_symlink(str(target))  # must not raise

    def test_raises_for_a_symlink(self, tmp_path):
        real_target = tmp_path / "elsewhere"
        real_target.mkdir()
        link = tmp_path / "planted_symlink"
        link.symlink_to(real_target)

        with pytest.raises(RuntimeError, match="symlink"):
            tmp_safety.refuse_if_symlink(str(link))

    def test_raises_for_a_dangling_symlink(self, tmp_path):
        """A symlink to a nonexistent target is still a symlink — must be
        refused too, not treated as "doesn't exist yet, fine"."""
        link = tmp_path / "dangling_symlink"
        link.symlink_to(tmp_path / "nonexistent_target")

        with pytest.raises(RuntimeError, match="symlink"):
            tmp_safety.refuse_if_symlink(str(link))


class TestSafeMkdir:
    def test_creates_directory_with_requested_mode(self, tmp_path):
        target = tmp_path / "newdir"
        tmp_safety.safe_mkdir(str(target), mode=0o700)
        assert target.is_dir()
        assert (os.stat(str(target)).st_mode & 0o777) == 0o700

    def test_creates_intermediate_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        tmp_safety.safe_mkdir(str(target))
        assert target.is_dir()

    def test_existing_real_directory_is_fine(self, tmp_path):
        target = tmp_path / "existing"
        target.mkdir()
        tmp_safety.safe_mkdir(str(target))  # must not raise

    def test_refuses_a_planted_symlink_instead_of_using_it(self, tmp_path):
        """The core scenario this module exists for: an attacker plants a
        symlink at the well-known debug/crash directory path before this
        process starts. Naive mkdir(exist_ok=True) would silently treat
        the symlink target as the log directory (os.path.isdir() follows
        symlinks) and all subsequent writes would go through it."""
        attacker_target = tmp_path / "attacker_controlled"
        attacker_target.mkdir()
        victim_path = tmp_path / "freecad_mcp_debug"
        victim_path.symlink_to(attacker_target)

        with pytest.raises(RuntimeError, match="symlink"):
            tmp_safety.safe_mkdir(str(victim_path))

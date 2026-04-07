"""Unit tests for mcp_versions module.

Tests VersionSpec parsing, comparison operators, VersionRegistry lifecycle,
and the global convenience functions.

Run with: python3 -m pytest tests/unit/test_mcp_versions.py -v
"""

import os
import sys
import pytest

# Ensure AICopilot is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'AICopilot'))

from mcp_versions import VersionSpec, VersionRegistry, get_registry
import mcp_versions


# ---------------------------------------------------------------------------
# VersionSpec._parse_semver
# ---------------------------------------------------------------------------

class TestParseSemver:
    """Tests for VersionSpec._parse_semver static method."""

    def test_basic_version(self):
        assert VersionSpec._parse_semver("1.2.3") == (1, 2, 3)

    def test_zero_version(self):
        assert VersionSpec._parse_semver("0.0.0") == (0, 0, 0)

    def test_large_numbers(self):
        assert VersionSpec._parse_semver("100.200.300") == (100, 200, 300)

    def test_leading_whitespace(self):
        assert VersionSpec._parse_semver("  1.2.3") == (1, 2, 3)

    def test_trailing_whitespace(self):
        assert VersionSpec._parse_semver("1.2.3  ") == (1, 2, 3)

    def test_trailing_text_ignored(self):
        # regex matches prefix, so trailing text is OK
        assert VersionSpec._parse_semver("1.2.3-beta") == (1, 2, 3)

    def test_invalid_missing_patch(self):
        with pytest.raises(ValueError, match="Invalid semantic version"):
            VersionSpec._parse_semver("1.2")

    def test_invalid_single_number(self):
        with pytest.raises(ValueError, match="Invalid semantic version"):
            VersionSpec._parse_semver("1")

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError, match="Invalid semantic version"):
            VersionSpec._parse_semver("")

    def test_invalid_letters(self):
        with pytest.raises(ValueError, match="Invalid semantic version"):
            VersionSpec._parse_semver("abc")

    def test_invalid_no_digits(self):
        with pytest.raises(ValueError, match="Invalid semantic version"):
            VersionSpec._parse_semver("...")


# ---------------------------------------------------------------------------
# VersionSpec parsing (constructor)
# ---------------------------------------------------------------------------

class TestVersionSpecParsing:
    """Tests for VersionSpec constraint parsing."""

    def test_no_operator_defaults_to_equal(self):
        spec = VersionSpec("1.2.3")
        assert spec.constraint_op == "=="
        assert spec.constraint_version == (1, 2, 3)

    def test_double_equal_operator(self):
        spec = VersionSpec("==1.2.3")
        assert spec.constraint_op == "=="
        assert spec.constraint_version == (1, 2, 3)

    def test_gte_operator(self):
        spec = VersionSpec(">=2.0.0")
        assert spec.constraint_op == ">="
        assert spec.constraint_version == (2, 0, 0)

    def test_lte_operator(self):
        spec = VersionSpec("<=3.1.0")
        assert spec.constraint_op == "<="
        assert spec.constraint_version == (3, 1, 0)

    def test_gt_operator(self):
        spec = VersionSpec(">1.0.0")
        assert spec.constraint_op == ">"
        assert spec.constraint_version == (1, 0, 0)

    def test_lt_operator(self):
        spec = VersionSpec("<2.0.0")
        assert spec.constraint_op == "<"
        assert spec.constraint_version == (2, 0, 0)

    def test_ne_operator(self):
        spec = VersionSpec("!=1.0.0")
        assert spec.constraint_op == "!="
        assert spec.constraint_version == (1, 0, 0)

    def test_tilde_operator(self):
        spec = VersionSpec("~1.2.0")
        assert spec.constraint_op == "~"
        assert spec.constraint_version == (1, 2, 0)

    def test_operator_with_space(self):
        spec = VersionSpec(">= 1.0.0")
        assert spec.constraint_op == ">="
        assert spec.constraint_version == (1, 0, 0)

    def test_str_representation(self):
        spec = VersionSpec(">=1.2.3")
        assert str(spec) == ">=1.2.3"

    def test_repr_representation(self):
        spec = VersionSpec(">=1.2.3")
        assert repr(spec) == "VersionSpec('>=1.2.3')"

    def test_original_preserved(self):
        spec = VersionSpec("~1.0.5")
        assert spec.original == "~1.0.5"


# ---------------------------------------------------------------------------
# VersionSpec.satisfies — each operator
# ---------------------------------------------------------------------------

class TestVersionSpecSatisfies:
    """Tests for VersionSpec.satisfies method."""

    # == (exact match)
    def test_equal_match(self):
        assert VersionSpec("==1.2.3").satisfies("1.2.3") is True

    def test_equal_no_match(self):
        assert VersionSpec("==1.2.3").satisfies("1.2.4") is False

    def test_equal_implicit(self):
        assert VersionSpec("1.2.3").satisfies("1.2.3") is True

    def test_equal_implicit_no_match(self):
        assert VersionSpec("1.2.3").satisfies("2.0.0") is False

    # >=
    def test_gte_equal(self):
        assert VersionSpec(">=1.2.0").satisfies("1.2.0") is True

    def test_gte_greater_patch(self):
        assert VersionSpec(">=1.2.0").satisfies("1.2.1") is True

    def test_gte_greater_minor(self):
        assert VersionSpec(">=1.2.0").satisfies("1.3.0") is True

    def test_gte_greater_major(self):
        assert VersionSpec(">=1.2.0").satisfies("2.0.0") is True

    def test_gte_less(self):
        assert VersionSpec(">=1.2.0").satisfies("1.1.9") is False

    # >
    def test_gt_greater(self):
        assert VersionSpec(">1.0.0").satisfies("1.0.1") is True

    def test_gt_equal(self):
        assert VersionSpec(">1.0.0").satisfies("1.0.0") is False

    def test_gt_less(self):
        assert VersionSpec(">1.0.0").satisfies("0.9.9") is False

    # <=
    def test_lte_equal(self):
        assert VersionSpec("<=2.0.0").satisfies("2.0.0") is True

    def test_lte_less(self):
        assert VersionSpec("<=2.0.0").satisfies("1.9.9") is True

    def test_lte_greater(self):
        assert VersionSpec("<=2.0.0").satisfies("2.0.1") is False

    # <
    def test_lt_less(self):
        assert VersionSpec("<2.0.0").satisfies("1.9.9") is True

    def test_lt_equal(self):
        assert VersionSpec("<2.0.0").satisfies("2.0.0") is False

    def test_lt_greater(self):
        assert VersionSpec("<2.0.0").satisfies("2.0.1") is False

    # !=
    def test_ne_different(self):
        assert VersionSpec("!=1.0.0").satisfies("1.0.1") is True

    def test_ne_same(self):
        assert VersionSpec("!=1.0.0").satisfies("1.0.0") is False

    # ~ (tilde — same major.minor, patch >= required)
    def test_tilde_exact(self):
        assert VersionSpec("~1.2.3").satisfies("1.2.3") is True

    def test_tilde_higher_patch(self):
        assert VersionSpec("~1.2.3").satisfies("1.2.5") is True

    def test_tilde_lower_patch(self):
        assert VersionSpec("~1.2.3").satisfies("1.2.2") is False

    def test_tilde_different_minor(self):
        assert VersionSpec("~1.2.3").satisfies("1.3.0") is False

    def test_tilde_different_major(self):
        assert VersionSpec("~1.2.3").satisfies("2.2.3") is False

    def test_tilde_zero_patch(self):
        # ~1.2.0 should accept 1.2.0 and above within 1.2.x
        assert VersionSpec("~1.2.0").satisfies("1.2.0") is True
        assert VersionSpec("~1.2.0").satisfies("1.2.99") is True

    # Cross-component tuple comparison
    def test_major_version_comparison(self):
        # (2, 0, 0) > (1, 9, 9) — major wins
        assert VersionSpec(">=2.0.0").satisfies("1.9.9") is False

    def test_minor_version_comparison(self):
        # (1, 3, 0) > (1, 2, 9) — minor wins
        assert VersionSpec(">=1.3.0").satisfies("1.2.9") is False
        assert VersionSpec(">=1.2.9").satisfies("1.3.0") is True


# ---------------------------------------------------------------------------
# VersionRegistry
# ---------------------------------------------------------------------------

class TestVersionRegistry:
    """Tests for VersionRegistry class."""

    def test_register_component(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        assert reg.versions["handler"] == "2.0.0"

    def test_register_with_loaded_at(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0", loaded_at="2026-04-07T12:00:00")
        assert reg.loaded_at["handler"] == "2026-04-07T12:00:00"

    def test_register_without_loaded_at(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        assert "handler" not in reg.loaded_at

    def test_register_overwrites(self):
        reg = VersionRegistry()
        reg.register("handler", "1.0.0")
        reg.register("handler", "2.0.0")
        assert reg.versions["handler"] == "2.0.0"

    def test_declare_requirements(self):
        reg = VersionRegistry()
        reqs = {"debug": ">=1.1.0", "health": ">=1.0.1"}
        reg.declare_requirements("handler", reqs)
        assert reg.requirements["handler"] == reqs

    def test_validate_all_satisfied(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.1.0")
        reg.register("health", "1.0.1")
        reg.declare_requirements("handler", {
            "debug": ">=1.1.0",
            "health": ">=1.0.1",
        })
        valid, error = reg.validate()
        assert valid is True
        assert error is None

    def test_validate_version_too_low(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.0.0")  # too low
        reg.declare_requirements("handler", {"debug": ">=1.1.0"})
        valid, error = reg.validate()
        assert valid is False
        assert "Version mismatch" in error
        assert "debug" in error
        assert ">=1.1.0" in error
        assert "1.0.0" in error

    def test_validate_missing_dependency(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.declare_requirements("handler", {"debug": ">=1.0.0"})
        valid, error = reg.validate()
        assert valid is False
        assert "not loaded" in error
        assert "debug" in error

    def test_validate_no_requirements(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        valid, error = reg.validate()
        assert valid is True
        assert error is None

    def test_validate_exact_version_match(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.1.0")
        reg.declare_requirements("handler", {"debug": "1.1.0"})
        valid, error = reg.validate()
        assert valid is True

    def test_validate_exact_version_mismatch(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.1.1")
        reg.declare_requirements("handler", {"debug": "==1.1.0"})
        valid, error = reg.validate()
        assert valid is False

    def test_validate_tilde_compatibility(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.1.5")
        reg.declare_requirements("handler", {"debug": "~1.1.0"})
        valid, error = reg.validate()
        assert valid is True

    def test_validate_tilde_incompatible(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.2.0")
        reg.declare_requirements("handler", {"debug": "~1.1.0"})
        valid, error = reg.validate()
        assert valid is False

    def test_validate_multiple_components_with_requirements(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        reg.register("debug", "1.1.0")
        reg.register("health", "1.0.1")
        reg.declare_requirements("handler", {"debug": ">=1.1.0"})
        reg.declare_requirements("debug", {"health": ">=1.0.0"})
        valid, error = reg.validate()
        assert valid is True

    def test_get_status(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0", loaded_at="t1")
        reg.register("debug", "1.1.0")
        reg.declare_requirements("handler", {"debug": ">=1.1.0"})

        status = reg.get_status()
        assert status["versions"] == {"handler": "2.0.0", "debug": "1.1.0"}
        assert status["requirements"] == {"handler": {"debug": ">=1.1.0"}}
        assert status["loaded_at"] == {"handler": "t1"}

    def test_get_status_returns_copies(self):
        reg = VersionRegistry()
        reg.register("handler", "2.0.0")
        status = reg.get_status()
        # Mutating the returned dict should not affect the registry
        status["versions"]["handler"] = "9.9.9"
        assert reg.versions["handler"] == "2.0.0"


# ---------------------------------------------------------------------------
# Global functions
# ---------------------------------------------------------------------------

class TestGlobalFunctions:
    """Tests for module-level convenience functions."""

    def setup_method(self):
        # Reset the global registry before each test
        mcp_versions._registry = None

    def test_get_registry_creates_singleton(self):
        reg1 = mcp_versions.get_registry()
        reg2 = mcp_versions.get_registry()
        assert reg1 is reg2

    def test_get_registry_returns_version_registry(self):
        reg = mcp_versions.get_registry()
        assert isinstance(reg, VersionRegistry)

    def test_register_component(self):
        mcp_versions.register_component("handler", "2.0.0")
        reg = mcp_versions.get_registry()
        assert reg.versions["handler"] == "2.0.0"

    def test_register_component_with_loaded_at(self):
        mcp_versions.register_component("handler", "2.0.0", loaded_at="now")
        reg = mcp_versions.get_registry()
        assert reg.loaded_at["handler"] == "now"

    def test_declare_requirements(self):
        mcp_versions.declare_requirements("handler", {"debug": ">=1.0.0"})
        reg = mcp_versions.get_registry()
        assert reg.requirements["handler"] == {"debug": ">=1.0.0"}

    def test_validate_all_pass(self):
        mcp_versions.register_component("handler", "2.0.0")
        mcp_versions.register_component("debug", "1.1.0")
        mcp_versions.declare_requirements("handler", {"debug": ">=1.0.0"})
        valid, error = mcp_versions.validate_all()
        assert valid is True
        assert error is None

    def test_validate_all_fail(self):
        mcp_versions.register_component("handler", "2.0.0")
        mcp_versions.register_component("debug", "0.9.0")
        mcp_versions.declare_requirements("handler", {"debug": ">=1.0.0"})
        valid, error = mcp_versions.validate_all()
        assert valid is False
        assert error is not None

    def test_get_status(self):
        mcp_versions.register_component("handler", "2.0.0")
        status = mcp_versions.get_status()
        assert "versions" in status
        assert status["versions"]["handler"] == "2.0.0"

    def test_fresh_registry_after_reset(self):
        mcp_versions.register_component("handler", "2.0.0")
        mcp_versions._registry = None
        reg = mcp_versions.get_registry()
        assert reg.versions == {}

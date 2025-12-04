#!/usr/bin/env python3
"""
FreeCAD MCP Version Management
==============================

Centralized version tracking and dependency validation for all MCP components.

This module ensures version compatibility across socket_server, freecad_debug,
and freecad_health, with fail-fast behavior on version mismatches.

Author: Brian (with Claude)
Version: 1.0.0
"""

import re
from typing import Dict, Optional, Tuple


__version__ = "1.0.0"


class VersionSpec:
    """Represents a semantic version with comparison operators."""
    
    def __init__(self, version_string: str):
        """
        Parse a semantic version string.
        
        Args:
            version_string: Version like "1.2.3" or constraint like ">=1.1.0"
        """
        self.original = version_string
        self.constraint_op = None
        self.constraint_version = None
        
        # Parse constraint operators
        for op in [">=", "<=", "==", "!=", ">", "<", "~"]:
            if version_string.startswith(op):
                self.constraint_op = op
                version_part = version_string[len(op):].strip()
                self.constraint_version = self._parse_semver(version_part)
                break
        
        if self.constraint_op is None:
            # No operator, treat as exact match
            self.constraint_op = "=="
            self.constraint_version = self._parse_semver(version_string)
    
    @staticmethod
    def _parse_semver(version_str: str) -> Tuple[int, int, int]:
        """Parse semantic version string to (major, minor, patch) tuple."""
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version_str.strip())
        if not match:
            raise ValueError(f"Invalid semantic version: {version_str}")
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    
    def __str__(self) -> str:
        return self.original
    
    def __repr__(self) -> str:
        return f"VersionSpec({self.original!r})"
    
    def satisfies(self, actual_version: str) -> bool:
        """Check if actual version satisfies this constraint."""
        actual = self._parse_semver(actual_version)
        
        if self.constraint_op == "==":
            return actual == self.constraint_version
        elif self.constraint_op == ">=":
            return actual >= self.constraint_version
        elif self.constraint_op == ">":
            return actual > self.constraint_version
        elif self.constraint_op == "<=":
            return actual <= self.constraint_version
        elif self.constraint_op == "<":
            return actual < self.constraint_version
        elif self.constraint_op == "!=":
            return actual != self.constraint_version
        elif self.constraint_op == "~":
            # Tilde: compatible with version (same major.minor)
            req_major, req_minor, _ = self.constraint_version
            act_major, act_minor, act_patch = actual
            return (act_major == req_major and act_minor == req_minor and 
                    act_patch >= self.constraint_version[2])
        
        return False


class VersionRegistry:
    """Registry of component versions with dependency validation."""
    
    def __init__(self):
        """Initialize the version registry."""
        self.versions: Dict[str, str] = {}
        self.requirements: Dict[str, Dict[str, str]] = {}
        self.loaded_at: Dict[str, str] = {}
    
    def register(self, component: str, version: str, loaded_at: Optional[str] = None):
        """
        Register a component version.
        
        Args:
            component: Component name (e.g., "socket_server", "freecad_debug")
            version: Semantic version string (e.g., "1.2.3")
            loaded_at: Optional timestamp of when component was loaded
        """
        self.versions[component] = version
        if loaded_at:
            self.loaded_at[component] = loaded_at
    
    def declare_requirements(self, component: str, requirements: Dict[str, str]):
        """
        Declare version requirements for a component's dependencies.
        
        Args:
            component: Component that has requirements
            requirements: Dict of {dependency: version_constraint}
                         e.g., {"freecad_debug": ">=1.1.0", "freecad_health": ">=1.0.1"}
        """
        self.requirements[component] = requirements
    
    def validate(self) -> Tuple[bool, Optional[str]]:
        """
        Validate all registered requirements.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        for component, requirements in self.requirements.items():
            for dependency, constraint_str in requirements.items():
                if dependency not in self.versions:
                    return False, f"{component} requires {dependency}, but it's not loaded"
                
                actual_version = self.versions[dependency]
                constraint = VersionSpec(constraint_str)
                
                if not constraint.satisfies(actual_version):
                    return False, (
                        f"Version mismatch for {component}: "
                        f"requires {dependency} {constraint_str}, "
                        f"but got {actual_version}"
                    )
        
        return True, None
    
    def get_status(self) -> Dict:
        """Get status of all registered components."""
        return {
            "versions": self.versions.copy(),
            "requirements": self.requirements.copy(),
            "loaded_at": self.loaded_at.copy(),
        }


# Global registry instance
_registry: Optional[VersionRegistry] = None


def get_registry() -> VersionRegistry:
    """Get or create the global version registry."""
    global _registry
    if _registry is None:
        _registry = VersionRegistry()
    return _registry


def register_component(component: str, version: str, loaded_at: Optional[str] = None):
    """Register a component version in the global registry."""
    get_registry().register(component, version, loaded_at)


def declare_requirements(component: str, requirements: Dict[str, str]):
    """Declare requirements in the global registry."""
    get_registry().declare_requirements(component, requirements)


def validate_all() -> Tuple[bool, Optional[str]]:
    """Validate all requirements in the global registry."""
    return get_registry().validate()


def get_status() -> Dict:
    """Get status of all registered components."""
    return get_registry().get_status()


if __name__ == "__main__":
    # Demo usage
    registry = VersionRegistry()
    
    # Register components
    registry.register("socket_server", "2.0.0")
    registry.register("freecad_debug", "1.1.0")
    registry.register("freecad_health", "1.0.1")
    
    # Declare requirements
    registry.declare_requirements("socket_server", {
        "freecad_debug": ">=1.1.0",
        "freecad_health": ">=1.0.1",
    })
    
    # Validate
    valid, error = registry.validate()
    print(f"Valid: {valid}")
    if error:
        print(f"Error: {error}")
    
    print(f"\nStatus:")
    import json
    print(json.dumps(registry.get_status(), indent=2))

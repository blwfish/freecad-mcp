"""
Unit tests for helpers/parametric_helpers.py.

Focus: evaluate_expr identifier substitution. The original implementation
used str.replace to substitute parameter names with their numeric values,
which is substring-based and corrupts names that are substrings of other
names (e.g. param "w" substituted inside identifier "width").
"""

import os
import sys
import pytest


# Make sure the repo's `helpers/` package can be imported. tests/unit/conftest
# already mocks FreeCAD before any import happens.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(autouse=True)
def _patch_freecad_vector(mock_freecad):
    """parametric_helpers does ``from FreeCAD import Vector`` at import time.

    The shared mock_freecad fixture in conftest doesn't define Vector, so add
    a stub before the module under test is imported.
    """
    if not hasattr(mock_freecad, "Vector"):
        mock_freecad.Vector = lambda *a, **kw: None
    yield


def _make_helpers(params: dict):
    """Build a ParametricHelpers with get_param backed by a plain dict.

    Skips real __init__ (which needs an active document + params sheet).
    """
    from helpers.parametric_helpers import ParametricHelpers

    obj = ParametricHelpers.__new__(ParametricHelpers)

    def _get_param(alias):
        if alias in params:
            return params[alias]
        raise ValueError(f"Parameter '{alias}' not found")

    obj.get_param = _get_param  # type: ignore[attr-defined]
    return obj


class TestEvaluateExpr:
    def test_plain_number_string(self):
        ph = _make_helpers({})
        assert ph.evaluate_expr("42") == 42.0

    def test_single_param(self):
        ph = _make_helpers({"width": 100.0})
        assert ph.evaluate_expr("width") == 100.0

    def test_param_with_arithmetic(self):
        ph = _make_helpers({"width": 100.0})
        assert ph.evaluate_expr("width/2") == 50.0

    def test_two_disjoint_params(self):
        ph = _make_helpers({"a": 3, "b": 4})
        assert ph.evaluate_expr("a + b") == 7

    def test_substring_param_names_short_then_long(self):
        """`w` is a substring of `width`; both are valid params.

        Naive str.replace iterates tokens and replaces all occurrences of
        each, so replacing `w` first corrupts `width` to `<val>idth`. The
        semantically correct behaviour is to substitute identifiers, not
        substrings.
        """
        ph = _make_helpers({"w": 3, "width": 100})
        assert ph.evaluate_expr("w + width") == 103

    def test_substring_param_names_long_then_short(self):
        ph = _make_helpers({"w": 3, "width": 100})
        # Same params, opposite source-order: result must not depend on
        # which token the regex happens to yield first.
        assert ph.evaluate_expr("width + w") == 103

    def test_param_name_is_substring_of_non_param_identifier(self):
        """`x` is a param; expression mentions `xMax`, which is not a param.

        Substring replace would mangle `xMax` into `5Max` and the eval would
        fail. Caller has used a parameter that doesn't exist — the right
        outcome is a ValueError from evaluate_expr, not a corrupted token.
        """
        ph = _make_helpers({"x": 5})
        with pytest.raises(ValueError):
            ph.evaluate_expr("x + xMax")

    def test_repeated_param_use(self):
        ph = _make_helpers({"w": 4})
        assert ph.evaluate_expr("w * w") == 16

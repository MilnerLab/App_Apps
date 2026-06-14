"""Unit tests for LLM parameter validation against RoutineSpec (L1)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.assistant.validation import ParamValidationError, validate_params
from app_apps.routines.linear.registry import clear_registry, get_routine, routine


class TestValidation(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()

        @routine("move_it", bounds={"x_mm": (0.0, 10.0)})
        def move_it(lab, x_mm: float, count: int = 1, label: str = "a"):
            """Move."""

        self.spec = get_routine("move_it")
        self.addCleanup(clear_registry)

    def test_valid_args_are_coerced(self) -> None:
        out = validate_params(self.spec, {"x_mm": 5, "count": 2, "label": "z"})
        self.assertEqual(out, {"x_mm": 5.0, "count": 2, "label": "z"})
        self.assertIsInstance(out["x_mm"], float)

    def test_optional_params_may_be_omitted(self) -> None:
        out = validate_params(self.spec, {"x_mm": 1.5})
        self.assertEqual(out, {"x_mm": 1.5})  # count/label fall back to defaults at call time

    def test_missing_required_raises(self) -> None:
        with self.assertRaises(ParamValidationError) as cm:
            validate_params(self.spec, {"count": 2})
        self.assertTrue(any("missing required" in e and "x_mm" in e for e in cm.exception.errors))

    def test_unknown_param_raises(self) -> None:
        with self.assertRaises(ParamValidationError) as cm:
            validate_params(self.spec, {"x_mm": 1.0, "bogus": 9})
        self.assertTrue(any("unknown parameter" in e for e in cm.exception.errors))

    def test_type_mismatch_raises(self) -> None:
        with self.assertRaises(ParamValidationError):
            validate_params(self.spec, {"x_mm": "not-a-number"})

    def test_bool_rejected_as_number(self) -> None:
        with self.assertRaises(ParamValidationError):
            validate_params(self.spec, {"x_mm": True})

    def test_non_integer_float_rejected_for_int(self) -> None:
        with self.assertRaises(ParamValidationError):
            validate_params(self.spec, {"x_mm": 1.0, "count": 2.5})

    def test_out_of_bounds_raises(self) -> None:
        with self.assertRaises(ParamValidationError) as cm:
            validate_params(self.spec, {"x_mm": 50.0})
        self.assertTrue(any("bounds" in e for e in cm.exception.errors))

    def test_errors_aggregate(self) -> None:
        with self.assertRaises(ParamValidationError) as cm:
            validate_params(self.spec, {"count": 2.5, "bogus": 1})
        # missing x_mm + bad count + unknown bogus
        self.assertGreaterEqual(len(cm.exception.errors), 3)


if __name__ == "__main__":
    unittest.main()

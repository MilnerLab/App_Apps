"""Unit tests for the @routine decorator and registry (R.2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.routines.linear.registry import (
    RoutineNotFound,
    RoutineRegistrationError,
    all_routines,
    clear_registry,
    get_routine,
    routine,
    routine_names,
)


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()

    def tearDown(self) -> None:
        clear_registry()

    def test_register_and_get(self) -> None:
        @routine("my_scan")
        def my_scan(lab, n):
            return n

        spec = get_routine("my_scan")
        self.assertEqual(spec.name, "my_scan")
        self.assertIs(spec.func, my_scan)
        # original function is returned unchanged and still callable
        self.assertEqual(my_scan("LAB", 3), 3)
        # spec is callable too, injecting the facade as the first arg
        self.assertEqual(spec("LAB", 5), 5)

    def test_default_name_from_function(self) -> None:
        @routine
        def bare_routine(lab):
            pass

        self.assertIn("bare_routine", routine_names())

    def test_summary_from_docstring(self) -> None:
        @routine("documented")
        def documented(lab):
            """Do the thing.

            More detail here.
            """

        spec = get_routine("documented")
        self.assertEqual(spec.summary, "Do the thing.")
        self.assertIn("More detail", spec.doc)

    def test_params_skip_lab_and_capture_metadata(self) -> None:
        @routine("scan")
        def scan(lab, start_mm: float, stop_mm: float, step_mm: float = 0.1):
            pass

        spec = get_routine("scan")
        names = [p.name for p in spec.params]
        self.assertEqual(names, ["start_mm", "stop_mm", "step_mm"])

        by_name = {p.name: p for p in spec.params}
        self.assertTrue(by_name["start_mm"].required)
        self.assertEqual(by_name["start_mm"].annotation, "float")
        self.assertFalse(by_name["step_mm"].required)
        self.assertEqual(by_name["step_mm"].default, 0.1)

    def test_var_args_are_ignored_in_metadata(self) -> None:
        @routine("flexible")
        def flexible(lab, a, *args, **kwargs):
            pass

        spec = get_routine("flexible")
        self.assertEqual([p.name for p in spec.params], ["a"])

    def test_duplicate_name_different_function_raises(self) -> None:
        @routine("dup")
        def first(lab):
            pass

        with self.assertRaises(RoutineRegistrationError):

            @routine("dup")
            def second(lab):
                pass

    def test_reapplying_decorator_to_same_function_is_ok(self) -> None:
        def same(lab):
            pass

        routine("again")(same)
        routine("again")(same)  # idempotent, no raise
        self.assertIs(get_routine("again").func, same)

    def test_routine_without_lab_param_raises(self) -> None:
        with self.assertRaises(RoutineRegistrationError):

            @routine("no_lab")
            def no_lab():
                pass

    def test_missing_routine_raises(self) -> None:
        with self.assertRaises(RoutineNotFound):
            get_routine("does_not_exist")

    def test_all_routines_returns_copy(self) -> None:
        @routine("a")
        def a(lab):
            pass

        snapshot = all_routines()
        snapshot.clear()
        self.assertIn("a", routine_names())  # registry unaffected


if __name__ == "__main__":
    unittest.main()

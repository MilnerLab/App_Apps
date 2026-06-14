"""Unit tests for the registry -> Claude tool-schema builder (L1)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.assistant.tools import (
    GET_STATUS,
    LIST_ROUTINES,
    PROPOSE_NEW_ROUTINE,
    _json_type,
    build_tools,
    routine_tool_schema,
)
from app_apps.routines.linear.registry import all_routines, clear_registry, get_routine, routine


class TestToolSchemas(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()

        @routine("safe_diag", safe=True)
        def safe_diag(lab, n: int = 5):
            """Read a diagnostic."""

        @routine("move_it", bounds={"x_mm": (0.0, 10.0)})
        def move_it(lab, x_mm: float, label: str = "a"):
            """Move the thing."""

        self.addCleanup(clear_registry)

    def test_json_type_mapping(self) -> None:
        self.assertEqual(_json_type("float"), "number")
        self.assertEqual(_json_type("int"), "integer")
        self.assertEqual(_json_type("bool"), "boolean")
        self.assertEqual(_json_type("str"), "string")
        self.assertEqual(_json_type("Sequence[float]"), "array")
        self.assertEqual(_json_type("Optional[str]"), "string")  # default

    def test_routine_schema_required_types_and_bounds(self) -> None:
        schema = routine_tool_schema(get_routine("move_it"))
        self.assertEqual(schema["name"], "move_it")
        props = schema["input_schema"]["properties"]
        self.assertEqual(props["x_mm"]["type"], "number")
        self.assertEqual(props["x_mm"]["minimum"], 0.0)
        self.assertEqual(props["x_mm"]["maximum"], 10.0)
        self.assertEqual(props["label"]["type"], "string")
        self.assertEqual(schema["input_schema"]["required"], ["x_mm"])
        self.assertIn("requires confirmation", schema["description"])  # unsafe routine

    def test_safe_routine_schema_has_no_confirm_note(self) -> None:
        schema = routine_tool_schema(get_routine("safe_diag"))
        self.assertNotIn("requires confirmation", schema["description"])
        self.assertEqual(schema["input_schema"]["properties"]["n"]["type"], "integer")
        self.assertEqual(schema["input_schema"]["required"], [])  # n has a default

    def test_build_tools_includes_routines_and_meta(self) -> None:
        names = {t["name"] for t in build_tools(all_routines(), include_planner=True)}
        self.assertIn("safe_diag", names)
        self.assertIn("move_it", names)
        self.assertIn(LIST_ROUTINES, names)
        self.assertIn(GET_STATUS, names)
        self.assertIn(PROPOSE_NEW_ROUTINE, names)

    def test_planner_tool_excluded_when_disabled(self) -> None:
        names = {t["name"] for t in build_tools(all_routines(), include_planner=False)}
        self.assertNotIn(PROPOSE_NEW_ROUTINE, names)
        self.assertIn(LIST_ROUTINES, names)


if __name__ == "__main__":
    unittest.main()

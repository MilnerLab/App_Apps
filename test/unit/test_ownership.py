"""Unit tests for the stage-ownership guard."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.control.ownership import StageBusyError, StageOwnership


class TestOwnership(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = StageOwnership()
        self.stage = ("esp301", 2)

    def test_acquire_then_reject_other_owner(self):
        self.assertTrue(self.reg.try_acquire(self.stage, "loopA"))
        self.assertFalse(self.reg.try_acquire(self.stage, "loopB"))
        self.assertEqual(self.reg.owner_of(self.stage), "loopA")

    def test_same_owner_is_idempotent(self):
        self.assertTrue(self.reg.try_acquire(self.stage, "loopA"))
        self.assertTrue(self.reg.try_acquire(self.stage, "loopA"))

    def test_release_frees_stage(self):
        self.reg.try_acquire(self.stage, "loopA")
        self.reg.release(self.stage, "loopA")
        self.assertFalse(self.reg.is_owned(self.stage))
        self.assertTrue(self.reg.try_acquire(self.stage, "loopB"))

    def test_release_by_non_owner_is_noop(self):
        self.reg.try_acquire(self.stage, "loopA")
        self.reg.release(self.stage, "loopB")  # not the owner
        self.assertEqual(self.reg.owner_of(self.stage), "loopA")

    def test_acquire_raises_on_contention(self):
        self.reg.acquire(self.stage, "loopA")
        with self.assertRaises(StageBusyError) as ctx:
            self.reg.acquire(self.stage, "loopB")
        self.assertEqual(ctx.exception.stage_id, self.stage)
        self.assertEqual(ctx.exception.current_owner, "loopA")

    def test_hold_context_manager(self):
        with self.reg.hold(self.stage, "loopA"):
            self.assertTrue(self.reg.is_owned(self.stage))
        self.assertFalse(self.reg.is_owned(self.stage))

    def test_hold_releases_on_exception(self):
        with self.assertRaises(ValueError):
            with self.reg.hold(self.stage, "loopA"):
                raise ValueError("boom")
        self.assertFalse(self.reg.is_owned(self.stage))

    def test_thread_safety_exactly_one_winner(self):
        winners: list[int] = []
        barrier = threading.Barrier(20)

        def contend(i: int) -> None:
            barrier.wait()
            if self.reg.try_acquire(self.stage, f"owner-{i}"):
                winners.append(i)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)


if __name__ == "__main__":
    unittest.main()

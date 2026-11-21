import unittest

from plan import plan_transfer
from plan.dual import DualPlan
from source import SourceDirectory, RecordLayout


def _directory(volume_id, positions, *, width=3, slot_count=None):
    return SourceDirectory(
        volume_id, RecordLayout(width), len(positions) if slot_count is None else slot_count, positions
    )


LEFT = _directory('LEFT', [0], slot_count=1)
RIGHT = _directory('RIGHT', [0, 1], slot_count=2)


class TestDualPlanning(unittest.TestCase):
    def test_example_plan_is_frozen_and_ordered(self) -> None:
        directories = [LEFT, RIGHT]
        targets = [
            ('meter', [('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0)]),
            ('quality', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)]),
        ]
        plan = plan_transfer(directories, targets, 1)
        self.assertIsInstance(plan, DualPlan)
        self.assertEqual(plan.directories, (LEFT, RIGHT))
        self.assertEqual([target.name for target in plan.targets], ['meter', 'quality'])
        self.assertEqual(plan.targets[0].origins, (('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0)))
        self.assertEqual(plan.capacity, 1)
        # Mutating the caller's containers afterwards must not disturb the plan.
        directories.append(LEFT)
        targets[0][1].append(('LEFT', 0))
        targets.append(('extra', []))
        self.assertEqual(plan.directories, (LEFT, RIGHT))
        self.assertEqual(plan.targets[0].origins, (('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0)))
        self.assertEqual(len(plan.targets), 2)

    def test_reads_are_forward_only_per_source(self) -> None:
        plan = plan_transfer([LEFT, RIGHT], [('a', [('RIGHT', 1), ('LEFT', 0)]), ('b', [])], 1)
        for volume in (0, 1):
            positions = [position for index, position in plan.reads if index == volume]
            self.assertEqual(positions, sorted(set(positions)))

    def test_zero_capacity_example_is_no_plan(self) -> None:
        targets = [
            ('meter', [('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0)]),
            ('quality', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)]),
        ]
        self.assertEqual(plan_transfer([LEFT, RIGHT], targets, 0), 'NO_PLAN')

    def test_zero_capacity_direct_only_needs_no_slot(self) -> None:
        plan = plan_transfer([LEFT, RIGHT], [('a', [('LEFT', 0)]), ('b', [])], 0)
        self.assertIsInstance(plan, DualPlan)
        self.assertEqual(plan.reads, ((0, 0),))

    def test_empty_targets_need_no_reads(self) -> None:
        plan = plan_transfer([LEFT, RIGHT], [('a', []), ('b', [])], 0)
        self.assertIsInstance(plan, DualPlan)
        self.assertEqual(plan.reads, ())

    def test_one_target_may_be_empty_but_shared_source_is_read(self) -> None:
        plan = plan_transfer([LEFT, RIGHT], [('a', [('RIGHT', 1)]), ('b', [])], 0)
        self.assertEqual(plan.reads, ((1, 1),))

    def test_shared_origin_needed_last_by_one_target_is_no_plan_at_one_slot(self) -> None:
        # Every origin is shared and consumed in opposite chains; holding one while the
        # other target catches up needs a second slot.
        targets = [
            ('a', [('LEFT', 0), ('RIGHT', 0), ('RIGHT', 1)]),
            ('b', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)]),
        ]
        self.assertEqual(plan_transfer([LEFT, RIGHT], targets, 1), 'NO_PLAN')
        self.assertIsInstance(plan_transfer([LEFT, RIGHT], targets, 2), DualPlan)

    def test_invalid_requests(self) -> None:
        good_targets = [('a', [('LEFT', 0)]), ('b', [])]
        invalid = [
            ([LEFT], good_targets, 0),
            ([LEFT, RIGHT, LEFT], good_targets, 0),
            ('xx', good_targets, 0),
            ([LEFT, RIGHT], [('a', [])], 0),
            ([LEFT, RIGHT], [('a', []), ('b', []), ('c', [])], 0),
            ([LEFT, RIGHT], [('a', []), ('a', [])], 0),
            ([LEFT, RIGHT], [('  ', []), ('b', [])], 0),
            ([LEFT, RIGHT], [('a', []), ('b', [])], -1),
            ([LEFT, RIGHT], [('a', []), ('b', [])], True),
            ([LEFT, RIGHT], [('a', []), ('b', [])], 1.0),
            ([LEFT, RIGHT], [('a', [('LEFT', 5)]), ('b', [])], 0),
            ([LEFT, RIGHT], [('a', [('LEFT', 1)]), ('b', [])], 0),      # absent from LEFT
            ([LEFT, RIGHT], [('a', [('LEFT', -1)]), ('b', [])], 0),
            ([LEFT, RIGHT], [('a', [('LEFT', True)]), ('b', [])], 0),
            ([LEFT, RIGHT], [('a', [('LEFT', 0), ('LEFT', 0)]), ('b', [])], 0),
            ([LEFT, RIGHT], [('a', [('NOPE', 0)]), ('b', [])], 0),
            ([LEFT, LEFT], good_targets, 0),
        ]
        for directories, targets, capacity in invalid:
            with self.subTest(directories=directories, targets=targets, capacity=capacity):
                with self.assertRaises(ValueError):
                    plan_transfer(directories, targets, capacity)

    def test_different_layouts_are_rejected(self) -> None:
        wide = _directory('RIGHT', [0, 1], width=4, slot_count=2)
        with self.assertRaises(ValueError):
            plan_transfer([LEFT, wide], [('a', []), ('b', [])], 0)

    def test_no_frame_body_is_inspected(self) -> None:
        # Planning only receives directories; this proves no device is involved.
        plan = plan_transfer([LEFT, RIGHT], [('a', [('RIGHT', 1)]), ('b', [])], 0)
        self.assertEqual(plan.reads, ((1, 1),))


if __name__ == '__main__':
    unittest.main()

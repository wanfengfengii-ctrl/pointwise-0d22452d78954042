import unittest

from plan import plan_single
from plan.validation import InvalidSelection
from source import SourceCatalog, register_source
from source.catalog import SourceConflict, UnknownSource
from tests.fixtures import RecordedStation, mounted_directory


class TestPreparation(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = SourceCatalog()
        self.station = RecordedStation(mounted_directory(positions=[0, 2, 4]))

    def test_fixed_catalog_and_selection(self) -> None:
        positions = [0, 2, 4]
        self.assertEqual(register_source(self.catalog, 'SC-17', 3, 5, positions), 'SC-17')
        first = self.catalog.lookup('SC-17')
        positions.clear()
        self.assertEqual(register_source(self.catalog, 'SC-17', 3, 5, [0, 2, 4]), 'SC-17')
        self.assertIs(self.catalog.lookup('SC-17'), first)
        selection = [0, 4]
        plan = plan_single(self.catalog, 'SC-17', selection)
        selection.append(5)
        self.assertIs(plan.binding.directory, first)
        self.assertEqual(plan.selection.positions, (0, 4))
        self.assertEqual(plan.selection.origins(first), (('SC-17', 0), ('SC-17', 4)))
        self.assertEqual(self.station.events, [])

    def test_empty_selection(self) -> None:
        register_source(self.catalog, 'EMPTY', 3, 0, [])
        register_source(self.catalog, 'SC-17', 3, 5, [0, 2, 4])
        for volume, count, positions in (('EMPTY', 0, ()), ('SC-17', 5, (0, 2, 4))):
            with self.subTest(volume=volume):
                plan = plan_single(self.catalog, volume, [])
                self.assertEqual(plan.selection.positions, ())
                self.assertEqual(plan.binding.directory.slot_count, count)
                self.assertEqual(plan.binding.directory.positions, positions)
        self.assertEqual(self.station.events, [])

    def test_conflicting_catalog(self) -> None:
        register_source(self.catalog, 'SC-17', 3, 5, [0, 2, 4])
        first = self.catalog.lookup('SC-17')
        for width, slot_count, positions in ((4, 5, [0, 4]), (3, 6, [0, 2, 4]), (3, 5, [0, 4])):
            with self.subTest(width=width, slot_count=slot_count, positions=positions):
                with self.assertRaises(SourceConflict):
                    register_source(self.catalog, 'SC-17', width, slot_count, positions)
                self.assertIs(self.catalog.lookup('SC-17'), first)
                self.assertEqual(plan_single(self.catalog, 'SC-17', [2]).selection.positions, (2,))
        self.assertEqual(first.layout.width, 3)
        self.assertEqual(first.positions, (0, 2, 4))
        self.assertEqual(self.station.events, [])

    def test_invalid_directory_layouts(self) -> None:
        invalid_layouts = (
            (0, 2, [0], 1), (True, 2, [0], 1), (3, -1, [], 1), (3, 2, [0], 2),
            (3, 2, [0, 0], 1), (3, 2, [1, 0], 1), (3, 2, [2], 1), (3, 2, [False], 1),
        )
        for width, count, positions, version in invalid_layouts:
            with self.subTest(width=width, count=count, positions=positions, version=version):
                with self.assertRaises(ValueError):
                    register_source(self.catalog, 'BAD', width, count, positions, version=version)
                with self.assertRaises(UnknownSource):
                    self.catalog.lookup('BAD')

    def test_invalid_positions(self) -> None:
        register_source(self.catalog, 'SC-17', 3, 5, [0, 2, 4])
        for selection in ([5], [-1], [0, 0, 2], [4, 2], [1], [False], [2.0], ''):
            with self.subTest(selection=selection):
                with self.assertRaises(InvalidSelection):
                    plan_single(self.catalog, 'SC-17', selection)
        self.assertEqual(self.station.events, [])

    def test_unknown_source(self) -> None:
        with self.assertRaises(UnknownSource):
            plan_single(self.catalog, 'UNREGISTERED', [])
        self.assertEqual(self.station.events, [])

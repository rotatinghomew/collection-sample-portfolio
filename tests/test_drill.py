"""核查练习数据的原始事实，不计算分析结果。"""
from pathlib import Path
import json
import unittest


class DrillDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / 'data' / 'drill.json'
        cls.data = json.loads(source.read_text(encoding='utf-8'))

    def test_schema(self):
        expected = {'rounds': ('id', 'deadline', 'budget', 'required_themes'), 'samples': ('id', 'design', 'material', 'units', 'shop', 'hours', 'start', 'deadline', 'themes'), 'offers': ('id', 'material', 'minimum', 'lot_step', 'lot_price', 'valid_from', 'valid_until', 'arrives_on', 'revision_of'), 'capacity': ('shop', 'on', 'hours', 'known_on'), 'purchases': ('id', 'round', 'offer', 'units', 'paid', 'refundable_units', 'refund_per_unit', 'committed_on'), 'holds': ('round', 'sample', 'shop', 'on', 'hours', 'strength')}
        self.assertEqual(set(self.data), set(expected))
        for name, fields in expected.items():
            with self.subTest(table=name):
                self.assertTrue(self.data[name])
                self.assertTrue(all(set(row) == set(fields) for row in self.data[name]))

    def test_paid_pool_with_later_quote_and_capacity_cut(self):
        purchase = self.data['purchases'][0]
        original = next(row for row in self.data['offers'] if row['id'] == purchase['offer'])
        revision = next(row for row in self.data['offers'] if row['revision_of'] == original['id'])
        self.assertLess(purchase['committed_on'], revision['valid_from'])
        self.assertLess(purchase['refundable_units'], purchase['units'])
        printed = next(row for row in self.data['holds'] if row['sample'] == 'D21')
        capacities = [row['hours'] for row in self.data['capacity'] if row['shop'] == printed['shop'] and row['on'] == printed['on']]
        self.assertIn(printed['hours'], capacities)
        self.assertLess(min(capacities), printed['hours'])

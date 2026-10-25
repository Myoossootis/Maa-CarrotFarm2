import unittest

from tools.production_targets import prepare_order_target, order_matches_shortage_focus, shortage_focus_was_submitted
from tools.allocate_employee_work import allocate
from tools.employee_capabilities import load_capabilities


def item(name, actual, required):
    return dict(name=name, actual=actual, required=required,
                missing=max(required - actual, 0), satisfied=actual >= required)


def order(number, *items, reason='insufficient'):
    return dict(order=number, items=list(items), reason=reason)


class SingleOrderTests(unittest.TestCase):
    def test_only_one_order_is_prepared_without_combining_later_orders(self):
        target = prepare_order_target([
            order(1, item('小麦', 10, 18), item('鸡蛋', 44, 3)),
            order(2, item('小麦', 10, 99), item('鸡蛋', 44, 3)),
        ])
        self.assertEqual(target['order'], 1)
        self.assertEqual([(p['name'], p['needed']) for p in target['production_targets']], [('小麦', 8)])

    def test_fish_skips_whole_order_even_if_stock_sufficient(self):
        target = prepare_order_target([
            order(1, item('鱼肉', 100, 1), item('小麦', 0, 99)),
            order(2, item('玉米', 5, 8), item('鸡蛋', 44, 3)),
        ])
        self.assertEqual(target['order'], 2)
        self.assertEqual([(p['name'], p['needed']) for p in target['production_targets']], [('玉米', 3)])

    def test_no_fourth_order_backfill(self):
        self.assertIsNone(prepare_order_target([
            order(1, item('鱼肉', 9, 1), item('小麦', 0, 3)),
            order(2, reason='unreadable'), order(3, reason='unreadable'),
            order(4, item('小麦', 0, 1), item('鸡蛋', 44, 3))]))

    def test_ready_order_requires_no_production(self):
        target = prepare_order_target([order(1, item('小麦', 20, 5), item('鸡蛋', 10, 3), reason='ready')])
        self.assertEqual(target['production_targets'], [])
        self.assertEqual(target['total_missing'], 0)

    def test_one_order_can_use_multiple_capable_idle_workers(self):
        target = prepare_order_target([order(1, item('小麦', 1, 78), item('鸡蛋', 99, 3))])
        slots = [s for s in load_capabilities()['slots'] if s['id'] in {'field_1', 'field_2'}]
        result = allocate(target['production_targets'], slots)
        self.assertTrue(result['ready'])
        self.assertEqual({j['slot_id'] for j in result['jobs']}, {'field_1', 'field_2'})
        self.assertEqual(sum(sum(j['items'].values()) for j in result['jobs']), 77)
        self.assertTrue(all(sum(j['items'].values()) <= 50 for j in result['jobs']))

    def test_submission_must_match_selected_recipe_not_just_missing_crop(self):
        target = prepare_order_target([order(1, item('小麦', 1, 78), item('鸡蛋', 99, 3))])
        other = [item('小麦', 100, 3), item('玉米', 99, 2)]
        self.assertFalse(order_matches_shortage_focus(target, other))
        self.assertFalse(shortage_focus_was_submitted({'submitted': [dict(items=other, acceptance_confirmed=True)]}, target))
        refreshed = [item('小麦', 100, 78), item('鸡蛋', 99, 3)]
        self.assertTrue(order_matches_shortage_focus(target, refreshed))


if __name__ == '__main__':
    unittest.main()

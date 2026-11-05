from copy import deepcopy
from datetime import datetime, timezone
import unittest

from tools.lion_workflow import review, lines

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc).timestamp()


def fixture():
    return dict(readOnly=True, source='local_client_cache', capturedAt='2026-11-05T00:00:00Z',
                familyContext=dict(selfUid=7, familyUid=8, season=126, staff=[dict(itemId=181, uid=100,
                                   posts=[dict(buildingItemId=5)])], rawQuotaRows=[dict(count=19)]),
                family=dict(tasks=[dict(slot=i, uid=1000+i, taskItemId=5451, text='UP餐厅订单',
                    acceptor=None, upgrader=None, status='unclaimed', isRare=True,
                    requirement=dict(kind='party_takeout', staffItemId=181, count=6),
                    progress=dict(current=None, available=False)) for i in range(1, 91)]))


class LionReadinessTests(unittest.TestCase):
    def test_never_infers_completed_from_raw_quota_or_authorizes_input(self):
        result = review(fixture(), now=NOW)
        self.assertEqual(result['periodTarget'], 26)
        self.assertIsNone(result['completed'])
        self.assertFalse(result['executionEnabled'])
        self.assertIn('未核对', lines(result))

    def test_prioritizes_own_even_nonrare_and_does_not_select_new_task(self):
        data = fixture()
        task = data['family']['tasks'][5]
        task.update(acceptor=dict(uid=7), status='occupied', isRare=False, progress=dict(available=True, current=2))
        result = review(data, now=NOW)
        self.assertEqual([r['slot'] for r in result['ownTasks']], [6])
        self.assertIn('不接新任务', result['next'])
        self.assertTrue(result['ownTasks'][0]['staffOnDuty'])

    def test_excludes_other_acceptors_and_other_upgraders_even_when_unclaimed(self):
        data = fixture()
        for task in data['family']['tasks']:
            task['upgrader'] = dict(uid=9)
        data['family']['tasks'][0].update(acceptor=dict(uid=9), status='occupied')
        result = review(data, now=NOW)
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['excluded'], dict(other_acceptor=1, other_upgrader_reserved=89))
        self.assertIn('等待', result['next'])

    def test_self_upgraded_unclaimed_is_not_reserved_by_others(self):
        data = fixture()
        data['family']['tasks'][0]['upgrader'] = dict(uid=7)
        self.assertEqual(len(review(data, now=NOW)['candidates']), 90)

    def test_unknown_own_progress_is_not_zero_or_done(self):
        data = fixture()
        data['family']['tasks'][0].update(acceptor=dict(uid=7), status='occupied')
        row = review(data, now=NOW)['ownTasks'][0]
        self.assertIsNone(row['progress']['current'])
        self.assertTrue(any('进度未知' in b for b in row['blockers']))

    def test_staff_missing_or_on_wrong_post_and_star_tasks_are_explicit(self):
        data = fixture()
        data['familyContext']['staff'][0]['posts'] = [dict(buildingItemId=12)]
        data['family']['tasks'][1]['requirement']['staffItemId'] = 999
        data['family']['tasks'][2]['requirement'] = dict(kind='star_orders', stars=30, count=6)
        rows = {r['slot']:r for r in review(data, now=NOW)['candidates']}
        self.assertFalse(rows[1]['staffOnDuty'])
        self.assertTrue(any('未拥有' in b for b in rows[2]['blockers']))
        self.assertTrue(any('星级' in b for b in rows[3]['blockers']))

    def test_stale_incomplete_and_unknown_identity_never_select(self):
        good = fixture()
        invalid = [None, [], dict(good, capturedAt='2026-11-04T00:00:00Z'),
                   dict(good, family=dict(tasks=good['family']['tasks'][:-1])),
                   dict(good, familyContext=dict(selfUid=None))]
        duplicate = deepcopy(good)
        duplicate['family']['tasks'][1]['uid'] = duplicate['family']['tasks'][0]['uid']
        invalid.append(duplicate)
        bad_owner = deepcopy(good)
        bad_owner['family']['tasks'][1]['acceptor'] = dict(uid=True)
        invalid.append(bad_owner)
        for data in invalid:
            with self.subTest(data=type(data)):
                result = review(data, now=NOW)
                self.assertEqual(result['candidates'], [])
                self.assertTrue(result['blockers'])

    def test_does_not_mutate_source_snapshot(self):
        data = fixture()
        before = deepcopy(data)
        review(data, now=NOW)
        self.assertEqual(data, before)

    def test_displays_direct_season_counter_but_keeps_this_page_read_only(self):
        data=fixture()
        data['family']['period']=dict(source='shared_3921_and_3972',selfUid=7,season=126,status=1,
                                    endsUnix=int(NOW)+3600,completed=2,points=164)
        result=review(data,now=NOW)
        self.assertEqual(result['completed'],2)
        self.assertIn('已完成：2',lines(result))
        self.assertFalse(result['executionEnabled'])

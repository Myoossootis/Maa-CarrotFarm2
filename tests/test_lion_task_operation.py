from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import decoded_runtime as decoded, employee_operation as employees
from tools import lion_task_operation as lion, run_lion_task as run
from tools.runtime_state import write_json


def data(current=66, status=1):
    task = dict(slot=66, uid=1001, taskItemId=3875, acceptor=dict(uid=7), isUpgraded=True,
                requirement=dict(kind='harvest', productItemId=29, staffItemId=2014), target=204,
                deadlineUnix=(datetime.now(timezone.utc) + timedelta(hours=10)).timestamp(), bellReward=dict(count=82))
    return dict(packageVersion='100', capturedAt=datetime.now(timezone.utc).isoformat(),
        familyContext=dict(selfUid=7, familyUid=8, season=126, staff=[dict(itemId=2014, posts=[dict(buildingItemId=286)])]),
        family=dict(tasks=[dict(task, uid=2000+i, slot=i+1, acceptor=None) for i in range(89)] + [task]),
        inventory=dict(records=[dict(uid=100, itemId=3875, count=1, extra=dict(r_id='1001', tk_c=f'3944_{current}', status=str(status))),
            dict(uid=101, itemId=3956, count=19), dict(uid=102, itemId=3638, count=586),
            dict(uid=103, itemId=2739, count=1, extra=dict(it_rc='5740_338'))]),
        employees=dict(repeatEnabled=False))


def rewarded(before):
    after = deepcopy(before)
    after['inventory']['records'] = [r for r in after['inventory']['records'] if r['uid'] != 100]
    after['inventory']['records'][0]['count'] = 18
    after['inventory']['records'][1]['count'] = 668
    after['inventory']['records'][2]['extra']['it_rc'] = '5740_339'
    return after


class LionTaskOperationTests(unittest.TestCase):
    def setUp(self):
        self.validation = patch.object(decoded, 'validate')
        self.validation.start()
        self.addCleanup(self.validation.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def record(self, before=None):
        return dict(id='a'*32, folder=str(self.folder), status='bound', binding=lion.bind(before or data()), waves=[])

    def test_binding_uses_personal_uid_and_account_period_not_nickname(self):
        binding = lion.bind(data())
        self.assertEqual(binding['personalUid'], 100)
        self.assertEqual(lion.validate_task(binding, data()), dict(current=66, status=1))
        for key in ('selfUid', 'familyUid', 'season'):
            changed = data();changed['familyContext'][key] += 1
            with self.subTest(key=key), self.assertRaises(decoded.DecodeError):
                lion.validate_task(binding, changed)

    def test_changed_instance_missing_progress_or_staff_blocks(self):
        binding = lion.bind(data())
        for change in ('instance', 'progress', 'staff', 'package'):
            changed = data()
            if change == 'instance':changed['inventory']['records'][0]['uid'] += 1
            if change == 'progress':changed['inventory']['records'][0]['extra']['tk_c'] = ''
            if change == 'staff':changed['familyContext']['staff'] = []
            if change == 'package':changed['packageVersion'] = '101'
            with self.subTest(change=change), self.assertRaises(decoded.DecodeError):
                lion.validate_task(binding, changed)

    def test_confirmed_selection_binds_current_personal_instance_without_other_board_slots(self):
        source=data();task=deepcopy(source['family']['tasks'][-1])
        selected=dict(status='ready',readyTask=task,readyPersonalUid=100,
            identity=dict(selfUid=7,familyUid=8,season=126,packageVersion='100'))
        source['family']={'period':None}
        source['familyContext']['personalTasks']=[dict(task,uid=100,slotUid=1001,status=1)]
        self.assertEqual(lion.bind(source,selected=selected)['personalUid'],100)
        source['familyContext']['personalTasks'][0]['slotUid']=1002
        with self.assertRaises(decoded.DecodeError):lion.bind(source,selected=selected)

    def test_finished_task_prevents_new_production_even_with_excess_progress(self):
        binding = lion.bind(data())
        self.assertEqual(lion.validate_task(binding, data(208, 2))['status'], 2)
        with self.assertRaises(decoded.DecodeError):
            lion.validate_task(binding, data(208, 2), producing=True)

    def test_reward_requires_instance_terminal_and_exact_quota_bell_and_item_changes(self):
        before = data(204, 2);binding = lion.bind(before)
        self.assertTrue(lion.reward_confirmed(binding, before, rewarded(before)))
        for failure in ('personal', 'quota', 'bell', 'extra', 'account'):
            after = rewarded(before)
            if failure == 'personal':after['inventory']['records'].append(before['inventory']['records'][0])
            if failure == 'quota':after['inventory']['records'][0]['count'] = 19
            if failure == 'bell':after['inventory']['records'][1]['count'] += 1
            if failure == 'extra':after['inventory']['records'][2]['extra']['it_rc'] = '5740_338'
            if failure == 'account':after['familyContext']['selfUid'] += 1
            with self.subTest(failure=failure):self.assertFalse(lion.reward_confirmed(binding, before, after))

    def test_hidden_reward_is_confirmed_in_its_real_counter_not_a_fabricated_bag_row(self):
        before = data(204,2);after=rewarded(before)
        self.assertFalse(any(r['itemId']==5740 for r in after['inventory']['records']))
        self.assertTrue(lion.reward_confirmed(lion.bind(before), before, after))
        for raw in ('5740_339#5740_339', '5740_bad', '5740_-1', ''):
            bad=deepcopy(after);bad['inventory']['records'][2]['extra']['it_rc']=raw
            with self.subTest(raw=raw), self.assertRaises(decoded.DecodeError):lion.reward_proxy_count(bad)
        after['inventory']['records'].append(deepcopy(after['inventory']['records'][2]))
        with self.assertRaises(decoded.DecodeError):lion.reward_proxy_count(after)

    def test_reward_intent_is_persisted_before_one_click_and_recovery_never_clicks(self):
        before = data(204, 2);after = rewarded(before);record = self.record(before)
        ui = Mock()
        ui.text.side_effect = ['66', '安排苹果收获204/204个苹果', '领取奖励']
        def click(*_):
            persisted = employees.read(self.folder / 'task.json')
            self.assertEqual(persisted['status'], 'reward_intent')
            raise RuntimeError('interrupted after delivery')
        ui.click_label.side_effect = click
        with patch('tools.lion_task_ui.LionUI', return_value=ui), patch.object(run, 'full_snapshot', return_value=before):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                run.claim(record)
        self.assertEqual(record['status'], 'reward_unconfirmed')
        ui.click_label.assert_called_once()
        with patch.object(decoded, 'snapshot', return_value=after), \
                patch('tools.lion_task_ui.LionUI') as new_ui, patch.object(run, 'return_after_reward', return_value=True):
            self.assertTrue(run.claim(record))
            new_ui.assert_not_called()
        self.assertTrue(record['reward_recovered'])
        self.assertEqual(record['reward_after'], after)

    def test_uncertain_reward_remains_blocked_without_repeated_click(self):
        before = data(204, 2);record = self.record(before)
        record.update(status='reward_unconfirmed', reward_before=before)
        with patch.object(decoded, 'snapshot', return_value=before), patch('tools.lion_task_ui.LionUI') as ui:
            with self.assertRaisesRegex(decoded.DecodeError, '禁止重复'):
                run.claim(record)
            ui.assert_not_called()

    def test_manual_completion_goes_to_claim_and_preserves_pending_production(self):
        before = data(204, 2);record = self.record(before)
        pending = dict(assignment_id='b'*32, status='applying', allocation=dict(bound_lion=record['binding']))
        with patch.object(decoded, 'snapshot', return_value=before), patch.object(employees, 'unfinished_active', return_value=pending), \
                patch.object(run, 'claim', return_value=True) as claim, patch('tools.run_employee_order_plan.apply_jobs') as apply:
            self.assertTrue(run.execute(record))
            claim.assert_called_once()
            apply.assert_not_called()
        self.assertEqual(record['unfinished_production_preserved'], 'b'*32)

    def test_partial_save_adopts_two_equal_reads_but_never_uses_an_edit_or_claim(self):
        before = data();record = self.record(before)
        operation = dict(assignment_id='c'*32, operation_dir=str(self.folder), status='applying',
                         allocation=dict(bound_lion=record['binding'], jobs=[dict(slot_id='orchard_2', items={'苹果':50})]))
        entry = dict(status='save_intent', items={'苹果':50}, owner={'buildingUid':4})
        path = employees.job_path(operation, 'orchard_2');write_json(path, entry)
        with patch.object(employees, 'owner', return_value=entry['owner']), patch.object(decoded, 'plan_items', return_value={'苹果':39}):
            result = lion.reconcile_partial_save(operation, 'orchard_2', before, before)
        self.assertEqual(result['items'], {'苹果':39})
        self.assertEqual(result['requested_items'], {'苹果':50})
        self.assertTrue(Path(result['partial_save_evidence']).exists())
        with patch.object(employees, 'owner', return_value=entry['owner']), patch.object(decoded, 'plan_items', return_value={'苹果':39}):
            with self.assertRaises(decoded.DecodeError):
                lion.reconcile_partial_save(operation, 'orchard_2', before, before)

    def test_dynamic_wave_uses_current_capacities_and_remaining_task_gap(self):
        before = data();binding=lion.bind(before)
        with patch.object(decoded, 'assignment_blocker', return_value=None):
            allocation = run.wave_allocation(binding, before, {'orchard_1':73, 'orchard_2':61})
        self.assertEqual([j['items']['苹果'] for j in allocation['jobs']], [73,61])
        self.assertEqual([j['capacity_limit'] for j in allocation['jobs']], [73,61])

    def test_bad_pointer_cannot_escape_task_root(self):
        write_json(self.folder / 'active.json', dict(id='../other'))
        with self.assertRaises(decoded.DecodeError):lion.begin(data(), root=self.folder)


class DraftCapacityTests(unittest.TestCase):
    def test_opening_capacity_uses_two_stable_reads_and_never_clicks(self):
        from tools import configure_field_employee as ui
        with patch.object(ui.time, 'sleep'), patch.object(ui, 'screenshot'), \
                patch.object(ui, 'editor_capacity', side_effect=[None,(0,5),(0,50),(0,50)]), patch.object(ui, 'click') as click:
            ui.confirm_empty_editor_capacity(object(), object(), 'orchard_2', 50)
            click.assert_not_called()

    def test_stable_changed_capacity_stops_before_any_input(self):
        from tools import configure_field_employee as ui
        with patch.object(ui.time, 'sleep'), patch.object(ui, 'screenshot'), \
                patch.object(ui, 'editor_capacity', return_value=(0,46)), patch.object(ui, 'click') as click:
            with self.assertRaises(RuntimeError):ui.confirm_empty_editor_capacity(object(), object(), 'orchard_2', 50)
            click.assert_not_called()

    def test_observed_unsaved_deficit_is_filled_and_read_twice(self):
        from tools import configure_field_employee as ui
        with patch.object(ui.time, 'sleep'), patch.object(ui, 'screenshot', return_value=object()), \
                patch.object(ui, 'editor_capacity', side_effect=[(39,50),(50,50),(50,50)]), patch.object(ui, 'click') as click:
            ui.confirm_draft_capacity(object(), object(), 'orchard_2', 50, 50)
            self.assertEqual(click.call_count, 11)

    def test_unknown_excess_or_changed_capacity_does_not_send_input(self):
        from tools import configure_field_employee as ui
        for value in (None, (51,50), (39,46)):
            with self.subTest(value=value), patch.object(ui.time, 'sleep'), patch.object(ui, 'screenshot'), \
                    patch.object(ui, 'editor_capacity', return_value=value), patch.object(ui, 'click') as click:
                with self.assertRaises(RuntimeError):ui.confirm_draft_capacity(object(), object(), 'orchard_2', 50, 50)
                click.assert_not_called()

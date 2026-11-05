from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import decoded_runtime as decoded, lion_acquisition as policy, run_lion_acquisition as runner
from tools.employee_operation import read
from tools.runtime_state import write_json


def regular(slot=66, item=3643, bells=21, kind='regular_harvest'):
    return dict(slot=slot, uid=1000+slot, taskItemId=item, status='unclaimed', acceptor=None, upgrader=None,
                isRare=False, isUpgraded=False, canUpgrade=False, upgrade=None,
                requirement=dict(kind=kind, productItemId=157, count=96), bellReward=dict(count=bells))


def up(slot=66, *, base=False, staff=2014, bells=82):
    task = dict(regular(slot, item=3875, bells=bells), isRare=True, isUpgraded=True,
                requirement=dict(kind='harvest', staffItemId=staff, productItemId=29, count=204), target=204)
    if base:
        task = dict(task, taskItemId=3729, isUpgraded=False, canUpgrade=True, upgradeItemId=3875,
                    upgrade=deepcopy(task), upgradeCost=dict(itemId=4030, count=80), bellReward=dict(count=41))
    return task


def snapshot(task=None):
    data = dict(packageVersion='100', capturedAt=datetime.now(timezone.utc).isoformat(),
        familyContext=dict(selfUid=7, familyUid=8, season=126, staff=[dict(itemId=2014)], quitStartedUnix=0,
            rules=dict(quitCooldownSeconds=120, refreshTiers=[dict(cost=2), dict(cost=20)], voucherItemId=4030)),
        family=dict(tasks=[dict(regular(i), status='occupied', acceptor=dict(uid=9)) for i in range(1,91)]),
        inventory=dict(records=[dict(uid=1, itemId=3956, count=18), dict(uid=2, itemId=4030, count=6696),
                                dict(uid=3, itemId=2, count=8043)]))
    if task:
        data['family']['tasks'][task['slot']-1] = deepcopy(task)
    return data


def accept(data, task=None, personal_uid=500):
    data = deepcopy(data)
    task = task or data['family']['tasks'][65]
    target = data['family']['tasks'][task['slot']-1]
    target.update(acceptor=dict(uid=7), status='occupied')
    data['inventory']['records'].append(dict(uid=personal_uid, itemId=target['taskItemId'], count=1,
                                           extra=dict(r_id=str(target['uid']), status='1', tk_c='3944_0')))
    return data


def changed(before, task, cost, *, personal_uid=501):
    after = deepcopy(before)
    after['family']['tasks'][task['slot']-1] = deepcopy(task)
    after['inventory']['records'] = [r for r in after['inventory']['records'] if r['uid'] not in (500,501)]
    for row in after['inventory']['records']:
        if row['itemId'] == 4030:row['count'] -= cost
    return accept(after, task, personal_uid)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.valid = patch.object(decoded, 'validate');self.valid.start();self.addCleanup(self.valid.stop)
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = dict(tiers=[20], budget=600, max_refreshes=20)

    def begin(self, data):
        return policy.begin(data, self.options, self.root)

    def test_upgrade_uses_resulting_reward_not_the_fee_or_current_bells(self):
        data = snapshot(up(base=True));ctx=data['familyContext']
        task=data['family']['tasks'][65]
        self.assertTrue(policy.suitability(task, ctx)[0])
        task['upgrade']['bellReward']['count'] = 70
        task['upgradeCost']['count'] = 136
        self.assertFalse(policy.suitability(task, ctx)[0])

    def test_no_star_order_or_missing_employee_even_with_102_bells(self):
        for task in (dict(up(), requirement=dict(kind='star_orders', stars=32)), up(staff=999), up(bells=79)):
            with self.subTest(task=task):self.assertFalse(policy.suitability(task, snapshot()['familyContext'])[0])

    def test_own_first_and_never_takes_other_members_reserve(self):
        data = snapshot(up());task=data['family']['tasks'][65]
        task['upgrader'] = dict(uid=9)
        self.assertEqual(policy.choose(data)[0], 'no_candidate')
        task['upgrader'] = dict(uid=7)
        self.assertEqual(policy.choose(data)[0], 'candidate')
        own = accept(data)
        self.assertEqual(policy.choose(own)[0], 'own')
        own['family']['tasks'][0] = up(1)
        self.assertEqual(policy.choose(own)[1]['slot'], 66)

    def test_prefers_qualified_rare_before_garbage_or_an_unknown_condition(self):
        data = snapshot(regular())
        data['family']['tasks'][0] = up(1)
        self.assertEqual(policy.choose(data)[1]['slot'], 1)
        data['family']['tasks'][0]['upgrader'] = dict(uid=9)
        self.assertEqual(policy.choose(data)[0], 'garbage')
        data['family']['tasks'][65]['requirement']['kind'] = 'unknown'
        self.assertEqual(policy.choose(data)[0], 'no_candidate')

    def test_verified_executor_is_preferred_and_linked_without_replacing_old_receipt(self):
        from tools import lion_task_operation as lion
        data=snapshot(up(84,base=True))
        data['family']['tasks'][0]=dict(up(1,base=True),upgrade=dict(up(1),taskItemId=3876))
        self.assertEqual(policy.choose(data)[1]['slot'],84)
        task=up();data=accept(snapshot(task));record=self.begin(data)
        record.update(status='ready',readyTask=task,readyPersonalUid=500);policy.save(record)
        old=self.root/'old';old.mkdir();receipt=old/'task.json';write_json(receipt,{'status':'completed'})
        with patch.object(policy,'ROOT',self.root),patch.object(lion,'ROOT',old), \
                patch.object(lion,'bind',return_value=dict(slotUid=1066,personalUid=500)):
            result=lion.acquisition_root(data)
        self.assertEqual(result,self.root/record['id']/'execution')
        self.assertEqual(read(receipt),{'status':'completed'})

    def test_quota_zero_never_accepts_or_buys_extra_counts(self):
        data=snapshot(up());data['inventory']['records'][0]['count']=0
        self.assertEqual(policy.choose(data), ('quota_empty', None))

    def test_expired_task_is_not_a_candidate_and_expired_own_is_preserved(self):
        data=snapshot(dict(up(),deadlinePassed=True))
        self.assertEqual(policy.choose(data),('no_candidate',None))
        data=accept(data)
        with self.assertRaisesRegex(decoded.DecodeError,'超时'):policy.choose(data)

    def test_identity_damage_or_other_package_blocks(self):
        for damage in ('version','season','duplicate','owner'):
            data=snapshot(up())
            if damage=='version':data['packageVersion']='101'
            if damage=='season':data['familyContext']['season']=None
            if damage=='duplicate':data['family']['tasks'][0]['uid']=1066
            if damage=='owner':data['family']['tasks'][0]['acceptor']={'uid':True}
            with self.subTest(damage=damage), self.assertRaises(decoded.DecodeError):policy.choose(data)

    def test_refresh_tiers_are_frozen_budgeted_and_prefer_selected_20(self):
        data=snapshot(regular());record=self.begin(data)
        self.assertEqual(policy.refresh_tier(record,data),20)
        record['options']['tiers']=[2,20];record['spent']=590
        self.assertEqual(policy.refresh_tier(record,data),2)
        record['options']['tiers']=[20]
        with self.assertRaises(decoded.DecodeError):policy.refresh_tier(record,data)
        record['spent']=0;record['refreshes']=20
        with self.assertRaises(decoded.DecodeError):policy.refresh_tier(record,data)

    def test_insufficient_vouchers_never_falls_back_to_pearls(self):
        data=snapshot(regular());record=self.begin(data)
        data['inventory']['records'][1]['count']=19
        with self.assertRaisesRegex(decoded.DecodeError,'珍珠'):policy.cost_allowed(record,20,data)

    def test_accept_intent_is_durable_and_confirmed_without_deducting_quota(self):
        data=snapshot(up());record=self.begin(data)
        policy.intent(record,'accept',data['family']['tasks'][65],data)
        self.assertEqual(read(self.root/record['id']/'acquisition.json')['actions'][-1]['status'],'intent')
        self.assertFalse(policy.confirm(record,data))
        self.assertTrue(policy.confirm(record,accept(data)))
        self.assertEqual(record['managedSlotUid'],1066)
        self.assertEqual(record['spent'],0)

    def test_upgrade_rebinds_new_personal_uid_and_requires_exact_currency(self):
        before=accept(snapshot(up(base=True)));record=self.begin(before)
        source=before['family']['tasks'][65]
        target=dict(up(), upgrader=dict(uid=7))
        after=changed(before,target,80,personal_uid=601)
        policy.intent(record,'upgrade',source,before,80)
        bad=deepcopy(after);bad['inventory']['records'][2]['count']-=1
        self.assertFalse(policy.confirm(record,bad))
        self.assertTrue(policy.confirm(record,after))
        self.assertEqual(record['spent'],80)
        self.assertTrue(policy.confirm(record,after))
        self.assertEqual(record['spent'],80)

    def test_same_id_refresh_without_instance_evidence_is_uncertain_not_retried(self):
        before=accept(snapshot(regular()));record=self.begin(before)
        task=before['family']['tasks'][65]
        policy.intent(record,'refresh',task,before,20)
        after=changed(before,task,20,personal_uid=500)
        self.assertFalse(policy.confirm(record,after))
        with self.assertRaisesRegex(decoded.DecodeError,'禁止重发'):runner.recover(record,lambda:after)
        with self.assertRaises(decoded.DecodeError):policy.intent(record,'refresh',task,after,20)
        self.assertEqual(record['spent'],0)

    def test_resume_never_reissues_accept_upgrade_refresh_or_quit(self):
        for action in ('accept','upgrade','refresh','quit'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp:
                data=snapshot(up(base=True)) if action=='accept' else accept(snapshot(up(base=True)))
                record=policy.begin(data,self.options,Path(temp))
                policy.intent(record,action,data['family']['tasks'][65],data,20 if action=='refresh' else 0)
                ui=Mock()
                with self.assertRaisesRegex(decoded.DecodeError,'禁止重发'):runner.run(record,ui,read=lambda:data)
                self.assertEqual(ui.mock_calls,[])

    def test_quit_preserves_star_slot_and_does_not_cost_quota(self):
        before=accept(snapshot(regular(kind='star_orders',bells=51)));record=self.begin(before)
        policy.intent(record,'quit',before['family']['tasks'][65],before)
        after=deepcopy(before);after['family']['tasks'][65].update(acceptor=None,status='unclaimed')
        after['inventory']['records']=[r for r in after['inventory']['records'] if r['uid']!=500]
        after['familyContext']['quitStartedUnix']=1000
        self.assertTrue(policy.confirm(record,after))
        self.assertEqual(record['skipped'],[66])
        with patch.object(runner.time,'time',return_value=1005):self.assertEqual(runner.quit_cooldown(after),115)
        after['familyContext']['quitStartedUnix']=None
        with self.assertRaises(decoded.DecodeError):runner.quit_cooldown(after)

    def test_restarting_cannot_reset_budget_or_follow_an_unsafe_pointer(self):
        data=snapshot(regular());record=self.begin(data);record['spent']=100;policy.save(record)
        self.assertEqual(self.begin(data)['spent'],100)
        with self.assertRaises(decoded.DecodeError):policy.begin(data,dict(self.options,budget=700),self.root)
        write_json(self.root/'active.json',dict(id='../other'))
        with self.assertRaises(decoded.DecodeError):self.begin(data)

    def test_accept_refresh_low_reward_star_exit_next_up_upgrade_replay(self):
        initial=snapshot(regular());record=self.begin(initial)
        accepted=accept(initial)
        green=dict(regular(item=3661,bells=35),canUpgrade=True,upgrade=dict(regular(item=3807,bells=70)),upgradeItemId=3807)
        green_data=changed(accepted,green,20)
        star=regular(item=3752,bells=51,kind='star_orders')
        star_data=changed(green_data,star,20,personal_uid=500)
        exited=deepcopy(star_data);exited['family']['tasks'][65].update(acceptor=None,status='unclaimed')
        exited['inventory']['records']=[r for r in exited['inventory']['records'] if r['uid']!=500]
        exited['familyContext']['quitStartedUnix']=1
        exited['family']['tasks'][0]=up(1,base=True)
        next_own=accept(exited,exited['family']['tasks'][0])
        upgraded=changed(next_own,dict(up(1),upgrader=dict(uid=7)),80)
        state=[initial]
        ui=Mock()
        def transition(after):
            def move(*_):
                pending=read(Path(record['folder'])/'acquisition.json')['actions'][-1]
                self.assertEqual(pending['status'],'intent')
                state[0]=after
            return move
        ui.accept.side_effect=[None,None]
        accepted_states=iter([accepted,next_own])
        ui.accept.side_effect=lambda *_:transition(next(accepted_states))()
        refreshed=iter([green_data,star_data])
        ui.refresh.side_effect=lambda *_:transition(next(refreshed))()
        ui.open_refresh.return_value=True
        ui.quit_task.side_effect=transition(exited)
        ui.upgrade.side_effect=transition(upgraded)
        result=runner.run(record,ui,read=lambda:deepcopy(state[0]))
        self.assertEqual(result['slot'],1)
        self.assertEqual(record['spent'],120)
        self.assertEqual(record['refreshes'],2)
        self.assertEqual([a['action'] for a in record['actions']],['accept','refresh','refresh','quit','accept','upgrade'])
        ui.cancel_star_warning.assert_called_once()
        self.assertEqual(record['status'],'ready')

    def test_existing_bad_own_task_and_newly_reserved_candidate_are_preserved(self):
        data=accept(snapshot(regular()));record=self.begin(data);ui=Mock()
        with self.assertRaisesRegex(decoded.DecodeError,'历史任务'):runner.run(record,ui,read=lambda:data)
        ui.accept.assert_not_called();ui.refresh.assert_not_called();ui.quit_task.assert_not_called()
        initial=snapshot(up());record=dict(record,status='searching')
        changed=deepcopy(initial);changed['family']['tasks'][65]['upgrader']=dict(uid=9)
        reads=iter([initial,changed])
        with self.assertRaisesRegex(decoded.DecodeError,'占用改变'):runner.run(record,ui,read=lambda:next(reads))
        ui.accept.assert_not_called()


if __name__=='__main__':unittest.main()

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import decoded_runtime as decoded, lion_acquisition as acquisition, lion_task_operation as lion
from tools import lion_loop_operation as campaign, run_lion_loop as runner
from tools.employee_operation import read
from tools.runtime_state import write_json
from tools.task_control import TaskCancelled
from test_lion_acquisition import snapshot, up, accept


def data():
    result=accept(snapshot(up()))
    result.update(readOnly=True,source='local_client_cache')
    result['familyContext']['staff'][0]['posts']=[dict(buildingItemId=286)]
    result['family']['period']=dict(season=126,endsUnix=int((datetime.now(timezone.utc)+timedelta(days=4)).timestamp()),
        status=1,completed=2,points=164,source='shared_3921_and_3972',selfUid=7)
    result['family']['tasks'][65]['deadlineUnix']=(datetime.now(timezone.utc)+timedelta(hours=20)).timestamp()
    result['inventory']['records'].append(dict(uid=10,itemId=2739,count=1,extra=dict(it_rc='5740_100')))
    result['inventory']['records'].append(dict(uid=11,itemId=3638,count=164,extra={}))
    stamp_ui(result)
    return result


def stamp_ui(result):
    value=result['family']['period']
    result['lionCounterUi']={k:value[k] for k in ('season','selfUid','completed','points')}


class LionLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for target,value in ((decoded,'validate'),):
            p=patch.object(target,value);p.start();self.addCleanup(p.stop)
        for module in (acquisition,lion):
            p=patch.object(module,'ROOT',self.root/module.__name__.split('.')[-1]);p.start();self.addCleanup(p.stop)
        self.options=dict(tiers=[20],budget=600,max_refreshes=20,max_tasks=2,poll_seconds=60)
        self.state=data();self.clicks=[]

    def begin(self):
        return campaign.begin(self.state,self.options,self.root/'campaign')

    def select(self,record,entry,current):
        saved=campaign.prepare_acquisition(record,entry,current)
        task=self.state['family']['tasks'][65]
        if task.get('acceptor') is None:
            self.state=accept(self.state,task,personal_uid=500+entry['number'])
            task=self.state['family']['tasks'][65]
            self.clicks.append('accept')
        saved.update(status='ready',readyTask=deepcopy(task),
                     readyPersonalUid=acquisition.personal(task,self.state)['uid'])
        acquisition.save(saved)
        return task

    def execute(self,task):
        if task['status']=='completed':
            return True
        before=deepcopy(self.state)
        binding=task['binding']
        for row in before['inventory']['records']:
            if row['uid']==binding['personalUid']:row['extra'].update(status='2',tk_c='3944_204')
        after=deepcopy(before)
        after['inventory']['records']=[r for r in after['inventory']['records'] if r['uid']!=binding['personalUid']]
        for row in after['inventory']['records']:
            if row['itemId']==3956:row['count']-=1
            if row['itemId']==3638:row['count']+=82
            if row['itemId']==2739:
                value=int(row['extra']['it_rc'].split('_')[1])+1;row['extra']['it_rc']=f'5740_{value}'
        after['family']['period']['completed']+=1;after['family']['period']['points']+=82
        stamp_ui(after)
        after['family']['tasks'][65].update(acceptor=None,status='unclaimed')
        task.update(status='completed',completed_tasks=1,returned_to_farm=True,reward_before=before,reward_after=after)
        lion.save(task);self.state=after;self.clicks.append('reward')
        return True

    def run_campaign(self,record,**overrides):
        options=dict(read=lambda:deepcopy(self.state),select=self.select,execute=self.execute,
                     read_counter=lambda *_:deepcopy(self.state),sleep=lambda *_:None)
        options.update(overrides)
        return runner.run(record,**options)

    def test_two_complete_rounds_use_distinct_instances_and_advance_without_ui_restart(self):
        record=self.run_campaign(self.begin())
        self.assertEqual(record['status'],'run_limit')
        self.assertEqual(record['observedPeriod']['completed'],4)
        self.assertEqual(len(record['completed']),2)
        self.assertEqual(self.clicks,['reward','accept','reward'])
        self.assertEqual(len({r['personalUid'] for r in record['completed']}),2)
        self.assertEqual([r['status'] for r in record['rounds']],['counted','counted'])

    def test_crash_after_reward_before_count_recovers_receipt_without_second_reward(self):
        self.options['max_tasks']=1;record=self.begin()
        def stop(*_):raise TaskCancelled('after reward')
        with self.assertRaises(TaskCancelled):self.run_campaign(record,read_counter=stop)
        self.assertEqual(record['status'],'stopped')
        self.assertEqual(record['completed'],[])
        self.assertEqual(self.clicks,['reward'])
        resumed=self.begin()
        self.run_campaign(resumed)
        self.assertEqual(len(resumed['completed']),1)
        self.assertEqual(self.clicks,['reward'])

    def test_positive_click_result_without_exact_reward_receipt_does_not_advance(self):
        record=self.begin()
        with self.assertRaisesRegex(decoded.DecodeError,'证据'):self.run_campaign(record,execute=lambda _:True)
        self.assertEqual(record['completed'],[])
        self.assertEqual(len(record['rounds']),1)

    def test_counter_must_add_exactly_one_and_corresponding_points(self):
        record=self.begin()
        def stale(*_):
            result=deepcopy(self.state);result['family']['period']['completed']-=1;stamp_ui(result);return result
        with self.assertRaisesRegex(decoded.DecodeError,'恰好增加'):self.run_campaign(record,read_counter=stale)
        self.assertEqual(record['completed'],[])
        self.assertEqual(self.clicks,['reward'])

    def test_period_26_or_zero_quota_stops_without_accepting(self):
        for reason in ('period_target','quota_exhausted'):
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as temp:
                self.state=data()
                if reason=='period_target':self.state['family']['period']['completed']=26
                else:self.state['inventory']['records'][0]['count']=0
                stamp_ui(self.state)
                record=campaign.begin(self.state,self.options,Path(temp))
                self.run_campaign(record)
                self.assertEqual(record['status'],reason)
                self.assertEqual(record['rounds'],[])
        self.assertEqual(self.clicks,[])

    def test_season_conflict_unknown_counter_or_27_never_starts(self):
        for change in ('missing','season','27','expired'):
            result=data()
            if change=='missing':result['family']['period']=None
            if change=='season':result['family']['period']['season']+=1
            if change=='27':result['family']['period']['completed']=27
            if change=='expired':result['family']['period']['endsUnix']=1
            with self.subTest(change=change),self.assertRaises(decoded.DecodeError):campaign.begin(result,self.options,self.root/change)

    def test_old_ready_acquisition_is_adopted_and_its_80_vouchers_are_not_reset(self):
        previous=acquisition.begin(self.state,{k:self.options[k] for k in ('tiers','budget','max_refreshes')},acquisition.ROOT)
        previous.update(status='ready',readyTask=self.state['family']['tasks'][65],readyPersonalUid=500,spent=80,
                        actions=[dict(action='upgrade',status='confirmed',cost=80)])
        acquisition.save(previous)
        record=self.begin();entry=campaign.new_round(record,self.state)
        self.assertEqual(entry['source'],'legacy_acquisition')
        self.assertEqual(campaign.totals(record),(80,0))
        self.assertEqual(campaign.prepare_acquisition(record,entry,self.state)['id'],previous['id'])
        with self.assertRaises(decoded.DecodeError):campaign.begin(self.state,dict(self.options,budget=599),self.root/'campaign')
        raised=campaign.begin(self.state,dict(self.options,budget=1000),self.root/'campaign')
        self.assertEqual(campaign.totals(raised),(80,0))
        self.assertEqual(raised['options']['budget'],1000)

    def test_resume_after_counted_round_does_not_replay_old_receipt(self):
        self.options['max_tasks']=1;record=self.run_campaign(self.begin())
        old_clicks=list(self.clicks)
        self.run_campaign(self.begin())
        self.assertEqual(self.clicks,old_clicks)
        self.assertEqual(len(record['completed']),1)

    def test_duplicate_personal_instance_or_pending_production_blocks_next_round(self):
        record=self.begin();entry=campaign.new_round(record,self.state);self.select(record,entry,self.state)
        receipt=campaign.prepare_execution(record,entry,self.state);self.execute(receipt)
        record['completed']=[dict(personalUid=receipt['binding']['personalUid'])]
        with self.assertRaisesRegex(decoded.DecodeError,'重复'):campaign.count_completed(record,entry,receipt,self.state)
        record['completed']=[]
        with patch.object(campaign,'unfinished_active',return_value={'status':'saved'}):
            with self.assertRaisesRegex(decoded.DecodeError,'未决生产'):campaign.count_completed(record,entry,receipt,self.state)

    def test_nonapple_harvest_requires_configuration_reward_proof_and_known_product(self):
        task=dict(up(),taskItemId=3876,requirement=dict(kind='harvest',staffItemId=2014,productItemId=30,productName='黄桃'))
        self.assertFalse(lion.supports(task))
        task['completionCounter']=dict(itemId=5740,count=1,carrierItemId=2739)
        self.assertTrue(lion.supports(task))
        task['requirement']['productName']='鱼肉'
        self.assertFalse(lion.supports(task))

    def test_old_reward_evidence_can_be_counted_without_authorizing_fresh_input(self):
        record=self.begin();entry=campaign.new_round(record,self.state);self.select(record,entry,self.state)
        receipt=campaign.prepare_execution(record,entry,self.state);self.execute(receipt)
        for key in ('reward_before','reward_after'):
            receipt[key]['capturedAt']='2026-01-01T00:00:00+00:00'
        self.assertTrue(lion.completed_receipt(receipt))
        receipt['reward_after']['inventory']['records'][0]['count']+=1
        self.assertFalse(lion.completed_receipt(receipt))

    def test_next_round_only_receives_the_remaining_global_voucher_budget(self):
        previous=acquisition.begin(self.state,{k:self.options[k] for k in ('tiers','budget','max_refreshes')},acquisition.ROOT)
        previous.update(status='ready',readyTask=self.state['family']['tasks'][65],readyPersonalUid=500,spent=80,
                        actions=[dict(action='upgrade',status='confirmed',cost=80)])
        acquisition.save(previous)
        record=self.begin();entry=campaign.new_round(record,self.state)
        receipt=campaign.prepare_execution(record,entry,self.state);self.execute(receipt)
        campaign.count_completed(record,entry,receipt,self.state)
        next_entry=campaign.new_round(record,self.state)
        saved=campaign.prepare_acquisition(record,next_entry,self.state)
        self.assertEqual(saved['options']['budget'],520)
        self.assertEqual(campaign.totals(record),(80,0))

    def test_supported_only_selection_leaves_good_but_unimplemented_up_for_family(self):
        self.state['family']['tasks'][65].update(acceptor=None,status='unclaimed',taskItemId=999,
            requirement=dict(kind='party_takeout',staffItemId=2014,count=6))
        choice,task=acquisition.choose(self.state,supported_only=True)
        self.assertEqual((choice,task),('no_candidate',None))

    def test_generic_wave_uses_product_and_current_capacity_without_overwriting_other_routes(self):
        from tools.run_lion_task import wave_allocation
        self.state['family']['tasks'][65].update(taskItemId=3876,completionCounter=dict(itemId=5740,count=1,carrierItemId=2739),
            requirement=dict(kind='harvest',staffItemId=2014,productItemId=30,productName='黄桃'))
        self.state['inventory']['records'][3]['itemId']=3876
        binding=lion.bind(self.state)
        with patch.object(decoded,'assignment_blocker',return_value=None):
            result=wave_allocation(binding,self.state,dict(orchard_1=73,orchard_2=61,field_1=100))
        self.assertEqual([j['items'] for j in result['jobs']],[{'黄桃':73}])
        self.assertEqual([j['slot_id'] for j in result['jobs']],['orchard_1'])

    def test_new_season_gets_new_campaign_only_after_all_old_rounds_are_counted(self):
        self.options['max_tasks']=1;old=self.run_campaign(self.begin())
        self.state['familyContext']['season']=127;self.state['family']['period']['season']=127
        stamp_ui(self.state)
        new=self.begin()
        self.assertNotEqual(old['id'],new['id'])
        self.assertEqual(new['previousCampaignId'],old['id'])
        self.assertEqual(new['completed'],[])
        self.assertEqual(len(read(Path(old['folder'])/'loop.json')['completed']),1)

    def test_unresolved_round_blocks_new_season_without_replacing_pointer(self):
        old=self.begin();campaign.new_round(old,self.state)
        self.state['familyContext']['season']=127;self.state['family']['period']['season']=127
        stamp_ui(self.state)
        with self.assertRaises(decoded.DecodeError):self.begin()
        self.assertEqual(campaign.active(self.root/'campaign')['id'],old['id'])


if __name__=='__main__':unittest.main()

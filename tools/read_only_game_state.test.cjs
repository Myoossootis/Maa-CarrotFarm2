'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {parseMMKV,crc32,progressOf,familyFromRecords,employeesOf,ordersOf}=require('./read_only_game_state.cjs');

function varint(value){const bytes=[];do{bytes.push((value&127)|(value>127?128:0));value>>>=7;}while(value);return Buffer.from(bytes);}
function entry(key,value){const a=Buffer.from(key),b=Buffer.from(value);return Buffer.concat([varint(a.length),a,varint(b.length),b]);}

test('MMKV uses last value and honors deletion; rejects truncated records',()=>{
  const data=Buffer.concat([varint(0),entry('bag','old'),entry('deleted','x'),entry('bag','new'),entry('deleted','')]);
  const result=parseMMKV(data);
  assert.equal(result.records,4);
  assert.equal(result.values.get('bag').toString(),'new');
  assert.equal(result.values.has('deleted'),false);
  assert.throws(()=>parseMMKV(data.subarray(0,-1)));
  assert.equal(crc32(Buffer.from('123456789')),0xcbf43926);
});

test('missing shared progress is null; personal progress can supply occupied task',()=>{
  assert.deepEqual(progressOf({tk_c:''},null,172),{current:null,target:172,available:false,source:'not_provided',checks:[]});
  assert.equal(progressOf({tk_c:''},[1,3879,1,'tk_c,3944_42;status,1'],172).current,42);
  assert.equal(progressOf({tk_c:'3944_0'},null,172).current,0);
});

function fixture(){
  const config={items:new Map([[3879,{item_type:13,desc:'description',quality:2,params:'expire_cd,72000;mutation_ref_id,3733'}]]),
    tasks:new Map([[3879,{check_condi:'3944,2013,173,172',reward_box_id:73194}]]),
    bellRewards:new Map([[73194,[{item_num_min:82,item_num_max:82,drop_rate:10000,drop_type:1}]]]),
    language:new Map([['description','安排%[1]收获%[3]个%[2]']]),name:id=>String(id)};
  const records=Array.from({length:90},(_,i)=>({uid:10000+i,itemId:3931,extra:`idx,${i+1};lock,${i===0?55:0};exp,0;r_id,3879;e_time,0;r_time,0;tk_c,`}));
  records.push({uid:99,itemId:3629,extra:'plrd,1:55@4:成员甲'});
  return {config,records};
}

test('occupied status is independent from availability of progress and deadline',()=>{
  const {config,records}=fixture(),board=familyFromRecords(records,[],config,100);
  assert.equal(board.slotCount,90);
  assert.equal(board.occupiedCount,1);
  assert.equal(board.tasks[0].status,'occupied');
  assert.equal(board.tasks[0].acceptor.name,'成员甲');
  assert.equal(board.tasks[0].progress.current,null);
  assert.equal(board.tasks[0].deadline,null);
  assert.equal(board.tasks[1].status,'unclaimed');
});

test('incomplete and conflicting cached boards are rejected',()=>{
  const {config,records}=fixture();
  assert.throws(()=>familyFromRecords(records.slice(1),[],config,100),/89\/90/);
  assert.throws(()=>familyFromRecords([...records,{...records[0],extra:records[0].extra.replace('lock,55','lock,0')}],[],config,100),/Conflicting/);
});

test('rare includes upgraded tasks; bell rewards come from the task reward box',()=>{
  const {config,records}=fixture();
  let task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.isRare,true);
  assert.equal(task.rarity,'rare');
  assert.equal(task.isUpgraded,true);
  assert.equal(task.bellReward.count,82);
  assert.equal(task.bellReward.itemId,3638);
  config.items.get(3879).quality=1;
  config.items.get(3879).params='expire_cd,36000';
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.isRare,true);
  assert.equal(task.isUpgraded,false);
  config.items.get(3879).quality=0;
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.isRare,false);
  assert.equal(task.rarity,'normal');
});

test('personal reward overrides are preserved and unknown/random rewards are rejected',()=>{
  const {config,records}=fixture();
  const bag=[[44,3879,1,'r_id,10000;rws,3638_99;tk_c,3944_42']];
  const task=familyFromRecords(records,bag,config,100).tasks[0];
  assert.equal(task.bellReward.count,99);
  assert.equal(task.bellReward.source,'personal_bag');
  config.bellRewards.get(73194)[0].item_num_max=90;
  assert.throws(()=>familyFromRecords(records,[],config,100),/non-fixed/);
});

test('employee flat pair lists retain all crops and zero accumulated values',()=>{
  const config={name:id=>String(id),items:new Map([[14,{item_type:7}],[155,{item_type:7}]])};
  const bag=[[1,12,1,'ae_items,14_2_155_3;g_items,14_0_155_1;col_m,14_2#155_1;equip_ids,2'],[2,2009,1,'lv,37']];
  const plan=employeesOf(bag,{'38':0},config).plans[0];
  assert.deepEqual(plan.targets.map(x=>[x.itemId,x.count]),[[14,2],[155,3]]);
  assert.deepEqual(plan.accumulated.map(x=>x.count),[0,1]);
  assert.deepEqual(plan.heldProducts.map(x=>x.count),[2,1]);
  assert.throws(()=>employeesOf([[1,12,1,'ae_items,14_2_155']],{},config),/pair list/);
});

test('menu eligibility mirrors level gates and NORMAL_EASY exception',()=>{
  const config={name:id=>String(id),orderLevels:new Map([['1837:12',{params:'menu_lv,2'}]])};
  const order=[1,1837,1,'status,1;cts,14_2#155_3;order_data,0_0_12_1_127'];
  const menu=[2,127,1,'lv,1'];
  const inventory={products:[{itemId:14,count:9},{itemId:155,count:9}]};
  assert.equal(ordersOf([order,menu],config,inventory)[0].menuEligibility.levelSufficient,false);
  order[3]=order[3].replace('0_0_12','3_0_12');
  assert.equal(ordersOf([order,menu],config,inventory)[0].menuEligibility.levelSufficient,true);
  assert.equal(ordersOf([order],config,inventory)[0].menuEligibility.unlocked,false);
});

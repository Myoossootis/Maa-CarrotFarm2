'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {parseMMKV,crc32,progressOf,familyFromRecords,employeesOf,ordersOf}=require('./read_only_game_state.cjs');
const {orderCapacityOf,inventoryOf}=require('./read_only_game_state.cjs');

function capacityConfig(){return {version:'90',name:id=>String(id),items:new Map([
  [101,{params:'ref_id,103'}],[102,{params:'ref_id,104'}],
  [103,{item_type:24,params:'output_id,501'}],[104,{item_type:24,params:'output_id,502'}]
]),outputs:new Map([[501,{item_id:103,type:2,limit:6}],[502,{item_id:104,type:2,limit:6}]])};}

test('resident reader reloads the bag and does not keep a stale inventory or the key',()=>{
  const {createReader}=require('./read_only_game_state.cjs');
  const bags=[{bag:[[1,14,3]]},{bag:[[1,14,8]]}];
  let storageCalls=0,configCalls=0,recoverCalls=0,stamp='pkg-a';
  const reader=createReader({
    storageSnapshot:()=>({sequence:++storageCalls,capturedAt:'2026-11-03T04:00:00.000Z'}),
    configStamp:()=>stamp,
    configuration:()=>{configCalls+=1;return {version:'98'};},
    decrypt:(snapshot,key)=>{
      if(key!=='kept') throw Error('stale key');
      return bags[Math.min(snapshot.sequence,bags.length)-1];
    },
    recover:()=>{recoverCalls+=1;return {state:bags[0],key:'kept',hintUsed:false};},
    assemble:({state,config,mode,snapshot})=>({mode,version:config.version,count:state.bag[0][2],sequence:snapshot.sequence}),
  });
  const first=reader.read('automation').report;
  const second=reader.read('automation').report;
  assert.equal(first.count,3);
  assert.equal(second.count,8);
  assert.equal(storageCalls,2);
  assert.equal(configCalls,1);
  assert.equal(recoverCalls,1);
  assert.equal(first.key,undefined);
  stamp='pkg-b';
  reader.read('automation');
  assert.equal(configCalls,2);
});

test('a rejected decrypt drops the cached key and reads the new bag',()=>{
  const {createReader}=require('./read_only_game_state.cjs');
  let fail=false,recoverCalls=0;
  const reader=createReader({
    storageSnapshot:()=>({sequence:1,capturedAt:'2026-11-03T04:00:00.000Z'}),
    configStamp:()=>'pkg',
    configuration:()=>({version:'98'}),
    decrypt:()=>{if(fail) throw Error('stale');return {bag:[[1,14,1]]};},
    recover:()=>{recoverCalls+=1;return {state:{bag:[[1,14,9]]},key:'next',hintUsed:true};},
    assemble:({state})=>({count:state.bag[0][2]}),
  });
  assert.equal(reader.read('orders').report.count,9);
  assert.equal(reader.read('orders').report.count,1);
  fail=true;
  assert.equal(reader.read('orders').report.count,9);
  assert.equal(recoverCalls,2);
});

test('order capacity uses linked tokens, sums records and keeps zero separate from unknown',()=>{
  const config=capacityConfig(),inventory={records:[{itemId:103,count:2},{itemId:103,count:4},{itemId:104,count:1}]};
  const result=orderCapacityOf(inventory,config);
  assert.equal(result.complete,true);
  assert.deepEqual(result.entries.map(x=>[x.category,x.tokenItemId,x.remaining,x.limit]),[['party',103,6,6],['takeout',104,1,6]]);
  assert.equal(orderCapacityOf({records:[]},config).entries[0].remaining,0);
  assert.equal(orderCapacityOf({records:[]},config).complete,true);
  config.version='91';
  assert.equal(orderCapacityOf({records:[]},config).complete,false);
  assert.equal(orderCapacityOf({records:[]},config).reason,'unverified_package_version');
  assert.deepEqual(orderCapacityOf({records:[]},config).entries,[]);
});

test('packages 99 and 100 reuse the capacity contract; unverified packages do not pretend zero',()=>{
  const {VERIFIED_CAPACITY_PACKAGES}=require('./read_only_game_state.cjs');
  assert.deepEqual([...VERIFIED_CAPACITY_PACKAGES].sort(),['100','90','99']);
  for(const version of ['99','100']){
    const config=capacityConfig();
    config.version=version;
    const filled=orderCapacityOf({records:[{itemId:103,count:2},{itemId:104,count:6}]},config);
    assert.equal(filled.complete,true);
    assert.equal(filled.semanticsPackageVersion,version);
    assert.equal(filled.reason,undefined);
    assert.deepEqual(filled.entries,[
      {category:'party',tokenItemId:103,remaining:2,limit:6},
      {category:'takeout',tokenItemId:104,remaining:6,limit:6},
    ]);
    const none=orderCapacityOf({records:[]},config);
    assert.equal(none.complete,true);
    assert.deepEqual(none.entries.map(row=>row.remaining),[0,0]);
    const expired=inventoryOf([[1,103,6,'',99],[2,104,1,'',101]],config,100);
    assert.deepEqual(orderCapacityOf(expired,config).entries.map(row=>row.remaining),[0,1]);
    const drifted=capacityConfig();
    drifted.version=version;
    drifted.items.get(101).params='ref_id,105';
    drifted.items.set(105,{item_type:24,params:'output_id,501'});
    drifted.outputs.get(501).item_id=105;
    assert.throws(()=>orderCapacityOf({records:[{itemId:105,count:6}]},drifted),/token identity/);
    const badType=capacityConfig();
    badType.version=version;
    badType.items.get(103).item_type=1;
    assert.throws(()=>orderCapacityOf({records:[]},badType),/token type/);
    const badOutput=capacityConfig();
    badOutput.version=version;
    badOutput.outputs.get(501).type=1;
    assert.throws(()=>orderCapacityOf({records:[]},badOutput),/output/);
    const badLimit=capacityConfig();
    badLimit.version=version;
    badLimit.outputs.get(501).limit=7;
    assert.throws(()=>orderCapacityOf({records:[{itemId:103,count:4}]},badLimit),/limit/);
  }
  for(const version of ['98','101']){
    const other=capacityConfig();
    other.version=version;
    const result=orderCapacityOf({records:[{itemId:103,count:6},{itemId:104,count:6}]},other);
    assert.equal(result.complete,false);
    assert.equal(result.reason,'unverified_package_version');
    assert.equal(result.semanticsPackageVersion,version);
    assert.deepEqual(result.entries,[]);
  }
});

test('expired order tokens do not grant capacity and corrupt mappings or counts fail closed',()=>{
  const config=capacityConfig();
  const inventory=inventoryOf([[1,103,6,'',99],[2,104,3,'',101]],config,100);
  assert.deepEqual(orderCapacityOf(inventory,config).entries.map(x=>x.remaining),[0,3]);
  for(const count of [-1,NaN,1.5,Number.MAX_SAFE_INTEGER+1])
    assert.throws(()=>orderCapacityOf({records:[{itemId:103,count}]},config),/capacity/);
  config.outputs.get(501).item_id=104;
  assert.throws(()=>orderCapacityOf({records:[]},config),/capacity/);
});

function cdConfig(){
  const config=capacityConfig();
  config.outputs.get(501).cd=3600;
  config.outputs.get(501).params='day_limit,6';
  config.outputs.get(502).cd=3600;
  config.outputs.get(502).params='day_limit,6';
  return config;
}

function shanghaiMidnight(unix){
  const offset=8*3600;
  return unix-((unix+offset)%86400);
}

test('order capacity keeps the raw bag count when the token has no CD clock',()=>{
  const config=cdConfig();
  const raw=orderCapacityOf({records:[{itemId:103,count:2},{itemId:103,count:1},{itemId:104,count:4}]},config);
  assert.deepEqual(raw.entries.map(row=>row.remaining),[3,4]);
  const plain=capacityConfig();
  assert.equal(orderCapacityOf({records:[{itemId:103,count:2},{itemId:104,count:6}]},plain).entries[0].remaining,2);
});

test('bag count plus CD ticks is the client-effective remaining, capped at the limit',()=>{
  const config=cdConfig();
  const midnight=shanghaiMidnight(1762300800);
  const now=midnight+10*3600;
  const twoHours={itemId:103,count:0,extra:{refresh_time:now-2*3600,repeat_count:0}};
  const party=orderCapacityOf({records:[twoHours,{itemId:104,count:0}]},config,now);
  assert.equal(party.complete,true);
  assert.deepEqual(party.entries.map(row=>row.remaining),[2,0]);
  const capped=orderCapacityOf({records:[{itemId:103,count:0,extra:{REFRESH_TIME:now-10*3600,REPEAT_COUNT:0}}]},config,now);
  assert.equal(capped.entries[0].remaining,6);
  const stacked=orderCapacityOf({records:[{itemId:103,count:5,extra:`r_time,${now-3*3600};rp_c,0`}]},config,now);
  assert.equal(stacked.entries[0].remaining,6);
  const sameDay=orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:now-4*3600,repeat_count:5}}]},config,now);
  assert.equal(sameDay.entries[0].remaining,1);
  const yesterday=midnight-2*3600;
  const nextMorning=midnight+3*3600;
  const rolled=orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:yesterday,repeat_count:6}}]},config,nextMorning);
  assert.equal(rolled.entries[0].remaining,5);
  const future=orderCapacityOf({records:[{itemId:103,count:2,extra:{refresh_time:now+60}}]},config,now);
  assert.equal(future.entries[0].remaining,2);
  const rowWins=orderCapacityOf(
    {records:[{itemId:103,count:0,extra:{refresh_time:now-3600}}]},
    config,now,{103:{refresh_time:now-5*3600}});
  assert.equal(rowWins.entries[0].remaining,1);
});

test('missing bag without recover fields stays zero; common storage can supply one clock',()=>{
  const config=cdConfig();
  config.version='100';
  const now=shanghaiMidnight(1762300800)+8*3600;
  const none=orderCapacityOf({records:[]},config,now,{38:1,87:0});
  assert.equal(none.complete,true);
  assert.equal(none.semanticsPackageVersion,'100');
  assert.deepEqual(none.entries.map(row=>[row.remaining,row.limit]),[[0,6],[0,6]]);
  const common={103:{refresh_time:now-2*3600,repeat_count:0},104:'refresh_time,'+(now-3600)+';repeat_count,0'};
  const recovered=orderCapacityOf({records:[]},config,now,common);
  assert.deepEqual(recovered.entries.map(row=>row.remaining),[2,1]);
  const sharedOnce=orderCapacityOf({records:[{itemId:103,count:0},{itemId:103,count:0}]},config,now,{103:{refresh_time:now-2*3600}});
  assert.equal(sharedOnce.entries[0].remaining,2);
  const named={bag_note:{item_id:104,REFRESH_TIME:now-3*3600,REPEAT_COUNT:0}};
  assert.equal(orderCapacityOf({records:[]},config,now,named).entries[1].remaining,3);
  const expired=inventoryOf([[1,103,0,`refresh_time,${now-7200};repeat_count,0`,now-1]],config,now);
  assert.equal(orderCapacityOf(expired,config,now).entries[0].remaining,0);
  const live=inventoryOf([[1,103,0,`refresh_time,${now-7200};repeat_count,0`,now+10]],config,now);
  assert.equal(orderCapacityOf(live,config,now).entries[0].remaining,2);
});

test('package 100 still verifies the contract and bad CD fields fail closed',()=>{
  const config=cdConfig();
  config.version='100';
  const now=1_762_300_000;
  const filled=orderCapacityOf({records:[{itemId:103,count:2},{itemId:104,count:6}]},config,now);
  assert.equal(filled.complete,true);
  assert.equal(filled.semanticsPackageVersion,'100');
  assert.deepEqual(filled.entries.map(row=>row.remaining),[2,6]);
  const unverified=cdConfig();
  unverified.version='101';
  const hidden=orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:now-7200}}]},unverified,now);
  assert.equal(hidden.complete,false);
  assert.deepEqual(hidden.entries,[]);
  const badCd=cdConfig();
  badCd.outputs.get(501).cd=0;
  assert.throws(()=>orderCapacityOf({records:[]},badCd),/cd/);
  const badDay=cdConfig();
  badDay.outputs.get(501).params='day_limit,0';
  assert.throws(()=>orderCapacityOf({records:[]},badDay),/day_limit/);
  const half=cdConfig();
  delete half.outputs.get(501).params;
  assert.throws(()=>orderCapacityOf({records:[]},half),/day_limit/);
  assert.throws(()=>orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:1.5}}]},config,now),/recover field/);
  assert.throws(()=>orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:now-3600,r_time:now-7200}}]},config,now),/recover field/);
  assert.throws(()=>orderCapacityOf({records:[{itemId:103,count:0,extra:{refresh_time:now-3600,repeat_count:-1}}]},config,now),/recover field/);
  assert.throws(()=>orderCapacityOf({records:[{itemId:103,count:1,extra:{refresh_time:now-3600,repeat_count:7}}]},config,now),/recover field/);
});

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

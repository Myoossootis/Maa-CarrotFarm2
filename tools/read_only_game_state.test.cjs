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
      {category:'party',tokenItemId:103,remaining:2,limit:6,emptyTokenCache:false},
      {category:'takeout',tokenItemId:104,remaining:6,limit:6,emptyTokenCache:false},
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

test('UI fallback evidence survives decoding only when both live token rows and recovery clock are absent',()=>{
  const config=cdConfig(),now=1762300800;
  assert.equal(orderCapacityOf({records:[]},config,now).entries[0].emptyTokenCache,true);
  const zeroRow=orderCapacityOf({records:[{itemId:103,count:0}]},config,now).entries[0];
  assert.equal(zeroRow.remaining,0);
  assert.equal(zeroRow.emptyTokenCache,false);
  const clock=orderCapacityOf({records:[]},config,now,{103:{refresh_time:now+100}}).entries[0];
  assert.equal(clock.remaining,0);
  assert.equal(clock.emptyTokenCache,false);
  assert.equal(orderCapacityOf({records:[]},config,now,{104:true}).entries[1].emptyTokenCache,true);
});

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
  const booleanCommon=orderCapacityOf({records:[]},config,now,{104:true});
  assert.deepEqual(booleanCommon.entries.map(row=>row.remaining),[0,0]);
  assert.equal(orderCapacityOf({records:[]},config,now,{103:true,104:true}).entries[0].remaining,0);
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

test('family context reads self and season from records, keeps quota raw, and includes restaurant duty',()=>{
  const {familyContextOf}=require('./read_only_game_state.cjs');
  const rows=[
    {uid:1,itemId:3628,extra:{plrd:'1:7@4:同名玩家',r_id:'8'}},
    {uid:2,itemId:3921,extra:{season:'126'}},
    {uid:3,itemId:3956,count:19,extra:{},expiresAt:null},
    {uid:100,itemId:181,name:'员工甲',type:5,extra:{lv:'4'}},
    {uid:200,itemId:5,name:'餐厅',type:3,extra:{equip_ids:'100_101'}},
    {uid:300,itemId:23,type:2,extra:{equip_ids:'100'}}
  ];
  const result=familyContextOf({records:rows});
  assert.equal(result.selfUid,7);
  assert.equal(result.familyUid,8);
  assert.equal(result.season,126);
  assert.equal(result.periodTarget,26);
  assert.equal(result.completed,null);
  assert.equal(result.quotaVerified,false);
  assert.equal(result.rawQuotaRows[0].count,19);
  assert.deepEqual(result.staff[0].posts,[{buildingUid:200,buildingItemId:5,buildingName:'餐厅'}]);
  assert.equal(familyContextOf({records:[...rows,rows[0]]}).selfUid,null);
  assert.equal(familyContextOf({records:[]}).selfUid,null);
});

test('structured UP harvest, restaurant, stars, and forward upgrade are independent from upgraded flag',()=>{
  const {config,records}=fixture();
  config.items.set(173,{item_type:7});
  config.items.get(3879).params='mutation_id,4000';
  config.items.set(4000,{item_type:13,desc:'description',quality:2,params:''});
  config.tasks.set(4000,{check_condi:'3944,181,3948,6',reward_box_id:1});
  config.bellRewards.set(1,config.bellRewards.get(73194));
  let task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.requirement.kind,'harvest');
  assert.equal(task.requirement.staffItemId,2013);
  assert.equal(task.requirement.productItemId,173);
  assert.equal(task.requirement.count,172);
  assert.equal(task.canUpgrade,true);
  assert.equal(task.upgradeItemId,4000);
  assert.equal(task.upgrade.requirement.kind,'party_takeout');
  assert.equal(task.isUpgraded,false);
  config.tasks.get(3879).check_condi='3944,181,3948,6';
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.requirement.kind,'party_takeout');
  assert.equal(task.requirement.workplace,'restaurant');
  config.tasks.get(3879).check_condi='3949,30,6';
  config.language.set('description','完成%[2]次%[1]星或以上的聚会、外卖订单');
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.deepEqual(task.requirement,{kind:'star_orders',stars:30,count:6});
  config.tasks.get(3879).check_condi='999,181,3948,6';
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.requirement.kind,'unknown');
});

test('upgrade fee is separate from forward reward; regular harvest and quit clock are explicit',()=>{
  const {config,records}=fixture();
  config.version='100';
  config.items.set(5554,{params:'limit,136'});
  config.items.set(3921,{params:'cd,120'});
  config.items.set(173,{item_type:7});
  config.items.get(3879).params='mutation_id,4000';
  config.items.set(4000,{item_type:13,desc:'description',quality:2,params:'mutation_ref_id,3879'});
  config.tasks.set(4000,{check_condi:'3944,2013,173,344',reward_box_id:2});
  config.bellRewards.set(2,[{item_num_min:164,item_num_max:164,drop_rate:10000,drop_type:1}]);
  let task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.upgradeCost.count,136);
  assert.equal(task.upgrade.bellReward.count,164);
  assert.equal(task.upgrade.upgrade,null);
  config.tasks.get(3879).check_condi='687,1,173,12';
  task=familyFromRecords(records,[],config,100).tasks[0];
  assert.equal(task.requirement.kind,'regular_harvest');
  const {familyContextOf}=require('./read_only_game_state.cjs');
  const rows=[{itemId:3921,extra:{season:'1',lv:'4'}},{itemId:3628,extra:{plrd:'1:7',r_id:'8'}}];
  config.lionLevels=[{level:4,params:'datas,4185_3970_71190_71159_71191_2_71192_20_71193_55'}];
  let ctx=familyContextOf({records:rows},config);
  assert.equal(ctx.quitStartedUnix,0);
  assert.deepEqual(ctx.rules.refreshTiers,[{boxId:71191,cost:2},{boxId:71192,cost:20}]);
  rows[0].extra.cd='bad';
  assert.equal(familyContextOf({records:rows},config).quitStartedUnix,null);
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

test('season task count comes from own 3972 and matching shared 3921, never quota arithmetic',()=>{
  const {familyPeriodOf}=require('./read_only_game_state.cjs');
  const bag=[[1,3921,1,'season,126'],[2,3628,1,'plrd,1:7;r_id,8']];
  const rows=[{uid:10000,itemId:3921,extra:'season,126;e_time,1000;status,1'},
    {uid:10001,itemId:3972,extra:'r_id,7;r_c,2;exp,164'},
    {uid:10002,itemId:3972,extra:'r_id,9;r_c,24;exp,2046'}];
  const result=familyPeriodOf(rows,bag,100);
  assert.equal(result.completed,2);assert.equal(result.points,164);assert.equal(result.season,126);
  assert.equal(familyPeriodOf(rows.slice(1),bag,100),null);
  assert.equal(familyPeriodOf(rows,[[1,3921,1,'season,127'],bag[1]],100),null);
  assert.equal(familyPeriodOf([...rows,{uid:10003,itemId:3972,extra:'r_id,7;r_c,3;exp,246'}],bag,100),null);
  assert.equal(familyPeriodOf(rows,bag,1001),null);
  assert.equal(familyPeriodOf(rows.map(r=>r.itemId===3972&&r.uid===10001?{...r,extra:'r_id,7;r_c,bad;exp,164'}:r),bag,100),null);
});

test('harvest executor counter contract requires fixed 5740 child reward, not bell-only metadata',()=>{
  const {config,records}=fixture();config.version='100';
  const bell={item_id:3638,item_num_min:82,item_num_max:82,drop_type:1,drop_rate:10000,sub_box_ids:'72100'};
  const counter={item_id:5740,item_num_min:1,item_num_max:1,drop_type:1,drop_rate:10000,sub_box_ids:null};
  config.dropBoxes=new Map([[73194,[bell]],[72100,[counter]]]);
  assert.deepEqual(familyFromRecords(records,[],config,100).tasks[0].completionCounter,{itemId:5740,count:1,carrierItemId:2739});
  counter.item_num_max=2;
  assert.equal(familyFromRecords(records,[],config,100).tasks[0].completionCounter,null);
});

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

test('incomplete boards are rejected and conflicting slots are quarantined',()=>{
  const {config,records}=fixture();
  assert.throws(()=>familyFromRecords(records.slice(1),[],config,100),/89\/90/);
  const board=familyFromRecords([...records,{...records[0],extra:records[0].extra.replace('lock,55','lock,0')}],[],config,100);
  assert.equal(board.complete,false);
  assert.deepEqual(board.unknownSlots,[1]);
  assert.equal(board.tasks[0].uid,null);
  assert.equal(board.tasks[0].taskItemId,null);
  assert.equal(board.tasks[0].status,'unknown');
  assert.equal(board.tasks[1].status,'unclaimed');
});

test('contribution row identity remains ambiguous for any retained matching member variant',()=>{
  const {familyPeriodOf}=require('./read_only_game_state.cjs');
  const bag=[[1,3921,1,'season,126'],[2,3628,1,'plrd,1:7@2:48;r_id,8']];
  const rows=[{uid:10,itemId:3921,extra:'season,126;e_time,1000;status,1'},
    {uid:11,itemId:3972,extra:'r_id,7;r_c,2;exp,164'},
    {uid:12,itemId:3972,extra:'r_id,9;r_c,3;exp,246'},
    {uid:13,itemId:3629,extra:'plrd,1:7@2:48@4:甲'},
    {uid:14,itemId:3629,extra:'plrd,1:9@2:48@4:乙'}];
  assert.equal(familyPeriodOf(rows,bag,100).identityTripleUnique,true);
  const tied=[...rows,{uid:15,itemId:3972,extra:'r_id,9;r_c,2;exp,164'}];
  assert.equal(familyPeriodOf(tied,bag,100).identityTripleUnique,false);
  assert.equal(familyPeriodOf(rows.filter(r=>r.uid!==14),bag,100).identityTripleUnique,false);
  const wrongLevel=[bag[0],[2,3628,1,'plrd,1:7@2:49;r_id,8']];
  assert.equal(familyPeriodOf(rows,wrongLevel,100).identityTripleUnique,false);
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

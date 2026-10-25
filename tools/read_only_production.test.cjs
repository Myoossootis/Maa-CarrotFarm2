'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {decodeSlot,productionOf,plantingCatalogOf}=require('./read_only_production.cjs');
const config={version:'87',outputs:new Map([[103,{id:103,item_id:155,type:1,cd:300}],
  [205,{id:205,item_id:172,type:5,cd:10800}],[301,{id:301,item_id:29,type:1,cd:3600}]]),
  items:new Map([[155,{params:''}],[172,{params:'ref_id,1937'}],[29,{params:''}]]),name:id=>String(id)};

test('third tuple field is percent reduction, fourth is preserved cycle marker even when empty',()=>{
  const crop=decodeSlot('103_1000_1_1128',null,config,1100,0);
  assert.equal(crop.effectiveDurationSeconds,297);
  assert.equal(crop.status,'growing');assert.equal(crop.remainingSeconds,197);
  assert.equal(crop.matureAtUnix,1297);assert.equal(crop.cycleIndex,1128);
  const empty=decodeSlot('0_0_0_1128',null,config,2000,0);
  assert.equal(empty.status,'empty');assert.equal(empty.cycleIndex,1128);
  assert.equal(empty.matureAtUnix,null);assert.equal(empty.remainingSeconds,null);
});

test('maturity boundary and floor match client arithmetic',()=>{
  const at=now=>decodeSlot('103_1000_33_5',null,config,now,0);
  assert.equal(at(1200).status,'growing');assert.equal(at(1200).remainingSeconds,1);
  assert.equal(at(1201).status,'mature');assert.equal(at(1202).remainingSeconds,0);
});

test('charged animal credits earlier segments and does not mistake feed durations for item counts',()=>{
  const animal=decodeSlot('205_1000_1_372',[1800,10800],config,9000,4);
  assert.equal(animal.entityItemId,1937);assert.equal(animal.productItemId,172);
  assert.equal(animal.matureAtUnix,9892);assert.equal(animal.remainingSeconds,892);
  assert.equal(animal.feeding.creditedSeconds,1800);
  assert.equal(animal.feeding.currentSegmentLimitSeconds,10800);
  assert.equal(animal.slotIndex,4);assert.equal(animal.slot,5);
  assert.equal(decodeSlot('205_1000_1_372',[1800,10800],config,9892,4).canCollect,true);
});

test('feed exhaustion freezes countdown, suppresses absolute maturity, and survives a day of waiting',()=>{
  const animal=now=>decodeSlot('205_1000_0_1',[1800],config,now,0);
  const first=animal(2801),later=animal(2801+86400);
  assert.equal(first.status,'needs_feed');assert.equal(first.remainingSeconds,9000);
  assert.equal(first.feeding.paused,true);assert.equal(first.matureAtUnix,null);
  assert.equal(later.remainingSeconds,9000);assert.equal(later.canCollect,false);
  assert.equal(first.theoreticalEndAtUnix,11800);
});

test('missing feed data and unstarted clocks do not manufacture maturity timestamps',()=>{
  const animal=decodeSlot('205_0_0_1',null,config,5000,0);
  assert.equal(animal.status,'needs_feed');assert.equal(animal.remainingSeconds,10800);
  assert.equal(animal.matureAtUnix,null);assert.equal(animal.timingReliable,false);
  const crop=decodeSlot('103_0_0_1',null,config,5000,0);
  assert.equal(crop.status,'unknown');assert.equal(crop.canCollect,false);
});

test('missing tuples, unknown outputs and future package rules remain unknown',()=>{
  for(const raw of [null,'','103_1000_1','103_1000_x_2','999_1000_1_2']){
    const slot=decodeSlot(raw,null,config,2000,0);
    assert.equal(slot.status,'unknown');assert.equal(slot.matureAtUnix,null);
  }
  assert.equal(decodeSlot('103_1000_1_2',null,{...config,version:'88'},2000,0).status,'unknown');
});

function inventory(raw='103_1000_0_40#0_0_0_41',map=''){
  return {records:[{uid:11,itemId:12,type:3,name:'农田1',count:1,extra:{decoa_ids:'12_13'}},
    {uid:12,itemId:5474,type:4,count:2,extra:{r_id:'11',datas_list:raw,datas_map:map}},
    {uid:13,itemId:2009,type:6,count:1,extra:{r_id:'11'}}]};
}

test('parent decoration links select production carriers, not staff records; raw zero-based positions remain stable',()=>{
  const data=productionOf(inventory(),config,1100);
  assert.equal(data.slotCount,2);assert.equal(data.buildingCount,1);assert.equal(data.complete,true);
  assert.deepEqual(data.states,{empty:1,growing:1,mature:0,needs_feed:0,unknown:0});
  assert.equal(data.buildings[0].slots[1].cycleIndex,41);
  assert.equal(data.clock.serverClockVerified,false);
});

test('missing slots are emitted as unknown rather than silently omitted or called empty',()=>{
  const data=productionOf(inventory('103_1000_0_40'),config,1100);
  assert.equal(data.slotCount,2);assert.equal(data.complete,false);
  assert.equal(data.buildings[0].slots[1].status,'unknown');
});

test('chance list has four independent draws per slot and missing repeat count stays null',()=>{
  const input=inventory();input.records[1].extra.clst='12_34_56_78_90_11_22_33';
  input.records[1].extra.r_cs='2_0';
  const slots=productionOf(input,config,1100).buildings[0].slots;
  assert.deepEqual(slots[0].chanceRolls,{collectDouble:12,mutation:34,mutationCollectDouble:56,playerMutation:78});
  assert.equal(slots[1].chanceRolls.collectDouble,90);assert.equal(slots[1].repeatCount,0);
  assert.equal(productionOf(inventory(),config,1100).buildings[0].slots[0].repeatCount,null);
});

test('duplicate feed map keys fail closed without confusing zero-based keys with one-based display slots',()=>{
  const data=productionOf(inventory('103_1000_0_40#0_0_0_41','0_1800#0_10800'),config,1100);
  assert.equal(data.complete,false);assert.equal(data.states.unknown,2);
  assert.equal(data.buildings[0].slots[0].canCollect,null);
});

test('literal null feed map on an empty carrier is absent, not a corrupt slot index',()=>{
  const data=productionOf(inventory('0_0_0_40#0_0_0_41','null'),config,1100);
  assert.equal(data.complete,true);assert.equal(data.states.empty,2);
  assert.equal(data.buildings[0].raw.datas_map,'null');
});

test('manual crop catalog reads fixed price, excludes mutation and festival, and leaves noncached gold unknown',()=>{
  const cfg={...config,items:new Map([[14,{id:14,item_type:7,params:'output_id,101;need_p_lv,1'}],
    [155,{id:155,item_type:7,params:'output_id,103;need_p_lv,5;mutation_id,1959'}],
    [1959,{id:1959,item_type:7,params:'output_id,153;mutation_ref_id,155;need_p_lv,999'}],
    [6452,{id:6452,item_type:7,params:'output_id,17001;fes_crop,1'}],
    [1,{id:1,item_type:1,sync_type:4}]]),
    prices:[{item_id:14,type:0,buy_time:0,cost_items:'1,0'}, {item_id:155,type:0,buy_time:0,cost_items:'1,10'}],
    playerLevels:[{level:1,exp_item_num:23},{level:2,exp_item_num:59},{level:5,exp_item_num:500}]};
  const bag={records:[{itemId:61,count:100}]},catalog=plantingCatalogOf(bag,cfg);
  assert.deepEqual(catalog.map(c=>c.itemId),[14,155]);
  assert.equal(catalog[1].costs[0].available,null);
  assert.equal(catalog[1].costs[0].perSlot,10);
  assert.deepEqual(catalog[1].acceptedOutputIds,[103,153]);
  assert.equal(catalog[1].playerLevel,5);assert.equal(catalog[1].unlocked,true);
  assert.equal(catalog[0].costs[0].perSlot,0);
  bag.records[0].count=23;
  assert.equal(plantingCatalogOf(bag,cfg)[0].playerLevel,2);
});

test('catalog rejects variable prices, distinguishes zero balance and unknown level',()=>{
  const cfg={...config,items:new Map([[155,{id:155,item_type:7,params:'output_id,103;need_p_lv,5'}],
    [1,{id:1,sync_type:4}]]),prices:[{item_id:155,type:2,buy_time:0,cost_items:'1,10'}]};
  const bag={records:[{itemId:1,count:0}]};
  let crop=plantingCatalogOf(bag,cfg)[0];
  assert.equal(crop.unlocked,null);assert.equal(crop.costKnown,false);
  cfg.prices[0].type=0;crop=plantingCatalogOf(bag,cfg)[0];
  assert.equal(crop.costKnown,true);assert.equal(crop.costs[0].available,0);
});

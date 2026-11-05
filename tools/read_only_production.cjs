'use strict';

// CROP_IDX / ViewFarmModel timing verified for 87 and 90 byte-for-byte.
// Package 98 keeps the same ViewFarmModel timing bodies; only minifier aliases
// for farmland lock/unlock property slots differ. Evidence: .tmp/production98/.
// Package 99 ViewFarmModel is byte-identical to 98, including getOutputProgress,
// CROP_IDX, OUTPUT_TYPE and SRATE, so the same timing rules apply. Party/takeout
// order capacity is a separate contract in the order decoder (packages 90, 99, and 100).
// This module consumes decoded records only; it has no process, UI or network API.
// 100 ViewFarmModel and EnumTypes are byte-identical to 98 (2026-11-05).
const VERIFIED_PACKAGES=new Set(['87','90','98','99','100']);
const BUILDINGS=new Map([[12,'field'],[284,'field'],[285,'field'],[17,'ranch'],[723,'ranch'],[28,'orchard'],[286,'orchard']]);
const LABELS={empty:'空位',growing:'生长中',mature:'已成熟',needs_feed:'待喂食',unknown:'未判定'};
const paramsOf=value=>Object.fromEntries(String(value||'').split(';').filter(Boolean).map(v=>{const i=v.indexOf(',');return [v.slice(0,i),v.slice(i+1)];}));
const integers=value=>String(value).split('_').map(v=>v!==''&&/^\d+$/.test(v)?Number(v):NaN);
const valid=values=>values.every(Number.isSafeInteger);

function decodeSlot(raw,chargeCds,config,now,index){
  const values=raw===null?[]:integers(raw);
  const slot={slot:index+1,slotIndex:index,outputId:null,productItemId:null,productName:null,
    entityItemId:null,entityName:null,isMutation:null,startedAtUnix:null,
    cdRatePercent:null,cycleIndex:null,baseDurationSeconds:null,effectiveDurationSeconds:null,
    theoreticalEndAtUnix:null,matureAtUnix:null,remainingSeconds:null,canCollect:null,
    status:'unknown',statusLabel:LABELS.unknown,timingReliable:false,timingReason:'',
    chargeCds,feeding:null,feedLabel:null,
    raw:{datasList:raw,tuple:values.length&&valid(values)?values:null,chargeCds}};
  const unknown=reason=>{slot.timingReason=reason;return slot;};
  if(values.length!==4||!valid(values)) return unknown('缺少或无效的四元组，未将缺失数据当作空位');
  [slot.outputId,slot.startedAtUnix,slot.cdRatePercent,slot.cycleIndex]=values;
  if(!slot.outputId){
    slot.startedAtUnix=null;slot.status='empty';slot.statusLabel=LABELS.empty;slot.canCollect=false;
    slot.timingReliable=true;slot.timingReason='OUTPUT_ID=0；轮次标记仍保留';return slot;
  }
  const output=config.outputs.get(slot.outputId);
  if(!output||![1,5].includes(output.type)||!Number.isSafeInteger(output.cd)||output.cd<=0)
    return unknown('未知生产配置或生产类型');
  const item=config.items.get(output.item_id),itemParams=paramsOf(item?.params);
  slot.productItemId=output.item_id;slot.productName=config.name(output.item_id);
  slot.entityItemId=Number(itemParams.ref_id)||output.item_id;slot.entityName=config.name(slot.entityItemId);
  slot.isMutation=Number(itemParams.mutation_ref_id)>0;
  slot.baseDurationSeconds=output.cd;
  slot.effectiveDurationSeconds=output.cd-Math.floor(output.cd*(slot.cdRatePercent/100));
  if(!VERIFIED_PACKAGES.has(String(config.version))) return unknown('客户端版本未核对；当前已核对87、90、98、99、100版规则');
  if(slot.cdRatePercent>100) return unknown('减时百分比超出已核对范围');
  if(chargeCds!==null&&(!Array.isArray(chargeCds)||!chargeCds.length||!valid(chargeCds)))
    return unknown('无效的供料计时数组');
  const credited=chargeCds===null?0:chargeCds.slice(0,-1).reduce((a,b)=>a+b,0);
  const limit=chargeCds===null?null:chargeCds.at(-1);
  const started=slot.startedAtUnix,elapsed=now-started;
  const advanced=limit===null?elapsed:Math.min(elapsed,limit);
  // Exactly matches getOutputProgress + getProgressLeftCd, including floor.
  const end=started+slot.effectiveDurationSeconds-credited;
  const left=started<=0?slot.effectiveDurationSeconds:end-started-advanced;
  slot.remainingSeconds=Math.max(0,left);
  slot.theoreticalEndAtUnix=started>0?end:null;
  slot.canCollect=left<=0;
  const needsFeed=output.type===5&&(chargeCds===null||started<=0||elapsed>limit&&left>0);
  const enoughFeed=chargeCds===null?output.type!==5:slot.effectiveDurationSeconds-credited<=limit;
  slot.matureAtUnix=started>0&&enoughFeed?end:null;
  if(output.type===5){
    slot.feeding={required:true,known:chargeCds!==null,creditedSegmentsSeconds:chargeCds?.slice(0,-1)||[],
      creditedSeconds:credited,currentSegmentLimitSeconds:limit,
      segmentElapsedSeconds:started>0?Math.max(0,advanced):0,
      segmentRemainingSeconds:limit===null?null:Math.max(0,limit-Math.max(0,elapsed)),
      needsFeed,paused:!slot.canCollect&&(started<=0||chargeCds===null||elapsed>=limit),
      completionWithinCurrentSegment:enoughFeed};
    slot.feedLabel=chargeCds===null?'无供料计时记录；需要喂食':
      `已计入 ${credited} 秒；本段上限 ${limit} 秒${needsFeed?'；待喂食':''}`;
  }else slot.feedLabel='无需供料';
  slot.status=slot.canCollect?'mature':needsFeed?'needs_feed':started<=0?'unknown':'growing';
  slot.statusLabel=LABELS[slot.status];
  slot.timingReliable=started>0&&slot.status!=='unknown'&&(output.type!==5||chargeCds!==null);
  slot.timingReason=started<=0?'计时尚未开始，不能给出绝对成熟时间':
    !enoughFeed?'当前供料不足以完成；后续成熟时刻取决于喂食时间':
    '客户端公式已核对；状态按采集时本机时钟推算';
  return slot;
}

function plantingCatalogOf(inventory,config){
  const exp=inventory.records.filter(r=>r.itemId===61).reduce((n,r)=>n+r.count,0);
  const hasExp=inventory.records.some(r=>r.itemId===61);
  const levels=config.playerLevels||[];
  const playerLevel=hasExp&&levels.length?(levels.find(r=>exp<r.exp_item_num)?.level||levels.at(-1).level):null;
  return [...config.items.values()].filter(item=>{
    const p=paramsOf(item.params);
    return item.item_type===7&&!Number(p.mutation_ref_id)&&!Number(p.fes_crop)&&Number(p.output_id)>0;
  }).map(item=>{
    const p=paramsOf(item.params),seedId=Number(p.ref_id)||item.id;
    const prices=(config.prices||[]).filter(row=>row.item_id===seedId);
    const price=prices.length===1&&prices[0].type===0&&prices[0].buy_time===0&&
      !prices[0].day_buy_limit&&!prices[0].limit?prices[0]:null;
    let costs=[],costKnown=false;
    if(price&&price.cost_items!==null){
      costs=String(price.cost_items).split(';').filter(Boolean).map(raw=>{
        const v=raw.split(',').map(Number),id=v[0],amount=v[1],rows=inventory.records.filter(r=>r.itemId===id);
        return {itemId:id,name:config.name(id),perSlot:amount,
          available:rows.length?rows.reduce((sum,r)=>sum+r.count,0):config.items.get(id)?.sync_type===0?0:null};
      });
      costKnown=costs.length>0&&costs.every(c=>Number.isSafeInteger(c.itemId)&&Number.isSafeInteger(c.perSlot)&&c.perSlot>=0);
    }else if(!prices.length){
      const rows=inventory.records.filter(r=>r.itemId===seedId);
      costs=[{itemId:seedId,name:config.name(seedId),perSlot:1,available:rows.reduce((sum,r)=>sum+r.count,0)}];
      costKnown=true;
    }
    const mutationId=Number(p.mutation_id)||0,mutationOutput=Number(paramsOf(config.items.get(mutationId)?.params).output_id)||0;
    return {itemId:item.id,name:config.name(item.id),seedItemId:seedId,outputId:Number(p.output_id),
      acceptedOutputIds:[Number(p.output_id),mutationOutput].filter(Boolean),
      requiredLevel:Number(p.need_p_lv)||1,playerLevel,
      unlocked:playerLevel===null?null:playerLevel>=(Number(p.need_p_lv)||1),
      costKnown,costSource:prices.length?'cfg_price':'owned_seed',costs};
  }).sort((a,b)=>a.requiredLevel-b.requiredLevel||a.itemId-b.itemId);
}

function productionOf(inventory,config,now,common={}){
  const byUid=new Map(inventory.records.map(r=>[r.uid,r])),buildings=[],issues=[];
  for(const parent of inventory.records.filter(r=>BUILDINGS.has(r.itemId)).sort((a,b)=>a.itemId-b.itemId)){
    const linked=String(parent.extra.decoa_ids||'').split('_').filter(Boolean).map(Number);
    const carriers=linked.map(uid=>byUid.get(uid)).filter(r=>r&&r.type===4);
    if(!carriers.length) issues.push({buildingUid:parent.uid,reason:'建筑关联的槽位载体未提供',linkedUids:linked});
    for(const carrier of carriers){
      if(Number(carrier.extra.r_id)!==parent.uid){issues.push({buildingUid:parent.uid,carrierUid:carrier.uid,reason:'载体归属不匹配'});continue;}
      const rawList=carrier.extra.datas_list?String(carrier.extra.datas_list).split('#'):[];
      const chanceRolls=carrier.extra.clst?integers(carrier.extra.clst):[];
      const repeats=carrier.extra.r_cs?integers(carrier.extra.r_cs):[];
      const charge=new Map();let mapError=false;
      const rawMap=carrier.extra.datas_map==='null'?'':String(carrier.extra.datas_map||'');
      for(const raw of rawMap.split('#').filter(Boolean)){
        const v=integers(raw),key=v[0];
        if(v.length<2||!valid(v)||key>=carrier.count||charge.has(key)){mapError=true;continue;}
        charge.set(key,v.slice(1));
      }
      // Package100 getFieldCropDatasList pads newly expanded trailing slots to
      // [0,0,0,0]. Preserve the absent raw tuple and expose its derivation.
      const padNewSlots=String(config.version)==='100'&&rawList.length<carrier.count;
      if(rawList.length!==carrier.count&&!padNewSlots) issues.push({carrierUid:carrier.uid,reason:'槽位条数与载体数量不同',expected:carrier.count,actual:rawList.length});
      if(mapError) issues.push({carrierUid:carrier.uid,reason:'供料索引或格式有误；该载体计时未推断'});
      const slots=Array.from({length:carrier.count},(_,index)=>{
        const padded=padNewSlots&&index>=rawList.length;
        const value=decodeSlot(padded?'0_0_0_0':rawList[index]??null,charge.get(index)??null,config,now,index);
        if(padded){value.raw.datasList=null;value.raw.tuple=null;value.raw.clientInitializedTuple=[0,0,0,0];
          value.timingReason='已核对getFieldCropDatasList：扩建后未落盘的尾部槽位初始化为空';}
        const rolls=chanceRolls.slice(index*4,index*4+4);
        value.chanceRolls=rolls.length===4&&valid(rolls)?
          {collectDouble:rolls[0],mutation:rolls[1],mutationCollectDouble:rolls[2],playerMutation:rolls[3]}:null;
        value.repeatCount=Number.isSafeInteger(repeats[index])?repeats[index]:null;
        if(mapError){value.status='unknown';value.statusLabel=LABELS.unknown;value.timingReliable=false;
          value.timingReason='供料索引或格式有误';value.matureAtUnix=null;value.remainingSeconds=null;value.canCollect=null;}
        if(value.status==='unknown') issues.push({carrierUid:carrier.uid,slot:index+1,reason:value.timingReason});
        return value;
      });
      buildings.push({buildingUid:parent.uid,buildingItemId:parent.itemId,buildingName:parent.name,
        kind:BUILDINGS.get(parent.itemId),carrierUid:carrier.uid,carrierItemId:carrier.itemId,
        slotCount:carrier.count,slots,complete:VERIFIED_PACKAGES.has(String(config.version))&&!mapError&&
          (rawList.length===carrier.count||padNewSlots)&&slots.every(s=>s.status!=='unknown'),raw:{...carrier.extra}});
    }
  }
  const all=buildings.flatMap(b=>b.slots),states=Object.fromEntries(Object.keys(LABELS).map(k=>[k,all.filter(s=>s.status===k).length]));
  return {schemaVersion:1,semanticsPackageVersion:VERIFIED_PACKAGES.has(String(config.version))?String(config.version):null,asOfUnix:now,
    manualControl:{totalControlEnabled:Number(common['87']||0)===1},
    plantingCatalog:plantingCatalogOf(inventory,config),
    clock:{source:'host_utc',serverClockVerified:false,note:'客户端使用服务器校时后的时钟；本读取器未读取该内存时钟，临界状态可能有秒级或时钟偏差'},
    tupleFields:['outputId','timingAnchorUnix','cdRatePercent','cycleIndex'],
    fieldNotes:{startedAtUnix:'BEGIN_TIME为计时锚点；喂食会重置，加速会前移，不保证是最初种植时间',
      cycleIndex:'生产轮次/版本标记INDEX；开始新轮次时客户端加1，服务端可回填；不是成熟状态、肥力或已收获次数',
      chargeCds:'datas_map键为零基槽位索引；数组末项限制本段可推进秒数，其余项从所需耗时中扣除；不等于饲料数量',
      chanceRolls:'clst按SRATE_INDEX每槽4个随机判定数，并非百分比或产量；不重新计算员工能力或推断随机奖励',
      repeatCount:'r_cs对应的重复次数；未提供时保留null，与INDEX轮次标记不同',
      status:'根据OUTPUT_ID、客户端倒计时与供料规则派生；不存在独立的原始状态位',
      matureAtUnix:'供料足以完成时的缓存公式时间；不足或尚未开始时为null。不能用理论结束时间冒充实际截止时间'},
    buildingCount:buildings.length,slotCount:all.length,states,complete:issues.length===0,issues,buildings};
}

module.exports={decodeSlot,productionOf,plantingCatalogOf};

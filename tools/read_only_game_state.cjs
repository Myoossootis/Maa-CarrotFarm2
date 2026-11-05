'use strict';
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto'), cp=require('node:child_process'), readline=require('node:readline');
const {performance}=require('node:perf_hooks');
const {productionOf}=require('./read_only_production.cjs');
function readv(buf,p){var v=0,n=0,x;do{if(p>=buf.length||n>49)throw Error('varint');x=buf[p++];v+=(x&127)*2**n;n+=7;}while(x&128);return [v,p];}
function crc32(b){var c=0xffffffff;for(var byte of b){c^=byte;for(var j=0;j<8;j++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;}
function parseMMKV(buf){var p=readv(buf,0)[1],out=new Map,records=0;while(p<buf.length){var x=readv(buf,p),kl=x[0];p=x[1];if(kl>100000||p+kl>buf.length)throw Error('key range '+p);var key=buf.toString('utf8',p,p+kl);p+=kl;x=readv(buf,p);var vl=x[0];p=x[1];if(vl>buf.length-p)throw Error('value range '+p);if(vl)out.set(key,buf.subarray(p,p+vl));else out.delete(key);p+=vl;records++;}return {records,values:out};}
function unwrapValue(v){var x=readv(v,0);if(x[0]!==v.length-x[1])throw Error('inner length');return JSON.parse(v.toString('utf8',x[1]));}
function extraOf(s){return Object.fromEntries(String(s||'').split(';').filter(Boolean).map(seg=>{var i=seg.indexOf(',');return [seg.slice(0,i),seg.slice(i+1)]}));}

const ROOT=path.resolve(__dirname,'..');
const APP='wx2d7c1a53c1bd45e1';
const MODES=['inventory','orders','employees','production','automation','family','all','serve'];
const PRODUCT_TYPES=new Set([7,11,23,94]);

function argumentsOf(argv){
  const options={mode:argv[0]||'all',output:null,applet:null,python:path.join(ROOT,'.venv','Scripts','python.exe')};
  if(!MODES.includes(options.mode)) throw Error('Usage: node tools/read_only_game_state.cjs '+MODES.join('|')+' [--output FILE] [--applet DIR] [--python EXE]');
  for(let i=1;i<argv.length;i+=2){
    const key={'--output':'output','--applet':'applet','--python':'python'}[argv[i]];
    if(!key||!argv[i+1]) throw Error('Unknown or incomplete argument: '+argv[i]);
    options[key]=argv[i+1];
  }
  return options;
}

function findApplet(override){
  if(override) return path.resolve(override);
  const users=path.join(process.env.APPDATA,'Tencent','xwechat','radium','users');
  const found=fs.readdirSync(users).map(x=>path.join(users,x,'applet')).filter(x=>fs.existsSync(path.join(x,'local',APP,'usrmmkvstorage0',APP)));
  if(found.length!==1) throw Error('Expected one matching WeChat account; select its applet directory with --applet (found '+found.length+').');
  return found[0];
}

function bridge(options,request){
  const result=cp.spawnSync(options.python,[path.join(__dirname,'read_only_memory.py')],{
    input:JSON.stringify(request),encoding:'utf8',maxBuffer:32*1024*1024,windowsHide:true,timeout:120000
  });
  if(result.error) throw result.error;
  let data;
  try{data=JSON.parse(result.stdout);}catch{throw Error('Memory reader failed: '+result.stderr);}
  if(result.status!==0||data.error) throw Error(data.error||'Memory reader failed');
  return data.result;
}

function storageSnapshot(applet){
  const file=path.join(applet,'local',APP,'usrmmkvstorage0',APP);
  for(let attempt=0;attempt<5;attempt++){
    const before=fs.readFileSync(file+'.crc'), data=fs.readFileSync(file), after=fs.readFileSync(file+'.crc');
    if(before.length<32||after.length<32||data.length<4) throw Error('Truncated MMKV');
    const size=data.readUInt32LE(0);
    if(!before.subarray(0,32).equals(after.subarray(0,32))||size!==after.readUInt32LE(28)||size>data.length-4) continue;
    const payload=data.subarray(4,4+size);
    if(crc32(payload)!==after.readUInt32LE(0)) continue;
    return {payload,iv:after.subarray(12,28),sequence:after.readUInt32LE(8),capturedAt:new Date().toISOString()};
  }
  throw Error('MMKV changed during read or failed CRC; retry later');
}

function decryptStorage(snapshot,key){
  const cipher=crypto.createDecipheriv('aes-128-cfb',key,snapshot.iv);
  const bytes=Buffer.concat([cipher.update(snapshot.payload),cipher.final()]);
  if(!bytes.includes(Buffer.from('SAVE_KEY_BAG'))) throw Error('Wrong storage key');
  const values=parseMMKV(bytes).values;
  const decode=name=>values.has(name)?JSON.parse(unwrapValue(values.get(name)).data):null;
  const bag=decode('SAVE_KEY_BAG');
  if(!Array.isArray(bag)||!bag.length||!bag.every(x=>Array.isArray(x)&&Number.isInteger(x[0])&&Number.isInteger(x[1]))) throw Error('Invalid bag schema');
  return {bag,common:decode('SAVE_KEY_COMMON')||{}};
}

function recoverStorage(options,snapshot,processes,hints){
  // Cache only a validated address and process birth time, never key bytes.
  const mains=processes.filter(p=>!processes.some(q=>q.pid===p.parent));
  if(hints.key&&processes.some(p=>p.pid===hints.key.pid&&p.born===hints.key.born)){
    try{
      const candidate=bridge(options,{op:'keys',pid:hints.key.pid,addresses:[hints.key.address]})[0];
      let key=Buffer.from(candidate.key,'hex');
      if(hints.key.swap) key=Buffer.from(key).swap32();
      return {state:decryptStorage(snapshot,key),hintUsed:true,key};
    }catch{/* A moving/restarted runtime invalidates the hint; scan again. */}
  }
  for(const process of mains){
    for(const candidate of bridge(options,{op:'keys',pid:process.pid})){
      for(const swap of [false,true]){
        try{
          let key=Buffer.from(candidate.key,'hex');
          if(swap) key=Buffer.from(key).swap32();
          const state=decryptStorage(snapshot,key);
          hints.key={pid:process.pid,born:process.born,address:candidate.address,swap};
          return {state,hintUsed:false,key};
        }catch{/* Candidate must decrypt and validate this exact MMKV snapshot. */}
      }
    }
  }
  throw Error('No validated MMKV key in running WeChatAppEx; open the mini program and retry.');
}

function unpack(file){
  const b=fs.readFileSync(file);
  let buf=b;
  if(b.subarray(0,6).toString()==='V1MMWX'){
    const cipher=crypto.createDecipheriv('aes-256-cbc',crypto.pbkdf2Sync(APP,'saltiest',1000,32,'sha1'),Buffer.from('the iv: 16 bytes'));
    cipher.setAutoPadding(false);
    const head=Buffer.concat([cipher.update(b.subarray(6,1030)),cipher.final()]).subarray(0,1023);
    const tail=Buffer.from(b.subarray(1030));
    for(let i=0;i<tail.length;i++) tail[i]^=APP.charCodeAt(APP.length-2);
    buf=Buffer.concat([head,tail]);
  }
  if(buf[0]!==0xbe||buf[13]!==0xed) throw Error('Unsupported wxapkg header');
  let at=18;
  const files=new Map();
  for(let i=0,n=buf.readUInt32BE(14);i<n;i++){
    const length=buf.readUInt32BE(at);at+=4;
    const name=buf.toString('utf8',at,at+length);at+=length;
    const offset=buf.readUInt32BE(at),size=buf.readUInt32BE(at+4);at+=8;
    if(offset+size>buf.length) throw Error('Invalid package index');
    files.set(name,buf.subarray(offset,offset+size));
  }
  return files;
}

function configuration(applet){
  const folder=path.join(applet,'packages',APP);
  const versions=fs.readdirSync(folder).filter(x=>/^\d+$/.test(x)).sort((a,b)=>Number(b)-Number(a));
  const version=versions.find(x=>fs.existsSync(path.join(folder,x,'_subpackages_subpkg_.wxapkg')));
  if(!version) throw Error('No cached configuration package');
  const tables=new Map();
  for(const [name,bytes] of unpack(path.join(folder,version,'_subpackages_subpkg_.wxapkg'))){
    if(!name.includes('/import/')||!name.endsWith('.json')) continue;
    let asset;try{asset=JSON.parse(bytes);}catch{continue;}
    if(!Array.isArray(asset[5])) continue;
    for(const row of asset[5]){
      if(!Array.isArray(row)||!String(row[1]).startsWith('cfg_')||!Array.isArray(row[2]?.key)) continue;
      const [headers,...rows]=row[2].key;
      tables.set(row[1],rows.map(values=>Object.fromEntries(headers.map((key,i)=>[key,values[i]]))));
    }
  }
  const items=new Map((tables.get('cfg_items')||[]).map(x=>[x.id,x]));
  const language=new Map((tables.get('cfg_language_cn')||[]).map(x=>[x.key,x.value]));
  const tasks=new Map((tables.get('cfg_task')||[]).filter(x=>x.type===121).map(x=>[x.item_id,x]));
  const bellRewards=new Map();
  for(const row of tables.get('cfg_drop_box')||[]){
    if(row.item_id!==3638) continue;
    if(!bellRewards.has(row.box_id)) bellRewards.set(row.box_id,[]);
    bellRewards.get(row.box_id).push(row);
  }
  if(items.size<1000||tasks.size<90) throw Error('Incomplete game configuration');
  const name=id=>language.get(items.get(id)?.name)||items.get(id)?.name||'#'+id;
  const orderLevels=new Map((tables.get('cfg_level')||[]).filter(x=>x.type===4).map(x=>[x.item_id+':'+x.level,x]));
  const outputs=new Map((tables.get('cfg_output')||[]).map(x=>[x.id,x]));
  const prices=tables.get('cfg_price')||[];
  const playerLevels=(tables.get('cfg_level')||[]).filter(x=>x.type===0&&x.item_id===0).sort((a,b)=>a.level-b.level);
  return {version,items,language,tasks,bellRewards,orderLevels,outputs,prices,playerLevels,name};
}

function itemAmounts(raw,config){
  if(!raw) return [];
  return String(raw).split('#').filter(Boolean).map(part=>{
    const [itemId,count]=part.split('_').map(Number);
    if(!Number.isFinite(itemId)||!Number.isFinite(count)) throw Error('Invalid item amount: '+part);
    return {itemId,name:config.name(itemId),count};
  });
}

function flatItemAmounts(raw,config){
  if(!raw) return [];
  const values=String(raw).split('_').map(Number);
  if(values.length%2||values.some(x=>!Number.isFinite(x))) throw Error('Invalid employee item-pair list');
  const result=[];
  for(let i=0;i<values.length;i+=2) result.push({itemId:values[i],name:config.name(values[i]),count:values[i+1]});
  return result;
}

function inventoryOf(bag,config,now){
  const totals=new Map();
  const records=bag.filter(x=>!x[4]||x[4]>=now).map(([uid,itemId,count,extra,expiresAt])=>{
    const item=config.items.get(itemId);
    if(!item) throw Error('Unknown item configuration: '+itemId);
    if(PRODUCT_TYPES.has(item.item_type)) totals.set(itemId,(totals.get(itemId)||0)+count);
    return {uid,itemId,name:config.name(itemId),type:item.item_type,count,extra:extraOf(extra),expiresAt:expiresAt||null};
  });
  return {recordCount:records.length,products:[...totals].map(([itemId,count])=>({itemId,name:config.name(itemId),count})),records};
}

// Party/takeout count semantics checked on every read for these packages only.
// 90: client refreshOrderCount / showPanelNeedFood / acceptOrder, 2026-10-25.
// 99 and 100: same cfg contract as 90 (order 101/102 → ref_id token, item_type 24,
// output type 2, limit 1–6). Effective remaining follows BagCore.getItemNum:
// non-expired item_num plus getCdItemNumVo().iCdNum for a CD day_limit token.
// Ticks require a persisted refresh time. No row and no recover fields stay 0;
// do not synthesize output.limit. 2026-11-05 Asia/Singapore: the party panel
// showed 6/6 while MMKV had no 103/104 rows (rawTok=[]) and common['104'] was
// only boolean true. That is not a refresh clock, so this function still
// returns 0. The board probe may trust a readable UI counter for that empty
// entry; do not invent ticks here. No client package is in this repo, so
// there is no SceneGuestOrderLogic byte check.
// Other packages stay incomplete; do not infer 0.
const VERIFIED_CAPACITY_PACKAGES=new Set(['90','99','100']);
const CAPACITY_ORDERS=[['party',101],['takeout',102]];
// Verified token identity from the v90 static check. ref_id is still read from
// config; a different id fails closed instead of being treated as that balance.
const CAPACITY_TOKENS={party:103,takeout:104};
// setItemNum writes REFRESH_TIME and REPEAT_COUNT onto the item extra. This
// client persists refresh timestamps as r_time elsewhere; accept that alias
// and the constant names. A missing or non-positive refresh is not a clock.
const CAPACITY_REFRESH_KEYS=['refresh_time','REFRESH_TIME','r_time'];
const CAPACITY_REPEAT_KEYS=['repeat_count','REPEAT_COUNT','rp_c'];
const SHANGHAI_OFFSET=8*3600;

function hasOwn(obj,key){return !!obj&&Object.prototype.hasOwnProperty.call(obj,key);}

function asSafeInt(value){
  if(typeof value==='number') return Number.isSafeInteger(value)?value:NaN;
  if(typeof value==='string'&&/^-?\d+$/.test(value)){const n=Number(value);return Number.isSafeInteger(n)?n:NaN;}
  return NaN;
}

function recordExtra(extra){
  if(extra==null||extra==='') return {};
  if(typeof extra==='string') return extraOf(extra);
  if(typeof extra==='object'&&!Array.isArray(extra)) return extra;
  throw Error('Invalid order capacity recover field');
}

function keyedInteger(extra,keys){
  if(!extra||typeof extra!=='object') return undefined;
  let seen=false,value;
  for(const key of keys){
    if(!hasOwn(extra,key)) continue;
    const raw=extra[key];
    if(raw===''||raw==null) continue;
    const parsed=asSafeInt(raw);
    if(!Number.isSafeInteger(parsed)) throw Error('Invalid order capacity recover field');
    if(seen&&value!==parsed) throw Error('Invalid order capacity recover field');
    seen=true;value=parsed;
  }
  return seen?value:undefined;
}

function positiveRefresh(extra){
  const refresh=keyedInteger(extra,CAPACITY_REFRESH_KEYS);
  return refresh!==undefined&&refresh>0;
}

// null: this output has no CD contract, so remaining is the raw bag sum.
// Both cd and day_limit must be valid once either one is present.
function cdSpec(output){
  const params=extraOf(output?.params);
  const cdPresent=hasOwn(output,'cd')&&output.cd!==''&&output.cd!=null;
  const dayPresent=hasOwn(params,'day_limit')&&params.day_limit!=='';
  if(!cdPresent&&!dayPresent) return null;
  const cd=asSafeInt(output.cd),day=asSafeInt(params.day_limit);
  if(!cdPresent||!Number.isSafeInteger(cd)||cd<1) throw Error('Invalid order capacity configuration: cd');
  if(!dayPresent||!Number.isSafeInteger(day)||day<1) throw Error('Invalid order capacity configuration: day_limit');
  return {cd,dayLimit:day,limit:output.limit};
}

function shanghaiDay(unix){return Math.floor((unix+SHANGHAI_OFFSET)/86400);}

// Unreified ticks since REFRESH_TIME, capped by the stack room and today's
// day_limit. REPEAT_COUNT counts realized recoveries on the refresh time's
// Shanghai day; a previous day does not consume today's cap. Host UTC is the
// clock — the server-synced clock is not in this snapshot.
function cdRecoveredCount(count,extra,spec,now){
  const refresh=keyedInteger(extra,CAPACITY_REFRESH_KEYS);
  if(refresh===undefined||refresh<=0) return 0;
  const repeat=keyedInteger(extra,CAPACITY_REPEAT_KEYS)??0;
  if(repeat<0) throw Error('Invalid order capacity recover field');
  if(!Number.isSafeInteger(now)||now<0) throw Error('Invalid order capacity clock');
  if(now<refresh) return 0;
  const ticks=Math.floor((now-refresh)/spec.cd);
  const used=shanghaiDay(now)===shanghaiDay(refresh)?repeat:0;
  if(used>spec.dayLimit) throw Error('Invalid order capacity recover field');
  const room=spec.limit-count;
  const recovered=Math.min(ticks,room>0?room:0,spec.dayLimit-used);
  if(!Number.isSafeInteger(recovered)||recovered<0) throw Error('Invalid order capacity total');
  return recovered;
}

function asExtra(value){
  if(typeof value==='string') return extraOf(value);
  if(value&&typeof value==='object'&&!Array.isArray(value)) return value;
  return null;
}

function namesToken(extra,tokenId){
  const raw=extra.item_id??extra.itemId??extra.id;
  if(raw==null||raw==='') return false;
  const id=asSafeInt(raw);
  return id===tokenId;
}

// One virtual stack when the bag row itself has no clock. Direct common[tokenId]
// counts; any other value must name the token. Disagreeing clocks fail closed.
function commonRecoverExtra(common,tokenId){
  if(!common||typeof common!=='object'||Array.isArray(common)) return null;
  const candidates=[];
  const consider=(extra,force)=>{
    if(!extra||!(force||namesToken(extra,tokenId))||!positiveRefresh(extra)) return;
    candidates.push(extra);
  };
  consider(asExtra(common[tokenId]??common[String(tokenId)]),true);
  for(const [key,value] of Object.entries(common)){
    if(key===String(tokenId)) continue;
    consider(asExtra(value),false);
  }
  if(!candidates.length) return null;
  const refresh=keyedInteger(candidates[0],CAPACITY_REFRESH_KEYS);
  const repeat=keyedInteger(candidates[0],CAPACITY_REPEAT_KEYS)??0;
  for(const extra of candidates.slice(1)){
    if(keyedInteger(extra,CAPACITY_REFRESH_KEYS)!==refresh||(keyedInteger(extra,CAPACITY_REPEAT_KEYS)??0)!==repeat)
      throw Error('Invalid order capacity recover field');
  }
  return candidates[0];
}

function orderCapacityOf(inventory,config,now=Math.floor(Date.now()/1000),common){
  // Client-effective token balance: non-expired item_num plus unreified CD
  // ticks, capped at output.limit. Separate from the historical completion
  // ledger. A verified package still fails closed when the live config no
  // longer matches the contract.
  const version=String(config.version);
  const result={complete:false,semanticsPackageVersion:version,entries:[]};
  if(!VERIFIED_CAPACITY_PACKAGES.has(version)) return {...result,reason:'unverified_package_version'};
  for(const [category,orderId] of CAPACITY_ORDERS){
    const orderItem=config.items.get(orderId);
    const tokenId=Number(extraOf(orderItem?.params).ref_id);
    const token=Number.isSafeInteger(tokenId)?config.items.get(tokenId):undefined;
    const outputId=Number(extraOf(token?.params).output_id);
    const output=config.outputs?.get(outputId);
    if(!orderItem) throw Error('Invalid order capacity configuration: '+category+' order item');
    if(!Number.isSafeInteger(tokenId)||!token) throw Error('Invalid order capacity configuration: '+category+' token ref');
    if(tokenId!==CAPACITY_TOKENS[category]) throw Error('Invalid order capacity configuration: '+category+' token identity');
    if(token.item_type!==24) throw Error('Invalid order capacity configuration: '+category+' token type');
    if(!output||output.item_id!==tokenId||output.type!==2) throw Error('Invalid order capacity configuration: '+category+' output');
    if(!Number.isSafeInteger(output.limit)||output.limit<1||output.limit>6)
      throw Error('Invalid order capacity configuration: '+category+' limit');
    const spec=cdSpec(output);
    const rows=inventory.records.filter(x=>x.itemId===tokenId);
    let remaining=0;
    if(!spec){
      for(const record of rows){
        if(!Number.isSafeInteger(record.count)||record.count<0) throw Error('Invalid order capacity count');
        remaining+=record.count;
      }
    }else if(!rows.length){
      const shared=commonRecoverExtra(common,tokenId);
      if(shared) remaining=cdRecoveredCount(0,shared,spec,now);
    }else{
      const parsed=rows.map(record=>{
        if(!Number.isSafeInteger(record.count)||record.count<0) throw Error('Invalid order capacity count');
        return {count:record.count,extra:recordExtra(record.extra)};
      });
      if(parsed.some(row=>positiveRefresh(row.extra))){
        for(const row of parsed){
          const recovered=positiveRefresh(row.extra)?cdRecoveredCount(row.count,row.extra,spec,now):0;
          remaining+=row.count+recovered;
        }
      }else{
        const count=parsed.reduce((sum,row)=>sum+row.count,0);
        const shared=commonRecoverExtra(common,tokenId);
        remaining=count+(shared?cdRecoveredCount(count,shared,spec,now):0);
      }
    }
    if(!Number.isSafeInteger(remaining)) throw Error('Invalid order capacity total');
    if(remaining>output.limit) remaining=output.limit;
    result.entries.push({category,tokenItemId:tokenId,remaining,limit:output.limit});
  }
  result.complete=true;
  return result;
}

function ordersOf(bag,config,inventory){
  const stock=new Map(inventory.products.map(x=>[x.itemId,x.count]));
  return [{id:1837,category:'normal'},{id:101,category:'party'},{id:102,category:'takeout'}].flatMap(({id,category})=>
    bag.filter(x=>x[1]===id).sort((a,b)=>a[0]-b[0]).map((x,i)=>{
      const extra=extraOf(x[3]),data=String(extra.order_data||'').split('_').map(Number);
      const costs=itemAmounts(extra.cts,config).map(cost=>({...cost,stock:stock.get(cost.itemId)||0,shortfall:Math.max(0,cost.count-(stock.get(cost.itemId)||0))}));
      const levelConfig=config.orderLevels?.get(id+':'+data[2]);
      const menuLevel=levelConfig?(Number(extraOf(levelConfig.params).menu_lv)||1):null;
      const menus=data.slice(4).map(itemId=>{const record=bag.find(x=>x[1]===itemId);return {itemId,unlocked:!!record,level:record?(Number(extraOf(record[3]).lv)||1):null};});
      return {category,slot:i+1,uid:x[0],statusCode:Number(extra.status),status:({1:'waiting',2:'doing',3:'success',4:'refresh',5:'fail',88:'client_refresh',99:'client_doing'})[extra.status]||'unknown',
        costs,rewards:itemAmounts(extra.rws,config),materialsSufficient:costs.length>0&&costs.every(x=>!x.shortfall),
        rareType:data[0],requiredStaff:data[1]||null,requiredLevel:data[2],guestCount:data[3],menuIds:data.slice(4),refreshAt:Number(extra.r_time)||null,
        menuEligibility:{requiredMenuLevel:menuLevel,menus,unlocked:menus.length>0&&menus.every(x=>x.unlocked),
          levelSufficient:menuLevel===null?null:data[0]===3||menus.every(x=>x.level!==null&&x.level>=menuLevel)}};
    }));
}

function employeesOf(bag,common,config){
  const buildings=new Set([12,284,285,17,723,28,286,32,878]);
  const byUid=new Map(bag.map(x=>[x[0],x]));
  return {repeatEnabled:Number(common['38'])===1,plans:bag.filter(x=>buildings.has(x[1])).map(x=>{
    const extra=extraOf(x[3]);
    const employees=String(extra.equip_ids||'').split('_').filter(Boolean).map(Number).map(uid=>{
      const staff=byUid.get(uid);if(!staff) return {uid,available:false};
      const detail=extraOf(staff[3]);
      return {uid,itemId:staff[1],name:config.name(staff[1]),level:Number(detail.lv)||1};
    });
    return {buildingUid:x[0],buildingItemId:x[1],buildingName:config.name(x[1]),employees,
      targets:flatItemAmounts(extra.ae_items,config),accumulated:flatItemAmounts(extra.g_items,config),
      heldProducts:itemAmounts(extra.col_m,config).filter(x=>PRODUCT_TYPES.has(config.items.get(x.itemId)?.item_type)),
      productionOperations:extra.ops||null,refreshAt:Number(extra.r_time)||null,extra};
  })};
}

function taskDescription(id,config){
  const task=config.tasks.get(id),item=config.items.get(id);
  if(!task||!item) throw Error('Unknown family task: '+id);
  const description=config.language.get(item.desc)||item.desc||'';
  const args=task.check_condi.split(',').slice(1).map(Number),values=args.map(String);
  if(description.startsWith('安排')) values[0]=config.name(args[0]);
  if(description.includes('收获')) values[1]=config.name(args[1]);
  const text=description.replace(/%\[(\d+)\]/g,(_,n)=>values[Number(n)-1]??'未知').replace(/\n/g,'；');
  if(!text||text.includes('未知')||text.includes('%[')) throw Error('Unresolved task description: '+id);
  const rewards=config.bellRewards?.get(task.reward_box_id)||[];
  if(rewards.length!==1||rewards[0].item_num_min!==rewards[0].item_num_max||rewards[0].drop_rate!==10000||rewards[0].drop_type!==1)
    throw Error('Missing or non-fixed family bell reward: '+id);
  const params=extraOf(item.params);
  return {text,condition:task.check_condi,target:description.startsWith('完成')?args[1]:args[2],
    durationSeconds:Number(params.expire_cd)||null,quality:item.quality,
    isRare:item.quality>0,rarity:item.quality>0?'rare':'normal',
    isUpgraded:Number(params.mutation_ref_id)>0,rewardBoxId:task.reward_box_id,
    bellReward:{itemId:3638,name:'铃铛',count:rewards[0].item_num_min,source:'task_configuration'}};
}

function progressOf(extra,personal,target){
  const source=personal?extraOf(personal[3]):extra;
  const checks=String(source.tk_c||'').split('#').filter(Boolean).map(x=>x.split('_').map(Number));
  return {current:checks.length===1?checks[0][1]:null,target,available:checks.length===1,
    source:personal?'personal_bag':checks.length?'shared_cache':'not_provided',checks};
}

function familyFromRecords(records,bag,config,now){
  const members=new Map(),slots=new Map();
  for(const record of records.filter(x=>x.itemId===3629)){
    const profile=Object.fromEntries(String(extraOf(record.extra).plrd||'').split('@').map(x=>{const i=x.indexOf(':');return [x.slice(0,i),x.slice(i+1)];}));
    if(profile['1']&&profile['4']) members.set(Number(profile['1']),profile['4']);
  }
  for(const record of records.filter(x=>x.itemId===3931)){
    const extra=extraOf(record.extra),slot=Number(extra.idx);
    if(!(slot>=1&&slot<=90)||!config.tasks.has(Number(extra.r_id))) continue;
    const previous=slots.get(slot);
    if(previous&&(previous.uid!==record.uid||JSON.stringify(previous.extra)!==JSON.stringify(extra)))
      throw Error('Conflicting cached versions of task slot '+slot+'; cannot safely choose a current value.');
    slots.set(slot,{...record,extra});
  }
  if(slots.size!==90) throw Error('Incomplete shared board: '+slots.size+'/90 slots. Open the shared task board and retry.');
  const member=uid=>uid?{uid,name:members.get(uid)||null}:null;
  const tasks=[...slots].sort((a,b)=>a[0]-b[0]).map(([slot,record])=>{
    const extra=record.extra,taskId=Number(extra.r_id),definition=taskDescription(taskId,config);
    const acceptorUid=Number(extra.lock)||0,upgraderUid=Number(extra.exp)||0,deadline=Number(extra.e_time)||null;
    const personal=bag.find(x=>config.items.get(x[1])?.item_type===13&&Number(extraOf(x[3]).r_id)===record.uid);
    const rewardOverride=personal?extraOf(personal[3]).rws:extra.rws;
    if(rewardOverride){
      const bell=itemAmounts(rewardOverride,config).find(x=>x.itemId===3638);
      if(bell) definition.bellReward={itemId:3638,name:'铃铛',count:bell.count,source:personal?'personal_bag':'shared_cache'};
    }
    return {slot,uid:record.uid,taskItemId:taskId,...definition,
      status:acceptorUid?'occupied':Number(extra.r_time)>now?'cooling_down':'unclaimed',
      acceptor:member(acceptorUid),upgrader:member(upgraderUid),upgradeCount:Number(extra.sp_c)||0,
      progress:progressOf(extra,personal,definition.target),deadlineUnix:deadline,
      deadline:deadline?new Date(deadline*1000).toISOString():null,deadlinePassed:deadline?deadline<now:null,
      refreshUnix:Number(extra.r_time)||null,personalStatusCode:personal?Number(extraOf(personal[3]).status):null};
  });
  for(const task of tasks){
    if((task.acceptor&&!task.acceptor.name)||(task.upgrader&&!task.upgrader.name)) throw Error('Family member names are incomplete; reopen the board and retry.');
  }
  return {slotCount:tasks.length,occupiedCount:tasks.filter(x=>x.status==='occupied').length,
    rareCount:tasks.filter(x=>x.isRare).length,upgradedCount:tasks.filter(x=>x.isUpgraded).length,tasks};
}

function familyOf(options,processes,hints,bag,config,now){
  const ordered=[...processes].sort((a,b)=>Number(b.pid===hints.game?.pid)-Number(a.pid===hints.game?.pid)||b.workingSet-a.workingSet);
  let incomplete;
  for(const process of ordered){
    // Always rescan records: old heap objects can remain readable after the board changes.
    const result=bridge(options,{op:'records',pid:process.pid});
    if(!result.records.some(x=>x.itemId===3931)) continue;
    let board;
    try{board=familyFromRecords(result.records,bag,config,now);}
    catch(error){
      if(!error.message.startsWith('Incomplete shared board:')) throw error;
      incomplete=error;
      continue;
    }
    hints.game={pid:process.pid,born:process.born};
    return board;
  }
  throw incomplete||Error('No readable family board in running WeChatAppEx; open the shared task board first.');
}

function buildReport(options,ctx){
  const {snapshot,state,hintUsed,config,processes,hints,timings,stage}=ctx;
  const now=Math.floor(Date.now()/1000),data={};
  const inventory=stage('inventoryMs',()=>inventoryOf(state.bag,config,now));
  if(['inventory','automation','all'].includes(options.mode)) data.inventory=inventory;
  if(['orders','automation','all'].includes(options.mode)){
    data.orders=stage('ordersMs',()=>ordersOf(state.bag,config,inventory));
    data.orderCapacity=stage('orderCapacityMs',()=>orderCapacityOf(inventory,config,now,state.common));
  }
  if(['employees','automation','all'].includes(options.mode)) data.employees=stage('employeesMs',()=>employeesOf(state.bag,state.common,config));
  if(['production','automation','all'].includes(options.mode)) data.production=stage('productionMs',()=>productionOf(inventory,config,now,state.common));
  if(['family','all'].includes(options.mode)){
    if(!processes) throw Error('Family board requires a fresh process list');
    data.family=stage('familyMs',()=>familyOf(options,processes,hints,state.bag,config,now));
  }
  return {schemaVersion:1,mode:options.mode,readOnly:true,capturedAt:snapshot.capturedAt,completedAt:new Date().toISOString(),
    source:'local_client_cache',packageVersion:config.version,storageSequence:snapshot.sequence,keyAddressHintUsed:hintUsed,
    timings,notes:['Client cache snapshot; server state may have changed.','Occupied tasks can omit shared progress; null does not mean zero.','No game operation, network request, process write, or key persistence.'],...data};
}

function createReader(deps){
  // Key bytes stay in this process. Every read loads a new storage snapshot.
  let key=null,config=null,configMark=null;
  return {read(mode){
    if(!MODES.includes(mode)||mode==='serve') throw Error('Unsupported decoder mode: '+mode);
    const started=performance.now(),timings={};
    const stage=(name,fn)=>{const start=performance.now();const value=fn();timings[name]=+(performance.now()-start).toFixed(2);return value;};
    const snapshot=stage('storageSnapshotMs',()=>deps.storageSnapshot());
    const mark=deps.configStamp();
    if(config===null||mark!==configMark){
      config=stage('configurationMs',()=>deps.configuration());
      configMark=mark;
    }
    let state=null,hintUsed=true;
    if(key){
      try{state=deps.decrypt(snapshot,key);}
      catch{key=null;state=null;}
    }
    if(!state){
      const recovered=stage('keyAndStorageDecodeMs',()=>deps.recover(snapshot));
      if(!recovered||!recovered.key||!recovered.state) throw Error('decoder key recovery did not return an in-memory key');
      state=recovered.state;
      key=recovered.key;
      hintUsed=recovered.hintUsed===true;
    }
    const report=deps.assemble({mode,snapshot,state,config,hintUsed,timings,stage});
    timings.totalMs=+(performance.now()-started).toFixed(2);
    if(Object.prototype.hasOwnProperty.call(report,'key')) throw Error('decoder report must not carry a key');
    return {report};
  }};
}

function configStamp(applet){
  const folder=path.join(applet,'packages',APP);
  const versions=fs.readdirSync(folder).filter(x=>/^\d+$/.test(x)).sort((a,b)=>Number(b)-Number(a));
  const version=versions.find(x=>fs.existsSync(path.join(folder,x,'_subpackages_subpkg_.wxapkg')));
  if(!version) throw Error('No cached configuration package');
  const file=path.join(folder,version,'_subpackages_subpkg_.wxapkg');
  const stat=fs.statSync(file);
  return version+':'+stat.mtimeMs+':'+stat.size;
}

function serve(options){
  const applet=findApplet(options.applet),hintFile=path.join(ROOT,'.tmp','read_only_decoder','hints.json');
  let hints={};try{hints=JSON.parse(fs.readFileSync(hintFile));}catch{}
  if(hints.applet!==applet) hints={applet};
  let processes=null;
  const reader=createReader({
    storageSnapshot:()=>storageSnapshot(applet),
    configStamp:()=>configStamp(applet),
    configuration:()=>configuration(applet),
    decrypt:(snapshot,key)=>decryptStorage(snapshot,key),
    recover(snapshot){
      processes=bridge(options,{op:'processes'});
      const recovered=recoverStorage(options,snapshot,processes,hints);
      fs.mkdirSync(path.dirname(hintFile),{recursive:true});
      fs.writeFileSync(hintFile,JSON.stringify(hints,null,2));
      return recovered;
    },
    assemble:input=>buildReport({mode:input.mode,applet:options.applet,python:options.python,output:null},{
      snapshot:input.snapshot,state:input.state,hintUsed:input.hintUsed,config:input.config,
      processes,hints,timings:input.timings,stage:input.stage}),
  });
  const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
  lines.on('line',line=>{
    const text=line.trim();
    if(!text) return;
    let request={};
    try{request=JSON.parse(text);}
    catch{process.stdout.write(JSON.stringify({ok:false,error:'invalid decoder request'})+'\n');return;}
    try{
      const {report}=reader.read(request.mode||'automation');
      process.stdout.write(JSON.stringify({id:request.id,ok:true,report})+'\n');
    }catch(error){
      process.stdout.write(JSON.stringify({id:request.id,ok:false,error:error.message})+'\n');
    }
  });
}

function main(){
  const started=performance.now(),options=argumentsOf(process.argv.slice(2));
  if(options.mode==='serve'){serve(options);return;}
  const timings={};
  function stage(name,fn){const start=performance.now();const value=fn();timings[name]=+(performance.now()-start).toFixed(2);return value;}
  const applet=findApplet(options.applet),hintFile=path.join(ROOT,'.tmp','read_only_decoder','hints.json');
  let hints={};try{hints=JSON.parse(fs.readFileSync(hintFile));}catch{}
  if(hints.applet!==applet) hints={applet};
  const processes=stage('processDiscoveryMs',()=>bridge(options,{op:'processes'}));
  const snapshot=stage('storageSnapshotMs',()=>storageSnapshot(applet));
  const recovered=stage('keyAndStorageDecodeMs',()=>recoverStorage(options,snapshot,processes,hints));
  const config=stage('configurationMs',()=>configuration(applet));
  fs.mkdirSync(path.dirname(hintFile),{recursive:true});
  fs.writeFileSync(hintFile,JSON.stringify(hints,null,2));
  const report=buildReport(options,{snapshot,state:recovered.state,hintUsed:recovered.hintUsed,config,processes,hints,timings,stage});
  timings.totalMs=+(performance.now()-started).toFixed(2);
  const json=JSON.stringify(report,null,2)+'\n';
  if(options.output){const output=path.resolve(options.output);fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,json);}
  else process.stdout.write(json);
  process.stderr.write(JSON.stringify({mode:options.mode,timings,output:options.output||'stdout'})+'\n');
}

module.exports={readv,crc32,parseMMKV,unwrapValue,extraOf,progressOf,familyFromRecords,inventoryOf,ordersOf,employeesOf,orderCapacityOf,VERIFIED_CAPACITY_PACKAGES,createReader};
if(require.main===module){try{main();}catch(error){process.stderr.write(JSON.stringify({error:error.message,readOnly:true})+'\n');process.exitCode=1;}}

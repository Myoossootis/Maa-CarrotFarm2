# 农田、牧场、果园逐槽位只读解码

> 2026-10-25：已核对并支持 90 版，86 槽、0 unknown；修复取整顺序并保留未知版本拦截。新增证据与限制见 [90 版验证](PRODUCTION90_VERIFICATION_2026-10-25.md)。下方样本为 10 月 18 日的 87 版历史记录。

2026-10-18，已接入 `tools/read_only_game_state.cjs` 和 Maa 只读数据窗口。
不导入控制器、不点击、不发游戏请求、不写微信缓存或进程内存。

## 运行与查看

```powershell
node tools/read_only_game_state.cjs production --output .tmp/production_decode/production.json
node tools/read_only_game_state.cjs automation --output .tmp/production_decode/current_state.json
.venv/Scripts/python.exe tools/decoded_dashboard.py --snapshot .tmp/production_decode/current_state.json --tab production
```

`production` 只返回生产部分；`automation` 同时返回库存、订单、员工计划及生产部分，省去家族任务堆扫描。
窗口新页为“农田 / 牧场 / 果园”，全局搜索可筛选区域、产品或状态。点“只读刷新”更新快照，倒计时显示的是采集时数值，不在旧快照上假装实时推进。
以 production 页启动的窗口使用 automation 刷新，狮王任务显示“未读取”；其他窗口仍按原 all 模式读取。
本次 all 模式发现任务槽位 2 的旧堆对象版本冲突，原有拒绝冲突规则保留；此问题不影响来自 MMKV 的生产数据。

## 已确认的四元组

由建筑 `extra.decoa_ids` 关联槽位载体 UID，并用载体 `r_id` 反查归属。载体 `count` 是槽位数。
`datas_list` 以 `#` 分槽，顺序是零基物理槽位索引；窗口显示索引加 1。

客户端 `EnumTypes.CROP_IDX` 明确定义：

| 位置 | 原名称 | 结构化字段 | 含义 |
| --- | --- | --- | --- |
| 0 | OUTPUT_ID | outputId | cfg_output 配置 ID；0 表示空位，不是背包物品 ID |
| 1 | BEGIN_TIME | startedAtUnix | 当前计时锚点，Unix 秒；喂食会重置，加速会前移，不能保证是最初种植时间 |
| 2 | CD_RATE | cdRatePercent | 百分制减时值，如 1 表示减少基础耗时的 1%；不是状态 |
| 3 | INDEX | cycleIndex | 生产轮次／版本标记；不是肥力、成熟状态或已收获次数 |

`FarmManager.sendOutputBegin` 给 `INDEX` 加 1；`ViewFarmModel.recordFieldMtPredictResult` 保存该标记，
`getMtPredictRecordStatus` 比较当前标记，失配就不再使用旧轮次预测。
`sendOutputGain` 的本地临时处理会清零整条四元组，随后服务器同步可回填；因此不能把 `INDEX` 当作可靠累计收获次数，也不承诺它严格连续。

`cfg_output.item_id` 提供产品名称；产品 `ref_id` 可追到实体，例如 output 205 → 牛奶 172 → 奶牛 1937。
output 351 是青苹果，不是白苹果。`isMutation` 从产品配置 `mutation_ref_id` 读取。
`clst` 按 `SRATE_INDEX` 每槽 4 个随机判定数，输出为 `chanceRolls`：收获双倍、变异、变异收获双倍、玩家变异判定。
这些是用于与概率阈值比较的数，不是概率百分比；本次不重新核算员工能力或推断随机产量。
独立的 `r_cs` 才是重复次数列表，未提供时 `repeatCount=null`，不把它与 INDEX 混淆。
载体原始附加字段完整保留在 `buildings[].raw`，四元组和供料数组保留在每槽 `raw`。

## 成熟时间、供料和派生状态

精确复现 `ViewFarmModel.getOutputProgress`、`getProgressLeftCd` 和 `isNeedChargeCd` 的只读计算：

```text
duration = cfg_output.cd - floor(cfg_output.cd × CD_RATE / 100)
credit = sum(chargeCds 中除末项以外的数值)
end = BEGIN_TIME + duration - credit
elapsed = now - BEGIN_TIME
如果有供料数组：elapsed = min(elapsed, chargeCds 末项)
left = end - BEGIN_TIME - elapsed
如果 BEGIN_TIME <= 0：left = duration
```

`datas_map` 每项第一数是零基槽位索引，后面的数组才是 `chargeCds`。
数组中间的数在客户端计算中抵扣所需耗时，末项是本段可推进时间的上限，单位均为秒。
这些数不代表饲料数量，也不能从它们反推最初喂食历史。
奶牛当前例子 `[1800,10800]` 表示抵扣 1800 秒，本段最多推进 10800 秒。

- `outputId=0`：`empty`，空位。
- `left<=0`：`mature`，缓存公式判定可领取；`remainingSeconds=0`。
- 非成熟的需供料生产，供料记录缺失、计时未开始或本段时长已耗尽：按客户端规则输出 `needs_feed`。
- 其余有效运行槽位：`growing`。
- 四元组缺失、未知配置、损坏供料索引或未核对的新包规则：`unknown`，保留问题而不伪造空位或成熟时间。

`theoreticalEndAtUnix` 是客户端中间计算结果。只有本段供料足够完成且计时已开始时，才把它输出为 `matureAtUnix`；
否则 `matureAtUnix=null`，因为何时成熟取决于下一次实际喂食。
供料暂停时，经过一天也不会把尚未完成的生产判为成熟。
`feeding.needsFeed` 沿用客户端严格 `elapsed > limit` 边界；恰好等于上限的瞬间暂停标记可先于喂食提示出现。

## 当前样本与验证

此次当前缓存共 7 个区域、84 个槽位，无缺失或未判定条目：

| 区域 | 槽位 | 空位 | 成熟产品位 |
| --- | ---: | ---: | --- |
| 农田1 | 15 | 15 | 0 |
| 农田2 | 15 | 15 | 0 |
| 农田3 | 6 | 6 | 0 |
| 牧场1 | 12 | 1 | 牛奶 11 |
| 牧场2 | 9 | 0 | 牛奶 9 |
| 果园1 | 16 | 1 | 苹果 14、青苹果 1 |
| 果园2 | 11 | 0 | 苹果 11 |

苹果／青苹果的缓存公式成熟时间是北京时间 2026-10-18 13:24:47；
奶牛为 2026-10-18 03:56:21 或 03:59:35。此处数字是产品槽位数，不是收获数量。

把客户端上述三段纯函数原文放入离线 VM，只注入捕获的缓存、配置和固定时钟，
对比 84 槽的 outputId/canCollect 以及 46 个活动槽的结束时刻、剩余秒和供料判断，全部一致。
此验证没有运行游戏启动代码，也没有模拟领取、喂食或其他游戏操作。
结果在 `.tmp/production_decode/reference_validation.json`。
真实 Tk 窗口校验 84 行、区域数量、牛奶搜索 20 行、恢复 84 行和 production 默认选中，
结果在 `.tmp/production_decode/window_validation.json`。新增回归覆盖成熟边界、取整、供料暂停、缺失记录与重复供料索引。

缓存解码内部总计约 0.52 秒，其中逐槽位解析约 1.7 毫秒；这是正常读取的单次耗时，未做重复压力测试。

## 时钟和覆盖边界

客户端 GUtils 使用服务器同步后的时钟，可有冻结及调试偏移；此读取器使用本机 UTC 时间，
尚未读取该内存时钟。`clock.serverClockVerified=false` 在 JSON 和窗口明确显示。
`timingReliable=true` 表示输入齐全且公式已核对，不保证服务器实时状态或临界秒完全一致。
本次没有通过点击对比实际成熟、收获或喂食后的状态变化。
当前支持已拥有的 3 农田、2 牧场、2 果园；新增建筑 ID 或新版规则需另行核对。

静态证据来自客户端包 87，按惰性文本提取，无游戏代码执行：

- `src/bundle-scripts/game.js` SHA256 `ff9b55eae13a50f63d521c054660712c7f8a7f7394b861dfdff368521a20a77e`。
  `CROP_IDX` 约字符偏移 1634189；三段纯函数起始偏移分别为 2472527、2473191、2473390。
- `subpackages/subpkg/game.js` SHA256 `9112bfba987db11f294cfde16176b71bbc043aa33eba622a532894d469ceb90c`。
  字符串表 JSON 共 12156 项，按其数值校验公式确定轮转 470 位后进行静态文字替换；没有执行包启动逻辑。
  FarmManager 的来源和解码辅助文本保存在 `.tmp/production_decode/package87`、`modules`。
- REA 全包证据 `ev_54fee2d8d3870b3f80b468e6d9c65946880b19390ec73df1d0a523de67980cd2`
  的自动语义图受内存限制而不完整；局部提取证据 `ev_d68cdb33e39f7a7c4ac0f2fead2e25362404e243e59cdd14c4204116d33bd1d2`
  仍有动态调用未知项及一处辅助模块解析失败。以上结论依据明确的枚举与函数体人工追踪及纯函数离线对照，
  不宣称完成整个客户端的自动语义恢复。

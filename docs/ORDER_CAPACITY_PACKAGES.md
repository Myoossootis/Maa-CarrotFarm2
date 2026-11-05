# 聚会／外卖次数已核对的包版本

日期：2026-11-05。只读解码每次从当前客户端配置复核契约，不按版本字符串直接填余额。未列入的包版本保持 `orderCapacity.complete=false`、`entries=[]`、`reason=unverified_package_version`，不能当成 0 次。

`remaining` 是客户端 `BagCore.getItemNum` 的有效次数，不是只加 MMKV 里的 `item_num`。聚会／外卖横幅用 `getItemNumByItemId(ref_id)`，次数道具 103／104 的 `add_type` 含 `NUM_ADD`（实机为 164），因此会加上 `getCdItemNumVo(uid).iCdNum`。产出 501／502 为 `type:2`、`cd:3600`、`limit:6`、`params:"day_limit,6"`。`setItemNum` 把 `REFRESH_TIME` 和 `REPEAT_COUNT` 写进道具附加字段。

## 已核对

| 包版本 | 结论 | 核对内容 |
| --- | --- | --- |
| 90 | 放行 | 2026-10-25 静态核对 `SceneGuestOrderLogic.refreshOrderCount`、`SceneGuestOrderView.showPanelNeedFood`、`acceptOrder`，以及配置关联。见 [次数验证](ORDER_CAPACITY_2026-10-25.md)。 |
| 99 | 放行 | 与 90 使用同一条配置契约，在每次读取时核对，不是只比较版本号。本仓库没有客户端包，因此没有在此环境重新对照 99 的 `SceneGuestOrderLogic` 字节。 |
| 100 | 放行 | 与 90／99 使用同一条配置契约，在每次读取时核对。2026-11-05 实机 MMKV（Asia/Singapore）读到 `packageVersion` 100：订单 101／102 的 `ref_id` 为 103／104，`item_type` 24，产出类型 2，上限 6。本仓库没有客户端包，没有对照 100 的 `SceneGuestOrderLogic` 字节。 |

98 的生产计时已另行核对，聚会／外卖次数没有纳入，仍然拦截。未列入的其他版本同样不放行。客户端 100 也列入普通订单的包版本字符串白名单，避免自动化读取只因版本号失败关闭；100 的生产计时没有核对，生产模块仍把该版本标成未核对。

## 每次读取要满足的契约

两类订单分开核对，任一失败即抛出配置错误，不写 `remaining: 0`：

- 聚会订单物品 **101**、外卖订单物品 **102**。
- `params.ref_id` 必须分别指向次数道具 **103**、**104**。道具编号来自配置；对不上已核对身份就失败，不改用别的道具充数。
- 次数道具 `item_type` 为 **24**。
- 道具 `params.output_id` 指向 `cfg_output`，且 `item_id` 等于该道具、`type` 为 **2**、`limit` 为 **1–6** 的整数。
- `remaining` 从该道具未过期的库存行加起。产出上没有 `cd` 也没有 `day_limit` 时，只累加这些行的 `count`。两者都出现时按 CD 日限恢复；只出现一个，或不是正整数，则失败，不写成 0。
- 恢复时钟读附加字段 `refresh_time`、`REFRESH_TIME` 或 `r_time`。同一时刻的重复次数读 `repeat_count`、`REPEAT_COUNT` 或 `rp_c`，缺省为 0。`ticks = floor((now - refresh) / cd)`。刷新时间若落在今天（Asia/Shanghai）之前，重复次数不占用今天的 `day_limit`。`iCdNum = min(ticks, limit - count, day_limit - 当日已恢复)`，小于 0 的房间按 0。刷新时间缺失、≤0 或仍在未来时不加 tick。
- 某一行自己有正的刷新时间时，只使用各行自己的时钟。行都在、但都没有时钟时，若 `SAVE_KEY_COMMON` 里该道具编号下，或另一条明确写着该道具编号的记录里有正的刷新时间，则把这些行的 `count` 之和当作一叠，只补一次 tick。多套时钟不一致则失败。
- 多行相加后如果超过 `output.limit`，封顶到该上限。没有有效行、公共存储也没有正的刷新时间时，契约通过之后记为 **0**，`complete` 仍为真。不把 `remaining` 填成 `limit`。负数、非整数、溢出、重复次数为负，或当日重复次数大于 `day_limit`，则失败。

Node `orderCapacityOf` 与 Python `decoded_runtime.order_capacity` 都只接受 90、99 和 100。Python 还要求 `semanticsPackageVersion` 等于当前 `packageVersion`，条目身份仍是 103／104，并且 `remaining` 不超过该条 `limit`。时钟是本机 UTC，没有读取客户端服务器校时，临界秒可能和界面差一截。

## 2026-11-05 空背包与界面 6/6

当天 Asia/Singapore、包 100：聚会订单横幅是 **完成订单次数: 6/6**。白名单放行后契约通过，但 MMKV 没有道具 103／104 的行（`rawTok=[]`），快照里也没有上面的刷新时间或重复次数。`SAVE_KEY_COMMON['104']` 只是布尔值 `true`，没有 `common['103']`。布尔值不是刷新时钟。解码 `remaining` 仍为 0。这不是未核对版本，Node 也不会把 `remaining` 写成 `limit`。

客户端内存里的 BagCache 可以在 `setItemNum` 写回之前，把 `item_num + iCdNum` 显示成 6，即使这一行不在我们读到的 MMKV 里。0.13.6 起，正式看板在下面的空缓存条件下改信界面上已经读到的分子，不购买次数，也不让 `orderCapacityOf` 补 tick。

`probe_availability` 只在这些条件同时成立时，把有效次数当成界面分子 `n`（来源 `ui_counter`）：

- `order_capacity` 已通过：`complete` 为真，包版本在已核对名单里。未核对或 `complete` 不为真时仍然抛出解码错误，不改用界面次数。
- 该条 `remaining` 为 0。探测只拿到容量条目；条目上没有该道具的未过期行，也没有正的刷新时间，就视为空背包。
- 界面计数是整数 `[n, limit]`，`limit` 等于该条上限，且 `0 < n <= limit`。

这时按 `n` 决定可接或 `order_batch_unavailable`，不返回 `capacity_ui_cache_mismatch`。没有可读计数时不会把次数写成上限。

## 余额为 0 时的正式结果

契约通过、且没有库存行也没有恢复时钟时，快照类似。2026-11-05 客户端 100 的实机读取就是这一形状；`common['104']===true` 不改变这两个 0：

```json
{
  "complete": true,
  "semanticsPackageVersion": "100",
  "entries": [
    {"category": "party", "tokenItemId": 103, "remaining": 0, "limit": 6},
    {"category": "takeout", "tokenItemId": 104, "remaining": 0, "limit": 6}
  ]
}
```

正式流程会把该结果与界面分子分母比较。两边都是 0/6 时返回 `order_capacity_exhausted` 并等待。界面是正的 `n/6` 且上面的空缓存条件成立时，按 `n` 继续，不把这次当成未核对。缓存里已经有次数却和界面不一致、界面分母不对、或计数缺失／不是整数，则 `board_unknown` 并停止。不购买次数。90 与 99 使用同一条比较。有刷新时间时，缓存余额应与横幅上的有效次数一致，比较通过后才继续；探测看不到背包行上的时钟，只拒绝条目自身带了正刷新时间的情况。

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

当天 Asia/Singapore、包 100：聚会订单横幅是 **完成订单次数: 6/6**。白名单放行后契约通过，但 MMKV 没有道具 103／104 的行（`rawTok=[]`），快照里也没有上面的刷新时间或重复次数。解码 `remaining` 为 0。这不是未核对版本，也不是可以自行补成 6 的证据。

客户端内存里的 BagCache 可以在 `setItemNum` 写回之前，把 `item_num + iCdNum` 显示成 6，即使持久化的 `item_num` 为 0 或这一行根本不在我们读到的 MMKV 里。没有行、也没有恢复字段时，仍然报 0。正式流程把界面分子分母和这个余额比较，对不上就是 `capacity_ui_cache_mismatch` 并停止。不购买次数，不退回 OCR 填余额。

## 余额为 0 时仍然不会交单

契约通过、且没有库存行也没有恢复时钟时，快照类似。2026-11-05 客户端 100 的实机读取就是这一形状；当时界面是 6/6，所以正式流程应停止而不是当成次数用尽：

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

正式流程会把该结果与界面分子分母比较。界面也是 0/6 时返回 `order_capacity_exhausted` 并等待；小于请求批次时返回 `order_batch_unavailable`；对不上（例如界面 6/6、缓存 0）则 `board_unknown` 并停止。不购买次数，不退回 OCR 填余额。90 与 99 在同一契约下，没有恢复证据而余额为 0 时同样等待。有刷新时间时，缓存余额应与横幅上的有效次数一致，比较通过后才继续。

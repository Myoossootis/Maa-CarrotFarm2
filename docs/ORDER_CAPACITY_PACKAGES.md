# 聚会／外卖次数已核对的包版本

日期：2026-11-04。只读解码每次从当前客户端配置复核契约，不按版本字符串直接填余额。未列入的包版本保持 `orderCapacity.complete=false`、`entries=[]`、`reason=unverified_package_version`，不能当成 0 次。

## 已核对

| 包版本 | 结论 | 核对内容 |
| --- | --- | --- |
| 90 | 放行 | 2026-10-25 静态核对 `SceneGuestOrderLogic.refreshOrderCount`、`SceneGuestOrderView.showPanelNeedFood`、`acceptOrder`，以及配置关联。见 [次数验证](ORDER_CAPACITY_2026-10-25.md)。 |
| 99 | 放行 | 与 90 使用同一条配置契约，在每次读取时核对，不是只比较版本号。本仓库没有客户端包，因此没有在此环境重新对照 99 的 `SceneGuestOrderLogic` 字节。 |

98 的生产计时已另行核对，聚会／外卖次数没有纳入。100 及其他版本同样不放行。

## 每次读取要满足的契约

两类订单分开核对，任一失败即抛出配置错误，不写 `remaining: 0`：

- 聚会订单物品 **101**、外卖订单物品 **102**。
- `params.ref_id` 必须分别指向次数道具 **103**、**104**。道具编号来自配置；对不上已核对身份就失败，不改用别的道具充数。
- 次数道具 `item_type` 为 **24**。
- 道具 `params.output_id` 指向 `cfg_output`，且 `item_id` 等于该道具、`type` 为 **2**、`limit` 为 **1–6** 的整数。
- `remaining` 只累加该道具未过期的库存记录。没有有效记录时，在契约通过之后记为 **0**，`complete` 仍为真。负数、非整数或溢出则失败。

Node `orderCapacityOf` 与 Python `decoded_runtime.order_capacity` 都只接受 90 和 99。Python 还要求 `semanticsPackageVersion` 等于当前 `packageVersion`，并且条目身份仍是 103／104。

## 余额为 0 时仍然不会交单

契约通过且库存没有 103／104 时，快照类似：

```json
{
  "complete": true,
  "semanticsPackageVersion": "99",
  "entries": [
    {"category": "party", "tokenItemId": 103, "remaining": 0, "limit": 6},
    {"category": "takeout", "tokenItemId": 104, "remaining": 0, "limit": 6}
  ]
}
```

正式流程会把该结果与界面分子分母比较。界面也是 0/6 时返回 `order_capacity_exhausted` 并等待；小于请求批次时返回 `order_batch_unavailable`；对不上则 `board_unknown` 并停止。不购买次数，不退回 OCR 填余额。

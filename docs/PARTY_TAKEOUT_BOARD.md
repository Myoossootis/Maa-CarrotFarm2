# 聚会 / 外卖订单板（v0.6.3）

与普通订单共用告示牌，顶部三页签：`聚会订单 | 外卖订单 | 普通订单`。

## 行为

1. 打开告示牌（餐厅入口 / 告示牌模板，与普通单相同）
2. 点击对应页签（模板：`party_order_tab` / `takeout_order_tab`）
3. 确认「完成6次」勾选（复用普通单勾选模板）
4. 识别当前客人需求（背包作物模板 + OCR 数量）
5. 有缺口 → 短循环排产 / 监控领取
6. 点击「接受」（复用 `normal_order_accept`；点击即成功）
7. 配额一次记 **6** 次；满 6 歇 1 小时，日上限 12

## 脚本

- `tools/complete_batch_orders.py --order-type party|takeout [--prepare-only|--target-file]`
- `tools/run_short_cycle.py --order-type party|takeout --apply`
- Maa 勾选「聚会订单」「外卖订单」

## 日志

- `[订单] 类型=聚会订单/外卖订单 …`
- `[员工安排] …`
- `[交单] … 批量=6`

## 回测（Maa）

1. 彻底退出 MFAAvalonia，再开 `启动Maa界面.cmd`（版本 **0.6.2**）
2. 只勾 **聚会订单** 或 **外卖订单**（可先关普通）
3. 游戏开着、告示牌可打开、微信别盖前台
4. 点开始：日志应出现页签切换、[订单] / [交单]；pipeline 焦点与日志都不应再出现「尚未接入」。

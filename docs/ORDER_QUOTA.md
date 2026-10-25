# Takeout / party order quota

Updated: 2026-10-15 Asia/Shanghai.

## Rules

| Type | Daily cap | Batch | Cooldown |
| --- | --- | --- | --- |
| Takeout (`takeout` / 外卖) | 12 | 6 | After 6 completions: **1 hour**, then up to 6 more |
| Party (`party` / 聚会) | 12 | same | same |

Date and cooldown use **Asia/Shanghai** calendar day. Counts reset at midnight.

Normal orders (short-cycle default) are **not** gated by this quota.

## State file

`.tmp/order_quota/state.json` (gitignored). Cleaning `.tmp` resets today's progress.

## CLI

```bat
cd /d E:\Maa-CarrotFarm2
.venv\Scripts\python.exe tools\order_quota_cli.py status
.venv\Scripts\python.exe tools\order_quota_cli.py record --type takeout
.venv\Scripts\python.exe tools\order_quota_cli.py record --type party
.venv\Scripts\python.exe tools\order_quota_cli.py reset --type party
```

## Short cycle

```bat
.venv\Scripts\python.exe tools\run_short_cycle.py --rounds 1
.venv\Scripts\python.exe tools\run_short_cycle.py --apply --order-type takeout --rounds 1
```

- dry-run summary includes takeout/party remaining + `next_available`
- `--order-type normal` (default): no quota check / no record
- `--order-type takeout|party`: if in cooldown or day-full, **skip** and print `next_available` (no busy-wait); on accepted submit, record +1

Philosophy: click = success; quota is a count gate only.

联合调度：`tools/run_daily_orders.py --order-types party takeout --apply`。先处理可用类型，两类都冷却时等待最早的 next_available。单独类型的日常入口遇冷却会让出队列。


## 普通订单

普通订单**没有每日上限**，不参与配额记录。当前 Maa 普通订单入口每次处理一单，长期循环待后续开发。


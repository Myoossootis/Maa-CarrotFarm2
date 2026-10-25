# Track C 状态：能力表补全 + 提交分组准备

> 更新：2026-10-15（UTC+8）。**未 commit / 未 push。**

## 目标

- 摸清 `employee_capabilities.json` 缺口（`worker_level` / pending `min_level`）
- 提供可离线/引导式录入 CLI（不跑长时实机巡游）
- 按脏树刷新 `docs/COMMIT_GROUPS.md`，供用户批准后入库

## 缺口清单（schema v2，observed_on=2026-09-26）

### 全部槽位 `worker_level=null`（7）

| slot_id | 标签 | 员工 | worker_level_status |
|---------|------|------|---------------------|
| field_1 | 农田1 | 兔耳员工 | needs_live_recording |
| field_2 | 农田2 | 卷发员工 | needs_live_recording |
| field_3 | 农田3 | 长颈鹿员工 | needs_live_recording |
| ranch_1 | 牧场1 | 黑白猫员工 | needs_live_recording |
| ranch_2 | 牧场2 | 鹿角员工 | needs_live_recording |
| orchard_1 | 果园1 | 白色员工 | needs_live_recording |
| orchard_2 | 果园2 | 粉色员工 | needs_live_recording |

### 槽内未完成项

- **field_1** `unverified_visible`：普通草莓、卷心菜（需 live 录等级后 promote）
- **ranch_1** `locked`：羊肉、牛奶（需解锁等级）
- **orchard_2** `locked`：黄桃、柠檬、椰子

已在 `product_gates` 且 `eligibility=observed_unlocked`、`min_level=null` 的作物：**可继续排产**（schema 规则允许无 worker_level 的 observed_unlocked）；数值门槛仍待补录。

### `pending_capabilities` 需 min_level（不含鱼肉）

| 名称 | route_hint | eligibility |
|------|------------|-------------|
| 红皮土豆 | field | needs_live_recording |
| 白玉草莓 | field | needs_live_recording |
| 山鸡肉 | livestock/ranch | needs_live_recording |
| 青苹果 | orchard | needs_live_recording |
| 红薯 | field | needs_live_recording |
| 鱼肉 | — | **unsupported**（永不排产） |

证据快照：`.tmp/track_c/gaps_summary.json`（跑 `--gaps` 会刷新）。

## 录入 CLI

路径：`tools/record_worker_levels.py`

- 默认 **dry-run**；加 `--write` 才改 `assets/catalog/employee_capabilities.json`
- 证据与备份只写 `.tmp/track_c/`
- 无长时实机循环；可选 `--from-image` 对已存截图 OCR

常用：

```bat
cd /d E:\Maa-CarrotFarm2
.venv\Scripts\python.exe tools\record_worker_levels.py --gaps

:: 人工读到等级后（先 dry-run 看 diff）
.venv\Scripts\python.exe tools\record_worker_levels.py --set-level field_1=12
.venv\Scripts\python.exe tools\record_worker_levels.py --set-level field_1=12 --write

:: 截图 OCR（需先手动打开员工卡并截图）
.venv\Scripts\python.exe tools\capture_current_frame_with_maa.py --output .tmp\track_c\field_1.png
.venv\Scripts\python.exe tools\record_worker_levels.py --from-image .tmp\track_c\field_1.png --slot field_1
.venv\Scripts\python.exe tools\record_worker_levels.py --from-image .tmp\track_c\field_1.png --slot field_1 --write

:: 提升未验证 / 解锁 / pending
.venv\Scripts\python.exe tools\record_worker_levels.py --promote-unverified field_1:普通草莓=6 --write
.venv\Scripts\python.exe tools\record_worker_levels.py --unlock-locked ranch_1:羊肉=20 --write
.venv\Scripts\python.exe tools\record_worker_levels.py --promote-pending 红薯 --slot field_2 --min-level 5 --write
```

离线测试：`tests/test_record_worker_levels.py` + `tests/test_employee_capabilities.py`。

## 提交分组

见 [COMMIT_GROUPS.md](./COMMIT_GROUPS.md)（已按当前脏树刷新，10 组 + 明确不暂存列表）。**本组任务不执行 commit。**

## 建议用户下一步（实机短录，非自动化巡游）

1. 游戏开着、勿最小化；微信勿盖在最前。
2. 手机 → 员工配置 → 农场帽签 → 员工列表。
3. 对每个槽：看清卡片/页眉 **等级数字**，记 `slot_id=等级`。
4. 有疑问的灰锁定作物：点开工作计划，确认是否灰；截图到 `.tmp/track_c/`。
5. 用上面 CLI `--set-level` / `--promote-*` / `--unlock-locked` 录入；先 dry-run 再 `--write`。
6. 每录完一轮跑：
   `python -m unittest tests.test_employee_capabilities tests.test_record_worker_levels`
7. 能力表满意后，按 COMMIT_GROUPS 组 7（及依赖组）让用户批准再 commit。

## 与主线关系

- Track A/B 不依赖数值 `min_level` 即可用 `observed_unlocked` 排已有作物。
- 本 Track 补齐等级后，pending 作物与 locked 才能进入正式排产。

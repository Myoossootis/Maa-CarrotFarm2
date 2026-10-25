# Track B status — formal short cycle

Updated: 2026-10-15 23:15 Asia/Shanghai. Evidence root: E:\Maa-CarrotFarm2\.tmp\track_b\.

## Ready (offline / code)

| Item | Status |
| --- | --- |
| Orchestration 	ools/run_short_cycle.py scan→plan→monitor→submit | Ready |
| Dry-run default (no --apply); writes dry_run_plan.json | Ready |
| --apply semantics + UI ttach.ui_apply mapping | Ready |
| Evidence under .tmp/track_b/ (+ legacy mirror .tmp/short_cycle/) | Ready |
| Fish / unsupported whole-order skip (UNSUPPORTED_ORDER_PRODUCTS) | Ready |
| One-shot plans only (
epeat=false, one_shot=true) | Ready |
| Collect only after two consecutive left≥right readings | Ready |
| One-shot clearance: wait for 2× empty-plan reads (not first work_plan frame) | Ready |
| Close-menu clicks soft-continue on WeChat→game FG raise | Ready |
| Sleep/close-shop after accept counted as accepted | Ready |
| FG-safe clicks (post_click_normalized); native Click blocked in UI worker | Ready |
| Maa UI task **单订单补产与交单** (default_check=true, 正式执行 case) | Ready |
| Offline unit tests (collection delayed/soft clearance, monitor, short cycle) | Ready |

## Maa UI — default check combo

Task entry: RunShortCycle · name: **单订单补产与交单** (no longer「实验」).

| Control | Recommended | Default in interface.json |
| --- | --- | --- |
| Task checkbox | **勾选** | default_check: true |
| 周期运行模式 | **正式执行** (live) / 仅预览 (safe peek) | 仅预览 (accidental-start safety) |
| 周期参数 · rounds | 1 | 1 |
| 周期参数 · poll | 20 | 20 |
| 周期参数 · timeout | 900 | 900 |

Live from Maa:勾选任务 → 周期运行模式选 **正式执行** → 开始。CLI 等价：

`at
cd /d E:\Maa-CarrotFarm2
.venv\Scripts\python.exe tools\run_short_cycle.py --apply --rounds 1 --poll-seconds 20 --timeout-seconds 900
`

## Live 2026-10-15 23:13–23:14（Asia/Shanghai）— PASS

Evidence: .tmp/track_b/live/apply_round1_retry2/

| Step | Result | Notes |
| --- | --- | --- |
| dry-run --rounds 1 | PASS | pply_round1_retry2/dry_run/ |
| --apply --rounds 1 | **PASS** | status=complete, ccepted=true, submitted_count=1 |
| plan/monitor | skipped | inventory already covered order#1 (番茄/玉米/鸡蛋 all satisfied from prior harvest) |
| submit | PASS | cceptance_confirmed=true |

## Clearance fix (root cause)

**Root cause:** After collect, collect_finished_slot returned on the **first** frame where the blue 工作计划 button reappeared. That frame often paints before 「暂无工作计划」, so 
o_work_plan.png scored low → plan_cleared=false → false CollectionUnconfirmedError even though the one-shot plan did clear (live probe later: field_2 
o_work_plan=0.977).

**Fix (	ools/configure_field_employee.py):**

1. plan_cleared_on_list() — positive empty-plan evidence; soft-accept at ≥0.60 when work_plan is back and collect is gone.
2. Post-collect poll up to ~6.4s; require **two consecutive** cleared reads before plan_cleared=true.
3. Tests: 	est_delayed_empty_plan_still_confirms_clearance, 	est_soft_no_work_plan_counts_as_cleared.

## Also landed this session

- close_employee_menu navigation clicks use 	olerate_fg_change (WeChat FG + CEF PostMessage can still raise 萝卜家园2; aborting mid-close blocked scan).
- Submit: sleep/close-shop confirmed after accept → count as accepted (cceptance_confirmed=sleep_prompt) instead of cceptance_pending exit 2.

## Gaps

| Item | Status |
| --- | --- |
| Live plant→monitor→collect exercising new clearance on a shortage order | Not in this PASS (inventory already enough); offline tests cover it |
| Formal 2-round --apply unattended | Not done |
| Capability table worker_level / pending crops | Track C |

## Preconditions

1. Game open, **not** minimized/shrunk.
2. Farm map chrome preferred (phone / backpack).
3. Manually clear leftover crops/plans/supplies when starting a shortage run.
4. Prefer not covering the game with WeChat (FG soft-continue on close only; collect path still stops after one click if FG flips).

## Evidence layout

`
.tmp/track_b/live/apply_round1_retry2/
  dry_run/
  apply/
    stdout_retry.txt
    latest_summary.json
    round_01/
`

## 验收策略：点击即成功，少打分（2026-10-15）

机械步（领取 / 清计划 / 关工作计划页 / 勾选「完成后重复」切换）**默认信任点击**：
- 识图只用于**找到**按钮或槽位，不做连帧分数门禁。
- 点过领取即 `collected=True` 且 `plan_cleared=True`，不再要求「暂无工作计划」模板连帧通过。
- 关计划弹层点一次即返回，不再连帧确认员工列表。
- 勾选切换点一次即保存，不再二次打分。

硬停只留给真阻塞：错页、找不到列表/领取钮、不支持作物、点击路径本身抛错（如前台守卫）。
不要用 soft 分数兜底替代已删硬门禁。

## Order quota (takeout / party)

See [ORDER_QUOTA.md](./ORDER_QUOTA.md). Daily 12 each; after 6 completions cool 1 hour then 6 more. Short cycle: --order-type takeout|party gates with skip + 
ext_available (default 
ormal ignores quota). Dry-run embeds order_quota in .tmp/track_b/dry_run_plan.json. CLI: 	ools/order_quota_cli.py status.

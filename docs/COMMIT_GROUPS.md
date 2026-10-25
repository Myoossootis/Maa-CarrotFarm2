# Suggested commit groups (do not auto-commit)

> 2026-10-25：用户已明确授权保留 Git 历史、整理并上传当前项目。以下“不要自动提交／待批准”为历史约束，不适用于本次授权；后续流程以根目录 CONTRIBUTING.md 为准。

> 刷新：2026-10-15 Track C。对照当前 `git status` 脏树；**不要**自动 `git add` / `git commit` / `push`。
> 勿纳入：`.tmp/`、`debug/`、`latest_*.json` 运行态、`.venv/`、`maafw.log`、生成态 backpack `catalog.json` / `pages/` / `segments/` / `index.png`。

## 验收清单（用户点头后再逐组执行）

| # | 主题 | 建议提交说明 | 状态 |
|---|------|--------------|------|
| 1 | Docs & 启动入口 | docs + README + gitignore + 启动脚本 | 待用户批准 |
| 2 | Pipeline / interface / CI | carrot_farm + interface + workflows | 待用户批准 |
| 3 | Agent 桥接 | agent/* 生产桥 | 待用户批准 |
| 4 | 核心编排 tools | 短循环 / 排产 / 交单 / 能力表 helper | 待用户批准 |
| 5 | 导航原子 CLI | 进出餐厅/农场/手机/员工页/工作计划 | 待用户批准 |
| 6 | 前台安全输入 | maa_frame_normalize + probe + FOREGROUND doc | 待用户批准 |
| 7 | Capabilities + registry | employee_capabilities.json + registry + recorder | 待用户批准 |
| 8 | Templates / images | navigation / production / crops | 待用户批准 |
| 9 | Tests | tests/** | 待用户批准 |
| 10 | 捕获/裁剪辅助（可选） | capture_* / crop_* 审计工具 | 可延后 |

---

## 1) Docs & 产品入口

- `README.md`
- `.gitignore`
- `docs/DEV_PLAN_REMAINING.md`
- `docs/COMMIT_GROUPS.md`
- `docs/TRACK_C_STATUS.md`
- `docs/PROGRESS_2026-10-15.md`
- `docs/FOREGROUND_SAFE_INPUT.md`
- `docs/MAA_INTEGRATION_PLAN.md`
- `docs/MAA_UI.md`
- `docs/MAA_UI_LIVE_ACCEPTANCE_2026-10-15.md`
- `docs/SINGLE_ORDER_LIVE_ACCEPTANCE_2026-10-15.md`
- `docs/EMPLOYEE_PAGE_STATE_FIX_2026-10-15.md`
- `docs/ENTER_FARM_STEP_2026-10-15.md`
- `docs/ENTER_RESTAURANT_STEP_2026-10-15.md`
- `docs/OFFLINE_INCOME_STEP_2026-10-15.md`
- `docs/OPEN_EMPLOYEE_CONFIG_STEP_2026-10-15.md`
- `docs/OPEN_PHONE_STEP_2026-10-15.md`
- `docs/SELECT_FARM_EMPLOYMENT_STEP_2026-10-15.md`
- `启动Maa界面.cmd`
- `启动Maa界面.ps1`
- `tools/start_maa_ui.ps1`
- `tools/prepare_maa_ui.py`
- `tools/cleanup_development_cache.ps1`

## 2) Pipeline / interface / CI

- `assets/interface.json`
- `assets/resource/pipeline/carrot_farm.json`（由 my_task.json 更名；已删 MyTask*）
- 删除已跟踪的 `assets/resource/pipeline/my_task.json`（`git rm` 若仍显示 D）
- `.github/workflows/install.yml`
- `.github/workflows/mirrorchyan_release.yml`
- `.github/workflows/mirrorchyan_release_note.yml`
- `tools/install.py`
- `tools/requirements.txt`

## 3) Agent 桥接

- `agent/main.py`
- `agent/my_reco.py`
- `agent/cycle_bridge.py`
- `agent/production_actions.py`
- `agent/production_start.py`

## 4) 核心编排 tools（Day1–3）

- `tools/configure_field_employee.py`
- `tools/monitor_employee_products.py`
- `tools/complete_normal_orders.py`
- `tools/order_board_helpers.py`
- `tools/order_template_click.py`
- `tools/run_short_cycle.py`
- `tools/run_order_cycle.py`
- `tools/run_employee_order_plan.py`
- `tools/allocate_employee_work.py`
- `tools/employee_capabilities.py`
- `tools/production_targets.py`
- `tools/ensure_ranch_feed.py`
- `tools/runtime_state.py`
- `tools/task_control.py`
- `tools/ui_worker.py`
- `tools/scan_normal_orders.py`
- `tools/scan_backpack_inventory.py`
- `tools/build_backpack_crop_catalog.py`
- `tools/audit_backpack_crop_catalog.py`
- `tools/audit_fast_inventory_scan.py`

## 5) 导航原子 CLI

- `tools/enter_farm.py`
- `tools/enter_restaurant.py`
- `tools/open_phone.py`
- `tools/open_employee_config.py`
- `tools/select_farm_employment.py`
- `tools/dismiss_offline_income.py`
- `tools/open_field_detail.py`
- `tools/return_employee_list.py`
- `tools/locate_field_2.py`
- `tools/open_work_plan.py`
- `tools/close_work_plan.py`
- `tools/verify_slot_work_plan.py`
- `tools/verify_navigation_with_maa.py`
- `tools/verify_backpack_with_maa.py`
- `tools/verify_notice_board_with_maa.py`
- `tools/verify_maa_ui_bridge.py`
- `tools/resize_game_window.py`

## 6) 前台安全输入

- `tools/maa_frame_normalize.py`（`post_click_normalized` / 禁原生 activate）
- `tools/probe_foreground_safe_click.py`
- `docs/FOREGROUND_SAFE_INPUT.md`（若未进组 1，则放这里）

## 7) Capabilities + registry + recorder

- `assets/catalog/employee_capabilities.json`
- `assets/catalog/backpack_crops/registry.json`
- `tools/record_worker_levels.py`
- `tools/employee_capabilities.py`（若未进组 4）
- `docs/TRACK_C_STATUS.md`（若未进组 1）

## 8) Templates / images

- `assets/resource/image/navigation/**`
- `assets/resource/image/production/**`
- `assets/resource/image/crops/**`

## 9) Tests

- `tests/**`（含 `test_employee_capabilities.py`、`test_record_worker_levels.py` 与 fixtures）

## 10) 捕获 / 裁剪辅助（可延后单独提交）

- `tools/capture_*.py`
- `tools/crop_*.py`
- `tools/capture_current_frame_with_maa.py`

## 明确保持未暂存

- `.tmp/**`（含 `.tmp/track_c/` 证据）
- `debug/**`
- `assets/catalog/**/latest_*.json`
- `assets/catalog/backpack_crops/catalog.json`
- `assets/catalog/backpack_crops/pages/`
- `assets/catalog/backpack_crops/segments/`
- `assets/catalog/backpack_crops/index.png`
- `.venv/**`
- `maafw.log`
- `tests/**/__pycache__/**`

## 建议提交顺序

1 → 2 → 9（先有文档/接口/测试基线）→ 8（模板）→ 7（能力表）→ 4+5+6（tools）→ 3（agent）→ 10（可选）。
每组单独 `git add` 指定路径后 `git commit`；**禁止** `git add .`。

# Track A 状态（导航原子任务）

日期：2026-10-15（Asia/Shanghai）  
范围：回列表 / 定位田2 / 开·关工作计划 / VerifyField2·3·Ranch·Orchard  
约束：未 commit；证据目录 `.tmp/track_a/`；未跑长时实机挂起。

## 结论一句话

**10 个导航原子已在 interface + pipeline + CLI 齐备；点击路径仅走 FG-safe；离线单测 35/35 通过。** 开/关工作计划与多槽 Verify* 的实机验收留给用户按下方 one-liner 跑（各条应 <30s）。

## 盘点（inventory）

| interface entry | CLI | 备注 |
| --- | --- | --- |
| `ReturnEmployeeListBackground` | `tools/return_employee_list.py` | 详情/空计划 → 列表；已在列表则 0 点击 |
| `LocateField2Background` | `tools/locate_field_2.py` | 只定位不打开 |
| `OpenWorkPlanBackground` | `tools/open_work_plan.py [--slot]` | 默认 `field_1`；需列表上蓝「工作计划」 |
| `CloseWorkPlanBackground` | `tools/close_work_plan.py` | 空白区关对话框 → 回列表 |
| `VerifyField2WorkPlanBackground` | `verify_slot_work_plan.py --slot field_2` | 开→关→确认列表 |
| `VerifyField3WorkPlanBackground` | `--slot field_3` | 同上 |
| `VerifyRanch1WorkPlanBackground` | `--slot ranch_1` | 同上 |
| `VerifyRanch2WorkPlanBackground` | `--slot ranch_2` | 同上 |
| `VerifyOrchard1WorkPlanBackground` | `--slot orchard_1` | 同上 |
| `VerifyOrchard2WorkPlanBackground` | `--slot orchard_2` | 同上 |

流水线：`assets/resource/pipeline/carrot_farm.json`（Verify* 带 `custom_action_param.slot_id`）。  
Agent：`agent/production_actions.py` → `ui_worker.py`（会装 FG-safe）。

完整 JSON：`.tmp/track_a/inventory.json`

## FG-safe

- 业务点击：`configure_field_employee.click` → `post_click_normalized`（只投渲染子窗鼠标消息）。
- 独立 CLI：`atom_cli_common.connect_controller()` 启动时调用 `install_foreground_safe_inputs()`（即使不经 ui_worker）。
- 扫描：生产原子路径无残留 `controller.post_click` / `SetForegroundWindow` 发点。
- 离线：`tests/test_background_click_target.py` 全绿。

## CLI / 证据

新增 `tools/atom_cli_common.py`：

- `--out <dir>`：证据目录（默认 `.tmp/track_a/<task>/`）
- 写入 `{timestamp}_{task}.json` + `latest.json`（含 `fg_safe_inputs: true`）
- `open_work_plan.py` 增加 `--slot`（默认 field_1）

## 离线测试

```
python -m unittest tests.test_atom_cli_common tests.test_work_plan_steps \
  tests.test_locate_field_2 tests.test_verify_slot_work_plan \
  tests.test_employee_list_steps tests.test_background_click_target \
  tests.test_employee_page_state
```

结果：**Ran 35 tests … OK**

## 实机 one-liner（请用户跑；勿最小化游戏；别把微信盖最前）

前置：已进入员工配置，且当前为**员工列表**（开/关与 Verify 用）。

```bat
cd /d E:\Maa-CarrotFarm2

REM 1) 开农田1工作计划（需蓝按钮可见）
.venv\Scripts\python.exe tools\open_work_plan.py --slot field_1 --out .tmp\track_a\open_work_plan

REM 2) 关工作计划（对话框已开）
.venv\Scripts\python.exe tools\close_work_plan.py --out .tmp\track_a\close_work_plan

REM 3) 多槽 Verify*（每次开→关，确认回列表）
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot field_2 --out .tmp\track_a\verify_field_2
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot field_3 --out .tmp\track_a\verify_field_3
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot ranch_1 --out .tmp\track_a\verify_ranch_1
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot ranch_2 --out .tmp\track_a\verify_ranch_2
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot orchard_1 --out .tmp\track_a\verify_orchard_1
.venv\Scripts\python.exe tools\verify_slot_work_plan.py --slot orchard_2 --out .tmp\track_a\verify_orchard_2

REM 可选：仅定位田2 / 从详情回列表
.venv\Scripts\python.exe tools\locate_field_2.py --out .tmp\track_a\locate_field_2
.venv\Scripts\python.exe tools\return_employee_list.py --out .tmp\track_a\return_employee_list
```

成功时 stdout JSON 含 `"status":"opened|closed|verified|located|returned"`，证据见对应 `--out` 下 `latest.json`。

也可用 Maa UI 勾选同名任务（走 Agent → ui_worker）。

## 缺口 / 阻塞

1. **实机未在本轮代跑**：需用户确认列表页状态与蓝按钮（有「收取物资」时 Verify/open 会拒点——符合安全策略）。
2. **槽位需滚动时**：`find_slot_action` 会后台滚列表；若帽签/列表态不对会失败——属前置导航（员工页状态），非 Track A 缺口。
3. **能力表 `worker_level` 仍空**：不挡开/关/Verify，挡 Track C 排产。
4. **未 commit**（按要求）。

## 本轮改动文件

- `tools/atom_cli_common.py`（新）
- `tools/return_employee_list.py`
- `tools/locate_field_2.py`
- `tools/open_work_plan.py`（+`--slot`）
- `tools/close_work_plan.py`
- `tools/verify_slot_work_plan.py`
- `tests/test_atom_cli_common.py`（新）
- `tests/test_work_plan_steps.py`（期望结果含 `slot`）
- `docs/TRACK_A_STATUS.md`（本文件）
- `.tmp/track_a/inventory.json`

## 实机验收 2026-10-15 22:45（Asia/Shanghai）

**结果：核心原子 + field_3/ranch_1 全部 PASS；全程 FG 未抢占。**

前置：游戏未最小化；初始页非员工列表（unknown），先跑 select_farm_employment 切橙帽后进入 employee_list。

| 步骤 | 结果 | 证据 |
| --- | --- | --- |
| open_work_plan field_1 | PASS | .tmp/track_a/live/open_work_plan/ |
| close_work_plan | PASS | .tmp/track_a/live/close_work_plan/ |
| verify field_2 / field_3 / ranch_1 | PASS | .tmp/track_a/live/verify_* |

FG HWND 597410（Grok Bot）开/关/验证前后不变。


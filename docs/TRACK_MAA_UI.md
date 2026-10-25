# Track Maa UI 状态（产品化接入）

日期：2026-10-15（Asia/Shanghai）
范围：assets/interface.json + pipeline 短循环节点 + docs/MAA_UI.md
约束：未 commit；未跑长时实机 apply（由并行 Track B 负责）。

## 结论一句话

**v0.5.0 已把「预览 / 正式」拆成可勾选任务，导航原子与日常预设写进 MAA_UI；订单/背包仍走 CustomAction，不恢复原生 Click。**

## 用户在界面上怎么点

1. 启动Maa界面.cmd → **重新打开**（吃到 v0.5.0）。
2. 日常：勾选 **Agent连接检查** +（可选）**关闭离线收益** + **正式：单订单补产交单** → 开始。
3. 只看计划：勾 **预览：单订单扫描与排产计划**。
4. 只验导航：勾对应原子（回列表 / 定位田2 / 开·关计划 / Verify*）。

## 改动文件

| 文件 | 变更 |
| --- | --- |
| assets/interface.json | version **0.5.0**；Agent 默认勾选；新增预览/正式独立任务；去「实验」文案；选项「正式执行」；周期参数覆盖三节点 |
| assets/resource/pipeline/carrot_farm.json | 新增 RunShortCyclePreview / RunShortCycleApply（attach.ui_apply 固化） |
| docs/MAA_UI.md | 30 秒用法 + 日常/导航预设 |
| tests/test_maa_ui_integration.py | Preview/Apply 与参数 override 断言 |
| docs/TRACK_MAA_UI.md | 本文件 |

未改 Agent 注册名：三节点 custom_action 仍为 RunShortCycle。

## FG-safe / 订单背包接线

- interface **无** 背包/最大化/原生订单 Click 任务（有意下架）。
- 正式/预览短循环 → CustomAction RunShortCycle → ui_worker → CLI；点击 post_click_normalized。
- pipeline 历史 Click 节点保留作模板；worker 禁止对其 post_task。

## 离线验证

python -m unittest tests.test_maa_ui_integration tests.test_production_start → **27/27 OK**。

## 缺口

- 正式整链实机仍跟 Track B（monitor 清计划等）。
- 需用户重新打开 MFAAvalonia 目视确认任务名。
- 能力表等级（Track C）不挡勾选，但影响正式排产成功率。

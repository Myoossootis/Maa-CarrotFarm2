# 萝卜家园2 自动化（Maa-CarrotFarm2）

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的 **Win32 后台**自动化：FramePool 截图 + PostMessage 点击，不抢前台鼠标。

开发版本 **0.13.0**：普通订单3倍持续循环已接入Maa任务，缺料支持员工分批补产与恢复；普通订单／生产路径允许客户端90、98，聚会／外卖次数语义仍仅核对90。「萝卜家园自动化」窗口查看监控、订单、背包与岗位，并可按模板编辑和保存全部Maa任务选项；13个模板可以直接开始、暂停、继续和结束，无需打开旧Maa面板。当前UI和实机验收边界见[独立界面报告](docs/AUTOMATION_UI_2026-11-03.md)，开发约定见[CONTRIBUTING.md](CONTRIBUTING.md)，交付写入[版本记录](CHANGELOG.md)。

直接双击根目录 `启动萝卜家园自动化.cmd`。旧 `启动Maa界面.cmd` 也已转入新主界面；默认普通3倍模板，打开时不自动运行任务。

> **方案决定（2026-10-25）**：Cocos 内部调用、Frida 注入及 WMPF 调试桥实验已封存，**不纳入使用脚本方案**。现有脚本维持原状；详见[封存说明](docs/ARCHIVED_COCOS_RESEARCH_2026-10-25.md)。

> **聚会单已实机完成提交与领奖**（2026-10-16）：默认入口「聚会与外卖订单（自动调度）」。优先现有库存，必要时以单次交单，按界面实际次数记账；中断后可续领奖。普通订单没有每日限额。验收范围及日志见 [修复记录](docs/REPAIRS_2026-10-16.md)。

## 窗口硬要求

1. 游戏 **萝卜家园2** 已打开；窗口类名 Chrome_WidgetWin_0
2. **禁止最小化 / 缩小窗口** —— 缩小后分辨率会掉，坐标与 OCR 全部失真
3. 控制器约定：display_short_side=720，模板坐标按约 720x1358（顶边 letterbox 由 maa_frame_normalize 处理）
4. 临时文件 **只写** 仓库下 .tmp/（例如 E:/Maa-CarrotFarm2/.tmp/），禁止往桌面 / 用户目录丢 dump
5. **后台点击时，微信主窗口不能处于前台**：实机发现此时即便向渲染子窗口发消息也可能激活游戏。现已在点击前检测并停止，请先切到其他应用；程序不主动切换焦点。这是当前限制，不代表完整循环已通过不抢前台验收。

## 环境

```
cd /d E:\Maa-CarrotFarm2
python -m venv .venv
.venv\Scripts\pip.exe install -r tools\requirements.txt
```

依赖本机已按 Maa 惯例装好的 Framework / 资源（见 deps/ 与上游文档）。日常业务脚本在 .venv 里跑即可。

## 历史短循环 CLI（Day2）

当前普通订单优先使用Maa任务「普通订单3倍持续循环」；已有生产先对账恢复，不按旧清地提示覆盖当前计划。以下保留历史CLI用法。

扫单/交单 → 员工排产 → 监控收取 → 按缺口作物重匹配交单。

```
cd /d E:\Maa-CarrotFarm2

REM dry-run
.venv\Scripts\python.exe tools\run_short_cycle.py --rounds 2

REM live (do NOT shrink/minimize the window)
.venv\Scripts\python.exe tools\run_short_cycle.py --rounds 2 --poll-seconds 20 --timeout-seconds 900 --apply
```

每轮 checkpoint：.tmp/short_cycle/round_XX/ 与 .tmp/short_cycle/latest_summary.json。

正式执行（`--apply`）前会弹出一次清地确认：请手动清空土地残留作物、处理员工旧计划及待领物资，再点确定。默认按钮是取消；取消后不操作游戏。一次启动包含多轮时只确认一次，重新启动必须重新确认；`--skip-checklist`不能跳过此确认。预览不弹窗。

员工计划默认取消“工作完成后继续使用当前计划”。领取必须连续两帧读到“分母等于本次目标，左数≥目标”；有领取按钮或完成横幅并不够。目标27时不会把容量50/50当作完成。查看计划不再自动收取；已领取后只识别“暂无工作计划”，不主动重置计划。领取结果不明时停止，不自动补点。

有牧场目标时，run_employee_order_plan 内会跑 ensure_ranch_feed 预检。

## 分步 CLI（Day1-3）

| 步骤 | 命令 |
| --- | --- |
| 扫单/交可交付订单并选出缺口 | .venv\Scripts\python.exe tools\complete_normal_orders.py |
| 预览/保存员工计划（默认单次） | .venv\Scripts\python.exe tools\run_employee_order_plan.py 或加 --apply |
| 监控并收取 | .venv\Scripts\python.exe tools\monitor_employee_products.py --assignment-file assets\catalog\employee_work\latest_assignment.json --apply |
| 按缺口作物重匹配交单 | .venv\Scripts\python.exe tools\complete_normal_orders.py --target-file <submit_target.json> |
| 单槽收取（例：计划27） | .venv\Scripts\python.exe tools\configure_field_employee.py --slot field_1 --collect --target 27 --apply |

员工能力表（等级门槛）：assets/catalog/employee_capabilities.json（schema v2）。未实机录等级的作物在 pending_capabilities，不要当成已可排产。

废弃长挂：tools/run_order_cycle.py（请用短循环）。

## Maa UI / interface

- assets/interface.json 项目名：**萝卜家园2**
- 控制器：萝卜家园2（Win32） / FramePool + PostMessage
- 本机部署显示：Agent连接检查、单订单补产与交单、右上角时间OCR；旧原生点击诊断仍保留在源码，但不在新UI显示。
- 周期有“仅预览/正式执行”和轮数、检查间隔、超时设置；正式执行会确认清地一次，停止可关闭该确认框。
- UI→Agent→可取消Framework工作进程复用CLI，只补选中一单的缺口，多名空闲员工可分担；取消循环计划，保存后复核，达标才领取。未知能力、空闲容量不足及不明状态会停止；详细限制与已验证范围见[MAA_UI.md](docs/MAA_UI.md)。

Pipeline：assets/resource/pipeline/carrot_farm.json（已去掉 MyTask 模板节点）。

## 饲料生产循环

新增「饲料生产循环（农田3）」任务，默认不勾选，放在队列最后运行。固定玉米20／甘蔗16／土豆12一次性计划，三项全部达标才领取并磨坊一键补满，定时领取磨坊员工饲料。背包饲料上限默认120、间隔60秒，均可自选；达到上限暂停，下降后恢复。使用、日志和实机验证范围见 [饲料循环说明](docs/FEED_PRODUCTION_LOOP.md)。

## 目录约定

| 路径 | 用途 |
| --- | --- |
| tools/ | 业务 CLI（主入口） |
| tests/ | 单测 |
| assets/resource/image/ | 模板图 |
| assets/catalog/employee_capabilities.json | 能力表（需入库） |
| assets/catalog/backpack_crops/registry.json | 作物登记（需入库） |
| assets/catalog/**/latest_*.json | 运行态，默认 gitignore |
| .tmp/ | 全部临时截图/日志/探针 |
| debug/ | 本地调试输出（gitignore） |

## 单测

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 已知限制

- 不支持真最小化挂机（FramePool 限制）
- 员工作物与员工等级挂钩；缺等级阈值的作物需实机补录
- Day1-3 代码路径已落地；连续两轮实机短循环验收仍取决于本机游戏状态

## 致谢

基于 MaaFramework 与其 Practice 模板起步，业务层为萝卜家园2 自用自动化。


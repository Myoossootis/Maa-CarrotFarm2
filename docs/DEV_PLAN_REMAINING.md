# Maa-CarrotFarm2 剩余开发任务规划

> 2026-10-25 接手开发以 [最新审计与交付顺序](DEVELOPMENT_TAKEOVER_AUDIT_2026-10-25.md) 为准，包含固定每期 26 次、UP 优先与员工排产优先的用户规则。下文保留为 10 月 15 日历史进度。

> 更新：2026-10-15。Day1–3 **代码路径已落地**；连续多轮实机验收仍待本机游戏状态。临时文件只写仓库 .tmp/。

> 最新增量：单槽小麦缺口补产、收取、库存复核及订单接受已实机通过（不是无人干预双轮验收）。Maa最小接入范围与验收顺序见 [MAA_INTEGRATION_PLAN.md](./MAA_INTEGRATION_PLAN.md)，最新证据见 [进度文末](./PROGRESS_2026-10-15.md)。

## 当前基线

- Day1：员工列表 progress OCR + 领取按钮/完成横幅二次确认；单次计划默认（不勾「完成后继续」）
- Day2：	ools/run_short_cycle.py；交单按缺口作物重匹配；看板 sleep/关板/回农场 helper
- Day3：能力表 schema v2（等级门槛字段 + 别名）；牧场预检挂在排产脚本，短循环有 ranch 提示
- Day4：README / interface / pipeline 去 MyTask·MaaXXX 模板味；gitignore 运行态 catalog；**以 tools/ CLI 为主**

硬约束：禁止最小化/缩小窗口；临时文件只写 E:\\Maa-CarrotFarm2\\.tmp\\。

---

## Day 1（稳收取 + 防误收）— 代码完成 / 实机抽查待做

验收目标：长单 OCR 卡住时仍能判断可领/未满。

状态：

- [x] list_progress ROI/早停/排序 + _secondary_finish_confirm
- [x] collect_finished_slot 横幅快路径；不盲信卡死 4/N
- [x] monitor 分田 progress；慢作物 timeout hint
- [ ] 实机：monitor→collect→背包抽查（需领取按钮可见）

## Day 2（稳交单 + 短循环）— 代码完成 / 双轮实机待做

状态：

- [x] 交单重扫按缺口作物匹配（不冻订单号）
- [x] order_board_helpers + 
un_short_cycle.py
- [x] 
un_order_cycle.py 标 DEPRECATED 并对齐缺口续种
- [x] 排产默认 one-shot
- [ ] 实机连续两轮短循环无人工介入

## Day 3（能力表与牧场）— 代码完成 / 等级实机补录待做

状态：

- [x] capabilities schema v2 + employee_capabilities.py 等级门
- [x] 小麦等别名规范化
- [x] 短循环 ranch 预检提示；排产内 ensure_ranch_feed
- [x] unmet / feed 单测
- [ ] 实机录 worker_level / min_level；pending 作物补全（见 Track C）

### Track C（能力表）进度

- 详见 [TRACK_C_STATUS.md](./TRACK_C_STATUS.md)：7 槽 `worker_level` 全空；pending 5 作物待 min_level；鱼肉 unsupported。
- 录入 CLI：`tools/record_worker_levels.py`（默认 dry-run，证据 `.tmp/track_c/`）。
- 提交分组已刷新：[COMMIT_GROUPS.md](./COMMIT_GROUPS.md)（勿自动 commit）。

## Day 4（产品化与入库准备）— 本轮完成（不自动 commit）

状态：

- [x] 清理 MyTask* / MaaXXX 展示名；pipeline 更名为 carrot_farm.json
- [x] README 写成萝卜家园2 使用说明
- [x] .gitignore 覆盖 .tmp/、debug、运行态 latest_*.json 等
- [x] docs/COMMIT_GROUPS.md 建议提交分组
- [ ] 用户确认后自行 git add / commit（本代理不执行 push）

## 明确不做（近期）

- 不追求真最小化挂机
- 不把完整短循环硬塞进未经验证的 MaaPiCli 长任务
- 不把运行态 catalog / 大截图提交进仓

## 新用户最短路径

1. 游戏打开且未缩小；cd /d E:\\Maa-CarrotFarm2
2. .venv\\Scripts\\python.exe tools\\run_short_cycle.py --rounds 1
3. 确认检查清单后加 --apply


---

> 2026-10-15 暂停：详见 [PROGRESS_2026-10-15.md](./PROGRESS_2026-10-15.md)。


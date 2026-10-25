# 解码数据接入：只读对比阶段

当前已进一步完成[正式 Maa 读取接入](DECODED_STATE_MAA.md)，正式工作进程使用解码读取。
本文件的首轮对比和预览内容保留为阶段记录；游戏操作尚未执行验收。

已增加一层纯数据适配器 `tools/decoded_state_adapter.py`，将解码结果转换为现有脚本使用的结构：

| 数据 | 接入结构 / 验证方式 |
| --- | --- |
| 库存 | 以游戏物品 ID 保留身份，精确名称映射为现有 `crop_XXX`，输出 `items[].quantity` |
| 订单 | 输出 `order/items/name/actual/required/missing/satisfied/reason`，普通、聚会、外卖分栏 |
| 员工 | 建筑 ID 映射为 `field_1`、`ranch_2` 等现有岗位 ID，分别保留目标与员工持有产品 |
| 狮王任务 | 保留 90 个槽位及成员归属、占用、进度和截止时间；未发现原有识图消费者 |

`tools/allocate_employee_work.py` 新增 `--decoded-state` 只读预览入口，复用原有
`prepare_order_target()`、`build_targets()`、`allocate()`。
普通订单仍按现有纯函数选择前三个槽位中的首个可读非鱼肉订单；
聚会/外卖本轮完成结构适配和历史对比，未接入其会操作界面的选单/提交循环。

**首轮对比阶段没有切换正式运行的数据入口。** 后续正式接入状态见上方链接。
不运行旧“扫描”脚本：旧扫描会点击、切页，某些流程还会刷新订单。
新适配器和对比入口不依赖 Maa、OpenCV 或任何游戏控制器。

## 运行

在项目根目录，读取当前缓存一次并对比已保存的识图 JSON：

```powershell
.venv/Scripts/python.exe -X utf8 tools/compare_decoded_state.py --live
```

复用已有解码文件进行离线对比，不再读取游戏进程：

```powershell
.venv/Scripts/python.exe -X utf8 tools/compare_decoded_state.py --decoded .tmp/decoded_shadow/current_state.json
```

直接调用现有排产脚本的新增预览入口，也不读取游戏进程：

```powershell
.venv/Scripts/python.exe -X utf8 tools/allocate_employee_work.py --decoded-state .tmp/decoded_shadow/current_state.json --batch-count 3
```

新增参数只会输出预览 JSON，没有 `--apply`。预览顶层始终为 `execution_eligible: false`。
不要将预览的 `allocation.ready` 当作允许执行：它仅表示旧能力表下的容量分配可行，
不表示已经执行了保存计划或订单提交。
员工能力按用户当前要求沿用现有配置，不要求再次核实；本阶段仍只展示和计算，不执行游戏操作。

对比输出固定写入 `.tmp/decoded_shadow/`：

- `current_state.json`：`--live` 模式的原始解码结果。
- `compatibility.json`：转换后的兼容数据，含 90 个任务。
- `planning_preview.json`：现有纯规划函数的计算结果。
- `comparison.json`：库存、订单、员工历史差异、来源时间和未验证事项。

不会覆盖 `assets/catalog/**/latest_*.json`，不会伪造 `returned_to_farm`、提交成功或界面勾选证明。
输入格式错误时退出码非零；缺少历史基线时报告 `no_baseline`；
映射缺失和历史数值差异本身不是命令失败，调用方应查看报告中的状态和 `mapping_issues`。
若本轮失败，磁盘旧报告可能仍在；请同时检查退出码及报告时间。

默认对比源为背包 `latest_scan.json`、普通订单 `latest_scan.json`、
订单 `latest_completion.json` 和员工 `latest_assignment.json`。
可用 `--legacy-inventory FILE`、重复的 `--legacy-orders FILE`、`--legacy-employees FILE` 指定其他已保存证据。

## 材料倍数

解码结果 `costs[].count` 是单次需求。客户端代码中：

- `ViewGuestModel.getOrderMenuVo` 从订单 `COSTS` 取单次材料。
- `SceneGuestOrderView.showPanelNeedFood` 显示 `item_num × 当前选定完成数`，未勾选批量时为 1。
- `SceneGuestOrderLogic.acceptOrder` 用同一完成数进行材料检查和扣除。

本次只读对比默认**假设**普通单 3 次、聚会/外卖 6 次，与旧脚本的常见规划口径一致，
并不证明当前界面处于该状态。可用 `--normal-count`、`--party-count`、`--takeout-count` 显式调整。
排产预览入口要求显式传 `--batch-count`。
如果旧记录的材料数不能被其标记的完成次数整除，报告 `batch_metadata_inconsistent`，
不继续把该记录当作可靠的批量数量证据。

## 首轮历史对比结果与边界

2026-10-18 北京时间 02:22:02 的一次缓存读取：24 种产品、27 条订单、9 个员工计划、90 个任务。
这些数据成功经过适配器和现有普通订单规划函数。

- 22 种库存产品有现成目录映射；游戏物品 161“白菜”、160“草莓”在旧目录缺失，保留原 ID、名称和数量。
  不把“草莓”当成“白玉草莓”，也不自动扩充员工可生产列表。
- 旧库存记录的 22 条中，21 条可按身份对应但数量不同，另 1 条“白玉草莓”未在当前产品记录中对应。
  旧文件没有可靠采集时间，文件修改时间在 8 月；这不是同一时刻的交叉验证。
- 旧普通单证据中有 2 条记录的完整配方仍能匹配当前槽位 7，验证了本例 3 倍需求的换算；
  这两条历史记录本身配方相同，不能由此证明原订单 UID 相同。其库存数也已经改变。
- 一份 10 月 16 日北京时间的聚会记录标记 6 次，但材料需求为 25、45，不能被 6 整除；报告单独标记证据矛盾。
- 旧员工记录中牧场 2 的鸡蛋 18 目标与缓存一致，三个农田的历史土豆目标与当前空计划不同。
  员工持有的鸡蛋 20 没有计入玩家库存，牧场 2 也不会进入预览的空计划候选。
- 以普通订单 3 次为假设，现有规划函数选择槽位 1：甘蔗需 51、背包有 2、缺 49，
  按旧能力表可分配给三个农田。只输出预览，没有保存计划。

员工能力沿用用户认可的现有能力表；即使缓存计划为空，
也不等于已经执行或更改了地块、工具、体力、饲料等状态。
新读取可以替代库存数、订单材料和计划字段的识图来源；
页面导航、游戏操作与操作后成功证明需要独立接入验证。
本轮没有当前画面与缓存的同一时刻对比，也没有提交、刷新、领取或保存计划验证。

## 后续接入：Maa 只读数据窗口

白菜、草莓的映射现已补齐，当前读取的 24 种产品映射缺失为 0：

| 游戏物品 ID | 名称 | 自动化 ID | 生产路线 |
| --- | --- | --- | --- |
| 161 | 白菜 | crop_025 | field |
| 160 | 草莓 | crop_026 | field |

映射位于 `assets/catalog/decoded_products.json`，适配器和缺货目标函数共用。
与识图模板目录分开维护，避免创建不存在的模板或把草莓混同为白玉草莓。
上文“本次实际结果”记录的是补映射前的历史结果，当前以新 `comparison.json` 为准。

Maa 任务列表首项新增 **解码数据窗口（只读）**，默认不勾选。
其 `OpenDecodedDashboard` 节点只有打开窗口的自定义动作，没有后继游戏节点。
Agent 会独立打开 `tools/decoded_dashboard.py`，启动时读取一次当前缓存；
窗口关闭不影响 Maa 主界面。现有生产任务的默认行为不变。
使用该入口时仅选择这个任务，其他生产任务仍保留各自功能。

窗口包含库存、订单、员工计划和狮王任务四个页签，支持搜索；任务可以筛选已升级、未升级。
狮王任务另有稀有度筛选，表格分别列出“稀有度”“铃铛奖励”“升级”。
游戏按 `quality > 0` 标为稀有，品质 2 的升级任务仍计入稀有，不能只显示“升级”而漏掉稀有标记。
铃铛由任务奖励箱配置读取，当前 28 条普通、62 条稀有（其中 61 条已升级）；
22 号奖励 90 铃铛，67 号奖励 82 铃铛。这里显示完成奖励，不是已获得数量。
可用 `--tab family` 启动时直接停在狮王任务页。
初始显示聚会订单 6 次口径，材料同时列出单次、6 次、背包和缺口。
普通订单可显示 1/3 次，聚会与外卖可显示 1/6 次；切换只是计算与展示，
不会改变游戏的勾选状态。用户已确认单次材料乘以 6 对应勾选 6 次。
员工能力直接沿用现有配置，任务截止时间使用北京时间。

“只读刷新”仅启动原有 Node 解码器，后台线程完成后替换成功快照；失败时保留旧快照并显示错误。
本窗口不导入 Maa 控制器，不包含提交、领取、刷新订单或保存计划按钮。

也可直接打开窗口：

```powershell
.venv/Scripts/python.exe -X utf8 tools/decoded_dashboard.py --launch
```

不读取新缓存，仅查看现有 JSON：

```powershell
.venv/Scripts/python.exe -X utf8 tools/decoded_dashboard.py --snapshot .tmp/decoded_shadow/current_state.json
```

Maa 本地入口已同步到 `.tmp/maa_ui/interface.json`。
修改源入口后可用 `tools/prepare_maa_ui.py` 同步；已运行的 Maa 需要重新打开才能重新载入任务列表。
窗口日志为 `.tmp/decoded_shadow/window.log`。

本次原生窗口已启动，读取实际窗口组件确认四个卡片为 24/27/9/90，表格行数为
库存 24、聚会材料 32、员工 9、任务 90，映射缺失为 0。
聚会首条材料番茄单次 29、6 次 174、背包 82、缺口 92。
截图验收工具出现 `FrameArrived timed out` / `window capture timed out`，
未完成像素级布局验收；组件内容验证及原生窗口启动验证已完成。

补映射和窗口实现后，通过 18 项适配测试、8 项窗口与 Maa 动作测试、7 项原排产测试、
16 项原 Maa 接入测试、9 项原订单选项测试、7 项原生产目标测试。
其中窗口动作测试使用模拟启动，实际原生窗口另已打开并读取组件内容验证。

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_decoded*.py -v
```

## 回归测试

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_decoded_state_adapter.py -v
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_employee_allocation.py -v
node --test tools/read_only_game_state.test.cjs
```

本次通过 17 项新适配测试、7 项原排产测试、4 项原解码测试。
覆盖材料倍数、物品 ID/名称歧义、未知作物、非等待订单、员工持有物品、忙碌岗位保留、
占用任务的缺失进度、历史配方跨槽位匹配、倍数元数据矛盾以及无 UI 依赖的导入路径。
`--live` 包装器的命令路由另用模拟测试确认只启动解码器；实际目标读取由前述本次缓存读取验证。

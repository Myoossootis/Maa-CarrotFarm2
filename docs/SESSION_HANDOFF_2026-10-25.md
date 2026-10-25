# 开发进度与断点交接

记录时间：2026-10-25 17:48（UTC+8）。用户因5小时额度接近上限要求保障连续性，先停止新增修复并保存进度。**先读本文，再核对真实Git／PR状态；CI状态只是记录时快照。**

## 1. 目标与固定约定

- 项目`E:\Maa-CarrotFarm2`，旧本地项目`E:\萝卜家园自动化脚本`。目标：自动订单→种植养殖与员工排产闭环→狮王循环。
- 只用本地只读解码＋Maa视觉／窗口消息输入。Cocos、Frida、WMPF内部调用实验保持封存，不恢复探针或内部调用。
- 狮王每期固定26次：优先本人已接任务，再选可完成、未占用的UP。指定员工只需按要求上阵，**不用由指定员工亲自执行生产**，其他员工生产也可计入。UP种收优先手动一键，普通订单缺料仍员工优先；星级最多24且不作首选。UP一键执行尚未实现。
- 不覆盖在产计划、不清旧未决记录、不买次数／员工、不放弃本人任务。业务输入前保存意图，结果不明先回读，不重复操作。
- 每轮先完整读Karpathy技能、根目录AGENTS和CONTRIBUTING。先复现与验收，再最小修改；主题分支、版本／changelog、回归、实机证据、提交和PR分别记录。原始账户数据、客户端源码、运行缓存仅本地。

## 2. Git断点

| 对象 | 记录时状态 |
| --- | --- |
| 稳定main | 提交交接前更新为`38a0535`，应用0.7.6；PR #5／#6／#7／#8已合并 |
| 最新功能提交 | `52517c381cc905e4452dd8342964d7fb3d41119c`，应用0.7.6，次数余额保护 |
| 功能分支 | `codex/order-completion-availability`，已推送 |
| [PR #8](https://github.com/Myoossootis/Maa-CarrotFarm2/pull/8) | 提交交接前4项CI全部成功，已按准确head合并，功能没有遗留待合并状态 |
| 下一修复分支 | `codex/order-return-farm`，从52517c3建立，**尚无N01e源代码修改** |
| 本交接分支 | `codex/session-handoff`，从稳定main建立并合入最新0.7.6；交付差异只有文档 |

尚未跟踪的两个回放图已生成，切分支要保留：

```
tests/fixtures/map_return/farm_navigation.png
tests/fixtures/map_return/restaurant_navigation.png
```

它们只保留截图底部1080—1358区域，尚无对应测试、尚未提交。纳入N01e前查看并再次核对无账户信息，不要`git add .`。其余功能代码均已提交到52517c3。

## 3. 本轮完成：0.7.6次数保护

静态核对客户端90：聚会／外卖配置分别关联103／104次数道具，容量均6。`SceneGuestOrderView.showPanelNeedFood`只在可用次数大于1等条件成立时显示多次控件；`SceneGuestOrderLogic.acceptOrder`在余额0时转入购买界面。因此不能把隐藏控件当成未勾选，或看见黄色接受按钮就点击。

52517c3修改路径：

- `read_only_game_state.cjs`新增`orderCapacity`：有效次数道具余额求和、排除过期记录；仅90版语义已验证，其他版本明确不完整，不伪装成0。
- `decoded_runtime.py::order_capacity`验证版本、完整性、分类／道具身份、余额及容量。
- `order_board_availability.py::probe_availability(required_count=...)`核对UI分子分母与本地余额／容量。0余额返回`order_capacity_exhausted`；不足请求批次返回`order_batch_unavailable`；冲突返回未知。
- 白名单检查、选卡前和接受前均接入保护。已接受配送凭据仍优先恢复领奖。默认6次不变，显式历史目标仍校验其请求次数。
- 等待状态不选卡、不排产、不接受、不修改本地配额、不猜恢复时间。新保护要求解码模式，独立CLI应通过`ui_worker`进入，不能退回OCR放行。

实机：两个正式prepare-only入口均等待，UI均0/6，本地余额均0。输入记录仅有打开板、切页签、关板，**无选卡、勾选或接受**；前后库存、员工及计划一致。

离线：Python535项，532通过、3个可选历史截图跳过；Node25项通过；Schema、maa-tools、版本和diff检查通过。最后仅调整接受准备日志位置，另跑23项批量订单回归通过。PR #8远端4项CI提交交接前已全部通过并合并；后续仍核对真实状态。

依据及限制在功能分支的`docs/ORDER_CAPACITY_2026-10-25.md`。REA仅静态解析，没有执行游戏代码；动态语义图有未解析部分，具体条件另按函数正文核对。

## 4. 下一项必须先修：N01e返回农场误报

**纠正旧结论：**`ensure_returned_to_farm`只看手机／背包，而餐厅也有这些图标。0.7.5报告“返回农场”的证据不足；本轮专用判定为false，实际最后停在餐厅。库存及计划未变的回读结论仍成立，不能把旧`returned_to_farm=true`当农场证明。

已做离线复现：

| 导航回放图 | 旧is_on_farm | 专用farm_visible |
| --- | --- | --- |
| restaurant_navigation.png | True（错误） | False |
| farm_navigation.png | True | True |

落点：`tools/order_board_helpers.py::is_on_farm/ensure_returned_to_farm`。复用`tools/enter_farm.py::farm_visible/enter_farm`，它用专用地图入口区分农场／餐厅，已知入口只点一次，再连续确认。

建议最小步骤：

1. 对两个导航图补失败回归，证明共有HUD不能当农场证据。
2. 先识别已知订单页签并关闭一次，再复用专用地图导航；已在农场不点，餐厅入口确认后只点一次，未知页零输入停止。订单板应优先处理，不能把弹窗背后的图标当当前地图；移除泛用关闭图标触发的固定坐标兜底。
3. 模拟已知板、餐厅、农场、未知页、导航未生效；未确认不得成功或再点。
4. 单写入者锁下做有限实机往返，最终用farm_visible核验，保存前后图，员工／计划不变。不为验证而收取、清地或排产。
5. 独立版本、测试、提交和PR之后，再做普通单prepare-only，核对选卡、配方、完成次数。尚不能直接接取。

聚会／外卖当前0余额，正余额批次及真实交单等待条件恢复；不购买、不重置、不反复扫描。可先做普通单准备和N03离线恢复开发。

## 5. 后续顺序与未决操作

- **N03恢复**：`run_employee_order_plan`在配置返回后才记saved；正式`monitor_employee_products`未复用新收取意图；普通单缺同等接取日志。先补业务输入前持久化身份／意图，模拟保存、收取、扣料后中断并证明不重发，再实机做一单。
- 复用`reconcile_production.py::collect_once`和`production_reconciliation`的单槽收取意图、回读补证和不重试规则，不另造一套。
- **N04互斥**：只有部分ui_worker入口持cycle_lock。独立批量／普通CLI不能当成自动有锁；明确顶层与子进程归属，避免递归上锁自锁。唯一运行ID，旧latest只作索引，不授权新输入；配额记账幂等。
- **N05能力**：真实员工等级已读，旧能力表等级仍为空；静态岗位能力不等于换人后能力，未验证产品不可分配。
- **N06冲突**：旧饲料20/16/12与后续农田3计划冲突，手动播种日志也未确认。重新对账当前计划、物理作物、持有及库存，不清记录、不重播、不覆盖。
- 本轮开始只读对账：90版生产完整，牧场2可领、牧场1空闲，其他在产保留。**只是当时快照；续做必须重新读取，不能据此直接收取或排产。**

路线见[DELIVERY_ROADMAP](DELIVERY_ROADMAP.md)。此前完成度45%—55%是范围粗估，未重新统计；尚未验收连续两轮订单生产闭环，也未实现首个UP执行闭环，不能用版本号／测试数推算完成率。

## 6. 本地证据和工具

目录`E:\Maa-CarrotFarm2\.tmp\completion_2026-10-25\`：

| 文件 | 内容 |
| --- | --- |
| initial_snapshot.json／initial_reconciliation.json | 初始只读对账和历史未决状态 |
| static_manifest.json／rules.txt／functions.txt | 包90源码摘要、范围和条件；static／standalone原代码仅本地 |
| before_capacity.json／after_capacity.json | 两类实机前后快照 |
| party_completion.json／takeout_completion.json | 正式入口等待结果 |
| party_inputs.json／takeout_inputs.json | 全部导航点击，无选卡／业务输入 |
| capacity_result.json／after_capacity.png | 库存／计划不变，专用农场核验false |
| initial.py／verify_capacity.py／trace_worker.py | 只读／有限导航工具，有外层cycle_lock |
| map_fixtures.py | 仅裁剪底部导航回放图，已执行，无游戏输入 |
| full.log／node_after.log／related.log／batch_final.log | 离线测试日志 |

旧证据仍在`.tmp/navigation_fix_2026-10-25/`、`.tmp/order_board_2026-10-25/`、`.tmp/normalized_entry_2026-10-25/`，不要覆盖旧版本目录。`.tmp/read_only_decoder/hints.json`含运行地址提示，不打印、不上传。账户快照、客户端包、解包、凭证一律仅本地。

临时工具运行前仍须审计参数和恢复分支；verify_capacity.py会有限导航，旧返回函数尚会误报农场，不能用其原返回标志验收。

## 7. 新会话起步

```powershell
git status --short --branch
git log --oneline -6
.venv/Scripts/python.exe tools/check_version.py
.venv/Scripts/python.exe .tmp/reconcile_2026-10-25/gh_cli.py pr view 8 --repo Myoossootis/Maa-CarrotFarm2 --json state,headRefOid,statusCheckRollup
```

gh_cli.py使用已有Git凭据，仅在子进程内认证，不输出密钥；不要打印凭据或重新登录。Git网络此前需逐条加`-c http.proxy= -c https.proxy=`绕过失效本机代理，不改全局配置。

PR #8若仍未合并：先核对head为52517c381cc905e4452dd8342964d7fb3d41119c及CI全绿，按现有流程合并；随后fetch，切回`codex/order-return-farm`并合入main，保留未跟踪回放图。如出现他人新改动，先辨明归属，不reset／clean。

重新读取当前状态、核对游戏运行和版本，再从第4节补N01e。MaaFw固定5.13.1；worker禁止原生Tasker.post_task，输入只走归一化窗口消息入口。离线模拟、真实只读和业务实机分别记录。**没有创建定时任务；额度恢复后需继续本会话，或让新会话读本文接手。**

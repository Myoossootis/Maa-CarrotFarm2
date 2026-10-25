# 开发进度与断点交接

## 当前暂停断点（优先读，2026-10-25 21:05 UTC+8）

用户明确要求暂停。已给本次Maa工作进程发送取消，日志确认exit 130，验收父进程结束；运行summary状态cancelled。本次有限两单运行完成0单0次，尚未交付当前目标；不能把此前历史回执再次计为本次完成。代码停止开发，后台自动输入已停，已保存员工计划保持原状。不要自行恢复，等待用户继续指令。

- 当前应用0.10.0，分支`codex/employee-plan-recovery-ui`；最新功能提交`03803fe`，之前返回修复`fb86c25`、领奖修复`9653659`、UI入口`23fd406`、员工恢复`16caa45`。代码已推送。[PR #12](https://github.com/Myoossootis/Maa-CarrotFarm2/pull/12)保持草稿，未合并。暂停时`9ff6bfc`的resource、offline-tests均成功；本交接文档提交后的准确head仍需重新查CI。
- 本次运行ID：`9b5e397e16bf48c9835071849ad7b1bb`，summary在`.tmp/normal_order_loop/<run_id>/summary.json`；主日志`.tmp/employee_recovery_2026-10-25/live_ui_loop_0100.log`，工作日志`.tmp/worker_logs/20261025T125938659313Z.log`。末尾明确记录cancelled，不能以Maa任务返回false当作业务失败重放。
- 活动员工操作：`e7a39ffd655747d0883dab71e09b11e1`，持久化状态saved；农田1／2各番茄32＋玉米18，各50的一次性计划。最后日志观测均为32/50，尚未收取。整单原缺番茄64、玉米48，本批安排100，理论剩余玉米12，**这只是旧计算，收取后必须重新核算**。农田3原玉米18＋甘蔗14＋土豆18计划保留，不得覆盖或代领旧资源。
- 暂停后的只读读取失败：`No validated MMKV key in running WeChatAppEx; open the mini program and retry.` 没有退回识图，没有重试业务。不能确认最后日志之后的游戏状态；下次用户要求继续后，先正常进入游戏并重新读取，不沿旧进度直接收取／排产。游戏自身生产是否继续取决于游戏状态，脚本没有清计划、关闭游戏或冻结生产。
- 前一普通单回执`9924e43278be4e94a3a0ca21ef687297`已completed且returned_to_farm=true，恢复返回已实机验证，不得重新计数或交单。更早`d7025292…`为有明确审计证据的历史完成；当前目标没有新的accept_intent。
- 离线582项：579通过、3项可选旧截图跳过；Node25项、schema、版本与diff检查通过。真实保存／恢复、甘蔗实收42、玉米实收48、三倍扣料／领奖、仅恢复返回均有证据；**0.10.0分批完整收取、剩余缺口补齐以及连续两单6次仍未验收**。

用户要求继续后的恢复顺序：先确认旧工作进程为0、共享锁可获取，再读当前解码和上述canonical操作；用正常UI入口或正式worker继续，它会优先监控同一saved操作，不重新保存两田计划。全部收取确认后重新扫描UID、配方和库存，按新缺口建立下一批；订单完成只按新独立回执与返回证明计数。旧未绑定饲料／手动播种记录继续封存待对账，内部调用实验不恢复。临时verify_ui_loop.py会真实运行有限两单，未得到继续指令不要执行。

## 以下为暂停前阶段记录

> 阶段更新（0.9.1时）：已推进到0.9.1，分支`codex/employee-plan-recovery-ui`，提交`16caa45`（0.8.2员工保存／收取恢复）、`23fd406`（0.9.0普通3倍UI）、`9653659`（0.9.1奖励导航、终态计数和动画等待）；[PR #12](https://github.com/Myoossootis/Maa-CarrotFarm2/pull/12)。577项回归通过（3跳过），Node25项通过。先读[UI闭环最新报告](NORMAL_LOOP_UI_2026-10-25.md)，核对真实Git、CI和未决操作，再参考下文历史断点。旧0.8.1缺甘蔗41的断点已完成补产与首单审计补证，不能重放旧计划。

当前源码**0.10.0**、提交`03803fe`：0.9.2（`fb86c25`）已实机恢复`9924e432…`返回，计为1单3次；下一单番茄缺64、玉米缺48，空闲容量100小于总缺口112，旧分配在保存前安全停止。0.10.0补容量分批，582项检查通过（3跳过），正式MFA已同步部署0.10.0且不自动开跑。

**暂停前记录：2026-10-25 21:01（UTC+8）曾进行有限两单实机，结果以顶部暂停断点为准**：源Maa任务`RunNormalOrderLoop`通过`.tmp/employee_recovery_2026-10-25/verify_ui_loop.py`运行，日志`live_ui_loop_0100.log`，首批操作`e7a39ffd655747d0883dab71e09b11e1`，农田1／2各番茄32＋玉米18，剩余玉米12待本批收取后重新核算；农田3原计划保留。CLI会自然等待、按绑定日志收取和继续；**不要同时再点UI开始或重跑验收脚本**。若会话中断，先查看该日志是否仍推进、`.tmp/employee_operations/active.json`及`.tmp/normal_order_delivery/active.json`，核对当前实际进程和只读状态后恢复。未确认编辑／保存／收取不能强制重试，不能删除日志绕过阻塞。真实单数以独立completed回执及returned_to_farm为准；不能把生产批次或旧终态重复计数。

本轮已完成：甘蔗20／21保存、重启回读及实收42；首单3倍扣料／奖励审计补证；玉米24／24实收48、新单3倍扣料／领奖与待返回恢复。新版本完整两单6次仍待当前运行结果。旧历史、饲料和手动播种未决记录均保留。5小时额度当时已用93%，代码已逐版提交并推送PR #12，维持草稿直到实机结果核对和准确head的CI通过。

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

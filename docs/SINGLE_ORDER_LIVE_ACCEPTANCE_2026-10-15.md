# Maa UI 单订单实机验收：未通过

2026-10-15，北京时间18:52–18:57。只通过真实MFAAvalonia的“单订单补产与交单（实验）”启动；没有改用CLI排产，也没有修改业务源码绕过失败。

## 实际进度

- 用户确认本轮已清地/白天后批准Maa清地框。首轮18:53:49启动，首单识别完成；进入员工菜单时鼠标从(910,725)变为(910,726)，诊断立即取消。前台未变；无法仅凭这次变化判断来源，不能算鼠标稳定通过。计划保存列表为空。
- 用户随后确认“静止，可以继续”。第二次通过UI开始，18:55:50确认清地，18:55:51进入实际周期。
- 18:56:02完成订单1准备扫描，仅识别这一单，先确认完成3次；番茄155/75、玉米34/45、鸡蛋42/30，无鱼肉，仅缺玉米11。
- 开始检查员工空闲状态，打开农田1空工作计划并关闭。之后仍停留在农田1“上阵员工”详情页，却被当成员工列表，在错误页面尝试列表滚动，最终18:56:51报`employee slot not found or button not visible: field_2`并退出。
- `latest_assignment.json`状态failed、`saved=[]`；没有点击安排、保存计划、领取物资或接受订单。没有覆盖其他员工的计划。

## 页面判定问题的证据

现场截图`.tmp/maa_ui_single_order_accept/current_planning.png`为720×1358 Maa FramePool实机截图，显示农田1详情页和员工头像选择区，没有农田2/农田3列表项。

对同一帧只读匹配得到：

```text
_farm_worker_list_visible(frame) = True    # 错误
slot_match(frame, field_1) = 0.891400      # 详情页也有相同标题，超过0.88
field_work_plan_button = 0.738389          # 详情页的工作计划按钮仍可识别
visible_slot_action(frame, field_2) = None
```

`tools/configure_field_employee.py`的`_farm_worker_list_visible`以任一农田标题匹配就返回True；`close_plan_dialog`只关闭工作计划弹层，不返回员工列表。`idle_allocation`随后直接检查下一名员工，`open_employee_list`因误判提前返回，因此滚动的是详情页而非列表。32次错误页面拖动并未取得期望导航结果，不能把输入发送成功当成功能成功。

后续修复应区分员工列表与上阵详情，核实返回列表后再查下一名员工，并为该实机帧补判定回归。本轮仅测试和诊断，未擅自修改业务实现。

## 前台、鼠标和验收范围

第二轮扫单工作进程49212、员工工作进程29380合计11次点击、32次拖动。每次输入前、紧接输入后及0.1秒后的前台HWND与鼠标坐标均不变，全部stable=true，前台为MFA UI而非游戏。该证据只适用于已执行的导航，不代表未执行的生产/领取/交单安全性已验收。

- 清地确认和单订单识别：已执行。
- 取消继续当前计划、保存后核对一次性状态：本轮尚未到达，不能沿用上一轮空计划取消勾选测试作为本轮通过。
- 达标才领取、交单后明确进行中：未到达。
- 完整链条：未通过。

## 证据与收尾

- `.tmp/maa_ui_single_order_accept/audit-46968.jsonl`：两次清地确认结果。
- `audit-49264.jsonl`、`audit-28080.jsonl`：首轮扫描及鼠标变化取消。
- `audit-49212.jsonl`、`audit-29380.jsonl`：第二轮输入记录，全部稳定。
- `.tmp/maa_ui/logs/log-20261015.log`：18:52–18:57真实UI参数、执行和错误日志。
- `.tmp/short_cycle/latest_summary.json`：failed，员工工作进程退出码1。
- `assets/catalog/employee_work/latest_assignment.json`：failed，saved为空。
- `assets/catalog/normal_orders/latest_completion.json`：首单及玉米11缺口。

业务工作进程已全部退出。随后尝试UI停止时任务已经失败、停止控件不可调用，不算一次生产期间停止验收。已选择预览，收尾从正常环境重开UI，移除临时PYTHONPATH验收钩子的运行影响；未删除游戏数据、员工计划或证据文件。

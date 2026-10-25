# 前台安全输入（Foreground-Safe Input）

## 根因

MaaFramework Win32 `MessageInput`（含 `PostMessage` 输入模式）在每次触摸/点击前会向目标窗口投递 **`WM_ACTIVATE`**（`MaaWin32ControlUnit.dll` 仍链接 `SetForegroundWindow` / `ShowWindow`）。微信小游戏宿主窗口类名为 `Chrome_WidgetWin_0`，收到 activate 后会把小游戏/微信抬到前台。

因此：**任何 `controller.post_click` / `post_swipe` / pipeline 原生 `Click`/`Scroll` 都会抢前台**，与是否选用 `MaaWin32InputMethodEnum.PostMessage` 无关。

## 唯一允许的点击入口

| API | 文件 | 行为 |
| --- | --- | --- |
| `post_click_normalized(controller, x, y)` | `tools/maa_frame_normalize.py` | 截图/模板坐标 → 映射到唯一 `Chrome_RenderWidgetHostHWND` → 仅 `PostMessage(WM_MOUSEMOVE/LBUTTONDOWN/UP)` |
| `post_swipe_normalized(...)` | 同上 | 同上，拖拽只打渲染子窗 |

禁止：

- `controller.post_click` / `post_swipe` / `post_touch_*` / `post_scroll` / 键盘注入
- MFAAvalonia / pipeline JSON 里原生 `"action": "Click"` / `"Scroll"`（已从 `assets/interface.json` 下掉相关入口）
- 向顶层 `Chrome_WidgetWin_0` 直接投鼠标消息（会激活宿主）

## 渲染子窗如何定位

1. 控制器 HWND = 标题「萝卜家园2」、类名 `Chrome_WidgetWin_0` 的顶层窗。
2. `EnumChildWindows` 收集类名为 `Chrome_RenderWidgetHostHWND` 的子窗；**必须恰好 1 个**，否则硬失败（禁止回退到顶层）。
3. 把游戏客户区坐标 `ClientToScreen` → 子窗 `ScreenToClient` 后再 PostMessage。

## 进程级封堵

`install_foreground_safe_inputs()` 会把 `Controller`/`Win32Controller` 上的原生输入方法改成硬错误。
`post_click_normalized` / `post_swipe_normalized` 首次调用会自动安装；`tools/ui_worker.py` 在 UI 子进程启动时也会安装。

## 前台策略（已去掉「拒点」创可贴）

- **不再**因为当前前台是微信 / 萝卜家园2 / MFA 就拒绝点击。
- 发送到渲染子窗期间临时禁止目标顶层窗口激活，结束后恢复原启用状态；不附加输入线程，不主动调用 `SetForegroundWindow`，不移动物理鼠标。
- 前台 HWND 发生变化时，写入 `.tmp/fg_steal/` 并输出日志，记录变化前后窗口、坐标、输入实际发送结果。窗口变化本身不代表输入失败，不再据此中止任务或重复点击；业务步骤继续通过截图/OCR确认实际结果。输入发送失败、目标窗口不明、领取/补货结果不明仍会报错。
- 这允许用户在任务运行期间切换应用；宿主自行激活导致的变化也单独留证据。本策略不主动把前台抢回来，因此不能把「任务不中断」描述为「所有环境下窗口绝不激活」。
- `wait_for_background_ready()` 仅检查任务是否停止，不再空等 60 秒让用户切走微信。

## 截图 / 连接

`post_screencap` / `post_connection` / FramePool 绑定不走本模块的鼠标路径；当前未发现它们必然激活游戏。若实机发现连接抢前台，把证据丢进 `.tmp/fg_steal/` 再议（优先永不激活，其次才考虑恢复原前台）。

## 30 秒自测

1. 打开记事本并保持前台。
2. 游戏已打开且未最小化。
3. 运行：

```bat
cd /d E:\Maa-CarrotFarm2
.venv\Scripts\python.exe tools\probe_foreground_safe_click.py
```

4. 期望：记事本仍在前台；`.tmp/fg_steal/probe_result.json` 里 `foreground_unchanged` 为 true。

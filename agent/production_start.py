"""Per-run land-clearance confirmation shared by Maa and the debug CLI.

No saved/global approval: a new cycle invocation always asks again. This module
does not inspect, harvest, clear land, move the mouse or activate the game.

Daily Maa tasks skip this dialog entirely (see production_actions.RunDailyOrders).
When still used, we print loudly and time out so the UI never hangs silently.
"""

import ctypes
import sys
import threading
from ctypes import wintypes


TITLE = "胡萝卜庄园2 · 开始循环确认"
MESSAGE = (
    "请先手动清理农田上残留的作物，并清空员工旧计划与待领取进度。\n\n"
    "残留作物会被员工收割，干扰订单计划的计算。\n"
    "本轮将使用一次性计划：自动领取后取消计划；完成后不会再使用当前计划。\n"
    "达到预计产量后会自动领取。\n\n"
    "清理完成后点「确定」开始本轮；未清理请点「取消」。"
)

DIALOG_TIMEOUT_SECONDS = 60


def _close_owned_dialog(user32, thread_id):
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    @callback_type
    def close(hwnd, _):
        title = ctypes.create_unicode_buffer(256)
        kind = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, kind, len(kind))
        if title.value == TITLE and kind.value == "#32770":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True

    user32.EnumThreadWindows.argtypes = [wintypes.DWORD, callback_type, wintypes.LPARAM]
    user32.EnumThreadWindows(thread_id, close, 0)


def show_land_clearance_dialog(*, is_stopping=lambda: False, timeout_seconds=DIALOG_TIMEOUT_SECONDS) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("land-clearance confirmation requires Windows")
    print(
        f"[日常] 等待清地确认弹窗（最多 {timeout_seconds} 秒）。"
        f"若看不到弹窗，请看任务栏；超时将按[错误]取消。",
        flush=True,
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dialog = user32.MessageBoxW
    dialog.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    dialog.restype = ctypes.c_int
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    thread_id = kernel32.GetCurrentThreadId()
    done = threading.Event()
    timed_out = {"v": False}

    def watch_stop():
        elapsed = 0.0
        last_beat = -5
        while not done.wait(0.5):
            elapsed += 0.5
            try:
                stopping = is_stopping()
            except Exception:
                stopping = True
            if stopping:
                _close_owned_dialog(user32, thread_id)
                return
            if elapsed >= timeout_seconds:
                timed_out["v"] = True
                print(
                    f"[错误] 清地确认弹窗超时（{timeout_seconds}s），自动取消。"
                    f"日常任务已不再弹窗；高级短循环请尽快点确定。",
                    flush=True,
                )
                _close_owned_dialog(user32, thread_id)
                return
            if elapsed - last_beat >= 5:
                last_beat = elapsed
                left = max(0, int(timeout_seconds - elapsed))
                print(f"[日常] 仍在等待清地确认…剩余约 {left} 秒", flush=True)

    watcher = threading.Thread(target=watch_stop, daemon=True)
    watcher.start()
    try:
        choice = dialog(None, MESSAGE, TITLE, 0x00000001 | 0x00000030 | 0x00000100)
    finally:
        done.set()
        watcher.join(timeout=1)
    if timed_out["v"]:
        return False
    if choice == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    return choice == 1


def confirm_cycle_start(*, is_stopping=lambda: False) -> bool:
    if is_stopping():
        return False
    approved = show_land_clearance_dialog(is_stopping=is_stopping)
    return approved and not is_stopping()


def wait_for_background_ready(*, is_stopping=lambda: False) -> bool:
    """Ready as soon as the task is not stopping.

    Safe clicks PostMessage the Chromium render child and do not activate the
    game, so WeChat or the game or MFA being foreground is no longer a blocker.
    Kept as a named gate so CustomActions retain a single preflight hook.
    """
    return not is_stopping()

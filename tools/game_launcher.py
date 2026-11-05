"""Open the installed WeChat mini game through its desktop protocol, once.

Window presence is not login, map recognition, or business readiness.
"""
import ctypes
import os
import time

GAME_URI = 'weixin://launchapplet/?app_id=wx2d7c1a53c1bd45e1'


def game_windows():
    if os.name != 'nt':
        raise RuntimeError('自动打开小游戏需要Windows和已安装的微信')
    api = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    @callback_type
    def visit(hwnd, _):
        title, kind = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        api.GetWindowTextW(ctypes.c_void_p(hwnd), title, len(title))
        api.GetClassNameW(ctypes.c_void_p(hwnd), kind, len(kind))
        if title.value == '萝卜家园2' and kind.value == 'Chrome_WidgetWin_0':
            found.append(dict(hwnd=int(hwnd), visible=bool(api.IsWindowVisible(ctypes.c_void_p(hwnd))),
                              minimized=bool(api.IsIconic(ctypes.c_void_p(hwnd)))))
        return True

    api.EnumWindows(visit, 0)
    return found


def checked_window(windows):
    if len(windows) > 1:
        raise RuntimeError('发现多个游戏窗口，请保留一个后重试')
    if not windows:
        return None
    window = windows[0]
    if not window['visible'] or window['minimized']:
        raise RuntimeError('游戏窗口隐藏或最小化，请恢复窗口后重试')
    return window['hwnd']


def request_open():
    """No click, focus call, login automation, process injection, or retry."""
    window = checked_window(game_windows())
    if window is not None:
        return '游戏窗口已存在；仍需读取当前状态并验证页面。'
    try:
        os.startfile(GAME_URI)
    except OSError as error:
        raise RuntimeError('无法通过微信打开小游戏，请检查微信安装、登录及小游戏桌面入口') from error
    return '已请求微信打开《萝卜家园2》，尚未确认进入游戏；登录或授权提示需手动处理。'


def ensure_open(timeout=45, *, check_cancelled=lambda: None, notify=print):
    check_cancelled()
    notify('[启动] ' + request_open())
    deadline = time.monotonic() + timeout
    while True:
        check_cancelled()
        window = checked_window(game_windows())
        if window is not None:
            notify('[启动] 已检测到游戏窗口；接下来读取并核对状态。')
            return window
        if time.monotonic() >= deadline:
            raise RuntimeError('等待游戏窗口超时；未执行业务操作。请检查微信登录或启动提示后重新开始。')
        time.sleep(.2)

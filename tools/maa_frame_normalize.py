# -*- coding: utf-8 -*-
"""Normalize Maa FramePool frames when the Win32 client is taller than 720x1358.

Foreground-safe input
----------------------
All game clicks/swipes MUST go through ``post_click_normalized`` /
``post_swipe_normalized``. Those helpers PostMessage mouse events only to the
unique ``Chrome_RenderWidgetHostHWND`` child — never ``WM_ACTIVATE``, never
``SetForegroundWindow``, never Maa ``controller.post_click`` (MessageInput
always activates Chrome_WidgetWin_0 first).

Call ``install_foreground_safe_inputs()`` (also done automatically on first
safe click) so any leftover ``Win32Controller.post_click`` / swipe / touch /
key call hard-errors instead of stealing focus.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

try:
    from .task_control import check_cancelled, atomic_input
except ImportError:
    from task_control import check_cancelled, atomic_input

EXPECTED_WIDTH = 720
EXPECTED_HEIGHT = 1358
MAX_TOP_LETTERBOX = 96

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RESOURCE = ROOT / "assets" / "resource"
RUNTIME_RESOURCE = ROOT / ".tmp" / "maa_resource_norm"
FG_STEAL_DIR = ROOT / ".tmp" / "fg_steal"

# Last observed top letterbox on the raw controller frame. Clicks derived from
# normalize_frame() coordinates must add this back before post_click.
_LAST_TOP_OFFSET = 0
# Raw FramePool size. to_controller_xy restores the crop before client scaling.
_LAST_FRAME_WH: tuple[int, int] = (EXPECTED_WIDTH, EXPECTED_HEIGHT)

# Win32 mouse messages (client coordinates in lParam).
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001

_SAFE_INPUTS_INSTALLED = False
_UNSAFE_INPUT_NAMES = (
    "post_click",
    "post_swipe",
    "post_scroll",
    "post_touch_down",
    "post_touch_move",
    "post_touch_up",
    "post_press_key",
    "post_click_key",
    "post_key_down",
    "post_key_up",
    "post_input_text",
)


class _ClickJob:
    """Tiny stand-in for Maa Job so callers can keep `.wait().succeeded`."""

    def __init__(self, ok: bool) -> None:
        self._ok = ok

    def wait(self, timeout: float | None = None):  # noqa: ARG002
        return self

    @property
    def succeeded(self) -> bool:
        return self._ok


def get_top_offset() -> int:
    return _LAST_TOP_OFFSET


def top_letterbox_offset(frame) -> int:
    """Return top crop for letterboxed frames; 0 when already ~720x1358.

    Live FramePool often yields 720x1348 (10px short of the audited 1358). Treat
    near-miss heights as no letterbox rather than failing the whole cycle.
    """
    if frame is None or frame.shape[1] != EXPECTED_WIDTH:
        raise RuntimeError(f"unexpected screenshot shape: {getattr(frame, 'shape', None)}")
    height = int(frame.shape[0])
    # Allow slight short/tall drift around the audited 1358 content height.
    if abs(height - EXPECTED_HEIGHT) <= 16:
        return 0
    extra = height - EXPECTED_HEIGHT
    if 0 < extra <= MAX_TOP_LETTERBOX:
        return extra
    raise RuntimeError(f"unexpected screenshot shape: {getattr(frame, 'shape', None)}")


def normalize_frame(frame):
    global _LAST_TOP_OFFSET, _LAST_FRAME_WH
    offset = top_letterbox_offset(frame)
    _LAST_TOP_OFFSET = offset
    if offset == 0:
        height = int(frame.shape[0])
        if height == EXPECTED_HEIGHT:
            _LAST_FRAME_WH = (int(frame.shape[1]), height)
            return frame
        if abs(height - EXPECTED_HEIGHT) <= 16:
            # Already content-sized (e.g. 1348); keep as-is for template ROIs.
            _LAST_FRAME_WH = (int(frame.shape[1]), height)
            return frame
        if height > EXPECTED_HEIGHT:
            start = (height - EXPECTED_HEIGHT) // 2
            out = frame[start : start + EXPECTED_HEIGHT]
            _LAST_FRAME_WH = (int(frame.shape[1]), height)
            return out
        raise RuntimeError(f"unexpected screenshot shape: {getattr(frame, 'shape', None)}")
    out = frame[offset : offset + EXPECTED_HEIGHT]
    _LAST_FRAME_WH = (int(frame.shape[1]), int(frame.shape[0]))
    return out


def to_controller_xy(x: int, y: int) -> tuple[int, int]:
    """Map 720x1358 template coordinates onto the live controller frame."""
    return int(x), int(y) + int(_LAST_TOP_OFFSET)


def _controller_hwnd(controller) -> int:
    info = getattr(controller, "info", None) or {}
    hwnd = info.get("hwnd") if isinstance(info, dict) else None
    if not hwnd:
        raise RuntimeError("Win32 controller hwnd missing; connect FramePool controller first")
    return int(hwnd)


def _client_size(hwnd: int) -> tuple[int, int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetClientRect failed: {ctypes.get_last_error()}")
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def _screenshot_to_client(x: int, y: int, client_w: int, client_h: int) -> tuple[int, int]:
    fw, fh = _LAST_FRAME_WH
    if fw <= 0 or fh <= 0 or client_w <= 0 or client_h <= 0:
        raise RuntimeError(f"bad click scale frame={_LAST_FRAME_WH} client=({client_w},{client_h})")
    return int(x * client_w / fw), int(y * client_h / fh)


def _window_title(hwnd: int) -> str:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    title = ctypes.create_unicode_buffer(256)
    if not hwnd:
        return ""
    user32.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
    return title.value


def _log_fg_event(event: dict) -> Path:
    FG_STEAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    event = {"time": datetime.now().astimezone().isoformat(timespec='milliseconds'), **event}
    path = FG_STEAL_DIR / f"fg_{stamp}_{os.getpid()}.json"
    path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = FG_STEAL_DIR / "latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def install_foreground_safe_inputs() -> bool:
    """Hard-error any Maa native Win32 input that would activate the game.

    Idempotent. Safe to call from UI worker, CLI, or the first safe click.
    Screenshot / connection APIs are left alone (they do not post mouse activate).
    """
    global _SAFE_INPUTS_INSTALLED
    if _SAFE_INPUTS_INSTALLED:
        return False
    try:
        from maa.controller import Controller, Win32Controller
    except Exception:
        return False

    def reject_input(*args, **kwargs):
        check_cancelled()
        raise RuntimeError(
            "unsafe Maa native input blocked: use post_click_normalized / "
            "post_swipe_normalized (PostMessage to Chrome_RenderWidgetHostHWND only)"
        )

    for cls in (Controller, Win32Controller):
        for name in _UNSAFE_INPUT_NAMES:
            if hasattr(cls, name):
                setattr(cls, name, reject_input)
    _SAFE_INPUTS_INSTALLED = True
    return True


@atomic_input()
def post_click_postmessage_only(hwnd: int, client_x: int, client_y: int) -> bool:
    """Post mouse down/up via PostMessageW only — no WM_ACTIVATE / SetForegroundWindow.

    MaaFw MessageInput.touch_down always calls send_activate_message(WM_ACTIVATE)
    before the click. Chromium/Unity clients (Chrome_WidgetWin_0) often raise
    themselves on that activate. This helper skips activate entirely.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    lp = (int(client_y) << 16) | (int(client_x) & 0xFFFF)
    hwnd_c = wintypes.HWND(hwnd)
    if not user32.PostMessageW(hwnd_c, _WM_MOUSEMOVE, 0, lp):
        return False
    time.sleep(0.01)
    if not user32.PostMessageW(hwnd_c, _WM_LBUTTONDOWN, _MK_LBUTTON, lp):
        return False
    try:
        time.sleep(0.03)
    finally:
        # A cancellation during the press must still release the button.
        released = bool(user32.PostMessageW(hwnd_c, _WM_LBUTTONUP, 0, lp))
    return released


@atomic_input()
def post_click_normalized(controller, x: int, y: int):
    """Click in screenshot/template coordinates without stealing foreground.

    Does **not** call controller.post_click (Maa MessageInput), which posts
    WM_ACTIVATE before every touch and can make 萝卜家园2 take focus.
    """
    install_foreground_safe_inputs()
    check_cancelled()
    hwnd = _controller_hwnd(controller)
    with _lock_foreground(hwnd) as foreground_before:
        cx, cy = to_controller_xy(x, y)
        hwnd = _controller_hwnd(controller)
        client_w, client_h = _client_size(hwnd)
        mx, my = _screenshot_to_client(cx, cy, client_w, client_h)
        target, mx, my = _render_input_target(hwnd, mx, my)
        ok = post_click_postmessage_only(target, mx, my)
        time.sleep(0.05)
    after = _foreground_window()
    if after != foreground_before:
        evidence = _log_fg_event(
            {
                "event": "foreground_changed_after_click",
                "before_hwnd": foreground_before,
                "before_title": _window_title(foreground_before),
                "after_hwnd": after,
                "after_title": _window_title(after),
                "game_hwnd": hwnd,
                "render_hwnd": target,
                "client_xy": [mx, my],
                "template_xy": [int(x), int(y)],
                "input_delivered": bool(ok),
                "input_replayed": False,
                "policy": "verify_game_result",
            }
        )
        print(f'[前台变化] 点击已发送={ok}，不重发；继续核对游戏结果。证据={evidence}', flush=True)
    return _ClickJob(ok)


@atomic_input()
def post_swipe_postmessage_only(hwnd: int, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> bool:
    """Drag only the render child; always release on stop or input failure."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    target = wintypes.HWND(hwnd)
    point = start

    def post(message, flags):
        return bool(user32.PostMessageW(target, message, flags, (point[1] << 16) | (point[0] & 0xFFFF)))

    check_cancelled()
    if not post(_WM_MOUSEMOVE, 0) or not post(_WM_LBUTTONDOWN, _MK_LBUTTON):
        return False
    try:
        steps = max(1, duration_ms // 25)
        for step in range(1, steps + 1):
            check_cancelled()
            point = tuple(round(a + (b - a) * step / steps) for a, b in zip(start, end))
            if not post(_WM_MOUSEMOVE, _MK_LBUTTON):
                return False
            time.sleep(duration_ms / steps / 1000)
    finally:
        released = post(_WM_LBUTTONUP, 0)
    return released


@atomic_input()
def post_swipe_normalized(controller, x1: int, y1: int, x2: int, y2: int, duration_ms: int):
    install_foreground_safe_inputs()
    check_cancelled()
    if not 1 <= duration_ms <= 2000:
        raise ValueError("background drag duration must be 1..2000 ms")
    hwnd = _controller_hwnd(controller)
    with _lock_foreground(hwnd) as foreground_before:
        width, height = _client_size(hwnd)
        points = [_screenshot_to_client(*to_controller_xy(x, y), width, height) for x, y in [(x1, y1), (x2, y2)]]
        mapped = [_render_input_target(hwnd, *point) for point in points]
        if mapped[0][0] != mapped[1][0]:
            raise RuntimeError("employee drag must remain within one render child")
        ok = post_swipe_postmessage_only(mapped[0][0], mapped[0][1:], mapped[1][1:], duration_ms)
    after = _foreground_window()
    if after != foreground_before:
        evidence = _log_fg_event(
            {
                "event": "foreground_changed_after_swipe",
                "before_hwnd": foreground_before,
                "before_title": _window_title(foreground_before),
                "after_hwnd": after,
                "after_title": _window_title(after),
                "game_hwnd": hwnd,
                "render_hwnd": mapped[0][0],
                "input_delivered": bool(ok),
                "input_replayed": False,
                "policy": "verify_game_result",
                "template_xy": [int(x1), int(y1), int(x2), int(y2)],
            }
        )
        print(f'[前台变化] 滑动已发送={ok}，不重发；继续核对游戏结果。证据={evidence}', flush=True)
    return _ClickJob(ok)


def _foreground_window() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0)


@contextmanager
def _lock_foreground(hwnd: int):
    """Prevent the game from activating while posting directly to its renderer.

    A disabled top-level window cannot be activated. Its renderer still handles
    posted messages. Restore the original enabled state even on stop/failure.
    Do not attach input queues or require foreground-process lock privileges.
    """
    foreground = _foreground_window()
    if not foreground:
        raise RuntimeError("no foreground window; no input sent")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.restype = wintypes.BOOL
    user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.EnableWindow.restype = wintypes.BOOL
    if not user32.IsWindowEnabled(wintypes.HWND(hwnd)):
        raise RuntimeError("game window already disabled; no input sent")
    suppress_activation = foreground != hwnd
    try:
        if suppress_activation:
            user32.EnableWindow(wintypes.HWND(hwnd), False)
            if user32.IsWindowEnabled(wintypes.HWND(hwnd)):
                raise RuntimeError("could not prevent game activation; no input sent")
        yield foreground
    finally:
        if suppress_activation:
            user32.EnableWindow(wintypes.HWND(hwnd), True)


def _reject_wechat_foreground() -> None:
    """Deprecated no-op: safe clicks no longer refuse WeChat/game FG."""
    return None


def _render_input_target(hwnd: int, x: int, y: int) -> tuple[int, int, int]:
    """Route to Chromium's render child; top-level mouse messages activate the game."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    children = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    @callback_type
    def collect(child, _):
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, name, len(name))
        if name.value == "Chrome_RenderWidgetHostHWND":
            children.append(int(child))
        return True

    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.EnumChildWindows(hwnd, collect, 0)
    if len(children) != 1:
        raise RuntimeError(f"expected one Chromium render input window, found {len(children)}")
    target = children[0]
    point = wintypes.POINT(x, y)
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)) or not user32.ScreenToClient(target, ctypes.byref(point)):
        raise RuntimeError("could not map game-client coordinates to render input window")
    return target, int(point.x), int(point.y)


def _shift_roi(roi, dy: int):
    if not isinstance(roi, (list, tuple)) or len(roi) < 4:
        return roi
    values = list(roi)
    values[1] = int(values[1]) + dy
    return values


def _shift_node(node: dict, dy: int) -> dict:
    out = dict(node)
    if "roi" in out:
        out["roi"] = _shift_roi(out["roi"], dy)
    if "target" in out and isinstance(out["target"], (list, tuple)) and len(out["target"]) >= 2:
        target = list(out["target"])
        target[1] = int(target[1]) + dy
        out["target"] = target
    return out


def build_runtime_resource(offset: int) -> Path:
    """Return a resource bundle path whose pipeline ROIs match the live frame height."""
    source_pipeline = SOURCE_RESOURCE / "pipeline" / "carrot_farm.json"
    data = json.loads(source_pipeline.read_text(encoding="utf-8"))
    if offset:
        data = {
            name: _shift_node(node, offset) if isinstance(node, dict) else node
            for name, node in data.items()
        }

    runtime_pipeline = RUNTIME_RESOURCE / "pipeline"
    runtime_pipeline.mkdir(parents=True, exist_ok=True)
    # Drop stale pipeline JSON (e.g. renamed my_task.json) to avoid duplicate keys.
    for stale in runtime_pipeline.glob("*.json"):
        try:
            stale.unlink()
        except OSError:
            pass
    (runtime_pipeline / "carrot_farm.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )

    for name in ("image", "model"):
        dest = RUNTIME_RESOURCE / name
        source = SOURCE_RESOURCE / name
        if dest.exists() or dest.is_symlink():
            try:
                dest.unlink()
            except OSError:
                import subprocess

                subprocess.run(["cmd", "/c", "rmdir", str(dest)], check=False)
        try:
            os.symlink(source, dest, target_is_directory=True)
        except OSError:
            import subprocess

            if dest.exists():
                subprocess.run(["cmd", "/c", "rmdir", str(dest)], check=False)
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
    return RUNTIME_RESOURCE


def measure_controller_offset(controller) -> int:
    global _LAST_TOP_OFFSET
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    # normalize_frame preserves the raw capture size for click scaling.
    normalize_frame(job.get())
    return _LAST_TOP_OFFSET

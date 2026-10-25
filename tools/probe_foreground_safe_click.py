# -*- coding: utf-8 -*-
"""30s probe: safe click must not raise 萝卜家园2 while Notepad (or any app) stays FG.

Does not run a full cycle. Issues one PostMessage click to the game render child
near a harmless corner, then reports whether the foreground HWND changed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum

from capture_current_frame_with_maa import find_window
from maa_frame_normalize import (
    FG_STEAL_DIR,
    _foreground_window,
    _window_title,
    install_foreground_safe_inputs,
    measure_controller_offset,
    post_click_normalized,
)


def main() -> int:
    FG_STEAL_DIR.mkdir(parents=True, exist_ok=True)
    install_foreground_safe_inputs()
    hwnd = find_window()
    before = _foreground_window()
    before_title = _window_title(before)
    controller = Win32Controller(
        hWnd=hwnd,
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    result = {
        "before_hwnd": before,
        "before_title": before_title,
        "game_hwnd": int(hwnd),
    }
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa connection failed")
        measure_controller_offset(controller)
        # Harmless upper-left content pixel (not a critical UI control).
        ok = post_click_normalized(controller, 40, 80).wait().succeeded
        time.sleep(0.2)
        after = _foreground_window()
        result.update(
            {
                "click_ok": bool(ok),
                "after_hwnd": after,
                "after_title": _window_title(after),
                "foreground_unchanged": after == before,
            }
        )
    finally:
        controller.__del__()
        controller._handle = None
    out = FG_STEAL_DIR / "probe_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result.get("foreground_unchanged"):
        return 2
    if not result.get("click_ok"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""Open the farm phone once and stop at its app grid."""
import json
import time

try:
    from .configure_field_employee import (
        Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum,
        find_window, screenshot,
    )
    from .order_template_click import template_hit, click_template_node
except ImportError:
    from configure_field_employee import (
        Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum,
        find_window, screenshot,
    )
    from order_template_click import template_hit, click_template_node


def phone_menu_visible(frame):
    return template_hit(frame, 'VerifyPhoneMenuOpen') is not None


def open_phone(controller):
    frame = screenshot(controller)
    if phone_menu_visible(frame):
        time.sleep(.3)
        if phone_menu_visible(screenshot(controller)):
            return dict(status='already_open', clicks=0)
        raise RuntimeError('phone menu state not confirmed; no click')
    if template_hit(frame, 'ClickPhoneIcon') is None:
        raise RuntimeError('phone icon not recognized; no click')
    if not click_template_node(controller, 'ClickPhoneIcon', frame):
        raise RuntimeError('phone click failed')
    confirmed = 0
    for _ in range(10):
        time.sleep(.3)
        confirmed = confirmed + 1 if phone_menu_visible(screenshot(controller)) else 0
        if confirmed >= 2:
            return dict(status='opened', clicks=1)
    raise RuntimeError('phone menu not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='open_phone', **open_phone(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

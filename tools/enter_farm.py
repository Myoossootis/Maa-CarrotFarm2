"""One farm-entry click; verify the restaurant lid, never production."""
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


def farm_visible(frame):
    return (template_hit(frame, 'VerifyRestaurantEntryVisible') is not None
            and template_hit(frame, 'VerifyFarmEntryVisible') is None
            and template_hit(frame, 'ClickFarmEntryAlert') is None)


def enter_farm(controller):
    frame = screenshot(controller)
    if farm_visible(frame):
        time.sleep(.3)
        if farm_visible(screenshot(controller)):
            return dict(status='already_on_farm', clicks=0)
        raise RuntimeError('farm state not confirmed; no click')
    entry = ('ClickFarmEntry' if template_hit(frame, 'ClickFarmEntry') is not None
             else 'ClickFarmEntryAlert' if template_hit(frame, 'ClickFarmEntryAlert') is not None
             else None)
    if entry is None:
        raise RuntimeError('farm entry not recognized; no click')
    if not click_template_node(controller, entry, frame):
        raise RuntimeError('farm entry click failed')
    confirmed = 0
    for _ in range(10):
        time.sleep(.3)
        confirmed = confirmed + 1 if farm_visible(screenshot(controller)) else 0
        if confirmed >= 2:
            return dict(status='entered', clicks=1)
    raise RuntimeError('farm entry not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='enter_farm', **enter_farm(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

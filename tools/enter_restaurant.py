"""One restaurant-entry click; verify the return-to-farm icon, never orders."""
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


def restaurant_visible(frame):
    return (template_hit(frame, 'VerifyFarmEntryVisible') is not None
            or template_hit(frame, 'ClickFarmEntryAlert') is not None) and template_hit(frame, 'VerifyRestaurantEntryVisible') is None


def enter_restaurant(controller):
    frame = screenshot(controller)
    if restaurant_visible(frame):
        time.sleep(.3)
        if restaurant_visible(screenshot(controller)):
            return dict(status='already_in_restaurant', clicks=0)
        raise RuntimeError('restaurant state not confirmed; no click')
    if template_hit(frame, 'ClickRestaurantEntry') is None:
        raise RuntimeError('restaurant entry not recognized; no click')
    if not click_template_node(controller, 'ClickRestaurantEntry', frame):
        raise RuntimeError('restaurant entry click failed')
    confirmed = 0
    for _ in range(10):
        time.sleep(.3)
        confirmed = confirmed + 1 if restaurant_visible(screenshot(controller)) else 0
        if confirmed >= 2:
            return dict(status='entered', clicks=1)
    raise RuntimeError('restaurant entry not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='enter_restaurant', **enter_restaurant(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

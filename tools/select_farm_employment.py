"""Select the farm-employment tab only when its hat is gray."""
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


def employment_state(frame):
    orange = template_hit(frame, 'VerifyFarmEmploymentSelected') is not None
    gray = template_hit(frame, 'ClickFarmEmploymentTab') is not None
    if orange == gray:
        return 'unknown'
    return 'selected' if orange else 'unselected'


def select_farm_employment(controller):
    frame = screenshot(controller)
    state = employment_state(frame)
    if state == 'selected':
        time.sleep(.3)
        if employment_state(screenshot(controller)) == 'selected':
            return dict(status='already_selected', clicks=0)
        raise RuntimeError('farm employment state not confirmed; no click')
    if state != 'unselected':
        raise RuntimeError('farm employment hat state is unknown; no click')
    if not click_template_node(controller, 'ClickFarmEmploymentTab', frame):
        raise RuntimeError('farm employment tab click failed')
    confirmed = 0
    for _ in range(10):
        time.sleep(.3)
        confirmed = confirmed + 1 if employment_state(screenshot(controller)) == 'selected' else 0
        if confirmed >= 2:
            return dict(status='selected', clicks=1)
    raise RuntimeError('orange farm employment hat not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='select_farm_employment', **select_farm_employment(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

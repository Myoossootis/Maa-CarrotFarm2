"""Open employee configuration once and stop before selecting a job tab."""
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


def employee_config_visible(frame):
    return template_hit(frame, 'VerifyEmployeeConfigOpen') is not None


def open_employee_config(controller):
    frame = screenshot(controller)
    if employee_config_visible(frame):
        time.sleep(.3)
        if employee_config_visible(screenshot(controller)):
            return dict(status='already_open', clicks=0)
        raise RuntimeError('employee configuration state not confirmed; no click')
    if template_hit(frame, 'ClickEmployeeConfig') is None:
        raise RuntimeError('employee configuration tile not recognized; no click')
    if not click_template_node(controller, 'ClickEmployeeConfig', frame):
        raise RuntimeError('employee configuration click failed')
    confirmed = 0
    for _ in range(10):
        time.sleep(.3)
        confirmed = confirmed + 1 if employee_config_visible(screenshot(controller)) else 0
        if confirmed >= 2:
            return dict(status='opened', clicks=1)
    raise RuntimeError('employee configuration not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='open_employee_config', **open_employee_config(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

"""Open the visible field-1 detail once; never open its work plan."""
import json
import time

try:
    from .configure_field_employee import (
        Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum,
        employee_page_state, find_window, screenshot, slot_match, visible_slot_action,
        click,
    )
except ImportError:
    from configure_field_employee import (
        Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum,
        employee_page_state, find_window, screenshot, slot_match, visible_slot_action,
        click,
    )


def field_1_detail_visible(frame):
    return employee_page_state(frame) == 'slot_detail' and slot_match(frame, 'field_1')[0] >= .88


def open_field_1_detail(controller):
    frame = screenshot(controller)
    if field_1_detail_visible(frame):
        time.sleep(.3)
        if field_1_detail_visible(screenshot(controller)):
            return dict(status='already_open', clicks=0)
        raise RuntimeError('field-1 detail state not confirmed; no click')
    if employee_page_state(frame) != 'employee_list':
        raise RuntimeError('employee list not recognized; no click')
    action = visible_slot_action(frame, 'field_1')
    if action is None or action[1] != 'work_plan':
        raise RuntimeError('field-1 assignment entry not available; no click')
    click(controller, 560, action[0])
    confirmed = 0
    for _ in range(12):
        frame = screenshot(controller)
        confirmed = confirmed + 1 if field_1_detail_visible(frame) else 0
        if confirmed >= 2:
            return dict(status='opened', clicks=1)
        time.sleep(.3)
    raise RuntimeError('field-1 detail not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        print(json.dumps(dict(task='open_field_1_detail', **open_field_1_detail(controller)), ensure_ascii=False), flush=True)
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

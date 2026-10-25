"""Close only the restaurant offline-income panel through its OCR footer."""
import json
import re
import time

from maa.pipeline import JOCR, JRecognitionType

try:
    from .configure_field_employee import (
        RESOURCE, Win32Controller, MaaWin32ScreencapMethodEnum,
        MaaWin32InputMethodEnum, Resource, Tasker, find_window, screenshot,
    )
    from .maa_frame_normalize import post_click_normalized
except ImportError:
    from configure_field_employee import (
        RESOURCE, Win32Controller, MaaWin32ScreencapMethodEnum,
        MaaWin32InputMethodEnum, Resource, Tasker, find_window, screenshot,
    )
    from maa_frame_normalize import post_click_normalized


TITLE_ROI = (90, 120, 550, 160)
FOOTER_ROI = (100, 1150, 530, 160)
TITLE = '餐厅离线收益'
FOOTER = '点击空白区域继续'


def recognize_text(tasker, frame, roi, expected):
    detail = tasker.post_recognition(
        JRecognitionType.OCR, JOCR(roi=roi, expected=[expected], threshold=.5), frame,
    ).wait().get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    return list(recognition.filtered_results) if recognition else []


def has_text(results, expected):
    return any(expected in re.sub(r'\s+', '', result.text) for result in results)


def income_footer(tasker, frame):
    title = recognize_text(tasker, frame, TITLE_ROI, TITLE)
    if not has_text(title, TITLE):
        return None
    results = recognize_text(tasker, frame, FOOTER_ROI, FOOTER)
    for result in results:
        if not has_text([result], FOOTER) or len(result.box) != 4:
            continue
        x, y, width, height = map(int, result.box)
        rx, ry, rw, rh = FOOTER_ROI
        if width > 0 and height > 0 and rx <= x and ry <= y and x + width <= rx + rw and y + height <= ry + rh:
            return x + width // 2, y + height // 2
    raise RuntimeError('offline income title found but blank-area footer not verified; no click')


def income_present(tasker, frame):
    return (has_text(recognize_text(tasker, frame, TITLE_ROI, TITLE), TITLE)
            or has_text(recognize_text(tasker, frame, FOOTER_ROI, FOOTER), FOOTER))


def dismiss_offline_income(controller, tasker):
    point = income_footer(tasker, screenshot(controller))
    if point is None:
        return False
    if not post_click_normalized(controller, *point).wait().succeeded:
        raise RuntimeError('offline income footer click failed')
    absent_frames = 0
    for _ in range(5):
        time.sleep(.3)
        absent_frames = 0 if income_present(tasker, screenshot(controller)) else absent_frames + 1
        if absent_frames >= 2:
            return True
    raise RuntimeError('offline income dismissal not confirmed; stopped without a second click')


def main():
    controller = Win32Controller(hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage)
    resource, tasker = Resource(), Tasker()
    try:
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(4)
        tasker.set_log_dir('')
        tasker.set_stdout_level(2)
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        if not resource.post_bundle(RESOURCE).wait().succeeded or not tasker.bind(resource, controller):
            raise RuntimeError('Maa OCR resource binding failed')
        closed = dismiss_offline_income(controller, tasker)
        print(json.dumps(dict(task='close_offline_income', status='closed' if closed else 'not_present',
                              clicks=1 if closed else 0), ensure_ascii=False), flush=True)
        return 0
    finally:
        tasker.clear_cache()
        for value in (tasker, resource, controller):
            value.__del__()
            value._handle = None


if __name__ == '__main__':
    raise SystemExit(main())

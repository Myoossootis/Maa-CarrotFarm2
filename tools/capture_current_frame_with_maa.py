"""Save one current 720-short-side Maa FramePool screenshot without input.

`--stream` keeps that connection open and writes preview frames to stdout.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum


OUTPUT = Path(".tmp/restaurant_capture/restaurant_loaded.png")
PREVIEW_FPS = 8
PREVIEW_MAX_WIDTH = 144


def find_window() -> int:
    found = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == "萝卜家园2" and class_name.value == "Chrome_WidgetWin_0":
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(found) != 1:
        raise RuntimeError(f"expected one game window, found {len(found)}")
    return found[0]


def encode_preview(image, max_width=PREVIEW_MAX_WIDTH):
    """Downscale a BGR frame to a PPM the HUD can display without another decode."""
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / width
        image = cv2.resize(image, (max_width, max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    header = f'P6\n{rgb.shape[1]} {rgb.shape[0]}\n255\n'.encode('ascii')
    return header + rgb.tobytes()


def write_packet(stream, kind, payload):
    stream.write(kind)
    stream.write(len(payload).to_bytes(4, 'little'))
    stream.write(payload)
    stream.flush()


def stream_frames(controller, stream, *, fps=PREVIEW_FPS, max_width=PREVIEW_MAX_WIDTH):
    """Reuse one FramePool connection. Stop when the HUD closes stdout."""
    interval = 1 / fps
    while True:
        started = time.perf_counter()
        try:
            image = controller.post_screencap().wait().get()
            if image is None:
                raise RuntimeError('empty frame')
            write_packet(stream, b'F', encode_preview(image, max_width))
        except BrokenPipeError:
            return
        except Exception as error:
            try:
                write_packet(stream, b'E', str(error).encode('utf-8', 'replace')[:400])
            except BrokenPipeError:
                return
            time.sleep(1)
            continue
        leftover = interval - (time.perf_counter() - started)
        if leftover > 0:
            time.sleep(leftover)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument('--stream', action='store_true', help='write paced preview frames to stdout')
    parser.add_argument('--fps', type=int, default=PREVIEW_FPS)
    parser.add_argument('--max-width', type=int, default=PREVIEW_MAX_WIDTH)
    args = parser.parse_args()
    if not 1 <= args.fps <= 15 or not 96 <= args.max_width <= 720:
        parser.error('preview fps must be 1..15 and max-width 160..720')
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    try:
        controller.set_screenshot_target_short_side(720)
        controller.post_connection().wait()
        if args.stream:
            stream_frames(controller, sys.stdout.buffer, fps=args.fps, max_width=args.max_width)
            return 0
        job = controller.post_screencap().wait()
        image = job.get()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.output), image):
            raise RuntimeError(f"failed to save screenshot: {args.output}")
        print(f"{args.output}: {image.shape[1]}x{image.shape[0]}")
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

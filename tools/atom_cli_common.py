"""Shared bootstrap for Track-A atomic navigation CLIs.

Ensures foreground-safe input is installed even when a tool is launched
outside ui_worker, and writes JSON evidence under .tmp/track_a/.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / '.tmp' / 'track_a'


def install_safe_inputs() -> None:
    """Hard-block native Maa post_click/swipe; use render-child PostMessage only."""
    from maa_frame_normalize import install_foreground_safe_inputs

    install_foreground_safe_inputs()


def evidence_dir(task: str, out: Path | None = None) -> Path:
    base = Path(out) if out is not None else DEFAULT_EVIDENCE_ROOT / task
    base.mkdir(parents=True, exist_ok=True)
    return base


def write_evidence(task: str, payload: dict[str, Any], out: Path | None = None) -> Path:
    folder = evidence_dir(task, out)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = folder / f'{stamp}_{task}.json'
    body = {
        'task': task,
        'written_at': datetime.now().isoformat(timespec='seconds'),
        'fg_safe_inputs': True,
        **payload,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    latest = folder / 'latest.json'
    latest.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    return path


def add_out_argument(parser) -> None:
    parser.add_argument(
        '--out',
        type=Path,
        default=None,
        help='Evidence directory (default: .tmp/track_a/<task>/)',
    )


def connect_controller():
    """Connect Win32 FramePool controller for standalone atom CLIs."""
    try:
        from configure_field_employee import (
            MaaWin32InputMethodEnum,
            MaaWin32ScreencapMethodEnum,
            Win32Controller,
            find_window,
        )
    except ImportError:
        from .configure_field_employee import (
            MaaWin32InputMethodEnum,
            MaaWin32ScreencapMethodEnum,
            Win32Controller,
            find_window,
        )

    install_safe_inputs()
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    controller.set_screenshot_target_short_side(720)
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError('Maa connection failed')
    return controller


def release_controller(controller) -> None:
    controller.__del__()
    controller._handle = None

"""Cooperative cancellation for the isolated Maa UI worker and its children."""

import os
import atexit
import ctypes
from pathlib import Path
import time
from contextlib import contextmanager
import threading
import sys

sys.modules.setdefault('task_control', sys.modules[__name__])
sys.modules.setdefault('tools.task_control', sys.modules[__name__])


CANCEL_ENV = "CARROT_UI_CANCEL_FILE"
RUN_CANCEL_ENV = 'CARROT_UI_RUN_CANCEL_FILE'
_sleep = time.sleep
_parent_handle = None
PAUSE_ENV = 'CARROT_UI_PAUSE_FILE'
_input_state = threading.local()


class TaskCancelled(BaseException):
    """Bypass recognition retries, while still running resource finalizers."""


def check_cancelled():
    _check_stop()
    filename = os.environ.get(PAUSE_ENV)
    if not filename or getattr(_input_state, 'depth', 0):
        return
    pause = Path(filename)
    acknowledgment = pause.parent / 'workers' / f'{os.getpid()}.paused'
    try:
        while pause.exists():
            _check_stop()
            acknowledgment.parent.mkdir(parents=True, exist_ok=True)
            acknowledgment.touch(exist_ok=True)
            _sleep(.05)
    finally:
        acknowledgment.unlink(missing_ok=True)


def _check_stop():
    for name in (RUN_CANCEL_ENV, CANCEL_ENV):
        filename = os.environ.get(name)
        if filename and Path(filename).exists():
            raise TaskCancelled("Maa UI requested stop")
    if os.environ.get('CARROT_UI_PARENT_PID') and _parent_has_exited():
        raise TaskCancelled('Maa UI Agent exited')


@contextmanager
def atomic_input():
    """Finish releasing a mouse press before acknowledging a pause."""
    check_cancelled()
    _input_state.depth = getattr(_input_state, 'depth', 0) + 1
    try:
        yield
    finally:
        _input_state.depth -= 1
    check_cancelled()


def _parent_has_exited():
    """Hold a process handle, so PID reuse cannot keep an orphan worker alive."""
    global _parent_handle
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    if _parent_handle is None:
        _parent_handle = kernel32.OpenProcess(0x00100000, False, int(os.environ['CARROT_UI_PARENT_PID']))
        if not _parent_handle:
            return True
        atexit.register(kernel32.CloseHandle, _parent_handle)
    return kernel32.WaitForSingleObject(_parent_handle, 0) != 0x102  # WAIT_TIMEOUT = still alive


def interruptible_sleep(seconds):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        _sleep(min(.1, remaining))


def install_interruptible_waits():
    # Only called inside the dedicated worker, not in the UI/Agent process.
    time.sleep = interruptible_sleep
    filename = os.environ.get(PAUSE_ENV)
    if filename:
        workers = Path(filename).parent / 'workers'
        workers.mkdir(parents=True, exist_ok=True)
        marker = workers / f'{os.getpid()}.active'
        marker.touch()
        def unregister():
            marker.unlink(missing_ok=True)
            marker.with_suffix('.paused').unlink(missing_ok=True)
        atexit.register(unregister)

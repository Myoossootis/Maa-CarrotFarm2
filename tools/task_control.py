"""Cooperative cancellation for the isolated Maa UI worker and its children."""

import os
import atexit
import ctypes
from pathlib import Path
import time


CANCEL_ENV = "CARROT_UI_CANCEL_FILE"
_sleep = time.sleep
_parent_handle = None


class TaskCancelled(BaseException):
    """Bypass recognition retries, while still running resource finalizers."""


def check_cancelled():
    filename = os.environ.get(CANCEL_ENV)
    if filename and Path(filename).exists():
        raise TaskCancelled("Maa UI requested stop")
    if os.environ.get('CARROT_UI_PARENT_PID') and _parent_has_exited():
        raise TaskCancelled('Maa UI Agent exited')


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

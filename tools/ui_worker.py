"""Framework-mode process: AgentServer cannot create a local Resource/Controller.

Use the existing CLI implementations, with bounded UI-side output and shared
cancellation, instead of duplicating recognition/production rules in the Agent.
"""

import argparse
from contextlib import contextmanager
import importlib.metadata
import os
from pathlib import Path
import runpy
import sys

sys.modules.setdefault('ui_worker', sys.modules[__name__])
sys.modules.setdefault('tools.ui_worker', sys.modules[__name__])

try:
    from .task_control import TaskCancelled, check_cancelled, install_interruptible_waits
except ImportError:
    from task_control import TaskCancelled, check_cancelled, install_interruptible_waits


ROOT = Path(__file__).resolve().parents[1]
_workflow_depth = 0


def inherits_cycle_lock():
    owner = os.environ.get('CARROT_UI_CYCLE_OWNER')
    if not owner or not owner.isdecimal():
        return False
    # Windows venv launchers can add a process between coordinator and worker.
    # Read process ancestry only; do not interpret game state or touch memory.
    try:
        from .read_only_memory import PROCESSENTRY32, k
    except ImportError:
        from read_only_memory import PROCESSENTRY32, k
    import ctypes
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise RuntimeError('Cannot verify UI lock owner ancestry')
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        ok = k.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = k.Process32NextW(handle, ctypes.byref(entry))
    finally:
        k.CloseHandle(handle)
    ancestor = parents.get(os.getpid())
    visited = set()
    while ancestor and ancestor not in visited:
        if ancestor == int(owner):
            return True
        visited.add(ancestor)
        ancestor = parents.get(ancestor)
    return False


@contextmanager
def cycle_lock():
    import msvcrt
    lock_path = ROOT / '.tmp' / 'maa_ui_cycle.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('another UI cycle is already running') from error
        previous_owner = os.environ.get('CARROT_UI_CYCLE_OWNER')
        os.environ['CARROT_UI_CYCLE_OWNER'] = str(os.getpid())
        try:
            yield
        finally:
            if previous_owner is None:
                os.environ.pop('CARROT_UI_CYCLE_OWNER', None)
            else:
                os.environ['CARROT_UI_CYCLE_OWNER'] = previous_owner
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def install_safe_inputs():
    from maa.tasker import Tasker
    import maa_frame_normalize as frames

    Tasker.set_log_dir('')
    Tasker.set_stdout_level(2)
    # Single FG-safe gate for all Win32Controller input methods.
    frames.install_foreground_safe_inputs()
    def block_native_task(self, entry, *args, **kwargs):
        check_cancelled()
        raise RuntimeError(f'Maa UI stopped: native task {entry}; use normalized-frame recognition')

    Tasker.post_task = block_native_task

    def source_bundle(offset):
        check_cancelled()
        width, height = frames._LAST_FRAME_WH
        valid = (type(offset) is int and 0 <= offset <= frames.MAX_TOP_LETTERBOX
                 and offset == frames.get_top_offset() and width == frames.EXPECTED_WIDTH
                 and (abs(height - frames.EXPECTED_HEIGHT) <= 16 if offset == 0
                      else height - offset == frames.EXPECTED_HEIGHT))
        if not valid:
            raise RuntimeError('Maa UI stopped: unvalidated screenshot dimensions or crop')
        # All worker recognition now receives normalized images explicitly;
        # native tasks that capture raw frames are blocked above.
        return frames.SOURCE_RESOURCE

    frames.build_runtime_resource = source_bundle


@contextmanager
def workflow_lock():
    """Allow only a verified child or this module's own nested workflow."""
    global _workflow_depth
    if _workflow_depth or inherits_cycle_lock():
        yield
        return
    with cycle_lock():
        _workflow_depth += 1
        try:
            yield
        finally:
            _workflow_depth -= 1


def dispatch(args, forwarded):
    if args.script == 'cycle':
        import run_short_cycle
        sys.argv = ['run_short_cycle.py', *forwarded]
        return run_short_cycle.main(startup_confirmed=True)
    if args.script == 'run_daily_orders.py':
        import run_daily_orders
        sys.argv = ['run_daily_orders.py', *forwarded]
        return run_daily_orders.main(startup_confirmed=True)
    if args.script == 'run_feed_production.py':
        import run_feed_production
        sys.argv = [args.script, *forwarded]
        return run_feed_production.main()
    if args.script == 'refresh_order.py':
        import refresh_order
        sys.argv = [args.script, *forwarded]
        return refresh_order.main()
    sys.argv = [args.script, *forwarded]
    runpy.run_path(str(ROOT / 'tools' / args.script), run_name='__main__')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', choices=['reconcile_production.py', 'run_manual_planting.py', 'run_feed_production.py', 'cycle', 'ensure_ranch_feed.py', 'check_first_orders.py', 'refresh_order.py', 'complete_normal_orders.py', 'complete_batch_orders.py', 'run_employee_order_plan.py', 'monitor_employee_products.py', 'dismiss_offline_income.py', 'enter_restaurant.py', 'enter_farm.py', 'open_phone.py', 'open_employee_config.py', 'select_farm_employment.py', 'open_field_detail.py', 'return_employee_list.py', 'locate_field_2.py', 'open_work_plan.py', 'close_work_plan.py', 'verify_slot_work_plan.py', 'run_daily_orders.py', 'run_normal_order_loop.py'])
    args, forwarded = parser.parse_known_args()
    if inherits_cycle_lock():
        os.environ.setdefault('CARROT_UI_PARENT_PID', os.environ['CARROT_UI_CYCLE_OWNER'])
    if importlib.metadata.version('MaaFw') != '5.13.1':
        raise RuntimeError('Maa UI worker requires MaaFw 5.13.1; use the project .venv')
    os.chdir(ROOT)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    os.environ['CARROT_STATE_READER'] = 'decoded'
    print('[数据源] 库存、订单、员工计划使用只读解码；页面定位与点击沿用原 Maa 方法。', flush=True)
    sys.path.insert(0, str(ROOT))
    install_interruptible_waits()
    install_safe_inputs()
    with workflow_lock():
        check_cancelled()
        return dispatch(args, forwarded)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except TaskCancelled:
        print('Maa UI worker cancelled; saved plans are not reset', flush=True)
        raise SystemExit(130)

"""Daily order batch for Maa checkboxes: party/takeout/normal.

UI only exposes feature checkboxes. Built-in counts:
  party/takeout -> up to 12/day; single tasks yield during cooldown
  --order-types -> run both types before waiting for earliest cooldown
  normal        -> one round per invocation; no daily quota

Philosophy: click=success; clear Chinese logs on every phase / fail / skip.
Never silent-wait >5s without a heartbeat line.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.production_start import confirm_cycle_start
from agent.order_preferences import validate_allowed_products

try:
    from .order_quota import (
        ORDER_TYPES,
        TYPE_LABELS,
        can_start,
    )
    from .runtime_state import write_json
    from .task_control import TaskCancelled, check_cancelled, interruptible_sleep
except ImportError:
    from order_quota import (
        ORDER_TYPES,
        TYPE_LABELS,
        can_start,
    )
    from runtime_state import write_json
    from task_control import TaskCancelled, check_cancelled, interruptible_sleep

try:
    from . import run_short_cycle as short_cycle
except ImportError:
    import run_short_cycle as short_cycle

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / ".tmp" / "daily_orders"
MONITOR_DIR = ROOT / ".tmp" / "maa_monitor"
LABELS = {
    "party": "聚会订单",
    "takeout": "外卖订单",
    "normal": "普通订单",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _log_error(msg: str, *, exc: BaseException | None = None) -> None:
    _log(f"[错误] {msg}")
    if exc is not None:
        _log(f"[错误原因] {type(exc).__name__}: {exc}")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for line in tb.strip().splitlines()[-12:]:
            _log(f"  {line}")


def _normalize_type(raw: str) -> str:
    key = (raw or "").strip().lower()
    aliases = {
        "party": "party",
        "juhui": "party",
        "聚会": "party",
        "聚会订单": "party",
        "takeout": "takeout",
        "waimai": "takeout",
        "外卖": "takeout",
        "外卖订单": "takeout",
        "normal": "normal",
        "普通": "normal",
        "普通订单": "normal",
    }
    if key not in aliases:
        raise ValueError(f"未知订单类型 {raw!r}，请用 party/takeout/normal")
    return aliases[key]


def _wait_cooldown(order_type: str, next_available: str | None) -> None:
    label = LABELS[order_type]
    if not next_available:
        raise RuntimeError(f"{label} 冷却缺少 next_available")
    try:
        target = datetime.fromisoformat(next_available)
        if target.tzinfo is None:
            from datetime import timedelta
            TZ = timezone(timedelta(hours=8))

            target = target.replace(tzinfo=TZ)
    except ValueError as exc:
        raise RuntimeError(f"{label} 无法解析冷却时间 {next_available!r}") from exc
    from datetime import timedelta
    TZ = timezone(timedelta(hours=8))

    now = datetime.now(TZ)
    seconds = max(0, int((target.astimezone(TZ) - now).total_seconds()))
    _log(
        f"[冷却] {label} 本批满 6 单，等待约 {seconds // 60} 分 {seconds % 60} 秒"
        f"（下次可开 {next_available}）。监控帧目录：{MONITOR_DIR}"
    )
    remaining = seconds
    while remaining > 0:
        check_cancelled()
        step = min(5, remaining)
        interruptible_sleep(step)
        remaining -= step
        if remaining > 0:
            _log(f"[冷却] {label} 还剩约 {remaining // 60} 分 {remaining % 60} 秒…")


def _run_one_short(
    *,
    order_type: str,
    apply: bool,
    poll: int,
    timeout: int,
    startup_confirmed: bool,
) -> dict:
    """Invoke one short-cycle round; returns summary-ish dict."""
    argv = [
        "run_short_cycle.py",
        "--order-type",
        order_type,
        "--rounds",
        "1",
        "--poll-seconds",
        str(poll),
        "--timeout-seconds",
        str(timeout),
        "--skip-checklist",
    ]
    if apply:
        argv.append("--apply")
    old = sys.argv
    try:
        sys.argv = argv
        _log(f"[日常] 进入短循环 type={order_type} apply={apply} poll={poll}s timeout={timeout}s")
        code = short_cycle.main(startup_confirmed=startup_confirmed)
    finally:
        sys.argv = old
    summary_path = short_cycle.TMP / "latest_summary.json"
    summary: dict = {}
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log_error(f"无法读取短循环摘要 {summary_path}", exc=exc)
    else:
        _log(f"[日常] 短循环未写出摘要文件：{summary_path}")
    summary["_exit_code"] = code
    _log(
        f"[日常] 短循环结束 exit={code} status={summary.get('status')!r} "
        f"accepted={_accepted(summary)}"
    )
    return summary


def _accepted(summary: dict) -> bool:
    rounds = summary.get("rounds") or []
    if not rounds:
        return False
    last = rounds[-1]
    return bool(last.get("accepted"))


def _no_orders(summary: dict) -> bool:
    status = summary.get("status")
    if status == "no_actionable_orders":
        return True
    rounds = summary.get("rounds") or []
    if rounds and rounds[-1].get("status") == "no_actionable_orders":
        return True
    return False


def _quota_blocked(summary: dict) -> bool:
    return summary.get("status") == "quota_blocked"


def first_order_arguments(order_type, allowed_products=None):
    arguments = ['--order-type', order_type]
    if allowed_products is not None:
        arguments.extend(['--allowed-products', *sorted(allowed_products)])
    return arguments


def run_daily(
    order_type: str,
    *,
    apply: bool,
    poll: int,
    timeout: int,
    startup_confirmed: bool = False,
    allowed_products=None,
) -> int:
    order_type = _normalize_type(order_type)
    label = LABELS[order_type]
    TMP.mkdir(parents=True, exist_ok=True)
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    if apply and not startup_confirmed and not confirm_cycle_start():
        _log_error(f"{label} 未确认清地，已取消。")
        return 2
    done = 0
    status = "dry_run" if not apply else "no_actionable_orders"
    quota = None
    try:
        while True:
            check_cancelled()
            if apply and done:
                # Claiming an order can generate a new slot-1 recipe. Recheck
                # it before the next round, including the next quota decision.
                short_cycle.run_script('check_first_orders.py', *first_order_arguments(order_type, allowed_products))
            if order_type in ORDER_TYPES:
                gate = can_start(order_type)
                quota = gate.as_dict()
                if not gate.can_start:
                    status = "cooldown" if gate.in_cooldown else "daily_cap_reached"
                    _log(f"[跳过] 类型={label} status={status} 今日={gate.completed_today}/{gate.daily_cap} "
                         f"next_available={gate.next_available}；让出队列执行其他订单。")
                    break
            _log(f"[日常] 类型={label} 本次已完成={done}，开始扫单/排产/监控/交单")
            summary = _run_one_short(order_type=order_type, apply=apply, poll=poll,
                                     timeout=timeout, startup_confirmed=True)
            code = int(summary.get("_exit_code") or 0)
            if code != 0:
                raise RuntimeError(f"短循环失败 exit={code} status={summary.get('status')}；"
                                   f"证据={short_cycle.TMP / 'latest_summary.json'}")
            if not apply and summary.get("status") == "dry_run":
                status = "dry_run"
                break
            if _quota_blocked(summary):
                status = "quota_blocked"
                quota = summary.get("quota")
                _log(f"[跳过] 类型={label} 配额暂不可用 next_available={(quota or {}).get('next_available')}")
                break
            if _no_orders(summary):
                status = "no_actionable_orders"
                _log(f"[跳过] 类型={label} 无可执行订单；详情={short_cycle.TMP / 'latest_summary.json'}")
                break
            if not _accepted(summary):
                raise RuntimeError(f"短循环未确认交单 status={summary.get('status')}")
            last = summary['rounds'][-1]
            batch = int(last.get('batch_count') or (6 if order_type in ORDER_TYPES else 1))
            done += batch
            _log(f"[交单] 类型={label} 结果=成功 本批={batch} 本跑={done}；配额仅由短循环记账")
            status = "complete"
            if order_type == "normal":
                # Normal orders have no daily quota. Continuous mode is future work.
                break
    except TaskCancelled:
        write_json(TMP / f"latest_{order_type}.json", {
            "status": "cancelled", "order_type": order_type, "done_this_run": done,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        raise
    except Exception as exc:
        _log_error(f"类型={label} 阶段=daily_cycle；证据={short_cycle.TMP / 'latest_summary.json'}", exc=exc)
        write_json(TMP / f"latest_{order_type}.json", {
            "status": "failed", "order_type": order_type, "error": str(exc),
            "traceback": traceback.format_exc(), "done_this_run": done,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        return 2
    if order_type in ORDER_TYPES:
        quota = can_start(order_type).as_dict()
    final = {
        "status": status, "order_type": order_type, "done_this_run": done,
        "quota": quota, "next_available": (quota or {}).get("next_available"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(TMP / f"latest_{order_type}.json", final)
    _log(f"[结束] 类型={label} status={status} 本跑={done} next_available={final['next_available']}")
    return 0


def run_scheduled(order_types, *, apply, poll, timeout, startup_confirmed=False, allowed_products=None):
    """Run selected batch types before waiting for the earliest cooldown."""
    pending = list(dict.fromkeys(_normalize_type(kind) for kind in order_types))
    if any(kind not in ORDER_TYPES for kind in pending):
        raise ValueError("联合调度只支持聚会/外卖；普通订单使用单独入口")
    if apply and not startup_confirmed and not confirm_cycle_start():
        return 2
    failed = False
    while pending:
        cooling = []
        for kind in list(pending):
            check_cancelled()
            gate = can_start(kind)
            if gate.completed_today >= gate.daily_cap:
                pending.remove(kind)
                continue
            if gate.in_cooldown and apply:
                cooling.append((kind, gate.next_available))
                continue
            code = run_daily(kind, apply=apply, poll=poll, timeout=timeout, startup_confirmed=True,
                             allowed_products=allowed_products)
            result = json.loads((TMP / f"latest_{kind}.json").read_text(encoding="utf-8"))
            if code:
                failed = True
                pending.remove(kind)
                _log_error(f"{LABELS[kind]} 已停止；继续检查其他订单。")
            elif result['status'] in {'cooldown', 'quota_blocked'} and apply:
                cooling.append((kind, result.get('next_available')))
            else:
                pending.remove(kind)
        if pending and cooling and apply:
            kind, next_time = min(cooling, key=lambda item: item[1] or '')
            if not next_time:
                raise RuntimeError(f"{kind} 冷却缺少 next_available")
            _wait_cooldown(kind, next_time)
    return 2 if failed else 0


def main(*, startup_confirmed: bool = False) -> int:
    parser = argparse.ArgumentParser(description="Maa 日常订单勾选入口")
    parser.add_argument(
        "--order-type",
        default=None,
        help="party / takeout / normal（或中文聚会/外卖/普通）",
    )
    parser.add_argument("--order-types", nargs="+", choices=ORDER_TYPES, help="联合调度，例如 party takeout")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument('--allowed-products', nargs='*', default=None, help='一号位允许品类，默认使用 Maa 勾选预设')
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.allowed_products is not None:
        try:
            args.allowed_products = validate_allowed_products(args.allowed_products)
        except ValueError as error:
            parser.error(str(error))
    if not 5 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds 须在 5..300")
    if args.timeout_seconds < args.poll_seconds or args.timeout_seconds > 7200:
        parser.error("--timeout-seconds 须 >= poll 且 <= 7200")
    if bool(args.order_type) == bool(args.order_types):
        parser.error("--order-type 与 --order-types 必须且只能选择一个")
    try:
        # Run before quota/cooldown checks, production, or delivery. The joint
        # task checks both tabs first so a long party cycle cannot delay takeout.
        if args.apply and not startup_confirmed:
            if not confirm_cycle_start():
                return 2
            startup_confirmed = True
        types = args.order_types or [_normalize_type(args.order_type)]
        ready = []
        startup_failed = False
        for kind in types:
            if args.apply:
                try:
                    short_cycle.run_script('check_first_orders.py', *first_order_arguments(kind, args.allowed_products))
                except Exception as exc:
                    startup_failed = True
                    _log_error(f'{LABELS[kind]} 一号位检查失败，停止该类订单', exc=exc)
                    write_json(TMP / f'latest_{kind}.json', dict(status='failed', stage='first_order_policy',
                        order_type=kind, error=str(exc), done_this_run=0))
                    continue
            ready.append(kind)
        if not ready:
            return 2
        if args.order_types:
            code = run_scheduled(ready, apply=args.apply, poll=args.poll_seconds,
                                 timeout=args.timeout_seconds, startup_confirmed=startup_confirmed,
                                 allowed_products=args.allowed_products)
            return 2 if startup_failed else code
        return run_daily(
            ready[0],
            apply=args.apply,
            poll=args.poll_seconds,
            timeout=args.timeout_seconds,
            startup_confirmed=startup_confirmed,
            allowed_products=args.allowed_products,
        )
    except TaskCancelled:
        _log("[停止] 日常任务被取消。")
        return 2
    except Exception as exc:
        _log_error("日常任务未捕获异常", exc=exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

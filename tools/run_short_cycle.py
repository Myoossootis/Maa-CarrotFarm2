"""Bounded short cycle: scan/submit -> plan -> monitor -> rematch submit.

Prefer this over run_order_cycle.py. Each round writes checkpoints under
.tmp/track_b/ (Track B evidence). Production submit rematches by shortage
crops (not a frozen order id). Employee plans default to one-shot (repeat
checkbox left unchecked).

Dry-run (default, no --apply): prints the step plan, writes
.tmp/track_b/dry_run_plan.json, and exits 0 without connecting to the game.
--apply: executes scan -> plan -> monitor -> rematch complete after the
land-clearance dialog (unless startup_confirmed=True from the UI worker).

Does not minimize the game window. All clicks go through
post_click_normalized (foreground-safe); never Maa native post_click.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Direct CLI execution starts with tools/ on sys.path, not the repository root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.production_start import confirm_cycle_start

try:
    from .order_quota import ORDER_TYPES, TYPE_LABELS, all_status, can_start, record_completion
except ImportError:
    from order_quota import ORDER_TYPES, TYPE_LABELS, all_status, can_start, record_completion

try:
    from .order_board_helpers import print_startup_checklist
    from .production_targets import shortage_focus_was_submitted, target_shortage_names
    from .runtime_state import write_json
    from .task_control import TaskCancelled, check_cancelled
    from .order_board_availability import WAITING_STATES as BOARD_WAITING_STATES
except ImportError:
    from order_board_helpers import print_startup_checklist
    from production_targets import shortage_focus_was_submitted, target_shortage_names
    from runtime_state import write_json
    from task_control import TaskCancelled, check_cancelled
    from order_board_availability import WAITING_STATES as BOARD_WAITING_STATES


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
# Track B evidence root (also used as live checkpoint root).
TMP = ROOT / ".tmp" / "track_b"
# Legacy alias kept for readers grepping older docs.
LEGACY_TMP = ROOT / ".tmp" / "short_cycle"
COMPLETION = ROOT / "assets/catalog/normal_orders/latest_completion.json"
ASSIGNMENT = ROOT / "assets/catalog/employee_work/latest_assignment.json"
MONITOR = ROOT / "assets/catalog/employee_work/latest_monitor.json"

DRY_RUN_STEPS = (
    "1. scan: complete_normal_orders.py --prepare-only OR complete_batch_orders.py --order-type party|takeout --prepare-only",
    "2. plan: run_employee_order_plan.py --apply (one-shot plans only; idle slots)",
    "3. monitor: monitor_employee_products.py --apply (collect only when left>=right twice)",
    "4. submit: complete_*_orders.py --target-file <round>/submit_target.json (shortage rematch; party/takeout batch=6)",
)


# normal -> complete_normal_orders; party/takeout -> complete_batch_orders (same board, tab switch).
ORDER_BOARD_IMPLEMENTED = frozenset({"normal", "party", "takeout"})
ORDER_TYPE_CN = {"normal": "普通订单", "takeout": "外卖订单", "party": "聚会订单"}


def _cn_type(order_type: str) -> str:
    return ORDER_TYPE_CN.get(order_type, order_type)


def log_order_snapshot(order_type: str, target: dict | None, *, phase: str) -> None:
    """Chinese order lines for MFA log (type / id / items / shortage)."""
    label = _cn_type(order_type)
    if not target:
        print(f"[订单] 类型={label} 阶段={phase}：无目标订单", flush=True)
        return
    order_no = target.get("order")
    items = target.get("items") or []
    missing = target.get("missing_items") or []
    print(f"[订单] 类型={label} 订单号={order_no} 阶段={phase}", flush=True)
    if items:
        parts = []
        for it in items:
            name = it.get("name", "?")
            req = it.get("required", "?")
            act = it.get("actual", "?")
            miss = it.get("missing")
            if miss is None and isinstance(req, int) and isinstance(act, int):
                miss = max(0, req - act)
            parts.append(f"{name} 需{req}/有{act}/缺{miss}")
        print(f"[订单] 需求：{'；'.join(parts)}", flush=True)
    else:
        print("[订单] 需求：（空）", flush=True)
    if missing:
        miss_parts = [f"{m.get('name')}×{m.get('missing')}" for m in missing]
        print(f"[订单] 缺口：{'；'.join(miss_parts)}", flush=True)
    else:
        print("[订单] 缺口：无", flush=True)
    prod = target.get("production_targets") or []
    if prod:
        prod_parts = [
            f"{p.get('name')}×{p.get('needed')}({p.get('route') or p.get('id')})"
            for p in prod
        ]
        print(f"[订单] 待产：{'；'.join(prod_parts)}", flush=True)


def log_assignment_snapshot(assignment: dict) -> None:
    """Chinese employee arrangement lines for MFA log."""
    saved = assignment.get("saved") or []
    status = assignment.get("status")
    print(f"[员工安排] status={status} 已保存计划={len(saved)} 条", flush=True)
    if assignment.get("error"):
        print(f"[员工安排] 错误：{assignment.get('error')}", flush=True)
    for job in saved:
        slot_id = job.get("slot_id") or "?"
        slot = job.get("slot") or slot_id
        items = job.get("items") or {}
        if isinstance(items, dict) and items:
            detail = "、".join(f"{name}×{qty}" for name, qty in items.items())
        else:
            detail = str(items) if items else "（空）"
        print(
            f"[员工安排] 槽位={slot}({slot_id}) 作物={detail} "
            f"one_shot={job.get('one_shot')} verified={job.get('verified')}",
            flush=True,
        )
    if not saved:
        print("[员工安排] 本轮无需排产或未写入计划", flush=True)


def log_submit_snapshot(order_type: str, *, accepted: bool, submit: dict, target: dict | None) -> None:
    label = _cn_type(order_type)
    order_no = None if not target else target.get("order")
    result = "成功" if accepted else "未确认成功"
    print(
        f"[交单] 类型={label} 订单号={order_no} 结果={result} "
        f"submit_status={submit.get('status')} submitted_count={submit.get('submitted_count')}",
        flush=True,
    )
    if submit.get("submitted"):
        for entry in submit.get("submitted") or []:
            print(f"[交单] 已交：订单{entry.get('order')} reason={entry.get('reason')}", flush=True)
    if not accepted:
        print(
            f"[交单] 缺口仍未满足或缺货重匹配失败；详见 {COMPLETION}",
            flush=True,
        )


def refuse_unimplemented_board(order_type: str) -> int:
    """Hard-fail party/takeout until their boards are wired. Never fall back to normal."""
    label = _cn_type(order_type)
    print(
        f"[错误] {label} 的游戏内订单板入口尚未接入"
        f"（当前只有普通订单板 open_normal_orders / complete_normal_orders）。",
        flush=True,
    )
    print(
        "[错误] 不会套用普通订单流程。请先只勾「普通订单」，"
        "或等聚会/外卖板入口（手机页签/餐厅入口/模板）做好后再跑。",
        flush=True,
    )
    TMP.mkdir(parents=True, exist_ok=True)
    write_json(
        TMP / "latest_summary.json",
        {
            "status": "order_board_not_implemented",
            "order_type": order_type,
            "label": label,
            "implemented_boards": sorted(ORDER_BOARD_IMPLEMENTED),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return 2


def _safe_print(text: str) -> None:
    """Print child output without crashing on Windows GBK consoles."""
    if not text:
        return
    try:
        print(text.rstrip(), flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.rstrip().encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


def run_script(name: str, *arguments: str) -> None:
    """Run a tools/*.py step; stream stdout line-by-line into Maa log."""
    check_cancelled()
    env = dict(**__import__("os").environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if os.environ.get("CARROT_UI_CANCEL_FILE"):
        cmd = [sys.executable, "-u", str(TOOLS / "ui_worker.py"), name, *arguments]
    else:
        cmd = [sys.executable, "-u", str(TOOLS / name), *arguments]
    print(f"[日常] 子步骤启动：{name} {' '.join(arguments)}", flush=True)
    log_dir = ROOT / '.tmp' / 'step_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_') + name + '.log')
    print(f"[日志] 子步骤完整日志：{log_path}", flush=True)
    process = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    try:
        with log_path.open('w', encoding='utf-8') as log:
            log.write(f'command={cmd!r}\n')
            for line in process.stdout:
                check_cancelled()
                log.write(line)
                log.flush()
                _safe_print(line)
    finally:
        process.stdout.close()
        code = process.wait()
    check_cancelled()
    if code:
        raise RuntimeError(f"{name} failed ({code}); 完整日志={log_path}")
    print(f"[日常] 子步骤完成：{name}", flush=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checkpoint(round_dir: Path, name: str, payload: dict) -> None:
    round_dir.mkdir(parents=True, exist_ok=True)
    write_json(round_dir / name, payload)


def write_dry_run_plan(args: argparse.Namespace) -> Path:
    """Document what --apply would do; no game connection."""
    TMP.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "dry_run",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "apply": False,
        "rounds": args.rounds,
        "max_scan_passes": args.max_scan_passes,
        "poll_seconds": args.poll_seconds,
        "timeout_seconds": args.timeout_seconds,
        "evidence_dir": str(TMP),
        "legacy_evidence_dir": str(LEGACY_TMP),
        "steps": list(DRY_RUN_STEPS),
        "semantics": {
            "without_apply": "preview only; no Win32 connect, no clicks, no land dialog",
            "with_apply": (
                "land-clearance dialog once per invocation, then scan->plan->monitor->submit; "
                "fish/unsupported whole-order skip; one-shot employee plans; "
                "collect only after two consecutive left>=right readings"
            ),
            "ui_mapping": (
                "Maa task RunShortCycle: option 周期运行模式 -> attach.ui_apply; "
                "仅预览 = dry-run; 正式执行（实验）= --apply"
            ),
        },
        "live_commands": {
            "dry_run": r".venv\Scripts\python.exe tools\run_short_cycle.py --rounds 1",
            "apply_1_round": (
                r".venv\Scripts\python.exe tools\run_short_cycle.py --apply --rounds 1 "
                r"--poll-seconds 20 --timeout-seconds 900"
            ),
            "apply_2_rounds": (
                r".venv\Scripts\python.exe tools\run_short_cycle.py --apply --rounds 2 "
                r"--poll-seconds 20 --timeout-seconds 2700"
            ),
        },
        "order_quota": all_status(),
        "order_type": getattr(args, "order_type", "normal"),
        "preconditions": [
            "Game open, not minimized/shrunk",
            "Prefer farm map chrome (phone/backpack) visible",
            "Manually clear leftover crops/plans/supplies before first --apply",
            "Do not put WeChat in foreground (FG-safe clicks still work, but UI may confuse ops)",
            "MaaFw 5.13.1 in project .venv when using UI worker",
        ],
    }
    path = TMP / "dry_run_plan.json"
    write_json(path, payload)
    # Convenience pointer for older scripts/docs expecting .tmp/short_cycle/
    LEGACY_TMP.mkdir(parents=True, exist_ok=True)
    write_json(LEGACY_TMP / "dry_run_plan.json", {"redirect": str(path), **payload})
    return path



def _order_script(order_type: str) -> str:
    return "complete_batch_orders.py" if order_type in {"party", "takeout"} else "complete_normal_orders.py"


def _order_script_args(order_type: str, *extra: str) -> tuple[str, ...]:
    """Script name plus CLI args including --order-type for batch boards."""
    script = _order_script(order_type)
    if order_type in {"party", "takeout"}:
        return (script, "--order-type", order_type, *extra)
    return (script, *extra)


def scan_for_target(max_passes: int, order_type: str = "normal") -> dict:
    """Run complete_normal_orders until a plant target or clean idle board."""
    last: dict = {}
    for attempt in range(1, max_passes + 1):
        print(f"[日常] 扫单中（pass {attempt}/{max_passes}）…", flush=True)
        extra = ("--prepare-only",) if order_type == "normal" else ()
        run_script(*_order_script_args(order_type, *extra))
        last = read_json(COMPLETION)
        print(
            f"scan pass {attempt}: status={last.get('status')} "
            f"submitted={last.get('submitted_count', 0)} "
            f"target={None if not last.get('target_order') else last['target_order'].get('order')}",
            flush=True,
        )
        if last.get('submitted_count'):
            return last
        if last.get("returned_to_farm") and last.get("target_order"):
            return last
        if last.get("returned_to_farm") and last.get("no_acceptable_orders") and not last.get("target_order"):
            return last
        if last.get("status") in {"existing_order_in_progress", "sleep_confirmation_pending"} | BOARD_WAITING_STATES:
            return last
    return last


def main(*, startup_confirmed=False) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Short cycle orchestration (scan -> plan -> monitor -> rematch complete). "
            "Default is dry-run; add --apply to execute against the game."
        )
    )
    parser.add_argument("--rounds", type=int, default=1, help="how many full plant/submit rounds")
    parser.add_argument("--max-scan-passes", type=int, default=6)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=2700)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute against the game (default: dry-run / preview only)",
    )
    parser.add_argument(
        "--order-type",
        choices=("normal", "takeout", "party", "外卖", "聚会"),
        default="normal",
        help="normal=no quota; takeout/party respect daily 12 + 1h after 6",
    )
    parser.add_argument(
        "--skip-checklist",
        action="store_true",
        help="do not print the startup checklist",
    )
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error("--rounds must be 1..20")
    if not 1 <= args.max_scan_passes <= 50:
        parser.error("--max-scan-passes must be 1..50")
    if not 5 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be 5..300")
    if args.timeout_seconds < args.poll_seconds:
        parser.error("--timeout-seconds must be >= --poll-seconds")

    order_type = args.order_type
    if order_type in {"外卖", "takeout", "waimai"}:
        order_type = "takeout"
    elif order_type in {"聚会", "party", "juhui"}:
        order_type = "party"
    else:
        order_type = "normal"
    args.order_type = order_type


    if not args.skip_checklist:
        print_startup_checklist()
        quota = all_status()
        for name, row in quota["types"].items():
            print(
                f"quota {row['label']}: {row['completed_today']}/{row['daily_cap']} done; "
                f"batch_left={row['remaining_in_batch']}; cooldown={row['in_cooldown']}; "
                f"next={row['next_available']}",
                flush=True,
            )

    if not args.apply:
        if args.order_type not in ORDER_BOARD_IMPLEMENTED:
            return refuse_unimplemented_board(args.order_type)
        plan_path = write_dry_run_plan(args)
        print(
            "dry run (no game connection): add --apply to execute "
            "scan -> plan -> monitor -> rematch complete; "
            f"rounds={args.rounds}; evidence under {TMP}",
            flush=True,
        )
        for step in DRY_RUN_STEPS:
            print(f"  {step}", flush=True)
        print(f"wrote {plan_path}", flush=True)
        # Keep parent daily runner from reading a stale --apply summary.
        write_json(
            TMP / "latest_summary.json",
            {
                "status": "dry_run",
                "order_type": args.order_type,
                "rounds": [],
                "plan_path": str(plan_path),
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        write_json(
            LEGACY_TMP / "latest_summary.json",
            {
                "status": "dry_run",
                "order_type": args.order_type,
                "redirect": str(TMP / "latest_summary.json"),
            },
        )
        if args.order_type != "normal":
            q = can_start(args.order_type)
            print(
                f"order_type={args.order_type} can_start={q.can_start} "
                f"remaining_batch={q.remaining_in_batch} next={q.next_available}",
                flush=True,
            )
        return 0

    # Quota gate for takeout/party: skip + report, never busy-wait.
    if args.order_type in ORDER_TYPES:
        gate = can_start(args.order_type)
        if not gate.can_start:
            print(
                f"[跳过] {TYPE_LABELS[args.order_type]}订单配额不可用："
                f"今日已完成 {gate.completed_today}/{gate.daily_cap}；"
                f"冷却中={gate.in_cooldown}；next_available={gate.next_available}。"
                f"请稍后再试或换其他订单类型。",
                flush=True,
            )
            TMP.mkdir(parents=True, exist_ok=True)
            write_json(
                TMP / "latest_summary.json",
                {
                    "status": "quota_blocked",
                    "order_type": args.order_type,
                    "quota": gate.as_dict(),
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            return 0

    # Refuse party/takeout until their boards are wired (never silently use normal).
    if args.order_type not in ORDER_BOARD_IMPLEMENTED:
        return refuse_unimplemented_board(args.order_type)

    # Once per invocation, outside the per-round loop. --skip-checklist only
    # hides terminal text and cannot bypass the explicit land-clearance dialog.
    if not startup_confirmed:
        print("[日常] 短循环等待清地确认弹窗（60秒超时，每5秒心跳）…", flush=True)
        if not confirm_cycle_start():
            print("[错误] 未确认清地或超时，短循环已取消。请先清地/清计划后再试。", flush=True)
            return 2

    print(
        f"[日常] 短循环开始 类型={_cn_type(args.order_type)} rounds={args.rounds} apply={args.apply}",
        flush=True,
    )
    TMP.mkdir(parents=True, exist_ok=True)
    LEGACY_TMP.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "rounds_requested": args.rounds,
        "evidence_dir": str(TMP),
        "rounds": [],
        "status": "running",
    }
    write_json(TMP / "latest_summary.json", summary)
    write_json(LEGACY_TMP / "latest_summary.json", summary)

    try:
        for round_number in range(1, args.rounds + 1):
            check_cancelled()
            round_dir = TMP / f"round_{round_number:02d}"
            round_record: dict = {
                "round": round_number,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "scanning",
            }
            checkpoint(round_dir, "round.json", round_record)
            print(f"=== short-cycle round {round_number}/{args.rounds} ===", flush=True)
            print(f"[日常] 扫单中（round {round_number}）…", flush=True)

            completion = scan_for_target(args.max_scan_passes, args.order_type)
            checkpoint(round_dir, "completion_scan.json", completion)
            round_record["scan"] = {
                "status": completion.get("status"),
                "submitted_count": completion.get("submitted_count"),
                "target_order": completion.get("target_order"),
                "board_availability": completion.get("board_availability"),
            }

            target = completion.get("target_order")
            if not target and completion.get('submitted_count'):
                target = completion['submitted'][0]
            if not target:
                round_record["status"] = (
                    "waiting" if completion.get("status") in {"existing_order_in_progress", "sleep_confirmation_pending"} | BOARD_WAITING_STATES
                    else "no_actionable_orders"
                )
                round_record["detail"] = completion.get("status")
                checkpoint(round_dir, "round.json", round_record)
                summary["rounds"].append(round_record)
                write_json(TMP / "latest_summary.json", summary)
                write_json(LEGACY_TMP / "latest_summary.json", summary)
                print(f"round {round_number}: no plant target ({completion.get('status')}); stopping", flush=True)
                print(
                    f"[订单] 类型={_cn_type(args.order_type)} 阶段=扫单："
                    f"无可用目标 status={completion.get('status')}",
                    flush=True,
                )
                break

            focus = sorted(target_shortage_names(target))
            print(f"round {round_number}: plant shortage crops {focus}", flush=True)
            log_order_snapshot(args.order_type, target, phase="扫单选定")
            production = target.get("production_targets") or []
            ranch_targets = [
                entry
                for entry in production
                if entry.get("route") == "livestock"
                or entry.get("id") in {
                    "crop_016",
                    "crop_017",
                    "crop_018",
                    "crop_019",
                    "crop_020",
                }
            ]
            if ranch_targets:
                print(
                    f"round {round_number}: ranch targets present -> ensure_ranch_feed runs inside run_employee_order_plan",
                    flush=True,
                )
            if production:
                round_record["status"] = "planning"
                round_record["ranch_feed_preflight"] = bool(ranch_targets)
                checkpoint(round_dir, "round.json", round_record)
                print("[日常] 排产中…", flush=True)
                run_script("run_employee_order_plan.py", "--apply")
                assignment = read_json(ASSIGNMENT)
                checkpoint(round_dir, "assignment.json", assignment)
                log_assignment_snapshot(assignment)
                if assignment.get("status") != "saved" or not assignment.get("saved") or not all(
                    job.get("verified") and job.get('repeat') is False and job.get('one_shot') is True
                    for job in assignment.get("saved") or []
                ):
                    raise RuntimeError("employee assignments were not all verified one-shot plans")
                round_record["status"] = "monitoring"
                checkpoint(round_dir, "round.json", round_record)
                print("[日常] 监控领取中…", flush=True)
                run_script(
                    "monitor_employee_products.py",
                    "--assignment-file", str(ASSIGNMENT),
                    "--poll-seconds", str(args.poll_seconds),
                    "--timeout-seconds", str(args.timeout_seconds), "--apply",
                )
                monitor_result = read_json(MONITOR)
                checkpoint(round_dir, "monitor.json", monitor_result)
                if not monitor_result.get("complete") and monitor_result.get("status") != "complete":
                    pending = monitor_result.get("pending") or []
                    raise RuntimeError(f"monitor incomplete; pending={pending}")

            round_record["status"] = "submitting"
            # Persist target for rematch; complete_normal_orders reads .target
            target_payload = {"target": target, "round": round_number}
            target_file = round_dir / "submit_target.json"
            write_json(target_file, target_payload)
            checkpoint(round_dir, "round.json", round_record)

            print("[日常] 交单中…", flush=True)
            if completion.get('submitted_count'):
                # A stocked batch was submitted and rewarded in the scan worker.
                submit = completion
            else:
                run_script(*_order_script_args(args.order_type, "--target-file", str(target_file)))
                submit = read_json(COMPLETION)
            checkpoint(round_dir, "completion_submit.json", submit)

            focus_names = target_shortage_names(target)
            if focus_names:
                accepted = shortage_focus_was_submitted(submit, target)
            else:
                # Stock already enough: any confirmed accept counts (party/takeout batch included).
                accepted = any(
                    entry.get("acceptance_confirmed") is True
                    for entry in (submit.get("submitted") or [])
                )
            log_submit_snapshot(args.order_type, accepted=accepted, submit=submit, target=target)
            quota_after = None
            if accepted and args.order_type in ORDER_TYPES:
                batch_count = 6 if args.order_type in {"party", "takeout"} else 1
                # Prefer batch_count from submit payload when present.
                batch_count = int(submit.get("batch_count") or batch_count)
                quota_after = record_completion(args.order_type, count=batch_count).as_dict()
                print(
                    f"quota recorded {TYPE_LABELS[args.order_type]} +{batch_count}: "
                    f"{quota_after['completed_today']}/{quota_after['daily_cap']} "
                    f"batch_left={quota_after['remaining_in_batch']} "
                    f"cooldown={quota_after['in_cooldown']}",
                    flush=True,
                )
                print(
                    f"[交单] 类型={_cn_type(args.order_type)} 配额已记 +{batch_count} "
                    f"今日 {quota_after['completed_today']}/{quota_after['daily_cap']}",
                    flush=True,
                )
            round_record.update(
                {
                    "status": "accepted" if accepted else "shortage_unresolved",
                    "accepted": accepted,
                    "order_type": args.order_type,
                    "quota": quota_after,
                    "submit_status": submit.get("status"),
                    "submitted_count": submit.get("submitted_count"),
                    "batch_count": int(submit.get("batch_count") or 1),
                    "reward_claimed": submit.get("reward_claimed", False),
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            checkpoint(round_dir, "round.json", round_record)
            summary["rounds"].append(round_record)
            write_json(TMP / "latest_summary.json", summary)
            write_json(LEGACY_TMP / "latest_summary.json", summary)
            print(
                json.dumps(
                    {
                        "round": round_number,
                        "accepted": accepted,
                        "shortage_crops": focus,
                        "submitted_count": submit.get("submitted_count"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not accepted:
                # Fresh board may still show remaining shortage for same crops.
                next_target = submit.get("target_order")
                if next_target and target_shortage_names(next_target) & target_shortage_names(target):
                    print(
                        "round continues: same shortage crops still present; "
                        "start another --rounds pass or re-run",
                        flush=True,
                    )
                break

        summary["status"] = (
            "complete" if len(summary["rounds"]) == args.rounds
            and all(item.get("accepted") is True for item in summary["rounds"])
            else summary["rounds"][-1]["status"] if summary["rounds"] else "incomplete"
        )
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(TMP / "latest_summary.json", summary)
        write_json(LEGACY_TMP / "latest_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        board_waiting = (summary['status'] == 'waiting' and summary['rounds']
                         and summary['rounds'][-1]['scan']['status'] in BOARD_WAITING_STATES)
        return 0 if summary["status"] in {"complete", "no_actionable_orders"} or board_waiting else 2
    except TaskCancelled:
        summary.update(status='cancelled', finished_at_utc=datetime.now(timezone.utc).isoformat())
        write_json(TMP / 'latest_summary.json', summary)
        write_json(LEGACY_TMP / 'latest_summary.json', summary)
        raise
    except Exception as error:
        summary.update(status="failed", error=str(error), finished_at_utc=datetime.now(timezone.utc).isoformat())
        write_json(TMP / "latest_summary.json", summary)
        write_json(LEGACY_TMP / "latest_summary.json", summary)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

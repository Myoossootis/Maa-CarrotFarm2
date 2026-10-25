"""CLI for takeout/party order quota status and manual record/reset."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .order_quota import ORDER_TYPES, TYPE_LABELS, all_status, record_completion, reset_type, status_for
except ImportError:
    from order_quota import ORDER_TYPES, TYPE_LABELS, all_status, record_completion, reset_type, status_for


def main() -> int:
    parser = argparse.ArgumentParser(description="takeout/party daily quota (12/day, cool 1h after 6)")
    parser.add_argument("command", choices=("status", "record", "reset"))
    parser.add_argument("--type", dest="order_type", choices=(*ORDER_TYPES, "\u5916\u5356", "\u805a\u4f1a", "all"), default="all")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()

    if args.command == "status":
        payload = all_status() if args.order_type == "all" else status_for(args.order_type).as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.order_type == "all":
        parser.error(f"{args.command} requires --type takeout|party")
    if args.command == "record":
        if args.count < 1:
            parser.error("--count must be >= 1")
        status = record_completion(args.order_type, count=args.count)
        print(
            f"recorded {args.count}x {TYPE_LABELS[status.order_type]}: "
            f"completed={status.completed_today}/{status.daily_cap}; "
            f"remaining_batch={status.remaining_in_batch}; cooldown={status.in_cooldown}; "
            f"next={status.next_available}",
            flush=True,
        )
        print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2), flush=True)
        return 0
    status = reset_type(args.order_type)
    print(f"reset {TYPE_LABELS[status.order_type]} for {status.date}", flush=True)
    print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

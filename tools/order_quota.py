"""Takeout/party order daily quota + batch cooldown (Asia/Shanghai)."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
try:
    from .runtime_state import write_json
except ImportError:
    from runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / ".tmp" / "order_quota" / "state.json"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai, no DST
ORDER_TYPES = ("takeout", "party")
TYPE_LABELS = {"takeout": "外卖", "party": "聚会", "normal": "普通"}

TYPE_RULES = {
    "takeout": {"daily_cap": 12, "batch_size": 6, "cooldown_hours": 1},
    "party": {"daily_cap": 12, "batch_size": 6, "cooldown_hours": 1},
}

def _rules(order_type):
    return TYPE_RULES[order_type]

DAILY_CAP = 12
BATCH_SIZE = 6
COOLDOWN = timedelta(hours=1)

def _now(now=None):
    if now is None:
        return datetime.now(TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=TZ)
    return now.astimezone(TZ)

def _day_key(now):
    return _now(now).date().isoformat()

def _empty_type():
    return {"completed_today": 0, "cooldown_until": None, "last_completed_at": None}

def _default_state(now=None):
    return {
        "timezone": "Asia/Shanghai",
        "date": _day_key(_now(now)),
        "daily_cap": DAILY_CAP,
        "batch_size": BATCH_SIZE,
        "cooldown_hours": 1,
        "types": {n: _empty_type() for n in ORDER_TYPES},
    }

def _parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

def _rollover(state, now):
    day = _day_key(now)
    if state.get("date") == day:
        types = state.setdefault("types", {})
        for name in ORDER_TYPES:
            if name not in types or not isinstance(types.get(name), dict):
                types[name] = _empty_type()
        return state
    state["date"] = day
    state["types"] = {n: _empty_type() for n in ORDER_TYPES}
    return state

def _normalize_type(order_type):
    key = (order_type or "").strip().lower()
    aliases = {
        "takeout": "takeout", "waimai": "takeout",
        "\u5916\u5356": "takeout", "\u5916\u5356\u8ba2\u5355": "takeout",
        "party": "party", "juhui": "party",
        "\u805a\u4f1a": "party", "\u805a\u4f1a\u8ba2\u5355": "party",
    }
    if key not in aliases:
        raise ValueError(f"订单类型 {order_type!r} 不适用配额；仅聚会/外卖有限额")
    return aliases[key]

def load_state(path=None, *, now=None):
    target = path or DEFAULT_STATE_PATH
    current = _now(now)
    if not target.is_file():
        return _default_state(current)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state(current)
    state = _default_state(current)
    state["date"] = str(raw.get("date") or state["date"])
    types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    for name in ORDER_TYPES:
        entry = types.get(name) if isinstance(types.get(name), dict) else {}
        state["types"][name] = {
            "completed_today": max(0, int(entry.get("completed_today") or 0)),
            "cooldown_until": entry.get("cooldown_until"),
            "last_completed_at": entry.get("last_completed_at"),
        }
    return _rollover(state, current)

def save_state(state, path=None):
    target = path or DEFAULT_STATE_PATH
    write_json(target, state)
    return target

@dataclass(frozen=True)
class QuotaStatus:
    order_type: str
    label: str
    date: str
    completed_today: int
    daily_cap: int
    remaining_today: int
    remaining_in_batch: int
    batch_size: int
    in_cooldown: bool
    cooldown_until: str | None
    next_available: str | None
    can_start: bool
    def as_dict(self):
        return {
            "order_type": self.order_type, "label": self.label, "date": self.date,
            "completed_today": self.completed_today, "daily_cap": self.daily_cap,
            "remaining_today": self.remaining_today, "remaining_in_batch": self.remaining_in_batch,
            "batch_size": self.batch_size, "in_cooldown": self.in_cooldown,
            "cooldown_until": self.cooldown_until, "next_available": self.next_available,
            "can_start": self.can_start,
        }

def status_for(order_type, *, state=None, path=None, now=None):
    key = _normalize_type(order_type)
    rules = _rules(key)
    daily_cap = int(rules["daily_cap"])
    batch_size = int(rules["batch_size"])
    cooldown_hours = float(rules["cooldown_hours"])
    current = _now(now)
    data = _rollover(state if state is not None else load_state(path, now=current), current)
    entry = data["types"][key]
    completed = int(entry.get("completed_today") or 0)
    cooldown_until = _parse_iso(entry.get("cooldown_until"))
    in_cooldown = cooldown_hours > 0 and cooldown_until is not None and current < cooldown_until
    remaining_today = max(0, daily_cap - completed)
    if completed >= daily_cap:
        tomorrow = current.date() + timedelta(days=1)
        next_available = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=TZ).isoformat()
        remaining_in_batch, can = 0, False
    elif in_cooldown:
        next_available = cooldown_until.isoformat() if cooldown_until else None
        remaining_in_batch, can = 0, False
    else:
        done_in_batch = completed % batch_size if batch_size else 0
        remaining_in_batch = min(batch_size - done_in_batch, remaining_today) if batch_size else remaining_today
        next_available, can = None, remaining_in_batch > 0
    return QuotaStatus(
        order_type=key, label=TYPE_LABELS[key], date=str(data["date"]),
        completed_today=completed, daily_cap=daily_cap, remaining_today=remaining_today,
        remaining_in_batch=remaining_in_batch, batch_size=batch_size, in_cooldown=in_cooldown,
        cooldown_until=cooldown_until.isoformat() if cooldown_until else None,
        next_available=next_available, can_start=can,
    )

def all_status(*, state=None, path=None, now=None):
    current = _now(now)
    data = _rollover(state if state is not None else load_state(path, now=current), current)
    return {
        "timezone": "Asia/Shanghai", "date": data["date"], "daily_cap": DAILY_CAP,
        "batch_size": BATCH_SIZE, "cooldown_hours": 1,
        "types": {n: status_for(n, state=data, now=current).as_dict() for n in ORDER_TYPES},
    }

def can_start(order_type, *, state=None, path=None, now=None):
    return status_for(order_type, state=state, path=path, now=now)

def record_completion(order_type, *, path=None, now=None, count=1):
    if count < 1:
        raise ValueError("count must be >= 1")
    key = _normalize_type(order_type)
    rules = _rules(key)
    batch_size = int(rules["batch_size"])
    cooldown_hours = float(rules["cooldown_hours"])
    current = _now(now)
    target = path or DEFAULT_STATE_PATH
    data = load_state(target, now=current)
    entry = data["types"][key]
    entry["completed_today"] = int(entry.get("completed_today") or 0) + count
    entry["last_completed_at"] = current.isoformat()
    if cooldown_hours > 0 and entry["completed_today"] == batch_size:
        entry["cooldown_until"] = (current + timedelta(hours=cooldown_hours)).isoformat()
    elif cooldown_hours > 0 and entry["completed_today"] > batch_size:
        cd = _parse_iso(entry.get("cooldown_until"))
        if cd is not None and current >= cd:
            entry["cooldown_until"] = None
    data["types"][key] = entry
    save_state(data, target)
    return status_for(key, state=data, now=current)

def reset_type(order_type, *, path=None, now=None):
    key = _normalize_type(order_type)
    current = _now(now)
    target = path or DEFAULT_STATE_PATH
    data = load_state(target, now=current)
    data["types"][key] = _empty_type()
    save_state(data, target)
    return status_for(key, state=data, now=current)

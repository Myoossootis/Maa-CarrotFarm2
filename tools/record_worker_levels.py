# -*- coding: utf-8 -*-
"""Record worker_level / crop min_level into employee_capabilities.json.

Guided / offline-first: no long live tour of every slot. Default is dry-run;
pass --write to update assets/catalog/employee_capabilities.json.
Evidence always lands under .tmp/track_c/.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

try:
    from .employee_capabilities import CAPABILITIES_PATH, load_capabilities, normalize_product_name
except ImportError:
    from employee_capabilities import CAPABILITIES_PATH, load_capabilities, normalize_product_name

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".tmp" / "track_c"
SLOT_IDS = (
    "field_1",
    "field_2",
    "field_3",
    "ranch_1",
    "ranch_2",
    "orchard_1",
    "orchard_2",
)


def _now_stamp() -> str:
    # Asia/Shanghai wall clock for evidence filenames.
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def gaps_report(caps: dict) -> dict:
    slots = []
    for slot in caps.get("slots") or []:
        slots.append(
            {
                "id": slot.get("id"),
                "label": slot.get("label"),
                "worker": slot.get("worker"),
                "worker_level": slot.get("worker_level"),
                "worker_level_status": slot.get("worker_level_status"),
                "unverified_visible": slot.get("unverified_visible") or [],
                "locked_gates": slot.get("locked_gates") or {},
                "product_gates_null_min_level": [
                    {
                        "name": name,
                        "eligibility": gate.get("eligibility"),
                        "min_level": gate.get("min_level"),
                    }
                    for name, gate in (slot.get("product_gates") or {}).items()
                    if gate.get("min_level") is None
                ],
            }
        )
    pending = caps.get("pending_capabilities") or []
    return {
        "schema_version": caps.get("schema_version"),
        "observed_on": caps.get("observed_on"),
        "slots_missing_worker_level": [
            s for s in slots if s.get("worker_level") is None
        ],
        "all_slots": slots,
        "pending_capabilities": pending,
        "pending_need_min_level": [
            p
            for p in pending
            if p.get("eligibility") in {"needs_live_recording", "locked_or_level_gated"}
            or (
                p.get("min_level") is None
                and p.get("eligibility") not in {"unsupported"}
            )
        ],
        "uncovered_registry_products": caps.get("uncovered_registry_products") or [],
    }


def find_slot(caps: dict, slot_id: str) -> dict:
    for slot in caps.get("slots") or []:
        if slot.get("id") == slot_id:
            return slot
    raise KeyError(f"unknown slot_id: {slot_id}")


def parse_kv_level(raw: str) -> tuple[str, int]:
    if "=" not in raw:
        raise ValueError(f"expected SLOT=LEVEL, got {raw!r}")
    slot_id, level_s = raw.split("=", 1)
    slot_id = slot_id.strip()
    if slot_id not in SLOT_IDS:
        raise ValueError(f"unknown slot_id: {slot_id}")
    level = int(level_s.strip())
    if level < 1 or level > 999:
        raise ValueError(f"level out of range: {level}")
    return slot_id, level


def parse_gate(raw: str) -> tuple[str, str, int]:
    # SLOT:CROP=MIN
    if "=" not in raw or ":" not in raw.split("=", 1)[0]:
        raise ValueError(f"expected SLOT:CROP=MIN, got {raw!r}")
    left, min_s = raw.split("=", 1)
    slot_id, crop = left.split(":", 1)
    slot_id = slot_id.strip()
    crop = crop.strip()
    if slot_id not in SLOT_IDS:
        raise ValueError(f"unknown slot_id: {slot_id}")
    min_level = int(min_s.strip())
    if min_level < 1 or min_level > 999:
        raise ValueError(f"min_level out of range: {min_level}")
    return slot_id, crop, min_level


def set_worker_level(slot: dict, level: int) -> dict:
    before = {"worker_level": slot.get("worker_level"), "worker_level_status": slot.get("worker_level_status")}
    slot["worker_level"] = int(level)
    slot["worker_level_status"] = "recorded"
    return {"before": before, "after": {"worker_level": slot["worker_level"], "worker_level_status": slot["worker_level_status"]}}


def set_product_gate(slot: dict, crop: str, min_level: int, *, aliases: dict | None = None) -> dict:
    want = normalize_product_name(crop, aliases)
    gates = dict(slot.get("product_gates") or {})
    matched = None
    for name in list(gates):
        if normalize_product_name(name, aliases) == want:
            matched = name
            break
    if matched is None:
        # Also allow setting gate for a product already in products list.
        products = list(slot.get("products") or [])
        for name in products:
            if normalize_product_name(name, aliases) == want:
                matched = name
                break
        if matched is None:
            matched = crop
            products.append(matched)
            slot["products"] = products
        gates[matched] = {"min_level": int(min_level), "eligibility": "verified"}
    else:
        gate = dict(gates[matched])
        gate["min_level"] = int(min_level)
        gate["eligibility"] = "verified"
        gates[matched] = gate
    slot["product_gates"] = gates
    return {"crop": matched, "gate": gates[matched]}


def promote_unverified(slot: dict, crop: str, min_level: int | None, *, aliases: dict | None = None) -> dict:
    want = normalize_product_name(crop, aliases)
    uv = list(slot.get("unverified_visible") or [])
    kept = []
    found = None
    for item in uv:
        if normalize_product_name(str(item.get("name") or ""), aliases) == want:
            found = item
        else:
            kept.append(item)
    if found is None:
        raise KeyError(f"slot {slot.get('id')}: unverified_visible missing {crop!r}")
    slot["unverified_visible"] = kept
    products = list(slot.get("products") or [])
    name = found.get("name") or crop
    if not any(normalize_product_name(p, aliases) == want for p in products):
        products.append(name)
    slot["products"] = products
    gates = dict(slot.get("product_gates") or {})
    level = min_level if min_level is not None else found.get("min_level")
    if level is None:
        gates[name] = {"min_level": None, "eligibility": "observed_unlocked"}
    else:
        gates[name] = {"min_level": int(level), "eligibility": "verified"}
    slot["product_gates"] = gates
    return {"promoted": name, "gate": gates[name], "removed_unverified": found}


def unlock_locked(slot: dict, crop: str, min_level: int, *, aliases: dict | None = None) -> dict:
    want = normalize_product_name(crop, aliases)
    locked = list(slot.get("locked") or [])
    locked_gates = dict(slot.get("locked_gates") or {})
    matched = None
    for name in list(locked):
        if normalize_product_name(name, aliases) == want:
            matched = name
            break
    if matched is None:
        for name in list(locked_gates):
            if normalize_product_name(name, aliases) == want:
                matched = name
                break
    if matched is None:
        raise KeyError(f"slot {slot.get('id')}: locked missing {crop!r}")
    slot["locked"] = [n for n in locked if n != matched]
    locked_gates.pop(matched, None)
    slot["locked_gates"] = locked_gates
    products = list(slot.get("products") or [])
    if not any(normalize_product_name(p, aliases) == want for p in products):
        products.append(matched)
    slot["products"] = products
    gates = dict(slot.get("product_gates") or {})
    gates[matched] = {"min_level": int(min_level), "eligibility": "verified"}
    slot["product_gates"] = gates
    return {"unlocked": matched, "gate": gates[matched]}


def promote_pending(
    caps: dict,
    crop: str,
    slot_id: str,
    min_level: int,
    *,
    aliases: dict | None = None,
) -> dict:
    want = normalize_product_name(crop, aliases)
    pending = list(caps.get("pending_capabilities") or [])
    found = None
    kept = []
    for item in pending:
        if normalize_product_name(str(item.get("name") or ""), aliases) == want:
            found = item
        else:
            kept.append(item)
    if found is None:
        raise KeyError(f"pending_capabilities missing {crop!r}")
    if found.get("eligibility") == "unsupported":
        raise ValueError(f"{crop!r} is unsupported; refuse to promote")
    caps["pending_capabilities"] = kept
    uncovered = [
        n
        for n in (caps.get("uncovered_registry_products") or [])
        if normalize_product_name(n, aliases) != want
    ]
    caps["uncovered_registry_products"] = uncovered
    slot = find_slot(caps, slot_id)
    products = list(slot.get("products") or [])
    name = found.get("name") or crop
    if not any(normalize_product_name(p, aliases) == want for p in products):
        products.append(name)
    slot["products"] = products
    gates = dict(slot.get("product_gates") or {})
    gates[name] = {"min_level": int(min_level), "eligibility": "verified"}
    slot["product_gates"] = gates
    return {"crop": name, "slot_id": slot_id, "gate": gates[name], "removed_pending": found}


def extract_level_from_texts(texts: list[str]) -> int | None:
    """Pick a plausible worker level from OCR / manual text snippets."""
    candidates: list[int] = []
    for text in texts:
        raw = str(text or "")
        for m in re.finditer(r"(?:Lv\.?\s*|等级\s*|LVL\s*)(\d{1,3})", raw, re.I):
            candidates.append(int(m.group(1)))
        for m in re.finditer(r"\b(\d{1,3})\b", raw):
            n = int(m.group(1))
            if 1 <= n <= 200:
                candidates.append(n)
    if not candidates:
        return None
    # Prefer values that came with an explicit Lv/等级 prefix by scanning again.
    preferred: list[int] = []
    for text in texts:
        for m in re.finditer(r"(?:Lv\.?\s*|等级\s*|LVL\s*)(\d{1,3})", str(text or ""), re.I):
            preferred.append(int(m.group(1)))
    pool = preferred or candidates
    # Mode-ish: most common, else first.
    return max(set(pool), key=pool.count)


def ocr_level_from_image(image_path: Path, roi: tuple[int, int, int, int] | None = None) -> dict:
    """Best-effort Maa OCR on a saved screenshot. Does not touch the game window."""
    import cv2
    from maa.pipeline import JOCR, JRecognitionType
    from maa.tasker import Tasker

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"cannot read image: {image_path}")
    if roi is not None:
        x, y, w, h = roi
        image = image[y : y + h, x : x + w]
    tasker = Tasker()
    # Resource not strictly required for post_recognition on raw image in recent maa;
    # if init fails, surface the error for the operator.
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(
            roi=(0, 0, image.shape[1], image.shape[0]),
            expected=[r".+"],
            threshold=0.3,
            replace=[["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"]],
        ),
        image,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    texts: list[str] = []
    scores: list[float] = []
    if recognition is not None:
        for result in recognition.filtered_results or []:
            texts.append(getattr(result, "text", "") or "")
            scores.append(float(getattr(result, "score", 0) or 0))
    level = extract_level_from_texts(texts)
    return {"texts": texts, "scores": scores, "level": level, "image": str(image_path), "roi": roi}


def apply_mutations(caps: dict, args: argparse.Namespace) -> list[dict]:
    aliases = caps.get("product_aliases") or {}
    changes: list[dict] = []
    for raw in args.set_level or []:
        slot_id, level = parse_kv_level(raw)
        slot = find_slot(caps, slot_id)
        changes.append({"op": "set_level", "slot_id": slot_id, **set_worker_level(slot, level)})
    for raw in args.set_gate or []:
        slot_id, crop, min_level = parse_gate(raw)
        slot = find_slot(caps, slot_id)
        changes.append(
            {
                "op": "set_gate",
                "slot_id": slot_id,
                **set_product_gate(slot, crop, min_level, aliases=aliases),
            }
        )
    for raw in args.promote_unverified or []:
        # SLOT:CROP or SLOT:CROP=MIN
        if "=" in raw:
            slot_id, crop, min_level = parse_gate(raw)
        else:
            if ":" not in raw:
                raise ValueError(f"expected SLOT:CROP[=MIN], got {raw!r}")
            slot_id, crop = raw.split(":", 1)
            slot_id = slot_id.strip()
            crop = crop.strip()
            min_level = None
            if slot_id not in SLOT_IDS:
                raise ValueError(f"unknown slot_id: {slot_id}")
        slot = find_slot(caps, slot_id)
        changes.append(
            {
                "op": "promote_unverified",
                "slot_id": slot_id,
                **promote_unverified(slot, crop, min_level, aliases=aliases),
            }
        )
    for raw in args.unlock_locked or []:
        slot_id, crop, min_level = parse_gate(raw)
        slot = find_slot(caps, slot_id)
        changes.append(
            {
                "op": "unlock_locked",
                "slot_id": slot_id,
                **unlock_locked(slot, crop, min_level, aliases=aliases),
            }
        )
    if args.promote_pending:
        if not args.slot or args.min_level is None:
            raise ValueError("--promote-pending requires --slot and --min-level")
        changes.append(
            {
                "op": "promote_pending",
                **promote_pending(
                    caps,
                    args.promote_pending,
                    args.slot,
                    int(args.min_level),
                    aliases=aliases,
                ),
            }
        )
    return changes


def write_evidence(payload: dict, stamp: str | None = None) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _now_stamp()
    path = EVIDENCE / f"record_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Always refresh gaps snapshot beside it.
    (EVIDENCE / "gaps_summary.json").write_text(
        json.dumps(payload.get("gaps_after") or payload.get("gaps") or {}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Record worker levels / crop gates into employee_capabilities.json (dry-run by default)."
    )
    p.add_argument("--gaps", action="store_true", help="Print pending worker_level / min_level gaps and exit.")
    p.add_argument("--set-level", action="append", default=[], metavar="SLOT=N", help="Repeatable. e.g. field_1=12")
    p.add_argument("--set-gate", action="append", default=[], metavar="SLOT:CROP=MIN", help="Repeatable verified gate.")
    p.add_argument(
        "--promote-unverified",
        action="append",
        default=[],
        metavar="SLOT:CROP[=MIN]",
        help="Move unverified_visible crop onto products(+gate).",
    )
    p.add_argument(
        "--unlock-locked",
        action="append",
        default=[],
        metavar="SLOT:CROP=MIN",
        help="Move locked crop onto products with verified min_level.",
    )
    p.add_argument("--promote-pending", metavar="CROP", help="Move pending_capabilities crop onto a slot.")
    p.add_argument("--slot", help="Target slot for --promote-pending or --from-image.")
    p.add_argument("--min-level", type=int, help="min_level for --promote-pending.")
    p.add_argument("--from-image", type=Path, help="OCR a saved screenshot for worker level (no live clicks).")
    p.add_argument("--roi", metavar="X,Y,W,H", help="Optional OCR ROI on --from-image.")
    p.add_argument("--level-text", action="append", default=[], help="Manual OCR snippets; used if image OCR skipped.")
    p.add_argument("--write", action="store_true", help="Write catalog JSON (otherwise dry-run).")
    p.add_argument("--catalog", type=Path, default=CAPABILITIES_PATH, help="Override catalog path.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    caps = load_capabilities(args.catalog)
    gaps = gaps_report(caps)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    if args.gaps and not any(
        [
            args.set_level,
            args.set_gate,
            args.promote_unverified,
            args.unlock_locked,
            args.promote_pending,
            args.from_image,
            args.level_text,
        ]
    ):
        path = write_evidence({"mode": "gaps", "gaps": gaps})
        print(json.dumps({"task": "record_worker_levels", "mode": "gaps", "evidence": str(path), "gaps": gaps}, ensure_ascii=False, indent=2))
        return 0

    working = deepcopy(caps)
    changes: list[dict] = []
    ocr_info = None

    if args.from_image or args.level_text:
        texts = list(args.level_text or [])
        if args.from_image:
            roi = None
            if args.roi:
                parts = [int(x.strip()) for x in args.roi.split(",")]
                if len(parts) != 4:
                    raise ValueError("--roi must be X,Y,W,H")
                roi = tuple(parts)  # type: ignore[assignment]
            # Copy evidence image under .tmp/track_c/
            dest = EVIDENCE / f"capture_{_now_stamp()}_{args.from_image.name}"
            shutil.copy2(args.from_image, dest)
            try:
                ocr_info = ocr_level_from_image(dest, roi=roi)
                texts.extend(ocr_info.get("texts") or [])
            except Exception as exc:  # noqa: BLE001 — surface OCR failure in report, allow --level-text fallback
                ocr_info = {"error": str(exc), "image": str(dest), "roi": roi}
        level = extract_level_from_texts(texts)
        if level is None:
            raise RuntimeError("could not extract worker level from image/text; pass --level-text or --set-level")
        slot_id = args.slot
        if not slot_id:
            raise ValueError("--from-image/--level-text requires --slot")
        slot = find_slot(working, slot_id)
        changes.append({"op": "set_level_from_ocr", "slot_id": slot_id, "ocr": ocr_info, "texts": texts, **set_worker_level(slot, level)})

    changes.extend(apply_mutations(working, args))
    if not changes and not args.gaps:
        raise SystemExit("nothing to do; use --gaps, --set-level, --set-gate, --from-image, ...")

    # Bump observed_on date stamp when writing.
    if args.write and changes:
        working["observed_on"] = datetime.now().astimezone().strftime("%Y-%m-%d")
        if "notes" in working and isinstance(working["notes"], list):
            note = f"worker_level / gates updated via tools/record_worker_levels.py on {working['observed_on']}."
            if note not in working["notes"]:
                working["notes"] = list(working["notes"]) + [note]

    gaps_after = gaps_report(working)
    evidence = {
        "mode": "write" if args.write else "dry_run",
        "catalog": str(args.catalog),
        "changes": changes,
        "ocr": ocr_info,
        "gaps_before": gaps,
        "gaps_after": gaps_after,
        "utc": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = write_evidence(evidence)

    if args.write:
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        # Backup beside evidence, not next to catalog (keeps git clean).
        backup = EVIDENCE / f"employee_capabilities.backup_{_now_stamp()}.json"
        if args.catalog.exists():
            shutil.copy2(args.catalog, backup)
            evidence["backup"] = str(backup)
        args.catalog.write_text(json.dumps(working, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "task": "record_worker_levels",
                "mode": evidence["mode"],
                "changes": changes,
                "evidence": str(evidence_path),
                "gaps_after_slots_missing_level": [
                    s["id"] for s in gaps_after.get("slots_missing_worker_level") or []
                ],
                "pending_need_min_level": [
                    p.get("name") for p in gaps_after.get("pending_need_min_level") or []
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

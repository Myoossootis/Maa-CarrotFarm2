# -*- coding: utf-8 -*-
"""Offline tests for tools/record_worker_levels.py (no live game)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.employee_capabilities import load_capabilities
from tools.record_worker_levels import (
    apply_mutations,
    extract_level_from_texts,
    gaps_report,
    promote_pending,
    promote_unverified,
    set_product_gate,
    set_worker_level,
    unlock_locked,
)


class RecordWorkerLevelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caps = load_capabilities()

    def test_gaps_report_lists_all_null_levels(self):
        report = gaps_report(self.caps)
        missing = {s["id"] for s in report["slots_missing_worker_level"]}
        self.assertEqual(
            missing,
            {"field_1", "field_2", "field_3", "ranch_1", "ranch_2", "orchard_1", "orchard_2"},
        )
        names = {p["name"] for p in report["pending_need_min_level"]}
        self.assertIn("红皮土豆", names)
        self.assertIn("白玉草莓", names)
        self.assertNotIn("鱼肉", names)

    def test_extract_level_prefers_lv_prefix(self):
        self.assertEqual(extract_level_from_texts(["能量 12462", "Lv.12", "50/50"]), 12)
        self.assertEqual(extract_level_from_texts(["等级 8"]), 8)

    def test_set_level_and_gate_roundtrip_on_copy(self):
        caps = copy.deepcopy(self.caps)
        slot = next(s for s in caps["slots"] if s["id"] == "field_1")
        set_worker_level(slot, 15)
        self.assertEqual(slot["worker_level"], 15)
        self.assertEqual(slot["worker_level_status"], "recorded")
        set_product_gate(slot, "玫瑰", 10, aliases=caps.get("product_aliases"))
        self.assertEqual(slot["product_gates"]["玫瑰"]["min_level"], 10)
        self.assertEqual(slot["product_gates"]["玫瑰"]["eligibility"], "verified")

    def test_promote_unverified_strawberry(self):
        caps = copy.deepcopy(self.caps)
        slot = next(s for s in caps["slots"] if s["id"] == "field_1")
        before_uv = len(slot.get("unverified_visible") or [])
        promote_unverified(slot, "普通草莓", 6, aliases=caps.get("product_aliases"))
        self.assertEqual(len(slot.get("unverified_visible") or []), before_uv - 1)
        self.assertIn("普通草莓", slot["products"])
        self.assertEqual(slot["product_gates"]["普通草莓"]["min_level"], 6)

    def test_unlock_locked_ranch(self):
        caps = copy.deepcopy(self.caps)
        slot = next(s for s in caps["slots"] if s["id"] == "ranch_1")
        unlock_locked(slot, "羊肉", 20, aliases=caps.get("product_aliases"))
        self.assertNotIn("羊肉", slot.get("locked") or [])
        self.assertIn("羊肉", slot["products"])
        self.assertEqual(slot["product_gates"]["羊肉"]["eligibility"], "verified")

    def test_promote_pending_onto_slot(self):
        caps = copy.deepcopy(self.caps)
        promote_pending(caps, "红薯", "field_2", 5, aliases=caps.get("product_aliases"))
        pending_names = {p["name"] for p in caps["pending_capabilities"]}
        self.assertNotIn("红薯", pending_names)
        slot = next(s for s in caps["slots"] if s["id"] == "field_2")
        self.assertIn("红薯", slot["products"])
        self.assertEqual(slot["product_gates"]["红薯"]["min_level"], 5)

    def test_refuse_unsupported_fish(self):
        caps = copy.deepcopy(self.caps)
        with self.assertRaises(ValueError):
            promote_pending(caps, "鱼肉", "field_1", 1, aliases=caps.get("product_aliases"))

    def test_cli_dry_run_writes_evidence_only(self):
        from tools import record_worker_levels as mod

        caps_path = Path(tempfile.mkdtemp()) / "caps.json"
        caps_path.write_text(json.dumps(self.caps, ensure_ascii=False), encoding="utf-8")
        before = caps_path.read_text(encoding="utf-8")
        rc = mod.main(["--catalog", str(caps_path), "--set-level", "field_1=9"])
        self.assertEqual(rc, 0)
        self.assertEqual(caps_path.read_text(encoding="utf-8"), before)
        # Evidence dir should exist under project .tmp/track_c
        self.assertTrue((Path(mod.ROOT) / ".tmp" / "track_c").exists())


if __name__ == "__main__":
    unittest.main()

"""Unit tests for takeout/party order quota (batch, cooldown, day rollover)."""
from __future__ import annotations
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tools import order_quota as q

TZ = timezone(timedelta(hours=8))


class OrderQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self.day0 = datetime(2026, 10, 15, 10, 0, tzinfo=TZ)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_can_start_full_batch(self):
        status = q.status_for("takeout", path=self.path, now=self.day0)
        self.assertTrue(status.can_start)
        self.assertEqual(status.remaining_today, 12)
        self.assertEqual(status.remaining_in_batch, 6)
        self.assertFalse(status.in_cooldown)
        self.assertEqual(status.label, "\u5916\u5356")

    def test_aliases_and_party_label(self):
        status = q.status_for("\u805a\u4f1a", path=self.path, now=self.day0)
        self.assertEqual(status.order_type, "party")
        self.assertEqual(status.label, "\u805a\u4f1a")

    def test_batch_then_cooldown(self):
        now = self.day0
        for i in range(6):
            status = q.record_completion("takeout", path=self.path, now=now + timedelta(minutes=i))
        self.assertEqual(status.completed_today, 6)
        self.assertTrue(status.in_cooldown)
        self.assertFalse(status.can_start)
        self.assertEqual(status.remaining_in_batch, 0)
        self.assertIsNotNone(status.next_available)
        mid = q.status_for("takeout", path=self.path, now=now + timedelta(minutes=30))
        self.assertTrue(mid.in_cooldown)
        self.assertFalse(mid.can_start)
        after = q.status_for("takeout", path=self.path, now=now + timedelta(hours=1, minutes=6))
        self.assertFalse(after.in_cooldown)
        self.assertTrue(after.can_start)
        self.assertEqual(after.remaining_in_batch, 6)
        self.assertEqual(after.remaining_today, 6)

    def test_second_batch_caps_at_twelve(self):
        now = self.day0
        for i in range(6):
            q.record_completion("party", path=self.path, now=now + timedelta(minutes=i))
        later = now + timedelta(hours=1, minutes=5)
        for i in range(6):
            status = q.record_completion("party", path=self.path, now=later + timedelta(minutes=i))
        self.assertEqual(status.completed_today, 12)
        self.assertFalse(status.can_start)
        self.assertEqual(status.remaining_today, 0)
        self.assertTrue(status.next_available.startswith("2026-10-16T00:00:00"))

    def test_types_are_independent(self):
        now = self.day0
        for i in range(6):
            q.record_completion("takeout", path=self.path, now=now + timedelta(minutes=i))
        party = q.status_for("party", path=self.path, now=now + timedelta(minutes=10))
        self.assertTrue(party.can_start)
        self.assertEqual(party.completed_today, 0)
        takeout = q.status_for("takeout", path=self.path, now=now + timedelta(minutes=10))
        self.assertTrue(takeout.in_cooldown)

    def test_day_rollover_resets(self):
        now = self.day0
        for i in range(6):
            q.record_completion("takeout", path=self.path, now=now + timedelta(minutes=i))
        next_day = datetime(2026, 10, 16, 0, 5, tzinfo=TZ)
        status = q.status_for("takeout", path=self.path, now=next_day)
        self.assertEqual(status.date, "2026-10-16")
        self.assertEqual(status.completed_today, 0)
        self.assertTrue(status.can_start)
        self.assertFalse(status.in_cooldown)
        self.assertEqual(status.remaining_in_batch, 6)

    def test_all_status_shape(self):
        payload = q.all_status(path=self.path, now=self.day0)
        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["daily_cap"], 12)
        self.assertEqual(payload["batch_size"], 6)
        self.assertIn("takeout", payload["types"])
        self.assertIn("party", payload["types"])
        self.assertTrue(payload["types"]["takeout"]["can_start"])

    def test_persist_roundtrip(self):
        q.record_completion("\u5916\u5356", path=self.path, now=self.day0)
        status = q.status_for("takeout", path=self.path, now=self.day0 + timedelta(minutes=1))
        self.assertEqual(status.completed_today, 1)
        self.assertEqual(status.remaining_in_batch, 5)
        self.assertTrue(self.path.is_file())


if __name__ == "__main__":
    unittest.main()

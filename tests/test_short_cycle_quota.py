"""Short-cycle quota gate: skip takeout/party when cooling down."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools import order_quota as q
from tools import run_short_cycle as cycle

TZ = timezone(timedelta(hours=8))


class ShortCycleQuotaTests(unittest.TestCase):
    def test_dry_run_embeds_quota(self):
        with patch.object(sys, "argv", ["short_cycle", "--skip-checklist", "--rounds", "1"]), patch.object(
            sys, "stdout", io.StringIO()
        ):
            self.assertEqual(cycle.main(), 0)
        plan = json.loads((cycle.TMP / "dry_run_plan.json").read_text(encoding="utf-8"))
        self.assertIn("order_quota", plan)
        self.assertEqual(plan["order_type"], "normal")
        self.assertIn("takeout", plan["order_quota"]["types"])
        self.assertIn("party", plan["order_quota"]["types"])

    def test_apply_skips_when_takeout_cooling(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            now = datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
            for i in range(6):
                q.record_completion("takeout", path=path, now=now + timedelta(minutes=i))
            blocked = q.can_start("takeout", path=path, now=now + timedelta(minutes=10))
            self.assertFalse(blocked.can_start)

            with patch.object(cycle, "can_start", return_value=blocked), patch.object(
                sys,
                "argv",
                ["short_cycle", "--apply", "--skip-checklist", "--rounds", "1", "--order-type", "takeout"],
            ), patch.object(sys, "stdout", io.StringIO()) as stdout, patch.object(
                cycle, "confirm_cycle_start"
            ) as confirm, patch.object(cycle, "run_script") as run, patch.object(
                cycle, "write_json"
            ) as write_json, patch.object(cycle, "TMP", Path(folder) / "track_b"):
                code = cycle.main()

            self.assertEqual(code, 0)
            confirm.assert_not_called()
            run.assert_not_called()
            out = stdout.getvalue()
            self.assertIn("[跳过]", out)
            self.assertIn("next_available", out)
            payload = write_json.call_args.args[1]
            self.assertEqual(payload["status"], "quota_blocked")


if __name__ == "__main__":
    unittest.main()

"""Wiring tests for Maa daily checkbox tasks (party/takeout/normal)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class InterfaceDailyTasksTests(unittest.TestCase):
    def setUp(self):
        self.iface = json.loads((ROOT / "assets/interface.json").read_text(encoding="utf-8"))
        self.pipe = json.loads(
            (ROOT / "assets/resource/pipeline/carrot_farm.json").read_text(encoding="utf-8")
        )

    def test_version_and_daily_entries(self):
        self.assertRegex(self.iface["version"], r'^\d+\.\d+\.\d+$')
        by_entry = {t["entry"]: t for t in self.iface["task"]}
        for entry, name in [
            ("RunDailyPartyOrders", "聚会订单"),
            ("RunDailyTakeoutOrders", "外卖订单"),
            ("RunDailyNormalOrders", "普通订单"),
        ]:
            self.assertIn(entry, by_entry)
            self.assertEqual(by_entry[entry]["name"], name)
            self.assertFalse(by_entry[entry]["default_check"])
            self.assertEqual(by_entry[entry]['option'], ['一号位允许品类'])
        # recommended order in task list
        entries = [t["entry"] for t in self.iface["task"]]
        self.assertLess(entries.index("RunDailyPartyOrders"), entries.index("RunDailyTakeoutOrders"))
        self.assertLess(entries.index("RunDailyTakeoutOrders"), entries.index("RunDailyNormalOrders"))
        # advanced short cycle unchecked
        self.assertFalse(by_entry["RunShortCycle"]["default_check"])
        self.assertTrue(by_entry["RuntimePreflight"]["default_check"])

    def test_pipeline_nodes_bake_order_type_and_apply(self):
        for entry, order_type in [
            ("RunDailyPartyOrders", "party"),
            ("RunDailyTakeoutOrders", "takeout"),
            ("RunDailyNormalOrders", "normal"),
        ]:
            node = self.pipe[entry]
            self.assertEqual(node["custom_action"], "RunDailyOrders")
            self.assertEqual(node["custom_action_param"]["order_type"], order_type)
            self.assertIs(node["attach"]["ui_apply"], True)

    def test_prepare_allowlist_keeps_daily(self):
        src = (ROOT / "tools/prepare_maa_ui.py").read_text(encoding="utf-8")
        self.assertIn("RunDailyPartyOrders", src)
        self.assertIn("RunDailyTakeoutOrders", src)
        self.assertIn("RunDailyNormalOrders", src)

    def test_welcome_mentions_monitor_preview(self):
        self.assertIn("监控预览", self.iface["welcome"])
        self.assertIn("左上角", self.iface["welcome"])


class RunDailyOrdersActionTests(unittest.TestCase):
    def test_action_passes_order_type_and_apply(self):
        from agent import production_actions as actions

        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            get_node_data=lambda _: {"attach": {"ui_apply": True}},
        )
        args = SimpleNamespace(
            node_name="RunDailyPartyOrders",
            custom_action_param=json.dumps(
                {"order_type": "party", "poll_seconds": 20, "timeout_seconds": 900}
            ),
        )
        with patch.object(actions, "confirm_cycle_start", return_value=True) as confirm, patch.object(
            actions, "wait_for_background_ready", return_value=True
        ), patch.object(actions, "run_cycle_worker", return_value=True) as worker:
            self.assertTrue(actions.RunDailyOrders().run(context, args))
            confirm.assert_not_called()  # daily Maa path skips MessageBox (was silent hang)
            argv = worker.call_args.args[0]
            self.assertEqual(worker.call_args.kwargs.get("script"), "run_daily_orders.py")
            self.assertIn("--order-type", argv)
            self.assertIn("party", argv)
            self.assertIn("--apply", argv)

    def test_invalid_order_type_never_launches(self):
        from agent import production_actions as actions

        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            get_node_data=lambda _: {"attach": {"ui_apply": True}},
        )
        args = SimpleNamespace(
            node_name="RunDailyPartyOrders",
            custom_action_param=json.dumps({"order_type": "fish"}),
        )
        with patch.object(actions, "run_cycle_worker") as worker, patch("sys.stdout", io.StringIO()) as out:
            self.assertFalse(actions.RunDailyOrders().run(context, args))
            worker.assert_not_called()
            self.assertIn("[错误]", out.getvalue())


class DailyOrchestratorUnitTests(unittest.TestCase):
    def test_quota_blocked_logs_chinese_skip(self):
        from tools import order_quota as q
        from tools import run_daily_orders as daily

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            from datetime import datetime, timedelta, timezone

            TZ = timezone(timedelta(hours=8))
            now = datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
            for i in range(6):
                q.record_completion("party", path=path, now=now + timedelta(minutes=i))
            blocked = q.can_start("party", path=path, now=now + timedelta(minutes=10))
            self.assertFalse(blocked.can_start)

            with patch.object(daily, "can_start", return_value=blocked), patch.object(
                daily, "_run_one_short"
            ) as run_one, patch.object(sys, "stdout", io.StringIO()) as out, patch.object(
                daily, "confirm_cycle_start", return_value=True
            ), patch.object(daily, "TMP", Path(folder) / "daily"), patch.object(
                daily, "MONITOR_DIR", Path(folder) / "mon"
            ), patch.object(daily, "write_json"):
                code = daily.run_daily(
                    "party", apply=False, poll=20, timeout=900, startup_confirmed=True
                )
            self.assertEqual(code, 0)
            run_one.assert_not_called()
            text = out.getvalue()
            self.assertIn("[跳过]", text)
            self.assertIn("next_available", text)


class NormalQuotaTests(unittest.TestCase):
    def test_normal_is_not_quota_controlled(self):
        from tools import order_quota as q
        self.assertNotIn("normal", q.ORDER_TYPES)
        self.assertNotIn("normal", q.TYPE_RULES)
        with self.assertRaises(ValueError):
            q.can_start("normal")



class BoardWiredTests(unittest.TestCase):
    def test_party_and_takeout_are_implemented(self):
        from tools import run_short_cycle as cycle

        self.assertEqual(
            cycle.ORDER_BOARD_IMPLEMENTED,
            frozenset({"normal", "party", "takeout"}),
        )

    def test_party_dry_run_no_longer_refuses(self):
        from tools import run_short_cycle as cycle
        import tempfile
        from unittest.mock import patch
        import io
        import sys

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(
                sys,
                "argv",
                ["short_cycle", "--skip-checklist", "--rounds", "1", "--order-type", "party"],
            ), patch.object(sys, "stdout", io.StringIO()) as out, patch.object(
                cycle, "run_script"
            ) as run, patch.object(cycle, "TMP", Path(folder) / "track_b"), patch.object(
                cycle, "write_json"
            ) as write_json, patch.object(cycle, "write_dry_run_plan", return_value=Path(folder) / "plan.json"):
                code = cycle.main()
            self.assertEqual(code, 0)
            run.assert_not_called()  # dry-run does not launch game scripts
            text_out = out.getvalue()
            self.assertNotIn("order_board_not_implemented", text_out)
            # latest summary should be dry_run, not refuse
            payload = write_json.call_args.args[1]
            self.assertEqual(payload.get("status"), "dry_run")
            self.assertEqual(payload.get("order_type"), "party")

    def test_order_script_routing(self):
        from tools import run_short_cycle as cycle

        self.assertEqual(cycle._order_script("party"), "complete_batch_orders.py")
        self.assertEqual(cycle._order_script("takeout"), "complete_batch_orders.py")
        self.assertEqual(cycle._order_script("normal"), "complete_normal_orders.py")
        args = cycle._order_script_args("party", "--prepare-only")
        self.assertEqual(args[0], "complete_batch_orders.py")
        self.assertIn("--order-type", args)
        self.assertIn("party", args)

    def test_pipeline_has_party_takeout_tabs(self):
        pipe = json.loads(
            (ROOT / "assets/resource/pipeline/carrot_farm.json").read_text(encoding="utf-8")
        )
        for name in (
            "ClickPartyOrderTab",
            "ClickTakeoutOrderTab",
            "VerifyPartyOrdersReady",
            "VerifyTakeoutOrdersReady",
        ):
            self.assertIn(name, pipe)
        self.assertTrue((ROOT / "assets/resource/image/navigation/party_order_tab.png").is_file())
        self.assertTrue((ROOT / "assets/resource/image/navigation/takeout_order_active.png").is_file())


    def test_pipeline_daily_focus_not_stale(self):
        pipe = json.loads((ROOT / "assets/resource/pipeline/carrot_farm.json").read_text(encoding="utf-8"))
        for key in ("RunDailyPartyOrders", "RunDailyTakeoutOrders"):
            focus = pipe[key].get("focus") or {}
            blob = json.dumps(focus, ensure_ascii=False)
            self.assertNotIn("尚未接入", blob)
            self.assertNotIn("order_board_not_implemented", blob)
            starting = (focus.get("Node.Action.Starting") or {}).get("content") or ""
            self.assertIn("页签", starting)
            self.assertEqual(pipe[key].get("custom_action"), "RunDailyOrders")

    def test_log_helpers_chinese(self):
        from tools import run_short_cycle as cycle
        import io
        from unittest.mock import patch
        import sys

        target = {
            "order": 2,
            "items": [{"name": "玉米", "actual": 1, "required": 5, "missing": 4}],
            "missing_items": [{"name": "玉米", "missing": 4}],
            "production_targets": [{"name": "玉米", "needed": 4, "route": "field", "id": "crop_001"}],
        }
        assignment = {
            "status": "saved",
            "saved": [
                {
                    "slot_id": "field_1",
                    "slot": "农田1",
                    "items": {"玉米": 4},
                    "one_shot": True,
                    "verified": True,
                }
            ],
        }
        submit = {"status": "submitted", "submitted_count": 1, "submitted": [{"order": 2}]}
        with patch.object(sys, "stdout", io.StringIO()) as out:
            cycle.log_order_snapshot("normal", target, phase="扫单选定")
            cycle.log_assignment_snapshot(assignment)
            cycle.log_submit_snapshot("normal", accepted=True, submit=submit, target=target)
        text = out.getvalue()
        self.assertIn("[订单]", text)
        self.assertIn("普通订单", text)
        self.assertIn("订单号=2", text)
        self.assertIn("玉米", text)
        self.assertIn("[员工安排]", text)
        self.assertIn("农田1", text)
        self.assertIn("[交单]", text)
        self.assertIn("成功", text)





if __name__ == "__main__":
    unittest.main()


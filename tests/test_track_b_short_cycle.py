import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import run_short_cycle as cycle
from tools import order_template_click as otc


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets/resource/pipeline/carrot_farm.json"
ORDER_NODES = (
    "VerifyNormalOrdersReady",
    "ClickNormalOrderTab",
    "NormalOrdersClickNoticeBoard",
    "NormalOrdersClickEmptyNoticeBoard",
    "NormalOrdersClickRestaurantEntry",
    "AcceptNormalOrderStart",
    "ConfirmSleepPromptStart",
    "ClickSleepPromptConfirm",
)


class TrackBDryRunTests(unittest.TestCase):
    def test_default_is_dry_run_and_writes_evidence(self):
        evidence = ROOT / ".tmp" / "track_b"
        with patch.object(sys, "argv", ["short_cycle", "--skip-checklist", "--rounds", "2"]), patch.object(
            sys, "stdout", io.StringIO()
        ) as stdout, patch.object(cycle, "confirm_cycle_start") as confirm, patch.object(
            cycle, "run_script"
        ) as run:
            code = cycle.main()
        self.assertEqual(code, 0)
        confirm.assert_not_called()
        run.assert_not_called()
        plan_path = evidence / "dry_run_plan.json"
        self.assertTrue(plan_path.is_file())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["mode"], "dry_run")
        self.assertFalse(plan["apply"])
        self.assertEqual(plan["rounds"], 2)
        self.assertIn("scan:", plan["steps"][0])
        out = stdout.getvalue()
        self.assertIn("dry run", out)
        self.assertIn(str(evidence), out)

    def test_tmp_points_at_track_b(self):
        self.assertEqual(cycle.TMP, ROOT / ".tmp" / "track_b")


class TrackBOrderTemplateSafetyTests(unittest.TestCase):
    def test_order_board_nodes_remain_template_matchable(self):
        data = json.loads(PIPELINE.read_text(encoding="utf-8"))
        for name in ORDER_NODES:
            with self.subTest(node=name):
                node = data[name]
                self.assertEqual(node.get("recognition"), "TemplateMatch")
                self.assertIn("template", node)
                self.assertEqual(node.get("method", 5), 5)

    def test_template_hit_rejects_non_template_nodes(self):
        with self.assertRaises(ValueError):
            otc.template_hit(object(), "RunShortCycle")


if __name__ == "__main__":
    unittest.main()

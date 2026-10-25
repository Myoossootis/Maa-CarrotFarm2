import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import complete_batch_orders as batch
from tools import run_daily_orders as daily
from tools import order_quota as quota


class WorkerEntryTests(unittest.TestCase):
    def test_real_worker_dispatches_batch_cli_before_game_connection(self):
        for kind in ("party", "takeout"):
            # Invalid target deliberately stops inside the batch CLI before game input.
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/ui_worker.py"), "complete_batch_orders.py",
                 "--order-type", kind, "--prepare-only", "--target-file", "unused.json"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--prepare-only cannot be combined", result.stderr)
            self.assertNotIn("invalid choice", result.stderr)

    def test_joint_action_passes_both_types(self):
        from agent import production_actions as actions
        ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                              get_node_data=lambda _: {"attach": {"ui_apply": True}})
        args = SimpleNamespace(node_name="RunDailyBatchOrders", custom_action_param='{"order_type":"batch"}')
        with patch.object(actions, "wait_for_background_ready", return_value=True), patch.object(actions, "run_cycle_worker", return_value=True) as worker, patch("sys.stdout", io.StringIO()):
            self.assertTrue(actions.RunDailyOrders().run(ctx, args))
            self.assertEqual(worker.call_args.args[0][:3], ["--order-types", "party", "takeout"])


class BridgeLogTests(unittest.TestCase):
    def test_failed_worker_output_survives_temporary_signal_cleanup(self):
        from agent import cycle_bridge as bridge
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder:
            root = Path(folder)
            (root / "tools").mkdir()
            (root / "tools/ui_worker.py").write_text(
                "import sys\nprint('[error] recipe could not be read', flush=True)\nsys.exit(2)\n",
                encoding="utf-8",
            )
            with patch.object(bridge, "ROOT", root), patch("sys.stdout", io.StringIO()) as out:
                self.assertFalse(bridge.run_cycle_worker([], is_stopping=lambda: False))
            logs = list((root / ".tmp/worker_logs").glob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("recipe could not be read", logs[0].read_text(encoding="utf-8"))
            self.assertIn(str(logs[0]), out.getvalue())
            self.assertEqual(list((root / ".tmp/maa_ui_runs").iterdir()), [])


class BatchFailureTests(unittest.TestCase):
    def acceptance_failure(self, *, checked=True, count=6, confirmation_error=None, capacity_after=None):
        items = [dict(name=name, actual=12, required=12, missing=0, satisfied=True)
                 for name in ('小麦', '玉米')]
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder, ExitStack() as stack:
            folder = Path(folder)
            for name in ('Win32Controller', 'Resource', 'Tasker'):
                stack.enter_context(patch.object(batch, name, return_value=MagicMock()))
            for name in ('load_products', 'find_window', 'install_foreground_safe_inputs',
                         'measure_controller_offset', 'build_runtime_resource', 'dismiss_offline_income',
                         'leave_employee_ui_if_visible', 'require_decoded_card_selection'):
                stack.enter_context(patch.object(batch, name))
            stack.enter_context(patch.object(batch.decoded, 'enabled', return_value=True))
            stack.enter_context(patch.object(batch.decoded, 'assert_order_unchanged', return_value=items))
            stack.enter_context(patch.object(batch, 'open_orders', return_value=True))
            stack.enter_context(patch.object(batch, 'probe_availability', return_value={'status': 'available'},
                side_effect=[{'status': 'available'}, {'status': capacity_after}] if capacity_after else None))
            stack.enter_context(patch.object(batch, '_select_recipe', return_value=(items, 7, (155, 782), 6)))
            stack.enter_context(patch.object(batch, '_enrich', return_value={}))
            stack.enter_context(patch.object(batch, 'screenshot', return_value=object()))
            stack.enter_context(patch.object(batch, 'completion_control_visible', return_value=True))
            stack.enter_context(patch.object(batch, 'completion_checked', return_value=(checked, 0)))
            stack.enter_context(patch.object(batch, '_completion_count', return_value=count))
            stack.enter_context(patch.object(batch, 'cv2', MagicMock()))
            stack.enter_context(patch.object(batch, 'RESULT', folder / 'result.json'))
            stack.enter_context(patch.object(batch, 'ROOT', folder))
            stack.enter_context(patch('sys.stdout', io.StringIO()))
            accept = stack.enter_context(patch.object(batch, '_click_accept'))
            confirm = stack.enter_context(patch.object(batch, 'finish_order_acceptance', side_effect=confirmation_error))
            reward = stack.enter_context(patch.object(batch, '_wait_and_claim_reward'))
            with self.assertRaises(batch.decoded.DecodeError):
                batch.run_batch(order_type='party', prepare_only=False, requested={})
            payload = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['submitted'], [])
            journal = folder / 'latest_party_delivery.json'
            if confirmation_error:
                self.assertTrue(journal.exists())
                intent = json.loads(journal.read_text(encoding='utf-8'))
                self.assertEqual(intent['status'], 'accept_intent')
                self.assertFalse(intent['automatic_retry_allowed'])
                self.assertFalse(intent['submitted'][0]['acceptance_confirmed'])
            else:
                self.assertFalse(journal.exists())
            reward.assert_not_called()
            return accept.call_count, confirm.call_count

    def test_changed_six_completion_control_stops_before_accept(self):
        for checked, count in ((False, 6), (True, 5)):
            with self.subTest(checked=checked, count=count):
                self.assertEqual(self.acceptance_failure(checked=checked, count=count), (0, 0))

    def test_capacity_changed_before_accept_sends_no_accept_or_intent(self):
        for status in ('order_capacity_exhausted', 'order_batch_unavailable', 'board_unknown'):
            self.assertEqual(self.acceptance_failure(capacity_after=status), (0, 0))

    def test_unconfirmed_debit_after_click_never_writes_accepted_checkpoint(self):
        self.assertEqual(self.acceptance_failure(confirmation_error=batch.decoded.DecodeError('no debit')), (1, 1))

    def test_unreadable_recipe_retries_without_replacing(self):
        with patch.object(batch, "screenshot", return_value=object()), patch.object(
            batch, "read_order", return_value=[]
        ) as read, patch.object(batch.time, "sleep"), patch.object(batch, "recognized_text", return_value="") as text:
            with self.assertRaisesRegex(RuntimeError, "order_recipe_unreadable"):
                batch._read_current(object(), object(), [])
        self.assertEqual(read.call_count, 3)
        self.assertEqual(text.call_count, 3)

    def test_loading_frame_then_readable_recipe(self):
        items = [{"name": "小麦"}, {"name": "玉米"}]
        with patch.object(batch, "screenshot"), patch.object(batch, "recognized_text", return_value=""), patch.object(batch, "read_order", side_effect=[[], items]), patch.object(batch.time, "sleep"):
            self.assertEqual(batch._read_current(object(), object(), []), items)

    def test_connect_failure_writes_diagnostic_and_invalidates_old_target(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder:
            folder = Path(folder)
            result = folder / "result.json"
            result.write_text('{"target_order":{"order":1}}', encoding="utf-8")
            with patch.object(batch, "RESULT", result), patch.object(batch, "ROOT", folder), patch.object(batch, "load_products", return_value=[]), patch.object(batch, "find_window", side_effect=RuntimeError("game window missing")), patch("sys.stdout", io.StringIO()) as out:
                with self.assertRaisesRegex(RuntimeError, "game window missing"):
                    batch.run_batch(order_type="party", prepare_only=True, requested=None)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["stage"], "connect")
            self.assertIsNone(payload["target_order"])
            self.assertTrue((Path(payload["evidence_dir"]) / "error.log").is_file())
            self.assertIn("阶段=connect", out.getvalue())

    def test_recipe_failure_is_not_reported_as_no_orders(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder, ExitStack() as stack:
            folder = Path(folder)
            for name in ("Win32Controller", "Resource", "Tasker"):
                stack.enter_context(patch.object(batch, name, return_value=MagicMock()))
            for name in ("load_products", "find_window", "install_foreground_safe_inputs", "measure_controller_offset", "build_runtime_resource", "dismiss_offline_income", "leave_employee_ui_if_visible", "_ensure_batch_checked"):
                stack.enter_context(patch.object(batch, name))
            stack.enter_context(patch.object(batch, "open_orders", return_value=True))
            stack.enter_context(patch.object(batch, 'probe_availability', return_value={'status': 'available'}))
            stack.enter_context(patch.object(batch, "_select_recipe", side_effect=RuntimeError("order_recipe_unreadable")))
            stack.enter_context(patch.object(batch, "screenshot", side_effect=RuntimeError("no frame")))
            stack.enter_context(patch.object(batch, "RESULT", folder / "result.json"))
            stack.enter_context(patch.object(batch, "ROOT", folder))
            stack.enter_context(patch("sys.stdout", io.StringIO()))
            with self.assertRaisesRegex(RuntimeError, "order_recipe_unreadable"):
                batch.run_batch(order_type="takeout", prepare_only=True, requested=None)
            data = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["stage"], "read_recipe")
            self.assertNotIn("no_acceptable_orders", data)


class RecipeSelectionTests(unittest.TestCase):
    def item(self, actual, required):
        return dict(name='小麦', actual=actual, required=required,
                    missing=max(0, required-actual), satisfied=actual>=required)

    def test_decoded_requested_card_reselects_once_before_read(self):
        requested = dict(order=7, board_pin=[155, 782], batch_count=1,
                         items=[self.item(0, 3)])
        for still_unselected in (False, True):
            with self.subTest(still_unselected=still_unselected), ExitStack() as stack:
                stack.enter_context(patch.object(batch.decoded, 'enabled', return_value=True))
                stack.enter_context(patch.object(batch.decoded, 'select_order'))
                stack.enter_context(patch.object(batch, 'screenshot'))
                stack.enter_context(patch.object(batch, 'completion_checked', return_value=(False, 0)))
                stack.enter_context(patch.object(batch, 'completion_control_visible', return_value=True))
                stack.enter_context(patch.object(batch.time, 'sleep'))
                proof = stack.enter_context(patch.object(batch, 'require_decoded_card_selection',
                    side_effect=[batch.decoded.DecodeError('not selected'),
                                 batch.decoded.DecodeError('not selected') if still_unselected else None]))
                click = stack.enter_context(patch.object(batch, 'post_click_normalized',
                    return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))))
                read = stack.enter_context(patch.object(batch, '_read_current', return_value=requested['items']))
                if still_unselected:
                    with self.assertRaises(batch.decoded.DecodeError):
                        batch._select_recipe(object(), object(), [], requested)
                    read.assert_not_called()
                else:
                    self.assertEqual(batch._select_recipe(object(), object(), [], requested)[1], 7)
                    read.assert_called_once()
                self.assertEqual(proof.call_count, 2)
                self.assertEqual(click.call_count, 2)
                self.assertTrue(all(call.args[1:] == (155, 782) for call in click.call_args_list))

    def test_stop_after_first_stocked_card(self):
        missing = [self.item(0, 2)]
        ready = [self.item(3, 2)]
        with patch.object(batch, 'screenshot'), patch.object(batch, '_completion_count', return_value=6), patch.object(batch, '_read_current', side_effect=[missing, ready]) as read, patch.object(batch, '_ensure_batch_checked', return_value=False), patch.object(batch, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, patch.object(batch.time, 'sleep'), patch('sys.stdout', io.StringIO()):
            items, number, pin, count = batch._select_recipe(object(), object(), [])
        self.assertEqual(number, 2)
        self.assertEqual(items, ready)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(click.call_count, 2)

    def test_locked_dish_skipped_without_replacing_board(self):
        ready = [self.item(3, 2)]
        with patch.object(batch, 'screenshot'), patch.object(batch, '_completion_count', return_value=6), patch.object(batch, '_read_current', side_effect=[batch.UnavailableOrderError('dish_level_locked'), ready]), patch.object(batch, '_ensure_batch_checked', return_value=False), patch.object(batch, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))), patch.object(batch.time, 'sleep'), patch('sys.stdout', io.StringIO()):
            self.assertEqual(batch._select_recipe(object(), object(), [])[1], 2)

    def test_saved_card_selected_once_and_signature_mismatch_stops(self):
        requested = dict(order=7, board_pin=[155,782], items=[self.item(0,3)])
        with patch.object(batch, '_read_current', return_value=[self.item(3,4)]), patch.object(batch, '_ensure_batch_checked'), patch.object(batch, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, patch.object(batch.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'order_rematch_failed'):
                batch._select_recipe(object(), object(), [], requested)
        self.assertEqual(click.call_count, 2)

    def test_delivery_claim_records_receipt_and_never_accepts(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder, patch.object(batch, 'ROOT', Path(folder)), patch.object(batch, 'open_orders', return_value=True), patch.object(batch.time, 'sleep'), patch.object(batch, 'screenshot', return_value=MagicMock()), patch.object(batch, 'reward_overlay_visible', return_value=False), patch.object(batch, 'claim_completed_reward', return_value=True), patch.object(batch.cv2, 'imwrite'), patch.object(batch, '_click_accept') as accept, patch('sys.stdout', io.StringIO()):
            path = batch._wait_and_claim_reward(object(), object(), 'party')
            self.assertTrue(Path(path).is_dir())
            accept.assert_not_called()


    def test_stocked_single_does_not_disable_six_completion_checkbox(self):
        stocked = [self.item(5,30)]
        smaller_gap = [dict(name='玉米', actual=0, required=12, missing=12, satisfied=False)]
        with patch.object(batch, 'screenshot'), patch.object(batch, '_completion_count', return_value=6), patch.object(batch, '_read_current', side_effect=[stocked]+[smaller_gap]*8) as read, patch.object(batch, '_ensure_batch_checked', return_value=False), patch.object(batch, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, patch.object(batch.time, 'sleep'), patch('sys.stdout', io.StringIO()):
            items, number, pin, count = batch._select_recipe(object(), object(), [])
        self.assertEqual((number,count), (2,6))
        self.assertEqual(items, smaller_gap)
        self.assertEqual(read.call_count, 9)
        self.assertFalse(any(call.args[1:] == (520, 1205) for call in click.call_args_list))

    def test_default_batch_stops_if_control_offers_less_than_six(self):
        with patch.object(batch.decoded, 'enabled', return_value=False), patch.object(batch, 'screenshot'), patch.object(batch, '_completion_count', return_value=5), patch.object(batch, '_read_current', return_value=[self.item(100, 12)]), patch.object(batch, '_ensure_batch_checked', return_value=False), patch.object(batch, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))), patch.object(batch.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'exactly six'):
                batch._select_recipe(object(), object(), [])

    def test_batch_count_comes_from_actual_control(self):
        for count in (1, 5, 6):
            with patch.object(batch, 'recognized_text', return_value=f'完成{count}次'):
                self.assertEqual(batch._completion_count(object(), object()), count)
        with patch.object(batch, 'recognized_text', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'batch_count_unreadable'):
                batch._completion_count(object(), object())

    def test_reward_dismissal_needs_only_one_blank_click(self):
        from tools import complete_normal_orders as normal
        with patch.object(normal, 'reward_overlay_visible', side_effect=[True, False]), patch.object(normal, 'screenshot'), patch.object(normal, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, patch.object(normal.time, 'sleep'):
            self.assertTrue(normal.dismiss_reward_overlay(object()))
        click.assert_called_once_with(unittest.mock.ANY, 360, 1100)



class DailySchedulingTests(unittest.TestCase):
    def test_single_task_yields_cooldown_without_waiting(self):
        gate = SimpleNamespace(can_start=False, in_cooldown=True, completed_today=6, daily_cap=12,
                               next_available="2026-10-16T02:00:00+08:00", as_dict=lambda: {"next_available": "later"})
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder, patch.object(daily, "TMP", Path(folder)), patch.object(daily, "MONITOR_DIR", Path(folder)), patch.object(daily, "can_start", return_value=gate), patch.object(daily, "_wait_cooldown") as wait, patch.object(daily, "_run_one_short") as run, patch("sys.stdout", io.StringIO()):
            self.assertEqual(daily.run_daily("party", apply=True, poll=20, timeout=900, startup_confirmed=True), 0)
            wait.assert_not_called()
            run.assert_not_called()
            self.assertEqual(json.loads((Path(folder) / "latest_party.json").read_text(encoding="utf-8"))["status"], "cooldown")

    def test_normal_never_checks_or_records_quota(self):
        accepted = {"_exit_code": 0, "status": "complete", "rounds": [{"accepted": True}]}
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder, patch.object(daily, "TMP", Path(folder)), patch.object(daily, "MONITOR_DIR", Path(folder)), patch.object(daily, "can_start") as gate, patch.object(daily, "_run_one_short", return_value=accepted), patch("sys.stdout", io.StringIO()):
            self.assertEqual(daily.run_daily("normal", apply=True, poll=20, timeout=900, startup_confirmed=True), 0)
            gate.assert_not_called()
            self.assertNotIn("normal", quota.ORDER_TYPES)

    def test_joint_scheduler_runs_takeout_before_waiting_for_party(self):
        events = []
        state = {"party": 0, "takeout": 0}
        cooled = False
        def gate(kind):
            count = state[kind]
            return SimpleNamespace(completed_today=count, daily_cap=12, in_cooldown=count == 6 and not cooled,
                                   next_available="2026-10-16T02:00:00+08:00", can_start=count < 12)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder:
            def run(kind, **kwargs):
                events.append(kind)
                state[kind] += 6
                status = "cooldown" if state[kind] == 6 else "daily_cap_reached"
                (Path(folder) / f"latest_{kind}.json").write_text(json.dumps({"status": status, "next_available": "2026-10-16T02:00:00+08:00"}))
                return 0
            def wait(*args):
                events.append("wait")
                nonlocal cooled
                cooled = True
            with patch.object(daily, "TMP", Path(folder)), patch.object(daily, "can_start", side_effect=gate), patch.object(daily, "run_daily", side_effect=run), patch.object(daily, "_wait_cooldown", side_effect=wait):
                self.assertEqual(daily.run_scheduled(["party", "takeout"], apply=True, poll=20, timeout=900, startup_confirmed=True), 0)
            self.assertEqual(events[:3], ["party", "takeout", "wait"])
            self.assertEqual(events[3:], ["party", "takeout"])

    def test_nonzero_short_cycle_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder, patch.object(daily, "TMP", Path(folder)), patch.object(daily, "MONITOR_DIR", Path(folder)), patch.object(daily, "_run_one_short", return_value={"_exit_code": 2, "status": "failed", "rounds": [{"accepted": True}]}), patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(daily.run_daily("normal", apply=True, poll=20, timeout=900, startup_confirmed=True), 2)
            self.assertIn("exit=2", out.getvalue())
            self.assertEqual(json.loads((Path(folder) / "latest_normal.json").read_text(encoding="utf-8"))["status"], "failed")


if __name__ == "__main__":
    unittest.main()

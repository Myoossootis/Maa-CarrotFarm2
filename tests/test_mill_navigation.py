from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2

from tools import ensure_ranch_feed as feed


FIXTURES = Path(__file__).parent / 'fixtures/mill_navigation'


class MillLandmarkTests(unittest.TestCase):
    def test_live_winter_mill_body_is_recognized(self):
        frame = cv2.imread(str(FIXTURES / 'winter_mill.png'))
        hit = feed.feed_match(frame, 'feed_silo_structure.png', feed.MAP_SCALES, .72)
        self.assertIsNotNone(hit, 'Visible winter mill must not require repeated map swipes')

    def test_farmhouse_does_not_match_mill(self):
        frame = cv2.imread(str(FIXTURES / 'farmhouse.png'))
        self.assertIsNone(feed.feed_match(frame, 'feed_silo_structure.png', feed.MAP_SCALES, .72))

    def test_visible_mill_stops_search_without_swiping(self):
        frame = cv2.imread(str(FIXTURES / 'winter_mill.png'))
        with patch.object(feed, 'screenshot', return_value=frame), patch.object(feed, 'post_swipe_normalized') as swipe, patch('sys.stdout', io.StringIO()):
            _, _, structure = feed.find_silo(object())
        self.assertIsNotNone(structure)
        swipe.assert_not_called()

    def test_shortage_badge_is_converted_to_body_coordinates(self):
        frame = cv2.imread(str(FIXTURES / 'winter_mill.png'))
        with patch.object(feed, 'screenshot', return_value=frame), patch.object(feed, 'feed_match', return_value=None), patch.object(feed, 'locate_scaled', return_value=(.95, 100, 200, 205, 234)), patch('sys.stdout', io.StringIO()):
            _, _, structure = feed.find_silo(object())
        self.assertEqual(structure, (.95, 120, 243, 120, 180))

    def test_failed_search_is_bounded_and_saves_screenshot_and_scores(self):
        frame = cv2.imread(str(FIXTURES / 'winter_mill.png'))
        job = SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))
        with tempfile.TemporaryDirectory() as folder, patch.object(feed, 'screenshot', return_value=frame), patch.object(feed, 'feed_match', return_value=None), patch.object(feed, 'locate_scaled', return_value=None), patch.object(feed, 'post_swipe_normalized', return_value=job) as swipe, patch.object(feed, 'time', SimpleNamespace(sleep=lambda _: None)), patch('sys.stdout', io.StringIO()):
            evidence = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'feed silo landmark was not found'):
                feed.find_silo(object(), evidence_dir=evidence)
            self.assertEqual(swipe.call_count, 6)
            self.assertTrue((evidence / 'search_failed.png').is_file())
            self.assertEqual(json.loads((evidence / 'search.json').read_text())['swipes'], 6)


class MillClickTests(unittest.TestCase):
    def setUp(self):
        self.frame = cv2.imread(str(FIXTURES / 'winter_mill.png'))
        self.location = (self.frame, None, (.99, 220, 700, 150, 130))

    def call_open(self, folder, states):
        with patch.object(feed, '_navigation_evidence', return_value=Path(folder)), patch.object(feed, 'screenshot', return_value=self.frame), patch.object(feed, 'read_mill_state', side_effect=states), patch.object(feed, 'click') as click, patch.object(feed, 'time', SimpleNamespace(sleep=lambda _: None)), patch('sys.stdout', io.StringIO()):
            self.click = click
            return feed.open_mill(object(), object(), location=self.location)

    def test_body_is_clicked_once_and_title_confirms_actual_open(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.call_open(folder, [None, '磨坊6级预计可生产0秒'])
            self.assertEqual(result['status'], 'opened')
            self.assertEqual(result['remaining_seconds'], 0)
            self.assertEqual(self.click.call_args.args[1:], (295, 765))
            self.click.assert_called_once()
            self.assertTrue((Path(folder) / 'opened.png').is_file())

    def test_already_open_does_not_click_or_refill(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.call_open(folder, ['磨坊6级预计可生产10分'])
        self.assertEqual(result['status'], 'already_open')
        self.assertEqual(result['clicks'], 0)
        self.click.assert_not_called()

    def test_wrong_popup_stops_without_second_click_and_records_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'mill_panel_not_opened'):
                self.call_open(folder, [None] * 5)
            result = json.loads((Path(folder) / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['stage'], 'verify_panel')
            self.assertEqual(result['status'], 'failed')
            self.assertTrue((Path(folder) / 'failure.png').is_file())
            self.assertIn('Traceback', (Path(folder) / 'error.log').read_text(encoding='utf-8'))
        self.click.assert_called_once()

    def test_duration_without_mill_title_is_not_a_confirmed_panel(self):
        with patch.object(feed, 'recognized_text', return_value='预计可生产0秒'):
            self.assertIsNone(feed.read_mill_state(object(), self.frame))

    def test_sack_without_body_does_not_authorize_a_building_click(self):
        self.location = (self.frame, None, None)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'mill_body_not_visible'):
                self.call_open(folder, [None])
        self.click.assert_not_called()

    def test_worker_exposes_open_only_and_rejects_mixed_modes_before_game_connection(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'tools/ui_worker.py'), 'ensure_ranch_feed.py',
                                 '--open-only', '--apply'], cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('choose inspect, open-only or apply', result.stderr)


if __name__ == '__main__':
    unittest.main()

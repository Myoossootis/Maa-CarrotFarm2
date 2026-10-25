"""One-shot field-3 ingredients -> mill refill -> employee feed collection loop."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback

try:
    from .runtime_state import write_json
    from .task_control import check_cancelled, TaskCancelled
except ImportError:
    from runtime_state import write_json
    from task_control import check_cancelled, TaskCancelled

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / '.tmp/feed_production/state.json'
ITEMS = {'玉米': 20, '甘蔗': 16, '土豆': 12}
MUTATION_RULES = {entry['name']: entry for entry in json.loads(
    (ROOT / 'assets/catalog/feed_mutations.json').read_text(encoding='utf-8'))['mutations']}


def combine_crop_progress(progress, mutation_counts):
    """Known variants count toward their own base product, at the configured ratio."""
    effective = {name: list(pair) for name, pair in progress.items()}
    for name, count in mutation_counts.items():
        rule = MUTATION_RULES.get(name)
        if rule is None or type(count) is not int or count < 0:
            raise ValueError(f'未知变异品类或数量不合法：{name}={count!r}')
        effective[rule['base_product']][0] += count * rule['ratio']
    return effective


def validate_parameters(max_feed, poll_seconds):
    if type(max_feed) is not int or not 1 <= max_feed <= 99999:
        raise ValueError('饲料上限应为 1–99999 的整数')
    if type(poll_seconds) is not int or not 10 <= poll_seconds <= 3600:
        raise ValueError('饲料检查间隔应为 10–3600 秒的整数')


def all_crops_complete(progress):
    """Never let a sum or one finished product authorize collection."""
    return (set(progress) == set(ITEMS) and all(
        isinstance(progress[name], (list, tuple)) and len(progress[name]) == 2
        and all(type(n) is int for n in progress[name])
        and progress[name][1] == target and progress[name][0] >= target
        for name, target in ITEMS.items()))


class FeedLoop:
    def __init__(self, ui, max_feed=120, poll_seconds=60, state_path=STATE_PATH, log_dir=None):
        validate_parameters(max_feed, poll_seconds)
        self.ui, self.max_feed, self.poll_seconds = ui, max_feed, poll_seconds
        self.path = Path(state_path)
        self.log_dir = Path(log_dir or ROOT / '.tmp/feed_production' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ui.evidence_dir = self.log_dir
        self.state = (json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists()
                      else dict(crop_phase='idle', refill_due=False, pending=None, items=ITEMS))
        if (self.state.get('items') != ITEMS or self.state.get('crop_phase') not in {'idle', 'growing'}
                or type(self.state.get('refill_due')) is not bool):
            raise RuntimeError(f'饲料循环检查点无效，保留现场：{self.path}')

    def log(self, event, **values):
        record = dict(time=datetime.now().astimezone().isoformat(timespec='seconds'),
                      event=event, crop_phase=self.state['crop_phase'], **values)
        with (self.log_dir / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        print('[饲料循环] ' + json.dumps(record, ensure_ascii=False), flush=True)

    def save(self, **values):
        self.state.update(values)
        self.state['updated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        write_json(self.path, self.state)

    def action(self, name, callback, **committed):
        check_cancelled()
        self.save(pending=name)
        self.log('action_start', action=name)
        result = callback()
        if name == 'refill' and result['status'] == 'started':
            committed['refill_due'] = False
        self.save(pending=None, **committed)
        self.log('action_done', action=name, result=result)
        return result

    def tick(self):
        check_cancelled()
        if self.state.get('pending'):
            raise RuntimeError(f"上次 {self.state['pending']} 未确认；为防重复操作已停止，请查看截图和检查点 {self.path}")
        feed = self.ui.inventory()
        if type(feed) is not int or feed < 0:
            raise RuntimeError(f'背包饲料数量无法确认：{feed!r}')
        self.log('inventory', count=feed, max_feed=self.max_feed)
        self.save(last_feed=feed, paused=feed >= self.max_feed)
        if feed >= self.max_feed:
            self.log('paused_at_cap', count=feed, max_feed=self.max_feed)
            return
        ready = self.ui.mill_ready()
        self.log('mill_employee', ready=ready)
        if type(ready) is not int or ready < 0:
            raise RuntimeError('磨坊员工饲料数量无法确认')
        if ready:
            self.action('collect_mill', lambda: self.ui.collect_mill(ready))
            feed = self.ui.inventory()
            if type(feed) is not int or feed < 0:
                raise RuntimeError('领取磨坊饲料后背包数量无法确认')
            self.save(last_feed=feed, paused=feed >= self.max_feed)
            self.log('inventory_after_collection', count=feed, max_feed=self.max_feed)
            if feed >= self.max_feed:
                self.log('paused_at_cap', count=feed, max_feed=self.max_feed)
                return
        if self.state['crop_phase'] == 'growing':
            progress = self.ui.crop_progress()
            self.log('crop_progress', progress=progress)
            if all_crops_complete(progress):
                # Re-read every individual counter; the collect button also
                # appears for partially finished multi-product plans.
                confirmed = self.ui.crop_progress()
                self.log('crop_progress_confirm', progress=confirmed)
                if all_crops_complete(confirmed):
                    self.action('collect_crops', self.ui.collect_crops,
                                crop_phase='idle', refill_due=True)
        if self.state['refill_due']:
            # Inventory could be consumed/changed while reading the farm card.
            feed = self.ui.inventory()
            if feed >= self.max_feed:
                self.save(last_feed=feed, paused=True)
                self.log('paused_before_refill', count=feed)
                return
            result = self.action('refill', self.ui.refill)
            if result['status'] != 'started':
                self.log('refill_wait', result=result)
                return
        if self.state['crop_phase'] == 'idle':
            # Busy slots are a precondition failure, not an uncertain save.
            # Do not leave a false pending intent when no plan was edited.
            self.ui.ensure_idle()
            self.action('arrange', self.ui.arrange, crop_phase='growing')

    def run(self, max_checks=0):
        write_json(self.path.parent / 'latest_run.json',
                   dict(evidence_dir=str(self.log_dir), checkpoint=str(self.path)))
        self.log('started', max_feed=self.max_feed, poll_seconds=self.poll_seconds,
                 checkpoint=str(self.path), evidence_dir=str(self.log_dir),
                 slot='field_3', items=ITEMS, repeat=False)
        try:
            checks = 0
            read_errors = 0
            while True:
                try:
                    self.tick()
                    self.ui.wait_on_farm()
                    read_errors = 0
                except RuntimeError as error:
                    # Retry observations only. An uncertain mutation must
                    # retain its intent and stop, never repeat the click.
                    if self.state.get('pending'):
                        raise
                    read_errors += 1
                    self.log('observation_error', attempt=read_errors, error=str(error))
                    self.ui.save_failure(self.log_dir / f'observation_error_{read_errors}.png')
                    if read_errors >= 3:
                        raise
                    time.sleep(min(self.poll_seconds, 10))
                    continue
                checks += 1
                if max_checks and checks >= max_checks:
                    self.log('bounded_checks_finished', checks=checks)
                    return
                self.log('waiting', seconds=self.poll_seconds)
                time.sleep(self.poll_seconds)
        except BaseException as error:
            cancelled = isinstance(error, TaskCancelled)
            self.log('cancelled' if cancelled else 'error',
                     error=str(error), pending=self.state.get('pending'),
                     evidence_dir=str(self.log_dir))
            (self.log_dir / ('cancelled.log' if cancelled else 'error.log')).write_text(
                traceback.format_exc(), encoding='utf-8')
            self.ui.save_failure(self.log_dir / ('stopped.png' if cancelled else 'failure.png'))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-feed', type=int, default=120)
    parser.add_argument('--poll-seconds', type=int, default=60)
    parser.add_argument('--max-checks', type=int, default=0,
                        help='Bounded live verification; 0 runs until Maa Stop')
    args = parser.parse_args()
    validate_parameters(args.max_feed, args.poll_seconds)
    if args.max_checks < 0:
        parser.error('--max-checks must be >= 0')
    try:
        from .feed_production_ui import FeedProductionUI
    except ImportError:
        from feed_production_ui import FeedProductionUI
    with FeedProductionUI() as ui:
        FeedLoop(ui, args.max_feed, args.poll_seconds).run(args.max_checks)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

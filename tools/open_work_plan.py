"""Open the work-plan dialog from a proven employee-list page."""
import argparse
import json
import time

try:
    from .atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from .configure_field_employee import (
        SLOTS,
        click,
        employee_page_state,
        screenshot,
        visible_slot_action,
    )
except ImportError:
    from atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from configure_field_employee import (
        SLOTS,
        click,
        employee_page_state,
        screenshot,
        visible_slot_action,
    )


def open_work_plan(controller, slot_id: str = 'field_1'):
    if slot_id not in SLOTS:
        raise ValueError(f'unknown employee slot: {slot_id}')
    frame = screenshot(controller)
    state = employee_page_state(frame)
    if state == 'work_plan':
        time.sleep(.3)
        if employee_page_state(screenshot(controller)) == 'work_plan':
            return dict(status='already_open', clicks=0, slot=slot_id)
        raise RuntimeError('work-plan state not confirmed; no click')
    if state != 'employee_list':
        raise RuntimeError('employee list not recognized; no click')
    action = visible_slot_action(frame, slot_id)
    if action is None or action[1] != 'work_plan':
        raise RuntimeError(f'{SLOTS[slot_id]["label"]} blue work-plan button not available; no click')
    click(controller, 230, action[0])
    confirmed = 0
    for _ in range(12):
        frame = screenshot(controller)
        confirmed = confirmed + 1 if employee_page_state(frame) == 'work_plan' else 0
        if confirmed >= 2:
            return dict(status='opened', clicks=1, slot=slot_id)
        time.sleep(.3)
    raise RuntimeError('work-plan dialog not confirmed; stopped without a second click')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slot', choices=sorted(SLOTS), default='field_1')
    add_out_argument(parser)
    args = parser.parse_args()
    controller = connect_controller()
    try:
        result = dict(task='open_work_plan', **open_work_plan(controller, args.slot))
        evidence = write_evidence('open_work_plan', result, args.out)
        result['evidence'] = str(evidence)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

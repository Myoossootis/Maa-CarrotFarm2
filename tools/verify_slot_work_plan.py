"""Locate one employee slot, open its blue work-plan button, then close it."""

import argparse
import json

try:
    from .atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from .configure_field_employee import (
        SLOTS,
        close_plan_dialog,
        employee_page_state,
        open_plan,
        screenshot,
    )
except ImportError:
    from atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from configure_field_employee import (
        SLOTS,
        close_plan_dialog,
        employee_page_state,
        open_plan,
        screenshot,
    )


def verify_slot_work_plan(controller, slot_id: str) -> dict:
    if slot_id not in SLOTS:
        raise ValueError(f'unknown employee slot: {slot_id}')
    if employee_page_state(screenshot(controller)) == 'work_plan':
        raise RuntimeError('a work-plan dialog is already open; target slot is unknown, no click')
    _, button_y = open_plan(controller, slot_id)
    close_plan_dialog(controller)
    if employee_page_state(screenshot(controller)) != 'employee_list':
        raise RuntimeError('employee list not visible after closing work plan')
    return {
        'status': 'verified',
        'slot': slot_id,
        'label': SLOTS[slot_id]['label'],
        'button_y': button_y,
        'opened': True,
        'closed': True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--slot', choices=sorted(SLOTS), required=True)
    add_out_argument(parser)
    args = parser.parse_args()
    controller = connect_controller()
    try:
        result = dict(task='verify_slot_work_plan', **verify_slot_work_plan(controller, args.slot))
        evidence = write_evidence('verify_slot_work_plan', result, args.out)
        result['evidence'] = str(evidence)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

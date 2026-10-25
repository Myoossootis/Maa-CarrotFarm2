"""Return from a slot detail to the employee list with one back click."""
import argparse
import json

try:
    from .atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from .configure_field_employee import employee_page_state, return_to_employee_list, screenshot
except ImportError:
    from atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from configure_field_employee import employee_page_state, return_to_employee_list, screenshot

import time


def return_employee_list(controller):
    frame = screenshot(controller)
    state = employee_page_state(frame)
    if state == 'employee_list':
        time.sleep(.3)
        if employee_page_state(screenshot(controller)) == 'employee_list':
            return dict(status='already_on_list', clicks=0)
        raise RuntimeError('employee list state not confirmed; no click')
    if state not in {'slot_detail', 'empty_plan_prompt'}:
        raise RuntimeError(f'expected slot detail, got {state}; no click')
    _, clicks = return_to_employee_list(controller)
    return dict(status='returned', clicks=clicks)


def main():
    parser = argparse.ArgumentParser()
    add_out_argument(parser)
    args = parser.parse_args()
    controller = connect_controller()
    try:
        result = dict(task='return_employee_list', **return_employee_list(controller))
        evidence = write_evidence('return_employee_list', result, args.out)
        result['evidence'] = str(evidence)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

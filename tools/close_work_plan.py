"""Close a proven work-plan dialog once and confirm the employee list."""
import argparse
import json
import time

try:
    from .atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from .configure_field_employee import close_plan_dialog, employee_page_state, screenshot
except ImportError:
    from atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from configure_field_employee import close_plan_dialog, employee_page_state, screenshot


def close_work_plan(controller):
    frame = screenshot(controller)
    if employee_page_state(frame) == 'employee_list':
        time.sleep(.3)
        if employee_page_state(screenshot(controller)) == 'employee_list':
            return dict(status='already_closed', clicks=0)
        raise RuntimeError('employee-list state not confirmed; no click')
    if employee_page_state(frame) != 'work_plan':
        raise RuntimeError('work-plan dialog not recognized; no click')
    close_plan_dialog(controller)
    return dict(status='closed', clicks=1)


def main():
    parser = argparse.ArgumentParser()
    add_out_argument(parser)
    args = parser.parse_args()
    controller = connect_controller()
    try:
        result = dict(task='close_work_plan', **close_work_plan(controller))
        evidence = write_evidence('close_work_plan', result, args.out)
        result['evidence'] = str(evidence)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

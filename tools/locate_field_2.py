"""Locate field 2 from the proven employee-list state without opening it."""
import argparse
import json

try:
    from .atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from .configure_field_employee import find_slot_action
except ImportError:
    from atom_cli_common import add_out_argument, connect_controller, release_controller, write_evidence
    from configure_field_employee import find_slot_action


def locate_field_2(controller):
    y, state = find_slot_action(controller, 'field_2')
    return dict(status='located', slot='field_2', action=state, button_y=y)


def main():
    parser = argparse.ArgumentParser()
    add_out_argument(parser)
    args = parser.parse_args()
    controller = connect_controller()
    try:
        result = dict(task='locate_field_2', **locate_field_2(controller))
        evidence = write_evidence('locate_field_2', result, args.out)
        result['evidence'] = str(evidence)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

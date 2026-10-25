"""Check application version and release record; no game access."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='optional release tag, e.g. v0.6.4')
    args = parser.parse_args()
    version = json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version):
        parser.error('interface version must be X.Y.Z')
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    if not re.search(r'^## ' + re.escape(version) + r'(?:\s|$)', changelog, re.MULTILINE):
        parser.error('CHANGELOG.md has no entry for ' + version)
    if args.tag and args.tag != 'v' + version:
        parser.error('release tag must match v' + version)
    print('Application version: ' + version)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

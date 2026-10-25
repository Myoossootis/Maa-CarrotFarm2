"""Rebuild planting coordinates from retained static geometry, without any game access."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOMETRY = ROOT / 'assets/catalog/manual_planting_geometry.json'
PROFILE = ROOT / 'assets/catalog/manual_planting_ui.json'
FIELD_ITEMS = {'field_1': 12, 'field_2': 284, 'field_3': 285}


def build_profile(geometry):
    if geometry.get('packageVersion') != '87':
        raise ValueError('未核对的规则版本')
    origin = geometry['fields']['field_1']['mapAnchor']
    matrix = geometry['projection']
    fields = {}
    skins = {r['row']['item_id']: r['row'] for r in geometry['evidence']['configuration']
             if r['table'] == 'cfg_skin_env' and r['row']['skin_id'] == 0}
    for name, item_id in FIELD_ITEMS.items():
        field = geometry['fields'][name]
        layout = field['layout']
        if (field['buildingItemId'] != item_id or skins[item_id]['bundle'] != 'farm' or
                skins[item_id]['prefab_path'] != f'prefabs/map/building/{layout}'):
            raise ValueError('农田身份与包内资源映射不一致')
        local_slots = geometry['layouts'][layout]
        if len(local_slots) != 15:
            raise ValueError('未核对的槽位数量')
        offset = [field['mapAnchor'][i] - origin[i] for i in range(2)]
        slots = {}
        for index, point in enumerate(local_slots, 1):
            local = [point[i] + offset[i] for i in range(2)] + [1]
            pixel = [round(sum(a*b for a, b in zip(row, local)), 2) for row in matrix]
            if not all(math.isfinite(v) for v in pixel):
                raise ValueError('无效投影')
            slots[str(index)] = pixel
        fields[name] = dict(buildingItemId=item_id, layout=layout, slots=slots,
                            source='包87资源映射、独立槽位顺序、同图锚点和历史截图交叉核对')
    views = geometry['referenceViews']
    profile = dict(packageVersion='87', referenceOrigin=[0, 280], referenceImage=views[0]['image'],
                   referenceViews=views, fields=fields)
    shifts = {r['image']: r['canonicalShift'] for r in views if 'canonicalShift' in r}
    checks = []
    for observation in geometry['observations']:
        point = fields[observation['fieldId']]['slots'][str(observation['slot'])]
        shift = shifts[observation['image']]
        projected = [point[i] + shift[i] for i in range(2)]
        error = math.dist(projected, observation['pixel'])
        if error > observation['maxErrorPixels']:
            raise ValueError(f"历史画面交叉核对超限：{observation['fieldId']} 槽{observation['slot']}，{error:.2f}px")
        checks.append(dict(**observation, projected=projected, errorPixels=round(error, 3)))
    if any(sum(o['fieldId'] == field for o in checks) < 5 for field in ('field_2', 'field_3')):
        raise ValueError('独立画面核对不足')
    report = dict(readOnly=True, gameInputs=0, liveAcceptancePerformed=False, status='passed',
                  slotCoverage={k: len(v['slots']) for k, v in fields.items()}, observations=checks,
                  maxErrorPixels=round(max(o['errorPixels'] for o in checks), 3),
                  registeredViewCount=len(views))
    return profile, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry', type=Path, default=GEOMETRY)
    parser.add_argument('--output', type=Path, default=PROFILE)
    parser.add_argument('--report', type=Path, default=ROOT / '.tmp/manual_planting_calibration/offline_report.json')
    args = parser.parse_args()
    profile, report = build_profile(json.loads(args.geometry.read_text(encoding='utf-8')))
    for path, data in ((args.output, profile), (args.report, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'observations'}, ensure_ascii=False))


if __name__ == '__main__':
    main()

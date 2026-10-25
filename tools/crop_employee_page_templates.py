"""Crop page-state templates from committed real Maa screenshots."""
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tests/fixtures/employee_pages/field_detail.png'
PROMPT_SOURCE = ROOT / 'tests/fixtures/employee_pages/empty_plan_prompt.png'
DEST = ROOT / 'assets/resource/image/production'


def main():
    frame = cv2.imread(str(SOURCE))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError('expected a 720x1358 Maa field-detail fixture')
    left, top, right, bottom = 135, 445, 590, 520
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / 'employee_assignment_prompt.png'
    if not cv2.imwrite(str(target), frame[top:bottom, left:right]):
        raise RuntimeError(f'failed to save {target}')
    print(target)
    prompt = cv2.imread(str(PROMPT_SOURCE))
    if prompt is None or prompt.shape[:2] != (1358, 720):
        raise RuntimeError('expected a 720x1358 Maa empty-plan prompt capture')
    target = DEST / 'empty_plan_prompt_text.png'
    if not cv2.imwrite(str(target), prompt[520:590, 100:625]):
        raise RuntimeError(f'failed to save {target}')
    print(target)


if __name__ == '__main__':
    main()

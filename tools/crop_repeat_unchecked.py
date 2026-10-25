"""Crop the unchecked one-shot control from a real 720x1358 Maa capture."""
from pathlib import Path
import cv2

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.tmp/single_order_check/after_toggle.png'
DESTINATION = ROOT / 'assets/resource/image/production/repeat_unchecked.png'


def main():
    frame = cv2.imread(str(SOURCE))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError('expected the audited 720x1358 Maa unchecked-plan capture')
    if not cv2.imwrite(str(DESTINATION), frame[1118:1158, 498:538]):
        raise RuntimeError('failed to save unchecked control template')
    print(DESTINATION)


if __name__ == '__main__':
    main()

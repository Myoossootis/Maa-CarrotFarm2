"""Conservative scene localization and original Maa background inputs for planting."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / 'assets/resource/image/manual_planting'
CROPS = (14, 154, 155, 157, 156, 158, 159, 160, 161, 162, 163, 164)


def check_profile(profile, plan):
    field = profile['fields'].get(plan['fieldId'], {})
    if profile.get('packageVersion') != '87' or any(str(s) not in field.get('slots', {}) for s in plan['selectedSlots']):
        raise RuntimeError(field.get('blockedReason', '目标槽位没有已核对的画面映射，未发送输入'))


def scene_translation(reference, frame):
    """Translation only; reject zoom/rotation, repetitive soil matches and weak coverage."""
    if frame.shape[:2] != (1358, 720):
        raise RuntimeError('需要已核对的720×1358画面')
    origin = np.array([0, 280], dtype=np.float32)
    orb = cv2.ORB_create(nfeatures=5000, fastThreshold=7)
    # HUD/footer are outside this map crop. A wide, dominant translation must
    # beat competing matches; a single repeated tile or moving NPC is insufficient.
    def features(image):
        mask = np.full(image.shape[:2], 255, dtype=np.uint8)
        # Fixed HUD must not masquerade as an unmoving map in the western view.
        mask[:620, :190] = 0
        mask[120:290, 420:] = 0
        mask[290:, 590:] = 0
        return orb.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), mask)
    keys_a, desc_a = features(reference)
    keys_b, desc_b = features(frame[280:1030])
    if desc_a is None or desc_b is None:
        raise RuntimeError('农田场景特征不足，不能定位')
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < .7 * b.distance]
    if len(good) < 60:
        raise RuntimeError('农田场景匹配不足；请回到已校准的农田画面')
    source = np.float32([keys_a[m.queryIdx].pt for m in good]) + origin
    target = np.float32([keys_b[m.trainIdx].pt for m in good]) + origin
    offsets = target - source
    votes = Counter(map(tuple, np.rint(offsets / 6).astype(int)))
    peak, _ = votes.most_common(1)[0]
    seed = np.array(peak) * 6
    accepted = np.linalg.norm(offsets - seed, axis=1) < 5
    shift = np.median(offsets[accepted], axis=0)
    accepted = np.linalg.norm(offsets - shift, axis=1) < 3
    competitor = max((n for p, n in votes.items() if np.linalg.norm(np.array(p) * 6 - shift) > 20), default=0)
    if accepted.sum() < 60 or accepted.sum() < competitor * 3:
        raise RuntimeError('场景位移没有唯一优势，不能区分相邻地块')
    matrix, inliers = cv2.estimateAffinePartial2D(source[accepted], target[accepted], method=cv2.RANSAC, ransacReprojThreshold=2)
    if matrix is None or inliers is None:
        raise RuntimeError('农田场景变换无法确认')
    points = source[accepted]
    if (inliers.mean() < .75 or
            np.ptp(points[:, 0]) < 180 or np.ptp(points[:, 1]) < 180 or
            not np.allclose(matrix[:, :2], np.eye(2), atol=.008)):
        raise RuntimeError('场景存在缩放、歧义或覆盖不足，不能使用坐标')
    if np.percentile(np.linalg.norm(offsets[accepted] - shift, axis=1), 90) > 2.5:
        raise RuntimeError('场景位移不一致，不能定位')
    return shift


def reference_matrix(value):
    """Canonical map -> a registered reference image. Only the two audited zooms."""
    value = np.asarray(value, dtype=float)
    if value.shape == (2,):
        value = np.column_stack((np.eye(2), value))
    if (value.shape != (2, 3) or not np.isfinite(value).all() or
            not any(np.allclose(value[:, :2], np.eye(2) * scale, atol=.004)
                    for scale in (1, 5/7))):
        raise RuntimeError('参考图的缩放或旋转未经校准；未点击')
    return value.copy()


def canonical_transform(references, frame):
    """Match a native-scale view, then compose its calibrated map transform and fresh pan."""
    if work_plan_visible(frame):
        raise RuntimeError('工作计划弹窗遮挡地图，禁止地块定位和种植')
    matches = []
    for reference, mapping in references:
        matrix = reference_matrix(mapping)
        try:
            shift = scene_translation(reference, frame)
        except RuntimeError:
            continue
        matrix[:, 2] += shift
        matches.append(matrix)
    if not matches:
        raise RuntimeError('没有已校准地图视图匹配当前画面；未点击')
    # Compare positions across the entire mapped area, not just one point.
    corners = np.array([[-500, 0, 1], [1250, 0, 1], [-500, 1400, 1], [1250, 1400, 1]])
    if any(np.max(np.linalg.norm(corners @ matrix.T - corners @ matches[0].T, axis=1)) > 4
           for matrix in matches[1:]):
        raise RuntimeError('地图视图之间定位矛盾；不能区分农田')
    return np.median(matches, axis=0)


def canonical_translation(references, frame):
    """Compatibility for callers which explicitly require the original zoom."""
    matrix = canonical_transform(references, frame)
    if not np.allclose(matrix[:, :2], np.eye(2), atol=.004):
        raise RuntimeError('当前视图需使用完整缩放变换，不能只加平移量')
    return matrix[:, 2]


def transform_point(matrix, point):
    return matrix @ np.array([*point, 1])


def popup_template_score(frame, path, roi):
    template = cv2.imread(str(path))
    if template is None:
        raise RuntimeError(f'缺少弹窗识别资源：{path.name}')
    x, y, w, h = roi
    patch = frame[y:y+h, x:x+w]
    if patch.shape[0] < template.shape[0] or patch.shape[1] < template.shape[1]:
        return 0
    return float(cv2.minMaxLoc(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED))[1])


def work_plan_visible(frame):
    return popup_template_score(frame, IMAGES.parent / 'production/work_plan_title.png',
                                (235, 220, 270, 120)) >= .85


def seed_bar_close_point(frame, plot_centers):
    """Click actual blank map ground, not the footer text which doesn't dismiss the panel."""
    for x, y in ((500, 900), (250, 900), (360, 900), (500, 950), (250, 950)):
        if any(np.linalg.norm(np.array([x, y]) - center) < 90 for center in plot_centers):
            continue
        patch = frame[y-8:y+9, x-8:x+9]
        if np.max(patch.std(axis=(0, 1))) > 3:
            continue  # Reject NPCs, objects, borders and textured crops.
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        grass = cv2.inRange(hsv, (30, 60, 120), (45, 190, 255))
        road = cv2.inRange(hsv, (18, 40, 140), (28, 115, 255))
        if cv2.countNonZero(grass | road) / grass.size > .98:
            return x, y
    raise RuntimeError('没有确认的空白地图区域，不能关闭种子栏；不点击地块')


class ManualPlantingUI:
    def __enter__(self):
        # Reuse the project's controller/resource lifetime and PostMessage input wrapper.
        try:
            from .feed_production_ui import FeedProductionUI
        except ImportError:
            from feed_production_ui import FeedProductionUI
        self.backend = FeedProductionUI()
        self.backend.__enter__()
        try:
            self.profile = json.loads((ROOT / 'assets/catalog/manual_planting_ui.json').read_text(encoding='utf-8'))
            self.references = [(cv2.imread(str(IMAGES / view['image'])),
                                reference_matrix(view.get('canonicalMatrix', view.get('canonicalShift'))))
                               for view in self.profile['referenceViews']]
            self.marker = cv2.imread(str(IMAGES / 'seed_bar_marker.png'))
            if any(frame is None for frame, _ in self.references) or self.marker is None:
                raise RuntimeError('缺少手动种植定位资源')
            self.point = None
            return self
        except BaseException:
            self.backend.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        return self.backend.__exit__(*args)

    def check_supported(self, plan):
        check_profile(self.profile, plan)

    def bar_visible(self, frame):
        patch = frame[1084:1111, 511:608]
        return patch.shape == self.marker.shape and cv2.matchTemplate(patch, self.marker, cv2.TM_CCOEFF_NORMED)[0, 0] > .9

    def close_bar(self):
        frame = self.backend.frame()
        if self.bar_visible(frame):
            matrix = canonical_transform(self.references, frame)
            centers = [transform_point(matrix, point) for field in self.profile['fields'].values()
                       for point in field['slots'].values()]
            self.backend.click(*seed_bar_close_point(frame, centers))
        if self.bar_visible(self.backend.frame()):
            raise RuntimeError('种子栏关闭未确认，不进行拖动或点击地块')

    def prepare(self, plan):
        self.check_supported(plan)
        frame = self.backend.frame()
        if work_plan_visible(frame):
            if popup_template_score(frame, IMAGES / 'work_plan_dismiss.png', (245, 1195, 230, 65)) < .9:
                raise RuntimeError('工作计划关闭提示未确认，不点击弹窗')
            # Existing Maa dismissal point, outside the form and its save/reset controls.
            self.backend.click(360, 1220)
            frame = self.backend.frame()
            if work_plan_visible(frame):
                raise RuntimeError('工作计划关闭未确认，停止，不重复点击')
        # Prove the scene before sending even a navigation input.
        canonical_transform(self.references, frame)
        self.close_bar()

    def target(self, plan, frame):
        self.check_supported(plan)
        matrix = canonical_transform(self.references, frame)
        return transform_point(matrix, self.profile['fields'][plan['fieldId']]['slots'][str(plan['selectedSlots'][0])])

    def locate(self, plan):
        self.close_bar()
        try:
            from .maa_frame_normalize import post_swipe_normalized
        except ImportError:
            from maa_frame_normalize import post_swipe_normalized
        for attempt in range(4):
            frame = self.backend.frame()
            target = self.target(plan, frame)
            if 190 <= target[0] <= 530 and 340 <= target[1] <= 880:
                break
            if attempt == 3:
                raise RuntimeError('目标槽位未进入安全区域，不种植')
            delta = np.clip(np.array([360, 620]) - target, [-200, -220], [200, 220]).astype(int)
            # The seed bar was positively closed: map drags cannot seed a row.
            if not post_swipe_normalized(self.backend.controller, 360, 950,
                                         360 + int(delta[0]), 950 + int(delta[1]), 650).wait().succeeded:
                raise RuntimeError('地图拖动失败，不重试输入')
            time.sleep(.6)
        # Use the same audited soil-side point as planting. The tile origin can
        # overlap an employee's interaction area even while its soil is empty.
        open_point = target + np.array([25, 0])
        self.assert_soil(frame, open_point)
        self.backend.click(*map(int, open_point))
        if not self.bar_visible(self.backend.frame()):
            raise RuntimeError('空位种子栏未打开，不重复点击地块')
        self.select_crop(plan)
        frame = self.backend.frame()
        self.point = self.target(plan, frame) + np.array([25, 0])
        self.assert_ready(plan)

    def select_crop(self, plan):
        desired = CROPS.index(plan['cropItemId'])
        frame = self.backend.frame()
        # Three page dots; don't infer a page from an unrecognized toolbar.
        values = [frame[1285, x] for x in (329, 360, 391)]
        green = [i for i, (b, g, r) in enumerate(values) if int(g) > int(r) + 15 and int(g) > int(b) + 20]
        if len(green) != 1:
            raise RuntimeError('种子栏页码无法确认')
        page = green[0]
        while page != desired // 5:
            self.backend.click(660 if page < desired // 5 else 55, 1225)
            next_page = page + (1 if page < desired // 5 else -1)
            frame = self.backend.frame()
            b, g, r = map(int, frame[1285, (329, 360, 391)[next_page]])
            if not (g > r + 15 and g > b + 20):
                raise RuntimeError('种子栏翻页未确认，不重复翻页')
            page = next_page
        self.backend.click((143, 252, 361, 470, 579)[desired % 5], 1225)
        self.assert_crop(plan, self.backend.frame())

    def assert_crop(self, plan, frame):
        try:
            from .ensure_ranch_feed import recognized_text
        except ImportError:
            from ensure_ranch_feed import recognized_text
        if not self.bar_visible(frame):
            raise RuntimeError('种子栏不可见')
        name = ''.join(recognized_text(self.backend.tasker, frame, (100, 1055, 200, 55), (r'.*',)).split())
        if name != plan['cropName']:
            raise RuntimeError(f'所选种子名称未确认：{name!r}，不种植')
        left = (96, 205, 314, 423, 532)[CROPS.index(plan['cropItemId']) % 5]
        b, g, r = map(int, frame[1215, left + 2])
        if not (r > 180 and g > 100 and b < 130):
            raise RuntimeError('种子选中边框未确认')

    def assert_ready(self, plan):
        frame = self.backend.frame()
        self.assert_crop(plan, frame)
        target = self.target(plan, frame) + np.array([25, 0])
        if self.point is None or np.linalg.norm(target - self.point) > 3:
            raise RuntimeError('画面发生移动，停止而不改点')
        self.assert_soil(frame, self.point)

    def assert_soil(self, frame, point):
        x, y = map(int, point)
        if not (190 <= x <= 560 and 340 <= y <= 880):
            raise RuntimeError('种植点击不在安全地图区域')
        patch = cv2.cvtColor(frame[y-4:y+5, x-4:x+5], cv2.COLOR_BGR2HSV)
        soil = cv2.inRange(patch, (16, 120, 20), (28, 255, 230))
        if cv2.countNonZero(soil) / soil.size < .9:
            raise RuntimeError('点击位置不是空地或被角色遮挡；停止，不补点')

    def plant(self, plan):
        # The durable reservation is already written. No retry on any exception.
        self.assert_ready(plan)
        self.backend.click(*map(int, self.point))

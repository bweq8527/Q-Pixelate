"""Model-free sticker tracking worker, launched with isolated site-packages."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout


SCALES = (.8, .9, .95, 1., 1.05, 1.1, 1.2)


class TemplateTracker:
    def __init__(self, cv2, np, frame, rect):
        self.cv2 = cv2
        self.np = np
        height, width = frame.shape[:2]
        x, y, w, h = rect
        left, top = round(x * width), round(y * height)
        right, bottom = min(width, round((x + w) * width)), min(height, round((y + h) * height))
        if right - left < 5 or bottom - top < 5:
            raise ValueError('框选区域太小，请扩大区域后重试。')
        # Keep the first appearance to avoid gradually replacing the target with background.
        self.reference = frame[top:bottom, left:right].copy()
        self.size = right - left, bottom - top
        self.box = left, top, *self.size
        self.textured = float(cv2.meanStdDev(self.reference)[1].max()) >= 6

    def update(self, frame):
        cv2 = self.cv2
        height, width = frame.shape[:2]
        x, y, w, h = self.box
        margin_x = max(14, min(160, round(w * .7)))
        margin_y = max(14, min(160, round(h * .7)))
        left, top = max(0, x - margin_x), max(0, y - margin_y)
        window = frame[top:min(height, y + h + margin_y), left:min(width, x + w + margin_x)]
        ref_w, ref_h = self.size
        method = cv2.TM_CCOEFF_NORMED if self.textured else cv2.TM_SQDIFF_NORMED
        best_score = float('-inf')
        best_box = None
        sizes = {(max(5, round(w * step)), max(5, round(h * step))) for step in SCALES}
        for new_w, new_h in sorted(sizes):
            if new_w > window.shape[1] or new_h > window.shape[0]:
                continue
            interpolation = cv2.INTER_AREA if new_w < ref_w or new_h < ref_h else cv2.INTER_LINEAR
            template = cv2.resize(self.reference, (new_w, new_h), interpolation=interpolation)
            matches = cv2.matchTemplate(window, template, method)
            if self.textured:
                _, similarity, _, (col, row) = cv2.minMaxLoc(matches)
                if similarity < .59:
                    continue
            else:
                # Flat templates can match many places equally well. Prefer a box
                # near the last confirmed center to avoid jumping on a blank area.
                rows, cols = self.np.ogrid[:matches.shape[0], :matches.shape[1]]
                dx = (left + cols + (new_w - w) / 2 - x) / margin_x
                dy = (top + rows + (new_h - h) / 2 - y) / margin_y
                penalty, _, (col, row), _ = cv2.minMaxLoc(matches + .01 * (dx * dx + dy * dy))
                if matches[row, col] > .045:
                    continue
                similarity = 1 - penalty
            sample = window[row:row+new_h, col:col+new_w]
            difference = sum(cv2.mean(cv2.absdiff(template, sample))[:3]) / (3 * 255)
            if difference > .18:
                continue
            scale_penalty = .12 * (abs(new_w / w - 1) + abs(new_h / h - 1))
            score = similarity - difference - scale_penalty
            if score > best_score:
                best_box = left + col, top + row, new_w, new_h
                best_score = score
        if best_box is None:
            return None
        self.box = best_box
        x, y, w, h = best_box
        return x / width, y / height, w / width, h / height


def _run(args):
    sys.path.insert(0, args.site_packages)
    with redirect_stdout(sys.stderr):
        import cv2
        import numpy as np

    cv2.setNumThreads(1)
    print(json.dumps({'ready': True}), flush=True)
    width, height = args.width, args.height
    frame_size = width * height * 3
    tracker = None
    while raw := sys.stdin.buffer.read(frame_size):
        if len(raw) != frame_size:
            raise ValueError('追踪进程收到不完整的画面。')
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
        if tracker is None:
            tracker = TemplateTracker(cv2, np, frame, args.rect)
            match = args.rect
        else:
            match = tracker.update(frame)
        print(json.dumps(match, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-packages', required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--rect', type=float, nargs=4, required=True)
    try:
        _run(parser.parse_args())
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

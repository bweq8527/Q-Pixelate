"""Stream coverage masks for moving, rectangular mosaic layers."""

import math
from collections import defaultdict

from .model import frame_bounds


def mask_frames(effects, width, height, first, last, fps):
    starts, ends = defaultdict(list), defaultdict(list)
    for key, (edit, layer) in enumerate(effects):
        start, end = frame_bounds(edit, fps)
        if max(start, first) < min(end, last):
            origin = edit.start + min(0, layer.start)
            starts[max(start, first)].append((key, origin, layer))
            ends[min(end, last)].append(key)
    active = {}
    blank = bytes(width * height)
    white = b'\xff' * width
    for frame in range(first, last):
        for key in ends.get(frame, ()):
            del active[key]
        for key, start, layer in starts.get(frame, ()):
            active[key] = start, layer
        if not active:
            yield blank
            continue
        mask = bytearray(blank)
        for start, layer in active.values():
            # The effect interval may have been clipped by the parent selection.
            box = layer.mosaic_rect_at(frame/fps - start)
            x1 = max(0, math.floor(box.x * width))
            y1 = max(0, math.floor(box.y * height))
            x2 = min(width, math.ceil((box.x + box.w) * width))
            y2 = min(height, math.ceil((box.y + box.h) * height))
            if x1 < x2 and y1 < y2:
                row = white[:x2-x1]
                for y in range(y1, y2):
                    start_pixel = y * width + x1
                    mask[start_pixel:start_pixel+len(row)] = row
        yield mask

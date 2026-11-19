from __future__ import annotations

import bisect
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Rect:
    x: float = .25
    y: float = .25
    w: float = .5
    h: float = .5

    def validate(self):
        values = (self.x, self.y, self.w, self.h)
        if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                or self.x < 0 or self.y < 0 or self.w <= 0 or self.h <= 0
                or self.x + self.w > 1.000001 or self.y + self.h > 1.000001):
            raise ValueError('遮挡区域必须位于画面内，且宽高大于零。')


@dataclass
class Edit:
    start: float
    end: float
    effect: str = 'blur'
    rect: Rect = field(default_factory=Rect)
    strength: int = 20
    image: str = ''

    def validate(self, duration: float):
        if (any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in (self.start, self.end))
                or not 0 <= self.start < self.end <= duration + .00001):
            raise ValueError('片段时间必须满足 0 ≤ 入点 < 出点 ≤ 视频时长。')
        if self.effect not in ('blur', 'region', 'image'):
            raise ValueError('未知的遮挡效果。')
        self.rect.validate()
        if not isinstance(self.strength, int) or not 1 <= self.strength <= 100:
            raise ValueError('模糊强度须为 1–100。')
        if self.effect == 'image' and (not isinstance(self.image, str) or not Path(self.image).is_file()):
            raise ValueError('遮挡图片不存在，请重新选择素材。')


@dataclass
class Project:
    source: str
    edits: list[Edit] = field(default_factory=list)
    fingerprint: dict = field(default_factory=dict)

    def validate(self, duration=float('inf')):
        if not isinstance(self.source, str) or not self.source:
            raise ValueError('工程未指定源视频。')
        for edit in self.edits:
            edit.validate(duration)
        ordered = sorted(self.edits, key=lambda e: e.start)
        if any(a.end > b.start + 1e-8 for a, b in zip(ordered, ordered[1:])):
            raise ValueError('修订片段重叠，请调整入点和出点；相邻片段可以共用边界。')

    def check_source(self):
        path = Path(self.source)
        if not path.is_file():
            raise ValueError('源视频不存在，请重新打开视频。')
        stat = path.stat()
        current = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        if self.fingerprint and self.fingerprint != current:
            raise ValueError('源视频已发生变化，请重新打开视频并检查片段。')
        self.fingerprint = current

    def save(self, path):
        self.validate()
        self.check_source()
        path = Path(path).resolve()
        def relative(value):
            if not value:
                return ''
            try:
                return os.path.relpath(Path(value).resolve(), path.parent)
            except ValueError:
                return str(Path(value).resolve())
        edits = [asdict(e) | {'image': relative(e.image)} for e in self.edits]
        data = {'version': 1, 'source': relative(self.source),
                'fingerprint': self.fingerprint, 'edits': edits}
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             suffix='.tmp', delete=False) as stream:
                name = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(name, path)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != 1:
                raise ValueError('工程格式或版本不受支持。')
            def absolute(value):
                if not isinstance(value, str):
                    raise ValueError('工程路径无效。')
                return str((path.parent / value).resolve()) if value else ''
            edits = []
            for raw in data['edits']:
                raw = dict(raw)
                raw['rect'] = Rect(**raw.get('rect', {}))
                raw['image'] = absolute(raw.get('image', ''))
                edits.append(Edit(**raw))
            project = cls(absolute(data['source']), edits, data.get('fingerprint', {}))
            project.validate()
            project.check_source()
            return project
        except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'无法读取工程：{exc}') from exc


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    encode: bool


def frame_bounds(edit: Edit, fps: float) -> tuple[int, int]:
    return (math.ceil(edit.start * fps - 1e-6), math.ceil(edit.end * fps - 1e-6))


def plan_ranges(edits: list[Edit], keyframes: list[int], total_frames: int, fps: float) -> list[Span]:
    """GOP boundaries in presentation frames; effect times themselves remain unchanged."""
    keys = sorted(set([0, total_frames] + keyframes))
    expanded = []
    for edit in sorted(edits, key=lambda e: e.start):
        first, last = frame_bounds(edit, fps)
        first, last = max(0, first), min(total_frames, last)
        if first >= last:
            raise ValueError('片段短于一帧，且没有覆盖任何视频帧。')
        start = keys[max(0, bisect.bisect_right(keys, first) - 1)]
        end = keys[bisect.bisect_left(keys, last)]
        if expanded and start <= expanded[-1][1]:
            expanded[-1] = (expanded[-1][0], max(end, expanded[-1][1]))
        else:
            expanded.append((start, end))
    spans = []
    cursor = 0
    for start, end in expanded:
        if cursor < start:
            spans.append(Span(cursor, start, False))
        spans.append(Span(start, end, True))
        cursor = end
    if cursor < total_frames:
        spans.append(Span(cursor, total_frames, False))
    return spans

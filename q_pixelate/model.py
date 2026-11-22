from __future__ import annotations

import bisect
import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


DEFAULT_TRANSITION_DURATION = .3
VIDEO_TRANSITIONS = {'cut': '硬切', 'black': '淡出至黑 / 淡入', 'white': '淡出至白 / 淡入'}
AUDIO_TRANSITIONS = {'cut': '硬切', 'fade': '淡出至静音 / 淡入'}


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


def legacy_image_box(rect, width, height):
    """Match the coded-pixel overlay bounds used by v1/v2 projects."""
    x = min(width-2, int(rect.x * width) // 2 * 2)
    y = min(height-2, int(rect.y * height) // 2 * 2)
    right = min(width, math.ceil((rect.x+rect.w)*width/2-1e-9)*2)
    bottom = min(height, math.ceil((rect.y+rect.h)*height/2-1e-9)*2)
    return x, y, max(2, right-x), max(2, bottom-y)


@dataclass
class Transform:
    x: float = .5
    y: float = .5
    w: float = .5
    h: float = .5
    angle: float = 0

    @classmethod
    def from_rect(cls, rect):
        return cls(rect.x + rect.w / 2, rect.y + rect.h / 2, rect.w, rect.h)

    def validate(self):
        values = (self.x, self.y, self.w, self.h, self.angle)
        if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                or not (-4 <= self.x <= 5 and -4 <= self.y <= 5)
                or not (0 < self.w <= 8 and 0 < self.h <= 8)):
            raise ValueError('图层变换无效。')


@dataclass
class Mask:
    shape: str = 'rect'
    transform: Transform = field(default_factory=Transform)
    points: list[tuple[float, float]] = field(default_factory=list)
    feather: float = 0

    def validate(self):
        self.transform.validate()
        Edit(0, 1, 'region', shape=self.shape, points=self.points, feather=self.feather).validate(1)


@dataclass
class Layer:
    start: float
    end: float
    kind: str = 'blur'
    name: str = ''
    image: str = ''
    transform: Transform = field(default_factory=Transform)
    mask: Mask | None = None
    strength: int = 20
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    motion: list[tuple[float, Rect]] = field(default_factory=list)

    def _motion_rect_at(self, elapsed):
        index = bisect.bisect_right(self.motion, elapsed, key=lambda point: point[0])
        if index == 0:
            return self.motion[0][1]
        if index == len(self.motion):
            return self.motion[-1][1]
        a, first = self.motion[index-1]
        b, second = self.motion[index]
        t = (elapsed-a)/(b-a)
        return Rect(first.x+(second.x-first.x)*t, first.y+(second.y-first.y)*t,
                    first.w+(second.w-first.w)*t, first.h+(second.h-first.h)*t)

    def mosaic_rect_at(self, elapsed):
        """Interpolate source boxes, then apply the user's edit to the whole track."""
        reference, box = self.motion[0][1], self._motion_rect_at(elapsed)
        transform = self.transform
        sx, sy = transform.w/reference.w, transform.h/reference.h
        return Rect(transform.x-transform.w/2+(box.x-reference.x)*sx,
                    transform.y-transform.h/2+(box.y-reference.y)*sy, box.w*sx, box.h*sy)

    def sticker_at(self, elapsed):
        """Resolve the sticker's position and size relative to the tracked target."""
        if not self.motion:
            return self
        reference = Transform.from_rect(self.motion[0][1])
        target = Transform.from_rect(self._motion_rect_at(elapsed))
        transform = self.transform
        sx, sy = target.w/reference.w, target.h/reference.h
        return replace(self, motion=[], transform=Transform(
            target.x+(transform.x-reference.x)*sx, target.y+(transform.y-reference.y)*sy,
            transform.w*sx, transform.h*sy, transform.angle))

    def validate(self):
        if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in (self.start, self.end))
                or self.start >= self.end):
            raise ValueError('图层时间范围无效。')
        if self.kind not in ('blur', 'sticker', 'mosaic') or not isinstance(self.name, str) or not isinstance(self.id, str) or not self.id:
            raise ValueError('图层类型或名称无效。')
        if not isinstance(self.strength, int) or not 1 <= self.strength <= 100:
            raise ValueError('模糊强度或马赛克块大小须为 1–100。')
        if not isinstance(self.image, str) or (self.kind == 'sticker' and not Path(self.image).is_file()):
            raise ValueError('贴纸图片不存在，请重新选择素材。')
        self.transform.validate()
        if self.mask is not None:
            self.mask.validate()
        if self.kind == 'mosaic':
            if not self.motion or self.mask or self.image or self.transform.angle:
                raise ValueError('马赛克图层需要矩形轨迹。')
        elif self.motion:
            if self.kind == 'blur' and (self.mask or self.image or self.transform.angle):
                raise ValueError('追踪模糊图层需要矩形轨迹。')
            if self.kind == 'sticker' and self.mask is not None:
                raise ValueError('跟随贴纸不能添加遮罩。')
        if self.motion:
            previous = -math.inf
            for timestamp, box in self.motion:
                if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= previous:
                    raise ValueError('轨迹时间无效。')
                box.validate()
                previous = timestamp


@dataclass
class Edit:
    start: float
    end: float
    effect: str = 'blur'
    rect: Rect = field(default_factory=Rect)
    strength: int = 20
    image: str = ''
    speed: float = 1.0
    shape: str = 'rect'
    points: list[tuple[float, float]] = field(default_factory=list)
    feather: float = 0
    range_locked: bool = False
    locked: bool = False
    layers: list[Layer] | None = None
    omit: bool = False
    speed_effect: bool = False
    video_transition: str = 'cut'
    audio_transition: str = 'cut'
    transition_duration: float = DEFAULT_TRANSITION_DURATION

    def promote_layers(self, width, height):
        if self.layers is not None:
            return
        self.layers = []
        if self.effect == 'none':
            return
        transform = Transform.from_rect(self.rect)
        mask = None
        if self.effect == 'image':
            x, y, w, h = legacy_image_box(self.rect, width, height)
            transform = Transform.from_rect(Rect(x/width, y/height, w/width, h/height))
        elif self.effect == 'region':
            if self.shape == 'lasso':
                xs, ys = zip(*self.points)
                bounds = Rect(min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys))
                transform = Transform.from_rect(bounds)
                points = [((x-bounds.x)/bounds.w, (y-bounds.y)/bounds.h) for x, y in self.points]
            else:
                points = []
            mask = Mask(self.shape, transform, points, self.feather)
        self.layers.append(Layer(0, self.end-self.start, 'sticker' if self.effect == 'image' else 'blur',
                                 {'blur': '全屏模糊', 'region': '区域模糊', 'image': '贴纸'}[self.effect],
                                 self.image, transform, mask, self.strength))

    def layer_span(self, layer):
        return max(self.start, self.start + layer.start), min(self.end, self.start + layer.end)

    def validate(self, duration: float):
        if (any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in (self.start, self.end))
                or not 0 <= self.start < self.end <= duration + .00001):
            raise ValueError('片段时间必须满足 0 ≤ 入点 < 出点 ≤ 视频时长。')
        if self.effect not in ('blur', 'region', 'image', 'none'):
            raise ValueError('未知的遮挡效果。')
        if not isinstance(self.speed, (int, float)) or not math.isfinite(self.speed) or not .25 <= self.speed <= 4:
            raise ValueError('片段速度须为 0.25–4 倍。')
        if not isinstance(self.range_locked, bool) or not isinstance(self.locked, bool):
            raise ValueError('片段锁定状态无效。')
        if not isinstance(self.omit, bool) or not isinstance(self.speed_effect, bool):
            raise ValueError('段落效果状态无效。')
        if (not isinstance(self.video_transition, str) or self.video_transition not in VIDEO_TRANSITIONS
                or not isinstance(self.audio_transition, str) or self.audio_transition not in AUDIO_TRANSITIONS):
            raise ValueError('未知的删除接缝过渡效果。')
        if (type(self.transition_duration) not in (int, float)
                or not .05 <= self.transition_duration <= 1):
            raise ValueError('接缝两侧的过渡时长须为 0.05–1 秒。')
        if self.layers is not None:
            ids = set()
            for layer in self.layers:
                layer.validate()
                if layer.id in ids:
                    raise ValueError('片段包含重复的图层标识。')
                ids.add(layer.id)
            return
        if self.shape not in ('rect', 'ellipse', 'lasso'):
            raise ValueError('未知的遮罩形状。')
        if not isinstance(self.feather, (int, float)) or not math.isfinite(self.feather) or not 0 <= self.feather <= 20:
            raise ValueError('羽化范围须为 0–20%。')
        if not isinstance(self.points, list):
            raise ValueError('套索路径无效。')
        for point in self.points:
            if (not isinstance(point, (tuple, list)) or len(point) != 2 or
                    any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in point)):
                raise ValueError('套索路径必须位于画面内。')
        if self.shape == 'lasso':
            if len(self.points) < 3:
                raise ValueError('请绘制至少三个点的套索遮罩。')
            area = abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(self.points, self.points[1:]+self.points[:1])))
            if area < .000001:
                raise ValueError('套索遮罩面积太小。')
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

    def asset_paths(self):
        yield Path(self.source).resolve()
        for edit in self.edits:
            images = [layer.image for layer in edit.layers] if edit.layers is not None else [edit.image]
            yield from (Path(image).resolve() for image in images if image)

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
        edits = []
        has_transitions = False
        for edit in self.edits:
            raw = asdict(edit)
            if edit.layers is None:
                raw.pop('layers')
                raw['image'] = relative(edit.image)
            else:
                raw = {key: raw[key] for key in ('start', 'end', 'speed', 'locked', 'range_locked', 'layers')}
                for layer in raw['layers']:
                    layer['image'] = relative(layer['image'])
                    if not layer['motion']:
                        layer.pop('motion')
            for key in ('omit', 'speed_effect'):
                if getattr(edit, key):
                    raw[key] = True
                else:
                    raw.pop(key, None)
            for key in ('video_transition', 'audio_transition', 'transition_duration'):
                value = getattr(edit, key)
                if value != Edit.__dataclass_fields__[key].default:
                    raw[key] = value
                    has_transitions = True
                else:
                    raw.pop(key, None)
            edits.append(raw)
        version = (7 if has_transitions else
                   6 if any(e.omit or e.speed_effect or any(layer.kind == 'blur' and layer.motion for layer in e.layers or [])
                            for e in self.edits)
                   else 5 if any(layer.kind == 'sticker' and layer.motion for e in self.edits for layer in e.layers or [])
                   else 4 if any(layer.kind == 'mosaic' for e in self.edits for layer in e.layers or [])
                   else 3 if any(e.layers is not None for e in self.edits) else 2)
        data = {'version': version, 'source': relative(self.source),
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
            if not isinstance(data, dict) or data.get('version') not in (1, 2, 3, 4, 5, 6, 7):
                raise ValueError('工程格式或版本不受支持。')
            def absolute(value):
                if not isinstance(value, str):
                    raise ValueError('工程路径无效。')
                return str((path.parent / value).resolve()) if value else ''
            edits = []
            for raw in data['edits']:
                raw = dict(raw)
                if 'layers' in raw:
                    layers = []
                    for item in raw['layers']:
                        item = dict(item)
                        item['image'] = absolute(item.get('image', ''))
                        item['transform'] = Transform(**item.get('transform', {}))
                        if 'motion' in item:
                            item['motion'] = [(timestamp, Rect(**box)) for timestamp, box in item['motion']]
                        if item.get('mask') is not None:
                            mask = dict(item['mask'])
                            mask['transform'] = Transform(**mask.get('transform', {}))
                            mask['points'] = [tuple(p) for p in mask.get('points', [])]
                            item['mask'] = Mask(**mask)
                        layers.append(Layer(**item))
                    edits.append(Edit(effect='none', **(raw | {'layers': layers})))
                    continue
                raw['rect'] = Rect(**raw.get('rect', {}))
                raw['image'] = absolute(raw.get('image', ''))
                if isinstance(raw.get('points', []), list):
                    raw['points'] = [tuple(p) if isinstance(p, list) else p for p in raw.get('points', [])]
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

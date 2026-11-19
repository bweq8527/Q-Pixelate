from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .process import Runner


@dataclass
class MediaInfo:
    path: str
    width: int
    height: int
    duration: float
    fps: Fraction
    video: dict
    audio: list[dict]
    raw: dict

    @property
    def fps_float(self):
        return float(self.fps)

    def fast_mode_error(self):
        v = self.video
        if v.get('codec_name') != 'h264':
            return '首版快速导出支持 H.264 视频；该文件的编码为 ' + str(v.get('codec_name'))
        if v.get('pix_fmt') not in ('yuv420p', 'yuvj420p'):
            return '首版快速导出仅支持 8-bit 4:2:0 视频。'
        if v.get('field_order') not in ('progressive', 'unknown', None):
            return '首版不支持隔行扫描视频的快速导出。'
        if v.get('color_transfer') in ('smpte2084', 'arib-std-b67') or v.get('color_primaries') == 'bt2020':
            return '首版不支持 HDR 视频的快速导出。'
        if any(abs(float(s.get('rotation', 0))) > .01 for s in v.get('side_data_list', [])):
            return '首版不支持带旋转信息的视频快速导出。'
        if abs(float(v.get('start_time', 0))) > .002:
            return '首版快速导出需要从零开始的视频时间戳。'
        if not 1 <= self.fps_float <= 240 or self.width % 2 or self.height % 2:
            return '视频帧率或画面尺寸不在首版快速导出的支持范围内。'
        return ''


@dataclass
class VideoIndex:
    frames: int
    keyframes: list[int]
    pts: list[float]
    key_dts: dict[int, float]


def probe(path, runner: Runner) -> MediaInfo:
    path = str(Path(path).resolve())
    if not Path(path).is_file():
        raise ValueError('视频文件不存在。')
    raw = json.loads(runner.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
    videos = [s for s in raw.get('streams', []) if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise ValueError('文件中没有可播放的视频轨。')
    v = videos[0]
    fps = Fraction(v.get('avg_frame_rate') or '0/1')
    if fps <= 0:
        fps = Fraction(v.get('r_frame_rate') or '0/1')
    duration = float(v.get('duration') or raw.get('format', {}).get('duration') or 0)
    if duration <= 0 or fps <= 0 or not math.isfinite(duration):
        raise ValueError('无法读取有效的视频时长或帧率。')
    return MediaInfo(path, int(v['width']), int(v['height']), duration, fps, v,
                     [s for s in raw.get('streams', []) if s.get('codec_type') == 'audio'], raw)


def index_video(info: MediaInfo, runner: Runner) -> VideoIndex:
    runner.report(2, '正在扫描视频关键帧和时间戳…')
    text = runner.run(['ffprobe', '-v', 'error', '-select_streams', str(info.video['index']),
                       '-show_packets', '-show_entries', 'packet=pts_time,dts_time,flags',
                       '-of', 'compact=p=0:nk=0', info.path])
    pts = []
    keys = []
    key_dts = {}
    for n, line in enumerate(text.splitlines()):
        if n % 10000 == 0:
            runner.check()
        fields = dict(part.split('=', 1) for part in line.split('|') if '=' in part)
        if fields.get('pts_time', 'N/A') == 'N/A':
            continue
        value = float(fields['pts_time'])
        pts.append(value)
        if 'K' in fields.get('flags', ''):
            keys.append(value)
            if fields.get('dts_time', 'N/A') != 'N/A':
                key_dts[round(value * info.fps_float)] = float(fields['dts_time'])
    pts.sort()
    if not pts:
        raise ValueError('无法建立视频帧索引。')
    # Allow millisecond container quantization, but reject VFR and timestamp gaps.
    tolerance = min(.0021, .1 / info.fps_float)
    if any(abs(time - i / info.fps_float) > tolerance for i, time in enumerate(pts)):
        raise ValueError('检测到可变帧率、非零时间戳或时间戳缺口，无法保证快速拼接。请先转为固定帧率录屏。')
    keyframes = sorted(set(round(t * info.fps_float) for t in keys))
    if not keyframes or keyframes[0] != 0:
        raise ValueError('视频没有有效的起始关键帧，无法快速拼接。')
    return VideoIndex(len(pts), keyframes, pts, key_dts)

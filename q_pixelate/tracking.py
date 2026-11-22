from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .detection import SCENE_TIME, _child, _decode_command, _diagnostic
from .media import MediaInfo, index_video
from .model import Edit, Rect, frame_bounds
from .process import Runner


@dataclass
class TrackingResult:
    points: list[tuple[float, Rect]]
    end: float
    reason: str


def _worker_command(python, site_packages, width, height, rect):
    script = Path(__file__).resolve().with_name('tracking_worker.py')
    return [str(Path(python).resolve()), '-I', '-S', '-X', 'utf8', str(script),
            '--site-packages', str(Path(site_packages).resolve()), '--width', str(width), '--height', str(height),
            '--rect', *(str(v) for v in (rect.x, rect.y, rect.w, rect.h))]


def _reply(worker):
    line = worker.stdout.readline()
    if not line:
        raise ValueError('追踪进程提前退出。')
    reply = json.loads(line)
    if isinstance(reply, dict) and 'error' in reply:
        raise ValueError(reply['error'])
    return reply


def _first_cut(log, fps):
    while line := log.readline():
        if not line.endswith(b'\n'):
            # The decoder may still be writing this line.
            log.seek(-len(line), os.SEEK_CUR)
            break
        if match := SCENE_TIME.search(line):
            return round(float(match[1]) * fps)
    return None


def _stream(decoder, worker, scene_log, frame_bytes, frames, fps, rect, updates):
    if _reply(worker) != {'ready': True}:
        raise ValueError('追踪进程未完成初始化。')

    points = []
    cut = None
    misses = 0
    last_progress = 5
    lost = '目标持续丢失，已停止追踪。'
    for number in frames:
        frame = decoder.stdout.read(frame_bytes)
        if len(frame) != frame_bytes:
            raise ValueError(f'解码缺少第 {number + 1} 源帧，无法确定追踪位置。')
        # scdet logs a cut before its frame reaches the rawvideo output. The reader has
        # a separate file handle so seeking it cannot disturb the decoder's writes.
        if cut is None:
            cut = _first_cut(scene_log, fps)
        if cut is not None and number >= cut and points:
            return points, '检测到切镜，已停止追踪。'

        worker.stdin.write(frame)
        worker.stdin.flush()
        match = _reply(worker)
        if match is None:
            misses += 1
            if misses == 3:
                return points, lost
        else:
            bounds = rect if number == frames.start else Rect(*match)
            bounds.validate()
            points.append((number, bounds))
            misses = 0
        progress = 5 + 94 * (number - frames.start + 1) // len(frames)
        if progress != last_progress:
            updates.put(('progress', progress))
            last_progress = progress

    if decoder.stdout.read(1):
        raise ValueError('解码帧数与视频索引不一致。')
    worker.stdin.close()
    if decoder.wait() or worker.wait():
        raise ValueError('解码或追踪进程异常退出。')
    return points, lost if misses else '已到达选区末端。'


def _follow(decode_args, worker_args, width, height, frames, fps, rect, runner):
    updates = queue.Queue()

    def process_frames():
        try:
            updates.put(('done', _stream(decoder, worker, scene_log, width * height * 3,
                                         frames, fps, rect, updates)))
        except Exception as exc:
            updates.put(('error', exc))

    with runner.workspace('qpixel-track-') as directory, \
            (directory / 'scene.log').open('w+b') as decode_err, \
            (directory / 'scene.log').open('rb') as scene_log, \
            (directory / 'worker.log').open('w+b') as worker_err:
        with _child(decode_args, subprocess.DEVNULL, decode_err, runner) as decoder, \
                _child(worker_args, subprocess.PIPE, worker_err, runner) as worker:
            thread = threading.Thread(target=process_frames, daemon=True)
            thread.start()
            try:
                while True:
                    runner.check()
                    try:
                        status, payload = updates.get(timeout=.05)
                    except queue.Empty:
                        continue
                    if status != 'progress':
                        break
                    runner.report(payload, '正在追踪框选目标…')
            finally:
                for process in (decoder, worker):
                    if process.poll() is None:
                        process.kill()
                thread.join()
        if status == 'error':
            for log in (worker_err, scene_log):
                if detail := _diagnostic(log):
                    runner.log(detail)
            raise ValueError(f'追踪失败：{payload}') from payload
    return payload


def track(info: MediaInfo, edit: Edit, runner: Runner, *, start, rect: Rect, python,
          site_packages) -> TrackingResult:
    """Follow one selected rectangle using absolute CFR source times and an exclusive end."""
    rect.validate()
    if not 0 <= edit.start <= start < edit.end <= info.duration + 1e-5:
        raise ValueError('追踪起点和选区必须位于源视频内。')
    if error := info.fast_mode_error():
        raise ValueError(error)
    index = index_video(info, runner)
    if not all(map(math.isfinite, index.pts)):
        raise ValueError('视频中存在无效的时间戳。')
    fps = info.fps_float
    first, end = frame_bounds(edit, fps)
    end = min(end, index.frames)
    if first >= end:
        raise ValueError('选区中没有可追踪的源视频帧。')
    first = min(end - 1, max(first, math.floor(start * fps + .5)))
    frames = range(first, end)
    ffmpeg = runner.executable('ffmpeg')
    if not ffmpeg:
        raise ValueError('找不到 FFmpeg。请在环境与组件页面配置视频工具。')
    scale = min(1, 960 / max(info.width, info.height))
    width, height = max(1, round(info.width * scale)), max(1, round(info.height * scale))
    runner.report(5, '正在启动追踪进程…')
    decode_args = _decode_command(ffmpeg, info, index, frames, end, width, height)
    worker_args = _worker_command(python, site_packages, width, height, rect)
    points, reason = _follow(decode_args, worker_args, width, height, frames, fps, rect, runner)
    if not points:
        raise ValueError('无法从框选区域开始追踪。')
    runner.report(100, reason)
    return TrackingResult([(number / fps, bounds) for number, bounds in points],
                          min(edit.end, (points[-1][0] + 1) / fps), reason)

from __future__ import annotations

import bisect
import json
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .media import MediaInfo, index_video
from .model import Edit, Rect, frame_bounds
from .process import Runner


PROVIDERS = {'cpu': 'CPUExecutionProvider', 'gpu': 'CUDAExecutionProvider'}
MAX_TRACK_INTERVAL = .35
SCENE_TIME = re.compile(rb'lavfi\.scd\.time: ([-+0-9.eE]+)')


@dataclass
class DetectionTrack:
    id: str
    label: str
    score: float
    start: float
    end: float
    points: list[tuple[float, Rect]]


def detector_command(python, site_packages, model, device, *, probe=False, runtime_probe=False,
                     width=None, height=None, profile_dir=None):
    if device not in PROVIDERS:
        raise ValueError('请选择 CPU 或 NVIDIA GPU 识别环境。')
    script = Path(__file__).resolve().with_name('detector_worker.py')
    args = [str(Path(python).resolve()), '-I', '-S', '-X', 'utf8', str(script),
            '--site-packages', str(Path(site_packages).resolve()), '--provider', PROVIDERS[device]]
    if runtime_probe:
        return args + ['--runtime-probe']
    args += ['--model', str(Path(model).resolve())]
    if probe:
        return args + ['--probe']
    args += ['--width', str(width), '--height', str(height)]
    if profile_dir is not None:
        args += ['--profile-dir', str(profile_dir)]
    return args


def _decode_command(ffmpeg, info, index, samples, end, width, height):
    first = samples.start
    key = index.keyframes[bisect.bisect_right(index.keyframes, first)-1]
    # Seek just before the indexed keyframe, preserving absolute timestamps. The
    # trim edges sit halfway between CFR frames, clear of timestamp rounding.
    seek = max(0, index.pts[key] - .003)
    trim_start = max(0, (first - .5) / info.fps_float)
    trim_end = (end - .5) / info.fps_float
    filters = [f'trim=start={trim_start:.9f}:end={trim_end:.9f}', 'scdet=t=12']
    if samples.step > 1:
        frame = f'round(t*{info.fps.numerator}/{info.fps.denominator})'
        filters.append(f"select='not(mod({frame}-{first},{samples.step}))'")
    filters.append(f'scale={width}:{height}:flags=area,format=bgr24')
    return [ffmpeg, '-hide_banner', '-v', 'info', '-nostats', '-nostdin', '-xerror', '-copyts',
            '-seek_timestamp', '1', '-ss', f'{seek:.9f}', '-noautorotate', '-i', info.path,
            '-map', f"0:{info.video['index']}", '-an', '-sn', '-dn', '-vf', ','.join(filters),
            '-fps_mode', 'passthrough', '-threads', '1', '-c:v', 'rawvideo', '-f', 'rawvideo', 'pipe:1']


@contextmanager
def _child(args, stdin, stderr, runner):
    runner.log(subprocess.list2cmdline(args))
    process = subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        if process.stdin is not None:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass


def _diagnostic(stderr):
    end = stderr.seek(0, os.SEEK_END)
    stderr.seek(max(0, end - 5000))
    return stderr.read().decode('utf-8', errors='replace').strip()


def _worker_reply(worker):
    line = worker.stdout.readline()
    if not line:
        raise ValueError('识别进程提前退出。')
    reply = json.loads(line)
    if isinstance(reply, dict) and 'error' in reply:
        raise ValueError(reply['error'])
    return reply


def _stream(decoder, worker, provider, width, height, samples, updates):
    ready = _worker_reply(worker)
    if ready.get('provider') != provider:
        raise ValueError('识别进程未使用所选设备。')
    frame_bytes = width * height * 3
    results = []
    last_percent = 5
    for position, frame_number in enumerate(samples, 1):
        frame = decoder.stdout.read(frame_bytes)
        if len(frame) != frame_bytes:
            raise ValueError(f'解码缺少第 {frame_number + 1} 源帧，无法确定识别位置。')
        worker.stdin.write(frame)
        worker.stdin.flush()
        hits = [(label, float(score), Rect(*bounds)) for label, score, bounds in _worker_reply(worker)]
        for _, _, bounds in hits:
            bounds.validate()
        if hits:
            results.append((frame_number, hits))
        percent = 5 + 90*position//len(samples)
        if percent != last_percent:
            updates.put(('progress', percent))
            last_percent = percent
    if decoder.stdout.read(1):
        raise ValueError('解码帧数与视频索引不一致。')
    worker.stdin.close()
    if decoder.wait() or worker.wait():
        raise ValueError('解码或识别进程异常退出。')
    return results


def _scan_frames(decode_args, worker_args, provider, width, height, samples, runner):
    updates = queue.Queue()

    def process_frames():
        try:
            updates.put(('done', _stream(decoder, worker, provider, width, height, samples, updates)))
        except Exception as exc:
            updates.put(('error', exc))

    with tempfile.TemporaryFile() as decode_err, tempfile.TemporaryFile() as worker_err:
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
                    runner.report(payload, '正在识别选区画面…')
            finally:
                # Killing both processes unblocks all pipe I/O before their streams close.
                for process in (decoder, worker):
                    if process.poll() is None:
                        process.kill()
                thread.join()
        if status == 'error':
            for stderr in (worker_err, decode_err):
                detail = _diagnostic(stderr)
                if detail:
                    runner.log(detail)
            raise ValueError(f'自动识别失败：{payload}') from payload
        decode_err.seek(0)
        scene_times = [float(match[1]) for line in decode_err if (match := SCENE_TIME.search(line))]
    return payload, scene_times


def _match_quality(previous, current):
    overlap_width = max(0, min(previous.x + previous.w, current.x + current.w) - max(previous.x, current.x))
    overlap_height = max(0, min(previous.y + previous.h, current.y + current.h) - max(previous.y, current.y))
    overlap = overlap_width * overlap_height
    iou = overlap / (previous.w * previous.h + current.w * current.h - overlap)
    if iou >= .12:
        return 1 + iou
    x = (previous.x - current.x) + (previous.w - current.w)/2
    y = (previous.y - current.y) + (previous.h - current.h)/2
    distance = math.hypot(x / max(previous.w, current.w), y / max(previous.h, current.h))
    return max(0, .65-distance)


def _group_tracks(results, cuts, step, fps, runner):
    tracks = []
    active = []
    previous = -1
    sparse = step > 1 and step/fps > MAX_TRACK_INTERVAL
    for number, hits in results:
        runner.check()
        index = bisect.bisect_right(cuts, previous)
        if number-previous != step or sparse or (index < len(cuts) and cuts[index] <= number):
            active = []
        candidates = []
        for track_index, track in enumerate(active):
            for hit_index, (label, _, rect) in enumerate(hits):
                if label == track.label and (quality := _match_quality(track.points[-1][1], rect)):
                    candidates.append((-quality, track_index, hit_index))
        matched_hits = {}
        matched_tracks = set()
        for _, track_index, hit_index in sorted(candidates):
            if hit_index not in matched_hits and track_index not in matched_tracks:
                matched_hits[hit_index] = active[track_index]
                matched_tracks.add(track_index)
        start, end = number/fps, (number+1)/fps
        active = []
        for hit_index, (label, score, rect) in enumerate(hits):
            track = matched_hits.get(hit_index)
            if track is None:
                track = DetectionTrack(uuid.uuid4().hex, label, score, start, end, [])
                tracks.append(track)
            track.end = end
            track.score = max(track.score, score)
            track.points.append((start, rect))
            active.append(track)
        previous = number
    return tracks


def scan(info: MediaInfo, edit: Edit, runner: Runner, *, python, site_packages, model, device,
         sample_fps=None) -> list[DetectionTrack]:
    """Scan the selection on the source time axis. Each track's end is exclusive."""
    error = info.fast_mode_error()
    if error:
        raise ValueError(error)
    if not 0 <= edit.start < edit.end <= info.duration + .00001:
        raise ValueError('识别选区的时间超出视频范围。')
    if sample_fps is not None and not (math.isfinite(sample_fps) and sample_fps >= 1):
        raise ValueError('快速扫描频率须为每秒至少 1 帧。')
    index = index_video(info, runner)
    if not all(map(math.isfinite, index.pts)):
        raise ValueError('视频中存在无效的时间戳。')
    fps = info.fps_float
    start, end = frame_bounds(edit, fps)
    end = min(end, index.frames)
    if start >= end:
        raise ValueError('识别选区中没有完整的源视频帧。')
    step = max(1, round(fps/sample_fps)) if sample_fps is not None else 1
    samples = range(start, end, step)
    ffmpeg = runner.executable('ffmpeg')
    if not ffmpeg:
        raise ValueError('找不到 FFmpeg。请在环境与组件页面配置视频工具。')
    scale = min(1, 640 / max(info.width, info.height))
    width, height = max(1, round(info.width*scale)), max(1, round(info.height*scale))
    runner.report(5, '正在启动识别进程…')
    decode_args = _decode_command(ffmpeg, info, index, samples, end, width, height)
    with tempfile.TemporaryDirectory(prefix='qpixel-detect-') as temp:
        worker_args = detector_command(python, site_packages, model, device, width=width, height=height,
                                       profile_dir=temp)
        results, scene_times = _scan_frames(decode_args, worker_args, PROVIDERS[device], width, height,
                                            samples, runner)
    runner.report(96, '正在整理识别结果…')
    cuts = sorted(round(time*fps) for time in scene_times)
    tracks = _group_tracks(results, cuts, step, fps, runner)
    runner.report(100, '选区识别完成。')
    return tracks

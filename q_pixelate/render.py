from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .media import index_video, probe
from .model import Project, plan_ranges, frame_bounds
from .process import Runner
from .masks import mask_image


FFMPEG = ['ffmpeg', '-hide_banner', '-v', 'warning', '-nostdin', '-y']


@dataclass
class ExportReport:
    path: str
    encoded_seconds: float
    copied_seconds: float
    frames: int
    audio_tracks: int


def require_idr(info, frame, runner):
    """A keyframe flag alone is insufficient for an independently copied closed GOP."""
    timestamp = frame / info.fps_float
    data = json.loads(runner.run(['ffprobe', '-v', 'error', '-select_streams', str(info.video['index']),
        '-read_intervals', f'{timestamp + .0001:.9f}%+#1', '-show_packets', '-show_data',
        '-show_entries', 'packet=pts_time,data', '-of', 'json', info.path]))
    packets = data.get('packets', [])
    if not packets or abs(float(packets[0].get('pts_time', '-999')) - timestamp) > .0021:
        raise ValueError('无法精确定位拼接关键帧，首版无法保证快速导出正确。')
    hex_lines = []
    for line in packets[0].get('data', '').splitlines():
        if ':' in line:
            hex_lines.append(line.split(':', 1)[1].strip().split('  ', 1)[0].replace(' ', ''))
    payload = bytes.fromhex(''.join(hex_lines))
    length_size = int(info.video.get('nal_length_size', 4))
    offset = 0
    while offset + length_size < len(payload):
        length = int.from_bytes(payload[offset:offset+length_size], 'big')
        offset += length_size
        if not length or offset+length > len(payload):
            break
        if payload[offset] & 31 == 5:
            return
        offset += length
    raise ValueError('拼接边界不是独立 IDR 帧，可能使用了开放 GOP；首版拒绝快速拼接以免重复或丢失画面。')


def effect_graph(edits, width, height, offset, workspace, fps, scale=None, speed=1):
    """Stage assets under safe names; user paths never appear inside filter syntax."""
    graph = ['[0:v:0]setpts=PTS-STARTPTS[v0]']
    inputs = []
    image_index = 0
    for i, edit in enumerate(edits):
        before, after = f'v{i}', f'v{i+1}'
        first, last = frame_bounds(edit, fps)
        offset_frame = round(offset * fps)
        enable = f"gte(n,{first-offset_frame})*lt(n,{last-offset_frame})"
        radius = max(1, min(edit.strength, width // 4, height // 4))
        rect = edit.rect
        x = min(width-2, int(rect.x * width) // 2 * 2)
        y = min(height-2, int(rect.y * height) // 2 * 2)
        right = min(width, math.ceil((rect.x+rect.w)*width/2-1e-9)*2)
        bottom = min(height, math.ceil((rect.y+rect.h)*height/2-1e-9)*2)
        w, h = max(2, right-x), max(2, bottom-y)
        if edit.effect == 'blur':
            graph.append(f"[{before}]boxblur=luma_radius={radius}:luma_power=2:chroma_radius={max(1, radius//2)}:enable='{enable}'[{after}]")
        elif edit.effect == 'region':
            image_index += 1
            asset = workspace / f'mask-{i}.png'
            if not mask_image(edit, width, height).save(str(asset)):
                raise ValueError('无法写入遮罩。')
            inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(asset)]
            graph.extend([f'[{before}]format=gbrp,split[base{i}][crop{i}]',
                          f'[crop{i}]boxblur={radius}:2[blur{i}]',
                          f'[{image_index}:v]format=gbrp[maskraw{i}]'])
            if edit.feather:
                sigma = max(.1, edit.feather*min(width, height)/100/3)
                graph.extend([f'[maskraw{i}]split[hard{i}][soft{i}]',
                              f'[soft{i}]gblur=sigma={sigma:.6f}[feather{i}]',
                              f'[hard{i}][feather{i}]blend=all_mode=multiply[mask{i}]'])
            else:
                graph.append(f'[maskraw{i}]null[mask{i}]')
            graph.append(f"[base{i}][blur{i}][mask{i}]maskedmerge=enable='{enable}'[{after}]")
        elif edit.effect == 'image':
            image_index += 1
            suffix = Path(edit.image).suffix.lower()
            asset = workspace / f'asset-{i}{suffix}'
            shutil.copyfile(edit.image, asset)
            inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(asset)]
            graph.extend([f'[{image_index}:v]scale={w}:{h},format=rgba[image{i}]',
                          f"[{before}][image{i}]overlay={x}:{y}:enable='{enable}':shortest=1[{after}]"])
        else:
            graph.append(f'[{before}]null[{after}]')
    last = f'v{len(edits)}'
    if speed != 1:
        graph.append(f'[{last}]setpts=(PTS-STARTPTS)/{speed:.12f},fps={fps}[retimed]')
        last = 'retimed'
    if scale:
        graph.append(f"[{last}]scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,scale=w='min({scale},iw)':h=-2[out]")
    else:
        graph.append(f'[{last}]null[out]')
    return inputs, ';'.join(graph)


def speed_sections(edits, start, end, fps):
    cuts = {start, end}
    speeds = []
    for edit in edits:
        if edit.speed != 1:
            a, b = frame_bounds(edit, fps)
            if a < end and b > start:
                cuts.update((max(a, start), min(b, end)))
                speeds.append((a, b, edit.speed))
    cuts = sorted(cuts)
    return [(a, b, next((s for x, y, s in speeds if x <= a < y), 1))
            for a, b in zip(cuts, cuts[1:])]


def output_frames(start, end, speed):
    return max(1, round((end-start)/speed))


def tempo_chain(speed):
    factors = []
    while speed < .5:
        factors.append(.5)
        speed /= .5
    while speed > 2:
        factors.append(2)
        speed /= 2
    return ','.join(f'atempo={s:.12f}' for s in factors+[speed])


def audio_graph(info, edits, start, end, input_index):
    sections = speed_sections(edits, start, end, info.fps_float)
    graph, maps = [], []
    for track in range(len(info.audio)):
        labels = ''.join(f'[a{track}s{i}]' for i in range(len(sections)))
        # Keep delayed tracks aligned with video; STARTPTS alone would pull their
        # first audible sample to zero and permanently lose the initial silence.
        graph.append(f'[{input_index}:a:{track}]aresample=async=1:first_pts=0,asplit={len(sections)}{labels}')
        for i, (a, b, speed) in enumerate(sections):
            count = output_frames(a, b, speed)
            duration = count/info.fps_float
            effective = (b-a)/count
            graph.append(f'[a{track}s{i}]atrim=start={a/info.fps_float:.12f}:end={b/info.fps_float:.12f},'
                         f'asetpts=PTS-STARTPTS,{tempo_chain(effective)},apad,atrim=duration={duration:.12f}[a{track}p{i}]')
        graph.append(''.join(f'[a{track}p{i}]' for i in range(len(sections)))+
                     f'concat=n={len(sections)}:v=0:a=1[aout{track}]')
        maps += ['-map', f'[aout{track}]']
    return ';'.join(graph), maps


def _encoder(info):
    profiles = {'High': 'high', 'Main': 'main', 'Constrained Baseline': 'baseline', 'Baseline': 'baseline'}
    profile = profiles.get(info.video.get('profile'), 'high')
    args = ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', info.video['pix_fmt'],
            '-profile:v', profile, '-r', str(info.fps), '-fps_mode', 'cfr',
            '-bf', str(min(2, int(info.video.get('has_b_frames', 0)))),
            '-x264-params', 'open-gop=0:repeat-headers=1:scenecut=0']
    level = int(info.video.get('level', 0))
    if level > 0:
        args += ['-level:v', f'{level / 10:.1f}']
    for key, option in [('color_space', '-colorspace'), ('color_transfer', '-color_trc'),
                        ('color_primaries', '-color_primaries'), ('color_range', '-color_range')]:
        value = info.video.get(key)
        if value and value not in ('unknown', 'unspecified'):
            args += [option, value]
    sar = info.video.get('sample_aspect_ratio', '1:1')
    if sar not in ('N/A', '0:1'):
        args += ['-aspect', f'{info.width * int(sar.split(":")[0])}:{info.height * int(sar.split(":")[1])}']
    return args


class Exporter:
    def export(self, project: Project, destination, runner: Runner, overwrite=False) -> ExportReport:
        runner.check()
        project.check_source()
        destination = Path(destination).resolve()
        protected = [Path(project.source).resolve()] + [Path(e.image).resolve() for e in project.edits if e.image]
        if destination in protected:
            raise ValueError('输出不能覆盖源视频或遮挡素材。')
        if destination.suffix.lower() not in ('.mp4', '.mkv'):
            raise ValueError('请选择 MP4 或 MKV 输出文件。')
        if destination.exists() and not overwrite:
            raise ValueError('输出文件已存在，请选择新路径或明确确认覆盖。')
        info = probe(project.source, runner)
        project.validate(info.duration)
        if project.edits and info.fast_mode_error():
            raise ValueError(info.fast_mode_error())
        # Only preserve tracks the destination container can hold without transcoding.
        if destination.suffix.lower() == '.mp4' and any(a.get('codec_name') not in ('aac', 'mp3', 'ac3', 'eac3', 'alac') for a in info.audio):
            raise ValueError('MP4 不能直接保留该音轨编码，请选择 MKV 输出。')
        if shutil.disk_usage(destination.parent).free < Path(project.source).stat().st_size * 2.2:
            raise ValueError('输出磁盘空间不足；请预留至少约源视频 2.2 倍空间。')
        with tempfile.TemporaryDirectory(prefix='.qpixel-', dir=destination.parent) as directory:
            work = Path(directory)
            pending = work / ('result' + destination.suffix)
            if not project.edits:
                runner.report(10, '没有修订片段，正在直接复制视频和音频…')
                runner.run(FFMPEG + ['-i', info.path, '-map', f'0:{info.video["index"]}', '-map', '0:a?',
                                    '-c', 'copy', '-avoid_negative_ts', 'disabled', '-progress', 'pipe:1', str(pending)], info.duration, 10, 85)
                frame_count = int(info.video.get('nb_frames', 0))
                encoded = 0
            else:
                index = index_video(info, runner)
                spans = plan_ranges(project.edits, index.keyframes, index.frames, info.fps_float)
                for span in spans:
                    require_idr(info, span.start, runner)
                encoded = sum(s.end-s.start for s in spans if s.encode) / info.fps_float
                retimed = any(e.speed != 1 for e in project.edits)
                pieces = [(a, b, speed, span.encode) for span in spans
                          for a, b, speed in speed_sections(project.edits, span.start, span.end, info.fps_float)]
                expected_frames = sum(output_frames(a, b, speed) for a, b, speed, _ in pieces)
                runner.log(f'计划：总帧数 {index.frames}，局部重编码 {encoded:.3f}s，其余直接复制。')
                files = []
                processed = 0
                for i, (first, last, speed, encode) in enumerate(pieces):
                    runner.check()
                    count = last - first
                    result_count = output_frames(first, last, speed)
                    duration = result_count / info.fps_float
                    start = first / info.fps_float
                    target = work / f'part-{i:04d}.ts'
                    base, weight = 5 + 75 * processed / index.frames, 75 * count / index.frames
                    runner.report(base, f'{"局部编码" if encode else "直接复制"} {i+1}/{len(pieces)} · {start:.2f}–{last/info.fps_float:.2f}s')
                    args = FFMPEG + ['-seek_timestamp', '1', '-ss', f'{start:.9f}', '-i', info.path]
                    if encode:
                        edits = [e for e in project.edits if e.start < last/info.fps_float and e.end > start]
                        inputs, graph = effect_graph(edits, info.width, info.height, start, work, info.fps_float,
                                                     speed=count/result_count)
                        args += inputs + ['-filter_complex', graph, '-map', '[out]'] + _encoder(info)
                    else:
                        if first:
                            # Matroska seeks may preroll an entire GOP. Output-side streamcopy
                            # trims on DTS, so preserve the target IDR's negative decode delay.
                            seek_dts = index.key_dts.get(first, start-int(info.video.get('has_b_frames', 0))/info.fps_float)
                            args += ['-ss', f'{seek_dts-start-.00001:.9f}']
                        args += ['-map', f'0:{info.video["index"]}', '-c:v', 'copy', '-bsf:v', 'h264_mp4toannexb']
                    args += ['-an', '-frames:v', str(result_count), '-mpegts_flags', '+resend_headers',
                             '-muxdelay', '0', '-progress', 'pipe:1', '-f', 'mpegts', str(target)]
                    runner.run(args, duration, base, weight)
                    # Absolute file URLs bypass concat's URL resolution of # in a parent path.
                    files.append(('file:' + target.as_posix(), duration))
                    processed += count
                listing = work / 'parts.ffconcat'
                listing.write_text('ffconcat version 1.0\n' + ''.join(
                    "file '" + name.replace("'", "'\\''") + f"'\nduration {duration:.12f}\n" for name, duration in files), encoding='utf-8')
                runner.report(82, '正在拼接视频并保留原始完整音轨…')
                args = FFMPEG + ['-f', 'concat', '-safe', '0', '-i', str(listing), '-i', info.path,
                                 '-map', '0:v:0', '-map_metadata', '1', '-c:v', 'copy', '-avoid_negative_ts', 'disabled']
                if retimed and info.audio:
                    graph, maps = audio_graph(info, project.edits, 0, index.frames, 1)
                    args += ['-filter_complex', graph] + maps + ['-c:a', 'aac', '-b:a', '192k']
                    for track, metadata in enumerate(info.audio):
                        for key, value in metadata.get('tags', {}).items():
                            if key.lower() in ('language', 'title'):
                                args += [f'-metadata:s:a:{track}', f'{key}={value}']
                else:
                    args += ['-map', '1:a?', '-c:a', 'copy']
                if destination.suffix.lower() == '.mp4':
                    # avc3 explicitly allows parameter-set updates in-band at splice boundaries.
                    args += ['-tag:v', 'avc3', '-video_track_timescale', '90000', '-movflags', '+faststart']
                args += ['-progress', 'pipe:1', str(pending)]
                runner.run(args, info.duration, 82, 8)
                runner.report(91, '正在校验输出帧数、时间戳和接缝…')
                result_info = probe(pending, runner)
                result_index = index_video(result_info, runner)
                if result_index.frames != expected_frames or len(result_info.audio) != len(info.audio):
                    raise ValueError('拼接校验失败：视频帧数或音轨数量发生变化。未交付输出文件。')
                # Decode the GOPs touching every splice; untouched hours are never re-encoded.
                output_cursor = 0
                for a, b, speed, encode in pieces:
                    runner.check()
                    count = output_frames(a, b, speed)
                    if encode:
                        start = max(0, (output_cursor-1)/info.fps_float)
                        duration = (count+2)/info.fps_float
                        runner.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', f'{start:.9f}',
                                    '-i', str(pending), '-t', f'{duration:.9f}', '-an', '-f', 'null', '-'])
                    output_cursor += count
                frame_count = expected_frames
            runner.check()
            # Recheck for external changes before replacing a destination.
            project.check_source()
            if destination.exists() and not overwrite:
                raise ValueError('输出文件已被其他程序创建，请选择新路径。')
            os.replace(pending, destination)
            runner.report(100, '导出完成')
            return ExportReport(str(destination), encoded, info.duration-encoded, frame_count, len(info.audio))


def render_preview(project: Project, edit_index: int, destination, runner: Runner):
    project.check_source()
    info = probe(project.source, runner)
    project.validate(info.duration)
    edit = project.edits[edit_index]
    # Preview represents the selected edit, including exact fractional-second enable timing.
    first, last = frame_bounds(edit, info.fps_float)
    if first >= last:
        raise ValueError('片段没有覆盖任何视频帧，请扩大时间范围。')
    start, duration = first/info.fps_float, (last-first)/info.fps_float
    with tempfile.TemporaryDirectory(prefix='qpixel-preview-') as directory:
        count = output_frames(first, last, edit.speed)
        effective = (last-first)/count
        inputs, graph = effect_graph([edit], info.width, info.height, start, Path(directory), info.fps_float, 960, effective)
        audio_maps = ['-map', '0:a?']
        if edit.speed != 1 and info.audio:
            # Input seek resets timestamps; express the preview edit on that local timeline.
            from dataclasses import replace
            audio, audio_maps = audio_graph(info, [replace(edit, start=0, end=duration)], 0, last-first, 0)
            graph += ';' + audio
        duration = count/info.fps_float
        runner.report(1, '正在生成该片段的真实效果预览…')
        runner.run(FFMPEG + ['-seek_timestamp', '1', '-ss', f'{start:.9f}', '-i', info.path] + inputs +
                   ['-filter_complex', graph, '-map', '[out]'] + audio_maps + ['-t', f'{duration:.9f}', '-frames:v', str(count),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-progress', 'pipe:1', str(destination)], duration, 1, 98)
    return str(destination)

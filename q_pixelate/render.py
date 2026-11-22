from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from .media import index_video, probe
from .model import Project, plan_ranges, frame_bounds, legacy_image_box
from .process import Runner
from .masks import mask_image, raster_layer, sticker_frames
from .mosaic import mask_frames


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


def effect_intervals(edits):
    for edit in edits:
        if edit.omit:
            continue
        if edit.layers is None:
            yield edit, None
            continue
        for layer in edit.layers:
            start, end = edit.layer_span(layer)
            if start < end:
                effect = {'sticker': 'image', 'mosaic': 'mosaic'}.get(
                    layer.kind, 'tracked_blur' if layer.motion else 'region' if layer.mask else 'blur')
                yield replace(edit, start=start, end=end, effect=effect, strength=layer.strength,
                              feather=layer.mask.feather if layer.mask else 0), layer


def encoding_intervals(edits, fps):
    # Legacy projects retain their existing spans. Layered projects only encode
    # the GOPs containing an actual effect or a speed change.
    intervals = [edit for edit in edits if edit.layers is None or edit.speed != 1 or edit.omit]
    intervals.extend(edit for edit, layer in effect_intervals(edits)
                     if layer is not None and frame_bounds(edit, fps)[0] < frame_bounds(edit, fps)[1])
    return intervals


def sample_aspect(info):
    try:
        x, y = map(int, info.video.get('sample_aspect_ratio', '1:1').split(':'))
        return x/y if x > 0 and y > 0 else 1
    except ValueError:
        return 1


def effect_graph(edits, width, height, offset, workspace, fps, scale=None, speed=1,
                 *, stop=None, pixel_aspect=1, runner=None):
    """Stage assets under safe names; user paths never appear inside filter syntax."""
    inputs = []
    image_index = 0
    offset_frame = round(offset * fps)
    stop_frame = round(stop * fps) if stop is not None else None
    effects = []
    for edit, layer in effect_intervals(edits):
        first, last = frame_bounds(edit, fps)
        if first < last and last > offset_frame and (stop_frame is None or first < stop_frame):
            effects.append((edit, layer))
    groups = [list(group) for _, group in groupby(effects, lambda pair: (
        ('mosaic', pair[0].strength) if pair[0].effect == 'mosaic' else object()))]
    rate = Fraction(fps).limit_denominator(1_000_000)
    frame_pts = f'settb={rate.denominator}/{rate.numerator},setpts=N'
    has_motion = any(layer is not None and layer.motion for _, layer in effects)
    trim = f'trim=end_frame={stop_frame-offset_frame},' if stop_frame is not None else ''
    graph = [f'[0:v:0]{trim}{frame_pts if has_motion else "setpts=PTS-STARTPTS"}[v0]']

    def add_input(asset, *, loop=True):
        nonlocal image_index
        image_index += 1
        if loop:
            inputs.extend(['-loop', '1', '-framerate', str(fps)])
        inputs.extend(['-i', str(asset)])
        return image_index

    def add_mask(i, edit, layer, pixel_format):
        asset = workspace / f'mask-{i}.png'
        image = raster_layer(layer, width, height, pixel_aspect, mask=True) if layer else mask_image(edit, width, height)
        if not image.save(str(asset)):
            raise ValueError('无法写入遮罩。')
        index = add_input(asset)
        graph.append(f'[{index}:v]format={pixel_format}[maskraw{i}]')
        if not edit.feather:
            return f'maskraw{i}'
        sigma = max(.1, edit.feather*min(width, height)/100/3)
        graph.extend([f'[maskraw{i}]split[hard{i}][soft{i}]',
                      f'[soft{i}]gblur=sigma={sigma:.6f}[feather{i}]',
                      f'[hard{i}][feather{i}]blend=all_mode=multiply[mask{i}]'])
        return f'mask{i}'

    for i, group in enumerate(groups):
        edit, layer = group[0]
        before, after = f'v{i}', f'v{i+1}'
        first, last = frame_bounds(edit, fps)
        enable = f"gte(n,{first-offset_frame})*lt(n,{last-offset_frame})"
        radius = max(1, min(edit.strength, width // 4, height // 4))
        if edit.effect in ('mosaic', 'tracked_blur'):
            if stop is None or runner is None:
                raise ValueError('追踪遮罩渲染需要明确的帧范围。')
            asset = workspace / f'coverage-{i}.nut'
            chunks = mask_frames(group, width, height, offset_frame, round(stop*fps), fps)
            runner.run(FFMPEG + ['-f', 'rawvideo', '-pixel_format', 'gray',
                                 '-video_size', f'{width}x{height}', '-framerate', str(rate),
                                 '-i', 'pipe:0', '-c:v', 'ffv1', '-f', 'nut', str(asset)], input_chunks=chunks)
            index = add_input(asset, loop=False)
            if edit.effect == 'mosaic':
                block = edit.strength
                effect = (f'scale={max(1, math.ceil(width/block))}:{max(1, math.ceil(height/block))}:flags=area,'
                          f'scale={width}:{height}:flags=neighbor,setsar={Fraction(pixel_aspect).limit_denominator()}')
            else:
                effect = f'boxblur={radius}:2'
            graph.extend([f'[{index}:v]{frame_pts},format=gbrp[coverage{i}]',
                          f'[{before}]format=gbrp,split[base{i}][pixels{i}]',
                          f'[pixels{i}]{effect}[changed{i}]',
                          f'[base{i}][changed{i}][coverage{i}]maskedmerge[{after}]'])
        elif edit.effect == 'blur':
            graph.append(f"[{before}]boxblur=luma_radius={radius}:luma_power=2:chroma_radius={max(1, radius//2)}:enable='{enable}'[{after}]")
        elif edit.effect == 'region':
            mask = add_mask(i, edit, layer, 'gbrp')
            graph.extend([f'[{before}]format=gbrp,split[base{i}][crop{i}]',
                          f'[crop{i}]boxblur={radius}:2[blur{i}]'])
            graph.append(f"[base{i}][blur{i}][{mask}]maskedmerge=enable='{enable}'[{after}]")
        elif edit.effect == 'image' and layer is not None and layer.motion:
            if stop is None or runner is None:
                raise ValueError('跟随贴纸渲染需要明确的帧范围。')
            active_first, active_last = max(first, offset_frame), min(last, round(stop*fps))
            if active_first >= active_last:
                graph.append(f'[{before}]null[{after}]')
                continue
            asset = workspace / f'tracked-{i}.nut'
            # The effect interval may start later than the layer if the selection was trimmed.
            origin = edit.start + min(0, layer.start)
            chunks = sticker_frames(layer, width, height, pixel_aspect, active_first, active_last, fps, origin)
            runner.run(FFMPEG + ['-f', 'rawvideo', '-pixel_format', 'rgba',
                                 '-video_size', f'{width}x{height}', '-framerate', str(rate),
                                 '-i', 'pipe:0', '-c:v', 'ffv1', '-pix_fmt', 'bgra', '-f', 'nut', str(asset)],
                       input_chunks=chunks)
            index = add_input(asset, loop=False)
            graph.extend([f'[{index}:v]{frame_pts}+{active_first-offset_frame},format=rgba[image{i}]',
                          f"[{before}][image{i}]overlay=0:0:enable='{enable}':eof_action=pass:repeatlast=0[{after}]"])
        elif edit.effect == 'image' and layer is not None:
            asset = workspace / f'layer-{i}.png'
            if not raster_layer(layer, width, height, pixel_aspect).save(str(asset)):
                raise ValueError('无法写入贴纸图层。')
            index = add_input(asset)
            if layer.mask:
                mask = add_mask(i, edit, layer, 'gray')
                graph.extend([f'[{index}:v]format=rgba,split[sticker{i}][alpha_source{i}]',
                              f'[alpha_source{i}]alphaextract[alpha{i}]',
                              f'[alpha{i}][{mask}]blend=all_mode=multiply[clipped{i}]',
                              f'[sticker{i}][clipped{i}]alphamerge[image{i}]'])
            else:
                graph.append(f'[{index}:v]format=rgba[image{i}]')
            graph.append(f"[{before}][image{i}]overlay=0:0:enable='{enable}':shortest=1[{after}]")
        elif edit.effect == 'image':
            x, y, w, h = legacy_image_box(edit.rect, width, height)
            suffix = Path(edit.image).suffix.lower()
            asset = workspace / f'asset-{i}{suffix}'
            shutil.copyfile(edit.image, asset)
            index = add_input(asset)
            graph.extend([f'[{index}:v]scale={w}:{h},format=rgba[image{i}]',
                          f"[{before}][image{i}]overlay={x}:{y}:enable='{enable}':shortest=1[{after}]"])
        else:
            graph.append(f'[{before}]null[{after}]')
    last = f'v{len(groups)}'
    if speed != 1:
        # Motion effects synchronize their inputs on whole-frame ticks. Resolve
        # fractional frame positions before changing the video speed.
        time_base = 'settb=AVTB,' if has_motion else ''
        graph.append(f'[{last}]{time_base}setpts=(PTS-STARTPTS)/{speed:.12f},fps={fps}[retimed]')
        last = 'retimed'
    if scale:
        graph.append(f"[{last}]scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,scale=w='min({scale},iw)':h=-2[out]")
    else:
        graph.append(f'[{last}]null[out]')
    return inputs, ';'.join(graph)


def speed_sections(edits, start, end, fps):
    """Plan retained source-frame runs and their output speeds."""
    cuts = {start, end}
    speeds = []
    for edit in edits:
        if edit.omit or edit.speed != 1:
            a, b = frame_bounds(edit, fps)
            if a < end and b > start:
                cuts.update((max(a, start), min(b, end)))
                speeds.append((a, b, None if edit.omit else edit.speed))
    cuts = sorted(cuts)
    sections = [(a, b, next((s for x, y, s in speeds if x <= a < y), 1))
                for a, b in zip(cuts, cuts[1:])]
    return [(a, b, speed) for a, b, speed in sections if speed is not None]


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
        graph.append(f'[{input_index}:a:{track}]aresample=async=1:first_pts=0,'
                     f'apad=whole_dur={end/info.fps_float:.12f},asplit={len(sections)}{labels}')
        for i, (a, b, speed) in enumerate(sections):
            count = output_frames(a, b, speed)
            duration = count/info.fps_float
            effective = (b-a)/count
            tempo = f',{tempo_chain(effective)}' if effective != 1 else ''
            graph.append(f'[a{track}s{i}]atrim=start={a/info.fps_float:.12f}:end={b/info.fps_float:.12f},'
                         f'asetpts=PTS-STARTPTS{tempo},apad,atrim=duration={duration:.12f}[a{track}p{i}]')
        graph.append(''.join(f'[a{track}p{i}]' for i in range(len(sections)))+
                     f'concat=n={len(sections)}:v=0:a=1[aout{track}]')
        maps += ['-map', f'[aout{track}]']
    return ';'.join(graph), maps


def edited_audio_options(info, edits, start, end, input_index):
    if not info.audio:
        return []
    graph, maps = audio_graph(info, edits, start, end, input_index)
    options = ['-filter_complex', graph] + maps + ['-c:a', 'aac', '-b:a', '192k']
    for track, metadata in enumerate(info.audio):
        for key, value in metadata.get('tags', {}).items():
            if key.lower() in ('language', 'title'):
                options += [f'-metadata:s:a:{track}', f'{key}={value}']
    return options


def write_concat(path, files):
    # Use absolute file URLs to prevent concat resolving # in a parent path.
    path.write_text('ffconcat version 1.0\n' + ''.join(
        "file 'file:" + target.as_posix().replace("'", "'\\''") + f"'\nduration {duration:.12f}\n"
        for target, duration in files), encoding='utf-8')


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
        if destination in project.asset_paths():
            raise ValueError('输出不能覆盖源视频或遮挡素材。')
        if destination.suffix.lower() not in ('.mp4', '.mkv'):
            raise ValueError('请选择 MP4 或 MKV 输出文件。')
        if destination.exists() and not overwrite:
            raise ValueError('输出文件已存在，请选择新路径或明确确认覆盖。')
        info = probe(project.source, runner)
        project.validate(info.duration)
        intervals = encoding_intervals(project.edits, info.fps_float)
        if intervals and info.fast_mode_error():
            raise ValueError(info.fast_mode_error())
        # Only preserve tracks the destination container can hold without transcoding.
        retimed = any(e.omit or e.speed != 1 for e in project.edits)
        if (not retimed and destination.suffix.lower() == '.mp4'
                and any(a.get('codec_name') not in ('aac', 'mp3', 'ac3', 'eac3', 'alac') for a in info.audio)):
            raise ValueError('MP4 不能直接保留该音轨编码，请选择 MKV 输出。')
        if shutil.disk_usage(destination.parent).free < Path(project.source).stat().st_size * 2.2:
            raise ValueError('输出磁盘空间不足；请预留至少约源视频 2.2 倍空间。')
        with tempfile.TemporaryDirectory(prefix='.qpixel-', dir=destination.parent) as directory:
            work = Path(directory)
            pending = work / ('result' + destination.suffix)
            if not intervals:
                runner.report(10, '没有修订片段，正在直接复制视频和音频…')
                runner.run(FFMPEG + ['-i', info.path, '-map', f'0:{info.video["index"]}', '-map', '0:a?',
                                    '-c', 'copy', '-avoid_negative_ts', 'disabled', '-progress', 'pipe:1', str(pending)], info.duration, 10, 85)
                frame_count = int(info.video.get('nb_frames', 0))
                encoded = 0
                copied = info.duration
            else:
                index = index_video(info, runner)
                spans = plan_ranges(intervals, index.keyframes, index.frames, info.fps_float)
                pieces = [(a, b, speed, span.encode) for span in spans
                          for a, b, speed in speed_sections(project.edits, span.start, span.end, info.fps_float)]
                if not pieces:
                    raise ValueError('全部视频帧已设为删除，没有保留内容可导出。')
                for span in spans:
                    require_idr(info, span.start, runner)
                encoded = sum(b-a for a, b, _, encode in pieces if encode) / info.fps_float
                copied = sum(b-a for a, b, _, encode in pieces if not encode) / info.fps_float
                retained_frames = sum(b-a for a, b, _, _ in pieces)
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
                    base, weight = 5 + 75 * processed / retained_frames, 75 * count / retained_frames
                    runner.report(base, f'{"局部编码" if encode else "直接复制"} {i+1}/{len(pieces)} · {start:.2f}–{last/info.fps_float:.2f}s')
                    args = FFMPEG + ['-seek_timestamp', '1', '-ss', f'{start:.9f}', '-i', info.path]
                    if encode:
                        edits = [e for e in project.edits if e.start < last/info.fps_float and e.end > start]
                        inputs, graph = effect_graph(edits, info.width, info.height, start, work, info.fps_float,
                                                     speed=count/result_count, stop=last/info.fps_float,
                                                     pixel_aspect=sample_aspect(info), runner=runner)
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
                    files.append((target, duration))
                    processed += count
                listing = work / 'parts.ffconcat'
                write_concat(listing, files)
                runner.report(82, '正在拼接视频与音轨…' if retimed else '正在拼接视频并保留原始完整音轨…')
                args = FFMPEG + ['-f', 'concat', '-safe', '0', '-i', str(listing), '-i', info.path,
                                 '-map', '0:v:0', '-map_metadata', '1', '-c:v', 'copy', '-avoid_negative_ts', 'disabled']
                if retimed:
                    args += edited_audio_options(info, project.edits, 0, index.frames, 1)
                    delay = int(info.video.get('has_b_frames', 0))
                    if delay:
                        # Very short pieces may have no B-frames even when the
                        # surrounding GOPs do. Use a common decode delay so the
                        # concatenated stream retains monotonic decode timestamps.
                        tick = f'({info.fps.denominator}/{info.fps.numerator}/TB)'
                        args += ['-bsf:v', f'setts=pts=PTS:dts=STARTPTS+round((N-{delay})*{tick}):'
                                           f'duration=round((N+1)*{tick})-round(N*{tick})']
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
            return ExportReport(str(destination), encoded, copied, frame_count, len(info.audio))


def render_preview(project: Project, edit_index: int, destination, runner: Runner):
    project.check_source()
    info = probe(project.source, runner)
    project.validate(info.duration)
    edit = project.edits[edit_index]
    # Preview represents the selected edit, including exact fractional-second enable timing.
    first, last = frame_bounds(edit, info.fps_float)
    if first >= last:
        raise ValueError('片段没有覆盖任何视频帧，请扩大时间范围。')
    if edit.omit:
        index = index_video(info, runner)
        retained = speed_sections(project.edits, 0, index.frames, info.fps_float)
        before = max((b for _, b, _ in retained if b <= first), default=None)
        after = min((a for a, _, _ in retained if a >= last), default=None)
        context = max(1, round(info.fps_float))
        start = first if before is None else max(0, before-context)
        end = last if after is None else min(index.frames, after+context)
        return _render_preview_range(project, info, start, min(end, index.frames), destination, runner)
    start, duration = first/info.fps_float, (last-first)/info.fps_float
    with tempfile.TemporaryDirectory(prefix='qpixel-preview-') as directory:
        count = output_frames(first, last, edit.speed)
        effective = (last-first)/count
        inputs, graph = effect_graph([edit], info.width, info.height, start, Path(directory), info.fps_float, 960, effective,
                                     stop=last/info.fps_float, pixel_aspect=sample_aspect(info), runner=runner)
        audio_maps = ['-map', '0:a?']
        if edit.speed != 1 and info.audio:
            # Input seek resets timestamps; express the preview edit on that local timeline.
            audio, audio_maps = audio_graph(info, [replace(edit, start=0, end=duration)], 0, last-first, 0)
            graph += ';' + audio
        duration = count/info.fps_float
        runner.report(1, '正在生成该片段的真实效果预览…')
        runner.run(FFMPEG + ['-seek_timestamp', '1', '-ss', f'{start:.9f}', '-i', info.path] + inputs +
                   ['-filter_complex', graph, '-map', '[out]'] + audio_maps + ['-t', f'{duration:.9f}', '-frames:v', str(count),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-progress', 'pipe:1', str(destination)], duration, 1, 98)
    return str(destination)


def render_project_preview(project: Project, destination, runner: Runner):
    project.check_source()
    info = probe(project.source, runner)
    project.validate(info.duration)
    index = index_video(info, runner)
    return _render_preview_range(project, info, 0, index.frames, destination, runner)


def _render_preview_range(project, info, start, end, destination, runner):
    destination = Path(destination).resolve()
    if destination in project.asset_paths():
        raise ValueError('预览不能覆盖源视频或遮挡素材。')
    fps = info.fps_float
    kept = speed_sections(project.edits, start, end, fps)
    if not kept:
        raise ValueError('全部视频帧已设为删除，没有保留内容可预览。')
    # Materialize per-frame coverage only where an effect is active. Keep a
    # retimed run intact so the audio and video round its duration together.
    motion_edges = sorted({frame for edit, layer in effect_intervals(project.edits) if layer and layer.motion
                           for frame in frame_bounds(edit, fps)})
    sections = []
    for first, last, speed in kept:
        cuts = [first, *(f for f in motion_edges if speed == 1 and first < f < last), last]
        sections.extend((a, b, speed) for a, b in zip(cuts, cuts[1:]))
    with tempfile.TemporaryDirectory(prefix='qpixel-preview-', dir=destination.parent) as directory:
        work = Path(directory)
        files = []
        retained = sum(b-a for a, b, _ in sections)
        rendered = 0
        for i, (first, last, speed) in enumerate(sections):
            runner.check()
            count = output_frames(first, last, speed)
            offset, duration = first/fps, count/fps
            target = work / f'part-{i:04d}.mp4'
            inputs, graph = effect_graph(project.edits, info.width, info.height, offset, work, fps, 960,
                                         (last-first)/count, stop=last/fps, pixel_aspect=sample_aspect(info), runner=runner)
            progress, weight = 5 + 85*rendered/retained, 85*(last-first)/retained
            runner.report(progress, f'正在生成真实效果预览 {i+1}/{len(sections)}…')
            args = FFMPEG + ['-seek_timestamp', '1', '-ss', f'{offset:.9f}', '-i', info.path] + inputs
            runner.run(args + ['-filter_complex', graph, '-map', '[out]', '-frames:v', str(count),
                               '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22', '-pix_fmt', 'yuv420p',
                               '-progress', 'pipe:1', str(target)], duration, progress, weight)
            files.append((target, duration))
            rendered += last-first
        listing = work / 'parts.ffconcat'
        write_concat(listing, files)
        pending = work / 'preview.mp4'
        args = FFMPEG + ['-f', 'concat', '-safe', '0', '-i', str(listing), '-i', info.path, '-map', '0:v:0',
                         '-c:v', 'copy', '-movflags', '+faststart']
        args += edited_audio_options(info, project.edits, start, end, 1)
        runner.run(args + ['-progress', 'pipe:1', str(pending)], sum(d for _, d in files), 90, 9)
        runner.check()
        project.check_source()
        os.replace(pending, destination)
    return str(destination)

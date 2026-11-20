"""Isolated NudeNet ONNX worker. Keep imports independent of the editor."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path


# NudeNet v3 model class indices. Only these exposed regions are used for redaction.
LABELS = {
    2: 'BUTTOCKS_EXPOSED',
    3: 'FEMALE_BREAST_EXPOSED',
    4: 'FEMALE_GENITALIA_EXPOSED',
    5: 'MALE_BREAST_EXPOSED',
    6: 'ANUS_EXPOSED',
    14: 'MALE_GENITALIA_EXPOSED',
}
MODEL_CLASSES = 18
CONFIDENCE = .25
NMS_IOU = .45
BOX_MARGIN = .08
GPU = 'CUDAExecutionProvider'

# Serialized ONNX IR 8 / opset 13 graph, y = Mul(x, x), for a float32 vector of
# four elements. This lets an installed runtime verify that it can execute a
# real kernel without downloading or installing either NudeNet model first.
RUNTIME_PROBE_MODEL = bytes.fromhex(
    '08 08 3a 48 0a 16 0a 01 78 0a 01 78 12 01 79 1a 06 73 71 75 61 72 65 22 03 4d 75 6c '
    '12 0c 64 65 76 69 63 65 5f 70 72 6f 62 65 '
    '5a 0f 0a 01 78 12 0a 0a 08 08 01 12 04 0a 02 08 04 '
    '62 0f 0a 01 79 12 0a 0a 08 08 01 12 04 0a 02 08 04 42 02 10 0d'
)


def model_size(node, metadata):
    """Read the training resolution for NudeNet's dynamic spatial inputs."""
    shape = node.shape
    resolution = metadata.get('imgsz')
    dimensions = json.loads(resolution) if resolution is not None else shape[-2:]
    if (node.type != 'tensor(float)' or len(shape) != 4 or shape[1] != 3
            or (isinstance(shape[0], int) and shape[0] != 1)
            or dimensions not in ([320, 320], [640, 640])
            or any(isinstance(axis, int) and axis != size for axis, size in zip(shape[-2:], dimensions))):
        raise ValueError('需要输入尺寸为 320 或 640 的 NudeNet v3 模型。')
    return dimensions[0]


def boxes_from_output(np, output, *, width, height):
    """Convert model-space boxes to clipped, normalized source rectangles."""
    predictions = np.squeeze(output, axis=0)
    if predictions.ndim != 2 or predictions.shape[0] != 4 + MODEL_CLASSES:
        raise ValueError('NudeNet 模型输出维度不受支持。')
    by_class = {}
    classes = predictions[4:].argmax(axis=0)
    scores = predictions[4:].max(axis=0)
    for i in np.flatnonzero(scores >= CONFIDENCE):
        label = LABELS.get(classes[i])
        if label is None:
            continue
        x, y, w, h = map(float, predictions[:4, i])
        score = float(scores[i])
        if not all(map(math.isfinite, (x, y, w, h, score))) or w <= 0 or h <= 0 or score > 1:
            continue
        if (x-w/2 >= width or y-h/2 >= height or x+w/2 <= 0 or y+h/2 <= 0):
            continue
        left, top = max(0, x-w/2)/width, max(0, y-h/2)/height
        right, bottom = min(width, x+w/2)/width, min(height, y+h/2)/height
        by_class.setdefault(label, []).append((score, left, top, right, bottom))

    hits = []
    for label, candidates in by_class.items():
        kept = []
        for candidate in sorted(candidates, reverse=True):
            if any(_iou(candidate, previous) > NMS_IOU for previous in kept):
                continue
            kept.append(candidate)
            score, left, top, right, bottom = candidate
            w, h = (right-left)*BOX_MARGIN, (bottom-top)*BOX_MARGIN
            left, top = max(0, left-w), max(0, top-h)
            right, bottom = min(1, right+w), min(1, bottom+h)
            hits.append((label, score, (left, top, right-left, bottom-top)))
    return sorted(hits, key=lambda hit: -hit[1])


def _iou(first, second):
    _, left, top, right, bottom = first
    _, other_l, other_t, other_r, other_b = second
    overlap = max(0, min(right, other_r)-max(left, other_l)) * max(0, min(bottom, other_b)-max(top, other_t))
    return overlap / ((right-left)*(bottom-top) + (other_r-other_l)*(other_b-other_t) - overlap)


def _open_session(ort, model, provider, profile_dir):
    if provider not in ort.get_available_providers():
        raise ValueError(f'私有 ONNX Runtime 未提供 {provider}。')
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    if provider == GPU:
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(profile_dir) / 'ort')
        providers = [(provider, {'cudnn_conv_algo_search': 'HEURISTIC'})]
    else:
        providers = [provider]
    session = ort.InferenceSession(model, sess_options=options, providers=providers)
    session.disable_fallback()
    if provider not in session.get_providers():
        raise ValueError(f'无法初始化 {provider}，请检查 NVIDIA 驱动、CUDA 和 cuDNN 组件。')
    return session


def _verify_gpu(session):
    profile = Path(session.end_profiling())
    events = json.loads(profile.read_text(encoding='utf-8'))
    if not any(event.get('cat') == 'Node' and event.get('args', {}).get('provider') == GPU for event in events):
        raise ValueError('测试推理未执行 CUDA 运算，请检查 NVIDIA 加速环境。')


def _run(args, resources):
    site_packages = Path(args.site_packages)
    sys.path.insert(0, str(site_packages))
    if os.name == 'nt':
        dll_dirs = [site_packages/'onnxruntime'/'capi']
        if args.provider == GPU:
            dll_dirs.extend(site_packages.glob('nvidia/*/bin'))
        for directory in dll_dirs:
            if directory.is_dir():
                resources.enter_context(os.add_dll_directory(directory))
    with redirect_stdout(sys.stderr):
        import numpy as np
        import cv2
        import onnxruntime as ort

        if os.name == 'nt' and args.provider == GPU and hasattr(ort, 'preload_dlls'):
            ort.preload_dlls(directory='')
        profile_dir = args.profile_dir
        if args.provider == GPU and profile_dir is None:
            profile_dir = resources.enter_context(tempfile.TemporaryDirectory(prefix='qpixel-ort-'))
        model = RUNTIME_PROBE_MODEL if args.runtime_probe else args.model
        session = _open_session(ort, model, args.provider, profile_dir)
        node = session.get_inputs()[0]
        if args.runtime_probe:
            sample = np.array([1, 2, 3, 4], dtype=np.float32)
            output, = session.run(None, {node.name: sample})
            if not np.array_equal(output, [1, 4, 9, 16]):
                raise ValueError('ONNX Runtime 的测试推理结果异常。')
        else:
            size = model_size(node, session.get_modelmeta().custom_metadata_map)
            if args.probe or args.provider == GPU:
                output = session.run(None, {node.name: np.zeros((1, 3, size, size), dtype=np.float32)})
                boxes_from_output(np, output[0], width=size, height=size)
        if args.provider == GPU:
            _verify_gpu(session)

    print(json.dumps({'provider': args.provider}), flush=True)
    if args.probe or args.runtime_probe:
        return
    width, height = args.width, args.height
    square = max(width, height)
    frame_size = width * height * 3
    padded = np.zeros((square, square, 3), dtype=np.uint8)
    while frame := sys.stdin.buffer.read(frame_size):
        if len(frame) != frame_size:
            raise ValueError('识别进程收到不完整的画面。')
        padded[:height, :width] = np.frombuffer(frame, dtype=np.uint8).reshape(height, width, 3)
        blob = cv2.dnn.blobFromImage(padded, 1/255, (size, size), swapRB=True)
        output = session.run(None, {node.name: blob})[0]
        hits = boxes_from_output(np, output, width=width*size/square, height=height*size/square)
        print(json.dumps(hits, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-packages', required=True)
    parser.add_argument('--provider', choices=('CPUExecutionProvider', GPU), required=True)
    parser.add_argument('--model')
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--profile-dir')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--runtime-probe', action='store_true')
    args = parser.parse_args()
    try:
        with ExitStack() as resources:
            _run(args, resources)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

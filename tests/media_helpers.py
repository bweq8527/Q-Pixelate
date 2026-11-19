import json
import subprocess
from pathlib import Path


def command(*args):
    result = subprocess.run(list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result.stdout


def fixture(path, fps='20', audio=True):
    args = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=320x180:rate={fps}:duration=6']
    if audio:
        args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6',
                 '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=6',
                 '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:a', 'aac', '-metadata:s:a:0', 'language=zho']
    args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-g', '40',
             '-keyint_min', '40', '-sc_threshold', '0', '-bf', '2', str(path)]
    command(*args)
    return path


def hashes(path, audio=False):
    out = command('ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a' if audio else '0:v:0',
                  '-f', 'framemd5', '-')
    return [line.split(',')[-1].strip() for line in out.decode().splitlines() if line and not line.startswith('#')]


def image_at(path, frame):
    from PIL import Image
    import io
    raw = command('ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'select=eq(n\\,{frame})',
                  '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-')
    return Image.open(io.BytesIO(raw)).convert('RGB')

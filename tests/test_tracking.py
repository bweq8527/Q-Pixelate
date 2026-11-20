import importlib.util
import os
import random
import shutil
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from media_helpers import command
from q_pixelate.media import probe
from q_pixelate.model import Edit, Rect
from q_pixelate.process import Cancelled, Runner
from q_pixelate.tracking import track


WIDTH, HEIGHT = 144, 96
SITE_PACKAGES = os.environ.get('QPIXEL_TRACKING_TEST_SITE_PACKAGES')
if SITE_PACKAGES is None and importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'):
    SITE_PACKAGES = sysconfig.get_path('purelib')


def _write_video(root, name, targets, *, cut=None, tone=None):
    noise = random.Random(617)
    pattern = bytes(tone if tone is not None else noise.choice((10, 52, 168, 241)) for _ in range(10 * 10))
    raw = root / f'{name}.gray'
    with raw.open('wb') as out:
        for frame_number, target in enumerate(targets):
            background = 210 if cut is not None and frame_number >= cut else 95
            frame = bytearray([background]) * (WIDTH * HEIGHT)
            if target:
                x, y, side = target
                for row in range(side):
                    offset = (y + row) * WIDTH + x
                    frame[offset:offset + side] = bytes(
                        pattern[(row * 10 // side) * 10 + col * 10 // side] for col in range(side)
                    )
            out.write(frame)
    video = root / f'{name}.mp4'
    command('ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'gray',
            '-s', f'{WIDTH}x{HEIGHT}', '-r', '30000/1001', '-i', str(raw), '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p', '-g', '4', '-keyint_min', '4', '-sc_threshold', '0',
            '-bf', '2', '-crf', '12', str(video))
    return probe(video, Runner())


def _rect(x, y, side):
    return Rect(x / WIDTH, y / HEIGHT, side / WIDTH, side / HEIGHT)


@unittest.skipUnless(SITE_PACKAGES and shutil.which('ffmpeg') and shutil.which('ffprobe'),
                     'Requires FFmpeg and OpenCV/NumPy (QPIXEL_TRACKING_TEST_SITE_PACKAGES)')
class TrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix='qpixel-tracking-test-')
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        x = [(12 + n * 3, 23, 20) for n in range(18)]
        cls.translation = _write_video(cls.root, '位移 目标', x)
        cls.disappearance = _write_video(cls.root, 'disappearance', x[:8] + [None] * 10)
        cls.cut = _write_video(cls.root, 'cut', x, cut=8)
        cls.resize = _write_video(cls.root, 'resize', [(21 + n, 18 + n, 20 + 2*n) for n in range(11)])
        cls.occlusion = _write_video(cls.root, 'occlusion', x[:8] + [None] + x[9:])

    def follow(self, info, rect, *, edit=None, start=0, runner=None):
        return track(info, edit or Edit(0, info.duration, 'none'), runner or Runner(), start=start,
                     rect=rect, python=sys.executable, site_packages=SITE_PACKAGES)

    def test_follows_translation_from_exact_nearest_frame_to_selection_end(self):
        start_rect = _rect(27, 23, 20)  # Frame 5, after the second keyframe.
        result = self.follow(self.translation, start_rect, edit=Edit(.1, .352, 'none'), start=.17)
        self.assertEqual(result.points[0][1], start_rect)
        self.assertEqual([round(t, 6) for t, _ in result.points],
                         [.166833, .2002, .233567, .266933, .3003, .333667])
        self.assertAlmostEqual(result.points[-1][1].x * WIDTH, 42, delta=1)
        self.assertAlmostEqual(result.points[-1][1].y * HEIGHT, 23, delta=1)
        self.assertAlmostEqual(result.end, .352)
        self.assertIn('选区', result.reason)

    def test_ends_at_first_unconfirmed_frame_after_persistent_loss(self):
        result = self.follow(self.disappearance, _rect(12, 23, 20))
        self.assertEqual(len(result.points), 8)
        self.assertAlmostEqual(result.end, .2669333333333333)
        self.assertIn('丢失', result.reason)

    def test_stops_at_cut_even_when_same_target_is_still_visible(self):
        result = self.follow(self.cut, _rect(12, 23, 20))
        self.assertEqual(len(result.points), 8)
        self.assertAlmostEqual(result.end, .2669333333333333)
        self.assertIn('切镜', result.reason)

    def test_follows_progressive_growth_from_the_original_texture(self):
        result = self.follow(self.resize, _rect(21, 18, 20))
        self.assertEqual(len(result.points), 11)
        final = result.points[-1][1]
        self.assertAlmostEqual(final.x * WIDTH, 31, delta=2)
        self.assertAlmostEqual(final.y * HEIGHT, 28, delta=2)
        self.assertAlmostEqual(final.w * WIDTH, 40, delta=2)
        self.assertAlmostEqual(final.h * HEIGHT, 40, delta=2)

    def test_a_brief_occlusion_does_not_reset_appearance_or_end_the_track(self):
        result = self.follow(self.occlusion, _rect(12, 23, 20))
        self.assertEqual(len(result.points), 17)
        self.assertNotIn(round(.2669333333333333, 6), [round(t, 6) for t, _ in result.points])
        self.assertAlmostEqual(result.points[-1][1].x * WIDTH, 63, delta=1)
        self.assertAlmostEqual(result.end, self.occlusion.duration)

    def test_indistinguishable_positions_do_not_move_a_flat_selection(self):
        source = _write_video(self.root, 'flat', [None] * 12)
        result = self.follow(source, _rect(37, 26, 20))
        self.assertEqual(len(result.points), 12)
        for _, bounds in result.points:
            self.assertAlmostEqual(bounds.x * WIDTH, 37, delta=1)
            self.assertAlmostEqual(bounds.y * HEIGHT, 26, delta=1)

    def test_tracks_a_solid_color_target_that_contrasts_with_the_background(self):
        source = _write_video(self.root, 'solid', [(12 + n*3, 23, 20) for n in range(10)], tone=210)
        result = self.follow(source, _rect(12, 23, 20))
        self.assertEqual(len(result.points), 10)
        self.assertAlmostEqual(result.points[-1][1].x * WIDTH, 39, delta=1)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires FFmpeg')
class TrackingProcessTests(unittest.TestCase):
    def test_cancel_when_worker_is_waiting_on_frame(self):
        with tempfile.TemporaryDirectory(prefix='qpixel-tracking-cancel-') as temp:
            root = Path(temp)
            info = _write_video(root, 'cancel', [(20, 20, 20)] * 15)
            script = root / 'slow_worker.py'
            script.write_text('import sys, time\nprint("{\\"ready\\": true}", flush=True)\n'
                              f'sys.stdin.buffer.read({WIDTH * HEIGHT * 3})\ntime.sleep(30)\n',
                              encoding='utf-8')
            cancel = threading.Event()
            timer = threading.Timer(.7, cancel.set)
            with patch('q_pixelate.tracking._worker_command', return_value=[sys.executable, str(script)]):
                start = time.monotonic()
                timer.start()
                try:
                    with self.assertRaises(Cancelled):
                        track(info, Edit(0, info.duration, 'none'), Runner(cancel), start=0,
                              rect=_rect(20, 20, 20), python=sys.executable, site_packages=root)
                finally:
                    timer.cancel()
                self.assertLess(time.monotonic() - start, 5)

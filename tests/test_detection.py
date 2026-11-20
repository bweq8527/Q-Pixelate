import json
import math
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_helpers import command
from q_pixelate.detection import detector_command, scan
from q_pixelate.detector_worker import boxes_from_output
from q_pixelate.media import probe
from q_pixelate.model import Edit
from q_pixelate.process import Cancelled, Runner


FAKE_WORKER = '''
import json
import sys
import time

width, height = map(int, sys.argv[1:3])
print(json.dumps({'provider': 'CPUExecutionProvider'}), flush=True)
while frame := sys.stdin.buffer.read(width * height * 3):
    if sys.argv[3] == 'slow':
        time.sleep(30)
    # The first target's x coordinate encodes source-frame luminance.
    luma = frame[0]
    hits = []
    if luma > 25:
        hits = [['BUTTOCKS_EXPOSED', .8, [luma / 2000, .2, .2, .2]],
                ['BUTTOCKS_EXPOSED', .6, [.6, .2, .2, .2]]]
    print(json.dumps(hits), flush=True)
'''


class DetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qpixel-scan-test-')
        cls.root = Path(cls.temp.name)
        cls.worker = cls.root / 'stub.py'
        cls.worker.write_text(FAKE_WORKER, encoding='utf-8')
        cls.source = cls.root / '测试 视频.mp4'
        # One miss at frame 13, then a cut from mid gray to white at 16.
        values = [50 + 2*n if n != 13 else 0 for n in range(16)] + [235]*12
        raw = cls.root / 'source.gray'
        raw.write_bytes(b''.join(bytes([value])*64*48 for value in values))
        command('ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', '64x48',
                '-r', '30000/1001', '-i', str(raw), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-g', '8', '-keyint_min', '8', '-sc_threshold', '0', '-bf', '2', '-crf', '12', str(cls.source))
        cls.info = probe(cls.source, Runner())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def detect(self, edit, runner=None, *, rate=None, slow=False):
        def fake_command(*args, width, height, **kwargs):
            return [sys.executable, str(self.worker), str(width), str(height), 'slow' if slow else 'fast']

        with patch('q_pixelate.detection.detector_command', side_effect=fake_command):
            return scan(self.info, edit, runner or Runner(), python=sys.executable,
                        site_packages=self.root, model=self.root/'model.onnx', device='cpu', sample_fps=rate)

    def test_scan_tracks_multiple_moving_boxes_on_exact_fractional_source_frames(self):
        tracks = self.detect(Edit(.3003, .4004, 'none'))  # Frames 9, 10, 11.
        self.assertEqual(len(tracks), 2)
        self.assertEqual(len({track.id for track in tracks}), 2)
        for track in tracks:
            self.assertAlmostEqual(track.start, .3003)
            self.assertAlmostEqual(track.end, .4004)
            self.assertEqual([round(time, 6) for time, _ in track.points], [.3003, .333667, .367033])
        self.assertAlmostEqual(tracks[0].points[0][1].x, .034, delta=.001)
        self.assertAlmostEqual(tracks[0].points[-1][1].x, .036, delta=.001)
        self.assertEqual(tracks[1].points[-1][1].x, .6)

    def test_fast_scan_splits_a_cut_between_sampled_frames(self):
        tracks = self.detect(Edit(.4671333333333333, .7674333333333333, 'none'), rate=10)
        second_box_tracks = [track for track in tracks if track.points[0][1].x == .6]
        self.assertEqual([[round(time, 6) for time, _ in track.points] for track in second_box_tracks],
                         [[.467133], [.567233, .667333]])

    def test_a_missed_frame_does_not_extend_a_track(self):
        tracks = self.detect(Edit(.4004, .5005, 'none'))  # Frames 12, 13, 14.
        second_box_tracks = [track for track in tracks if track.points[0][1].x == .6]
        self.assertEqual([(round(t.start, 6), round(t.end, 6)) for t in second_box_tracks],
                         [(.4004, .433767), (.467133, .5005)])

    def test_sparse_scan_does_not_interpolate_across_long_unsampled_regions(self):
        tracks = self.detect(Edit(0, .4337666666666667, 'none'), rate=2.5)  # Frames 0 and 12.
        second_box_tracks = [track for track in tracks if track.points[0][1].x == .6]
        self.assertEqual([(round(t.start, 6), round(t.end, 6)) for t in second_box_tracks],
                         [(0, .033367), (.4004, .433767)])

    def test_adjacent_source_frames_remain_one_track_at_low_frame_rate(self):
        from q_pixelate.detection import _group_tracks
        from q_pixelate.model import Rect

        hit = [('BUTTOCKS_EXPOSED', .8, Rect(.1, .1, .2, .2))]
        tracks = _group_tracks([(8, hit), (9, hit)], [], 1, 1, Runner())
        self.assertEqual([(track.start, track.end) for track in tracks], [(8, 10)])

    def test_cancel_while_inference_is_blocked(self):
        event = threading.Event()
        cancel = None

        def progress(value, text):
            nonlocal cancel
            if value >= 5 and cancel is None:
                cancel = threading.Timer(.3, event.set)
                cancel.start()

        start = time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                self.detect(Edit(.3003, .4004, 'none'), Runner(event, progress), slow=True)
            self.assertLess(time.monotonic() - start, 5)
        finally:
            if cancel:
                cancel.cancel()

    def test_nudenet_boxes_are_filtered_and_clipped_out_of_padding(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('NumPy is an optional detector dependency')
        # Rows are model-space center coordinates, size, and class probabilities.
        scores = np.zeros((22, 5), dtype=np.float32)
        scores[:4] = [(16, 17, 16, 17, 17), (30, 30, 175, 30, 300),
                      (40, 40, 40, 40, 40), (40, 40, 40, 40, 40)]
        scores[6, :3] = [.9, .8, .85]   # Exposed buttocks, including overlap and lower edge
        scores[5, 3] = .99             # Female face
        scores[6, 4] = .95             # Box entirely in padding
        hits = boxes_from_output(np, scores[None, ...], width=320, height=180)
        self.assertEqual(len(hits), 2)
        self.assertEqual([hit[0] for hit in hits], ['BUTTOCKS_EXPOSED', 'BUTTOCKS_EXPOSED'])
        first, second = [hit[2] for hit in hits]
        self.assertEqual(first[0], 0)
        self.assertTrue(math.isclose(second[1] + second[3], 1.0))

    def test_runtime_probe_reports_missing_dependencies_from_private_runtime(self):
        args = detector_command(sys.executable, self.root, None, 'cpu', runtime_probe=True)
        proc = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('numpy', json.loads(proc.stdout)['error'])

    def test_nudenet_resolution_comes_from_metadata_for_dynamic_inputs(self):
        from q_pixelate.detector_worker import model_size

        image = SimpleNamespace(shape=['batch', 3, 'height', 'width'], type='tensor(float)')
        self.assertEqual(model_size(image, {'imgsz': '[320, 320]'}), 320)
        self.assertEqual(model_size(image, {'imgsz': '[640, 640]'}), 640)
        with self.assertRaises(ValueError):
            model_size(image, {'imgsz': '[320, 640]'})

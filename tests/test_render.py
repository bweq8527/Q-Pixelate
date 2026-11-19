import tempfile
import threading
import unittest
from pathlib import Path

from media_helpers import command, fixture, hashes, image_at
from q_pixelate.model import Edit, Project, Rect
from q_pixelate.process import Cancelled, Runner
from q_pixelate.media import probe, index_video
from q_pixelate.render import Exporter, render_preview


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qpixel-test-')
        cls.root = Path(cls.temp.name)
        cls.source = fixture(cls.root / '中文 原始.mp4')
        cls.original = hashes(cls.source)
        cls.audio = hashes(cls.source, True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def export(self, edits, name='out.mp4', source=None):
        source = source or self.source
        out = self.root / name
        result = Exporter().export(Project(str(source), edits), out, Runner())
        self.assertTrue(out.is_file())
        return out, result

    def test_non_keyframe_blur_preserves_untouched_frames_audio_and_total_count(self):
        out, report = self.export([Edit(2.25, 2.75)])
        frames = hashes(out)
        self.assertEqual(len(frames), 120)
        self.assertEqual(frames[:40], self.original[:40])
        self.assertEqual(frames[80:], self.original[80:])
        self.assertEqual(hashes(out, True), self.audio)
        self.assertNotEqual(frames[48], self.original[48])
        self.assertAlmostEqual(report.encoded_seconds, 2, places=3)
        command('ffmpeg', '-v', 'error', '-xerror', '-i', str(out), '-f', 'null', '-')

    def test_region_blur_only_changes_selected_rectangle(self):
        out, _ = self.export([Edit(2.1, 2.9, 'region', Rect(.5, .5, .4, .4))], 'region.mp4')
        original = image_at(self.source, 50)
        changed = image_at(out, 50)
        from PIL import ImageChops, ImageStat
        # Re-encoding can perturb all pixels slightly; blur should dominate inside the mask.
        outside = ImageStat.Stat(ImageChops.difference(original.crop((0, 0, 120, 70)), changed.crop((0, 0, 120, 70)))).mean
        inside = ImageStat.Stat(ImageChops.difference(original.crop((180, 110, 270, 150)), changed.crop((180, 110, 270, 150)))).mean
        self.assertGreater(sum(inside), sum(outside) * 3)

    def test_image_path_with_punctuation_and_alpha(self):
        from PIL import Image
        image = self.root / "遮挡 ' [,];.png"
        Image.new('RGBA', (80, 80), (0, 255, 0, 255)).save(image)
        out, _ = self.export([Edit(.2, .7, 'image', Rect(0, 0, .25, .5), image=str(image))], 'image.mp4')
        rgb = image_at(out, 8).getpixel((25, 25))
        self.assertGreater(rgb[1], 220)
        self.assertLess(rgb[0], 30)
        self.assertEqual(hashes(out)[40:], self.original[40:])

    def test_adjacent_edits_and_endpoints(self):
        out, _ = self.export([Edit(0, .2), Edit(.2, .4), Edit(5.7, 6)], 'edges.mp4')
        self.assertEqual(len(hashes(out)), 120)
        self.assertEqual(hashes(out)[40:80], self.original[40:80])

    def test_no_edits_is_stream_copy(self):
        out, report = self.export([], 'copy.mp4')
        self.assertEqual(hashes(out), self.original)
        self.assertEqual(report.encoded_seconds, 0)

    def test_silent_fractional_fps(self):
        source = fixture(self.root / 'fractional.mp4', '30000/1001', False)
        out, _ = self.export([Edit(2.1, 2.5)], 'fractional-out.mp4', source)
        self.assertEqual(len(hashes(out)), len(hashes(source)))

    def test_cancel_preserves_existing_destination(self):
        out = self.root / 'existing.mp4'
        out.write_bytes(b'existing content')
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            Exporter().export(Project(str(self.source), [Edit(1, 2)]), out, Runner(event), overwrite=True)
        self.assertEqual(out.read_bytes(), b'existing content')

    def test_preview_renders_effect_and_preserves_duration(self):
        out = self.root / 'preview.mp4'
        render_preview(Project(str(self.source), [Edit(2.25, 2.75)]), 0, out, Runner())
        self.assertAlmostEqual(probe(out, Runner()).duration, .5, delta=.06)

    def test_mkv_input_and_output_preserve_frame_order(self):
        source = self.root / 'source.mkv'
        command('ffmpeg', '-v', 'error', '-y', '-i', str(self.source), '-map', '0', '-c', 'copy', '-avoid_negative_ts', 'disabled', str(source))
        out, _ = self.export([Edit(2.25, 2.75)], 'output.mkv', source)
        frames = hashes(out)
        self.assertEqual(len(frames), 120)
        self.assertEqual(frames[:40], self.original[:40])
        self.assertEqual(frames[80:], self.original[80:])

    def test_cancel_during_processing_removes_only_task_files(self):
        event = threading.Event()
        out = self.root / 'cancelled.mp4'
        out.write_bytes(b'keep me')
        def progress(value, text):
            if value >= 5:
                event.set()
        with self.assertRaises(Cancelled):
            Exporter().export(Project(str(self.source), [Edit(2.1, 2.5)]), out,
                              Runner(event, progress), overwrite=True)
        self.assertEqual(out.read_bytes(), b'keep me')
        self.assertEqual(list(self.root.glob('.qpixel-*')), [])

    def test_refuse_overwriting_source(self):
        with self.assertRaisesRegex(ValueError, '覆盖源视频'):
            Exporter().export(Project(str(self.source)), self.source, Runner(), overwrite=True)

    def test_unsupported_codec_is_rejected_without_output(self):
        source = self.root / 'mpeg4.mp4'
        command('ffmpeg', '-v', 'error', '-y', '-i', str(self.source), '-an', '-c:v', 'mpeg4', str(source))
        output = self.root / 'unsupported.mp4'
        with self.assertRaisesRegex(ValueError, 'H.264'):
            Exporter().export(Project(str(source), [Edit(1, 2)]), output, Runner())
        self.assertFalse(output.exists())

    def test_open_gop_boundary_is_rejected_instead_of_corrupting_copy(self):
        source = self.root / 'open-gop.mp4'
        command('ffmpeg', '-v', 'error', '-y', '-i', str(self.source), '-an', '-c:v', 'libx264',
                '-g', '40', '-x264-params', 'open-gop=1:scenecut=0', str(source))
        with self.assertRaisesRegex(ValueError, 'IDR|开放'):
            self.export([Edit(2.25, 2.75)], 'open-gop-output.mp4', source)

    def test_mask_rounding_covers_selected_far_edges(self):
        from PIL import Image
        image = self.root / 'edge-white.png'
        Image.new('RGBA', (20, 20), (255, 255, 255, 255)).save(image)
        out, _ = self.export([Edit(2.1, 2.9, 'image', Rect(.03, .03, .09, .09), image=str(image))], 'far-edge.mp4')
        pixel = image_at(out, 50).getpixel((37, 20))
        self.assertGreater(min(pixel), 220, 'Selected far edge must be covered, including chroma-aligned rounding')

    def test_fractional_frame_at_exact_effect_start_is_already_redacted(self):
        from PIL import Image
        image = self.root / 'fractional-green.png'
        Image.new('RGBA', (50, 50), (0, 255, 0, 255)).save(image)
        source = fixture(self.root / 'boundary-source.mp4', '30000/1001', False)
        edit = Edit(3.003, 3.2, 'image', Rect(0, 0, .25, .5), image=str(image))
        out, _ = self.export([edit], 'boundary-output.mp4', source)
        for frame in (90, 95):
            rgb = image_at(out, frame).getpixel((25, 25))
            self.assertGreater(rgb[1], 220, f'Frame {frame} must be redacted')
            self.assertLess(rgb[0], 30)


if __name__ == '__main__':
    unittest.main()

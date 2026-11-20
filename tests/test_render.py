import tempfile
import threading
import unittest
from pathlib import Path

from media_helpers import command, fixture, hashes, image_at
from q_pixelate.model import Edit, Layer, Mask, Project, Rect, Transform
from q_pixelate.process import Cancelled, Runner
from q_pixelate.media import probe, index_video
from q_pixelate.render import Exporter, render_preview


class RenderTests(unittest.TestCase):
    def test_layers_composite_by_time_and_sticker_mask_stays_in_video_coordinates(self):
        from PIL import Image
        sticker = self.root.resolve()/'green.png'
        top = self.root.resolve()/'magenta.png'
        Image.new('RGBA', (20, 20), (0, 255, 0, 255)).save(sticker)
        Image.new('RGBA', (20, 20), (255, 0, 255, 255)).save(top)
        mask = Mask('ellipse', Transform(.5, .5, .28, .45), feather=3)
        layers = [
            Layer(.25, 1.5, 'sticker', image=str(sticker), transform=Transform(.4, .5, .65, .6, 20), mask=mask),
            Layer(.75, 1, 'sticker', image=str(top), transform=Transform(.5, .5, .12, .12)),
        ]
        edit = Edit(2, 5, 'none', layers=layers)
        path = self.root/'layers.qpixel'
        Project(str(self.source), [edit]).save(path)
        project = Project.load(path)
        self.assertEqual(project.edits, [edit])
        out, report = self.export(project.edits, 'layers.mp4')
        preview = self.root/'layers-preview.mp4'
        render_preview(project, 0, preview, Runner())
        self.assertEqual(report.encoded_seconds, 2)
        for video, offset in ((out, 40), (preview, 0)):
            base, green, magenta, after = [image_at(video, offset+i) for i in (2, 10, 17, 34)]
            self.assertGreater(green.getpixel((160, 90))[1], 220)
            self.assertGreater(magenta.getpixel((160, 90))[0], 220)
            self.assertGreater(magenta.getpixel((160, 90))[2], 220)
            for image, frame in ((base, 42), (after, 74)):
                original = image_at(self.source, frame)
                self.assertLess(sum(abs(a-b) for a, b in zip(image.getpixel((160, 90)), original.getpixel((160, 90)))), 35)
            outside = image_at(self.source, 50).getpixel((95, 90))
            self.assertLess(sum(abs(a-b) for a, b in zip(green.getpixel((95, 90)), outside)), 35)

        # Shift only the image. Its old overlap with the stationary mask disappears.
        layers[0].transform.x = .05
        layers[0].transform.w = .2
        moved, _ = self.export([edit], 'moved.mp4')
        self.assertLess(sum(abs(a-b) for a, b in zip(image_at(moved, 50).getpixel((160, 90)),
                                                    image_at(self.source, 50).getpixel((160, 90)))), 35)

    def test_migrating_old_sticker_keeps_even_pixel_coverage(self):
        from PIL import Image
        asset = self.root/'legacy-sticker.png'
        Image.new('RGBA', (24, 24), (0, 255, 0, 255)).save(asset)
        path = self.root/'legacy.qpixel'
        edit = Edit(2, 3, 'image', Rect(83.9/320, .25, .4, .5), image=str(asset))
        Project(str(self.source), [edit]).save(path)
        project = Project.load(path)
        project.edits[0].promote_layers(320, 180)
        output = self.root/'legacy-migrated.mp4'
        render_preview(project, 0, output, Runner())
        red, green, blue = image_at(output, 1).getpixel((82, 90))
        self.assertGreater(green-max(red, blue), 180)

    def test_speed_preserves_delayed_audio_on_source_timeline(self):
        import array
        source = self.root/'delayed.mp4'
        command('ffmpeg', '-v', 'error', '-y', '-i', str(self.source), '-itsoffset', '1', '-i', str(self.source),
                '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(source))
        out, _ = self.export([Edit(2, 4, 'none', speed=2)], 'delayed-out.mp4', source)
        samples = array.array('h', command('ffmpeg', '-v', 'error', '-i', str(out), '-map', '0:a:0',
                                         '-ac', '1', '-ar', '8000', '-f', 's16le', '-'))
        self.assertLess(max(abs(s) for s in samples[:5600]), 30)
        self.assertGreater(max(abs(s) for s in samples[9600:10400]), 1000)

    def test_speed_changes_duration_audio_and_preserves_copied_tail(self):
        out, report = self.export([Edit(2, 4, 'none', speed=2)], 'speed.mp4')
        info = probe(out, Runner())
        frames = hashes(out)
        self.assertEqual(len(frames), 100)
        self.assertEqual(frames[:40], self.original[:40])
        self.assertEqual(frames[60:], self.original[80:])
        self.assertEqual(report.frames, 100)
        self.assertEqual(len(info.audio), 2)
        self.assertEqual(info.audio[0]['tags']['language'], 'zho')
        self.assertAlmostEqual(info.duration, 5, delta=.08)
        for track in info.audio:
            self.assertAlmostEqual(float(track['duration']), 5, delta=.08)

    def test_preview_speed_and_multiple_changes_inside_one_gop(self):
        out, _ = self.export([Edit(2.2, 2.6, 'none', speed=2),
                              Edit(2.8, 3.2, 'none', speed=.5)], 'mixed-speed.mkv')
        self.assertEqual(len(hashes(out)), 124)
        preview = self.root / 'speed-preview.mp4'
        render_preview(Project(str(self.source), [Edit(1, 2, 'none', speed=2)]), 0, preview, Runner())
        self.assertEqual(len(hashes(preview)), 10)
        self.assertAlmostEqual(probe(preview, Runner()).duration, .5, delta=.08)

    def test_shapes_and_feather_mask_pixels(self):
        from q_pixelate.masks import mask_image
        edit = Edit(1, 2, 'region', Rect(.25, .25, .5, .5), shape='ellipse')
        mask = mask_image(edit, 320, 180)
        self.assertEqual(mask.pixelColor(80, 45).red(), 0)
        self.assertEqual(mask.pixelColor(160, 90).red(), 255)
        out, _ = self.export([edit], 'ellipse.mp4')
        self.assertEqual(len(hashes(out)), 120)
        edit.shape = 'lasso'
        edit.points = [(.25, .25), (.75, .25), (.5, .75)]
        edit.feather = 4
        out, _ = self.export([edit], 'lasso.mp4')
        self.assertEqual(len(hashes(out)), 120)

    def test_slow_motion_at_eof_and_fast_fractional_silent_clip(self):
        out, _ = self.export([Edit(5, 6, 'none', speed=.25)], 'slow-end.mp4')
        self.assertEqual(len(hashes(out)), 180)
        self.assertAlmostEqual(probe(out, Runner()).duration, 9, delta=.08)
        source = fixture(self.root/'speed-fractional.mp4', '30000/1001', False)
        out, report = self.export([Edit(0, .667, 'none', speed=3)], 'speed-fractional-out.mp4', source)
        self.assertEqual(report.frames, 167)  # 180 source frames, 20 selected frames → 7 at 3x.
        self.assertEqual(len(probe(out, Runner()).audio), 0)

    def test_feather_softens_inside_ellipse_edge_without_expanding_mask(self):
        from PIL import Image, ImageChops, ImageStat
        pattern = Image.new('RGB', (320, 180))
        pattern.putdata([(255, 255, 255) if (x//2+y//2)%2 else (0, 0, 0)
                         for y in range(180) for x in range(320)])
        asset = self.root/'checker.png'
        pattern.save(asset)
        source = self.root/'checker.mp4'
        command('ffmpeg', '-v', 'error', '-y', '-loop', '1', '-framerate', '20', '-i', str(asset),
                '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '40', str(source))
        edit = Edit(0, 2, 'region', Rect(.25, .25, .5, .5), shape='ellipse')
        hard, _ = self.export([edit], 'hard-ellipse.mp4', source)
        edit.feather = 12
        soft, _ = self.export([edit], 'soft-ellipse.mp4', source)
        original, hard_image, soft_image = (image_at(path, 20) for path in (source, hard, soft))
        def difference(changed, box):
            return sum(ImageStat.Stat(ImageChops.difference(original.crop(box), changed.crop(box))).mean)
        edge = (81, 84, 88, 96)
        self.assertGreater(difference(hard_image, edge), difference(soft_image, edge)*1.2)
        self.assertGreater(difference(soft_image, (150, 80, 170, 100)), 250)
        self.assertLess(difference(soft_image, (65, 80, 75, 100)), 15)

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

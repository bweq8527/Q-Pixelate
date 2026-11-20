import tempfile
import unittest
from pathlib import Path

from PIL import Image

from media_helpers import command, image_at
from q_pixelate.model import Edit, Layer, Mask, Project, Rect, Transform
from q_pixelate.process import Runner
from q_pixelate.render import Exporter, render_preview


class TrackedStickerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.asset = self.root / 'sticker.png'
        Image.new('RGBA', (32, 24), (0, 255, 0, 255)).save(self.asset)

    def make_source(self, rate='20'):
        source = self.root / 'source.mp4'
        command('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                f'color=black:s=160x96:r={rate}:d=2', '-c:v', 'libx264',
                '-g', '20', '-sc_threshold', '0', str(source))
        return source

    def test_transform_preserves_target_relative_offset_scale_and_rotation(self):
        layer = Layer(0, 2, 'sticker', image=str(self.asset),
                      transform=Transform(.25, .15, .1, .12, 37),
                      motion=[(0, Rect(.1, .1, .2, .2)), (1, Rect(.4, .3, .4, .3))])
        layer.validate()
        middle = layer.sticker_at(.5).transform
        self.assertAlmostEqual(middle.x, .475)
        self.assertAlmostEqual(middle.y, .2625)
        self.assertAlmostEqual(middle.w, .15)
        self.assertAlmostEqual(middle.h, .15)
        self.assertEqual(middle.angle, 37)
        self.assertAlmostEqual(layer.sticker_at(3).transform.x, .7)
        self.assertEqual(layer.transform, Transform(.25, .15, .1, .12, 37))

    def test_tracked_sticker_rejects_mask_attachment(self):
        layer = Layer(0, 1, 'sticker', image=str(self.asset), mask=Mask(), motion=[(0, Rect())])
        with self.assertRaisesRegex(ValueError, '遮罩'):
            layer.validate()

    def test_project_round_trip_keeps_motion_and_export_respects_frame_boundaries(self):
        source = self.make_source('30000/1001')
        tick = 1001/30000
        initial = Rect(.1, .2, .15, .2)
        layer = Layer(4*tick, 13*tick, 'sticker', image=str(self.asset),
                      transform=Transform.from_rect(initial),
                      motion=[(0, initial), (8*tick, Rect(.55, .2, .3, .4))])
        project = Project(str(source), [Edit(20*tick, 35*tick, 'none', layers=[layer])])
        saved = self.root / 'tracked.qpixel'
        project.save(saved)
        loaded = Project.load(saved)
        self.assertEqual(loaded.edits, project.edits)

        output = self.root / 'tracked.mp4'
        report = Exporter().export(loaded, output, Runner())
        self.assertEqual(report.frames, 60)
        self.assertLess(image_at(output, 23).getpixel((28, 28))[1], 30)
        self.assertGreater(image_at(output, 24).getpixel((28, 28))[1], 220)
        moved = image_at(output, 32)
        self.assertLess(moved.getpixel((28, 28))[1], 30)
        self.assertGreater(moved.getpixel((105, 52))[1], 220)
        self.assertLess(image_at(output, 33).getpixel((105, 52))[1], 30)

    def test_slow_preview_resolves_track_on_source_frames_when_selection_clips_start(self):
        source = self.make_source()
        initial = Rect(.1, .2, .15, .2)
        layer = Layer(-.1, .25, 'sticker', image=str(self.asset),
                      transform=Transform.from_rect(initial),
                      motion=[(0, initial), (.1, Rect(.55, .2, .15, .2)),
                              (.25, Rect(.2, .6, .15, .2))])
        project = Project(str(source), [Edit(1.5, 2, 'none', speed=.5, layers=[layer])])
        output = self.root / 'preview.mp4'
        render_preview(project, 0, output, Runner())
        self.assertGreater(image_at(output, 0).getpixel((100, 28))[1], 220)
        self.assertGreater(image_at(output, 1).getpixel((100, 28))[1], 220)
        self.assertGreater(image_at(output, 6).getpixel((44, 67))[1], 220)
        self.assertLess(image_at(output, 10).getpixel((44, 67))[1], 30)
        self.assertLess(image_at(output, 19).getpixel((44, 67))[1], 30)

import tempfile
import unittest
from pathlib import Path

from PIL import ImageChops, ImageStat

from media_helpers import fixture, image_at
from q_pixelate.model import Edit, Layer, Project, Rect, Transform
from q_pixelate.process import Runner
from q_pixelate.render import Exporter


class MosaicTests(unittest.TestCase):
    def test_mosaic_and_slow_motion_at_eof_keep_the_planned_frame_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = fixture(root / 'source.mp4', audio=False)
            box = Rect(.5, .5, .45, .45)
            layer = Layer(0, 1, 'mosaic', transform=Transform.from_rect(box), motion=[(0, box)])
            project = Project(str(source), [Edit(5, 6, 'none', speed=2/3, layers=[layer])])
            report = Exporter().export(project, root / 'output.mp4', Runner())
            self.assertEqual(report.frames, 130)

    def test_moving_mosaic_round_trips_and_renders_only_inside_span(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = fixture(root / 'source.mp4', audio=False)
            origin = Rect(.05, .03, .30, .47)
            layer = Layer(.25, .85, 'mosaic', strength=25, transform=Transform.from_rect(origin),
                          motion=[(0, origin), (.55, Rect(.55, .03, .30, .47))])
            edit = Edit(2, 3, 'none', layers=[layer])
            project = Project(str(source), [edit])
            project.save(root / 'edit.qpixel')
            loaded = Project.load(root / 'edit.qpixel')
            self.assertEqual(loaded.edits, project.edits)
            output = root / 'output.mp4'
            report = Exporter().export(loaded, output, Runner())
            self.assertEqual(report.frames, 120)

            def difference(frame, box):
                delta = ImageChops.difference(image_at(source, frame), image_at(output, frame))
                return sum(ImageStat.Stat(delta.crop(box)).mean) / 3

            left, right = (16, 7, 108, 88), (177, 7, 269, 88)
            self.assertGreater(difference(45, left), difference(45, right) + 8)
            self.assertGreater(difference(56, right), difference(56, left) + 8)
            self.assertLess(difference(44, left), 8)
            self.assertLess(difference(57, right), 8)

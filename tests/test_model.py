import math
import tempfile
import unittest
from pathlib import Path

from q_pixelate.model import Edit, Layer, Project, Rect, Transform, plan_ranges


class ModelTests(unittest.TestCase):
    def test_workflow_options_and_tracked_blur_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(b'video')
            first = Rect(.2, .2, .25, .2)
            second = Rect(.6, .2, .25, .2)
            layer = Layer(0, 1, 'blur', transform=Transform.from_rect(first),
                          motion=[(0, first), (1, second)])
            edits = [Edit(1, 3, 'none', layers=[layer], omit=True, speed_effect=True)]
            path = root / 'workflow.qpixel'
            Project(str(source), edits).save(path)
            self.assertEqual(Project.load(path).edits, edits)

    def test_extended_edit_persistence_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(b'video')
            edit = Edit(1, 3, 'region', speed=2, shape='lasso',
                        points=[(.1, .1), (.8, .1), (.4, .8)], feather=5,
                        range_locked=True, locked=True)
            path = root / 'new.qpixel'
            Project(str(source), [edit]).save(path)
            self.assertEqual(Project.load(path).edits, [edit])
            for updates in ({'speed': 0}, {'speed': float('nan')}, {'shape': 'bad'},
                            {'feather': -1}, {'points': [(0, 0)]}, {'locked': 'yes'}):
                from dataclasses import replace
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    replace(edit, **updates).validate(6)

    def test_reject_overlap_invalid_coordinates_and_nonfinite_times(self):
        for edits in ([Edit(1, 3), Edit(2, 4)], [Edit(float('nan'), 2)],
                      [Edit(2, 1)], [Edit(0, 6)], [Edit(0, 1, rect=Rect(-.1, 0, .2, .2))]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                Project('source.mp4', edits).validate(5)
        Project('source.mp4', [Edit(0, 1), Edit(1, 2)]).validate(5)

    def test_expands_and_merges_gop_ranges_without_moving_effect_times(self):
        edits = [Edit(.51, .8), Edit(1.2, 1.4), Edit(4.2, 4.4)]
        spans = plan_ranges(edits, [0, 20, 40, 60], 60, 10)
        self.assertEqual([(s.start, s.end, s.encode) for s in spans],
                         [(0, 20, True), (20, 40, False), (40, 60, True)])
        self.assertEqual(edits[0].start, .51)

    def test_zero_edits_and_end_of_file(self):
        self.assertEqual([(s.start, s.end, s.encode) for s in plan_ranges([], [0, 20], 25, 10)],
                         [(0, 25, False)])
        self.assertEqual([(s.start, s.end) for s in plan_ranges([Edit(2.3, 2.5)], [0, 20], 25, 10)],
                         [(0, 20), (20, 25)])

    def test_project_round_trip_relative_assets_and_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / '中文 源.mp4'
            source.write_bytes(b'original')
            image = root / '遮挡.png'
            image.write_bytes(b'png')
            project = Project(str(source), [Edit(1, 2, 'image', image=str(image))])
            path = root / 'project.qpixel'
            project.save(path)
            self.assertNotIn(str(root).replace('\\', '\\\\'), path.read_text(encoding='utf-8'))
            loaded = Project.load(path)
            self.assertEqual(Path(loaded.source), source)
            self.assertEqual(Path(loaded.edits[0].image), image)
            source.write_bytes(b'changed longer')
            with self.assertRaisesRegex(ValueError, '变化|改变'):
                Project.load(path)

    def test_malformed_project_does_not_escape_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.qpixel'
            for content in ('[]', '{"version":900}', '{"version":1,"source":null,"edits":[]}'):
                p.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValueError):
                    Project.load(p)


if __name__ == '__main__':
    unittest.main()

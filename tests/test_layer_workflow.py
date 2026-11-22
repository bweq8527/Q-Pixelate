import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from media_helpers import fixture
from q_pixelate.app import MainWindow
from q_pixelate.media import probe
from q_pixelate.model import Edit, Layer, Mask, Project, Rect, Transform
from q_pixelate.process import Runner


class LayerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.home = Path(temp.name)
        cls.source = fixture(cls.home / 'test.mp4', audio=False)
        cls.info = probe(cls.source, Runner())

    def window(self, layers, **edit_options):
        with patch.dict(os.environ, {'QPIXELATE_HOME': str(self.home)}):
            w = MainWindow()
        self.addCleanup(self.close_window, w)
        w.set_project(Project(str(self.source), [Edit(1, 4, 'none', layers=layers, **edit_options)]), self.info)
        return w

    def close_window(self, window):
        window.dirty = False
        window.close()
        self.app.processEvents()

    def test_range_selection_updates_only_edited_common_parameters(self):
        w = self.window([Layer(.25, 1.5, name='左', strength=19, mask=Mask()),
                         Layer(.5, 2, name='中', strength=24, mask=Mask(feather=3)),
                         Layer(.75, 2.5, name='右', strength=32, mask=Mask(feather=5))])
        w.show()
        w.editor_tabs.setCurrentIndex(2)
        self.app.processEvents()
        picker = w.layer_panel.picker
        for index, modifier in [(0, Qt.NoModifier), (2, Qt.ShiftModifier)]:
            row = picker.visualItemRect(picker.item(index))
            QTest.mouseClick(picker.viewport(), Qt.LeftButton, modifier, row.center())
        self.assertEqual(len(picker.selectedItems()), 3)
        self.assertEqual(w.layer_panel.strength.editor.placeholderText(), '多值')
        w.layer_panel.strength.setValue(60)
        w.layer_panel.start.setValue(2)
        layers = w.layer_edit().layers
        self.assertEqual([layer.strength for layer in layers], [60, 60, 60])
        self.assertEqual([(layer.start, layer.end) for layer in layers], [(1, 1.5), (1, 2), (1, 2.5)])
        self.assertEqual([layer.mask.feather for layer in layers], [0, 3, 5])
        self.assertEqual([layer.name for layer in layers], ['左', '中', '右'])
        row = picker.visualItemRect(picker.item(1))
        QTest.mouseClick(picker.viewport(), Qt.LeftButton, Qt.ControlModifier, row.center())
        self.assertEqual({picker.row(item) for item in picker.selectedItems()}, {0, 2})
        w.layer_panel.feather.setValue(8)
        self.assertEqual([layer.mask.feather for layer in w.layer_edit().layers], [8, 3, 8])

    def test_tracking_blur_requires_no_sticker_and_creates_motion_layer(self):
        w = self.window([])
        w.canvas.frame_time = 1.25
        w.tracking_runtime = lambda: ('python.exe', self.home)
        w.start_job = Mock()
        w.begin_blur_tracking()
        self.assertEqual(w.canvas.mask_shape, 'rect')
        left, right = Rect(.1, .2, .2, .15), Rect(.45, .3, .2, .15)
        w.finish_mask(Mask('rect', Transform.from_rect(left)))
        w.start_job.assert_called_once()
        w.start_job.call_args.args[1](SimpleNamespace(points=[(1.25, left), (1.5, right)], end=1.55, reason='结束'))
        layer, = w.layer_edit().layers
        self.assertEqual((layer.kind, layer.image, layer.mask), ('blur', '', None))
        self.assertEqual(layer.motion, [(0, left), (.25, right)])
        followed = layer.mosaic_rect_at(.25)
        self.assertAlmostEqual(followed.x, right.x)
        self.assertAlmostEqual(followed.y, right.y)

    def test_omit_inspector_stores_independent_video_and_audio_transitions(self):
        w = self.window([], omit=True)
        w.select_effect_layer('@omit')
        panel = w.layer_panel
        self.assertFalse(panel.transition_duration.isEnabled())
        panel.video_transition.setCurrentIndex(panel.video_transition.findData('white'))
        panel.audio_transition.setCurrentIndex(panel.audio_transition.findData('fade'))
        panel.transition_duration.setValue(.7)
        w.apply_edit()
        edit = w.project.edits[0]
        self.assertEqual((edit.video_transition, edit.audio_transition, edit.transition_duration),
                         ('white', 'fade', .7))

    def test_removing_omit_effect_restores_retained_speed_and_layers(self):
        original = Layer(0, 2, name='保留的效果')
        w = self.window([original], omit=True, speed_effect=True, speed=2)
        w.select_effect_layer('@omit')
        w.delete_layer()
        restored = w.layer_edit()
        self.assertFalse(restored.omit)
        self.assertEqual(restored.layers, [original])
        self.assertTrue(restored.speed_effect)
        self.assertEqual(restored.speed, 2)
        w.select_effect_layer('@speed')
        w.layer_panel.speed.setValue(1)
        self.assertTrue(w.layer_edit().speed_effect)
        self.assertEqual(w.layer_edit().speed, 1)

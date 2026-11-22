import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from media_helpers import fixture
from q_pixelate.app import MainWindow
from q_pixelate.model import Project, Edit, Layer, Mask, Rect, Transform
from q_pixelate.masks import transform_geometry
from q_pixelate.media import probe
from q_pixelate.process import Runner
from q_pixelate.widgets import Timeline, VideoCanvas
from q_pixelate.stickers import STICKER_MIME, StickerLibrary


class EditorUpgradeTests(unittest.TestCase):
    def test_tracking_draw_starts_at_displayed_frame_and_creates_sticker_until_loss(self):
        w = self.window([Edit(1, 3, 'none', layers=[])])
        asset = Path(self.temp.name) / 'tracking-target.png'
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.assertTrue(image.save(str(asset)))
        w.sticker_library_list.add_asset(str(asset), '跟随贴纸')
        w.sticker_library_list.setCurrentRow(w.sticker_library_list.count()-1)
        w.canvas.frame_time = 1.25
        w.tracking_runtime = lambda: ('python.exe', Path(self.temp.name))
        w.start_job = Mock()
        w.track_sticker_button.click()
        self.assertEqual(w.canvas.mask_shape, 'rect')
        first, second = Rect(.1, .15, .2, .2), Rect(.4, .25, .2, .2)
        w.finish_mask(Mask('rect', Transform.from_rect(first)))
        w.start_job.assert_called_once()
        done = w.start_job.call_args.args[1]
        done(SimpleNamespace(points=[(1.25, first), (1.50, second)], end=1.55, reason='目标丢失'))
        layer, = w.layer_edit().layers
        self.assertEqual((layer.kind, layer.start, layer.end, layer.image), ('sticker', .25, .55, str(asset)))
        self.assertEqual(layer.motion, [(0, first), (.25, second)])
        w.update_canvas_mask(1.5)
        self.assertEqual(w.canvas.layers, [(0, layer)])
        w.update_canvas_mask(1.55)
        self.assertEqual(w.canvas.layers, [])
        w.seek = Mock()
        w.layer_panel.track_end.click()
        w.seek.assert_called_once_with(1.55)

    def test_review_detection_marks_and_applies_once(self):
        w = self.window([Edit(1, 2, 'none', layers=[]), Edit(3, 4, 'none', layers=[])])
        w.show_auto_mosaic()
        tracks = [SimpleNamespace(id=f'track-{i}', label='BUTTOCKS_EXPOSED', score=.9,
                                 start=start, end=start+.1, points=[(start, Rect(.1, .2, .3, .3))])
                  for i, start in enumerate((1.1, 1.5))]
        w.show_detection_results((1, 2), tracks, '逐帧')
        self.assertEqual(w.timeline.detection_spans, [(t.start, t.end) for t in tracks])
        w.detection_results.item(1).setCheckState(Qt.CheckState.Unchecked)
        w.apply_detection_results()
        w.apply_detection_results()
        self.assertEqual([(layer.kind, layer.id) for layer in w.layer_edit().layers], [('mosaic', 'track-0')])
        w.select_edit(1)
        self.assertEqual(w.detection_results.count(), 0)
        w.select_edit(0)
        self.assertEqual(w.detection_results.count(), 2)
        w.drag_selection(0, 1, 2.25, 'right')
        self.assertEqual(w.detection_results.count(), 0)
        w.drag_selection(0, 1, 2, 'right')
        self.assertEqual(w.detection_results.count(), 0)

    def test_editing_legacy_selection_permanently_invalidates_detection_results(self):
        w = self.window([Edit(1, 2, 'none')])
        w.show_detection_results((1, 2), [SimpleNamespace(
            id='track', label='BUTTOCKS_EXPOSED', score=.9, start=1.1, end=1.2,
            points=[(1.1, Rect(.1, .2, .3, .3))])], '逐帧')
        self.assertEqual(w.detection_results.count(), 1)
        w.in_time.setValue(1.2)
        self.assertEqual(w.detection_results.count(), 0)
        w.in_time.setValue(1)
        self.assertEqual(w.detection_results.count(), 0)

    def test_mosaic_outline_appears_at_a_rounded_fractional_frame_position(self):
        frame = 1001 / 30000
        box = Rect(.1, .1, .3, .3)
        layer = Layer(0, frame, 'mosaic', transform=Transform.from_rect(box), motion=[(0, box)])
        w = self.window([Edit(frame, 2*frame, 'none', layers=[layer])])
        w.info.fps = Fraction(30000, 1001)
        w.update_canvas_mask(.033)
        self.assertEqual(w.canvas.layers, [(0, layer)])

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = fixture(Path(cls.temp.name)/'source.mp4', audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def window(self, edits=None):
        window = MainWindow()
        window.error = lambda text: self.fail(text)
        window.set_project(Project(str(self.source), edits or []), probe(self.source, Runner()))
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window):
        window.dirty = False
        window.close()
        self.app.processEvents()

    def test_confirm_locks_and_blocks_later_edits_until_explicit_unlock(self):
        w = self.window()
        w.set_selection(1, 2)
        w.add_edit()
        w.set_selection(0, 3)
        self.assertEqual(w.timeline.selection, (1, 2))
        w.speed.setValue(2)
        w.apply_edit()
        self.assertTrue(w.project.edits[0].locked)
        w.strength.setValue(88)
        w.apply_edit()
        w.delete_edit()
        self.assertEqual(len(w.project.edits), 1)
        self.assertEqual(w.project.edits[0].strength, 20)
        self.assertEqual(w.project.edits[0].speed, 2)
        w.unlock_edit()
        w.strength.setValue(40)
        w.apply_edit()
        self.assertEqual(w.project.edits[0].strength, 40)

    def test_unconfirmed_draft_blocks_switch_and_export_commit(self):
        w = self.window([Edit(1, 2), Edit(3, 4)])
        w.clip_list.setCurrentRow(0)
        w.strength.setValue(37)
        w.clip_list.setCurrentRow(1)
        self.assertEqual(w.clip_list.currentRow(), 0)
        self.assertFalse(w.commit_form())
        self.assertEqual(w.project.edits[0].strength, 20)
        w.apply_edit()
        w.clip_list.setCurrentRow(1)
        self.assertEqual(w.project.edits[0].strength, 37)

    def test_legacy_unlocked_range_drag_marks_unsaved_draft(self):
        w = self.window([Edit(1, 2)])
        self.assertFalse(w.dirty)
        w.timeline.selectionChanged.emit(.5, 2.5)
        self.assertTrue(w.dirty)
        self.assertEqual((w.current_edit().start, w.current_edit().end), (.5, 2.5))

    def test_playback_lane_and_locked_selection_never_resize_range(self):
        t = Timeline()
        t.resize(800, 120)
        t.set_duration(100)
        t.selection = (20, 40)
        t.show()
        self.addCleanup(t.close)
        seeks = []
        t.seekRequested.connect(seeks.append)
        pos = t.point_for_time(20)
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, QPoint(pos.x(), 40))
        self.assertTrue(seeks)
        self.assertEqual(t.selection, (20, 40))
        seeks.clear()
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(30))
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(60))
        t.selection_locked = True
        QTest.mousePress(t, Qt.LeftButton, Qt.ShiftModifier, t.point_for_time(50))
        QTest.mouseRelease(t, Qt.LeftButton, Qt.ShiftModifier, t.point_for_time(70))
        self.assertEqual(t.selection, (20, 40))
        self.assertEqual(seeks, [])

    def test_right_drag_splits_around_existing_selections_without_seeking(self):
        w = self.window([Edit(2, 3, locked=True)])
        w.player.pause()
        w.timeline.resize(640, 120)
        seeks = []
        w.timeline.seekRequested.connect(seeks.append)
        QTest.mousePress(w.timeline, Qt.RightButton, Qt.NoModifier, w.timeline.point_for_time(4))
        QTest.mouseRelease(w.timeline, Qt.RightButton, Qt.NoModifier, w.timeline.point_for_time(1))
        self.assertEqual(sorted((e.start, e.end) for e in w.project.edits), [(1, 2), (2, 3), (3, 4)])
        self.assertEqual(seeks, [])

    def test_drop_creates_multiple_layers_and_mask_edits_do_not_move_sticker(self):
        w = self.window([Edit(1, 5, 'none', layers=[])])
        w.show()
        self.app.processEvents()
        w.player.pause()
        w.position_changed(2000)
        w.sticker_library = StickerLibrary(Path(self.temp.name)/'drop-library')
        path = Path(self.temp.name)/'drop.png'
        image = QImage(40, 20, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.green)
        self.assertTrue(image.save(str(path)))
        asset = w.sticker_library.add(path)
        w.refresh_stickers(asset)
        mime = QMimeData()
        mime.setData(STICKER_MIME, asset.encode('utf-8'))
        box = w.canvas.video_rect()
        for x, milliseconds in ((.3, 2000), (.6, 2000), (.75, 4990)):
            w.position_changed(milliseconds)
            point = QPointF(box.x()+box.width()*x, box.center().y())
            front = QDragEnterEvent(point.toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(w.canvas, front)
            drop = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(w.canvas, drop)
            self.assertTrue(drop.isAccepted())
        layers = w.current_edit().layers
        self.assertEqual([(item.start, item.end) for item in layers[:2]], [(1, 3), (1, 3)])
        self.assertEqual(len(layers), 3)
        self.assertAlmostEqual(layers[2].start, 3.95)
        self.assertEqual(layers[2].end, 4)
        self.assertAlmostEqual(layers[1].transform.x, .6)
        w.position_changed(2000)
        w.editor_tabs.setCurrentIndex(2)
        w.layer_panel.picker.setCurrentRow(1)
        QTest.mouseClick(w.layer_panel.redraw, Qt.LeftButton)
        a, b = (QPoint(round(box.x()+box.width()*x), round(box.y()+box.height()*x)) for x in (.4, .7))
        QTest.mousePress(w.canvas, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseRelease(w.canvas, Qt.LeftButton, Qt.NoModifier, b)
        layer = w.current_edit().layers[1]
        self.assertEqual(layer.transform, layers[1].transform)
        self.assertIsNotNone(layer.mask)
        w.layer_panel.geometry[0].setValue(44)
        changed = w.current_edit().layers[1]
        self.assertEqual(changed.mask.transform.x, .44)
        self.assertEqual(changed.transform, layer.transform)
        w.layer_panel.target.setCurrentIndex(0)
        w.canvas.setFocus()
        QTest.keyClick(w.canvas, Qt.Key_T, Qt.ControlModifier)
        matrix, local = transform_geometry(changed.transform, w.canvas.video_rect())
        knob = matrix.map(QPointF(0, local.top()-22)).toPoint()
        rotated = matrix.map(QPointF(-local.top()+22, 0)).toPoint()
        QTest.mousePress(w.canvas, Qt.LeftButton, Qt.NoModifier, knob)
        QTest.mouseRelease(w.canvas, Qt.LeftButton, Qt.NoModifier, rotated)
        self.assertAlmostEqual(w.current_edit().layers[1].transform.angle, 90, delta=2)
        geometry_field = w.layer_panel.geometry[0]
        geometry_field.setFocus()
        QTest.keyClick(geometry_field.editor, Qt.Key_Escape)
        self.assertEqual(w.current_edit().layers[1].transform, changed.transform)
        self.assertEqual(w.current_edit().layers[1].mask, changed.mask)
        QTest.mouseClick(w.layer_timeline, Qt.LeftButton, Qt.NoModifier, QPoint(50, 32))
        QTest.keyClick(w.layer_timeline, Qt.Key_Delete)
        self.assertEqual(len(w.project.edits), 1)
        self.assertEqual([layer.id for layer in w.current_edit().layers], [layers[0].id, layers[2].id])

    def test_dragging_selection_moves_layer_times_but_trimming_keeps_source_times(self):
        first = Edit(1, 3, 'none', layers=[Layer(.25, 1.25)])
        w = self.window([first, Edit(4, 5, locked=True)])
        t = w.timeline
        t.resize(640, 120)
        seeks = []
        t.seekRequested.connect(seeks.append)
        QTest.mousePress(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(2))
        QTest.mouseRelease(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(2.5))
        moved = w.current_edit()
        self.assertEqual((moved.start, moved.end), (1.5, 3.5))
        self.assertEqual(moved.layer_span(moved.layers[0]), (1.75, 2.75))
        QTest.mousePress(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(1.5))
        QTest.mouseRelease(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(2))
        trimmed = w.current_edit()
        self.assertEqual(trimmed.layer_span(trimmed.layers[0]), (2, 2.75))
        self.assertEqual(trimmed.layers[0].start, -.25)
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(4.5))
        self.assertEqual(w.clip_list.currentRow(), 1)
        QTest.mouseClick(t, Qt.LeftButton, Qt.NoModifier, t.point_for_time(2.5))
        self.assertEqual(w.current_edit(), trimmed)
        self.assertEqual(seeks, [])

    def test_focusing_time_controls_does_not_restore_out_of_range_layer(self):
        clip = Edit(2, 5, 'none', layers=[Layer(-2, -1)])
        w = self.window([clip])
        w.show()
        w.editor_tabs.setCurrentIndex(2)
        self.app.processEvents()
        w.layer_panel.start.setFocus()
        w.layer_panel.end.setFocus()
        w.layer_panel.name.setFocus()
        self.assertEqual(w.current_edit(), clip)
        self.assertFalse(w.dirty)

    def test_zoom_pan_changes_view_only(self):
        t = Timeline()
        t.resize(800, 120)
        t.set_duration(100)
        t.set_zoom(10)
        t.show()
        self.addCleanup(t.close)
        QTest.mousePress(t, Qt.MiddleButton, Qt.NoModifier, QPoint(500, 70))
        QTest.mouseRelease(t, Qt.MiddleButton, Qt.NoModifier, QPoint(200, 70))
        self.assertGreater(t.view_start, 3)
        self.assertEqual(t.position, 0)
        self.assertEqual(t.selection, (0, 0))

    def test_scrollbar_reaches_zoomed_video_end_without_moving_selection(self):
        w = self.window()
        self.assertFalse(w.scrollbar.isEnabled())
        selected = w.timeline.selection
        w.zoom.setValue(10)
        w.scrollbar.setValue(w.scrollbar.maximum())
        self.assertAlmostEqual(w.timeline.view_start, 5.4)
        self.assertEqual(w.timeline.selection, selected)
        w.zoom.setValue(1)
        self.assertFalse(w.scrollbar.isEnabled())

    def test_wheel_zoom_keeps_time_under_pointer_in_panned_view(self):
        w = self.window([Edit(2, 3)])
        w.player.pause()
        w.position_changed(500)
        t = w.timeline
        t.resize(640, 120)
        w.zoom.setValue(4)
        w.scrollbar.setValue(6000)
        pointer = QPointF(420, 76)
        anchor = t.time_at(pointer.x())
        selected = t.selection
        seeks = []
        t.seekRequested.connect(seeks.append)
        for delta in (120, -120):
            before = t.visible
            event = QWheelEvent(pointer, QPointF(t.mapToGlobal(pointer.toPoint())),
                                QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(t, event)
            self.assertAlmostEqual(t.visible, before/1.3 if delta > 0 else before*1.3)
            self.assertAlmostEqual(t.time_at(pointer.x()), anchor)
            self.assertGreater(w.scrollbar.value(), 0)
        self.assertEqual(t.position, .5)
        self.assertEqual(t.selection, selected)
        self.assertEqual(seeks, [])

    def test_space_over_focused_button_does_not_apply_or_resize(self):
        w = self.window()
        w.show()
        w.set_selection(1, 2)
        w.add_edit()
        w.confirm_button.setFocus()
        QTest.keyClick(w.confirm_button, Qt.Key_Space)
        self.assertFalse(w.project.edits[0].locked)
        self.assertEqual(w.timeline.selection, (1, 2))

    def test_lasso_draw_emits_normalized_points_and_replaces_existing_shape(self):
        c = VideoCanvas()
        c.resize(640, 360)
        c.editable = True
        c.shape = 'lasso'
        c.show()
        self.addCleanup(c.close)
        paths = []
        c.pointsChanged.connect(paths.append)
        QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        QTest.mouseMove(c, QPoint(400, 100))
        QTest.mouseMove(c, QPoint(250, 300))
        QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        self.assertGreaterEqual(len(paths[-1]), 3)
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in paths[-1]))

    def test_main_window_lasso_path_survives_coordinate_signal_and_confirmation(self):
        w = self.window()
        w.show()
        w.set_selection(1, 2)
        w.add_edit()
        w.effect.setCurrentIndex(1)
        w.shape.setCurrentIndex(2)
        c = w.canvas
        box = c.video_rect()
        points = [QPoint(round(box.x()+box.width()*x), round(box.y()+box.height()*y))
                  for x, y in ((.2, .2), (.8, .2), (.5, .8), (.2, .2))]
        QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, points[0])
        for point in points[1:-1]:
            QTest.mouseMove(c, point)
        QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, points[-1])
        self.assertGreaterEqual(len(w.mask_points), 3)
        self.assertAlmostEqual(w.current_edit().rect.x, .2, delta=.01)
        self.assertAlmostEqual(w.current_edit().rect.w, .6, delta=.01)
        w.apply_edit()
        self.assertEqual(w.project.edits[0].points, w.mask_points)

    def test_single_shift_and_held_arrow_preview_preserve_selection(self):
        w = self.window()
        w.show()
        for _ in range(100):
            self.app.processEvents()
            if not w.canvas.image.isNull() and not w._pause_on_frame:
                break
            QTest.qWait(10)
        w.player.pause()
        w.player.setPosition(1000)
        w.canvas.setFocus()
        QTest.keyClick(w.canvas, Qt.Key_Right)
        self.assertAlmostEqual(w.player.position(), 1050, delta=3)
        w.seek_seconds.setValue(2)
        QTest.keyClick(w.canvas, Qt.Key_Right, Qt.ShiftModifier)
        self.assertAlmostEqual(w.player.position(), 3050, delta=3)
        selected = w.timeline.selection
        QTest.keyPress(w.canvas, Qt.Key_Left)
        QTest.qWait(550)
        QTest.keyRelease(w.canvas, Qt.Key_Left)
        self.assertLess(w.player.position(), 2700)
        self.assertEqual(w.timeline.selection, selected)
        self.assertFalse(w.shuttle_timer.isActive())

    def test_sticker_library_copies_asset_and_survives_reopening(self):
        from q_pixelate.stickers import StickerLibrary
        from PySide6.QtGui import QImage
        root = Path(self.temp.name)
        asset = root/'自制.png'
        image = QImage(20, 20, QImage.Format_ARGB32)
        image.fill(Qt.green)
        image.save(str(asset))
        library = StickerLibrary(root/'library')
        saved = library.add(asset)
        self.assertNotEqual(Path(saved), asset)
        self.assertEqual(StickerLibrary(root/'library').items(), [saved])
        self.assertEqual(library.add(asset), saved)

